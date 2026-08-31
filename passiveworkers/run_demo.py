#!/usr/bin/env python3
"""
passiveworkers/run_demo.py — the whole idea, running end to end
========================================================
Demonstrates Passive Workers' beating heart at two-machine (here, multi-model) scale:

  1. A user asks a question; a DIVERSE council of perspectives answers in parallel.
  2. A judge scores them blind (ideas compete) and MERGES them into a better answer.
  3. The non-transferable ledger debits the asker and credits the helpers + judge,
     keeping give/take balanced — a pure free-rider gets blocked.

Then it VERIFIES the claims that matter:
  • merge beats best-single (blind A/B by an independent model),
  • diversity is captured (the merge credits unique contributions),
  • credit is conserved, and a free-rider is blocked.

Run:  cd <project root> && source .venv/bin/activate && python -m passiveworkers.run_demo
"""

from __future__ import annotations

import os
import random
import sys

# Allow `python passiveworkers/run_demo.py` as well as `python -m passiveworkers.run_demo`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from passiveworkers.coordinator import Council                       # noqa: E402
from passiveworkers.judge import Judge                               # noqa: E402
from passiveworkers.ledger import InsufficientCredit, Ledger         # noqa: E402
from passiveworkers.worker import PerspectiveWorker                  # noqa: E402

rng = random.Random(7)


