"""Handle/owner namespace integrity (F2): a signup must never bind a user secret to a name that
already holds credit — a system account (`__escrow__`, any `__*__`), a node owner's earnings
account, or an existing user (case/whitespace variants included) — and a node must never register
as owner of a system account. Rejections happen BEFORE any enrollment redeem (no token burn, D48)."""
import importlib

import pytest

RESERVED_VARIANTS = ["__escrow__", "__ESCROW__", "  __Escrow__  ", "__escrow__\t", "__system__"]


def _reload():
    for m in ("passiveworkers.ledger", "passiveworkers.net.config", "passiveworkers.net.store",
              "passiveworkers.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "handles.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    import passiveworkers.ledger as led
    importlib.reload(led)
    import passiveworkers.net.config as cfg
    importlib.reload(cfg)
    import passiveworkers.net.store as st
    importlib.reload(st)
    return st.Store()


def _users(store):
    return [r[0] for r in store.conn.execute("SELECT handle FROM users")]


# ----------------------------------------------------------------- reserved system handles
@pytest.mark.parametrize("handle", RESERVED_VARIANTS)
def test_signup_with_reserved_handle_is_rejected(store, handle):
    out = store.register_user(handle)
    assert out == {"error": "handle is reserved"}
    assert "user_secret" not in out and _users(store) == []


def test_signup_cannot_take_over_escrow_holding_credit(store):
    # the F2 repro: an assisted offer holds 50 in escrow; signing up as "__escrow__" used to return
    # a user_secret bound to that balance (spendable via /jobs).
    from passiveworkers.ledger import ESCROW_ID
    store.register_user("alice")
    store.create_job("alice", "human task", job_type="assisted", context="c", requires={})
    held = store.ledger.balance(ESCROW_ID)
    assert held == 50.0
    assert store.register_user(ESCROW_ID) == {"error": "handle is reserved"}
    assert store.ledger.balance(ESCROW_ID) == held and store.ledger.conservation_ok()
    assert store.user_for_secret("") is None and ESCROW_ID not in _users(store)


def test_reserved_signup_does_not_burn_enrollment_token(store):
    tok = store.mint_enrollment(kind="user", grant=40)["enroll_token"]
    assert store.register_user("__escrow__", enroll_token=tok)["error"] == "handle is reserved"
    ok = store.register_user("carol", enroll_token=tok)      # the single-use token still works
    assert ok["balance"] == 40


@pytest.mark.parametrize("owner", RESERVED_VARIANTS)
def test_node_cannot_register_as_reserved_owner(store, owner):
    out = store.register_node({"owner": owner, "answer_model": "m"})
    assert out == {"error": "owner name is reserved"}
    assert store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0
    assert not any(k.strip().casefold() == owner.strip().casefold() for k in store.ledger.accounts)


def test_reserved_node_owner_does_not_burn_enrollment_token(store):
    tok = store.mint_enrollment(kind="node", grant=30)["enroll_token"]
    out = store.register_node({"owner": "__escrow__", "answer_model": "m"}, enroll_token=tok)
    assert out["error"] == "owner name is reserved"
    ok = store.register_node({"owner": "op", "answer_model": "m"}, enroll_token=tok)
    assert ok.get("node_id") and store.ledger.balance("op") == 30


# ----------------------------------------------------------------- names already holding credit
@pytest.mark.parametrize("variant", ["op", "OP", " op ", "Op\t"])
def test_signup_cannot_claim_a_node_owners_earnings_account(store, variant):
    store.register_node({"owner": "op", "answer_model": "m"})
    assert store.ledger.balance("op") == 100.0
    out = store.register_user(variant)
    assert out == {"error": "handle taken"}
    assert _users(store) == [] and store.ledger.balance("op") == 100.0


@pytest.mark.parametrize("variant", ["alice", "ALICE", "  Alice  "])
def test_signup_cannot_shadow_an_existing_user(store, variant):
    first = store.register_user("alice")
    assert first.get("user_secret")
    assert store.register_user(variant) == {"error": "handle taken"}
    assert _users(store) == ["alice"]


def test_taken_signup_does_not_burn_enrollment_token(store):
    store.register_node({"owner": "op", "answer_model": "m"})
    tok = store.mint_enrollment(kind="user", grant=25)["enroll_token"]
    assert store.register_user("op", enroll_token=tok)["error"] == "handle taken"
    assert store.register_user("dave", enroll_token=tok)["balance"] == 25


# ----------------------------------------------------------------- legitimate flows unchanged
def test_user_can_still_run_a_node_crediting_their_own_handle(store):
    # documented give/take (ROADMAP M4): "asking debits the user's handle; contributing their node
    # credits the same handle" — a user registering a node under their own handle must keep working.
    assert store.register_user("alice").get("user_secret")
    out = store.register_node({"owner": "alice", "answer_model": "m"})
    assert out.get("node_id")
    second = store.register_node({"owner": "alice", "answer_model": "m"})   # multi-node operator
    assert second.get("node_id")


def test_distinct_and_dunder_inside_handles_still_allowed(store):
    store.register_node({"owner": "op", "answer_model": "m"})
    for h in ("bob", "op2", "my__handle", "__lead", "trail__", "__escrow__-a1b2c3"):
        assert store.register_user(h).get("user_secret"), h


# ----------------------------------------------------------------- HTTP surface
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "handles_http.db"))
    monkeypatch.setenv("PW_TOKEN", "admintok")
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    monkeypatch.delenv("PW_ENROLL", raising=False)
    _reload()
    import passiveworkers.net.coordinator_app as capp
    from fastapi.testclient import TestClient
    return TestClient(capp.app)


def test_http_signup_reserved_or_taken_is_409(client):
    assert client.post("/nodes/register", json={"owner": "op", "answer_model": "m"},
                       headers={"X-PW-Token": "admintok"}).json().get("node_id")
    for h in ("__escrow__", " __Escrow__ ", "op", "OP"):
        r = client.post("/users", json={"handle": h})
        assert r.status_code == 409, h
        assert "user_secret" not in r.text
    assert client.post("/users", json={"handle": "alice"}).status_code == 200


def test_http_admin_token_register_as_reserved_owner_is_403(client):
    # PR #30 follow-up (b): the shared-token path returned HTTP 200 with an error body here, while
    # the enrolled path already raised 403; a client checking only the status saw success.
    for owner in ("__escrow__", " __Escrow__ "):
        r = client.post("/nodes/register", json={"owner": owner, "answer_model": "m"},
                        headers={"X-PW-Token": "admintok"})
        assert r.status_code == 403, owner
        assert r.json() == {"detail": "owner name is reserved"}
        assert "node_secret" not in r.text
    ok = client.post("/nodes/register", json={"owner": "op", "answer_model": "m"},
                     headers={"X-PW-Token": "admintok"})
    assert ok.status_code == 200 and ok.json().get("node_id")
