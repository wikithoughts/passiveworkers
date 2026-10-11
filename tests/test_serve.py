"""The single-player web desk (passiveworkers/serve.py): report listing/reading, the path-traversal guard
on /report/{name}, progress 404, and job spawn (research mocked — no Ollama)."""
import time

import pytest
from fastapi import HTTPException

import passiveworkers.serve as serve


@pytest.fixture()
def reports(tmp_path, monkeypatch):
    d = tmp_path / "reports"
    d.mkdir()
    monkeypatch.setattr(serve, "REPORTS", d)
    return d


@pytest.fixture()
def client(reports):
    from fastapi.testclient import TestClient
    return TestClient(serve.app, base_url="http://127.0.0.1:8770")   # Host must pass the allow-list


def test_report_guard_basenames_blocks_traversal(reports):
    # a secret OUTSIDE the reports dir; the guard must basename the name and never escape
    (reports.parent / "secret.md").write_text("TOP SECRET")
    (reports / "ok.md").write_text("OK report")
    assert serve.report("ok.md") == "OK report"             # normal read works
    with pytest.raises(HTTPException):                       # ../secret.md → basename 'secret.md' ∉ reports
        serve.report("../secret.md")
    with pytest.raises(HTTPException):                       # absolute path → basenamed too
        serve.report("/etc/passwd")
    with pytest.raises(HTTPException):                       # non-.md rejected
        serve.report("ok.txt")
    with pytest.raises(HTTPException):                       # missing
        serve.report("nope.md")


def test_reports_lists_md_files(client, reports):
    (reports / "2026-06-14-a.md").write_text("a")
    (reports / "data.txt").write_text("x")                  # not listed
    listed = client.get("/reports").json()
    assert "2026-06-14-a.md" in listed and "data.txt" not in listed


def test_progress_unknown_job_is_404(client):
    assert client.get("/progress/deadbeef00").status_code == 404


def test_research_spawns_job_and_reports_done(client, reports, monkeypatch):
    out = reports / "done.md"
    out.write_text("the result")
    monkeypatch.setattr(serve, "run_research", lambda *a, **k: out)   # no real Ollama
    jid = client.post("/research", json={"brief": "hello", "depth": "quick",
                                         "analysts": 1}).json()["job_id"]
    for _ in range(100):                                     # the worker runs in a daemon thread
        p = client.get(f"/progress/{jid}").json()
        if p["done"]:
            break
        time.sleep(0.02)
    assert p["done"] and p["file"] == "done.md" and p["error"] is None


def _drain(client, jid):
    for _ in range(100):
        p = client.get(f"/progress/{jid}").json()
        if p["done"]:
            return p
        time.sleep(0.02)
    return p


