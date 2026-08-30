#!/usr/bin/env python3
"""
council/researcher.py — the iterative per-country researcher (D13, the pivot)
==============================================================================
The worker-side engine of the `research_report` job type. This node's OWN agent runs
multiple rounds of egress-localized web research (council/research.py — the moat) and
writes its OWN findings with citations. It never proxies traffic (D4).

Rounds (sized for small local models — every model call has a parse fallback):
  1. PLAN    — turn the brief into 3 concrete search queries.
  2. SEARCH  — run them through this node's egress (search_structured, SSRF-guarded).
  3. REFINE  — 2 follow-up queries given what round 1 surfaced; search those too.
  4. DRAFT   — findings from THIS country's vantage, citing only [S#] source markers.

Returns a contribution the judge can score like any answer (text) plus the structured
sources for the editor pass (research):
  {"text": draft+source list, "tokens": int, "elapsed_s": float,
   "research": {"country": str, "sources": [{"id","title","url","host"}]}}
"""

from __future__ import annotations

import datetime as _dt
import os
import time
from dataclasses import dataclass, field
from typing import Callable

from council import ollama as _ollama
from council.judge import _extract_json
from council.research import (extract_date_hint, fetch_extract, inject_recency, is_breaking,
                              is_time_sensitive, order_by_recency, route_engines,
                              search_structured)
from council.worker import LENSES


def _num_env(key: str, default, cast):
    """Read a numeric env var, falling back to `default` on empty/invalid rather than crashing the run
    (mirrors council.ollama.resolve_timeout — an empty `PW_RESEARCH_DEADLINE=` must not raise). R36 review."""
    val = os.environ.get(key)
    if val:
        try:
            return cast(val)
        except (TypeError, ValueError):
            pass
    return default


