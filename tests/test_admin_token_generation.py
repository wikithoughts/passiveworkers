"""PW-3 / D56: no guessable default admin token. An unset (or legacy `dev-token`) PW_TOKEN makes the
coordinator generate a random token on first start, persist it 0600 under PW_HOME, never log it, and
never accept `dev-token`."""
import importlib
import os
import stat

import pytest

ENROLL = {"owner": "x", "kind": "user", "grant": 1000000, "max_uses": 5}


def _boot(tmp_path, monkeypatch, token=None):
    monkeypatch.setenv("PW_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PW_HOME", str(tmp_path / "home"))
    if token is None:
        monkeypatch.delenv("PW_TOKEN", raising=False)
    else:
        monkeypatch.setenv("PW_TOKEN", token)
    for m in ("passiveworkers.ledger", "passiveworkers.net.config", "passiveworkers.net.store",
              "passiveworkers.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))
    import passiveworkers.net.coordinator_app as mod
    from fastapi.testclient import TestClient
    return mod, TestClient(mod.app), tmp_path / "home" / "admin_token"


def test_unset_token_generates_random_persisted_0600_token(tmp_path, monkeypatch, capsys):
    mod, client, path = _boot(tmp_path, monkeypatch)
    tok = mod.CONFIG.token
    assert len(tok) >= 24 and tok != "dev-token"
    assert path.read_text().strip() == tok
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    out = capsys.readouterr()
    assert tok not in out.out + out.err                       # never logged
    assert str(path) in out.err                               # but the message says where to read it
    assert client.post("/admin/enroll", json=ENROLL).status_code == 401
    assert client.post("/admin/enroll", json=ENROLL, headers={"X-PW-Token": "dev-token"}).status_code == 401
    assert client.post("/admin/enroll", json=ENROLL, headers={"X-PW-Token": tok}).status_code == 200


def test_generated_token_is_reused_on_restart_and_unique_per_home(tmp_path, monkeypatch):
    mod1, _, path = _boot(tmp_path, monkeypatch)
    first = mod1.CONFIG.token
    mod2, _, _ = _boot(tmp_path, monkeypatch)
    assert mod2.CONFIG.token == first
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("PW_HOME", str(other))
    import passiveworkers.net.admin_token as at
    assert at.resolve(None)[0] != first


def test_legacy_dev_token_env_is_not_accepted_and_tells_the_operator(tmp_path, monkeypatch, capsys):
    mod, client, path = _boot(tmp_path, monkeypatch, token="dev-token")
    assert mod.CONFIG.token != "dev-token" and path.exists()
    err = capsys.readouterr().err
    assert "dev-token" in err and "no longer accepted" in err and str(path) in err
    assert mod.CONFIG.token not in err
    assert client.post("/admin/enroll", json=ENROLL, headers={"X-PW-Token": "dev-token"}).status_code == 401
    assert client.post("/admin/enroll", json=ENROLL,
                       headers={"X-PW-Token": mod.CONFIG.token}).status_code == 200


def test_explicit_strong_token_wins_and_writes_no_file(tmp_path, monkeypatch):
    strong = "a-long-enough-admin-token-0123456789"
    mod, client, path = _boot(tmp_path, monkeypatch, token=strong)
    assert mod.CONFIG.token == strong and not path.exists()


def test_explicit_empty_token_still_disables_admin(tmp_path, monkeypatch):
    mod, client, path = _boot(tmp_path, monkeypatch, token="")
    assert mod.CONFIG.token == "" and not path.exists()
    assert client.post("/admin/enroll", json=ENROLL, headers={"X-PW-Token": ""}).status_code == 401


@pytest.mark.parametrize("mode", [0o666, 0o664, 0o644, 0o640, 0o604])
def test_loose_permission_file_is_rotated_not_adopted(tmp_path, monkeypatch, mode):
    """Any group/other bit means the token may be planted (writable) or disclosed (readable)."""
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    tok, _ = at.resolve(None)
    p = tmp_path / "admin_token"
    os.chmod(p, mode)
    p.write_text("planted-known-token-0123456789abcdef\n")   # what another local user could have left
    os.chmod(p, mode)
    again, note = at.resolve(None)
    assert again != tok and again != "planted-known-token-0123456789abcdef"
    assert p.read_text().strip() == again and stat.S_IMODE(p.stat().st_mode) == 0o600
    assert f"rotated (was mode {mode:04o})" in note and str(p) in note
    assert again not in note and tok not in note


def test_empty_file_from_a_racing_writer_is_waited_for_not_repaired(tmp_path, monkeypatch):
    """The no-hardlink fallback creates the path empty before writing it; a concurrent reader must
    retry briefly instead of replacing it with a DIFFERENT token."""
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    p = tmp_path / "admin_token"
    p.write_text("")
    os.chmod(p, 0o600)
    final = "w" * 40
    calls = {"n": 0}

    def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] == 2:                      # the writer finishes mid-wait
            p.write_text(final + "\n")
    monkeypatch.setattr(at.time, "sleep", fake_sleep)
    tok, note = at.resolve(None)
    assert tok == final and calls["n"] >= 2
    assert p.read_text().strip() == final


