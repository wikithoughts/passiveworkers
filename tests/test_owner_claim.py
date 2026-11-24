"""PW-2 (D56): one-time owner claim of node-earned credit (POST /nodes/claim-user).

Allowed only when the handle has no `users` row (and enrollment is not required), or the node's
enrollment token bound the owner. Once per handle, node-secret authenticated, audited."""
import importlib
import sqlite3

import pytest

TOK = {"X-PW-Token": "tok"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "claim.db"))
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    monkeypatch.delenv("PW_ENROLL", raising=False)
    for m in ("passiveworkers.ledger", "passiveworkers.net.config", "passiveworkers.net.store",
              "passiveworkers.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))
    import passiveworkers.net.coordinator_app as capp
    from fastapi.testclient import TestClient
    return TestClient(capp.app), capp, tmp_path / "claim.db"


def _node(c, owner, headers=TOK, extra=None):
    h = dict(headers)
    h.update(extra or {})
    r = c.post("/nodes/register", json={"owner": owner, "answer_model": "m"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["node_secret"]


def _claim(c, node_secret):
    return c.post("/nodes/claim-user", headers={"X-Node-Secret": node_secret})


def _earn(capp, owner, amount):
    with capp.store.lock:
        capp.store.ledger.accounts[owner].balance += amount
        capp.store._save_ledger()
        capp.store.conn.commit()


def test_claim_requires_node_secret(env):
    c, _, _ = env
    assert c.post("/nodes/claim-user").status_code == 401
    assert _claim(c, "bogus").status_code == 401


def test_claim_unblocks_spending_without_changing_balance(env):
    c, capp, _ = env
    sec = _node(c, "alice")
    _earn(capp, "alice", 40)
    before = capp.store.ledger.accounts["alice"].balance
    granted = capp.store.ledger._granted_total
    cons = capp.store.ledger.conservation_ok()
    r = _claim(c, sec)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["handle"] == "alice" and d["balance"] == round(before, 1)
    # the new user secret authenticates as the owner and sees the same balance
    me = c.get("/me", headers={"X-User-Secret": d["user_secret"]}).json()
    assert me["handle"] == "alice" and me["balance"] == round(before, 1)
    # no credit created or lost
    assert capp.store.ledger.accounts["alice"].balance == before
    assert capp.store.ledger._granted_total == granted
    assert capp.store.ledger.conservation_ok() == cons   # the claim itself moves nothing


def test_second_claim_refused_even_from_another_node(env):
    c, capp, _ = env
    s1 = _node(c, "alice")
    s2 = _node(c, "alice")
    first = _claim(c, s1)
    assert first.status_code == 200
    assert _claim(c, s1).status_code == 409
    assert _claim(c, s2).status_code == 409
    # the first secret still works, and no second one was minted
    assert c.get("/me", headers={"X-User-Secret": first.json()["user_secret"]}).status_code == 200


def test_case_variant_cannot_claim_twice(env):
    c, _, _ = env
    assert _claim(c, _node(c, "alice")).status_code == 200
    # "Alice" is a distinct ledger account but the same handle for claim purposes
    assert _claim(c, _node(c, "ALICE")).status_code in (403, 409)


def test_existing_user_handle_cannot_be_claimed(env):
    c, capp, _ = env
    u = c.post("/users", json={"handle": "bob"}).json()
    sec = _node(c, "bob")            # node registered under an existing user's handle (user-first)
    r = _claim(c, sec)
    assert r.status_code == 403
    # the user's secret is untouched and still works
    assert c.get("/me", headers={"X-User-Secret": u["user_secret"]}).status_code == 200


def test_existing_user_case_variant_cannot_be_claimed(env):
    c, _, _ = env
    c.post("/users", json={"handle": "Bob"})
    assert _claim(c, _node(c, "bob")).status_code == 403


def test_reserved_owner_cannot_register_or_claim(env):
    c, _, _ = env
    r = c.post("/nodes/register", json={"owner": "__escrow__", "answer_model": "m"}, headers=TOK)
    assert r.status_code == 403


def test_claim_is_audited(env):
    c, capp, db = env
    sec = _node(c, "alice")
    _earn(capp, "alice", 5)
    assert _claim(c, sec).status_code == 200
    rows = sqlite3.connect(db).execute(
        "SELECT handle, node_id, balance, mode FROM owner_claims").fetchall()
    assert len(rows) == 1 and rows[0][0] == "alice" and rows[0][2] == 105.0 and rows[0][3] == "create"


def test_signup_still_refused_after_claim(env):
    c, _, _ = env
    _claim(c, _node(c, "alice"))
    assert c.post("/users", json={"handle": "alice"}).status_code == 409


# ------------------------------------------------------------------ enrollment mode
@pytest.fixture()
def enrolled(env, monkeypatch):
    monkeypatch.setenv("PW_ENROLL", "1")
    return env


def _token(c, owner, grant=0):
    return c.post("/admin/enroll", json={"owner": owner, "kind": "node", "grant": grant},
                  headers=TOK).json()["enroll_token"]


def test_enrolled_bound_owner_can_claim(enrolled):
    c, capp, _ = enrolled
    sec = _node(c, "alice", headers={}, extra={"X-Enroll-Token": _token(c, "alice", 10)})
    r = _claim(c, sec)
    assert r.status_code == 200 and r.json()["balance"] == 10.0


def test_enrolled_unbound_owner_cannot_claim(enrolled):
    c, _, _ = enrolled
    # token names no owner -> nothing binds the self-declared handle
    sec = _node(c, "victim", headers={}, extra={"X-Enroll-Token": _token(c, "")})
    assert _claim(c, sec).status_code == 403
    # token bound to a different owner than the node declares
    sec2 = _node(c, "victim2", headers={}, extra={"X-Enroll-Token": _token(c, "someone-else")})
    assert _claim(c, sec2).status_code == 403


def test_unbound_first_node_cannot_preempt_a_bound_node(env):
    c, capp, _ = env
    s_attacker = _node(c, "alice")                        # open registration, shared token, no binding
    tok = c.post("/admin/enroll", json={"owner": "alice", "kind": "node", "grant": 0},
                 headers=TOK).json()["enroll_token"]
    import os
    os.environ["PW_ENROLL"] = "1"
    try:
        s_bound = _node(c, "alice", headers={}, extra={"X-Enroll-Token": tok})
    finally:
        del os.environ["PW_ENROLL"]
    assert _claim(c, s_attacker).status_code == 403
    assert _claim(c, s_bound).status_code == 200


def test_enrolled_bound_claim_over_squatter_rotates_secret(enrolled):
    c, capp, _ = enrolled
    squat = c.post("/users", json={"handle": "alice"}).json()["user_secret"]   # open signup, 0 grant
    sec = _node(c, "alice", headers={}, extra={"X-Enroll-Token": _token(c, "alice", 7)})
    r = _claim(c, sec)
    assert r.status_code == 200
    assert c.get("/me", headers={"X-User-Secret": squat}).status_code == 401
    assert c.get("/me", headers={"X-User-Secret": r.json()["user_secret"]}).status_code == 200
    assert _claim(c, sec).status_code == 409


def test_enrolled_bound_claim_not_on_case_variant_user_row(enrolled):
    c, _, _ = enrolled
    c.post("/users", json={"handle": "Alice"})
    sec = _node(c, "alice", headers={}, extra={"X-Enroll-Token": _token(c, "alice")})
    assert _claim(c, sec).status_code == 403


def test_claim_persists_across_restart(env):
    c, capp, db = env
    sec = _node(c, "alice")
    assert _claim(c, sec).status_code == 200
    import passiveworkers.net.store as st
    s2 = st.Store()
    assert s2.claim_owner(s2.node_for_secret(sec))["error"] == "already claimed"


# ------------------------------------------------------------------ review follow-ups
def test_only_first_node_of_an_owner_may_claim(env):
    c, _, _ = env
    s1 = _node(c, "alice")      # the owner's own node, registered first
    s2 = _node(c, "alice")      # a later node re-declaring the name
    assert _claim(c, s2).status_code == 403
    assert _claim(c, s1).status_code == 200


def test_padded_owner_claims_its_exact_ledger_key(env):
    c, capp, _ = env
    sec = _node(c, "Pat ")
    _earn(capp, "Pat ", 3)
    r = _claim(c, sec)
    assert r.status_code == 200 and r.json()["balance"] == 103.0
    me = c.get("/me", headers={"X-User-Secret": r.json()["user_secret"]}).json()
    assert me["balance"] == 103.0


def test_padded_owner_cannot_claim_a_stripped_users_account(env):
    c, _, _ = env
    u = c.post("/users", json={"handle": "Pat"}).json()
    assert _claim(c, _node(c, "Pat ")).status_code == 403
    assert c.get("/me", headers={"X-User-Secret": u["user_secret"]}).status_code == 200


def test_ambiguous_case_variants_are_both_refused(env):
    c, _, _ = env
    sb = _node(c, "Bob")
    sl = _node(c, "bob")
    assert _claim(c, sl).status_code == 403     # a variant can't win the name...
    assert _claim(c, sb).status_code == 403     # ...and nobody gains a user credential either
    assert c.post("/users", json={"handle": "bob"}).status_code == 409


def test_failed_write_leaves_no_user_or_claim(env, monkeypatch):
    c, capp, _ = env
    sec = _node(c, "alice")
    nid = capp.store.node_for_secret(sec)
    real = capp.store.conn

    class Boom:
        def __getattr__(self, n):
            return getattr(real, n)

        def execute(self, sql, *a):
            if sql.startswith("INSERT INTO owner_claims"):
                raise RuntimeError("boom")
            return real.execute(sql, *a)
    monkeypatch.setattr(capp.store, "conn", Boom())
    with pytest.raises(RuntimeError):
        capp.store.claim_owner(nid)
    monkeypatch.setattr(capp.store, "conn", real)
    assert real.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert _claim(c, sec).status_code == 200      # not burned
