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

import json
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests

OLLAMA_BASE = "http://localhost:11434"


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
            },
            timeout=400,
        )
        resp.raise_for_status()
        return (resp.json().get("response") or "").strip()

    # ------------------------------------------------------------------ 1. SCORE
    def score(self, question: str, answers: list) -> list[ScoredCandidate]:
        """answers: list[council.worker.Answer]. Blind, shuffled, deterministic mapping."""
        # Deterministic shuffle (rotate by length) so the judge can't learn an ordering.
        order = list(range(len(answers)))
        rot = len(answers) % max(1, len(answers))
        order = order[rot:] + order[:rot]

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
                    by_display[int(obj["answer"])] = (float(obj["score"]), str(obj.get("reason", "")))
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
            "  • Write ONE direct answer to the asker — never mention 'Perspective N' or that "
            "multiple answers exist.\n"
            f"  • Length target: {max(60, int(longest * 0.8))}–{longest} words — as substantive as the "
            "best perspective, never padded, never a stub. End with one line 'Diverse angles: …' "
            "(≤15 words) naming the distinct contributions.\n\n"
            f"QUESTION:\n{question}\n\n"
            f"PERSPECTIVES:\n{joined}\n\n"
            "Write the tight merged answer now."
        )
        return self._generate(prompt, num_predict=min(900, max(300, longest * 2)))

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
        order = list(range(len(answers)))
        order = order[len(answers) % max(1, len(answers)):] + order[:len(answers) % max(1, len(answers))]
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
            'leading with the answer. Write it as ONE direct answer to the asker — never mention '
            'Answer 1/2/3, candidates, or that multiple answers exist"}\n'
            "Judge on merit only; you do not know who wrote them.\n\n"
            f"QUESTION:\n{question}\n\nCANDIDATES:\n{joined}\n\nJSON:"
        )
        raw = self._generate(prompt, num_predict=min(1100, max(500, longest * 3)))
        parsed = _extract_json(raw) or {}

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
            pt = str(u.get("point", "")).strip()
            if wid and pt:
                unique.append({"worker_id": wid, "point": pt})

        consensus = [str(x).strip() for x in (parsed.get("consensus") or []) if str(x).strip()][:6]
        disagreements = [
            {"point": str(d.get("point", "")).strip(), "sides": str(d.get("sides", "")).strip()}
            for d in (parsed.get("disagreements") or []) if isinstance(d, dict) and d.get("point")
        ][:6]
        merged = str(parsed.get("merge", "")).strip()
        if not merged:   # fall back to the dedicated merge prompt if JSON merge was empty
            merged = self.merge(question, answers)
        return {"scores": scores, "merged": merged,
                "council": {"consensus": consensus, "disagreements": disagreements, "unique": unique}}

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
        parsed = _extract_json(raw) or {}
        winner = str(parsed.get("winner", "tie")).strip().upper()
        winner = winner if winner in {"A", "B", "TIE"} else "TIE"
        return {"winner": "tie" if winner == "TIE" else winner, "reason": str(parsed.get("reason", ""))}
