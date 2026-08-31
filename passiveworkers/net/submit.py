#!/usr/bin/env python3
"""
passiveworkers/net/submit.py — ask the networked Council a question
============================================================
Submits a question to the coordinator and polls until the council has answered,
judged, and merged — then prints the perspectives, scores, the merged answer, and the
credit movements.

Usage:
  PW_COORDINATOR=http://127.0.0.1:8088 PW_TOKEN=… \
    python -m passiveworkers.net.submit --asker alice "Your question here"
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

import requests


def trunc(t: str, n: int = 110) -> str:
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[:n] + "…"


# ---------------------------------------------------------------- R23: persisted asker identity
def _asker_state_path() -> pathlib.Path:
    base = os.environ.get("PW_LIBRARY_DIR", str(pathlib.Path.home() / ".passiveworkers"))
    return pathlib.Path(base) / "asker.json"


def _load_asker_state() -> dict:
    try:
        return json.loads(_asker_state_path().read_text())
    except Exception:
        return {}


def _persisted_secret(base: str) -> tuple[str, str] | None:
    """(handle, user_secret) for this coordinator, if this machine has already signed up — or
    None. Mirrors join.json's shape via the same reserved-key-aware reader (R1/R23 review)."""
    from passiveworkers import paths
    e = paths.coordinator_entries(_load_asker_state()).get(base)
    return (e.get("handle", ""), e["user_secret"]) if e and e.get("user_secret") else None


def _save_asker_secret(base: str, handle: str, user_secret: str) -> None:
    from passiveworkers import paths
    state = _load_asker_state()
    state[base] = {"handle": handle, "user_secret": user_secret}
    state["default"] = base
    paths.write_private_json(_asker_state_path(), state)


def main(persist: bool = False) -> int:
    """`persist=False` (default, `python -m passiveworkers.net.submit`): unchanged legacy behavior —
    mints a fresh user each run and never saves the secret. `persist=True` (`pworkers ask`, R23
    review): reuses a cached secret across runs and, on first signup, PRINTS AND SAVES it so
    it doesn't evaporate at process exit (the F6 review finding)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--asker", default="alice")
    ap.add_argument("--type", default=None, help="job type, e.g. research_report (see GET /job-types)")
    ap.add_argument("--enroll-token", default=os.environ.get("PW_ENROLL_TOKEN", ""))
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    base = os.environ.get("PW_COORDINATOR", "http://127.0.0.1:8088").rstrip("/")

    # End-users authenticate with their own secret (not the operator token). Get one:
    # reuse PW_USER_SECRET if set, else a persisted identity (persist=True only), else sign up
    # the handle (with a unique suffix if taken).
    user_secret = os.environ.get("PW_USER_SECRET")
    handle = args.asker
    minted = False
    if not user_secret and persist:
        cached = _persisted_secret(base)
        if cached:
            handle, user_secret = cached
            # Reusing a cached identity for a base that ISN'T the last-saved "default" must still
            # update the pointer — otherwise `pworkers fetch`/`pworkers rate` (which read "default" when
            # PW_COORDINATOR is unset) would target whichever coordinator was last FRESHLY minted,
            # not the one this job actually ran on (review finding).
            state = _load_asker_state()
            if state.get("default") != base:
                _save_asker_secret(base, handle, user_secret)
    if not user_secret:
        headers_su = {"X-Enroll-Token": args.enroll_token} if args.enroll_token else {}
        for attempt in range(3):
            ru = requests.post(f"{base}/users", json={"handle": handle},
                              headers=headers_su, timeout=15)
            if ru.status_code == 409:   # handle taken → try a fresh one
                handle = f"{args.asker}-{os.urandom(3).hex()}"
                continue
            ru.raise_for_status()
            user_secret = ru.json()["user_secret"]
            minted = True
            break
        if not user_secret:
            print("✗ could not create a user"); return 1
    if persist and minted:
        _save_asker_secret(base, handle, user_secret)
        print(f"✓ new asker identity '{handle}' saved to {_asker_state_path()} (0600) — reused "
              f"automatically by `pworkers ask`/`pworkers fetch`/`pworkers rate` for {base}.\n  secret: {user_secret}\n")
    headers = {"X-User-Secret": user_secret}

    body = {"question": args.question}
    if args.type:
        body["type"] = args.type
    r = requests.post(f"{base}/jobs", json=body, headers=headers, timeout=15)
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


def ask_main() -> int:
    """`pworkers ask "<brief>"` (R23 review): the asker front door. Same engine as
    `python -m passiveworkers.net.submit`, but persists+prints the minted secret so it doesn't
    evaporate at process exit (F6) — `pworkers fetch`/`pworkers rate` read it back automatically."""
    return main(persist=True)


if __name__ == "__main__":
    sys.exit(main())
