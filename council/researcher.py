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

import os
import time
from dataclasses import dataclass

import requests

from council.judge import _extract_json
from council.research import search_structured

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

    def _generate(self, prompt: str, num_predict: int) -> tuple[str, int]:
        r = requests.post(
            f"{self.ollama_base}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False,
                  "options": {"temperature": self.temperature, "num_predict": num_predict}},
            timeout=_GEN_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return (data.get("response") or "").strip(), (data.get("eval_count") or 0)

    # ------------------------------------------------------------------ rounds
    def _plan_queries(self, brief: str) -> list[str]:
        raw, _ = self._generate(
            "You are planning web research. Turn this brief into exactly 3 concrete, "
            "specific search queries (different angles, current-year aware). "
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

    def _draft(self, brief: str, evidence: list[dict]) -> tuple[str, int]:
        from council.sanitize import spotlight
        src_block = spotlight("\n".join(
            f"[S{i+1}] {e['title']} ({e['host']})\n     {e['snippet'][:300]}"
            for i, e in enumerate(evidence)))
        geo = self.country not in ("", "local", "your location")
        role = (f"You are a researcher physically located in {self.country}, writing your "
                "contribution to a multi-country research report."
                if geo else
                "You are an independent research analyst writing your contribution to a "
                "multi-analyst research report.")
        vantage = (f"  • Add one short paragraph: what looks different from {self.country}'s "
                   "vantage (local sources, local context), if anything.\n" if geo else "")
        return self._generate(
            f"{role} Using ONLY the sources below, "
            "write your findings on the brief:\n"
            "  • Lead with the most decision-relevant findings; concrete numbers and dates.\n"
            "  • Cite every claim with its [S#] marker. Never invent sources or facts.\n"
            f"{vantage}"
            "  • If the sources are thin on some aspect, say so honestly.\n"
            "  • 250-400 words. No preamble.\n\n"
            f"BRIEF:\n{brief}\n\nSOURCES:\n{src_block}\n\nFINDINGS:", num_predict=700)

    # ------------------------------------------------------------------ entry
    def research(self, brief: str) -> dict:
        t0 = time.monotonic()
        evidence: list[dict] = []
        seen_urls: set[str] = set()

        def _collect(queries: list[str], per_query: int) -> None:
            for q in queries:
                for row in search_structured(q, max_results=per_query):
                    if row["url"] in seen_urls:
                        continue
                    seen_urls.add(row["url"])
                    evidence.append(row)

        _collect(self._plan_queries(brief), per_query=4)
        if evidence and self.depth != "quick":
            _collect(self._refine_queries(brief, evidence), per_query=3)
            if self.depth == "deep":
                _collect(self._refine_queries(brief, evidence), per_query=3)
        cap = {"quick": 8, "deep": 16}.get(self.depth, 12)
        evidence[:] = evidence[:cap]   # keep the prompt within small-model context

        if not evidence:
            # No web access / nothing found — fall back to an honest no-sources note;
            # the judge will score it low, which is correct.
            text = (f"(No web sources reachable from this {self.country} node for this brief; "
                    "no findings to report.)")
            return {"text": text, "tokens": 0,
                    "elapsed_s": round(time.monotonic() - t0, 2),
                    "research": {"country": self.country, "sources": []}}

        # Slow/contended nodes: retry once with a smaller prompt; if synthesis still fails,
        # contribute the SOURCES alone — this node's geo-discovery is valuable by itself.
        try:
            draft, tokens = self._draft(brief, evidence)
        except Exception:
            try:
                evidence[:] = evidence[:7]
                draft, tokens = self._draft(brief, evidence)
            except Exception:
                draft, tokens = (f"(This {self.country} node found the sources below but could "
                                 "not synthesize in time — titles and links are its findings.)", 0)
        sources = [{"id": f"S{i+1}", "title": e["title"], "url": e["url"], "host": e["host"]}
                   for i, e in enumerate(evidence)]
        src_list = "\n".join(f"[{s['id']}] {s['title']} — {s['url']}" for s in sources)
        label = (f"SOURCES ({self.country})"
                 if self.country not in ("", "local", "your location") else "SOURCES")
        return {
            "text": f"{draft}\n\n{label}:\n{src_list}",
            "tokens": tokens,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "research": {"country": self.country, "sources": sources},
        }
