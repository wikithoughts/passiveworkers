"""D35 stage chaining: a job's `then` follow-on spawns an ASSISTED hand-off (the "connecting them"
step), seeded with the parent's deliverable, charged to the same asker, linked parent↔child. Drives
the Store directly (no network)."""
import json
import pytest


def _store(tmp_path, monkeypatch, credits="100000"):
    monkeypatch.setenv("PW_DB", str(tmp_path / "pipe.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", credits)
    import importlib
    import council.ledger as led
    importlib.reload(led)
    import council.net.config as cfg
    importlib.reload(cfg)
    import council.net.store as st
    importlib.reload(st)
    return st.Store()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    return _store(tmp_path, monkeypatch)


def _reg(store, owner, answer_model="m", judge=False):
    out = store.register_node({
        "owner": owner, "name": owner, "country": "X", "lens": "neutral",
        "answer_model": answer_model, "can_judge": judge, "judge_model": ("m" if judge else ""),
        "profile": {"cores": 4, "ram_gb": 16, "models": [answer_model] if answer_model else []},
    })
    return out["node_id"]


def _run_code_job(store, then=None, minds=2):
    """Create + fully complete a code_generation job; return its job_id."""
    j = store.create_job("alice", "generate code per spec", job_type="code_generation",
                         items=["spec0", "spec1"], minds=minds, then=then)
    jid = j["job_id"]
    assert j["status"] == "pending_answers", j
    for t in store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='answer'", (jid,)):
        shard = json.loads(t["payload"])["shard"]
        res = {"text": "done",
               "results": [{"i": e["i"], "item": e["item"], "output": f"code_{e['i']}"} for e in shard]}
        store.complete_task(t["task_id"], res, node_id=t["node_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'", (jid,)).fetchone()
    store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
    return jid


def _child_context(store, child_id):
    row = store.conn.execute(
        "SELECT payload FROM tasks WHERE job_id=? AND type='assisted'", (child_id,)).fetchone()
    return json.loads(row["payload"])["context"]


def test_code_generation_chains_to_assisted_followon(store):
    _reg(store, "opA")
    _reg(store, "opB")
    _reg(store, "judge", answer_model="", judge=True)
    jid = _run_code_job(store, then={"question": "integrate the generated units and build"})
    parent = store.job_view(jid)
    assert parent["status"] == "done"
    child_id = parent["child"]
    assert child_id, "expected a `then` follow-on to be spawned"
    child = store.job_view(child_id)
    assert child["type"] == "assisted" and child["status"] == "pending_assist"
    assert child["parent"] == jid and child["asker"] == "alice"
    # the follow-on carries the parent's assembled deliverable as bounded context (the "parts")
    ctx = _child_context(store, child_id)
    assert "code_0" in ctx and "code_1" in ctx
    assert store.ledger.conservation_ok()


def test_no_then_means_no_chain(store):
    _reg(store, "opA")
    _reg(store, "opB")
    _reg(store, "judge", answer_model="", judge=True)
    jid = _run_code_job(store, then=None)
    assert store.job_view(jid)["child"] is None


def test_followon_carries_no_then_so_chains_never_recurse(store):
    _reg(store, "opA")
    _reg(store, "opB")
    _reg(store, "judge", answer_model="", judge=True)
    jid = _run_code_job(store, then={"question": "integrate the units"})
    child_id = store.job_view(jid)["child"]
    # the child is an assisted job with NO then_spec → delivering it spawns no grandchild
    row = store.conn.execute("SELECT then_spec FROM jobs WHERE job_id=?", (child_id,)).fetchone()
    assert row["then_spec"] is None


def test_chain_skipped_when_asker_cannot_fund_followon(tmp_path, monkeypatch):
    # starter funds stage-1 (code_generation minds=1: pool 20 + fee 5 = 25) but NOT the assisted
    # follow-on escrow (pool 50). The chain must skip cleanly — parent done, no child, conserved.
    store = _store(tmp_path, monkeypatch, credits="60")
    _reg(store, "opA")
    _reg(store, "judge", answer_model="", judge=True)
    jid = _run_code_job(store, then={"question": "integrate the units"}, minds=1)
    parent = store.job_view(jid)
    assert parent["status"] == "done"
    assert parent["child"] is None                       # follow-on skipped (unaffordable)
    # no stray assisted job was created either
    n_assisted = store.conn.execute(
        "SELECT COUNT(*) c FROM jobs WHERE type='assisted'").fetchone()["c"]
    assert n_assisted == 0
    assert store.ledger.conservation_ok()


def test_chain_failure_never_fails_the_parent(store, monkeypatch):
    # review (HIGH): the parent is already settled+committed before chaining — a follow-on that
    # blows up must be swallowed (best-effort), never propagating to fail the parent's completion.
    _reg(store, "opA")
    _reg(store, "opB")
    _reg(store, "judge", answer_model="", judge=True)

    def _boom(*a, **k):
        raise RuntimeError("simulated follow-on failure")

    monkeypatch.setattr(store, "_create_assisted", _boom)
    jid = _run_code_job(store, then={"question": "integrate the units"})   # must NOT raise
    parent = store.job_view(jid)
    assert parent["status"] == "done" and parent["child"] is None
    assert store.ledger.conservation_ok()


def test_create_assisted_refunds_hold_if_persisting_fails(store, monkeypatch):
    # review (HIGH): if the escrow hold succeeds but the INSERT fails, the in-memory hold must be
    # rolled back so the ledger never diverges from the DB (no stranded credit).
    import sqlite3

    class _Proxy:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, *a):
            if isinstance(sql, str) and "INSERT INTO jobs" in sql and "pool,type" in sql:
                raise sqlite3.OperationalError("simulated persist failure")
            return self._real.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self._real, name)

    store.ledger.open_account("alice")
    before = store.ledger.accounts["alice"].balance
    monkeypatch.setattr(store, "conn", _Proxy(store.conn))
    with pytest.raises(Exception):
        store._create_assisted("alice", "do x", "", None)
    monkeypatch.undo()
    assert store.ledger.accounts["alice"].balance == before     # hold rolled back
    assert store.ledger.conservation_ok()


def test_chat_job_can_also_declare_a_followon(store):
    # chaining isn't code-specific: any automated job that settles can hand off
    _reg(store, "opA")
    _reg(store, "judge", answer_model="", judge=True)
    j = store.create_job("alice", "draft a plan", job_type="chat", minds=1,
                         then={"question": "execute the plan by hand"})
    jid = j["job_id"]
    t = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='answer'", (jid,)).fetchone()
    store.complete_task(t["task_id"], {"text": "the plan: do X then Y"}, node_id=t["node_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'", (jid,)).fetchone()
    store.complete_task(jt["task_id"], {"scores": {}, "merged": "PLAN: X then Y"},
                        node_id=jt["node_id"])
    parent = store.job_view(jid)
    assert parent["status"] == "done" and parent["child"]
    assert store.job_view(parent["child"])["type"] == "assisted"
