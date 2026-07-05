#!/usr/bin/env python3
"""
council/net/submit.py — ask the networked Council a question
============================================================
Submits a question to the coordinator and polls until the council has answered,
judged, and merged — then prints the perspectives, scores, the merged answer, and the
credit movements.

Usage:
  PW_COORDINATOR=http://127.0.0.1:8088 PW_TOKEN=… \
    python -m council.net.submit --asker alice "Your question here"
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import requests


def trunc(t: str, n: int = 110) -> str:
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[:n] + "…"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--asker", default="alice")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    base = os.environ.get("PW_COORDINATOR", "http://127.0.0.1:8088").rstrip("/")

    # End-users authenticate with their own secret (not the operator token). Get one:
    # reuse PW_USER_SECRET if set, else sign up the handle (with a unique suffix if taken).
    user_secret = os.environ.get("PW_USER_SECRET")
    handle = args.asker
    if not user_secret:
        for attempt in range(3):
            ru = requests.post(f"{base}/users", json={"handle": handle}, timeout=15)
            if ru.status_code == 409:   # handle taken → try a fresh one
                handle = f"{args.asker}-{os.urandom(3).hex()}"
                continue
            ru.raise_for_status()
            user_secret = ru.json()["user_secret"]
            break
        if not user_secret:
            print("✗ could not create a user"); return 1
    headers = {"X-User-Secret": user_secret}

    r = requests.post(f"{base}/jobs", json={"question": args.question},
                      headers=headers, timeout=15)
    r.raise_for_status()
    job = r.json()
    if job["status"] == "failed":
        print(f"✗ job failed: {job.get('error')}")
        return 1
    job_id = job["job_id"]
    print(f"submitted job {job_id[:8]}…  ({args.asker} asks)  → {job.get('assigned', [])} workers")

    deadline = time.monotonic() + args.timeout
    last = None
    while time.monotonic() < deadline:
        v = requests.get(f"{base}/jobs/{job_id}", headers=headers, timeout=15).json()
        if v["status"] != last:
            print(f"  … {v['status']}")
            last = v["status"]
        if v["status"] in ("done", "failed"):
            break
        time.sleep(2)

    if v["status"] == "failed":
        print(f"✗ job failed: {v.get('error')}")
        return 1
    if v["status"] != "done":
        print("✗ timed out")
        return 1

    print("\n" + "=" * 78)
    print(f"Q ({v['asker']}): {v['question']}")
    print("=" * 78)
    print(f"\n{'perspective':<34}{'score':>7}   one-line")
    print("-" * 78)
    for a in sorted(v["answers"], key=lambda x: -(x["score"] or 0)):
        lbl = f"{a['owner']} [{a['model']}/{a['lens']}/{a['country']}]"
        print(f"{lbl:<34}{(a['score'] or 0):>7.1f}   {trunc(a['text'])}")
    print("\nMERGED ANSWER:\n")
    print(v["merged"])
    rec = v.get("receipt") or {}
    if rec:
        payouts = ", ".join(f"{o} +{c:.1f}" for o, c in (rec.get("payouts") or {}).items())
        print(f"\nledger: {rec.get('asker_id')} −{rec.get('total_cost', 0):.1f}  |  {payouts}  |  "
              f"judge +{rec.get('judge_fee', 0):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