def test_research_records_generic_error_and_marks_done(client, reports, monkeypatch):
    monkeypatch.setattr(serve, "run_research",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    jid = client.post("/research", json={"brief": "x", "depth": "quick", "analysts": 1}).json()["job_id"]
    p = _drain(client, jid)
    assert p["done"] and p["file"] is None and "RuntimeError" in p["error"]


def test_research_systemexit_does_not_hang_the_job(client, reports, monkeypatch):
    # run_research raises SystemExit on first-run failures (Ollama down). SystemExit is a
    # BaseException — if _work caught only Exception the job would hang at done=False forever.
    def no_ollama(*a, **k):
        raise SystemExit("Can't reach Ollama — start it with `ollama serve`.")
    monkeypatch.setattr(serve, "run_research", no_ollama)
    jid = client.post("/research", json={"brief": "x", "depth": "quick", "analysts": 1}).json()["job_id"]
    p = _drain(client, jid)
    assert p["done"] and "ollama serve" in p["error"].lower()


def test_research_concurrency_cap_returns_429(client):
    # drain every free slot, then a new research must be rejected (not spawn an Ollama-thrashing run)
    got = []
    while serve._sem.acquire(blocking=False):
        got.append(1)
    try:
        assert client.post("/research", json={"brief": "hi"}).status_code == 429
    finally:
        for _ in got:
            serve._sem.release()


def test_cancel_sets_flag_and_unknown_is_404(client):
    serve._jobs["job-xyz"] = {"log": [], "done": False, "file": None, "error": None, "cancel": False}
    try:
        assert client.post("/cancel/job-xyz").status_code == 200
        assert serve._jobs["job-xyz"]["cancel"] is True
        assert client.post("/cancel/nope-nope").status_code == 404
    finally:
        serve._jobs.pop("job-xyz", None)


def test_research_accepts_scope_and_rejects_bad(client, reports, monkeypatch):
    monkeypatch.setattr(serve, "run_research",
                        lambda *a, **k: (_ for _ in ()).throw(SystemExit("no ollama")))
    r = client.post("/research", json={"brief": "x", "scope": "local"})   # library-only research
    assert r.status_code == 200 and "job_id" in r.json()
    assert client.post("/research", json={"brief": "x", "scope": "bogus"}).status_code == 422


def test_cancelled_run_reports_cancelled(client, reports, monkeypatch):
    from passiveworkers.local import Cancelled
    monkeypatch.setattr(serve, "run_research",
                        lambda *a, **k: (_ for _ in ()).throw(Cancelled()))
    jid = client.post("/research", json={"brief": "x"}).json()["job_id"]
    p = _drain(client, jid)
    assert p["done"] and p["error"] == "cancelled"


# --- DNS-rebinding defence (F25): only loopback names + PW_SERVE_HOST may reach the desk ---

@pytest.mark.parametrize("host", ["evil.example", "evil.example:8770", "127.0.0.1.evil.example",
                                  "localhost.evil.example:8770", "0.0.0.0:8770", "", "[::1"])
def test_foreign_host_header_is_403(client, host, monkeypatch):
    monkeypatch.delenv("PW_SERVE_HOST", raising=False)
    called = []
    monkeypatch.setattr(serve, "run_research", lambda *a, **k: called.append(1))
    hdr = {"host": host}
    assert client.get("/", headers=hdr).status_code == 403
    assert client.get("/reports", headers=hdr).status_code == 403
    assert client.get("/report/x.md", headers=hdr).status_code == 403
    assert client.post("/research", json={"brief": "x"}, headers=hdr).status_code == 403
    assert not called                                         # no run was started


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8770", "localhost:8770", "LOCALHOST",
                                  "[::1]:8770", "[::1]"])
def test_loopback_host_headers_pass(client, host, monkeypatch):
    monkeypatch.delenv("PW_SERVE_HOST", raising=False)
    assert client.get("/reports", headers={"host": host}).status_code == 200


def test_configured_bind_host_is_allowed_but_wildcard_is_not(client, monkeypatch):
    monkeypatch.setenv("PW_SERVE_HOST", "192.168.1.5")
    assert client.get("/reports", headers={"host": "192.168.1.5:8770"}).status_code == 200
    assert client.get("/reports", headers={"host": "192.168.1.6:8770"}).status_code == 403
    monkeypatch.setenv("PW_SERVE_HOST", "0.0.0.0")             # Docker bind: loopback names only
    assert client.get("/reports", headers={"host": "0.0.0.0:8770"}).status_code == 403
    assert client.get("/reports", headers={"host": "localhost:8770"}).status_code == 200


# Host is 127.0.0.1:8770 (the client's base_url), so only that exact origin is same-origin.
@pytest.mark.parametrize("origin, ok", [("http://127.0.0.1:8770", True), ("http://localhost:8770", False),
                                        ("http://evil.example", False), ("null", False),
                                        ("http://127.0.0.1.evil.example", False)])
def test_cross_origin_requests_are_403(client, reports, monkeypatch, origin, ok):
    monkeypatch.delenv("PW_SERVE_HOST", raising=False)
    monkeypatch.setattr(serve, "run_research",
                        lambda *a, **k: (_ for _ in ()).throw(SystemExit("no ollama")))
    r = client.post("/research", json={"brief": "x"}, headers={"origin": origin})
    assert (r.status_code == 200) is ok and (r.status_code == 403) is (not ok)


# --- review of #39: Origin must match scheme+host+port; strict Host; operator override ---

@pytest.mark.parametrize("origin", ["http://127.0.0.1:3000", "http://localhost:3000", "http://127.0.0.1",
                                    "https://127.0.0.1:8770", "http://127.0.0.1:8770/", "http://evil@127.0.0.1:8770",
                                    "ftp://127.0.0.1:8770", "127.0.0.1:8770"])
