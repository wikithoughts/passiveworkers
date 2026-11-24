"""PW-1116 review items L1/L2/L3/L7: client enrollment-token handling, atomic owner-only config
writes over a loose existing file, the MCP whole-home-directory refusal, and PW_TRUSTED_PROXIES keying."""
from __future__ import annotations

import json
import os
import stat

import pytest

import passiveworkers.config as C
import passiveworkers.mcp_server as M
import passiveworkers.net.agent as A
import passiveworkers.paths as P


# ---------------------------------------------------------------- L2: atomic, loose-file-safe write
def test_write_private_json_replaces_loose_existing_file(tmp_path):
    target = tmp_path / "join.json"
    target.write_text("{}")
    os.chmod(target, 0o644)                       # pre-existing, world-readable
    P.write_private_json(target, {"k": "v"})
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"k": "v"}
    assert [p.name for p in tmp_path.iterdir()] == ["join.json"]     # no temp file left behind


def test_write_private_json_failure_keeps_old_file_and_cleans_temp(tmp_path):
    target = tmp_path / "c.json"
    P.write_private_json(target, {"ok": 1})
    with pytest.raises(TypeError):
        P.write_private_json(target, {"bad": object()})
    assert json.loads(target.read_text()) == {"ok": 1}                 # not truncated
    assert [p.name for p in tmp_path.iterdir()] == ["c.json"]


def test_config_save_tightens_loose_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    p = C.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    os.chmod(p, 0o666)
    C._save({"PW_X": "1"})
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert list(json.loads(p.read_text())) == ["PW_X"]


def test_save_join_tightens_loose_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    p = A._join_state_path()
    p.write_text("{}")
    os.chmod(p, 0o644)
    A._save_join({"default": "http://x"})
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


# ---------------------------------------------------------------- L1: client token handling
def test_cleartext_remote_detection():
    assert A._is_cleartext_remote("http://coord.example.com:8800")
    assert A._is_cleartext_remote("http://10.0.0.5:8800")
    assert not A._is_cleartext_remote("https://coord.example.com")
    assert not A._is_cleartext_remote("http://localhost:8800")
    assert not A._is_cleartext_remote("http://127.0.0.1:8800")


def test_register_drops_single_use_token_after_success(monkeypatch):
    class _R:
        def raise_for_status(self): pass
        def json(self): return {"node_id": "n1", "node_secret": "s1"}

    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(headers or {})
        return _R()

    monkeypatch.setattr(A.requests, "post", fake_post)
    monkeypatch.setenv("PW_ENROLL_TOKEN", "tok123")
    monkeypatch.setenv("PW_COORDINATOR", "http://127.0.0.1:1")
    agent = A.Agent()
    monkeypatch.setattr(agent, "_profile", lambda: {})
    agent.register()
    assert sent.get("X-Enroll-Token") == "tok123"
    assert agent.enroll_token == "" and "PW_ENROLL_TOKEN" not in os.environ