@dataclass
class ResearchWorker:
    worker_id: str
    model: str
    lens: str = "neutral"
    country: str = "local"
    temperature: float = 0.4
    ollama_base: str = field(default_factory=_ollama.base)
    depth: str = "standard"   # quick (plan only) | standard (plan+refine) | deep (plan+2×refine)
    angle: str = ""           # STORM-lite: the distinct perspective THIS analyst researches through
    page_evidence: bool = True  # fetch top result pages (leaders draft from pages, not snippets)
    scope: str = "both"       # both | web | local — local pulls from your private library (D19)
    today: str = ""           # ISO date the research is "as of" (R18); defaults to today

    def _today(self) -> str:
        return self.today or _dt.date.today().isoformat()

    def _bumped_depth(self, brief: str) -> str:
        """Breaking briefs earn one extra depth notch — more refine queries, a bigger evidence
        cap, and more page fetches — to outrun SEO-stale pages on fast-moving topics (R19/D31).
        Bounded at 'deep'; non-breaking briefs keep the configured depth unchanged.
        Gated on time-sensitivity TOO (review R19, finding 4): _BREAKING_RE has triggers like
        'developing story', 'live updates', 'this morning' that also fire on stable briefs — so a
        'quick' caller isn't silently pushed into a two-refine-round deep run by an incidental
        word. We only deepen when the brief is genuinely time-sensitive AND breaking."""
        t = f"{brief} {self.angle}"
        order = ("quick", "standard", "deep")
        if not (is_time_sensitive(t) and is_breaking(t)):
            return self.depth
        try:
            i = order.index(self.depth)
        except ValueError:
            i = 1   # an unknown depth already behaves like 'standard' in the .get() defaults
        return order[min(i + 1, len(order) - 1)]

    def _generate(self, prompt: str, num_predict: int) -> tuple[str, int]:
        # keep this analyst warm across its plan→refine→draft rounds (R17); PW_OLLAMA_BASE honored
        return _ollama.generate(prompt, model=self.model, base_url=self.ollama_base,
                                temperature=self.temperature, num_predict=num_predict,
                                timeout_env="PW_RESEARCH_GEN_TIMEOUT", timeout_default=480)

    # ------------------------------------------------------------------ rounds
    def _plan_queries(self, brief: str) -> list[str]:
        focus = (f"Focus specifically on this angle of the brief: {self.angle}\n"
                 if self.angle else "")
        try:
            raw, _ = self._generate(
                f"You are planning web research. Today is {self._today()}. Turn this brief into "
                "exactly 3 concrete, specific search queries (different angles). For anything "
                "time-sensitive, make the queries current — include the current year/month so the "
                "freshest results surface. "
                f"{focus}"
                'Reply STRICT JSON only: ["query one","query two","query three"]\n\n'
                f"BRIEF:\n{brief}\n\nJSON:", num_predict=140)
        except Exception:
            # A generate failure here must degrade to the same brief-as-query fallback the JSON
            # parse failure already uses below — never crash the whole run over the planner (R3).
            return [brief[:200]]
        parsed = _extract_json(raw)
        qs = [str(q).strip() for q in parsed if str(q).strip()] if isinstance(parsed, list) else []
        return qs[:3] or [brief[:200]]

    def _refine_queries(self, brief: str, evidence: list[dict]) -> list[str]:
        seen = "\n".join(f"- {e['title']} ({e['host']})" for e in evidence[:10])
        try:
            raw, _ = self._generate(
                "Given the brief and the sources found so far, name up to 2 follow-up search queries "
                "that fill the biggest remaining gaps (be specific; avoid repeating what is already "
                "covered). If the brief is already well covered by the sources, reply with an empty "
                'list to stop. Reply STRICT JSON only: ["query one","query two"] (or [])\n\n'
                f"BRIEF:\n{brief}\n\nFOUND SO FAR:\n{seen}\n\nJSON:", num_predict=100)
        except Exception:
            # The refine while-loop already treats [] as "stop refining" — a generate failure
            # degrades to a clean stop, not a lost analyst (R3 review).
            return []
        parsed = _extract_json(raw)
        qs = [str(q).strip() for q in parsed if str(q).strip()] if isinstance(parsed, list) else []
        return qs[:2]

    @staticmethod
    def _ev(i: int, e: dict) -> str:
        # full-page extract when we fetched one (denser grounding), else the snippet.
        # show the date (real or hinted) so the model can prefer the freshest source (R18).
        d = e.get("date") or e.get("date_hint")
        dated = f", {d}" if d else ""
        if e.get("page"):
            return f"[S{i+1}] {e['title']} ({e['host']}{dated})\n     EXTRACT: {e['page'][:1500]}"
        return f"[S{i+1}] {e['title']} ({e['host']}{dated})\n     {e['snippet'][:300]}"

    def _source_block(self, evidence: list[dict], local: list[dict] | None) -> str:
        """The SOURCES block the analyst drafts from — web extracts as [S#], library docs as [L#],
        spotlight-wrapped. Shared by _draft and the self-repair pass so both show the model the
        same source text (corrections then align with what was originally cited)."""
        from council.sanitize import spotlight
        web_block = "\n".join(self._ev(i, e) for i, e in enumerate(evidence))
        local_block = ""
        if local:
            local_block = "\n\nYOUR DOCUMENTS (cite as [L#]):\n" + "\n".join(
                f"[L{i+1}] {d['title']}\n     {d['text'][:1500]}" for i, d in enumerate(local))
        return spotlight((web_block + local_block).strip())

    def _draft(self, brief: str, evidence: list[dict], local: list[dict] | None = None) -> tuple[str, int]:
        src_block = self._source_block(evidence, local)
        geo = self.country not in ("", "local", "your location")
        role = (f"You are a researcher physically located in {self.country}, writing your "
                "contribution to a multi-country research report."
                if geo else
                "You are an independent research analyst writing your contribution to a "
                "multi-analyst research report.")
        if self.angle:
            role += f" Your assigned angle: {self.angle} — go deep on it; others cover the rest."
        vantage = (f"  • Add one short paragraph: what looks different from {self.country}'s "
                   "vantage (local sources, local context), if anything.\n" if geo else "")
        cite = " and ".join(p for p in (
            ("[S#] for web sources" if evidence else ""),
            ("[L#] for your documents" if local else "")) if p) or "[S#]"
        lens_instruction = LENSES.get(self.lens, LENSES["neutral"])
        return self._generate(
            f"{role} Today is {self._today()}. Using ONLY the sources below, "
            "write your findings on the brief:\n"
            f"  • {lens_instruction}\n"
            "  • Lead with the most decision-relevant findings; concrete numbers and dates.\n"
            "  • CURRENCY MATTERS: when sources span time or conflict, trust the MOST RECENT "
            "(sources are listed with their dates, freshest first), and STATE the date of any "
            "time-sensitive fact. Do NOT rely on your own training-time memory for current "
            "dates/figures — only what the sources below say.\n"
            f"  • Cite every claim with its marker ({cite}). Never invent sources or facts.\n"
            f"{vantage}"
            "  • If the sources are thin on some aspect, say so honestly.\n"
            "  • 250-400 words. No preamble.\n\n"
            f"BRIEF:\n{brief}\n\nSOURCES:\n{src_block}\n\nFINDINGS:", num_predict=700)

    # ------------------------------------------------------------------ citation-fidelity critic (R35/D51)
    @staticmethod
    def _evidence_extracts(evidence: list[dict], local: list[dict] | None) -> dict[str, str]:
        """The marker→source-text map the draft was written from: the exact ~1500-char extract each
        [S#]/[L#] pointed at (full-page fetch when we have one, else the snippet). Mirrors the
        evidence-capture the offline eval scores against (the PW_CAPTURE_EVIDENCE block below), so the
        inline critic and scripts/eval_citation_fidelity.py judge a claim against identical text."""
        m = {f"S{i+1}": (e.get("page") or e.get("snippet") or "")[:1500]
             for i, e in enumerate(evidence)}
        for i, d in enumerate(local or []):
            m[f"L{i+1}"] = (d.get("text") or "")[:1500]
        return m

    def _repair_draft(self, brief: str, draft: str, evidence: list[dict],
                      local: list[dict] | None) -> str:
        """Best-effort citation-fidelity self-repair. Score the draft's cited claims against the exact
        evidence the model saw; if any are UNGROUNDED or assert a multi-digit number absent from their
        source (the fabricated-statistic failure the trial found), do ONE bounded re-prompt to
        correct/remove ONLY those. Accept the revision only if it STRICTLY reduces the unsupported set
        without losing grounded claims or gutting the content — so by fidelity's own measure the pass
        can never lower a report's grounding; on any doubt it returns the original. Callers also wrap
        this in try/except, so a model/parse failure degrades to the un-repaired draft, never a crash."""
        from council import fidelity
        body = fidelity.split_draft(draft)          # score the findings, not any appended source list
        extracts = self._evidence_extracts(evidence, local)
        before = fidelity.unsupported_claims(body, extracts)
        if not before:
            return draft                            # already clean → no model call at all
        g_before, _ = fidelity.grounded_counts(body, extracts)
        gw_before = fidelity.grounded_word_count(body, extracts)
        flagged = "\n".join(
            f"- {f['claim']}" + (f"  [these numbers are not in the cited source(s): "
                                 f"{', '.join(f['missing_numbers'])}]" if f["missing_numbers"] else "")
            for f in before[:12])
        src_block = self._source_block(evidence, local)
        revised, _ = self._generate(
            "You wrote the FINDINGS below, citing the SOURCES below. A grounding check flagged these "
            "sentences as asserting a specific fact (a number, date, name, or claim) that the source(s) "
            "they cite do NOT contain:\n"
            f"{flagged}\n\n"
            "Rewrite the findings so every flagged sentence states ONLY what its cited sources support "
            "— correct the detail to match a source, or delete just the unsupported detail. Change "
            "NOTHING else: keep every other sentence word-for-word and keep every [S#]/[L#] marker "
            "exactly where it is. No preamble, similar length.\n\n"
            f"SOURCES:\n{src_block}\n\nFINDINGS:\n{body}\n\nREVISED FINDINGS:", num_predict=700)
        revised = (revised or "").strip()
        if not revised:
            return draft
        # Anti-gaming guards (review R35): the fabrication-risk set can also "shrink" if the model
        # strips/mangles a citation rather than fixing the claim — the marker-less sentence just falls
        # out of the score. Refuse a revision that (a) cites a marker absent from the sources it was
        # given (an invented/dangling [S#]), or (b) leaves a previously-flagged claim's text in place
        # un-corrected (only its citation removed). Both would raise the grounded RATE by hiding a
        # fabrication instead of correcting it.
        revised_body = fidelity.split_draft(revised)
        if any(m not in extracts for m in fidelity.markers_in(revised_body)):
            return draft                          # invented/dangling citation → refuse
        if fidelity.unsupported_survived(before, revised):
            return draft                          # a flagged number survived un-cited → refuse
        after = fidelity.unsupported_claims(revised, extracts)
        g_after, _ = fidelity.grounded_counts(revised, extracts)
        accept = (len(after) < len(before)                     # strictly fewer fabrication-risk claims
                  and g_after >= g_before                      # kept every grounded claim
                  and len(revised.split()) >= 0.7 * gw_before)  # kept the grounded prose (no gutting)
        return revised if accept else draft

    # ------------------------------------------------------------------ entry
    def research(self, brief: str, on_progress: Callable[[str], None] | None = None) -> dict:
        # R26: stage-level progress for a caller that wants it (council/local.py's per-analyst
        # wiring, the MCP progress bridge). Kept OUTSIDE the private stage methods themselves
        # (_plan_queries/_refine_queries/_draft/_repair_draft) — those are monkeypatched with
        # fixed-arity lambdas across several tests, so their signatures must not change; this
        # local closure wraps each call site here instead. Swallows exceptions like
        # council.local's own _make_emit — a broken progress sink must never break a run.
        def _emit(msg: str) -> None:
            if on_progress:
                try:
                    on_progress(msg)
                except Exception:
                    pass

        t0 = time.monotonic()
        evidence: list[dict] = []
        seen_urls: set[str] = set()
        today = self._today()
        # Does the brief want CURRENT info? Drives both year-injection (R19) and the
        # freshness reordering (R18). Computed once up front so _collect can close over it.
        fresh = is_time_sensitive(f"{brief} {self.angle}")
        # Breaking briefs research deeper (R19/D31); everything below keys off this, not self.depth.
        depth = self._bumped_depth(brief)

        # Local-documents retrieval (D19): draw on the private library alongside the web.
        local: list[dict] = []
        if self.scope in ("both", "local"):
            _emit("searching your library…")
            try:
                from council.library import Library
                k = {"quick": 4, "deep": 8}.get(depth, 6)
                hits = Library().search(brief + (" " + self.angle if self.angle else ""), k=k)
                # collapse chunks to ONE entry per document so [L#] numbering is identical
                # in the draft prompt and the source listing (no dangling local citations)
                by_doc: dict = {}
                for h in hits:
                    e = by_doc.setdefault(h["title"], {"title": h["title"],
                                                       "source": h["source"], "text": ""})
                    if len(e["text"]) < 2000:
                        e["text"] = (e["text"] + " " + h["text"]).strip()
                local = list(by_doc.values())
            except Exception:
                local = []

        def _collect(queries: list[str], per_query: int) -> None:
            for q in queries:
                # dynamic source routing (R17/D29): always the egress-localized web (the moat),
                # plus arXiv/Wikipedia when the query signals academic/encyclopedic intent. The
                # extras are queried shallower so they augment rather than crowd out web results.
                for engine in route_engines(q):
                    n = per_query if engine == "web" else max(2, per_query - 1)
                    # R19/D31: pin the current year into time-sensitive WEB queries so search
                    # returns CURRENT results (not the SEO-dominant historical page) — the fix
                    # R18's recency RANKING can't make. Web only: arXiv/Wikipedia don't SEO-stale.
                    eq = inject_recency(q, today, fresh) if engine == "web" else q
                    for row in search_structured(eq, max_results=n, engine=engine):
                        if row["url"] in seen_urls:
                            continue
                        seen_urls.add(row["url"])
                        evidence.append(row)

        if self.scope in ("both", "web"):
            _emit("planning search queries…")
            _collect(self._plan_queries(brief), per_query=4)
            _emit(f"searching the web — {len(evidence)} source(s) so far…")
        # Adaptive gap-driven deepening (R36/D52): keep issuing follow-up queries while the model
        # still names gaps AND a round keeps surfacing NEW sources — instead of a fixed round count.
        # Hard bounds keep it from ever running away: a max round count, a wall-clock budget (the
        # essential guard — each round stacks a generate + searches), and a source ceiling. Defaults
        # reproduce the old fixed counts (standard=1-2 rounds, deep more) for a well-covered brief.
        # The deadline is budgeted from HERE (after plan+first search), not t0 (R16 review) — on a
        # slow box, plan+first-search alone can burn most of a t0-anchored budget, silently starving
        # every refine round even though the analyst never explicitly "went shallow".
        refine_start = time.monotonic()
        rounds, max_rounds, deadline_hit = 0, 0, False
        if evidence and self.scope in ("both", "web") and depth != "quick":
            max_rounds = _num_env("PW_RESEARCH_MAX_ROUNDS", {"deep": 4}.get(depth, 2), int)
            deadline = refine_start + _num_env("PW_RESEARCH_DEADLINE", 240.0, float)
            max_sources = _num_env("PW_RESEARCH_MAX_SOURCES", 30, int)
            while rounds < max_rounds and len(seen_urls) < max_sources:
                if time.monotonic() >= deadline:
                    deadline_hit = True
                    break
                _emit(f"refining search — round {rounds + 1} of {max_rounds}…")
                follow = self._refine_queries(brief, evidence)
                if not follow:                        # model says the brief is well covered → stop
                    break
                before = len(seen_urls)
                _collect(follow, per_query=3)
                rounds += 1
                if len(seen_urls) == before:          # a round found nothing new → search saturated
                    break
        # Freshness-biased ordering (R18/D30): the council's edge is CURRENCY, so sniff a date
        # hint for each source and — ONLY when the brief actually cares about recency (fresh,
        # computed up front) — lead with the most-recently-dated ones (they then survive the cap
        # AND get page-fetched first). For stable-fact briefs we leave relevance order alone, so an
        # authoritative older source isn't buried under a recent repost (review R18). Undated last.
        for e in evidence:
            e["date_hint"] = extract_date_hint(e.get("url", ""), e.get("snippet", ""))
        if fresh:
            evidence[:] = order_by_recency(evidence)
        elif os.environ.get("PW_RESEARCH_RERANK", "1") != "0" and len(evidence) > 1:
            # Non-temporal briefs have no recency signal, so evidence is in raw search-backend order —
            # the cap and page-fetch below would then draft from whatever the engine ranked first.
            # Rerank by relevance-to-brief so the best sources survive the cap AND get page-fetched
            # (R36/D52). Shared listwise reranker; a permutation (never drops) with identity-on-failure,
            # so it is strictly additive. Temporal briefs keep recency ordering (untouched above).
            try:
                from council.rerank import rerank_listwise
                q = f"{brief} {self.angle}".strip()
                passages = [f"{e.get('title', '')} {e.get('snippet', '')}".strip() for e in evidence]
                order = rerank_listwise(q, passages, base_url=self.ollama_base)
                evidence[:] = [evidence[j] for j in order]
            except Exception:
                pass
        cap = {"quick": 8, "deep": 16}.get(depth, 12)
        evidence[:] = evidence[:cap]   # keep the prompt within small-model context

        # Full-page evidence (D17): fetch the top result pages and draft from extracts —
        # the single biggest quality lever the ecosystem leaders proved. Best-effort.
        if self.page_evidence and evidence:
            n_pages = {"quick": 2, "deep": 4}.get(depth, 3)
            _emit(f"fetching {min(n_pages, len(evidence))} page(s)…")
            for e in evidence[:n_pages]:
                try:
                    e["page"], e["date"] = fetch_extract(e["url"], max_chars=1500, with_date=True)
                except Exception:
                    pass
            # a real fetched date beats the hint; re-order so the freshest leads (time-sensitive only)
            if fresh:
                evidence[:] = order_by_recency(evidence)

        if not evidence and not local:
            # Nothing found anywhere — honest no-sources note; the judge scores it low (correct).
            where = "web sources" if self.scope == "web" else (
                "documents in your library" if self.scope == "local" else "web or local sources")
            text = f"(No {where} reachable for this brief; no findings to report.)"
            return {"text": text, "tokens": 0,
                    "elapsed_s": round(time.monotonic() - t0, 2),
                    "research": {"country": self.country, "sources": [], "local_sources": [],
                                 "refine_rounds": rounds, "refine_rounds_target": max_rounds,
                                 "deadline_hit": deadline_hit}}

        # Slow/contended nodes: retry once with a smaller prompt; if synthesis still fails,
        # contribute the SOURCES alone — this node's geo-discovery is valuable by itself.
        _emit("drafting findings…")
        try:
            draft, tokens = self._draft(brief, evidence, local)
        except Exception:
            try:
                evidence[:] = evidence[:7]
                draft, tokens = self._draft(brief, evidence, local[:4] if local else None)
            except Exception:
                draft, tokens = (f"(This {self.country} node found the sources below but could "
                                 "not synthesize in time — titles and links are its findings.)", 0)
        # Inference-time citation-fidelity self-repair (R35/D51): catch plausible-but-wrong cited
        # specifics before the report is saved. Self-verifying (accepted only if it lowers the
        # unsupported set) and best-effort — never allowed to break a run. PW_FIDELITY_REPAIR=0 opts
        # out (A/B measurement, speed). Skipped when there are no sources to check a claim against.
        repair_trace = None
        if os.environ.get("PW_FIDELITY_REPAIR", "1") != "0" and draft and (evidence or local):
            _emit("checking citation fidelity…")
            pre_repair = draft
            try:
                draft = self._repair_draft(brief, draft, evidence, local)
            except Exception:
                pass
            # Eval-only trace (R35): PW_FIDELITY_TRACE=1 records the pre- and post-repair draft so
            # eval_citation_fidelity --paired can score BOTH against the SAME captured evidence — a
            # within-run, confound-free measure of the repair effect (unlike --compare, which re-
            # researches each arm). Local-only, same PW_COORDINATOR guard as evidence capture.
            if os.environ.get("PW_FIDELITY_TRACE") == "1" and not os.environ.get("PW_COORDINATOR"):
                repair_trace = {"pre": pre_repair, "post": draft, "changed": draft != pre_repair}
        sources = [{"id": f"S{i+1}", "title": e["title"], "url": e["url"], "host": e["host"],
                    "date": e.get("date") or e.get("date_hint", "")}
                   for i, e in enumerate(evidence)]
        local_sources = [{"id": f"L{i+1}", "title": d["title"], "source": d["source"]}
                         for i, d in enumerate(local)]
        # Evidence capture (R15/D27): off by default — when PW_CAPTURE_EVIDENCE=1 attach the
        # exact extract the model SAW (full-page fetch when we have one, else the snippet) so
        # the citation-fidelity eval can grade claims against it with no network re-fetch and
        # no page-drift. Bounded to what the draft prompt actually used (1500 chars).
        # LOCAL-ONLY by construction: suppressed whenever this process is a federated worker
        # (PW_COORDINATOR set), so untrusted page text is NEVER POSTed to the coordinator — the
        # eval drives ResearchWorker in-process, where PW_COORDINATOR is unset (review: EXTRACT-
        # FEDERATION-LEAK).
        if os.environ.get("PW_CAPTURE_EVIDENCE") == "1" and not os.environ.get("PW_COORDINATOR"):
            for s, e in zip(sources, evidence):
                s["extract"] = (e.get("page") or e.get("snippet") or "")[:1500]
            for s, d in zip(local_sources, local):
                s["extract"] = (d.get("text") or "")[:1500]
        blocks = []
        if sources:
            label = (f"WEB SOURCES ({self.country})"
                     if self.country not in ("", "local", "your location") else "WEB SOURCES")
            blocks.append(label + ":\n" + "\n".join(
                f"[{s['id']}] {s['title']}" + (f" ({s['date']})" if s['date'] else "") + f" — {s['url']}"
                for s in sources))
        if local_sources:
            # de-dup by document title (multiple chunks → one listing)
            seen_t, doc_lines = set(), []
            for s in local_sources:
                if s["title"] in seen_t:
                    continue
                seen_t.add(s["title"])
                doc_lines.append(f"[{s['id']}] {s['title']} — {s['source']}")
            blocks.append("YOUR DOCUMENTS:\n" + "\n".join(doc_lines))
        src_list = "\n\n".join(blocks)
        research = {"country": self.country, "sources": sources, "local_sources": local_sources,
                    "refine_rounds": rounds, "refine_rounds_target": max_rounds,
                    "deadline_hit": deadline_hit}
        if repair_trace is not None:
            research["repair_trace"] = repair_trace
        return {
            "text": f"{draft}\n\n{src_list}",
            "tokens": tokens,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "research": research,
        }
