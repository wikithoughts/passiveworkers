#!/usr/bin/env python3
"""
passiveworkers/retrieval.py — lean, local, dependency-free retrieval primitives (R8/D20)
==================================================================================
Best-in-class retrieval without a heavy vector DB or a paid reranker:

  • BM25Okapi  — sparse lexical scoring (catches exact terms, names, codes, numbers
                 that dense embeddings miss). Pure Python; k1=1.5, b=0.75 (standard).
  • reciprocal_rank_fusion — fuse dense (cosine) and sparse (BM25) rankings without
                 score normalization. RRF constant k=60 (the established default).

Hybrid (dense ⊕ sparse via RRF) is the proven core of modern retrieval; combined with
Anthropic-style Contextual Retrieval (see passiveworkers/library.py) it's the current SOTA that
still runs entirely on a laptop.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

_TOKEN = re.compile(r"[^\W_]+")   # runs of Unicode letters/digits (Arabic, accented Latin, CJK…)
# Han + kana are written without spaces, so a whole run would be one unmatchable "word": index them
# as overlapping character bigrams instead (the standard CJK analyzer approach).
_CJK_RUN = re.compile(r"([\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+)")
_KEEP_MARKS = frozenset("\u3099\u309a")   # kana voicing marks: が must not fold into か
_TATWEEL = "\u0640"                        # Arabic elongation filler, not a letter


def _fold_char(c: str) -> str:
    """A non-ASCII decimal digit (Arabic-Indic ٠-٩, Persian ۰-۹, Devanagari…) becomes its ASCII
    digit, so '٢٠٢٤' and '2024' are one token; NFKD alone leaves these digits untouched."""
    return str(unicodedata.decimal(c)) if c.isdecimal() and not c.isascii() else c


def _fold(text: str) -> str:
    """Casefold + compatibility-decompose, then drop combining marks (accents, Arabic harakat,
    Hebrew niqqud) so 'café'/'cafe' and diacritized/undiacritized Arabic match, and map every
    decimal digit to ASCII; NFC recomposes."""
    text = unicodedata.normalize("NFKD", text.casefold()).replace(_TATWEEL, "")
    text = "".join(_fold_char(c) for c in text
                   if c in _KEEP_MARKS or not unicodedata.category(c).startswith("M"))
    return unicodedata.normalize("NFC", text)


def tokenize(text: str) -> list[str]:
    """Lexical tokens for BM25 and the fidelity scorer. ASCII text takes the old fast path
    (lowercase [a-z0-9] runs); anything else is Unicode-folded first, so Arabic, accented and CJK
    text is indexed instead of silently dropped (review F10)."""
    text = text or ""
    if text.isascii():
        return _TOKEN.findall(text.lower())
    out: list[str] = []
    for tok in _TOKEN.findall(_fold(text)):
        if not _CJK_RUN.search(tok):
            out.append(tok)
            continue
        for part in _CJK_RUN.split(tok):
            if not part:
                continue
            if not _CJK_RUN.fullmatch(part):
                out.append(part)
            elif len(part) == 1:
                out.append(part)
            else:
                out.extend(part[i:i + 2] for i in range(len(part) - 1))
    return out


class BM25Okapi:
    """Classic BM25 over an in-memory corpus of token lists. Small-corpus friendly
    (a personal document library is thousands of chunks, not billions)."""

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = corpus_tokens
        self.n = len(corpus_tokens)
        self.doc_len = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        df: Counter = Counter()
        for d in corpus_tokens:
            df.update(set(d))
        # BM25 idf with the +1 inside the log to keep it non-negative
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [Counter(d) for d in corpus_tokens]

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = [0.0] * self.n
        if not self.n:
            return out
        for i in range(self.n):
            tf, dl, s = self.tf[i], self.doc_len[i], 0.0
            for term in q:
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                s += idf * (f * (self.k1 + 1)) / (denom or 1)
            out[i] = s
        return out

    def top(self, query: str, k: int) -> list[int]:
        # compute the score vector ONCE — not once per sort comparison (was O(n²·|q|))
        s = self.scores(query)
        return sorted(range(self.n), key=lambda i: s[i], reverse=True)[:k]


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = 60, top_k: int | None = None) -> list[int]:
    """Fuse multiple ranked lists of item-ids by Reciprocal Rank Fusion.
    Each `rankings[j]` is item-ids best-first. RRF score = Σ 1/(k + rank). Robust because
    it uses rank position, not raw (incomparable) dense/sparse scores. Returns fused ids
    best-first (optionally truncated to top_k)."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + rank + 1)
    order = sorted(fused, key=lambda i: fused[i], reverse=True)
    return order[:top_k] if top_k else order
