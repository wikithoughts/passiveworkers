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
from dataclasses import dataclass

import requests

from council.judge import _extract_json
from council.research import (extract_date_hint, fetch_extract, inject_recency, is_breaking,
                              is_time_sensitive, order_by_recency, route_engines,
                              search_structured)

OLLAMA_BASE = "http://localhost:11434"
_GEN_TIMEOUT = float(os.environ.get("PW_RESEARCH_GEN_TIMEOUT",
                                    os.environ.get("PW_OLLAMA_TIMEOUT", "480")))


@dataclass
class ResearchWorker:
    worker_id: str
    model: str
    lens: str = "neutral"
    country: str = "local"
    temperature: float = 0.4
    ollama_base: str = OLLAMA_BASE
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
        r = requests.post(
            f"{self.ollama_base}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False,
                  "options": {"temperature": self.temperature, "num_predict": num_predict},
                  # keep this analyst warm across its plan→refine→draft rounds (R17)
                  "keep_alive": os.environ.get("PW_OLLAMA_KEEP_ALIVE", "30m")},
            timeout=_GEN_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return (data.get("response") or "").strip(), (data.get("eval_count") or 0)

    # ------------------------------------------------------------------ rounds
    def _plan_queries(self, brief: str) -> list[str]:
        focus = (f"Focus specifically on this angle of the brief: {self.angle}\n"
                 if self.angle else "")
        raw, _ = self._generate(
            f"You are planning web research. Today is {self._today()}. Turn this brief into "
            "exactly 3 concrete, specific search queries (different angles). For anything "
            "time-sensitive, make the queries current — include the current year/month so the "
            "freshest results surface. "
            f"{focus}"
            'Reply STRICT JSON only: ["query one","query two","query three"]\n\n'
            f"BRIEF:\n{brief}\n\nJSON:", num_predict=140)
        parsed = _extract_json(raw)
        qs = [str(q).strip() for q in parsed if str(q).strip()] if isinstance(parsed, list) else []
        return qs[:3] or [brief[:200]]

    def _refine_queries(self, brief: str, evidence: list[dict]) -> list[str]:
        seen = "\n".join(f"- {e['title']} ({e['host']})" for e in evidence[:10])
        raw, _ = self._generate(
            "Given the brief and the sources found so far, name 2 follow-up search queries "
            "that fill the biggest remaining gaps (be specific; avoid repeating what is "
            'already covered). Reply STRICT JSON only: ["query one","query two"]\n\n'
            f"BRIEF:\n{brief}\n\nFOUND SO FAR:\n{seen}\n\nJSON:", num_predict=100)
        parsed = _extract_json(raw)
        qs = [str(q).strip() for q in parsed if str(q).strip()] if isinstance(parsed, list) else []
        return qs[:2]

    def _draft(self, brief: str, evidence: list[dict], local: list[dict] | None = None) -> tuple[str, int]:
        from council.sanitize import spotlight

        def _ev(i: int, e: dict) -> str:
            # full-page extract when we fetched one (denser grounding), else the snippet.
            # show the date (real or hinted) so the model can prefer the freshest source (R18).
            d = e.get("date") or e.get("date_hint")
            dated = f", {d}" if d else ""
            if e.get("page"):
                return f"[S{i+1}] {e['title']} ({e['host']}{dated})\n     EXTRACT: {e['page'][:1500]}"
            return f"[S{i+1}] {e['title']} ({e['host']}{dated})\n     {e['snippet'][:300]}"

        web_block = "\n".join(_ev(i, e) for i, e in enumerate(evidence))
        local_block = ""
        if local:
            local_block = "\n\nYOUR DOCUMENTS (cite as [L#]):\n" + "\n".join(
                f"[L{i+1}] {d['title']}\n     {d['text'][:1500]}" for i, d in enumerate(local))
        src_block = spotlight((web_block + local_block).strip())
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
        return self._generate(
            f"{role} Today is {self._today()}. Using ONLY the sources below, "
            "write your findings on the brief:\n"
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

    # ------------------------------------------------------------------ entry
    def research(self, brief: str) -> dict:
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
            _collect(self._plan_queries(brief), per_query=4)
        if evidence and self.scope in ("both", "web") and depth != "quick":
            _collect(self._refine_queries(brief, evidence), per_query=3)
            if depth == "deep":
                _collect(self._refine_queries(brief, evidence), per_query=3)
        # Freshness-biased ordering (R18/D30): the council's edge is CURRENCY, so sniff a date
        # hint for each source and — ONLY when the brief actually cares about recency (fresh,
        # computed up front) — lead with the most-recently-dated ones (they then survive the cap
        # AND get page-fetched first). For stable-fact briefs we leave relevance order alone, so an
        # authoritative older source isn't buried under a recent repost (review R18). Undated last.
        for e in evidence:
            e["date_hint"] = extract_date_hint(e.get("url", ""), e.get("snippet", ""))
        if fresh:
            evidence[:] = order_by_recency(evidence)
        cap = {"quick": 8, "deep": 16}.get(depth, 12)
        evidence[:] = evidence[:cap]   # keep the prompt within small-model context

        # Full-page evidence (D17): fetch the top result pages and draft from extracts —
        # the single biggest quality lever the ecosystem leaders proved. Best-effort.
        if self.page_evidence and evidence:
            n_pages = {"quick": 2, "deep": 4}.get(depth, 3)
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
                    "research": {"country": self.country, "sources": [], "local_sources": []}}

        # Slow/contended nodes: retry once with a smaller prompt; if synthesis still fails,
        # contribute the SOURCES alone — this node's geo-discovery is valuable by itself.
        try:
            draft, tokens = self._draft(brief, evidence, local)
        except Exception:
            try:
                evidence[:] = evidence[:7]
                draft, tokens = self._draft(brief, evidence, local[:4] if local else None)
            except Exception:
                draft, tokens = (f"(This {self.country} node found the sources below but could "
                                 "not synthesize in time — titles and links are its findings.)", 0)
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
        return {
            "text": f"{draft}\n\n{src_list}",
            "tokens": tokens,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "research": {"country": self.country, "sources": sources,
                         "local_sources": local_sources},
        }
