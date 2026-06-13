"""D32 failover: the reaper reassigns a stalled task to a fresh node (offline node OR stuck claim)
instead of failing the whole job; it fails only when no replacement exists or retries are exhausted;
and ledger conservation holds across a reassign-then-settle. Drives the Store directly (no network)."""
import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "reap.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100000")
    import importlib
    import council.ledger as led
    importlib.reload(led)
    import council.net.config as cfg
    importlib.reload(cfg)
    import council.net.store as st
    importlib.reload(st)
    return st.Store()


def _reg(store, owner, answer_model="m", judge=False):
    out = store.register_node({
        "owner": owner, "name": owner, "country": "X", "lens": "neutral",
        "answer_model": answer_model, "can_judge": judge, "judge_model": ("m" if judge else ""),
        "profile": {"cores": 4, "ram_gb": 16, "models": [answer_model] if answer_model else []},
    })
    return out["node_id"]


def _answer(store, job_id):
    return store.conn.execute(
        "SELECT * FROM tasks WHERE job_id=? AND type='answer'", (job_id,)).fetchone()


def _job_status(store, job_id):
    return store.conn.execute("SELECT status, error FROM jobs WHERE job_id=?", (job_id,)).fetchone()


def _set(store, sql, params):
    store.conn.execute(sql, params)
    store.conn.commit()


def test_offline_node_task_is_reassigned_not_job_failed(store):
    n1, n2 = _reg(store, "op1"), _reg(store, "op2")
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    t = _answer(store, j["job_id"])
    assigned = t["node_id"]
    spare = n2 if assigned == n1 else n1
    _set(store, "UPDATE nodes SET last_seen=? WHERE node_id=?", (1.0, assigned))  # assigned offline
    store._reap_once()
    t2 = store.conn.execute("SELECT * FROM tasks WHERE task_id=?", (t["task_id"],)).fetchone()
    assert t2["node_id"] == spare and t2["status"] == "queued" and t2["retries"] == 1
    assert t2["claimed_at"] is None
    assert _job_status(store, j["job_id"])["status"] == "pending_answers"   # NOT failed


def test_stuck_claim_is_reassigned_even_if_node_heartbeats(store):
    n1, n2 = _reg(store, "op1"), _reg(store, "op2")
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    t = _answer(store, j["job_id"])
    assigned = t["node_id"]
    spare = n2 if assigned == n1 else n1
    store.next_task(assigned)                                   # node claims it
    _set(store, "UPDATE tasks SET claimed_at=? WHERE task_id=?", (1.0, t["task_id"]))  # ancient claim
    store.heartbeat(assigned, 0.0)                              # node is still alive (not offline)
    store._reap_once()
    t2 = store.conn.execute("SELECT * FROM tasks WHERE task_id=?", (t["task_id"],)).fetchone()
    assert t2["node_id"] == spare and t2["status"] == "queued" and t2["retries"] == 1


def test_no_replacement_available_fails_the_job(store):
    n1 = _reg(store, "solo")
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    _set(store, "UPDATE nodes SET last_seen=? WHERE node_id=?", (1.0, n1))   # the only node goes away
    store._reap_once()
    st = _job_status(store, j["job_id"])
    assert st["status"] == "failed" and "no replacement" in st["error"]


def test_retries_exhausted_fails_the_job(store):
    n1, n2 = _reg(store, "op1"), _reg(store, "op2")
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    t = _answer(store, j["job_id"])
    _set(store, "UPDATE tasks SET retries=2 WHERE task_id=?", (t["task_id"],))     # at the cap
    _set(store, "UPDATE nodes SET last_seen=? WHERE node_id=?", (1.0, t["node_id"]))  # and stalled
    store._reap_once()
    st = _job_status(store, j["job_id"])
    assert st["status"] == "failed" and "reassignment" in st["error"]


