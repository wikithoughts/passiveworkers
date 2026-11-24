"""PW-4 / D56: an empty or whitespace-only assisted deliverable must not release the pool.

Rejected server-side before `ledger.release`: nothing moves, the offer stays claimed (retry or
reaper refund), and the error is a clear 409 over HTTP. Non-empty deliverables are unchanged.
"""

import pytest

BLANKS = ["", " ", "   \n\t  ", "  ", "​​", " ​﻿\n"]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "a.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    import importlib
    import passiveworkers.ledger as led
    importlib.reload(led)
    import passiveworkers.net.config as cfg
    importlib.reload(cfg)
    import passiveworkers.net.store as st
    importlib.reload(st)
    return st.Store()


def _claimed(store):
    j = store.create_job("alice", "Do a task a human handles", job_type="assisted",
                         context="ctx", requires={})
    tid = store.assisted_offers({"profile": "{}", "answer_model": "", "owner": "bob"})[0]["task_id"]
    assert store.accept_assisted(tid, "bobnode", "bob")["ok"]
    return j, tid


def _snapshot(store):
    return {k: vars(a).copy() for k, a in store.ledger.accounts.items()}


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_deliverable_releases_nothing_and_stays_retryable(store, blank):
    j, tid = _claimed(store)
    pre = _snapshot(store)
    out = store.deliver_assisted(tid, "bobnode", blank)
    assert out["ok"] is False and "empty deliverable" in out["error"]
    assert _snapshot(store) == pre                      # no credit created, lost or moved
    assert store.ledger.accounts["bob"].balance == 100.0
    assert store.ledger.accounts["bob"].jobs_helped == 0
    assert store.job_status(j["job_id"]) == "assisting"  # still claimed, not done
    assert store.ledger.conservation_ok()
    # retry with real content pays exactly once
    assert store.deliver_assisted(tid, "bobnode", "real work")["ok"]
    assert store.ledger.accounts["bob"].balance == 150.0
    assert not store.deliver_assisted(tid, "bobnode", "real work")["ok"]
    assert store.ledger.accounts["bob"].balance == 150.0
    assert store.ledger.accounts["alice"].balance == 50.0
    assert store.ledger.conservation_ok()


def test_blank_then_expiry_refunds_asker_once(store, monkeypatch):
    import passiveworkers.net._store_jobs as sj
    j, tid = _claimed(store)
    assert not store.deliver_assisted(tid, "bobnode", "  ")["ok"]
    monkeypatch.setattr(sj, "_now", lambda: 10**12)
    store._reap_once()
    assert store.job_status(j["job_id"]) == "failed"
    assert store.ledger.accounts["alice"].balance == 100.0
    assert store.ledger.accounts["bob"].balance == 100.0
    assert store.ledger.conservation_ok()


def test_blank_check_does_not_mask_authorization_errors(store):
    _, tid = _claimed(store)
    assert store.deliver_assisted(tid, "evilnode", "")["error"] == "not your task"
    assert store.deliver_assisted("nope", "bobnode", "")["error"] == "no such assisted task"


def test_blank_after_delivery_still_reports_already_delivered(store):
    _, tid = _claimed(store)
    assert store.deliver_assisted(tid, "bobnode", "x")["ok"]       # one char is fine (unchanged)
    assert store.deliver_assisted(tid, "bobnode", "")["error"] == "already delivered"
    assert store.ledger.accounts["bob"].balance == 150.0


def test_nonblank_with_surrounding_whitespace_is_delivered_verbatim(store):
    j, tid = _claimed(store)
    assert store.deliver_assisted(tid, "bobnode", "  hi \n")["ok"]
    row = store.conn.execute("SELECT merged FROM jobs WHERE job_id=?", (j["job_id"],)).fetchone()
    assert row["merged"] == "  hi \n"


def test_http_blank_deliver_is_409_then_real_deliver_pays(coord_client):
    h = {"X-PW-Token": "tok"}
    user = coord_client.post("/users", json={"handle": "alice"}).json()["user_secret"]
    job = coord_client.post("/jobs", json={"question": "t", "type": "assisted"},
                            headers={"X-User-Secret": user}).json()
    assert job["status"] == "pending_assist"
    node = coord_client.post("/nodes/register", headers=h, json={"owner": "bob"}).json()
    nh = {**h, "X-Node-Secret": node["node_secret"]}
    tid = coord_client.get("/tasks/offers", headers=nh).json()["offers"][0]["task_id"]
    assert coord_client.post(f"/tasks/{tid}/accept", headers=nh).status_code == 200
    r = coord_client.post(f"/tasks/{tid}/deliver", headers=nh, json={"deliverable": " \n\t"})
    assert r.status_code == 409 and "empty deliverable" in r.json()["detail"]
    r = coord_client.post(f"/tasks/{tid}/deliver", headers=nh, json={"deliverable": ""})
    assert r.status_code == 409
    r = coord_client.post(f"/tasks/{tid}/deliver", headers=nh, json={"deliverable": "ok"})
    assert r.status_code == 200


def test_zero_chunk_file_artifact_is_empty_and_pays_nothing(store):
    from passiveworkers import artifacts as A
    j, tid = _claimed(store)
    pre = _snapshot(store)
    for size in (0, 999_999):
        m = {"name": "x", "size": size, "chunk_size": 262144, "chunks": [],
             "root": A.manifest_root([])}
        out = store.deliver_assisted(tid, "bobnode", A.wrap_artifact(m))
        assert out["ok"] is False and "empty deliverable" in out["error"]
    assert _snapshot(store) == pre and store.job_status(j["job_id"]) == "assisting"
    assert store.ledger.conservation_ok()
