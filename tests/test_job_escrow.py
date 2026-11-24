"""D56 / PW-1: an automated job's credit is held in escrow at creation, answers stay unreadable until
settlement, settlement pays out of the hold (no second debit) and every failure path refunds exactly once."""
import pytest

ESCROW = "__escrow__"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "e.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    import importlib
    import passiveworkers.ledger as led
    importlib.reload(led)
    import passiveworkers.net.config as cfg
    importlib.reload(cfg)
    # the mixins import InsufficientCredit from the ledger module: reload them too, or they keep a
    # stale exception class and `except InsufficientCredit` misses the reloaded ledger's error.
    import passiveworkers.net._store_jobs as sj
    import passiveworkers.net._store_assisted as sa
    import passiveworkers.net.store as st
    importlib.reload(sj)
    importlib.reload(sa)
    importlib.reload(st)
    s = st.Store()
    s._stop.set()
    yield s
    s.conn.close()


def _reg(store, owner, judge=False):
    return store.register_node({
        "owner": owner, "name": owner, "country": "X", "lens": "neutral",
        "answer_model": "m", "can_judge": judge, "judge_model": ("m" if judge else ""),
        "profile": {"cores": 4, "ram_gb": 16, "models": ["m"]},
    })["node_id"]


def _setup(store):
    _reg(store, "op1")
    _reg(store, "judgeop", judge=True)


def _bal(store, who):
    return store.ledger.balance(who) if who in store.ledger.accounts else 0.0


def _held(store, jid):
    return store.conn.execute("SELECT held FROM jobs WHERE job_id=?", (jid,)).fetchone()["held"]


def _answer_all(store, job_id, text="an answer"):
    for a in list(store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='answer'", (job_id,))):
        store.complete_task(a["task_id"], {"text": text}, node_id=a["node_id"])


def _judge(store, job_id):
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'", (job_id,)).fetchone()
    return store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])


def _expire(store, monkeypatch):
    import passiveworkers.net._store_jobs as sj
    real = sj._now
    monkeypatch.setattr(sj, "_now", lambda: real() + 10**9)
    store._reap_once()
    monkeypatch.setattr(sj, "_now", real)


def test_creation_holds_the_credit(store):
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    assert j["status"] == "pending_answers"
    cost = _held(store, j["job_id"])
    assert cost > 0
    assert store.ledger.balance("alice") == pytest.approx(100 - cost)
    assert store.ledger.balance(ESCROW) == pytest.approx(cost)
    assert store.ledger_integrity()["ok"]


def test_insufficient_balance_refuses_creation_and_holds_nothing(store):
    _setup(store)
    store.ledger.open_account("poor", grant_amount=1.0)
    store._save_ledger()
    store.conn.commit()
    j = store.create_job("poor", "q", job_type="chat", minds=1)
    assert j["status"] == "failed" and "insufficient" in j["error"]
    assert store.ledger.balance("poor") == 1.0 and _bal(store, ESCROW) == 0.0
    n = store.conn.execute("SELECT COUNT(*) c FROM tasks WHERE job_id=?", (j["job_id"],)).fetchone()["c"]
    assert n == 0


def test_unpaid_compute_hole_closed_second_job_refused(store):
    # PW-1 failure scenario: balance covers ONE job; before D56 the 2nd and 3rd also passed creation.
    _setup(store)
    probe = store.create_job("probe", "q", job_type="chat", minds=1)
    cost = _held(store, probe["job_id"])
    store.ledger.open_account("tight", grant_amount=cost + 0.5)
    store._save_ledger()
    store.conn.commit()
    first = store.create_job("tight", "q1", job_type="chat", minds=1)
    second = store.create_job("tight", "q2", job_type="chat", minds=1)
    third = store.create_job("tight", "q3", job_type="chat", minds=1)
    assert first["status"] == "pending_answers"
    assert second["status"] == third["status"] == "failed"
    assert store.ledger_integrity()["ok"]


def test_answers_unreadable_until_settled_then_readable(store):
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    jid = j["job_id"]
    _answer_all(store, jid, "SECRET ANSWER")
    v = store.job_view(jid)
    assert v["status"] == "judging"
    assert all(a["text"] == "" for a in v["answers"]) and not v["merged"]
    assert "SECRET ANSWER" not in repr(v)
    assert _judge(store, jid)
    v = store.job_view(jid)
    assert v["status"] == "done"
    assert any("SECRET ANSWER" in a["text"] for a in v["answers"])


def test_settlement_pays_from_escrow_with_no_second_debit(store):
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    jid = j["job_id"]
    after_create = store.ledger.balance("alice")
    held = store.ledger.balance(ESCROW)
    _answer_all(store, jid)
    assert _judge(store, jid)
    assert store.job_status(jid) == "done"
    assert store.ledger.balance("alice") == after_create          # no second debit
    assert store.ledger.balance(ESCROW) == 0.0
    paid = _bal(store, "op1") + _bal(store, "judgeop") - 200.0
    assert paid == pytest.approx(held)                            # nothing created, nothing lost
    assert _held(store, jid) == 0
    assert store.ledger_integrity()["ok"]
    assert store.ledger.get("alice").lifetime_spent == pytest.approx(held)   # counted once