def test_other_local_origin_or_scheme_is_403(client, monkeypatch, origin):
    # another app on loopback (a dev server on :3000) must not be able to POST to the desk
    monkeypatch.delenv("PW_SERVE_HOST", raising=False)
    called = []
    monkeypatch.setattr(serve, "run_research", lambda *a, **k: called.append(1))
    hdr = {"host": "127.0.0.1:8770", "origin": origin}
    assert client.post("/research", json={"brief": "x"}, headers=hdr).status_code == 403
    assert client.post("/cancel/abc", headers=hdr).status_code == 403
    assert not called


@pytest.mark.parametrize("host, origin", [("127.0.0.1:8770", "http://127.0.0.1:8770"),
                                          ("LOCALHOST:8770", "http://localhost:8770"),
                                          ("[::1]:8770", "http://[::1]:8770"),
                                          ("localhost", "http://localhost:80"),
                                          ("localhost:9000", "http://localhost:9000")])   # docker -p 9000:8770
def test_same_origin_post_passes(client, reports, monkeypatch, host, origin):
    monkeypatch.delenv("PW_SERVE_HOST", raising=False)
    monkeypatch.setattr(serve, "run_research",
                        lambda *a, **k: (_ for _ in ()).throw(SystemExit("no ollama")))
    r = client.post("/research", json={"brief": "x"}, headers={"host": host, "origin": origin})
    assert r.status_code == 200


@pytest.mark.parametrize("host", ["evil@localhost", "evil@localhost:8770", "user:pw@127.0.0.1:8770",
                                  "localhost:8770/x", "localhost :8770", "localhost:", "localhost:80:80"])
def test_host_with_userinfo_or_junk_is_403(client, monkeypatch, host):
    monkeypatch.delenv("PW_SERVE_HOST", raising=False)
    assert client.get("/reports", headers={"host": host}).status_code == 403


def test_absent_host_header_is_403(monkeypatch):
    import asyncio
    monkeypatch.delenv("PW_SERVE_HOST", raising=False)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": "/reports", "raw_path": b"/reports", "query_string": b"",
             "root_path": "", "headers": [], "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8770)}
    asyncio.run(serve.app(scope, receive, send))
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 403


def test_allowed_hosts_override_admits_lan_and_tailscale_names(client, reports, monkeypatch):
    monkeypatch.setenv("PW_SERVE_HOST", "0.0.0.0")
    monkeypatch.setenv("PW_SERVE_ALLOWED_HOSTS", " Desk.Tailnet.ts.net , 192.168.1.5,,[fd7a::1], 0.0.0.0, bad@x")
    for host in ("desk.tailnet.ts.net", "DESK.tailnet.ts.net:8770", "192.168.1.5:8770", "[fd7a::1]:8770",
                 "localhost:8770"):
        assert client.get("/reports", headers={"host": host}).status_code == 200, host
    for host in ("192.168.1.6:8770", "0.0.0.0:8770", "x", "evil.example"):
        assert client.get("/reports", headers={"host": host}).status_code == 403, host
    ok = {"host": "desk.tailnet.ts.net:8770", "origin": "http://desk.tailnet.ts.net:8770"}
    monkeypatch.setattr(serve, "run_research",
                        lambda *a, **k: (_ for _ in ()).throw(SystemExit("no ollama")))
    assert client.post("/research", json={"brief": "x"}, headers=ok).status_code == 200
    cross = {"host": "desk.tailnet.ts.net:8770", "origin": "http://192.168.1.5:8770"}
    assert client.post("/research", json={"brief": "x"}, headers=cross).status_code == 403
    monkeypatch.delenv("PW_SERVE_ALLOWED_HOSTS")                 # default unchanged: loopback only
    assert client.get("/reports", headers={"host": "desk.tailnet.ts.net"}).status_code == 403


def test_history_items_use_data_name_not_inline_onclick():
    # a planted report name must never be spliced into an inline JS handler (HTML-decoded first)
    html = serve.SERVE_HTML
    assert "onclick=\"openReport" not in html
    assert 'data-name="' in html and "dataset.name" in html
