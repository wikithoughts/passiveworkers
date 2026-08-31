#!/usr/bin/env python3
"""
scripts/merge_eval.py — is the MERGE honestly better than the best single answer?
=================================================================================
The earlier 2/2 "merge wins" used a plain LLM A/B judge, which is biased toward the
LONGER answer (the merge). This eval removes that bias two ways and reports a fair number:

  • POSITION-SWAPPED: each comparison is run twice (merge as A, then as B); the merge
    only "wins" if it wins BOTH orientations (kills position bias).
  • LENGTH-CONTROLLED: a second pass truncates the longer answer to the shorter one's
    word count before comparing, so the merge can't win merely by being longer.

Reports strict win-rate for raw vs. length-controlled over several questions. If the merge
still wins length-controlled, the diversity dividend is real — not a verbosity artifact.

Run:  python scripts/merge_eval.py   (local, multi-model; takes a few minutes)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from passiveworkers.coordinator import Council  # noqa: E402
from passiveworkers.judge import Judge
from passiveworkers.ledger import Ledger
from passiveworkers.worker import PerspectiveWorker

QUESTIONS = [
    "What are the two biggest mistakes first-time managers make, and how to avoid each?",
    "Should a small SaaS startup offer a free tier? Give the key trade-off and a recommendation.",
    "What's a robust way to back up a personal laptop in 2026? Name the single most common failure.",
]
WORKERS = [
    PerspectiveWorker("w_opp", "gemma3:4b",  lens="opportunity", country="AE", num_predict=300),
    PerspectiveWorker("w_skep", "gemma2:9b", lens="skeptic",     country="DE", num_predict=300),
    PerspectiveWorker("w_prac", "gemma3:12b", lens="practical",  country="BR", num_predict=300),
]
MERGE_JUDGE = Judge(model="qwen2.5:14b")
VERIFY_JUDGE = Judge(model="qwen2.5:14b")   # same family; internal eval (noted)


def truncate_words(text: str, n: int) -> str:
    w = text.split()
    return " ".join(w[:n])


def merge_wins_strict(question: str, merged: str, single: str) -> bool:
    """Position-swapped: merge must win BOTH orientations to count as a win."""
    v1 = VERIFY_JUDGE.compare(question, merged, single)   # merged = A
    v2 = VERIFY_JUDGE.compare(question, single, merged)   # merged = B
    return v1["winner"] == "A" and v2["winner"] == "B"


def main() -> int:
    ledger = Ledger()
    council = Council(ledger=ledger, judge=MERGE_JUDGE, judge_owner_id="jn")
    owner = {w.worker_id: w.worker_id for w in WORKERS}

    raw_wins = lc_wins = 0
    print("=" * 74)
    print("HONEST MERGE EVAL — position-swapped + length-controlled")
    print("=" * 74)
    for i, q in enumerate(QUESTIONS, 1):
        res = council.run(f"asker{i}", q, WORKERS, owner)
        merged, single = res.merged_answer, res.best_single().text
        mw, sw = len(merged.split()), len(single.split())

        raw = merge_wins_strict(q, merged, single)
        # length-controlled: truncate the longer to the shorter's word count.
        n = min(mw, sw)
        lc = merge_wins_strict(q, truncate_words(merged, n), truncate_words(single, n))

        raw_wins += int(raw)
        lc_wins += int(lc)
        print(f"\n[Q{i}] {q}")
        print(f"   merge {mw}w vs best-single {sw}w  ({res.best_single().model})")
        print(f"   raw (swapped):              merge {'WINS' if raw else 'does NOT win'}")
        print(f"   length-controlled (swapped): merge {'WINS' if lc else 'does NOT win'}")

    n = len(QUESTIONS)
    print("\n" + "=" * 74)
    print(f"  RAW strict win-rate              : {raw_wins}/{n}")
    print(f"  LENGTH-CONTROLLED strict win-rate: {lc_wins}/{n}   ← the honest number")
    print("=" * 74)
    if lc_wins >= max(1, n - 1):
        print("  ✅ The merge beats the best single answer even when length is controlled —")
        print("     the diversity dividend is real, not a verbosity artifact.")
    elif lc_wins == 0:
        print("  ⚠️ Length-controlled, the merge does NOT win — its edge was mostly length.")
        print("     Action: make the merge denser, not longer (tighten the merge prompt).")
    else:
        print("  ◻︎ Mixed — the merge sometimes wins on substance. Tighten the merge prompt and re-run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