def test_expiry_refunds_exactly_once(store, monkeypatch):
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    _expire(store, monkeypatch)
    assert store.job_status(j["job_id"]) == "failed"
    assert store.ledger.balance("alice") == 100.0 and store.ledger.balance(ESCROW) == 0.0
    _expire(store, monkeypatch)     # further passes must not refund again
    _expire(store, monkeypatch)
    assert store.ledger.balance("alice") == 100.0 and store.ledger.balance(ESCROW) == 0.0
    assert store.ledger_integrity()["ok"]


def test_expiry_of_one_job_does_not_touch_anothers_hold(store):
    _setup(store)
    a = store.create_job("alice", "q", job_type="chat", minds=1)
    store.conn.execute("UPDATE jobs SET created=created-10000000 WHERE job_id=?", (a["job_id"],))
    store.conn.commit()
    b = store.create_job("bob", "q", job_type="chat", minds=1)
    held_b = _held(store, b["job_id"])
    store._reap_once()
    assert store.job_status(a["job_id"]) == "failed"
    assert store.job_status(b["job_id"]) == "pending_answers"
    assert store.ledger.balance("alice") == 100.0
    assert store.ledger.balance(ESCROW) == pytest.approx(held_b)
    assert store.ledger_integrity()["ok"]


def test_all_answers_failed_refunds(store):
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    for a in store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='answer'", (j["job_id"],)):
        store.complete_task(a["task_id"], {"text": "", "error": "boom"}, node_id=a["node_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'", (j["job_id"],)).fetchone()
    if jt:
        store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
    assert store.job_status(j["job_id"]) == "failed"
    assert store.ledger.balance("alice") == 100.0 and store.ledger.balance(ESCROW) == 0.0
    assert store.ledger_integrity()["ok"]


def test_no_judge_online_refunds(store):
    _reg(store, "op1")      # no judge node
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    _answer_all(store, j["job_id"])
    assert store.job_status(j["job_id"]) == "failed"
    assert store.ledger.balance("alice") == 100.0 and store.ledger.balance(ESCROW) == 0.0
    assert store.ledger_integrity()["ok"]


def test_failed_job_does_not_serve_answers(store, monkeypatch):
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    _answer_all(store, j["job_id"], "FREE COMPUTE")
    _expire(store, monkeypatch)
    v = store.job_view(j["job_id"])
    assert v["status"] == "failed" and "FREE COMPUTE" not in repr(v)


def test_hold_survives_restart(store):
    _setup(store)
    store.create_job("alice", "q", job_type="chat", minds=1)
    held = store.ledger.balance(ESCROW)
    import passiveworkers.net.store as st
    s2 = st.Store()
    s2._stop.set()
    try:
        assert s2.ledger.balance(ESCROW) == pytest.approx(held)
        assert s2.ledger_integrity()["ok"]
    finally:
        s2.conn.close()


def test_legacy_unheld_job_settles_by_old_path(store):
    # an in-flight job created before D56 has held=0 and nothing in escrow: it must still be charged
    # at settlement, and must not draw on escrow that belongs to other jobs.
    _setup(store)
    other = store.create_job("bob", "other", job_type="chat", minds=1)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    held_a = _held(store, j["job_id"])
    store.ledger.refund("alice", held_a)          # simulate legacy: no hold was ever taken
    store.conn.execute("UPDATE jobs SET held=0 WHERE job_id=?", (j["job_id"],))
    store._save_ledger()
    store.conn.commit()
    escrow_before = store.ledger.balance(ESCROW)
    _answer_all(store, j["job_id"])
    assert _judge(store, j["job_id"])
    assert store.job_status(j["job_id"]) == "done"
    assert store.ledger.balance("alice") == pytest.approx(100 - held_a)
    assert store.ledger.balance(ESCROW) == pytest.approx(escrow_before)   # bob's hold untouched
    assert store.ledger_integrity()["ok"]
    assert store.job_status(other["job_id"]) == "pending_answers"


def test_settle_from_escrow_refuses_when_escrow_short_and_mutates_nothing():
    import passiveworkers.ledger as L
    led = L.Ledger()
    led.open_account("a", grant_amount=100.0)
    led.open_account("w", grant_amount=0.0)
    led.open_account("j", grant_amount=0.0)
    led.hold("a", 5.0)
    with pytest.raises(L.InsufficientCredit):
        led.settle_job("x", "a", {"w": 5.0}, 10.0, "j", 1.0, from_escrow=True)
    assert led.balance(L.ESCROW_ID) == 5.0 and led.balance("w") == 0.0 and led.balance("a") == 95.0
    assert led.conservation_ok()
