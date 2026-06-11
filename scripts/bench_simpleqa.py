#!/usr/bin/env python3
"""
scripts/bench_simpleqa.py — honest benchmark on a SimpleQA public subset
=========================================================================
Runs N questions from OpenAI's SimpleQA test set through the LOCAL research pipeline
(single quickest analyst, --quick) and grades answers with the strongest local model.
Published results include the misses — candor is the house style (see TRIAL_RESULTS).

    python scripts/bench_simpleqa.py --n 100
    python scripts/bench_simpleqa.py --n 20 --grader qwen2.5:14b

Notes: SimpleQA rewards short factoid answers; our pipeline targets reports. We extract
a direct answer with a final model call over the analyst's evidence. LLM-graded (the
standard practice for SimpleQA) — grader sees question, gold answer, and our answer.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import time

import requests

DATASET = "https://openaipublic.blob.core.windows.net/simple-evals/simple_qa_test_set.csv"
OLLAMA = os.environ.get("PW_OLLAMA_BASE", "http://localhost:11434")


def _gen(model: str, prompt: str, num_predict: int = 120) -> str:
    r = requests.post(f"{OLLAMA}/api/generate",
                      json={"model": model, "prompt": prompt, "stream": False,
                            "options": {"temperature": 0.0, "num_predict": num_predict}},
                      timeout=float(os.environ.get("PW_OLLAMA_TIMEOUT", "300")))
    r.raise_for_status()
    return (r.json().get("response") or "").strip()


def answer_question(q: str, model: str) -> str:
    from council.research import search_structured
    from council.sanitize import spotlight
    rows = search_structured(q, max_results=5)
    ev = spotlight("\n".join(f"- {r['title']} ({r['host']}): {r['snippet'][:300]}"
                             for r in rows)) if rows else "(no sources found)"
    return _gen(model,
                f"Answer the question with the single most likely SHORT factual answer "
                f"(a name, date, number, or phrase — no explanation). Use the evidence.\n\n"
                f"QUESTION: {q}\n\nEVIDENCE:\n{ev}\n\nANSWER:", num_predict=60)


def grade(q: str, gold: str, ours: str, grader: str) -> bool:
    verdict = _gen(grader,
                   "Grade strictly. Is the CANDIDATE answer factually equivalent to the GOLD "
                   "answer for this question? Reply exactly CORRECT or INCORRECT.\n\n"
                   f"QUESTION: {q}\nGOLD: {gold}\nCANDIDATE: {ours}\n\nVERDICT:",
                   num_predict=8)
    return verdict.upper().startswith("CORRECT")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--model", default="", help="answering model (default: largest installed)")
    p.add_argument("--grader", default="", help="grading model (default: largest installed)")
    a = p.parse_args()
    os.environ.setdefault("PW_WEB_BACKEND", "ddgs")

    from council.local import detect_models
    models = detect_models()
    model = a.model or models[-1]["name"]
    grader = a.grader or models[-1]["name"]
    print(f"SimpleQA subset n={a.n} · answerer: {model} · grader: {grader}", flush=True)

    rows = list(csv.DictReader(io.StringIO(requests.get(DATASET, timeout=30).text)))[:a.n]
    correct, results = 0, []
    t0 = time.monotonic()
    for i, row in enumerate(rows, 1):
        q, gold = row["problem"], row["answer"]
        try:
            ours = answer_question(q, model)
            ok = grade(q, gold, ours, grader)
        except Exception as e:
            ours, ok = f"(error: {e})", False
        correct += ok
        results.append({"q": q, "gold": gold, "ours": ours, "correct": ok})
        print(f"[{i}/{len(rows)}] {'✓' if ok else '✗'} {q[:60]}… → {ours[:40]}", flush=True)
    acc = correct / max(1, len(rows))
    mins = (time.monotonic() - t0) / 60
    print(f"\nACCURACY: {correct}/{len(rows)} = {acc:.1%} · {mins:.0f} min", flush=True)
    out = f"/tmp/pw_simpleqa_{len(rows)}.json"
    json.dump(results, open(out, "w"), indent=2)
    print(f"per-question results → {out}")
    return 0


if __name__ == "__main__":
    main()
