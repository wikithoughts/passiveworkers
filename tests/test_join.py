"""D42 — one-command `pworkers join`: config+identity persistence to join.json (0o600), env seeding so the
agent's hot-path PW_WEB_BACKEND isn't silently off, the X-Enroll-Token header, and resume-without-token
reusing the cached identity (no re-register). The agent loop itself is monkeypatched away."""
import json
import os
import stat

import pytest

import passiveworkers.net.agent as A


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _FakeRequests:
    """Stand-in for passiveworkers.net.agent.requests: register() POST returns an identity; the profile
    /api/tags GET fails (→ models=[]). Records POST headers so we can assert the enroll token."""
    exceptions = A.requests.exceptions

    def __init__(self, reg_payload):
        self._reg = reg_payload
        self.posts = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json, headers or {}))
        return _Resp(self._reg)

    def get(self, url, timeout=None):
        raise A.requests.exceptions.RequestException("no ollama in tests")


@pytest.fixture()
def joindir(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))   # _join_state_path() reads this each call
    return tmp_path


@pytest.fixture(autouse=True)
def _restore_env():
    keys = ["PW_COORDINATOR", "PW_OWNER", "PW_NAME", "PW_COUNTRY", "PW_ANSWER_MODEL", "PW_LENS",
            "PW_CAN_JUDGE", "PW_JUDGE_MODEL", "PW_WEB_BACKEND", "PW_ENROLL_TOKEN"]
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_join_first_persists_config_identity_and_seeds_env(joindir, monkeypatch):
    monkeypatch.setattr(A, "requests", _FakeRequests({"node_id": "nid-123", "node_secret": "sec-xyz"}))
    monkeypatch.setattr(A.Agent, "run", lambda self: self.register())   # no infinite loop

    rc = A.join(["join", "https://hub/", "tok-1",
                 "--owner", "alice", "--country", "DE", "--model", "qwen3:14b", "--web", "ddgs"])
    assert rc == 0

    p = joindir / "join.json"
    assert p.exists()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600          # bearer secret → owner-only
    s = json.loads(p.read_text())
    assert s["default"] == "https://hub"
    e = s["https://hub"]
    assert e["owner"] == "alice" and e["country"] == "DE" and e["answer_model"] == "qwen3:14b"
    assert e["node_id"] == "nid-123" and e["node_secret"] == "sec-xyz"
    assert "enroll_token" not in e                          # the single-use token is never persisted
    # env seeded for the agent's hot paths
    assert os.environ["PW_COORDINATOR"] == "https://hub"
    assert os.environ["PW_WEB_BACKEND"] == "ddgs"
    assert os.environ["PW_OWNER"] == "alice"


def test_join_defaults_to_judge_on(joindir, monkeypatch):
    # a lone operator must judge by default, else jobs fail "no judge node online" (VPS dogfood)
    monkeypatch.setattr(A, "requests", _FakeRequests({"node_id": "n", "node_secret": "s"}))
    monkeypatch.setattr(A.Agent, "run", lambda self: None)
    A.join(["join", "https://hub", "tok", "--model", "qwen3:4b"])
    e = json.loads((joindir / "join.json").read_text())["https://hub"]
    assert e["can_judge"] is True and e["judge_model"] == "qwen3:4b"   # reuses the answer model
    assert os.environ["PW_CAN_JUDGE"] == "1"


def test_join_no_judge_opts_out(joindir, monkeypatch):
    monkeypatch.setattr(A, "requests", _FakeRequests({"node_id": "n", "node_secret": "s"}))
    monkeypatch.setattr(A.Agent, "run", lambda self: None)
    A.join(["join", "https://hub", "tok", "--no-judge"])
    e = json.loads((joindir / "join.json").read_text())["https://hub"]
    assert e["can_judge"] is False and e["judge_model"] == ""
    assert os.environ["PW_CAN_JUDGE"] == "0"


def test_join_sends_enroll_token_header(joindir, monkeypatch):
    fake = _FakeRequests({"node_id": "nid", "node_secret": "sec"})
    monkeypatch.setattr(A, "requests", fake)
    monkeypatch.setattr(A.Agent, "run", lambda self: self.register())

    A.join(["join", "https://hub", "tok-9"])
    reg = [p for p in fake.posts if p[0].endswith("/nodes/register")][-1]
    assert reg[2].get("X-Enroll-Token") == "tok-9"


