#!/usr/bin/env python3
"""
council/batch.py — the per-node batch worker for `shard_map` jobs (D13/D15)
============================================================================
One big job, split across computers: this node receives its SHARD of the items and
applies the instruction to each item with its local model. Wall-clock scales with
items ÷ computers — the honest "divide to save time" the marketplace sells.

With `fetch=True`, items are PUBLIC URLs: the node fetches each one itself (SSRF-guarded,
one polite request, size-capped) and returns the model's EXTRACTION — never the raw page.
The D15 bright line: a node only fetches what it could lawfully fetch alone, and only
value-added output leaves the node. No relaying, no rate-limit evasion.

Per-item failures never kill the shard — the item's output records the error and the
batch continues (the judge's spot-check and score reflect quality).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

from council.research import fetch_extract
from council.sanitize import clean, spotlight

OLLAMA_BASE = "http://localhost:11434"
_GEN_TIMEOUT = float(os.environ.get("PW_BATCH_GEN_TIMEOUT",
                                    os.environ.get("PW_OLLAMA_TIMEOUT", "480")))


@dataclass
class BatchWorker:
    worker_id: str
    model: str
    country: str = "local"
    temperature: float = 0.2
    ollama_base: str = OLLAMA_BASE

    def _generate(self, prompt: str, num_predict: int = 220) -> tuple[str, int]:
        r = requests.post(
            f"{self.ollama_base}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False,
                  "options": {"temperature": self.temperature, "num_predict": num_predict},
                  # keep the model warm across this shard's items (R17) — batch loops _generate per item
                  "keep_alive": os.environ.get("PW_OLLAMA_KEEP_ALIVE", "30m")},
            timeout=_GEN_TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()
        return (d.get("response") or "").strip(), (d.get("eval_count") or 0)

    def process(self, instruction: str, shard: list[dict], fetch: bool = False) -> dict:
        t0 = time.monotonic()
        # the instruction is the asker's TASK (kept as a directive) but still scrubbed of invisible/
        # bidi/HTML-comment injection vectors; every per-item value is untrusted DATA → spotlighted.
        instruction = clean(instruction)
        results, tokens, ok = [], 0, 0
        for entry in shard:
            idx, item = entry.get("i", 0), str(entry.get("item", ""))
            try:
                if fetch:
                    content = fetch_extract(item)
                    prompt = (f"{instruction}\n\nSOURCE URL: {item}\n"
                              f"PAGE TEXT (extracted):\n{spotlight(content)}\n\nOUTPUT:")
                else:
                    prompt = f"{instruction}\n\nITEM:\n{spotlight(item)}\n\nOUTPUT (concise):"
                out, tk = self._generate(prompt)
                tokens += tk
                ok += 1
                results.append({"i": idx, "item": item, "output": out})
            except Exception as e:
                results.append({"i": idx, "item": item,
                                "output": f"(error: {type(e).__name__}: {str(e)[:120]})"})
        elapsed = round(time.monotonic() - t0, 2)
        return {
            "text": (f"Processed {ok}/{len(shard)} items in {elapsed}s on this "
                     f"{self.country} node ({self.model})."),
            "results": results,
            "tokens": tokens,
            "elapsed_s": elapsed,
        }
