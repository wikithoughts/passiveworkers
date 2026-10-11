"""Coordinator resource bounds (review F11, F12, F13):
  • F11 — request bodies over PW_MAX_BODY_BYTES get a 413 BEFORE parsing or auth, whether the
    size is declared (Content-Length) or only discovered while streaming (chunked / lying client).
  • F12 — baseline generation runs on at most PW_BASELINE_WORKERS threads, not one per job.
  • F13 — the hot lookups have SQLite indexes, created idempotently on every open.
"""
import asyncio
import importlib
import sqlite3
import threading

import pytest


@pytest.fixture()
def capp(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "limits.db"))
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setenv("PW_STARTER_CREDITS", "100000")
    monkeypatch.setenv("PW_BASELINE_WORKERS", "2")
    for m in ("passiveworkers.ledger", "passiveworkers.net.config", "passiveworkers.net.store",
              "passiveworkers.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))
    import passiveworkers.net.coordinator_app as mod
    return mod


@pytest.fixture()
def client(capp):
    from fastapi.testclient import TestClient
    return TestClient(capp.app)


TOK = {"X-PW-Token": "tok"}


# --------------------------------------------------------------------------------- F11 body cap
def _spy_complete(capp, monkeypatch):
    calls = []
    monkeypatch.setattr(capp.store, "complete_task", lambda *a, **k: calls.append(a) or True)
    return calls


def test_declared_oversize_body_is_413_before_auth_or_parsing(capp, client, monkeypatch):
    calls = _spy_complete(capp, monkeypatch)
    body = b'{"text": "' + b"x" * (4 * 1024 * 1024) + b'"}'      # default cap is 4 MiB
    r = client.post("/tasks/t1/result", content=body,
                    headers={"content-type": "application/json", "X-Node-Secret": "nope"})
    assert r.status_code == 413
    assert r.json()["detail"] == "request body too large"
    assert calls == []


def test_chunked_oversize_body_is_413(capp, client, monkeypatch):
    monkeypatch.setenv("PW_MAX_BODY_BYTES", "4096")
    calls = _spy_complete(capp, monkeypatch)

    def gen():                                   # no Content-Length → Transfer-Encoding: chunked
        yield b'{"text": "'
        yield b"x" * 8192
        yield b'"}'

    r = client.post("/tasks/t1/result", content=gen(),
                    headers={"content-type": "application/json", "X-Node-Secret": "nope"})
    assert "content-length" not in {k.lower() for k in r.request.headers}
    assert r.status_code == 413
    assert calls == []


def test_streamed_body_is_cut_off_at_the_cap(capp, monkeypatch):
    """Drive the ASGI app with a body that arrives in many 64 KiB messages: the read must stop
    at the first message past the cap instead of buffering the whole thing."""
    monkeypatch.setenv("PW_MAX_BODY_BYTES", str(256 * 1024))
    chunk, total = b"x" * 65536, 100
    consumed = 0
    sent = []

    async def receive():
        nonlocal consumed
        if consumed >= total:
            return {"type": "http.disconnect"}
        consumed += 1
        return {"type": "http.request", "body": chunk, "more_body": consumed < total}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/tasks/t1/result", "raw_path": b"/tasks/t1/result",
             "root_path": "", "query_string": b"", "client": ("127.0.0.1", 1), "server": ("t", 80),
             "headers": [(b"content-type", b"application/json"), (b"x-node-secret", b"nope"),
                         (b"transfer-encoding", b"chunked")]}
    asyncio.run(capp.app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413
    assert consumed <= 5                          # 256 KiB cap / 64 KiB chunks → stops on the 5th


def test_bodies_under_the_cap_still_reach_the_endpoint(client, monkeypatch):
    monkeypatch.setenv("PW_MAX_BODY_BYTES", "4096")
    r = client.post("/users", json={"handle": "alice"})
    assert r.status_code == 200 and r.json()["user_secret"]
    r = client.post("/tasks/t1/result", json={"text": "x" * 1000}, headers={"X-Node-Secret": "nope"})
    assert r.status_code == 401                  # normal auth path, not the size cap


@pytest.mark.parametrize("raw", ["0", "-5", "lots"])
def test_bad_cap_setting_falls_back_to_the_default(capp, monkeypatch, raw):
    monkeypatch.setenv("PW_MAX_BODY_BYTES", raw)
    assert capp._max_body_bytes() == 4 * 1024 * 1024


# --------------------------------------------------------------------------- F12 baseline bound
def _new_baseline_threads(before):
    """pw-baseline threads started since `before` (other modules' jobs may leave their own running)."""
    return [t for t in threading.enumerate() if t.name == "pw-baseline" and t not in before]


def _join(threads):
    for t in threads:
        t.join(5)
        assert not t.is_alive()


def test_baseline_threads_are_bounded(capp, client, monkeypatch):
    release = threading.Event()
    lock = threading.Lock()
    running = {"now": 0, "peak": 0, "calls": 0}

    def fake_baseline(job_id, question):
        with lock:
            running["now"] += 1
            running["calls"] += 1
            running["peak"] = max(running["peak"], running["now"])
        release.wait(10)
        with lock:
            running["now"] -= 1

    monkeypatch.setattr(capp, "_baseline_async", fake_baseline)
    before = set(threading.enumerate())
    client.post("/nodes/register", headers=TOK,
                json={"owner": "op", "answer_model": "m", "profile": {"models": ["m"]}})
    usec = client.post("/users", json={"handle": "alice"}).json()["user_secret"]
    for i in range(6):
        r = client.post("/jobs", json={"question": f"q{i}"}, headers={"X-User-Secret": usec})
        assert r.json()["status"] == "pending_answers"
    try:
        live = [t for t in _new_baseline_threads(before) if t.is_alive()]
        assert len(live) <= 2                     # PW_BASELINE_WORKERS=2, not one thread per job
        assert running["calls"] <= 2
    finally:
        release.set()
    _join(_new_baseline_threads(before))
    assert running["peak"] == 2
    # slots are released when a baseline finishes, so the next job gets one again
    client.post("/jobs", json={"question": "after"}, headers={"X-User-Secret": usec})
    _join(_new_baseline_threads(before))
    assert running["calls"] == 3


def test_baseline_failure_releases_its_slot(capp, monkeypatch):
    def boom(job_id, question):
        raise RuntimeError("model down")

    monkeypatch.setattr(capp, "_baseline_async", boom)
    before = set(threading.enumerate())
    for i in range(5):                            # more than the 2 slots, run one after another
        assert capp._start_baseline(f"job{i}", "q") is True
        _join(_new_baseline_threads(before))


# ------------------------------------------------------------------------------ F13 indexes
_EXPECTED = {
    "idx_nodes_secret_hash": ("nodes", ["secret_hash"]),
    "idx_users_secret_hash": ("users", ["secret_hash"]),
    "idx_tasks_job_id": ("tasks", ["job_id"]),
    "idx_tasks_node_status": ("tasks", ["node_id", "status"]),
    "idx_jobs_asker_created": ("jobs", ["asker", "created"]),
    "idx_jobs_status": ("jobs", ["status"]),
}


def _indexes(conn):
    out = {}
    for name, table in conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index' "
                                    "AND name LIKE 'idx_%'"):
        cols = [r[2] for r in conn.execute(f"PRAGMA index_info({name})")]
        out[name] = (table, cols)
    return out


def test_store_creates_the_hot_path_indexes(tmp_path):
    from passiveworkers.net.store import Store
    s = Store(str(tmp_path / "a.db"))
    assert _indexes(s.conn) == _EXPECTED
    plan = " ".join(r[3] for r in s.conn.execute(
        "EXPLAIN QUERY PLAN SELECT node_id FROM nodes WHERE secret_hash=?", ("h",)))
    assert "idx_nodes_secret_hash" in plan
    plan = " ".join(r[3] for r in s.conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM tasks WHERE node_id=? AND status='queued' "
        "ORDER BY created LIMIT 1", ("n",)))
    assert "idx_tasks_node_status" in plan


def test_indexes_are_added_to_an_existing_db_and_reopen_is_idempotent(tmp_path):
    from passiveworkers.net.store import Store
    path = str(tmp_path / "old.db")
    Store(path)
    raw = sqlite3.connect(path)                  # simulate a pre-index database
    for name in _EXPECTED:
        raw.execute(f"DROP INDEX {name}")
    raw.commit()
    assert _indexes(raw) == {}
    raw.close()
    Store(path)                                  # open → indexes created
    s = Store(path)                              # open again → no error, no duplicates
    assert _indexes(s.conn) == _EXPECTED