def test_join_resume_reuses_identity_without_register(joindir, monkeypatch):
    (joindir / "join.json").write_text(json.dumps({
        "default": "https://hub",
        "https://hub": {"owner": "alice", "name": "n", "country": "DE", "answer_model": "m",
                        "lens": "neutral", "can_judge": False, "judge_model": "", "web_backend": "ddgs",
                        "node_id": "old-nid", "node_secret": "old-sec"},
    }))
    seen = {}

    def fake_run(self):
        seen["node_id"] = self.node_id
        seen["node_secret"] = self.node_secret
    monkeypatch.setattr(A.Agent, "run", fake_run)
    # register() would raise if called (no token persisted), proving resume must NOT register:
    monkeypatch.setattr(A.Agent, "register",
                        lambda self: (_ for _ in ()).throw(AssertionError("must not re-register")))
    # The R10 model preflight calls _installed_models() -> requests.get() unconditionally, even on
    # a bare resume. Without mocking this, the test's pass/fail depended on whether THIS machine's
    # real local Ollama happened to be reachable at the moment pytest ran (flaky, found in review).
    monkeypatch.setattr(A, "requests", _FakeRequests({"node_id": "old-nid", "node_secret": "old-sec"}))

    rc = A.join(["work"])                                   # bare resume, no url/token
    assert rc == 0
    assert seen == {"node_id": "old-nid", "node_secret": "old-sec"}
    assert os.environ["PW_COORDINATOR"] == "https://hub"


def test_join_without_url_or_saved_default_errors(joindir, monkeypatch):
    monkeypatch.setattr(A, "requests", _FakeRequests({"node_id": "n", "node_secret": "s"}))
    with pytest.raises(SystemExit):
        A.join(["join"])                                   # no url given, nothing cached


# ---------------------------------------------------------------- R10: model preflight
class _FakeRequestsWithTags(_FakeRequests):
    """Like _FakeRequests, but /api/tags succeeds with a given model list."""

    def __init__(self, reg_payload, installed_models):
        super().__init__(reg_payload)
        self._models = installed_models

    def get(self, url, timeout=None):
        return _Resp({"models": [{"name": m} for m in self._models]})


def test_join_refuses_when_declared_model_not_pulled(joindir, monkeypatch):
    fake = _FakeRequestsWithTags({"node_id": "n", "node_secret": "s"}, ["other:model"])
    monkeypatch.setattr(A, "requests", fake)
    with pytest.raises(SystemExit) as e:
        A.join(["join", "https://hub", "tok", "--model", "qwen3:14b"])
    assert "qwen3:14b" in str(e.value)
    assert "ollama pull" in str(e.value)
    assert not fake.posts   # register() must never have been reached


def test_join_succeeds_when_declared_model_is_pulled(joindir, monkeypatch):
    fake = _FakeRequestsWithTags({"node_id": "n", "node_secret": "s"}, ["qwen3:14b", "other:model"])
    monkeypatch.setattr(A, "requests", fake)
    monkeypatch.setattr(A.Agent, "run", lambda self: self.register())
    rc = A.join(["join", "https://hub", "tok", "--model", "qwen3:14b"])
    assert rc == 0
    assert fake.posts   # register() did run


def test_join_warns_but_continues_when_ollama_unreachable_for_preflight(joindir, monkeypatch, capsys):
    # _FakeRequests.get() always raises RequestException — the existing "unreachable" behavior;
    # pinned explicitly here (R10 review) rather than only implicitly by every other passing test.
    monkeypatch.setattr(A, "requests", _FakeRequests({"node_id": "n", "node_secret": "s"}))
    monkeypatch.setattr(A.Agent, "run", lambda self: self.register())
    rc = A.join(["join", "https://hub", "tok", "--model", "qwen3:14b"])
    assert rc == 0   # continues rather than hard-refusing on an unrelated outage
    assert "could not reach Ollama" in capsys.readouterr().out


def test_save_join_fallback_path_is_still_owner_only(joindir, monkeypatch):
    # if os.open fails (special filesystems), the fallback must NOT leave the bearer secret
    # world-readable — force the fallback and assert the final file is 0o600.
    real_open = os.open

    def boom_open(path, *a, **k):
        if str(path).endswith("join.json"):
            raise OSError("no mode support here")
        return real_open(path, *a, **k)
    monkeypatch.setattr(A.os, "open", boom_open)
    A._save_join({"default": "https://h", "https://h": {"node_secret": "shh"}})
    p = joindir / "join.json"
    assert p.exists() and stat.S_IMODE(p.stat().st_mode) == 0o600
    assert "shh" in p.read_text()
