"""Review M1: an EMPTY PW_TOKEN must not authenticate the admin routes, and the startup guard must
warn about a weak token on loopback (the documented "loopback behind a tunnel" topology)."""
import dataclasses
import importlib

import pytest

ENROLL = {"owner": "x", "kind": "user", "grant": 1000000, "max_uses": 5}
STRONG = "a-long-enough-admin-token-0123456789"


def _coord_with_token(tmp_path, monkeypatch, token):
    monkeypatch.setenv("PW_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PW_TOKEN", token)
    for m in ("passiveworkers.ledger", "passiveworkers.net.config", "passiveworkers.net.store",
              "passiveworkers.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))
    import passiveworkers.net.coordinator_app as mod
    from fastapi.testclient import TestClient
    return mod, TestClient(mod.app)


def test_empty_pw_token_refuses_admin_enroll(tmp_path, monkeypatch):
    mod, client = _coord_with_token(tmp_path, monkeypatch, "")
    assert mod.CONFIG.token == ""
    assert client.post("/admin/enroll", json=ENROLL).status_code == 401              # no header
    assert client.post("/admin/enroll", json=ENROLL, headers={"X-PW-Token": ""}).status_code == 401
    assert client.post("/admin/enroll", json=ENROLL, headers={"X-PW-Token": "x"}).status_code == 401


def test_a_real_pw_token_still_mints_enrollment(tmp_path, monkeypatch):
    _, client = _coord_with_token(tmp_path, monkeypatch, STRONG)
    assert client.post("/admin/enroll", json=ENROLL).status_code == 401
    r = client.post("/admin/enroll", json=ENROLL, headers={"X-PW-Token": STRONG})
    assert r.status_code == 200 and r.json().get("enroll_token")


@pytest.mark.parametrize("token, needle", [("", "empty"), ("dev-token", "dev-token"),
                                           ("short-token", "shorter than")])
def test_startup_guard_warns_on_weak_token_on_loopback(tmp_path, monkeypatch, capsys, token, needle):
    mod, _ = _coord_with_token(tmp_path, monkeypatch, STRONG)
    monkeypatch.setattr(mod, "CONFIG", dataclasses.replace(mod.CONFIG, host="127.0.0.1", token=token))
    capsys.readouterr()
    mod._startup_guard()   # loopback: warns, does not raise
    err = capsys.readouterr().err
    assert "WARNING" in err and needle in err
    assert token == "" or token not in err.replace(needle, "")   # never echoes the token value


def test_startup_guard_quiet_for_strong_token_and_refuses_public_weak(tmp_path, monkeypatch, capsys):
    mod, _ = _coord_with_token(tmp_path, monkeypatch, STRONG)
    monkeypatch.setattr(mod, "CONFIG", dataclasses.replace(mod.CONFIG, host="127.0.0.1", token=STRONG))
    capsys.readouterr()
    mod._startup_guard()
    assert capsys.readouterr().err == ""
    for weak in ("", "dev-token", "x", "password", "a" * 23):
        monkeypatch.setattr(mod, "CONFIG", dataclasses.replace(mod.CONFIG, host="0.0.0.0", token=weak))
        with pytest.raises(RuntimeError, match="refusing to bind"):
            mod._startup_guard()