def test_existing_home_must_be_ours_and_not_group_or_world_writable(tmp_path, monkeypatch):
    import passiveworkers.net.admin_token as at
    home = tmp_path / "home"
    home.mkdir()
    os.chmod(home, 0o777)
    monkeypatch.setenv("PW_HOME", str(home))
    with pytest.raises(RuntimeError, match="writable by other users"):
        at.resolve(None)
    assert not (home / "admin_token").exists()
    os.chmod(home, 0o775)
    with pytest.raises(RuntimeError, match="writable by other users"):
        at.resolve(None)
    os.chmod(home, 0o755)                           # readable by others but not writable: fine
    assert at.resolve(None)[0]
    monkeypatch.setattr(os, "getuid", lambda: os.stat(home).st_uid + 1)
    with pytest.raises(RuntimeError, match="not owned by this user"):
        at.resolve(None)


@pytest.mark.parametrize("junk", ["", "short", "has space in it but is long enough 0123456789\nx"])
def test_corrupt_or_weak_file_is_replaced_not_trusted(tmp_path, monkeypatch, junk):
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    (tmp_path / "admin_token").write_text(junk)
    tok, _ = at.resolve(None)
    assert len(tok) >= 24 and tok != junk.strip()
    assert (tmp_path / "admin_token").read_text().strip() == tok


def test_unwritable_home_fails_closed(tmp_path, monkeypatch):
    import passiveworkers.net.admin_token as at
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("PW_HOME", str(blocker / "sub"))      # a path under a regular file
    with pytest.raises(RuntimeError, match="PW_TOKEN"):
        at.resolve(None)


def test_clients_have_no_dev_token_default(monkeypatch):
    monkeypatch.delenv("PW_TOKEN", raising=False)
    monkeypatch.setenv("PW_COORDINATOR", "http://127.0.0.1:1")
    from passiveworkers.net import config
    importlib.reload(config)
    assert config.CONFIG.token == ""
    from passiveworkers.net.agent import Agent
    assert Agent().token == ""


def test_symlinked_token_file_is_refused_not_adopted(tmp_path, monkeypatch):
    import passiveworkers.net.admin_token as at
    planted = tmp_path / "planted"
    planted.write_text("attacker-chosen-known-token-0123456789\n")
    home = tmp_path / "home"
    home.mkdir()
    (home / "admin_token").symlink_to(planted)
    monkeypatch.setenv("PW_HOME", str(home))
    with pytest.raises(RuntimeError, match="refusing to trust"):
        at.resolve(None)
    assert planted.read_text().startswith("attacker")           # target untouched


def test_token_file_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    at.resolve(None)
    owner = (tmp_path / "admin_token").stat().st_uid
    monkeypatch.setattr(os, "getuid", lambda: owner + 1)
    with pytest.raises(RuntimeError, match="owned by this user"):
        at.resolve(None)


def test_concurrent_first_start_never_sees_a_partial_file(tmp_path):
    """Publishing is atomic (os.link of a complete temp file), so a racing first start can never read
    an empty file and 'repair' it with a different token."""
    import passiveworkers.net.admin_token as at
    p = tmp_path / "admin_token"
    assert at._publish_new(p, "x" * 40) is True
    assert at._publish_new(p, "y" * 40) is False             # loser leaves the winner's file alone
    assert p.read_text().strip() == "x" * 40
    assert [f.name for f in tmp_path.iterdir()] == ["admin_token"]   # no stray temp files


def test_publish_falls_back_when_hard_links_are_unsupported(tmp_path, monkeypatch):
    import passiveworkers.net.admin_token as at

    def no_link(*a, **k):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "link", no_link)
    p = tmp_path / "admin_token"
    assert at._publish_new(p, "x" * 40) is True
    assert p.read_text().strip() == "x" * 40 and stat.S_IMODE(p.stat().st_mode) == 0o600
    assert at._publish_new(p, "y" * 40) is False
    assert [f.name for f in tmp_path.iterdir()] == ["admin_token"]


def test_fifo_at_token_path_fails_fast_instead_of_hanging(tmp_path, monkeypatch):
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    os.mkfifo(tmp_path / "admin_token")
    with pytest.raises(RuntimeError, match="is not a regular file owned by this user"):
        at.resolve(None)
