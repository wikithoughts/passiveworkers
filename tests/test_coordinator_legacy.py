"""R36/D52 — pin the legacy in-process Council orchestrator (council/coordinator.py), used by
run_demo.py / merge_eval.py (not the live federation path, but not dead). Pure unit test: a fake Judge
+ stub workers, no network/Ollama. Locks ledger credit-conservation on that path before any refactor."""
from council.coordinator import Council
from council.judge import ScoredCandidate
from council.ledger import Ledger
from council.worker import Answer


class _StubWorker:
    def __init__(self, wid):
        self.worker_id = wid

    def answer(self, question):
        return Answer(worker_id=self.worker_id, model="m", lens="neutral", country="local",
                      text=f"answer from {self.worker_id}", tokens=1, elapsed_s=0.0)


class _FakeJudge:
    def score(self, question, answers):
        # deterministic descending scores by worker order (Council sorts answers by worker_id first)
        return [ScoredCandidate(worker_id=a.worker_id, score=float(10 - i), reason="ok")
                for i, a in enumerate(answers)]

    def merge(self, question, answers):
        return "merged answer"


def test_council_run_conserves_credit():
    led = Ledger()
    council = Council(ledger=led, judge=_FakeJudge(), worker_pool=30.0, judge_fee=5.0)
    fleet = [_StubWorker("w1"), _StubWorker("w2"), _StubWorker("w3")]

    res = council.run(asker_id="asker", question="q?", fleet=fleet)
    r = res.receipt
    # the asker pays worker_pool + judge_fee; the helper payouts split exactly the worker_pool,
    # and the judge fee lands in the judge's account — nothing created or destroyed.
    assert r.total_cost == 30.0 + 5.0
    assert round(sum(r.payouts.values()), 4) == 30.0     # worker_pool split among helpers
    assert r.asker_balance_after == 65.0                 # 100 starter − 35
    assert led.balance("judge_node") == 105.0            # judge got its 5-credit fee
    assert led.conservation_ok()                         # credit conserved across all accounts
    # the result helpers behave
    assert res.best_single().worker_id == "w1"           # highest score
    assert res.score_for("w1") == 10.0
    assert res.merged_answer == "merged answer"