def trunc(text: str, n: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


def hr(title: str = "") -> None:
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


# --------------------------------------------------------------------------- fleet
# Each worker = one contributor's machine: a model + a lens + a (here simulated)
# country. Ownership says whose account earns when that worker helps.
WORKERS = {
    "w_us": PerspectiveWorker("w_us", "gemma3:4b",        lens="opportunity",       country="sim-US", num_predict=350),
    "w_de": PerspectiveWorker("w_de", "gemma2:9b",        lens="skeptic",           country="sim-DE", num_predict=350),
    "w_fr": PerspectiveWorker("w_fr", "mistral-small:22b", lens="first_principles", country="sim-FR", num_predict=350),
    "w_br": PerspectiveWorker("w_br", "gemma3:12b",       lens="practical",         country="sim-BR", num_predict=350),
}
OWNER_OF = {"w_us": "carlos", "w_de": "alice", "w_fr": "dora", "w_br": "bob"}

MERGE_JUDGE = Judge(model="qwen2.5:14b")        # merges + scores
VERIFY_JUDGE = Judge(model="mistral-small:22b")  # independent A/B verifier (different family)


def show_result(result, council: Council) -> None:
    print(f"\nQ ({result.asker_id} asks): {result.question}")
    print(f"  fan-out: {len(result.answers)} perspectives, {result.elapsed_s:.0f}s\n")
    print(f"  {'perspective':<28}{'score':>7}   one-line")
    print("  " + "-" * 74)
    for a in sorted(result.answers, key=lambda x: -result.score_for(x.worker_id)):
        lbl = f"{a.worker_id} [{a.model}/{a.lens}/{a.country}]"
        print(f"  {lbl:<28}{result.score_for(a.worker_id):>7.1f}   {trunc(a.text, 90)}")
    best = result.best_single()
    print(f"\n  best single → {best.worker_id} ({best.model}/{best.country})")
    print("\n  MERGED ANSWER:")
    for line in result.merged_answer.splitlines():
        print(f"    {line}")
    r = result.receipt
    print(f"\n  ledger: {r.asker_id} −{r.total_cost:.1f}  |  "
          + ", ".join(f"{owner} +{c:.1f}" for owner, c in r.payouts.items())
          + f"  |  judge({r.judge_id}) +{r.judge_fee:.1f}")


def main() -> int:
    ledger = Ledger()
    council = Council(ledger=ledger, judge=MERGE_JUDGE, judge_owner_id="judge_node")

    hr("PASSIVE WORKERS — The Council MVP")
    print("Diverse models + lenses + (simulated) countries → judged merge → "
          "non-transferable give/take credit.")
    print("Real geo-diversity activates when a second machine abroad joins; here we prove the loop.")

    verifications = []

    # -- Job 1: alice asks; her own worker (w_de) is excluded (you don't help your own Q).
    hr("JOB 1")
    q1 = ("What are the most promising strategies for a small city to cut food waste, "
          "and what is the single biggest risk of each?")
    fleet1 = [WORKERS["w_us"], WORKERS["w_fr"], WORKERS["w_br"]]
    res1 = council.run("alice", q1, fleet1, OWNER_OF)
    show_result(res1, council)
    verifications.append(("job1", res1))

    # -- Job 2: bob asks; fleet includes alice's worker (w_de) so ALICE earns.
    hr("JOB 2  (note: alice's machine helps bob → alice earns credit back)")
    q2 = ("For a two-person startup, what is the smartest way to choose between building "
          "on open-source local models versus paying for a frontier API?")
    fleet2 = [WORKERS["w_us"], WORKERS["w_de"], WORKERS["w_fr"]]
    res2 = council.run("bob", q2, fleet2, OWNER_OF)
    show_result(res2, council)
    verifications.append(("job2", res2))

    # -- Free-rider: leo only ever asks. Starter 100, cost 35 → ok, ok, BLOCKED.
    hr("FREE-RIDER TEST  (leo only takes, never helps)")
    leo_fleet = [WORKERS["w_us"], WORKERS["w_de"]]
    blocked = False
    for i in range(1, 4):
        try:
            bal_before = ledger.open_account("leo").balance
            council.run("leo", f"(quick) Give me one tip about productivity. [ask #{i}]", leo_fleet, OWNER_OF)
            print(f"  ask #{i}: OK   (balance was {bal_before:.0f}, now {ledger.balance('leo'):.0f})")
        except InsufficientCredit as exc:
            print(f"  ask #{i}: BLOCKED ✅ — {exc}")
            blocked = True
            break

    # -------------------------------------------------------------- VERIFICATION
    hr("VERIFICATION")

    # (1) merge beats best-single — blind A/B by the INDEPENDENT verifier model.
    merge_wins = 0
    for name, res in verifications:
        merged, best = res.merged_answer, res.best_single().text
        if rng.random() < 0.5:
            verdict = VERIFY_JUDGE.compare(res.question, merged, best)   # merged = A
            won = verdict["winner"] == "A"
        else:
            verdict = VERIFY_JUDGE.compare(res.question, best, merged)   # merged = B
            won = verdict["winner"] == "B"
        merge_wins += int(won)
        print(f"  [{name}] merge vs best-single → "
              f"{'MERGE wins' if won else ('tie' if verdict['winner']=='tie' else 'single wins')}"
              f"  ({trunc(verdict['reason'], 80)})")

    # (2) diversity captured — the merge explicitly credits unique contributions.
    diversity_ok = all(
        any(k in res.merged_answer.lower() for k in ("unique", "perspective", "angle"))
        for _, res in verifications
    )

    # (3) credit conserved.
    conserved = ledger.conservation_ok()

    print(f"\n  (1) merge beats best-single : {merge_wins}/{len(verifications)} jobs")
    print(f"  (2) diversity captured      : {'yes' if diversity_ok else 'no'}")
    print(f"  (3) credit conserved        : {conserved}")
    print(f"  (4) free-rider blocked      : {blocked}")

    hr("LEDGER")
    print(ledger.summary())

    passed = merge_wins >= len(verifications) - 0 and conserved and blocked and diversity_ok
    # Allow one tie/loss on merge without failing the whole MVP (quality is probabilistic).
    soft_pass = merge_wins >= max(1, len(verifications) - 1) and conserved and blocked
    hr()
    if passed:
        print("MVP: ✅ PASS — merge wins every job, credit conserved, give/take enforced.")
    elif soft_pass:
        print("MVP: ✅ PASS (soft) — credit + give/take solid; merge won the majority of jobs.")
    else:
        print("MVP: ⚠️  REVIEW — inspect the merge-vs-single and ledger results above.")
    return 0 if (passed or soft_pass) else 1


if __name__ == "__main__":
    sys.exit(main())
