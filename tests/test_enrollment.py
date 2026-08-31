"""D37 per-operator enrollment tokens: the starter grant (and node registration, when PW_ENROLL is
on) requires redeeming an admin-minted token — closing the Sybil starter-grant hole. Default off →
current open behavior is unchanged (backward compatible)."""
import importlib
import pytest


def _reload():
    for m in ("council.ledger", "council.net.config", "council.net.store",
              "council.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))


# ----------------------------------------------------------------- store primitives
@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "enr.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    import council.ledger as led
    importlib.reload(led)
    import council.net.config as cfg
    importlib.reload(cfg)
    import council.net.store as st
    importlib.reload(st)
    return st.Store()


def test_mint_and_redeem_consumes_uses(store):
    tok = store.mint_enrollment(owner="op", kind="node", grant=50, max_uses=2)["enroll_token"]
    r1 = store.redeem_enrollment(tok, kind="node")
    assert r1["ok"] and r1["grant"] == 50 and r1["owner"] == "op"
    assert store.redeem_enrollment(tok, kind="node")["ok"]        # 2nd use ok
    assert not store.redeem_enrollment(tok, kind="node")["ok"]    # exhausted (max_uses=2)


def test_redeem_kind_and_validity(store):
    node_tok = store.mint_enrollment(kind="node")["enroll_token"]
    assert not store.redeem_enrollment(node_tok, kind="user")["ok"]   # wrong kind
    any_tok = store.mint_enrollment(kind="any")["enroll_token"]
    assert store.redeem_enrollment(any_tok, kind="user")["ok"]        # 'any' matches
    assert not store.redeem_enrollment("", kind="node")["ok"]         # missing
    assert not store.redeem_enrollment("bogus", kind="node")["ok"]    # invalid


_HEX_CHARS = set("0123456789abcdef")


def test_minted_tokens_and_secrets_are_lowercase_hex(store):
    # regression: secrets.token_urlsafe()'s alphabet can produce a token starting with '-', which
    # council/net/submit.py's argparse then misparses as a flag (SystemExit(2)) — flaky
    # test_invite_then_ask_with_enroll_token_gets_starter_grant. token_hex() can never do this.
    tokens = []
    for i in range(20):
        tokens.append(store.mint_enrollment(owner=f"op{i}", kind="any")["enroll_token"])
        tokens.append(store.register_node({"owner": f"node-owner{i}", "answer_model": "m"})
                      ["node_secret"])
        tokens.append(store.register_user(f"user{i}")["user_secret"])
    assert len(tokens) == 60
    for tok in tokens:
        assert tok, "token must be non-empty"
        assert not tok.startswith("-"), f"token starts with '-': {tok!r}"
        assert set(tok) <= _HEX_CHARS, f"token has non-hex characters: {tok!r}"


def test_open_account_grant_amounts_conserve(store):
    store.ledger.open_account("a")                       # default → STARTER (100)
    store.ledger.open_account("b", grant_amount=0)       # no grant
    store.ledger.open_account("c", grant_amount=25)      # custom
    assert store.ledger.accounts["a"].balance == 100
    assert store.ledger.accounts["b"].balance == 0
    assert store.ledger.accounts["c"].balance == 25
    assert store.ledger.conservation_ok()


def test_zero_grant_identities_mint_nothing(store):
    # the Sybil closure: many ungranted identities create accounts but mint ZERO credit
    before = store.ledger._granted_total
    for i in range(10):
        store.register_user(f"u{i}", grant_amount=0)
    assert all(store.ledger.accounts[f"u{i}"].balance == 0 for i in range(10))
    assert store.ledger._granted_total == before
    assert store.ledger.conservation_ok()


def test_non_finite_or_negative_grant_cannot_corrupt_ledger(store):
    # review (CRITICAL): inf/nan/negative grants must never reach the ledger (would break conservation)
    for bad in (float("inf"), float("-inf"), float("nan"), -50):
        store.ledger.open_account(f"acct-{bad}", grant_amount=bad)
        bal = store.ledger.accounts[f"acct-{bad}"].balance
        import math as _m
        assert _m.isfinite(bal) and bal >= 0.0
    assert store.ledger.conservation_ok()
    # mint clamps too (it's directly callable)
    m = store.mint_enrollment(grant=float("inf"))
    import math as _m
    assert _m.isfinite(m["grant"]) and m["grant"] >= 0.0


def test_enroll_mode_default_grant_is_zero(monkeypatch, store):
    # review (#4): with PW_ENROLL on, the DEFAULT (None) grant is 0, so no call site (create_job,
    # accept_assisted, …) can mint a fresh starter grant without an explicit token amount.
    monkeypatch.setenv("PW_ENROLL", "1")
    store.ledger.open_account("ghost")                    # default grant, enroll on
    assert store.ledger.accounts["ghost"].balance == 0.0
    assert store.ledger.conservation_ok()


# ----------------------------------------------------------------- HTTP, PW_ENROLL on
ADMIN = {"X-PW-Token": "admintok"}


@pytest.fixture()
def eclient(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "enr_http.db"))
    monkeypatch.setenv("PW_TOKEN", "admintok")
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    monkeypatch.setenv("PW_ENROLL", "1")
    _reload()
    import council.net.coordinator_app as capp
    from fastapi.testclient import TestClient
    return TestClient(capp.app)