def test_forward_progress_resets_claim_and_prevents_reassignment(store):
    # review (HIGH): a slow-but-honest node reporting forward progress must keep its claim —
    # progress resets claimed_at so the reaper does NOT reassign it out from under itself.
    n1, n2 = _reg(store, "op1"), _reg(store, "op2")
    j = store.create_job("alice", "q", job_type="shard_map", items=["a", "b"], minds=1)
    t = _answer(store, j["job_id"])
    node = t["node_id"]
    store.next_task(node)
    _set(store, "UPDATE tasks SET claimed_at=? WHERE task_id=?", (1.0, t["task_id"]))  # long-running
    store.heartbeat(node, 0.0)
    assert store.update_task_progress(node, t["task_id"], 1, 2) is True   # forward progress
    store._reap_once()
    t2 = store.conn.execute("SELECT * FROM tasks WHERE task_id=?", (t["task_id"],)).fetchone()
    assert t2["node_id"] == node and t2["retries"] == 0   # kept its claim — not reassigned


def test_non_advancing_progress_cannot_keep_a_stalled_claim_alive(store):
    # review (MED): repeating the SAME progress must NOT reset the clock — a stalled node that only
    # spams (without advancing) still times out and is reassigned.
    n1, n2 = _reg(store, "op1"), _reg(store, "op2")
    j = store.create_job("alice", "q", job_type="shard_map", items=["a", "b"], minds=1)
    t = _answer(store, j["job_id"])
    node = t["node_id"]
    spare = n2 if node == n1 else n1
    store.next_task(node)
    store.update_task_progress(node, t["task_id"], 1, 2)                  # advance once
    _set(store, "UPDATE tasks SET claimed_at=? WHERE task_id=?", (1.0, t["task_id"]))  # then stalls
    store.heartbeat(node, 0.0)
    assert store.update_task_progress(node, t["task_id"], 1, 2) is True   # repeat → ignored
    store._reap_once()
    assert _answer(store, j["job_id"])["node_id"] == spare                # reassigned despite spam


def test_progress_rejects_out_of_range_and_foreign_node(store):
    # review (LOW + ownership): bounded, ordered contract; only the owner may report.
    _reg(store, "op1")
    j = store.create_job("alice", "q", job_type="shard_map", items=["a", "b", "c"], minds=1)
    t = _answer(store, j["job_id"])
    node = t["node_id"]
    store.next_task(node)
    assert store.update_task_progress(node, t["task_id"], 5, 3) is False        # done > total
    assert store.update_task_progress(node, t["task_id"], -1, 3) is False       # done < 0
    assert store.update_task_progress(node, t["task_id"], 1, 0) is False        # total <= 0
    assert store.update_task_progress("not-owner", t["task_id"], 1, 3) is False  # ownership
    assert store.update_task_progress(node, t["task_id"], 2, 3) is True         # valid forward


def test_conservation_holds_after_reassign_then_settle(store):
    n1, n2 = _reg(store, "op1"), _reg(store, "op2")
    _reg(store, "judge", answer_model="", judge=True)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    jid = j["job_id"]
    t = _answer(store, jid)
    assigned = t["node_id"]
    spare = n2 if assigned == n1 else n1
    _set(store, "UPDATE nodes SET last_seen=? WHERE node_id=?", (1.0, assigned))   # offline → failover
    store._reap_once()
    t2 = _answer(store, jid)
    assert t2["node_id"] == spare and t2["status"] == "queued"
    # the replacement completes the answer; the judge then settles the job
    assert store.complete_task(t2["task_id"], {"text": "hi", "tokens": 1, "elapsed_s": 0.1},
                               node_id=spare)
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'", (jid,)).fetchone()
    assert store.complete_task(jt["task_id"], {"scores": {t2["worker_id"]: 7.0}},
                               node_id=jt["node_id"])
    assert store.job_view(jid)["status"] == "done"
    assert store.ledger.conservation_ok()
