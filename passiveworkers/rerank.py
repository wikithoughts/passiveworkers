#!/usr/bin/env python3
"""
passiveworkers/rerank.py — one listwise relevance reranker for both retrieval surfaces (R36/D52)
==========================================================================================
A single local-model call scores candidate passages by relevance to a query and returns a
best-first permutation. Shared by the private-library hybrid search (``passiveworkers/library.py``)
and by web-evidence selection in the researcher (``passiveworkers/researcher.py``) — so the most
relevant sources survive the small-model context cap and get page-fetched, instead of the
drafter drawing on whatever order the search backend happened to return.

Design guarantees (so it is always safe to insert):
  • Returns a PERMUTATION of the input indices (append-not-drop): passages the model omits keep
    their original order at the tail — a passage is never lost, and none is invented.
  • Degrades to identity order ``[0..n)`` on an empty set, no usable model, or ANY model/parse
    failure — i.e. exactly today's behavior when reranking is off.
  • Untrusted passage text is spotlight-wrapped; a planted instruction can at worst reorder, and
    the index-clamp bounds even that (a garbage/out-of-range index is dropped, never followed).
Zero new dependencies (reuses the shared Ollama client + judge JSON parser + sanitizer).
"""
from __future__ import annotations

from passiveworkers import ollama as _ollama


def rerank_listwise(query: str, passages: list[str], k: int | None = None, *,
                    base_url: str = "", model: str = "") -> list[int]:
    """Reorder ``passages`` by relevance to ``query`` via one local-model call; return indices
    into ``passages``, best-first, truncated to ``k`` (default: all). See the module docstring for
    the permutation / fail-safe guarantees. ``model`` defaults to the smallest installed chat model
    (``ollama.smallest_chat_model``); ``base_url`` targets a remote Ollama host."""
    n = len(passages)
    k = n if k is None else max(0, min(k, n))
    if n <= 1:
        return list(range(k))
    model = model or _ollama.smallest_chat_model(base_url=base_url)
    if not model:
        return list(range(k))
    try:
        from passiveworkers.judge import _extract_json
        from passiveworkers.sanitize import spotlight
        cand = spotlight("\n".join(f"[{j}] {(p or '')[:300]}" for j, p in enumerate(passages)))
        prompt = ("Rank the passages by relevance to the QUERY. Return STRICT JSON: "
                  '{"order":[indices best-first]}.\n\n'
                  f"QUERY: {query}\n\nPASSAGES:\n{cand}\n\nJSON:")
        raw, _ = _ollama.generate(prompt, model=model, base_url=base_url, temperature=0.0,
                                  num_predict=120, timeout=120)
        parsed = _extract_json(raw)
        order = parsed.get("order") if isinstance(parsed, dict) else None
        if isinstance(order, list):
            seen: set[int] = set()
            picked = [j for j in order
                      if isinstance(j, int) and 0 <= j < n and not (j in seen or seen.add(j))]
            picked += [j for j in range(n) if j not in seen]   # append the omitted, original order
            return picked[:k]
    except Exception:
        pass
    return list(range(k))
