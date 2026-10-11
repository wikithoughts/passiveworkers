"""Coordinator resource bounds (review F11, F12, F13):
  • F11 — request bodies over their route's cap (never above PW_MAX_BODY_BYTES) get a 413 BEFORE
    parsing or auth, whether the size is declared (Content-Length) or only discovered while
    streaming (chunked / lying client).
  • F12 — baseline generation runs on at most PW_BASELINE_WORKERS threads, not one per job; a
    job that finds every slot busy is skipped with an exact /metrics counter and a throttled log
    line (the first skip, then every Nth).
  • F13 — the hot lookups have SQLite indexes, created idempotently on every open.
"""
import asyncio
import importlib
import json
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


# ----------------------------------------------------------------------- F11 per-route caps
def _node_secret(client):
    r = client.post("/nodes/register", headers=TOK,
                    json={"owner": "op", "answer_model": "m", "profile": {"models": ["m"]}})
    return r.json()["node_secret"]


def _padded_json(prefix: bytes, size: int) -> bytes:
    """A valid JSON object `prefix…}` padded with whitespace to exactly `size` bytes."""
    assert len(prefix) + 1 <= size
    return prefix + b" " * (size - len(prefix) - 1) + b"}"


def test_deliver_body_exactly_at_the_cap_passes_and_one_byte_more_is_413(capp, client, monkeypatch):
    """The worst-case legitimate /deliver — 200k astral chars, each JSON-escaped to 12 bytes —
    fits under the route cap; the cap is exact (cap passes, cap+1 is refused before the endpoint)."""
    calls = []
    monkeypatch.setattr(capp.store, "deliver_assisted",
                        lambda task_id, node_id, deliverable, **k:
                        calls.append(len(deliverable)) or {"ok": True})
    hdr = {"content-type": "application/json", "X-Node-Secret": _node_secret(client)}
    prefix = json.dumps({"deliverable": "\U0001F600" * 200_000}).encode()[:-1]   # drop the "}"
    cap = capp._DELIVER_BODY_CAP

    r = client.post("/tasks/t1/deliver", content=_padded_json(prefix, cap), headers=hdr)
    assert r.status_code == 200, r.text
    assert calls == [200_000]

    r = client.post("/tasks/t1/deliver", content=_padded_json(prefix, cap + 1), headers=hdr)
    assert r.status_code == 413
    assert r.json()["detail"] == "request body too large"
    assert calls == [200_000]                    # the endpoint never ran


_SMALL_ROUTES = ["/users", "/nodes/register", "/nodes/heartbeat", "/admin/enroll",
                 "/tasks/t1/progress", "/tasks/t1/accept", "/jobs/j1/feedback", "/jobs/j1/rate"]


@pytest.mark.parametrize("path", _SMALL_ROUTES)
def test_small_body_routes_are_capped_at_64_kib(capp, client, path):
    cap = 64 * 1024
    hdr = {"content-type": "application/json"}
    r = client.post(path, content=_padded_json(b'{"x": 1', cap + 1), headers=hdr)
    assert r.status_code == 413
    r = client.post(path, content=_padded_json(b'{"x": 1', cap), headers=hdr)
    assert r.status_code != 413                  # at the cap: the normal auth/validation path