def test_admin_enroll_requires_admin_token(eclient):
    assert eclient.post("/admin/enroll", json={"kind": "node"}).status_code == 401
    assert eclient.post("/admin/enroll", json={"kind": "node"}, headers=ADMIN).status_code == 200


def test_admin_enroll_rejects_bad_grant_and_kind(eclient):
    # review: the API boundary rejects negative grants and typo'd kinds (422). Non-finite (inf/nan)
    # is additionally clamped at the store/ledger chokepoint — see the store-level test above; the
    # `allow_inf_nan=False` field rejects it too, so it can never reach the ledger.
    assert eclient.post("/admin/enroll", json={"grant": -5}, headers=ADMIN).status_code == 422
    assert eclient.post("/admin/enroll", json={"kind": "NODE"}, headers=ADMIN).status_code == 422


def test_node_registration_requires_enrollment_token(eclient):
    # the shared token alone no longer registers when enrollment is on
    assert eclient.post("/nodes/register", json={"owner": "op", "answer_model": "m"},
                        headers=ADMIN).status_code == 403
    tok = eclient.post("/admin/enroll", json={"kind": "node", "owner": "op"},
                       headers=ADMIN).json()["enroll_token"]
    ok = eclient.post("/nodes/register", json={"owner": "op", "answer_model": "m"},
                      headers={"X-Enroll-Token": tok})
    assert ok.status_code == 200 and ok.json().get("node_id")
    # single-use token can't be reused
    again = eclient.post("/nodes/register", json={"owner": "op", "answer_model": "m"},
                         headers={"X-Enroll-Token": tok})
    assert again.status_code == 403


def test_enrolled_node_works_with_node_secret_alone(eclient):
    # D42 fix (found by dogfooding `pw join` on the VPS): an operator who enrolled via a token does
    # NOT have the shared admin PW_TOKEN. Its per-node secret must be sufficient for the worker loop —
    # otherwise heartbeat/poll/result all 401 and every job the node touches fails.
    tok = eclient.post("/admin/enroll", json={"kind": "node", "owner": "op"},
                       headers=ADMIN).json()["enroll_token"]
    reg = eclient.post("/nodes/register", json={"owner": "op", "answer_model": "m"},
                       headers={"X-Enroll-Token": tok}).json()
    secret = reg["node_secret"]
    nodeonly = {"X-Node-Secret": secret}                    # NO X-PW-Token — the operator never has it
    assert eclient.post("/nodes/heartbeat", json={"load": 0.1}, headers=nodeonly).status_code == 200
    assert eclient.get("/tasks/next", headers=nodeonly).status_code in (200, 204)
    assert eclient.get("/tasks/offers", headers=nodeonly).status_code == 200
    # a bad/absent secret is still rejected (auth didn't get weaker)
    assert eclient.post("/nodes/heartbeat", json={"load": 0.1},
                        headers={"X-Node-Secret": "nope"}).status_code == 401
    assert eclient.post("/nodes/heartbeat", json={"load": 0.1}).status_code == 401


def test_user_signup_open_but_grant_is_token_gated(eclient):
    no_tok = eclient.post("/users", json={"handle": "alice"}).json()
    assert no_tok["balance"] == 0                          # signs up, but ZERO free credit
    tok = eclient.post("/admin/enroll", json={"kind": "user", "grant": 75},
                       headers=ADMIN).json()["enroll_token"]
    granted = eclient.post("/users", json={"handle": "bob"},
                           headers={"X-Enroll-Token": tok}).json()
    assert granted["balance"] == 75                        # granted via the token


def test_sybil_signup_mints_no_credit_when_enrolled(eclient):
    for i in range(15):                                    # under the 20/min users rate limit
        assert eclient.post("/users", json={"handle": f"s{i}"}).json()["balance"] == 0


# ----------------------------------------------------------------- backward compat (PW_ENROLL off)
def test_enroll_off_keeps_open_grants(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "off.db"))
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    monkeypatch.delenv("PW_ENROLL", raising=False)        # default off
    _reload()
    import council.net.coordinator_app as capp
    from fastapi.testclient import TestClient
    c = TestClient(capp.app)
    assert c.post("/users", json={"handle": "alice"}).json()["balance"] == 100   # open grant
    assert c.post("/nodes/register", json={"owner": "op", "answer_model": "m"},
                  headers={"X-PW-Token": "tok"}).status_code == 200               # shared-token reg
