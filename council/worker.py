#!/usr/bin/env python3
"""
council/worker.py — A "perspective" agent
==========================================
One worker = one contributor's machine running ONE model, optionally with a
distinct LENS (angle of attack) and a COUNTRY tag. The diversity of the council
comes from three stacked axes:

  • different MODELS  (gemma vs qwen vs mistral — genuinely different training)
  • different LENSES  (optimist vs skeptic vs first-principles — different prompts)
  • different COUNTRIES (the real moat: a worker that researches the live web from
                         inside Brazil sees different sources than one in Japan)

A worker returns an OWNED DELIVERABLE — an answer it produced — never a tunnel for
someone else's traffic. That bright line is what keeps Passive Workers clear of the
residential-proxy / exit-node legal trap. The optional `web_context` is retrieved by
the worker's OWN agent and handed in as text; wiring real per-country search is the
next step (and the reason we need a second machine abroad).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests

OLLAMA_BASE = "http://localhost:11434"

# A small library of lenses — different prompts pull different ideas out of the same model.
LENSES: dict[str, str] = {
    "neutral": "Answer thoroughly and accurately.",
    "opportunity": "Focus on the strongest opportunities, upsides, and what could go right.",
    "skeptic": "Focus on risks, failure modes, hidden costs, and what could go wrong.",
    "first_principles": "Reason from first principles; question assumptions others would take for granted.",
    "practical": "Be concrete and actionable; prefer specific steps over abstractions.",
}


@dataclass
class Answer:
    worker_id: str
    model: str
    lens: str
    country: str
    text: str
    tokens: int
    elapsed_s: float


@dataclass
class PerspectiveWorker:
    worker_id: str
    model: str
    lens: str = "neutral"
    country: str = "local"
    temperature: float = 0.7          # natural divergence between workers
    num_predict: int = 500
    ollama_base: str = OLLAMA_BASE
    # Optional hook: a function (question) -> retrieved web context string, run by
    # THIS worker's own agent. Left None in the MVP; this is where per-country
    # search plugs in once a second machine abroad joins.
    web_search: Optional[Callable[[str], str]] = None

    def answer(self, question: str) -> Answer:
        lens_instruction = LENSES.get(self.lens, LENSES["neutral"])
        context_block = ""
        if self.web_search is not None:
            try:
                ctx = self.web_search(question)
                if ctx:
                    context_block = (
                        f"\n\nResearch you gathered from your local ({self.country}) web sources:\n{ctx}\n"
                        "Use it where relevant and cite what you used.\n"
                    )
            except Exception:
                context_block = ""  # web is best-effort; never block the answer

        prompt = (
            f"{lens_instruction}\n\n"
            f"Question:\n{question}\n"
            f"{context_block}\n"
            "Give your best standalone answer."
        )

        t0 = time.monotonic()
        resp = requests.post(
            f"{self.ollama_base}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": self.temperature, "num_predict": self.num_predict},
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.monotonic() - t0
        text = (data.get("response") or "").strip()
        tokens = data.get("eval_count") or len(text.split())
        return Answer(
            worker_id=self.worker_id,
            model=self.model,
            lens=self.lens,
            country=self.country,
            text=text,
            tokens=tokens,
            elapsed_s=elapsed,
        )

    def label(self) -> str:
        return f"{self.worker_id} [{self.model}/{self.lens}/{self.country}]"
