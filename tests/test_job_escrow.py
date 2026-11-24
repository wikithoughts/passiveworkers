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
    store.ledger.refund("alice", held_a, job_id=j["job_id"])          # simulate legacy: no hold was ever taken
    store.conn.execute("UPDATE jobs SET held=NULL WHERE job_id=?", (j["job_id"],))
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


def _judge_owner(store, job_id):
    return store.conn.execute("SELECT owner FROM tasks WHERE job_id=? AND type='judge'",
                              (job_id,)).fetchone()["owner"]


def test_judge_prefers_another_owner_over_the_askers_own_node(store, capsys):
    # owner decision e1355: prefer judges owned by someone else, even when the asker's is "fresher".
    _reg(store, "op1")
    _reg(store, "judgeop", judge=True)
    _reg(store, "alice", judge=True)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    _answer_all(store, j["job_id"])
    assert _judge_owner(store, j["job_id"]) == "judgeop"
    assert "falling back" not in capsys.readouterr().out


def test_askers_own_judge_is_the_fallback_when_no_other_judge_exists(store, capsys):
    # lone-operator setups keep working: the asker's own judge node is used, and it is logged.
    _reg(store, "op1")
    _reg(store, "alice", judge=True)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    _answer_all(store, j["job_id"])
    assert _judge_owner(store, j["job_id"]) == "alice"
    assert store.job_status(j["job_id"]) == "judging"
    assert "falling back to the asker's own judge" in capsys.readouterr().out
    assert _judge(store, j["job_id"])
    assert store.job_status(j["job_id"]) == "done" and store.ledger_integrity()["ok"]


