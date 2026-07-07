"""council/net/submit.py — the asker CLI client. Its value is the client-side state machine (the 409
signup retry, the poll/timeout loop, receipt printing); the server endpoints it calls are covered in
test_federation_http.py. Scripted `requests` responses drive each branch deterministically."""
import itertools

import council.net.submit as S


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
         "merged": "THE MERGED ANSWER",
         "receipt": {"asker_id": "alice", "total_cost": 15.0, "payouts": {"o": 10.0}, "judge_fee": 5.0}}


def test_signup_409_retry_then_done(monkeypatch, capsys):
    monkeypatch.delenv("PW_USER_SECRET", raising=False)
    monkeypatch.setenv("PW_COORDINATOR", "http://c")
    n = {"users": 0}

    def post(url, **kw):
        if url.endswith("/users"):
            n["users"] += 1
            if n["users"] == 1:
                return _Resp(409, {"detail": "handle taken"})   # first handle collides → retry
            return _Resp(200, {"user_secret": "SEC", "handle": "alice-ab12"})
        if url.endswith("/jobs"):
            return _Resp(200, {"job_id": "job12345", "status": "pending", "assigned": ["w1"]})
        raise AssertionError(url)

    poll = {"n": 0}

    def get(url, **kw):
        poll["n"] += 1
        return _Resp(200, dict(_DONE, status=("running" if poll["n"] < 2 else "done")))

    monkeypatch.setattr(S, "requests", _fake_requests(post, get))
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    monkeypatch.setattr("sys.argv", ["submit", "Q?", "--asker", "alice"])
    assert S.main() == 0
    out = capsys.readouterr().out
    assert n["users"] == 2                                # retried after the 409
    assert "THE MERGED ANSWER" in out


def test_job_failed_at_creation(monkeypatch, capsys):
    monkeypatch.setenv("PW_USER_SECRET", "SEC")           # skip signup
    monkeypatch.setenv("PW_COORDINATOR", "http://c")

    def post(url, **kw):
        return _Resp(200, {"job_id": "x", "status": "failed", "error": "no worker nodes online"})

    monkeypatch.setattr(S, "requests", _fake_requests(post))
    monkeypatch.setattr("sys.argv", ["submit", "Q?"])
    assert S.main() == 1
    assert "no worker nodes online" in capsys.readouterr().out


def test_poll_reaches_failed(monkeypatch, capsys):
    monkeypatch.setenv("PW_USER_SECRET", "SEC")
    monkeypatch.setenv("PW_COORDINATOR", "http://c")
    monkeypatch.setattr(S, "requests", _fake_requests(
        post=lambda url, **kw: _Resp(200, {"job_id": "j", "status": "pending", "assigned": []}),
        get=lambda url, **kw: _Resp(200, {"status": "failed", "error": "all answers failed"})))
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    monkeypatch.setattr("sys.argv", ["submit", "Q?"])
    assert S.main() == 1
    assert "all answers failed" in capsys.readouterr().out


def test_timeout(monkeypatch, capsys):
    monkeypatch.setenv("PW_USER_SECRET", "SEC")
    monkeypatch.setenv("PW_COORDINATOR", "http://c")
    monkeypatch.setattr(S, "requests", _fake_requests(
        post=lambda url, **kw: _Resp(200, {"job_id": "j", "status": "pending", "assigned": []}),
        get=lambda url, **kw: _Resp(200, {"status": "running"})))     # never completes
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    ticks = itertools.chain([0, 0, 1, 2], itertools.repeat(999))       # deadline=0+3; advance past it
    monkeypatch.setattr(S.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr("sys.argv", ["submit", "Q?", "--timeout", "3"])
    assert S.main() == 1
    assert "timed out" in capsys.readouterr().out
