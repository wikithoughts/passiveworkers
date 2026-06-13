#!/usr/bin/env python3
"""
council/judge.py — Score, then MERGE
====================================
The judge is what turns "many answers" into "better intelligence":

  1. SCORE — reads the candidate answers BLIND (anonymized, shuffled order so neither
     identity nor position biases the result) and rates each 0-10. The scores feed the
     ledger, so good answers earn more credit (ideas compete).
  2. MERGE — synthesizes the candidates into one answer that is better than any single
     one: it keeps the consensus, ADDS the unique points only one perspective found, and
     reconciles disagreements instead of hiding them. This is the diversity dividend.
  3. COMPARE — a blind A/B check (used for verification) of merged vs. best-single.

Use a STRONG model, ideally a different family from the workers, at temperature 0 so
judging is steady while the workers diverge.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests

from council.sanitize import strip_invisible

OLLAMA_BASE = "http://localhost:11434"


def _blind_order(n: int, seed: str) -> list[int]:
    """A deterministic, CONTENT-derived rotation of range(n) so the judge sees answers in an order
    that neither tracks worker identity NOR is a fixed positional pattern it could learn. Stable
    across processes (sha256, not the PYTHONHASHSEED-salted builtin hash) so judging is reproducible
    and testable. Replaces the prior `len % max(1,len)` rotation, which was ALWAYS 0 — a silent
    no-op that left position-bias unmitigated despite the docstrings (review)."""
    order = list(range(n))
    if n <= 1:
        return order
    rot = int(hashlib.sha256((seed or "").encode("utf-8", "replace")).hexdigest(), 16) % n
    return order[rot:] + order[:rot]


def _extract_json(text: str):
    """Tolerantly pull the first JSON value out of a model response."""
    # Strip code fences.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate.strip())
    except Exception:
        pass
    # Find the first balanced [...] or {...} span.
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = candidate.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(candidate)):
            if candidate[i] == open_ch:
                depth += 1
            elif candidate[i] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start : i + 1])
                    except Exception:
                        break
    return None


_CITE_TOKEN = re.compile(r"[SL]\d+")
_CITE_SPAN = re.compile(r"\[[SL]\d+(?:\s*,\s*[SL]\d+)*\]")


def _known_markers(answers) -> set[str]:
    """Every [S#]/[L#] marker that appears in the source answers — the only citations a
    synthesis is allowed to keep."""
    return set(_CITE_TOKEN.findall(" ".join(getattr(a, "text", "") or "" for a in answers)))


def _drop_invented_markers(text: str, known: set[str]) -> str:
    """Remove any citation marker the synthesis INVENTED (not present in any source answer),
    so a merge can't fabricate a citation even if it ignores the prompt rule (review
    CITATION_MERGE_001). It may still drop/renumber — that the prompt discourages — but it can
    never point at a source that was never cited."""
    if "[" not in text:
        return text

    def _repl(m: re.Match) -> str:
        kept = [t for t in _CITE_TOKEN.findall(m.group(0)) if t in known]
        return ("[" + ", ".join(kept) + "]") if kept else ""

    return _CITE_SPAN.sub(_repl, text)


@dataclass
class ScoredCandidate:
    worker_id: str
    score: float
    reason: str


@dataclass
class Judge:
    model: str
    ollama_base: str = OLLAMA_BASE
    num_predict: int = 900

    def _generate(self, prompt: str, num_predict: Optional[int] = None) -> str:
        resp = requests.post(
            f"{self.ollama_base}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": num_predict or self.num_predict},
                "keep_alive": os.environ.get("PW_OLLAMA_KEEP_ALIVE", "30m"),  # warm judge (R17)
            },
            # CPU-only/busy machines need headroom (measured: a 4B judge can exceed 400s
            # under contention); configurable like the worker/researcher timeouts.
            timeout=float(os.environ.get("PW_JUDGE_TIMEOUT",
                                         os.environ.get("PW_OLLAMA_TIMEOUT", "400"))),
        )
        resp.raise_for_status()
        return (resp.json().get("response") or "").strip()

    # ------------------------------------------------------------------ 1. SCORE
    def score(self, question: str, answers: list) -> list[ScoredCandidate]:
        """answers: list[council.worker.Answer]. Blind, shuffled, deterministic mapping."""
        # Deterministic, content-derived rotation so neither identity nor a fixed position pattern
        # can bias the judge — and it actually rotates (the old `len % len` was always 0).
        order = _blind_order(len(answers), question)

        blocks = []
        for display_idx, real_idx in enumerate(order, start=1):
            blocks.append(f"--- Answer {display_idx} ---\n{answers[real_idx].text}")
        joined = "\n\n".join(blocks)

        prompt = (
            "You are an impartial judge. Score each candidate answer to the question on a "
            "0-10 scale for correctness, depth, usefulness, and insight. Judge only on merit; "
            "you do not know who wrote them.\n\n"
            f"QUESTION:\n{question}\n\n"
            f"CANDIDATES:\n{joined}\n\n"
            "Respond with ONLY a JSON array, one object per answer, like:\n"
            '[{"answer": 1, "score": 7.5, "reason": "..."}, ...]'
        )
        raw = self._generate(prompt, num_predict=600)
        parsed = _extract_json(raw) or []

        # Map display index -> real worker.
        by_display = {}
        if isinstance(parsed, list):
            for obj in parsed:
                try:
                    by_display[int(obj["answer"])] = (
                        float(obj["score"]), strip_invisible(str(obj.get("reason", "")).strip()))
                except Exception:
                    continue

        results: list[ScoredCandidate] = []
        for display_idx, real_idx in enumerate(order, start=1):
            score, reason = by_display.get(display_idx, (5.0, "(unscored — defaulted)"))
            score = max(0.0, min(10.0, score))
            results.append(ScoredCandidate(answers[real_idx].worker_id, score, reason))
        return results

    # ------------------------------------------------------------------ 2. MERGE
    def merge(self, question: str, answers: list) -> str:
        blocks = [f"--- Perspective {i + 1} ---\n{a.text}" for i, a in enumerate(answers)]
        joined = "\n\n".join(blocks)
        longest = max((len(a.text.split()) for a in answers), default=200)
        prompt = (
            "You are a synthesizer. Several independent perspectives answer the same question below. "
            "Write ONE answer that is strictly BETTER and NO LONGER than the best single perspective — "
            "win on DENSITY, not length.\n"
            "Rules:\n"
            "  • Integrate the views — do NOT append them or describe each separately.\n"
            "  • Include the strongest points and any correct insight only one perspective found.\n"
            "  • If they disagree, state the resolution in ONE line.\n"
            "  • Cut filler, repetition, hedging, and preamble. Lead with the answer.\n"
            "  • Preserve any [S#]/[L#] citation markers EXACTLY as written next to the claims "
            "they support; never renumber, merge, or invent a marker (R17).\n"
            "  • Write ONE direct answer to the asker — never mention 'Perspective N' or that "
            "multiple answers exist.\n"
            f"  • Length target: {max(60, int(longest * 0.8))}–{longest} words — as substantive as the "
            "best perspective, never padded, never a stub. End with one line 'Diverse angles: …' "
            "(≤15 words) naming the distinct contributions.\n\n"
            f"QUESTION:\n{question}\n\n"
            f"PERSPECTIVES:\n{joined}\n\n"
            "Write the tight merged answer now."
        )
        # the synthesized text is the last untrusted-output hop before the report → strip hidden
        # chars, then drop any citation marker the synthesis invented (keep merges honest, R17)
        out = strip_invisible(self._generate(prompt, num_predict=min(900, max(300, longest * 2))))
        return _drop_invented_markers(out, _known_markers(answers))

    # ------------------------------------------------------------------ DELIBERATE (one blind call)
    def deliberate(self, question: str, answers: list) -> dict:
        """
        One blind pass that powers the UI: per-answer scores, a terse merge (TL;DR), and the
        'council read' — where the perspectives AGREE, where they DIFFER, and each UNIQUE point.
        Returns {"scores": {worker_id: score}, "merged": str,
                 "council": {"consensus": [...], "disagreements": [{"point","sides"}],
                             "unique": [{"worker_id","point"}]}}.
        Answers are anonymized + rotated so identity/position can't bias the read.
        """
        order = _blind_order(len(answers), question)
        blocks = []
        for disp, real in enumerate(order, start=1):
            blocks.append(f"--- Answer {disp} ---\n{answers[real].text}")
        joined = "\n\n".join(blocks)
        longest = max((len(a.text.split()) for a in answers), default=200)
        prompt = (
            "You are an impartial council secretary. Read the candidate answers to the question and "
            "return STRICT JSON only, no prose, with this exact shape:\n"
            '{"scores":[{"answer":1,"score":0-10}],'
            '"consensus":["points all/most answers agree on"],'
            '"disagreements":[{"point":"what they differ on","sides":"who says what"}],'
            '"unique":[{"answer":N,"point":"a valuable point only answer N made"}],'
            f'"merge":"a TIGHT synthesis of {max(60, int(longest * 0.8))}-{longest} words — as substantive '
            'as the best candidate, never padded, never a stub; integrated not appended, '
            'leading with the answer. Preserve any [S#]/[L#] citation markers exactly as written; '
            'never renumber or invent one. Write it as ONE direct answer to the asker — never mention '
            'Answer 1/2/3, candidates, or that multiple answers exist"}\n'
            "Judge on merit only; you do not know who wrote them.\n\n"
            f"QUESTION:\n{question}\n\nCANDIDATES:\n{joined}\n\nJSON:"
        )
        raw = self._generate(prompt, num_predict=min(1100, max(500, longest * 3)))
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):   # models sometimes emit a bare list — never crash
            parsed = {}

        def _wid(disp_idx: int):
            try:
                return answers[order[int(disp_idx) - 1]].worker_id
            except (ValueError, IndexError, TypeError):
                return None

        scores: dict[str, float] = {}
        for obj in parsed.get("scores", []) if isinstance(parsed.get("scores"), list) else []:
            wid = _wid(obj.get("answer"))
            if wid is not None:
                try:
                    scores[wid] = max(0.0, min(10.0, float(obj.get("score", 5.0))))
                except (TypeError, ValueError):
                    scores[wid] = 5.0
        for a in answers:                       # default any unscored answer
            scores.setdefault(a.worker_id, 5.0)

        unique = []
        for u in parsed.get("unique", []) if isinstance(parsed.get("unique"), list) else []:
            wid = _wid(u.get("answer"))
            pt = strip_invisible(str(u.get("point", "")).strip())
            if wid and pt:
                unique.append({"worker_id": wid, "point": pt})

        def _sides(v) -> str:
            if isinstance(v, dict):    # models sometimes return {"Answer 1": "...", ...}
                return " · ".join(f"{k}: {x}" for k, x in v.items())
            if isinstance(v, list):    # …or ["Answer 1: ...", "Answer 2: ..."]
                return " · ".join(str(x) for x in v)
            return str(v or "").strip()

        # every council-read string is model output → strip smuggled hidden characters
        consensus = [strip_invisible(str(x).strip()) for x in (parsed.get("consensus") or []) if str(x).strip()][:6]
        disagreements = [
            {"point": strip_invisible(str(d.get("point", "")).strip()),
             "sides": strip_invisible(_sides(d.get("sides")))}
            for d in (parsed.get("disagreements") or []) if isinstance(d, dict) and d.get("point")
        ][:6]
        merged = _drop_invented_markers(strip_invisible(str(parsed.get("merge", "")).strip()),
                                        _known_markers(answers))
        if not merged:   # fall back to the dedicated merge prompt if JSON merge was empty (already guarded)
            merged = self.merge(question, answers)
        return {"scores": scores, "merged": merged,
                "council": {"consensus": consensus, "disagreements": disagreements, "unique": unique}}

    # ------------------------------------------------------------------ 2b. COMPILE REPORT (D13)
    def compile_report(self, question: str, contributions: list[dict], read: dict,
                       local: bool = False) -> str:
        """
        Editor pass for `research_report` jobs: one cited markdown report from the
        per-country contributions. The model writes ONLY the executive summary and the
        agreement/difference synthesis; the per-country findings (with their [S#]
        citations and source lists) are assembled verbatim by code — small models
        garble citation markers if asked to rewrite them.
        contributions: payload answers [{country, model, lens, text, research}].
        read: the deliberate() result (used as fallback + disagreement bullets).
        """
        drafts = "\n\n".join(
            f"--- Contribution from {c.get('country', '?')} ---\n{strip_invisible((c.get('text') or '')[:2500])}"
            for c in contributions)
        raw = self._generate(
            "You are the editor of a multi-country research report. Read the brief and the "
            "contributions, then reply STRICT JSON only, no prose:\n"
            '{"summary":"a 120-180 word executive summary leading with the most '
            'decision-relevant findings (concrete numbers/dates; mention which country a '
            'finding came from)",'
            '"agreements":"2-3 sentences: what the countries\' findings agree on",'
            '"differences":"2-3 sentences: where they genuinely differ and why it matters"}\n\n'
            f"BRIEF:\n{question}\n\nCONTRIBUTIONS:\n{drafts}\n\nJSON:",
            num_predict=700)
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):   # model may emit a list/garbage — never crash the report
            parsed = {}
        summary = strip_invisible(str(parsed.get("summary", "")).strip()) or str(read.get("merged", "")).strip()
        agreements = strip_invisible(str(parsed.get("agreements", "")).strip())
        differences = strip_invisible(str(parsed.get("differences", "")).strip())

        countries = sorted({c.get("country", "?") for c in contributions})
        if local:
            byline = (f"_Researched independently by {len(contributions)} local model(s) "
                      f"({', '.join(c.get('model', '?') for c in contributions)}) on this "
                      "computer, live web, compiled by a blind editor._")
        else:
            byline = (f"_Researched independently by {len(contributions)} computer(s) in "
                      f"{len(countries)} countr{'y' if len(countries) == 1 else 'ies'} "
                      f"({', '.join(countries)}), live web, compiled by a blind editor._")
        parts = [f"# Research report\n**Brief:** {question}", byline,
                 "## Executive summary", summary]
        if agreements or differences:
            parts.append("## Where the analysts agree — and differ" if local
                         else "## Where the countries agree — and differ")
            if agreements:
                parts.append(f"**Agree:** {agreements}")
            if differences:
                parts.append(f"**Differ:** {differences}")
        dis = (read.get("council") or {}).get("disagreements") or []
        if dis:
            parts.append("\n".join(f"- {d.get('point', '')}"
                                   + (f" — _{d['sides']}_" if d.get("sides") else "")
                                   for d in dis if d.get("point")))
        parts.append("## Findings by analyst" if local else "## Findings by country")
        for c in contributions:
            head = c.get("model", "") if local else f"{c.get('country', '?')} — {c.get('model', '')}"
            parts.append(f"### {head}")
            # contribution text is model output that may echo an injected source → strip hidden
            # chars but KEEP markdown layout/citations (don't collapse indentation)
            parts.append(strip_invisible(c.get("text") or "") or "_(no findings)_")
        return "\n\n".join(p for p in parts if p)

    # ------------------------------------------------------------------ 2c. SPOT-CHECK (shard_map QA)
    def spot_check(self, instruction: str, answers: list[dict]) -> dict:
        """
        QA sampler for batch jobs: each node's payload entry carries a small `sample` of
        its (item, output) pairs. One blind call scores instruction-compliance per node
        0–10 (anonymized as Worker N). Returns {"scores": {worker_id: float}}.
        """
        order = _blind_order(len(answers), instruction)
        blocks = []
        for disp, real in enumerate(order, start=1):
            sample = answers[real].get("sample") or []
            pairs = "\n".join(
                f"  ITEM: {str(s.get('item', ''))[:300]}\n  OUTPUT: {str(s.get('output', ''))[:400]}"
                for s in sample) or "  (no sample — node returned nothing)"
            blocks.append(f"--- Worker {disp} sample ---\n{pairs}")
        raw = self._generate(
            "You are a QA inspector for batch work. Each worker applied the INSTRUCTION to "
            "its items; you see a random sample per worker. Score each worker 0-10 on "
            "instruction-compliance and output quality (0 = empty/garbage, 10 = flawless). "
            'Reply STRICT JSON only: {"scores":[{"worker":1,"score":0-10}]}\n\n'
            f"INSTRUCTION:\n{instruction}\n\n" + "\n\n".join(blocks) + "\n\nJSON:",
            num_predict=200)
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            parsed = {}
        scores: dict[str, float] = {}
        for obj in parsed.get("scores", []) if isinstance(parsed.get("scores"), list) else []:
            try:
                real = order[int(obj.get("worker")) - 1]
                scores[answers[real]["worker_id"]] = max(0.0, min(10.0, float(obj.get("score", 5.0))))
            except (ValueError, IndexError, TypeError, KeyError):
                continue
        for a in answers:
            scores.setdefault(a["worker_id"], 5.0)
        return {"scores": scores, "merged": "",
                "council": {"consensus": [], "disagreements": [], "unique": []}}

    # ------------------------------------------------------------------ 3. COMPARE (verification)
    def compare(self, question: str, text_a: str, text_b: str) -> dict:
        """Blind A/B. Returns {'winner': 'A'|'B'|'tie', 'reason': str}."""
        prompt = (
            "Two answers to the same question are below. Decide which is the better answer "
            "(more complete, accurate, and useful). Judge blind.\n\n"
            f"QUESTION:\n{question}\n\n"
            f"=== Answer A ===\n{text_a}\n\n"
            f"=== Answer B ===\n{text_b}\n\n"
            'Respond with ONLY JSON: {"winner": "A" | "B" | "tie", "reason": "..."}'
        )
        raw = self._generate(prompt, num_predict=300)
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            parsed = {}
        winner = str(parsed.get("winner", "tie")).strip().upper()
        winner = winner if winner in {"A", "B", "TIE"} else "TIE"
        return {"winner": "tie" if winner == "TIE" else winner,
                "reason": strip_invisible(str(parsed.get("reason", "")).strip())}
