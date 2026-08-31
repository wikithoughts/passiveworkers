"""D48 security & privacy regressions:
  • GET /status no longer leaks the per-account balance sheet or asker handles / job ids.
  • GET /jobs/{id} is a capability URL: the answer is readable by the (unguessable) id, but the
    asker's identity and the credit receipt are returned ONLY to the authenticated asker.
  • A job whose every answer errored is marked failed and the asker is NOT charged.
  • An enrollment token is redeemed atomically with the node INSERT — a failed register rolls the
    single-use token back instead of burning it.
"""
import importlib

import pytest


# --------------------------------------------------------------------- HTTP (TestClient) fixture
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "sec.db"))
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setenv("PW_STARTER_CREDITS", "100000")
    for m in ("passiveworkers.ledger", "passiveworkers.net.config", "passiveworkers.net.store",
              "passiveworkers.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))
    import passiveworkers.net.coordinator_app as capp
    from fastapi.testclient import TestClient
    return TestClient(capp.app)


TOK = {"X-PW-Token": "tok"}


def _user(client, handle="alice"):
    return client.post("/users", json={"handle": handle}).json()["user_secret"]


def _node(client, owner="op", model="m"):
    return client.post("/nodes/register",
                       json={"owner": owner, "answer_model": model,
                             "profile": {"cores": 4, "ram_gb": 16, "models": [model]}},
                       headers=TOK).json()


def test_status_does_not_leak_accounts_or_asker(client):
    usec = _user(client, "alice")
    _node(client, owner="operator-bob")
    client.post("/jobs", json={"question": "hi there"}, headers={"X-User-Secret": usec})
    st = client.get("/status").json()
    # the raw balance sheet is gone entirely
    assert "accounts" not in st
    # the recent-jobs pulse carries no identifying asker handle and no readable job id
    for j in st.get("recent_jobs", []):
        assert "asker" not in j
        assert "job_id" not in j
        assert "status" in j and "type" in j          # the pseudonymous pulse is still present
    # online_nodes stays pseudonymous-by-owner (the leaderboard contract) but never leaks IPs
    for n in st.get("online_nodes", []):
        assert "ip" not in n


def test_job_view_redacts_identity_and_receipt_for_non_asker(client):
    usec = _user(client, "alice")
    _node(client)
    jid = client.post("/jobs", json={"question": "echo please"},
                      headers={"X-User-Secret": usec}).json()["job_id"]

    # anonymous caller (has the id but not the secret): sees the shape, NOT the asker/receipt
    anon = client.get(f"/jobs/{jid}").json()
    assert anon["asker"] is None
    assert anon["receipt"] is None
    assert anon["question"] == "echo please"          # the shareable result itself stays visible

    # a DIFFERENT user's secret is still not the asker → redacted
    other = _user(client, "mallory")
    seen = client.get(f"/jobs/{jid}", headers={"X-User-Secret": other}).json()
    assert seen["asker"] is None

    # the authenticated asker sees their own identity
    mine = client.get(f"/jobs/{jid}", headers={"X-User-Secret": usec}).json()
    assert mine["asker"] == "alice"


# --------------------------------------------------------------------- Store-level fixture
@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "sec_store.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100000")
    import passiveworkers.ledger as led
    importlib.reload(led)
    import passiveworkers.net.config as cfg
    importlib.reload(cfg)
    import passiveworkers.net.store as st
    importlib.reload(st)
    return st.Store()


def _reg(store, owner, judge=True):
    return store.register_node({
        "owner": owner, "name": owner, "country": "X", "lens": "neutral",
        "answer_model": "m", "can_judge": judge, "judge_model": ("m" if judge else ""),
        "profile": {"cores": 4, "ram_gb": 16, "models": ["m"]},
    })


def test_all_errored_job_is_failed_and_asker_not_charged(store):
    _reg(store, "op", judge=True)
    before = store.ledger.accounts["alice"].balance if "alice" in store.ledger.accounts else None
    j = store.create_job("alice", "answer me", job_type="chat", minds=1)
    jid = j["job_id"]
    start_balance = store.ledger.accounts["alice"].balance   # charged only at settle, so unchanged
    # the sole answer comes back errored (worker crashed / returned nothing)
    ans = list(store.conn.execute(
        "SELECT * FROM tasks WHERE job_id=? AND type='answer'", (jid,)))
    for a in ans:
        store.complete_task(a["task_id"], {"text": "", "error": "boom"}, node_id=a["node_id"])
    # settle whatever judge stage exists
    jt = store.conn.execute(
        "SELECT * FROM tasks WHERE job_id=? AND type='judge' LIMIT 1", (jid,)).fetchone()
    if jt:
        store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
    view = store.job_view(jid)
    assert view["status"] == "failed"
    assert "not charged" in (view["error"] or "")
    # the asker's balance is exactly what it was before settlement — no charge for a blank result
    assert store.ledger.accounts["alice"].balance == start_balance
    if before is not None:
        assert store.ledger.accounts["alice"].balance == before


