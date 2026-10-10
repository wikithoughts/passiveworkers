"""Settlement integrity: a job the reaper already failed must never be resurrected into judging
(and so never settle or charge the asker after being reported failed). F14 regression."""
import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "settle.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100000")
    import importlib
    import passiveworkers.ledger as led
    importlib.reload(led)
    import passiveworkers.net.config as cfg
    importlib.reload(cfg)
    import passiveworkers.net.store as st
    importlib.reload(st)
    return st.Store()


def _reg(store, owner, judge=False):
    return store.register_node({
        "owner": owner, "name": owner, "country": "X", "lens": "neutral",
        "answer_model": "m", "can_judge": judge, "judge_model": ("m" if judge else ""),
        "profile": {"cores": 4, "ram_gb": 16, "models": ["m"]},
    })["node_id"]


def test_late_answer_does_not_resurrect_deadline_failed_job(store, monkeypatch):
    import passiveworkers.net._store_jobs as sj
    _reg(store, "op1")
    _reg(store, "judgeop", judge=True)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    t = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='answer'",
                           (j["job_id"],)).fetchone()
    balance_before = store.ledger.balance("alice")
    real_now = sj._now
    monkeypatch.setattr(sj, "_now", lambda: real_now() + 10**9)   # past the job deadline
    store._reap_once()
    assert store.job_status(j["job_id"]) == "failed"
    monkeypatch.setattr(sj, "_now", real_now)
    # the slow answer finally arrives
    assert store.complete_task(t["task_id"], {"text": "late answer"}, node_id=t["node_id"])
    assert store.job_status(j["job_id"]) == "failed"              # NOT flipped to 'judging'
    assert store.conn.execute("SELECT 1 FROM tasks WHERE job_id=? AND type='judge'",
                              (j["job_id"],)).fetchone() is None  # no judge task queued
    assert store.ledger.balance("alice") == balance_before        # never charged
    assert store.ledger.conservation_ok()


def test_on_time_answers_still_start_judging(store):
    _reg(store, "op1")
    _reg(store, "judgeop", judge=True)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    t = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='answer'",
                           (j["job_id"],)).fetchone()
    assert store.complete_task(t["task_id"], {"text": "answer"}, node_id=t["node_id"])
    assert store.job_status(j["job_id"]) == "judging"
