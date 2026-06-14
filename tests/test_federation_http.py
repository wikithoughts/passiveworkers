"""D32 federation HTTP contracts via FastAPI TestClient — the coordinator endpoints were
previously untested end-to-end. Covers auth gates, the per-job progress field, and the new
mid-flight progress endpoint."""
import importlib
import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "http.db"))
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setenv("PW_STARTER_CREDITS", "100000")
    for m in ("council.ledger", "council.net.config", "council.net.store",
              "council.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))
    import council.net.coordinator_app as capp
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


# ----------------------------------------------------------------- auth contracts
def test_jobs_requires_user_auth(client):
    assert client.post("/jobs", json={"question": "hi"}).status_code == 401


def test_register_requires_operator_token(client):
    assert client.post("/nodes/register", json={"owner": "o", "answer_model": "m"}).status_code == 401


def test_next_task_requires_node_secret(client):
    assert client.get("/tasks/next", headers=TOK).status_code == 401   # token ok, no node secret


# ----------------------------------------------------------------- progress field on job view
def test_job_view_exposes_progress_fraction(client):
    usec = _user(client)
    _node(client)
    r = client.post("/jobs", json={"question": "echo", "type": "shard_map",
                                   "items": ["a", "b", "c", "d"]},
                    headers={"X-User-Secret": usec})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    view = client.get(f"/jobs/{jid}").json()
    assert "progress" in view and isinstance(view["progress"], (int, float))
    assert view["progress"] == 0.0          # nothing done yet


def test_progress_endpoint_raises_the_fraction(client):
    usec = _user(client)
    node = _node(client)
    nsec = node["node_secret"]
    jid = client.post("/jobs", json={"question": "echo", "type": "shard_map",
                                     "items": ["a", "b", "c", "d"]},
                      headers={"X-User-Secret": usec}).json()["job_id"]
    # the node claims its task, then reports it is halfway through
    task = client.get("/tasks/next", headers={**TOK, "X-Node-Secret": nsec}).json()
    pr = client.post(f"/tasks/{task['task_id']}/progress", json={"done": 2, "total": 4},
                     headers={**TOK, "X-Node-Secret": nsec})
    assert pr.status_code == 200
    assert client.get(f"/jobs/{jid}").json()["progress"] > 0.0


def test_blob_upload_caps_peak_memory(client):
    # D34: an oversize chunk is rejected by a STREAMED cap (413), not buffered whole; a non-claimant
    # is rejected (403) before any body is read; a valid small chunk stores.
    import hashlib
    usec = _user(client)
    node = _node(client)
    nsec = node["node_secret"]
    jid = client.post("/jobs", json={"question": "deliver a file", "type": "assisted"},
                      headers={"X-User-Secret": usec}).json()["job_id"]
    offers = client.get("/tasks/offers", headers={**TOK, "X-Node-Secret": nsec}).json()["offers"]
    tid = next(o["task_id"] for o in offers if o["job_id"] == jid)
    assert client.post(f"/tasks/{tid}/accept", headers={**TOK, "X-Node-Secret": nsec}).status_code == 200
    # valid small chunk → stored
    data = b"hello chunk"
    h = hashlib.sha256(data).hexdigest()
    assert client.post(f"/jobs/{jid}/blobs/{h}", content=data,
                       headers={**TOK, "X-Node-Secret": nsec}).status_code == 200
    # oversize chunk → 413 (the stream is aborted at the cap, not buffered whole)
    big = b"x" * (512 * 1024 + 64)
    r = client.post(f"/jobs/{jid}/blobs/{hashlib.sha256(big).hexdigest()}", content=big,
                    headers={**TOK, "X-Node-Secret": nsec})
    assert r.status_code == 413
    # a non-claimant node is rejected before any body read
    other = _node(client, owner="op2")
    r2 = client.post(f"/jobs/{jid}/blobs/{h}", content=data,
                     headers={**TOK, "X-Node-Secret": other["node_secret"]})
    assert r2.status_code == 403


def test_progress_rejected_for_foreign_node(client):
    """A node cannot report progress on a task it does not own (best-effort: 200 but no effect)."""
    usec = _user(client)
    owner_node = _node(client, owner="op1")
    other = _node(client, owner="op2")
    jid = client.post("/jobs", json={"question": "echo", "type": "shard_map", "items": ["a", "b"]},
                      headers={"X-User-Secret": usec}).json()["job_id"]
    task = client.get("/tasks/next", headers={**TOK, "X-Node-Secret": owner_node["node_secret"]}).json()
    # the OTHER node tries to report progress on op1's task → ignored
    client.post(f"/tasks/{task['task_id']}/progress", json={"done": 2, "total": 2},
                headers={**TOK, "X-Node-Secret": other["node_secret"]})
    assert client.get(f"/jobs/{jid}").json()["progress"] == 0.0   # unchanged