def test_small_route_cap_also_applies_to_a_chunked_body(client):
    def gen():                                   # no Content-Length → Transfer-Encoding: chunked
        yield b'{"handle": "'
        yield b"x" * (64 * 1024)
        yield b'"}'

    r = client.post("/users", content=gen(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_free_form_routes_keep_the_ceiling(client):
    """/result's dict and /jobs have no documented smaller bound, so a 1 MiB body still reaches
    the endpoint (its auth check / body validation) under the default 4 MiB ceiling."""
    body = _padded_json(b'{"text": "x"', 1024 * 1024)
    hdr = {"content-type": "application/json", "X-Node-Secret": "nope", "X-User-Secret": "nope"}
    assert client.post("/tasks/t1/result", content=body, headers=hdr).status_code == 401
    assert client.post("/jobs", content=body, headers=hdr).status_code == 422   # no "question"


def test_body_cap_for_routes_and_the_ceiling(capp, monkeypatch):
    small, ceiling = 64 * 1024, 4 * 1024 * 1024
    assert capp._body_cap_for("POST", "/tasks/t1/deliver") == capp._DELIVER_BODY_CAP
    assert capp._body_cap_for("POST", "/tasks/t1/result") == ceiling
    assert capp._body_cap_for("POST", "/jobs/j1/blobs/abc") == capp._BLOB_BODY_CAP
    assert capp._BLOB_BODY_CAP == capp._BLOB_CAP + 16 * 1024    # derived from the handler's cap
    assert capp._body_cap_for("POST", "/users") == small
    assert capp._body_cap_for("GET", "/status") == small
    assert capp._body_cap_for("POST", "/tasks/a/b/deliver") == ceiling   # not the deliver route
    monkeypatch.setenv("PW_MAX_BODY_BYTES", "4096")     # an operator's lower ceiling wins everywhere
    assert capp._body_cap_for("POST", "/tasks/t1/deliver") == 4096
    assert capp._body_cap_for("POST", "/users") == 4096


# ----------------------------------------------------------------------- F12 skipped baselines
def test_busy_slots_skip_the_baseline_with_a_log_line_and_a_counter(capp, client, monkeypatch,
                                                                    capsys):
    release = threading.Event()
    monkeypatch.setattr(capp, "_baseline_async", lambda job_id, question: release.wait(10))
    before = set(threading.enumerate())
    assert client.get("/metrics").json()["baseline_skipped_busy"] == 0
    try:
        assert capp._start_baseline("jobaaaa1", "q") is True
        assert capp._start_baseline("jobaaaa2", "q") is True     # PW_BASELINE_WORKERS=2
        assert capp._start_baseline("jobbusy1", "q") is False
        assert capp._start_baseline("jobbusy2", "q") is False
    finally:
        release.set()
    _join(_new_baseline_threads(before))
    out = capsys.readouterr().out
    assert "[baseline] skipped for job jobbusy1: all 2 slots busy" in out
    assert "1 skipped since start" in out
    assert "jobbusy2" not in out                 # throttled: only the first and every Nth are logged
    assert client.get("/metrics").json()["baseline_skipped_busy"] == 2


def test_skip_log_is_throttled_but_the_counter_stays_exact(capp, client, monkeypatch, capsys):
    """Low 5 (#50 review): one line for the first skip and then every Nth, not one per job."""
    monkeypatch.setattr(capp, "_BASELINE_SKIP_LOG_EVERY", 3)
    monkeypatch.setattr(capp, "_baseline_slots", threading.BoundedSemaphore(1))
    assert capp._baseline_slots.acquire(blocking=False)          # every slot busy
    try:
        for i in range(1, 8):
            assert capp._start_baseline(f"busy{i:04d}", "q") is False
    finally:
        capp._baseline_slots.release()
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("[baseline] skipped")]
    assert [ln.split()[4].rstrip(":") for ln in lines] == ["busy0001", "busy0003", "busy0006"]
    assert "6 skipped since start" in lines[-1]
    assert client.get("/metrics").json()["baseline_skipped_busy"] == 7


# ----------------------------------------------- F11 exact caps on every cap class (#50 review)
def test_small_route_body_exactly_at_the_cap_passes_and_one_byte_more_is_413(client):
    cap = 64 * 1024
    hdr = {"content-type": "application/json"}
    r = client.post("/users", content=_padded_json(b'{"handle": "alice"', cap), headers=hdr)
    assert r.status_code == 200 and r.json()["user_secret"]
    r = client.post("/users", content=_padded_json(b'{"handle": "bob"', cap + 1), headers=hdr)
    assert r.status_code == 413
    assert r.json()["detail"] == "request body too large"


def test_result_body_exactly_at_the_ceiling_passes_and_one_byte_more_is_413(capp, client,
                                                                            monkeypatch):
    calls = _spy_complete(capp, monkeypatch)
    hdr = {"content-type": "application/json", "X-Node-Secret": _node_secret(client)}
    ceiling = 4 * 1024 * 1024
    r = client.post("/tasks/t1/result", content=_padded_json(b'{"text": "x"', ceiling), headers=hdr)
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    r = client.post("/tasks/t1/result", content=_padded_json(b'{"text": "x"', ceiling + 1),
                    headers=hdr)
    assert r.status_code == 413
    assert len(calls) == 1                       # the endpoint never ran


def test_jobs_body_exactly_at_the_ceiling_passes_and_one_byte_more_is_413(capp, client,
                                                                          monkeypatch):
    calls = []
    monkeypatch.setattr(capp.store, "create_job",
                        lambda *a, **k: calls.append(a) or {"job_id": "j1", "status": "queued"})
    usec = client.post("/users", json={"handle": "alice"}).json()["user_secret"]
    hdr = {"content-type": "application/json", "X-User-Secret": usec}
    ceiling = 4 * 1024 * 1024
    r = client.post("/jobs", content=_padded_json(b'{"question": "q"', ceiling), headers=hdr)
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    r = client.post("/jobs", content=_padded_json(b'{"question": "q"', ceiling + 1), headers=hdr)
    assert r.status_code == 413
    assert len(calls) == 1


def test_blob_route_cap_is_the_chunk_cap_plus_slack_not_the_ceiling(capp, client, monkeypatch):
    """Medium 1 (#50 review): a blob upload over _BLOB_BODY_CAP is refused before auth, not let
    through to 4 MiB; within it, put_blob's own exact _BLOB_CAP check still decides."""
    stored = []
    node_secret = _node_secret(client)
    node_id = capp.store.node_for_secret(node_secret)
    monkeypatch.setattr(capp.store, "assisted_claimant", lambda job_id: node_id)   # it claimed j1
    monkeypatch.setattr(capp.store, "put_blob",
                        lambda job_id, h, data: stored.append(len(data)) or {"ok": True})
    good = {"content-type": "application/octet-stream", "X-Node-Secret": node_secret}
    bad = {"content-type": "application/octet-stream", "X-Node-Secret": "nope"}
    chunk, route_cap = capp._BLOB_CAP, capp._BLOB_BODY_CAP

    r = client.post("/jobs/j1/blobs/h1", content=b"x" * chunk, headers=good)
    assert r.status_code == 200, r.text
    assert stored == [chunk]
    r = client.post("/jobs/j1/blobs/h1", content=b"x" * (chunk + 1), headers=good)
    assert r.status_code == 413 and r.json()["detail"] == "chunk too large"     # the handler's cap
    r = client.post("/jobs/j1/blobs/h1", content=b"x" * route_cap, headers=bad)
    assert r.status_code == 401                  # within the route cap: reaches auth
    r = client.post("/jobs/j1/blobs/h1", content=b"x" * (route_cap + 1), headers=bad)
    assert r.status_code == 413 and r.json()["detail"] == "request body too large"   # pre-auth
    assert stored == [chunk]


def test_get_with_a_body_over_64_kib_is_413(client):
    cap = 64 * 1024
    assert client.request("GET", "/status", content=b"x" * cap).status_code == 200
    r = client.request("GET", "/status", content=b"x" * (cap + 1))
    assert r.status_code == 413
    assert r.json()["detail"] == "request body too large"


def test_trailing_slash_gets_the_route_cap_not_the_ceiling(capp, client):
    cap = 64 * 1024
    hdr = {"content-type": "application/json"}
    r = client.post("/users/", content=_padded_json(b'{"x": 1', cap + 1), headers=hdr,
                    follow_redirects=False)
    assert r.status_code == 413                  # was a 307 to /users under the 4 MiB ceiling
    r = client.post("/users/", content=_padded_json(b'{"x": 1', cap), headers=hdr,
                    follow_redirects=False)
    assert r.status_code != 413
    assert capp._body_cap_for("POST", "/tasks/t1/deliver/") == capp._DELIVER_BODY_CAP
    assert capp._body_cap_for("POST", "/jobs/j1/blobs/abc/") == capp._BLOB_BODY_CAP


def test_root_path_prefix_gets_the_route_cap(capp):
    """Medium-Low 3 (#50 review): uvicorn --root-path puts the prefix in scope["path"] too; the
    cap must be chosen from the app-relative path, as Starlette's router does."""
    from fastapi.testclient import TestClient
    client = TestClient(capp.app, root_path="/pw")
    cap = 64 * 1024
    hdr = {"content-type": "application/json"}
    r = client.post("/pw/users", content=_padded_json(b'{"handle": "alice"', cap), headers=hdr)
    assert r.status_code == 200, r.text          # routed to /users under the prefix
    r = client.post("/pw/users", content=_padded_json(b'{"handle": "bob"', cap + 1), headers=hdr)
    assert r.status_code == 413
    small, ceiling = cap, 4 * 1024 * 1024
    assert capp._body_cap_for("POST", "/pw/users", "/pw") == small
    assert capp._body_cap_for("POST", "/users", "/pw") == small        # server left path unprefixed
    assert capp._body_cap_for("POST", "/pw", "/pw") == ceiling
    assert capp._body_cap_for("POST", "/pwx/users", "/pw") == ceiling  # not a whole-segment prefix