def test_failed_register_does_not_burn_enrollment_token(store, monkeypatch):
    mint = store.mint_enrollment(owner="op", kind="node", grant=10.0, max_uses=1)
    token = mint["enroll_token"]
    body = {"owner": "op", "name": "n", "country": "X", "answer_model": "m",
            "lens": "neutral", "profile": {"cores": 4, "ram_gb": 16, "models": ["m"]}}

    # force the INSERT side to blow up AFTER the token has been redeemed in-transaction
    def boom():
        raise RuntimeError("disk full")
    monkeypatch.setattr(store, "_save_ledger", boom)
    with pytest.raises(RuntimeError):
        store.register_node(dict(body), enroll_token=token, enroll_kind="node")
    monkeypatch.undo()

    # the single-use token was rolled back with the failed insert → still redeemable
    from passiveworkers.net.store import _hash
    row = store.conn.execute("SELECT uses FROM enroll_tokens WHERE token_hash=?",
                             (_hash(token),)).fetchone()
    assert (row["uses"] or 0) == 0
    ok = store.register_node(dict(body), enroll_token=token, enroll_kind="node")
    assert "node_id" in ok and "error" not in ok


# --------------------------------------------------------------------- review-found (D48) fixes
def test_failed_register_reverts_in_memory_ledger(store, monkeypatch):
    # D48 review: a DB failure after open_account must not leave a phantom granted account in memory.
    mint = store.mint_enrollment(owner="ghost", kind="node", grant=100.0, max_uses=1)
    body = {"owner": "ghost", "name": "n", "country": "X", "answer_model": "m",
            "lens": "neutral", "profile": {"cores": 4, "ram_gb": 16, "models": ["m"]}}
    granted_before = store.ledger._granted_total
    monkeypatch.setattr(store, "_save_ledger", lambda: (_ for _ in ()).throw(RuntimeError("io")))
    with pytest.raises(RuntimeError):
        store.register_node(dict(body), enroll_token=mint["enroll_token"], enroll_kind="node")
    monkeypatch.undo()
    assert "ghost" not in store.ledger.accounts          # no phantom account
    assert store.ledger._granted_total == granted_before  # grant total not inflated


def test_signup_token_not_burned_on_handle_taken(store):
    # D48 review: a 'handle taken' collision must not consume the single-use signup token.
    store.register_user("alice")                          # handle now taken (no token)
    mint = store.mint_enrollment(owner="", kind="user", grant=50.0, max_uses=1)
    tok = mint["enroll_token"]
    res = store.register_user("alice", enroll_token=tok, enroll_kind="user")
    assert res.get("error") == "handle taken"
    from passiveworkers.net.store import _hash
    row = store.conn.execute("SELECT uses FROM enroll_tokens WHERE token_hash=?",
                             (_hash(tok),)).fetchone()
    assert (row["uses"] or 0) == 0                        # token NOT burned
    ok = store.register_user("bob", enroll_token=tok, enroll_kind="user")   # still works + grants
    assert "user_secret" in ok and store.ledger.accounts["bob"].balance == 50.0


def test_all_errored_batch_is_failed_and_not_charged(store):
    # D48 review: a sharded job whose every item errored must NOT be charged (bool(results) fooled it).
    _reg(store, "op", judge=True)
    j = store.create_job("alice", "classify each", job_type="shard_map", items=["a", "b"], minds=1)
    jid = j["job_id"]
    start = store.ledger.accounts["alice"].balance
    for a in store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='answer'", (jid,)):
        allerr = {"text": "Processed 0/2 items", "results": [
            {"i": 0, "item": "a", "error": True, "output": "(error: ConnectionError)"},
            {"i": 1, "item": "b", "error": True, "output": "(error: ConnectionError)"}]}
        store.complete_task(a["task_id"], allerr, node_id=a["node_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge' LIMIT 1",
                            (jid,)).fetchone()
    if jt:
        store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
    view = store.job_view(jid)
    assert view["status"] == "failed"
    assert store.ledger.accounts["alice"].balance == start   # not charged


def _capp(client):
    import passiveworkers.net.coordinator_app as capp
    return capp


def test_get_job_redacts_error_parent_child_for_non_asker(client):
    capp = _capp(client)
    usec = _user(client, "alice")
    _node(client)
    jid = client.post("/jobs", json={"question": "q"},
                      headers={"X-User-Secret": usec}).json()["job_id"]
    # simulate a settle-time failure error (embeds the asker handle + balance) + a chain link
    capp.store.conn.execute(
        "UPDATE jobs SET error=?, parent=?, child=? WHERE job_id=?",
        ("settlement failed: alice has 1.0 credits but the job costs 4.0.", "PARENT-UUID",
         "CHILD-UUID", jid))
    capp.store.conn.commit()

    anon = client.get(f"/jobs/{jid}").json()
    assert anon["error"] is None and anon["parent"] is None and anon["child"] is None

    mine = client.get(f"/jobs/{jid}", headers={"X-User-Secret": usec}).json()
    assert "alice has 1.0 credits" in (mine["error"] or "")   # the asker still sees their own
    assert mine["parent"] == "PARENT-UUID" and mine["child"] == "CHILD-UUID"


def test_non_ascii_pw_token_is_401_not_500(client):
    # D48 review: compare_digest raises TypeError on non-ASCII str; _auth must fail closed (401).
    from fastapi import HTTPException
    capp = _capp(client)
    with pytest.raises(HTTPException) as ei:
        capp._auth("café")          # non-ASCII → must be a clean 401, not an unhandled TypeError/500
    assert ei.value.status_code == 401
