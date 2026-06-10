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
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from council.research import _host_is_public

OLLAMA_BASE = "http://localhost:11434"
_GEN_TIMEOUT = float(os.environ.get("PW_BATCH_GEN_TIMEOUT",
                                    os.environ.get("PW_OLLAMA_TIMEOUT", "480")))
_FETCH_CAP = 200_000   # bytes per page — extraction input, not archival
_UA = "PassiveWorkers-Batch/0.1 (mutual-aid marketplace; owned deliverables only)"


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


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
                  "options": {"temperature": self.temperature, "num_predict": num_predict}},
            timeout=_GEN_TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()
        return (d.get("response") or "").strip(), (d.get("eval_count") or 0)

    def _fetch_public(self, url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        if not url.startswith(("http://", "https://")) or not _host_is_public(host):
            raise ValueError(f"not a public http(s) URL: {url[:80]}")
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=15, stream=True)
        r.raise_for_status()
        return _strip_html(r.raw.read(_FETCH_CAP, decode_content=True)
                           .decode("utf-8", "replace"))[:6000]

    def process(self, instruction: str, shard: list[dict], fetch: bool = False) -> dict:
        t0 = time.monotonic()
        results, tokens, ok = [], 0, 0
        for entry in shard:
            idx, item = entry.get("i", 0), str(entry.get("item", ""))
            try:
                if fetch:
                    content = self._fetch_public(item)
                    prompt = (f"{instruction}\n\nSOURCE URL: {item}\n"
                              f"PAGE TEXT (extracted):\n{content}\n\nOUTPUT:")
                else:
                    prompt = f"{instruction}\n\nITEM:\n{item}\n\nOUTPUT (concise):"
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
