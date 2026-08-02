"""R10/R33 review — GET /nodes/me (a worker's own balance/reputation/jobs-helped) and heartbeat
task-count reporting, via the real FastAPI TestClient (no socket)."""

TOK = {"X-PW-Token": "tok"}


def _node(client, owner="op", model="m"):
    return client.post("/nodes/register",
                       json={"owner": owner, "answer_model": model,
                             "profile": {"cores": 4, "ram_gb": 16, "models": [model]}},
                       headers=TOK).json()


def test_nodes_me_returns_own_balance(coord_client):
    reg = _node(coord_client, owner="alice")
    r = coord_client.get("/nodes/me", headers={"X-Node-Secret": reg["node_secret"]})
    assert r.status_code == 200
    d = r.json()
    assert d["handle"] == "alice"
    assert "balance" in d and "reputation" in d and "helped" in d


def test_nodes_me_requires_valid_secret(coord_client):
    r = coord_client.get("/nodes/me", headers={"X-Node-Secret": "not-a-real-secret"})
    assert r.status_code == 401


def test_nodes_me_requires_a_secret_at_all(coord_client):
    r = coord_client.get("/nodes/me")
    assert r.status_code == 401


def test_heartbeat_persists_and_status_exposes_task_counts(coord_client):
    reg = _node(coord_client, owner="bob")
    node_secret = reg["node_secret"]
    r = coord_client.post("/nodes/heartbeat",
                          json={"load": 0.5, "tasks_ok": 3, "tasks_failed": 1},
                          headers={"X-Node-Secret": node_secret})
    assert r.status_code == 200

    status = coord_client.get("/status").json()
    node = next(n for n in status["online_nodes"] if n["owner"] == "bob")
    assert node["tasks_ok"] == 3
    assert node["tasks_failed"] == 1


def test_heartbeat_without_task_counts_defaults_to_zero(coord_client):
    # backward-compat: an older agent that doesn't send tasks_ok/tasks_failed must not 422
    reg = _node(coord_client, owner="carol")
    r = coord_client.post("/nodes/heartbeat", json={"load": 0.1},
                          headers={"X-Node-Secret": reg["node_secret"]})
    assert r.status_code == 200
    status = coord_client.get("/status").json()
    node = next(n for n in status["online_nodes"] if n["owner"] == "carol")
    assert node["tasks_ok"] == 0 and node["tasks_failed"] == 0
