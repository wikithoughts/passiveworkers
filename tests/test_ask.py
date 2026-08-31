"""passiveworkers/net/submit.py — `pworkers ask` (persist=True): persists+prints the minted asker secret so
it survives past process exit (R23 review, F6 finding), and reuses it on later runs. The legacy
`python -m passiveworkers.net.submit` entry point (persist=False, default) must stay byte-for-byte
unchanged — pinned by test_submit.py."""
import json
import stat

import passiveworkers.net.submit as S


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload if payload is not None else {}

    @property
    def ok(self):
        return self.status_code < 400

    @property
    def text(self):
        return str(self._p)

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_requests(post=None, get=None):
    return type("R", (), {"post": staticmethod(post or (lambda *a, **k: _Resp())),
                          "get": staticmethod(get or (lambda *a, **k: _Resp()))})


_DONE = {"status": "done", "asker": "alice", "question": "Q?",
         "answers": [{"owner": "o", "model": "m", "lens": "l", "country": "AE",
                      "score": 9.0, "text": "an answer"}],
         "merged": "THE MERGED ANSWER", "receipt": {}}


def _drive(monkeypatch, tmp_path, post, argv):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    monkeypatch.delenv("PW_USER_SECRET", raising=False)
    monkeypatch.setenv("PW_COORDINATOR", "http://c")
    get = lambda url, **kw: _Resp(200, dict(_DONE, status="done"))
    monkeypatch.setattr(S, "requests", _fake_requests(post, get))
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    monkeypatch.setattr("sys.argv", argv)
    return S.ask_main()


