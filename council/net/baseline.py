#!/usr/bin/env python3
"""
council/net/baseline.py — the honest single-model baseline
===========================================================
The demand metric ("was the council more useful than one model?") is only meaningful
if the "one model" is the asker's REAL alternative. Before this module the baseline was
the council's own best answer — that measured merge-vs-ingredient, not demand.

Order of preference:
  1. A frontier API model via an OpenAI-compatible endpoint (PW_BASELINE_API_KEY set
     → OpenRouter by default). This is what an asker would actually use instead.
  2. A strong local Ollama model (PW_BASELINE_LOCAL_MODEL, default qwen3:14b) — never
     a small worker-class model.
  3. None → the UI falls back to best-single-council-answer, clearly labelled.

Runs in a background thread per job (parallel with the council), result stored on the
job row. Failures are silent by design — the baseline must never block or fail a job.
"""

from __future__ import annotations

import time
from typing import Optional

import requests

from .config import CONFIG

_PROMPT = "Answer the question directly and concretely. Be concise."


def generate_baseline(question: str) -> Optional[dict]:
    """Return {text, model, source, elapsed_s} or None. Never raises."""
    try:
        if CONFIG.baseline_api_key:
            return _via_api(question)
        if CONFIG.baseline_local_model:
            return _via_ollama(question)
    except Exception as e:
        # never fail the job, but never fail SILENTLY either (journalctl picks this up)
        print(f"[baseline] generation failed: {type(e).__name__}: {e}", flush=True)
    return None


def _via_api(question: str) -> Optional[dict]:
    t0 = time.monotonic()
    r = requests.post(
        CONFIG.baseline_api_url,
        headers={"Authorization": f"Bearer {CONFIG.baseline_api_key}",
                 "Content-Type": "application/json"},
        json={"model": CONFIG.baseline_model,
              "messages": [{"role": "system", "content": _PROMPT},
                           {"role": "user", "content": question}],
              "max_tokens": 700},
        timeout=120,
    )
    r.raise_for_status()
    text = (r.json()["choices"][0]["message"]["content"] or "").strip()
    if not text:
        return None
    return {"text": text, "model": CONFIG.baseline_model, "source": "api",
            "elapsed_s": round(time.monotonic() - t0, 1)}


def _via_ollama(question: str) -> Optional[dict]:
    t0 = time.monotonic()
    r = requests.post(
        f"{CONFIG.ollama_base}/api/generate",
        json={"model": CONFIG.baseline_local_model,
              "prompt": f"{_PROMPT}\n\nQuestion:\n{question}",
              "stream": False,
              "think": False,  # qwen3-style models reason by default; older Ollama ignores this
              "options": {"temperature": 0.4, "num_predict": 300}},
        timeout=CONFIG.baseline_timeout_s,
    )
    r.raise_for_status()
    text = (r.json().get("response") or "").strip()
    if text.startswith("<think>") and "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    if not text:
        return None
    return {"text": text, "model": CONFIG.baseline_local_model, "source": "local",
            "elapsed_s": round(time.monotonic() - t0, 1)}
