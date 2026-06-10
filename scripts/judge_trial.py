#!/usr/bin/env python3
"""
scripts/judge_trial.py — blind verdicts for the demand trial (docs/TRIAL_PROTOCOL.md).
=======================================================================================
Two phases, so the human/LLM co-judge stays blind:

  python scripts/judge_trial.py            # Judge A: local model, position-swapped.
                                           # Reads blind_pairs.json → writes judge_a.json
  python scripts/judge_trial.py --finalize # after judge_b.json exists (the co-judge's
                                           # blind verdicts): combine (disagree → tie),
                                           # POST /jobs/{id}/feedback votes, write final.json

Judge A discipline (mirrors scripts/merge_eval.py): each pair judged TWICE with positions
swapped; only a consistent winner counts, otherwise tie. The judge never sees which side
is the council. judge_b.json format: {"<n>": "A"|"B"|"tie", ...}
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import time
import urllib.request

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path("/tmp/pw_trial")
OLLAMA = "http://localhost:11434"
JUDGE_MODEL = "qwen2.5:14b"

RUBRIC = (
    "You are judging two anonymous answers to the same question. Pick the one that is "
    "more USEFUL to the asker: accurate, specific, actionable, and honest about uncertainty. "
    "Ignore length and style; substance only.\n"
    'Reply STRICT JSON only: {"winner":"A"|"B"|"tie","reason":"one short sentence"}\n\n'
    "QUESTION:\n{q}\n\n--- Answer A ---\n{a}\n\n--- Answer B ---\n{b}\n\nJSON:"
)


def _ask_judge(q: str, a: str, b: str) -> str:
    r = requests.post(f"{OLLAMA}/api/generate", json={
        "model": JUDGE_MODEL,
        "prompt": RUBRIC.format(q=q, a=a, b=b),
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 120},
    }, timeout=300)
    r.raise_for_status()
    raw = r.json().get("response", "")
    m = re.search(r'"winner"\s*:\s*"(A|B|tie)"', raw)
    return m.group(1) if m else "tie"


def judge_a() -> None:
    pairs = json.loads((OUT / "blind_pairs.json").read_text())
    verdicts = {}
    for p in pairs:
        v1 = _ask_judge(p["question"], p["A"], p["B"])                  # original order
        v2 = _ask_judge(p["question"], p["B"], p["A"])                  # swapped order
        v2u = {"A": "B", "B": "A", "tie": "tie"}[v2]                     # un-swap
        verdict = v1 if v1 == v2u else "tie"                            # consistent or tie
        verdicts[str(p["n"])] = {"v1": v1, "v2_unswapped": v2u, "verdict": verdict}
        print(f"[{p['n']}] {p['category']:9s} order1={v1} order2={v2u} → {verdict}", flush=True)
    (OUT / "judge_a.json").write_text(json.dumps(verdicts, indent=2))
    print(f"\nJudge A done → {OUT}/judge_a.json", flush=True)


def finalize() -> None:
    env = dict(l.strip().split("=", 1) for l in open(ROOT / ".vps.env") if "=" in l)
    base = f"http://127.0.0.1:{env['PW_PORT']}"
    res = json.loads((OUT / "results.json").read_text())
    mapping = json.loads((OUT / "mapping.json").read_text())
    ja = json.loads((OUT / "judge_a.json").read_text())
    jb = json.loads((OUT / "judge_b.json").read_text())          # {"n": "A"|"B"|"tie"}
    pairs = {str(p["n"]): p for p in json.loads((OUT / "blind_pairs.json").read_text())}

    final = []
    for n, m in mapping.items():
        a_v, b_v = ja[n]["verdict"], jb[n]
        # blind A/B → council/single/tie via the hidden mapping
        def to_side(v): return "tie" if v == "tie" else m[v]
        sa, sb = to_side(a_v), to_side(b_v)
        verdict = sa if sa == sb else "tie"
        final.append({"n": int(n), "category": pairs[n]["category"], "job_id": m["job_id"],
                      "judge_a": sa, "judge_b": sb, "verdict": verdict})
        # cast the real vote
        req = urllib.request.Request(f"{base}/jobs/{m['job_id']}/feedback",
                                     data=json.dumps({"verdict": verdict}).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-User-Secret": res["user_secret"]})
        try:
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            print(f"  vote failed for {n}: {e}", flush=True)
        print(f"[{n}] {pairs[n]['category']:9s} A={sa:7s} B={sb:7s} → {verdict}", flush=True)

    (OUT / "final.json").write_text(json.dumps(sorted(final, key=lambda x: x["n"]), indent=2))
    by_cat: dict = {}
    for f in final:
        by_cat.setdefault(f["category"], []).append(f["verdict"])
    print("\nPER-CATEGORY:", {k: {v: c.count(v) for v in set(c)} for k, c in by_cat.items()})
    m = json.load(urllib.request.urlopen(f"{base}/metrics", timeout=15))
    print("LIVE METRICS:", m, flush=True)


if __name__ == "__main__":
    finalize() if "--finalize" in sys.argv else judge_a()
