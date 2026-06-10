#!/usr/bin/env python3
"""
council/coordinator.py — the orchestrator
=========================================
Ties the loop together:

    intake  →  concurrent fan-out to a diverse fleet  →  judge scores + merges  →
    score-weighted ledger settlement  →  result bundle

The coordinator is the open-source, self-hostable hub. It holds the non-transferable
ledger and the worker registry; it never holds a tradeable token and never relays raw
traffic — it routes TASKS and settles CREDIT for delivered answers.

A worker is owned by a participant (its `owner`); credit for that worker's answer flows
to the owner's account. That is how "your machine helping on my question earns YOU
credit" works — and how a user who both asks and helps keeps their give/take balanced.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from council.judge import Judge, ScoredCandidate
from council.ledger import InsufficientCredit, Ledger, Receipt
from council.worker import Answer, PerspectiveWorker

WORKER_POOL = 30.0   # credits split among helpers per job (by score)
JUDGE_FEE = 5.0      # credits to the judge per job


@dataclass
class CouncilResult:
    job_id: str
    question: str
    asker_id: str
    answers: list[Answer]
    scored: list[ScoredCandidate]
    merged_answer: str
    receipt: Receipt
    elapsed_s: float
    owner_of: dict = field(default_factory=dict)  # worker_id -> owner_id

    def best_single(self) -> Answer:
        best_wid = max(self.scored, key=lambda s: s.score).worker_id
        return next(a for a in self.answers if a.worker_id == best_wid)

    def score_for(self, worker_id: str) -> float:
        return next((s.score for s in self.scored if s.worker_id == worker_id), 0.0)


@dataclass
class Council:
    ledger: Ledger
    judge: Judge
    judge_owner_id: str = "judge_node"
    worker_pool: float = WORKER_POOL
    judge_fee: float = JUDGE_FEE
    _job_seq: int = 0

    def run(
        self,
        asker_id: str,
        question: str,
        fleet: list[PerspectiveWorker],
        owner_of: dict | None = None,
    ) -> CouncilResult:
        owner_of = dict(owner_of or {})
        for w in fleet:
            owner_of.setdefault(w.worker_id, w.worker_id)

        # Make sure every participant has an account.
        self.ledger.open_account(asker_id)
        self.ledger.open_account(self.judge_owner_id)
        for w in fleet:
            self.ledger.open_account(owner_of[w.worker_id])

        # Affordability gate — the give/take rule in action.
        cost = self.ledger.quote(self.worker_pool, self.judge_fee)
        if not self.ledger.can_afford(asker_id, cost):
            raise InsufficientCredit(
                f"{asker_id} cannot afford {cost:.1f} credits "
                f"(balance {self.ledger.balance(asker_id):.1f}). Help on a job first."
            )

        self._job_seq += 1
        job_id = f"job{self._job_seq:03d}"
        t0 = time.monotonic()

        # Concurrent fan-out — every perspective answers in parallel.
        answers: list[Answer] = []
        with ThreadPoolExecutor(max_workers=len(fleet)) as pool:
            futures = {pool.submit(w.answer, question): w for w in fleet}
            for fut in as_completed(futures):
                try:
                    answers.append(fut.result())
                except Exception as exc:
                    w = futures[fut]
                    print(f"  [coordinator] worker {w.worker_id} failed: {exc}")
        # Stable order by worker_id for reproducible display.
        answers.sort(key=lambda a: a.worker_id)

        # Judge: score (blind) then merge.
        scored = self.judge.score(question, answers)
        merged = self.judge.merge(question, answers)

        # Aggregate scores by OWNER (a user running >1 model sums their scores).
        score_by_owner: dict[str, float] = {}
        for sc in scored:
            owner = owner_of[sc.worker_id]
            score_by_owner[owner] = score_by_owner.get(owner, 0.0) + sc.score

        receipt = self.ledger.settle_job(
            job_id=job_id,
            asker_id=asker_id,
            score_by_worker=score_by_owner,
            worker_pool=self.worker_pool,
            judge_id=self.judge_owner_id,
            judge_fee=self.judge_fee,
        )

        return CouncilResult(
            job_id=job_id,
            question=question,
            asker_id=asker_id,
            answers=answers,
            scored=scored,
            merged_answer=merged,
            receipt=receipt,
            elapsed_s=time.monotonic() - t0,
            owner_of=owner_of,
        )
