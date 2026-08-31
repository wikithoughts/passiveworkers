#!/usr/bin/env python3
"""
passiveworkers/ledger.py — Non-transferable mutual-aid credit ledger
=============================================================
Implements the give/take economy at the heart of Passive Workers:

  • Credit is NON-TRANSFERABLE — there is no account-to-account `transfer()`.
    Credit only moves through `settle_job()`, i.e. as payment for real work.
  • Every account opens with a STARTER_ALLOWANCE so a newcomer can ask for help
    before they have contributed anything (bootstrap).
  • To keep asking once the allowance is spent, you must EARN by helping — a pure
    free-rider depletes to zero and is blocked. This is the "no one only-takes,
    no one only-helps" rule, enforced by balance, not by trust.
  • IDEAS COMPETE: a job's worker pool is split by the judge's quality score, so
    a better answer earns more credit than a worse one for the same question.
  • Every job is CONSERVED: the asker's debit exactly equals the sum credited to
    helpers + judge. No credit is minted or destroyed inside a transaction.

This is the open-source, no-token core: money (later) only ever enters or leaves
at the platform boundary, never as a tradeable instrument between users.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

ESCROW_ID = "__escrow__"   # internal assisted-offer holding account (no starter grant)

# Credits granted on account creation (the bootstrap grant). Operator-tunable:
# 100 ≈ 2–3 council asks before you must contribute — right for production give/take,
# too tight for trials/demos (the first 10-question trial died at question 3 on this).
STARTER_ALLOWANCE = float(os.environ.get("PW_STARTER_CREDITS", "100"))


class InsufficientCredit(Exception):
    """Raised when an asker cannot afford a job — they must contribute more first."""


@dataclass
class Account:
    user_id: str
    balance: float = STARTER_ALLOWANCE
    lifetime_earned: float = 0.0   # credits earned by HELPING (as worker or judge)
    lifetime_spent: float = 0.0    # credits spent by ASKING
    jobs_helped: int = 0
    jobs_asked: int = 0
    quality_sum: float = 0.0       # sum of judge scores this owner's answers received
    quality_n: int = 0             # number of judged answers

    @property
    def net_contribution(self) -> float:
        """Positive = net giver, negative = net taker (excludes starter grant)."""
        return self.lifetime_earned - self.lifetime_spent

    @property
    def avg_quality(self) -> float:
        """Rolling reputation = mean judge score (0-10). 0 if never judged."""
        return round(self.quality_sum / self.quality_n, 2) if self.quality_n else 0.0


@dataclass
class Receipt:
    job_id: str
    asker_id: str
    total_cost: float
    payouts: dict[str, float]          # helper_id -> credit earned
    judge_id: str
    judge_fee: float
    asker_balance_after: float


@dataclass
class Ledger:
    accounts: dict[str, Account] = field(default_factory=dict)
    _granted_total: float = 0.0        # sum of all starter grants (for accounting)
    _job_count: int = 0

    # ------------------------------------------------------------------ accounts
    def open_account(self, user_id: str, grant_amount: float | None = None) -> Account:
        """Open an account if new, with a starter grant. `grant_amount`:
          • None → the DEFAULT grant, which is enrollment-aware (D37): STARTER_ALLOWANCE normally,
            but **0 when PW_ENROLL is on** — so NO call site (create_job, _create_assisted,
            accept_assisted, …) can mint a fresh grant without an explicit, token-authorized amount,
            even on a ledger-desync edge. (Off → unchanged: every fresh account gets STARTER.)
          • an explicit amount → used as-is, but sanitized to a FINITE, non-negative value (a non-
            finite/negative grant — e.g. inf/nan from a crafted enrollment token — would corrupt
            conservation, so it is clamped to 0). Conserved for every value (balance == grant)."""
        if user_id not in self.accounts:
            if grant_amount is None:
                amt = 0.0 if os.environ.get("PW_ENROLL", "0").lower() in ("1", "true", "yes") \
                    else STARTER_ALLOWANCE
            else:
                f = float(grant_amount)
                amt = f if (math.isfinite(f) and f >= 0.0) else 0.0
            self.accounts[user_id] = Account(user_id=user_id, balance=amt)
            self._granted_total += amt
        return self.accounts[user_id]

    def get(self, user_id: str) -> Account:
        return self.accounts[user_id]

    def balance(self, user_id: str) -> float:
        return self.accounts[user_id].balance

    def can_afford(self, user_id: str, amount: float) -> bool:
        return self.accounts[user_id].balance >= amount

    # ------------------------------------------------------------------ settlement
    def quote(self, worker_pool: float, judge_fee: float) -> float:
        """Total a job will cost the asker (worker pool + judge fee)."""
        return round(worker_pool + judge_fee, 4)

    def settle_job(
        self,
        job_id: str,
        asker_id: str,
        score_by_worker: dict[str, float],
        worker_pool: float,
        judge_id: str,
        judge_fee: float,
    ) -> Receipt:
        """
        Debit the asker `worker_pool + judge_fee`; split `worker_pool` among helpers
        in proportion to their judge score (ideas compete); pay the judge `judge_fee`.
        Conserved: total debit == total credit.
        """
        total_cost = self.quote(worker_pool, judge_fee)
        asker = self.accounts[asker_id]
        if asker.balance < total_cost:
            raise InsufficientCredit(
                f"{asker_id} has {asker.balance:.1f} credits but the job costs "
                f"{total_cost:.1f}. They must HELP on some jobs before asking again."
            )

        # Score-weighted split of the worker pool (better answers earn more).
        # CRITICAL: the split must sum EXACTLY to `worker_pool`, or rounding mints/burns
        # credit and breaks conservation. We round every share but the last, and give the
        # last payee the exact remainder.
        items = list(score_by_worker.items())
        score_sum = sum(max(0.0, s) for _, s in items)
        weights = (
            [max(0.0, s) / score_sum for _, s in items]
            if score_sum > 0
            else [1.0 / max(1, len(items))] * len(items)  # degenerate: split evenly
        )
        payouts: dict[str, float] = {}
        allocated = 0.0
        for i, (w, _) in enumerate(items):
            if i < len(items) - 1:
                share = round(worker_pool * weights[i], 4)
                payouts[w] = share
                allocated = round(allocated + share, 4)
            else:  # last payee absorbs the remainder → exact sum
                payouts[w] = round(worker_pool - allocated, 4)

        # Apply debit then credits (conserved).
        asker.balance = round(asker.balance - total_cost, 4)
        asker.lifetime_spent = round(asker.lifetime_spent + total_cost, 4)
        asker.jobs_asked += 1

        for w, credit in payouts.items():
            acct = self.accounts[w]
            acct.balance = round(acct.balance + credit, 4)
            acct.lifetime_earned = round(acct.lifetime_earned + credit, 4)
            acct.jobs_helped += 1

        jacct = self.accounts[judge_id]
        jacct.balance = round(jacct.balance + judge_fee, 4)
        jacct.lifetime_earned = round(jacct.lifetime_earned + judge_fee, 4)
        if judge_id not in payouts:   # don't double-count help if judge is also a payee
            jacct.jobs_helped += 1

        self._job_count += 1
        return Receipt(
            job_id=job_id,
            asker_id=asker_id,
            total_cost=total_cost,
            payouts=payouts,
            judge_id=judge_id,
            judge_fee=judge_fee,
            asker_balance_after=asker.balance,
        )

    # ------------------------------------------------------------------ escrow (assisted, D21)
    # Internal holding account for assisted offers: the asker's reward is HELD at offer
    # creation so it can't be spent elsewhere before the operator delivers, and is released
    # to the operator on delivery (or refunded on expiry). Opened WITHOUT a starter grant —
    # it only ever holds credit moved from real accounts, so conservation is preserved.
    def _escrow(self) -> "Account":
        if ESCROW_ID not in self.accounts:
            # balance 0 (NOT the starter grant) and not counted in _granted_total — it only
            # ever holds credit moved from real accounts, so conservation is preserved.
            self.accounts[ESCROW_ID] = Account(user_id=ESCROW_ID, balance=0.0)
        return self.accounts[ESCROW_ID]

    def hold(self, asker_id: str, amount: float) -> None:
        """Move `amount` from the asker into escrow (raises if they can't afford it)."""
        a = self.accounts[asker_id]
        amount = round(amount, 4)
        if a.balance < amount:
            raise InsufficientCredit(
                f"{asker_id} has {a.balance:.1f} but the offer holds {amount:.1f}. "
                "Help on a job first.")
        a.balance = round(a.balance - amount, 4)
        a.lifetime_spent = round(a.lifetime_spent + amount, 4)
        self._escrow().balance = round(self._escrow().balance + amount, 4)

    def release(self, to_id: str, amount: float) -> None:
        """Pay an escrow hold out to the operator (on delivery)."""
        amount = round(amount, 4)
        self._escrow().balance = round(self._escrow().balance - amount, 4)
        o = self.accounts[to_id]
        o.balance = round(o.balance + amount, 4)
        o.lifetime_earned = round(o.lifetime_earned + amount, 4)

    def refund(self, asker_id: str, amount: float) -> None:
        """Return an escrow hold to the asker (on offer expiry / failure). ATOMIC: the asker account
        is resolved BEFORE any balance moves, so a missing account raises without first decrementing
        escrow — nothing mutates on failure, so credit is never burned and a caller can safely retry
        (R36 hardening; previously escrow was debited first, so a KeyError destroyed the held credit)."""
        a = self.accounts[asker_id]
        amount = round(amount, 4)
        self._escrow().balance = round(self._escrow().balance - amount, 4)
        a.balance = round(a.balance + amount, 4)
        a.lifetime_spent = round(a.lifetime_spent - amount, 4)

    # ------------------------------------------------------------------ integrity
    def total_credit(self) -> float:
        return round(sum(a.balance for a in self.accounts.values()), 4)

    def conservation_ok(self) -> bool:
        """Total credit in circulation must equal the sum of starter grants."""
        return abs(self.total_credit() - self._granted_total) < 1e-6

    def summary(self) -> str:
        lines = [f"{'user':<16}{'balance':>10}{'earned':>10}{'spent':>10}{'net':>8}{'help':>6}{'ask':>5}"]
        for a in self.accounts.values():
            lines.append(
                f"{a.user_id:<16}{a.balance:>10.1f}{a.lifetime_earned:>10.1f}"
                f"{a.lifetime_spent:>10.1f}{a.net_contribution:>8.1f}{a.jobs_helped:>6}{a.jobs_asked:>5}"
            )
        lines.append(
            f"\n  total credit = {self.total_credit():.1f} | granted = {self._granted_total:.1f} | "
            f"conserved = {self.conservation_ok()} | jobs = {self._job_count}"
        )
        return "\n".join(lines)