def test_asker_judging_own_job_cannot_steer_the_payout(store):
    # round-3 review H1: with the own-judge fallback, an asker whose node also answered could score
    # every other worker 0 and take the whole pool back. The asker's scores must be ignored.
    n1 = _reg(store, "op1")
    n2 = _reg(store, "op2")
    na = _reg(store, "alice", judge=True)
    j = store.create_job("alice", "q", job_type="chat", minds=3)
    _answer_all(store, j["job_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'",
                            (j["job_id"],)).fetchone()
    assert jt["owner"] == "alice"
    assert store.complete_task(jt["task_id"], {"scores": {n1: 0, n2: 0, na: 10}}, node_id=na)
    assert store.job_status(j["job_id"]) == "done"
    assert store.ledger.balance("op1") > 100.0 and store.ledger.balance("op2") > 100.0
    assert store.ledger_integrity()["ok"] and store.ledger.conservation_ok()


def test_alarm_failure_after_reaper_commit_does_not_roll_back_refunds(store, monkeypatch):
    # round-3 review M1: _escrow_alarm used to run inside the reaper's rollback scope.
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    _expire_job(store, j["job_id"])
    monkeypatch.setattr(store, "_escrow_alarm", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    store._reap_once()
    assert store.job_status(j["job_id"]) == "failed"
    assert store.ledger.balance("alice") == 100.0 and store.ledger.balance(ESCROW) == 0.0
    assert store.ledger._holds == {}


def test_zero_amount_holds_leave_no_bucket(store):
    store.ledger.open_account("z", grant_amount=5.0)
    store.ledger.hold("z", 0.0, "jobz")
    assert "jobz" not in store.ledger._holds


def test_failover_prefers_other_owner_then_falls_back_to_askers_own(store, capsys):
    _reg(store, "op1")
    jn = _reg(store, "judgeop", judge=True)
    _reg(store, "alice", judge=True)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    _answer_all(store, j["job_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'",
                            (j["job_id"],)).fetchone()
    job = store.conn.execute("SELECT * FROM jobs WHERE job_id=?", (j["job_id"],)).fetchone()
    repl = store._pick_replacement(job, jt)           # the other owner's node is the current judge
    assert repl["owner"] == "alice" and jt["node_id"] == jn
    assert "falling back" in capsys.readouterr().out
    _reg(store, "judge2", judge=True)
    assert store._pick_replacement(job, jt)["owner"] == "judge2"


def test_baseline_not_served_before_settlement(store):
    # review finding 2: the single-model baseline is an answer; a refunded job must not serve it.
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    store.set_baseline(j["job_id"], {"text": "BASELINE ANSWER"})
    assert store.job_view(j["job_id"])["baseline"] is None
    _answer_all(store, j["job_id"])
    assert _judge(store, j["job_id"])
    assert "BASELINE ANSWER" in repr(store.job_view(j["job_id"])["baseline"])


def test_short_escrow_at_settlement_leaves_job_open_and_refundable(store):
    # review finding 3: a refused settlement must not strand the hold on a closed job.
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    held = store.ledger.balance(ESCROW)
    _answer_all(store, j["job_id"])
    store.ledger.accounts[ESCROW].balance = held - 1.0      # simulate a desync
    assert _judge(store, j["job_id"])
    assert store.job_status(j["job_id"]) == "judging"       # not failed with the hold stranded
    store.ledger.accounts[ESCROW].balance = held            # ledger repaired
    store.conn.execute("UPDATE jobs SET created=created-10000000 WHERE job_id=?", (j["job_id"],))
    store.conn.commit()
    store._reap_once()
    assert store.job_status(j["job_id"]) == "failed"
    assert store.ledger.balance("alice") == 100.0 and store.ledger_integrity()["ok"]


def _deficit(store, job_id, amount=1.0):
    """Simulate a ledger desync that belongs to ONE job: its own escrow bucket and the pooled escrow
    are both `amount` short (a legacy desync or a bug elsewhere)."""
    store.ledger.accounts[ESCROW].balance = round(store.ledger.accounts[ESCROW].balance - amount, 4)
    holds = getattr(store.ledger, "_holds", None)
    if holds is not None:
        holds[job_id] = round(holds[job_id] - amount, 4)


def _expire_job(store, job_id):
    store.conn.execute("UPDATE jobs SET created=created-10000000 WHERE job_id=?", (job_id,))
    store.conn.commit()


def test_deficit_on_job_b_does_not_freeze_settlement_or_refund_of_job_a(store, capsys):
    # third review M1: the old pooled all-or-nothing check froze EVERY payout once any job was short.
    _setup(store)
    a = store.create_job("alice", "qa", job_type="chat", minds=1)
    b = store.create_job("bob", "qb", job_type="chat", minds=1)
    c = store.create_job("carol", "qc", job_type="chat", minds=1)
    held_b = _held(store, b["job_id"])
    _deficit(store, b["job_id"])
    _answer_all(store, a["job_id"])
    assert _judge(store, a["job_id"])
    assert store.job_status(a["job_id"]) == "done"              # A settles from its OWN hold
    _expire_job(store, c["job_id"])
    _expire_job(store, b["job_id"])
    store._reap_once()
    assert store.job_status(c["job_id"]) == "failed" and store.ledger.balance("carol") == 100.0
    assert store.job_status(b["job_id"]) == "pending_answers"   # the deficit lands on B alone
    assert store.ledger.balance("bob") == pytest.approx(100 - held_b)
    assert not store.ledger_integrity()["ok"]
    assert "ALARM" in capsys.readouterr().out                   # and it is loud, not silent


def test_settlement_of_the_short_job_itself_is_refused_without_touching_others(store):
    _setup(store)
    a = store.create_job("alice", "qa", job_type="chat", minds=1)
    b = store.create_job("bob", "qb", job_type="chat", minds=1)
    held_b = _held(store, b["job_id"])
    _deficit(store, a["job_id"])
    _answer_all(store, a["job_id"])
    assert _judge(store, a["job_id"])
    assert store.job_status(a["job_id"]) == "judging"
    assert store.ledger.balance(ESCROW) >= held_b - 1e-6        # B's hold was not drawn on
    assert store.ledger.balance("judgeop") == 100.0 and store.ledger.balance("op1") == 100.0


def _assisted_offer(store, asker="dave"):
    j = store.create_job(asker, "human task", job_type="assisted")
    tid = store.conn.execute("SELECT task_id FROM tasks WHERE job_id=? AND type='assisted'",
                             (j["job_id"],)).fetchone()["task_id"]
    return j["job_id"], tid


def test_assisted_delivery_cannot_drain_a_council_hold(store):
    # third review M2: assisted release()/refund() bypassed the guard, so a short assisted bucket
    # was silently paid out of a council job's hold (the pooled total still covered it).
    _setup(store)
    b = store.create_job("bob", "qb", job_type="chat", minds=1)
    held_b = _held(store, b["job_id"])
    jid, tid = _assisted_offer(store)
    assert store.accept_assisted(tid, "opnode", "opuser")["ok"]
    _deficit(store, jid)
    out = store.deliver_assisted(tid, "opnode", "done")
    assert not out["ok"] and "refused" in out["error"]
    assert store.ledger.balance("opuser") == 100.0 or "opuser" not in store.ledger.accounts \
        or store.ledger.balance("opuser") == 0.0
    assert store.ledger.balance(ESCROW) >= held_b - 1e-6        # council hold intact


def test_assisted_expiry_refund_cannot_drain_a_council_hold(store):
    _setup(store)
    b = store.create_job("bob", "qb", job_type="chat", minds=1)
    held_b = _held(store, b["job_id"])
    jid, _ = _assisted_offer(store)
    _deficit(store, jid)
    _expire_job(store, jid)
    store._reap_once()
    assert store.job_status(jid) in ("pending_assist", "assisting")   # kept open, not paid from B
    assert store.ledger.balance(ESCROW) >= held_b - 1e-6
    assert store.job_status(b["job_id"]) == "pending_answers"


def test_concurrent_settle_and_refund_conserve_credit(store):
    import threading
    _setup(store)
    a = store.create_job("alice", "qa", job_type="chat", minds=1)
    b = store.create_job("bob", "qb", job_type="chat", minds=1)
    _answer_all(store, a["job_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'",
                            (a["job_id"],)).fetchone()
    _expire_job(store, b["job_id"])
    errs = []

    def settle():
        try:
            store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
        except Exception as e:   # pragma: no cover
            errs.append(e)

    def reap():
        try:
            for _ in range(3):
                store._reap_once()
        except Exception as e:   # pragma: no cover
            errs.append(e)

    ts = [threading.Thread(target=settle), threading.Thread(target=reap)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert store.job_status(a["job_id"]) == "done" and store.job_status(b["job_id"]) == "failed"
    assert store.ledger.balance("bob") == 100.0 and store.ledger.balance(ESCROW) == 0.0
    assert store.ledger_integrity()["ok"] and store.ledger.conservation_ok()


def test_zero_cost_new_job_is_not_mistaken_for_a_legacy_job(store):
    # third review L4: held==0 doubled as the legacy marker, so a zero-cost new job was charged
    # CONFIG.worker_pool at settlement although nothing was held.
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    held = _held(store, j["job_id"])
    store.ledger.refund("alice", held, job_id=j["job_id"]) if held else None
    store.conn.execute("UPDATE jobs SET held=0.0, pool=0.0 WHERE job_id=?", (j["job_id"],))
    store._save_ledger()
    store.conn.commit()
    _answer_all(store, j["job_id"])
    assert _judge(store, j["job_id"])
    assert store.job_status(j["job_id"]) == "done"
    assert store.ledger.balance("alice") == 100.0               # never charged for a zero-cost job
    assert store.ledger_integrity()["ok"]


def test_no_judge_online_reason_is_distinct_from_the_own_node_fallback(store, capsys):
    # third review L5: "no judge online" (refund) must read differently from "used the asker's own".
    _reg(store, "op1")
    j1 = store.create_job("bob", "q", job_type="chat", minds=1)
    _answer_all(store, j1["job_id"])
    assert store.job_view(j1["job_id"])["error"] == "no judge node online"
    assert "falling back" not in capsys.readouterr().out


def test_buckets_survive_restart_and_are_rebuilt_for_a_pre_bucket_ledger(store):
    import json
    import passiveworkers.net.store as st
    _setup(store)
    b = store.create_job("bob", "q", job_type="chat", minds=1)
    jid, _ = _assisted_offer(store)
    holds = dict(store.ledger._holds)
    assert set(holds) == {b["job_id"], jid}
    s2 = st.Store()
    s2._stop.set()
    try:
        assert s2.ledger._holds == holds and s2.ledger_integrity()["ok"]
        # a ledger saved before per-job buckets has no "holds" key: rebuilt from the jobs table
        d = json.loads(store.conn.execute("SELECT data FROM ledger WHERE id=1").fetchone()["data"])
        d.pop("holds")
        store.conn.execute("UPDATE ledger SET data=? WHERE id=1", (json.dumps(d),))
        store.conn.commit()
        s3 = st.Store()
        s3._stop.set()
        try:
            assert s3.ledger._holds == holds and s3.ledger_integrity()["ok"]
        finally:
            s3.conn.close()
    finally:
        s2.conn.close()


def test_integrity_flags_a_hold_stranded_on_a_closed_job(store):
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    store.conn.execute("UPDATE jobs SET status='failed' WHERE job_id=?", (j["job_id"],))
    store.conn.commit()
    assert not store.ledger_integrity()["ok"]


def test_zero_pool_job_never_charges_the_judge(store):
    # review finding 4: a stored pool of 0 was treated as missing, making judge_fee negative.
    _setup(store)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    held = store.ledger.balance(ESCROW)
    store.conn.execute("UPDATE jobs SET pool=0 WHERE job_id=?", (j["job_id"],))
    store.conn.commit()
    _answer_all(store, j["job_id"])
    assert _judge(store, j["job_id"])
    assert store.job_status(j["job_id"]) == "done"
    assert store.ledger.balance("judgeop") == pytest.approx(100 + held)   # whole hold is the judge fee
    assert store.ledger.balance("op1") == 100.0
    assert store.ledger_integrity()["ok"]


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