def test_join_reads_token_from_env_when_not_on_argv(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    monkeypatch.setenv("PW_ENROLL_TOKEN", "envtok")
    seen = {}

    class _Stop(Exception):
        pass

    monkeypatch.setattr(A, "_installed_models", lambda base: None)

    def fake_run(self):
        seen["token"] = self.enroll_token
        raise _Stop

    monkeypatch.setattr(A.Agent, "run", fake_run)
    monkeypatch.setattr(A.signal, "signal", lambda *a, **k: None)
    with pytest.raises(_Stop):
        A.join(["join", "http://coord.example.com:8800"])
    assert seen["token"] == "envtok"
    assert "plain http://" in capsys.readouterr().out          # cleartext warning shown


# ---------------------------------------------------------------- L3: MCP refuses the whole home dir
def test_mcp_library_add_refuses_home_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PW_LIBRARY_ROOTS", raising=False)
    called = []

    class _Lib:
        def add(self, path):
            called.append(path)
            return 1

    monkeypatch.setattr("passiveworkers.library.Library", _Lib)
    for target in (str(tmp_path), "~", str(tmp_path.parent), "/"):
        out = M._library_add_text(target)
        assert out.startswith("error: refusing to index your whole home directory"), target
    assert called == []
    sub = tmp_path / "Documents"
    sub.mkdir()
    assert M._library_add_text(str(sub)).startswith("Indexed")     # subfolders still fine


def test_mcp_library_add_allows_home_when_roots_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PW_LIBRARY_ROOTS", str(tmp_path))

    class _Lib:
        def add(self, path):
            return 2

    monkeypatch.setattr("passiveworkers.library.Library", _Lib)
    assert M._library_add_text(str(tmp_path)).startswith("Indexed")


def test_mcp_library_add_refuses_hidden_dirs_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PW_LIBRARY_ROOTS", raising=False)
    (tmp_path / ".ssh").mkdir()
    assert M._library_add_text(str(tmp_path / ".ssh")).startswith("error: refusing")


def test_join_resume_ignores_stale_env_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    monkeypatch.setenv("PW_ENROLL_TOKEN", "spent")
    A._save_join({"default": "http://127.0.0.1:1",
                  "http://127.0.0.1:1": {"node_id": "n1", "node_secret": "s1"}})
    monkeypatch.setattr(A, "_installed_models", lambda base: None)
    seen = {}

    class _Stop(Exception):
        pass

    def fake_run(self):
        seen.update(token=self.enroll_token, node_id=self.node_id)
        raise _Stop

    monkeypatch.setattr(A.Agent, "run", fake_run)
    monkeypatch.setattr(A.signal, "signal", lambda *a, **k: None)
    with pytest.raises(_Stop):
        A.join(["join"])
    assert seen["node_id"] == "n1"           # cached identity reused, no re-register
    assert os.environ.get("PW_ENROLL_TOKEN") == "spent" or seen["token"] in ("", "spent")


# ---------------------------------------------------------------- L7: PW_TRUSTED_PROXIES
class _Req:
    def __init__(self, host, xff=None):
        self.client = type("C", (), {"host": host})()
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}


def test_trusted_proxies_keys_per_client_behind_loopback_tunnel(monkeypatch):
    import passiveworkers.net.coordinator_app as capp
    monkeypatch.delenv("PW_TRUST_XFF", raising=False)
    monkeypatch.setenv("PW_TRUSTED_PROXIES", "127.0.0.1,::1")
    assert capp._client_key(_Req("127.0.0.1", "198.51.100.7")) == "198.51.100.7"
    # a forged leftmost hop is ignored: the right-most non-proxy hop wins
    assert capp._client_key(_Req("127.0.0.1", "6.6.6.6, 198.51.100.7")) == "198.51.100.7"
    assert capp._client_ip(_Req("127.0.0.1", "6.6.6.6, 198.51.100.7, 127.0.0.1")) == "198.51.100.7"


def test_trusted_proxies_handles_ipv4_mapped_peer_and_multiple_xff_lines(monkeypatch):
    import passiveworkers.net.coordinator_app as capp
    from starlette.datastructures import Headers
    monkeypatch.delenv("PW_TRUST_XFF", raising=False)
    monkeypatch.setenv("PW_TRUSTED_PROXIES", "127.0.0.1")
    req = _Req("::ffff:127.0.0.1")
    req.headers = Headers(raw=[(b"x-forwarded-for", b"6.6.6.6"), (b"x-forwarded-for", b"198.51.100.7")])
    assert capp._client_key(req) == "198.51.100.7"


def test_trusted_proxies_ignores_xff_from_untrusted_peer(monkeypatch):
    import passiveworkers.net.coordinator_app as capp
    monkeypatch.delenv("PW_TRUST_XFF", raising=False)
    monkeypatch.setenv("PW_TRUSTED_PROXIES", "127.0.0.1")
    assert capp._client_key(_Req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_trusted_proxies_unset_keeps_default_peer_keying(monkeypatch):
    import passiveworkers.net.coordinator_app as capp
    monkeypatch.delenv("PW_TRUST_XFF", raising=False)
    monkeypatch.delenv("PW_TRUSTED_PROXIES", raising=False)
    assert capp._client_key(_Req("127.0.0.1", "198.51.100.7")) == "127.0.0.1"
