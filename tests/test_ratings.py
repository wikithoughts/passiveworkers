"""Assisted ratings → operator reputation, and reputation-gated offers (D24)."""
import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "r.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "1000")
    import importlib
    import passiveworkers.ledger as led          # STARTER_ALLOWANCE is read at import → reload it first
    importlib.reload(led)
    import passiveworkers.net.config as cfg
    importlib.reload(cfg)
    import passiveworkers.net.store as st
    importlib.reload(st)
    return st.Store()


def _node(owner):
    return {"profile": "{}", "answer_model": "", "owner": owner}


def _earned(store, who="alice"):
    """Give a rater independent earned standing so their rating counts toward reputation
    (anti-farming rule: throwaway handles can't move the gate)."""
    store.ledger.open_account(who)
    store.ledger.accounts[who].lifetime_earned = 50.0


def _deliver(store, owner="bob", node="bobnode", asker="alice", requires=None):
    j = store.create_job(asker, "do a task", job_type="assisted", requires=requires or {})
    tid = store.assisted_offers(_node(owner))[0]["task_id"] if not requires else \
        next((o["task_id"] for o in store.assisted_offers(_node(owner)) if o["job_id"] == j["job_id"]), None)
    store.accept_assisted(tid, node, owner)
    store.deliver_assisted(tid, node, "done")
    return j["job_id"]


def test_rating_feeds_reputation(store):
    jid = _deliver(store)
    _earned(store, "alice")
    out = store.rate_assisted(jid, "alice", 8)
    assert out["ok"] and out["counted_toward_reputation"]
    rep, n = store.operator_reputation("bob")
    assert rep == 8.0 and n == 1


def test_unearned_rater_does_not_move_reputation(store):
    # a throwaway asker (no earned standing) can rate, but it must NOT move the gate metric
    jid = _deliver(store)
    out = store.rate_assisted(jid, "alice", 10)
    assert out["ok"] and not out["counted_toward_reputation"]
    assert store.operator_reputation("bob") == (0.0, 0)


def test_rating_is_idempotent(store):
    jid = _deliver(store)
    _earned(store, "alice")
    assert store.rate_assisted(jid, "alice", 8)["ok"]
    assert not store.rate_assisted(jid, "alice", 2)["ok"]   # already rated
    assert store.operator_reputation("bob")[0] == 8.0       # unchanged


def test_only_asker_rates(store):
    jid = _deliver(store)
    assert not store.rate_assisted(jid, "mallory", 10)["ok"]


def test_cannot_rate_undelivered(store):
    j = store.create_job("alice", "x", job_type="assisted")
    assert not store.rate_assisted(j["job_id"], "alice", 5)["ok"]


def test_min_reputation_gate(store):
    # build bob up to rep 8 (rater has earned standing so it counts)
    jid = _deliver(store); _earned(store, "alice"); store.rate_assisted(jid, "alice", 8)
    j_hi = store.create_job("alice", "hi", job_type="assisted", requires={"min_reputation": 9})
    j_ok = store.create_job("alice", "ok", job_type="assisted", requires={"min_reputation": 7})
    seen = {o["job_id"] for o in store.assisted_offers(_node("bob"))}
    assert j_hi["job_id"] not in seen and j_ok["job_id"] in seen


def test_newcomer_excluded_from_gated_but_sees_ungated(store):
    j_gated = store.create_job("alice", "g", job_type="assisted", requires={"min_reputation": 5})
    j_open = store.create_job("alice", "o", job_type="assisted")
    seen = {o["job_id"] for o in store.assisted_offers(_node("newbie"))}
    assert j_gated["job_id"] not in seen and j_open["job_id"] in seen


def test_per_pair_rating_counts_once(store):
    # the same earned rater rating the same operator across two jobs lifts rep only once
    _earned(store, "alice")
    j1 = _deliver(store); store.rate_assisted(j1, "alice", 9)
    j2 = _deliver(store); store.rate_assisted(j2, "alice", 9)
    assert store.operator_reputation("bob") == (9.0, 1)   # counted once, not twice


def test_malformed_min_reputation_rejected_at_creation(store):
    out = store.create_job("alice", "x", job_type="assisted", requires={"min_reputation": "high"})
    assert out["status"] == "failed" and "min_reputation" in out["error"]
    out2 = store.create_job("alice", "y", job_type="assisted", requires={"min_reputation": 99})
    assert out2["status"] == "failed"   # out of 0-10 range


def test_gate_fails_closed_on_bad_value(store):
    # defense-in-depth: even if a malformed gate reached the filter, it admits no one
    assert store._meets_reputation("anyone", {"min_reputation": "abc"}) is False
    assert store._meets_reputation("anyone", {"min_reputation": float("nan")}) is False
    assert store._meets_reputation("anyone", {}) is True   # genuinely ungated still open
