#!/usr/bin/env python3
"""
scripts/bench_rag.py — measure retrieval quality on YOUR corpus (R8/D20)
========================================================================
Our house rule (and the research's explicit warning): do NOT trust vendor
"+35%" numbers — measure on your own embedder + corpus. This builds a small
synthetic corpus + query/answer set and reports recall@k for dense-only vs the
hybrid (BM25⊕dense⊕RRF) retriever, so you can see what actually helps here.

    python scripts/bench_rag.py            # synthetic corpus
    PW_CONTEXTUAL_CHUNKS=1 python scripts/bench_rag.py   # with contextual blurbs

For a real measurement, point it at your own library: index your files with
`pworkers library add`, then write a {query: expected-doc} set in this script's EVAL.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile

import numpy as np


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pw_ragbench_")
    os.environ["PW_LIBRARY_DIR"] = tmp
    os.environ["PW_LIBRARY_ROOTS"] = tmp
    corpus = pathlib.Path(tmp, "corpus"); corpus.mkdir()
    # mixed corpus: semantic prose + exact codes/names (where lexical matching matters)
    docs = {
        "polaris.md": "# Project Polaris\nShips 2026-09-15. Budget 4,200,000 AED. Lead Mariam Al-Futtaim.",
        "atlas.md": "# Project Atlas\nGCC freight logistics platform. Enterprise invoice INV-ATLAS-7731.",
        "hr.md": "# Remote Work Policy\nUp to 3 remote days weekly. Manager approval code RW-2026.",
        "weather.md": "# Field Notes\nHumid coastal summers; mild pleasant winters for outdoor work.",
        "finance.md": "# Q1 Review\nRevenue +18% all regions. Operating margin 22%. Runway 19 months.",
    }
    for n, t in docs.items():
        (corpus / n).write_text(t)
    EVAL = [
        ("When does Polaris ship and its budget?", "polaris.md"),
        ("enterprise invoice INV-ATLAS-7731", "atlas.md"),
        ("approval needed to work from home", "hr.md"),
        ("RW-2026", "hr.md"),
        ("operating margin and cash runway", "finance.md"),
        ("Mariam Al-Futtaim", "polaris.md"),
        ("is the climate good for outdoor work", "weather.md"),
    ]
    from passiveworkers.library import Library
    import passiveworkers.library as L
    lib = Library(); lib.add(str(corpus))
    rows = list(lib.conn.execute("SELECT source,title,ord,text,context,vec FROM chunks"))
    mat = np.asarray([json.loads(r["vec"]) for r in rows], dtype="float32")

    def dense_top1_title(q):
        qv = np.asarray(L.embed([q])[0], dtype="float32"); qn = qv / (np.linalg.norm(qv) + 1e-9)
        mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        return rows[int((mn @ qn).argmax())]["title"]

    d = sum(dense_top1_title(q) == exp for q, exp in EVAL)
    h = sum(bool(r := lib.search(q, k=1, window=False)) and r[0]["title"] == exp for q, exp in EVAL)
    n = len(EVAL)
    print(f"recall@1 over {n} queries:")
    print(f"   dense-only        : {d}/{n}  ({d/n:.0%})")
    print(f"   hybrid (BM25⊕RRF) : {h}/{n}  ({h/n:.0%})")
    print(f"   contextual chunks : {'ON' if L._CONTEXTUAL else 'off (PW_CONTEXTUAL_CHUNKS=1)'}")
    print("\nNote: a strong local embedder often saturates small clean corpora — hybrid is")
    print("robustness insurance for the long tail (typos, OCR, multilingual, very long docs).")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