def test_ask_persists_and_prints_secret_on_first_run(tmp_path, monkeypatch, capsys):
    calls = {"users": 0, "jobs": 0}

    def post(url, **kw):
        if url.endswith("/users"):
            calls["users"] += 1
            return _Resp(200, {"user_secret": "SEC-1", "handle": "alice"})
        if url.endswith("/jobs"):
            calls["jobs"] += 1
            return _Resp(200, {"job_id": "j1", "status": "pending", "assigned": ["w1"]})
        raise AssertionError(url)

    rc = _drive(monkeypatch, tmp_path, post, ["pworkers ask", "Q?", "--asker", "alice"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "secret:" in out and "SEC-1" in out

    p = tmp_path / "asker.json"
    assert p.exists()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    state = json.loads(p.read_text())
    assert state["default"] == "http://c"
    assert state["http://c"] == {"handle": "alice", "user_secret": "SEC-1"}
    assert calls["users"] == 1


def test_ask_reuses_persisted_secret_second_run(tmp_path, monkeypatch, capsys):
    calls = {"users": 0, "jobs": 0}

    def post(url, **kw):
        if url.endswith("/users"):
            calls["users"] += 1
            return _Resp(200, {"user_secret": "SEC-1", "handle": "alice"})
        if url.endswith("/jobs"):
            calls["jobs"] += 1
            headers = kw.get("headers") or {}
            assert headers.get("X-User-Secret") == "SEC-1"
            return _Resp(200, {"job_id": "j1", "status": "pending", "assigned": ["w1"]})
        raise AssertionError(url)

    _drive(monkeypatch, tmp_path, post, ["pworkers ask", "Q1?", "--asker", "alice"])
    assert calls["users"] == 1

    _drive(monkeypatch, tmp_path, post, ["pworkers ask", "Q2?", "--asker", "alice"])
    assert calls["users"] == 1     # NOT signed up again — reused the persisted secret
    assert calls["jobs"] == 2


def test_ask_forwards_type_field(tmp_path, monkeypatch):
    seen = {}

    def post(url, **kw):
        if url.endswith("/users"):
            return _Resp(200, {"user_secret": "SEC", "handle": "alice"})
        if url.endswith("/jobs"):
            seen["body"] = kw.get("json")
            return _Resp(200, {"job_id": "j1", "status": "pending", "assigned": []})
        raise AssertionError(url)

    _drive(monkeypatch, tmp_path, post,
          ["pworkers ask", "Q?", "--asker", "alice", "--type", "research_report"])
    assert seen["body"]["type"] == "research_report"


def test_ask_forwards_enroll_token_header(tmp_path, monkeypatch):
    seen = {}

    def post(url, **kw):
        if url.endswith("/users"):
            seen["headers"] = kw.get("headers")
            return _Resp(200, {"user_secret": "SEC", "handle": "alice"})
        if url.endswith("/jobs"):
            return _Resp(200, {"job_id": "j1", "status": "pending", "assigned": []})
        raise AssertionError(url)

    _drive(monkeypatch, tmp_path, post,
          ["pworkers ask", "Q?", "--asker", "alice", "--enroll-token", "tok-9"])
    assert seen["headers"].get("X-Enroll-Token") == "tok-9"


def test_ask_reusing_cached_identity_updates_stale_default(tmp_path, monkeypatch):
    # Workflow-review finding: mint fresh against A (default=A), mint fresh against B
    # (default=B), then reuse the CACHED identity for A again — "default" must move back to A,
    # or a later pworkers fetch/pworkers rate with no PW_COORDINATOR set would target the wrong coordinator.
    import json as _json

    def make_post(secret_suffix):
        def post(url, **kw):
            if url.endswith("/users"):
                return _Resp(200, {"user_secret": f"SEC-{secret_suffix}", "handle": "alice"})
            if url.endswith("/jobs"):
                return _Resp(200, {"job_id": "j", "status": "pending", "assigned": []})
            raise AssertionError(url)
        return post

    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    monkeypatch.delenv("PW_USER_SECRET", raising=False)
    get = lambda url, **kw: _Resp(200, dict(_DONE, status="done"))
    monkeypatch.setattr(S.time, "sleep", lambda s: None)

    monkeypatch.setenv("PW_COORDINATOR", "http://a")
    monkeypatch.setattr(S, "requests", _fake_requests(make_post("A"), get))
    monkeypatch.setattr("sys.argv", ["pworkers ask", "Q?", "--asker", "alice"])
    S.ask_main()

    monkeypatch.setenv("PW_COORDINATOR", "http://b")
    monkeypatch.setattr(S, "requests", _fake_requests(make_post("B"), get))
    S.ask_main()
    state = _json.loads((tmp_path / "asker.json").read_text())
    assert state["default"] == "http://b"

    monkeypatch.setenv("PW_COORDINATOR", "http://a")

    def post_no_resignup(url, **kw):
        if url.endswith("/users"):
            raise AssertionError("must not re-signup — a cached identity exists for http://a")
        if url.endswith("/jobs"):
            return _Resp(200, {"job_id": "j", "status": "pending", "assigned": []})
        raise AssertionError(url)
    monkeypatch.setattr(S, "requests", _fake_requests(post_no_resignup, get))
    S.ask_main()
    state = _json.loads((tmp_path / "asker.json").read_text())
    assert state["default"] == "http://a"   # moved back — a stranded pointer would still say "b"


def test_legacy_module_main_unchanged(monkeypatch, capsys):
    # python -m passiveworkers.net.submit (persist=False, the default) must behave exactly as before —
    # no persistence, no secret printed, no --type/--enroll-token side effects when unset.
    monkeypatch.delenv("PW_USER_SECRET", raising=False)
    monkeypatch.setenv("PW_COORDINATOR", "http://c")
    n = {"users": 0}

    def post(url, **kw):
        if url.endswith("/users"):
            n["users"] += 1
            return _Resp(200, {"user_secret": "SEC", "handle": "alice"})
        if url.endswith("/jobs"):
            assert "type" not in (kw.get("json") or {})
            return _Resp(200, {"job_id": "job12345", "status": "pending", "assigned": ["w1"]})
        raise AssertionError(url)

    get = lambda url, **kw: _Resp(200, dict(_DONE, status="done"))
    monkeypatch.setattr(S, "requests", _fake_requests(post, get))
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    monkeypatch.setattr("sys.argv", ["submit", "Q?", "--asker", "alice"])
    assert S.main() == 0
    out = capsys.readouterr().out
    assert "secret:" not in out   # never printed under the legacy path
