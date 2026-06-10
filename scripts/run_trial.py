#!/usr/bin/env python3
"""
scripts/run_trial.py — run the first demand trial (docs/TRIAL_PROTOCOL.md) unattended.
======================================================================================
Submits the 10 protocol questions ONE AT A TIME to the live council, waits for the
judged merge + the independent baseline, and saves everything needed for blind
verdicts to /tmp/pw_trial/:

  results.json      — full per-question artifacts (question, category, merged,
                      baseline, individual answers, scores, job_id) + the trial
                      user secret (needed later to POST feedback votes).
  blind_pairs.json  — per question: {qid, category, question, A, B} with
                      council/baseline RANDOMLY assigned to A/B.
  mapping.json      — the hidden A/B key. Judges read blind_pairs.json, commit
                      verdicts, and only then open this file.

Votes are NOT cast here — verdicts happen in the judging step so they stay blind.
Usage:  python scripts/run_trial.py   (tunnel up, council online)
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path("/tmp/pw_trial")
OUT.mkdir(exist_ok=True)

QUESTIONS = [
    ("geo", "What do people in Finland actually pay for home electricity right now, and is fixed or spot pricing the better deal this year?"),
    ("geo", "What's the current process and real waiting time for a UAE freelance visa, and what does it actually cost all-in?"),
    ("geo", "How do everyday people in Finland vs the UAE invest small monthly savings — what do locals actually use?"),
    ("research", "What changed in the EU AI Act's enforcement in the last six months, and who has actually been fined or warned?"),
    ("research", "What are the realistic price and availability trends for used EVs this year — is now a good time to buy?"),
    ("research", "What is the current state of small modular nuclear reactors — who is actually building one, and when does the first one come online?"),
    ("reasoning", "I can save 1,500/month. Argue for and against: paying down a 4% mortgage faster vs investing in index funds vs keeping cash. What would you do and why?"),
    ("reasoning", "Design a fair way for three co-founders with unequal time commitments to split equity, including what happens if one leaves in year one."),
    ("everyday", "Give me a simple one-week meal plan for someone who wants high protein, minimal cooking, and a budget."),
    ("everyday", "Explain to a 10-year-old why planes can fly."),
]


def _env() -> dict:
    return dict(l.strip().split("=", 1) for l in open(ROOT / ".vps.env") if "=" in l)


def _req(base: str, path: str, data: dict | None = None, secret: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-User-Secret"] = secret
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(base + path, data=body, headers=headers)
    return json.load(urllib.request.urlopen(r, timeout=30))


def wait_idle(base: str) -> None:
    """Don't start while another job is still running (one-at-a-time discipline)."""
    while True:
        st = _req(base, "/status")
        busy = [j for j in st.get("recent_jobs", [])
                if j.get("status") in ("pending_answers", "judging")]
        if not busy:
            return
        print(f"  waiting for {len(busy)} running job(s) to finish…", flush=True)
        time.sleep(15)


def main() -> int:
    env = _env()
    base = f"http://127.0.0.1:{env['PW_PORT']}"
    u = _req(base, "/users", {"handle": f"trial_{int(time.time())}"})
    secret = u["user_secret"]
    print(f"trial user: {u['handle']}", flush=True)

    results, pairs, mapping = [], [], {}
    for n, (cat, q) in enumerate(QUESTIONS, 1):
        wait_idle(base)
        print(f"[{n}/10] ({cat}) {q[:70]}…", flush=True)
        j = _req(base, "/jobs", {"question": q}, secret=secret)
        if j.get("status") == "failed":
            print(f"  ✗ submit failed: {j.get('error')}", flush=True)
            results.append({"n": n, "category": cat, "question": q, "failed": j.get("error")})
            continue
        jid = j["job_id"]
        d, t0 = {}, time.monotonic()
        while time.monotonic() - t0 < 900:
            d = _req(base, f"/jobs/{jid}")
            if d["status"] == "failed":
                break
            if d["status"] == "done" and d.get("baseline", {}) and d["baseline"].get("text"):
                break
            time.sleep(10)
        if d.get("status") != "done" or not (d.get("baseline") or {}).get("text"):
            print(f"  ✗ incomplete: status={d.get('status')} baseline={bool(d.get('baseline'))}", flush=True)
            results.append({"n": n, "category": cat, "question": q, "job_id": jid,
                            "failed": d.get("error") or "no baseline in 15 min"})
            continue
        merged = d["merged"]
        btext = d["baseline"]["text"]
        elapsed = round(time.monotonic() - t0)
        print(f"  ✓ done in {elapsed}s · merge {len(merged.split())}w · baseline {len(btext.split())}w "
              f"({d['baseline']['model']})", flush=True)
        results.append({
            "n": n, "category": cat, "question": q, "job_id": jid,
            "merged": merged, "council": d.get("council"),
            "baseline": d["baseline"],
            "answers": [{k: a[k] for k in ("country", "model", "lens", "score", "text")}
                        for a in d["answers"]],
            "elapsed_s": elapsed,
        })
        # deterministic blind assignment: hash decides which side the council lands on
        council_is_a = int(hashlib.sha256(f"{jid}:blind".encode()).hexdigest(), 16) % 2 == 0
        pairs.append({"n": n, "category": cat, "question": q,
                      "A": merged if council_is_a else btext,
                      "B": btext if council_is_a else merged})
        mapping[str(n)] = {"A": "council" if council_is_a else "single",
                           "B": "single" if council_is_a else "council", "job_id": jid}

    (OUT / "results.json").write_text(json.dumps(
        {"user_secret": secret, "results": results}, indent=2))
    (OUT / "blind_pairs.json").write_text(json.dumps(pairs, indent=2))
    (OUT / "mapping.json").write_text(json.dumps(mapping, indent=2))
    ok = sum(1 for r in results if "merged" in r)
    print(f"\nTRIAL RUN COMPLETE: {ok}/10 questions answered · artifacts in {OUT}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
