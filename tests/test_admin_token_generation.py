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
    assert at.resolve(None) != first


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
    tok = at.resolve(None)
    p = tmp_path / "admin_token"
    os.chmod(p, mode)
    p.write_text("planted-known-token-0123456789abcdef\n")   # what another local user could have left
    os.chmod(p, mode)
    again = at.resolve(None)
    note = at.startup_note()
    assert again != tok and again != "planted-known-token-0123456789abcdef"
    assert p.read_text().strip() == again and stat.S_IMODE(p.stat().st_mode) == 0o600
    assert f"rotated (was mode {mode:04o})" in note and str(p) in note
    assert again not in note and tok not in note


@pytest.mark.parametrize("how", ["no_flock", "flock_errors", "lock_unopenable"])
def test_unavailable_lock_fails_closed_and_writes_nothing(tmp_path, monkeypatch, how):
    """Without the lock, two coordinators starting together could end up with different tokens
    (a rotate/repair race, or a no-hardlink writer's empty-file window). Refuse instead of running
    unserialised: no token file is created, read, chmodded or replaced, and the operator is told to
    set PW_TOKEN."""
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    p = tmp_path / "admin_token"
    if how == "no_flock":
        monkeypatch.setattr(at, "fcntl", None)                    # platform without flock
    elif how == "flock_errors":
        def bad_flock(fd, op):
            raise OSError(45, "Operation not supported")          # e.g. some NFS/FUSE mounts
        monkeypatch.setattr(at.fcntl, "flock", bad_flock)
    else:
        (tmp_path / ".admin_token.lock").mkdir()                  # lock file cannot be opened
    with pytest.raises(RuntimeError, match="PW_TOKEN") as e:
        at.resolve(None)
    assert "lock" in str(e.value)
    assert not p.exists()                                         # nothing generated unserialised
    p.write_text("planted-or-disclosed-token-0123456789\n")
    os.chmod(p, 0o644)
    with pytest.raises(RuntimeError, match="PW_TOKEN"):
        at.resolve(None)
    assert stat.S_IMODE(p.stat().st_mode) == 0o644                # not chmodded into a "trusted" file
    assert p.read_text().startswith("planted-or-disclosed")       # nor rotated/adopted
    assert at.startup_note() is None                              # a failed resolve leaves no note
    assert at.resolve("an-explicit-strong-admin-token-0123456789")   # explicit PW_TOKEN still works


def test_existing_home_must_be_ours_and_not_group_or_world_writable(tmp_path, monkeypatch):
    import passiveworkers.net.admin_token as at
    home = tmp_path / "home"
    home.mkdir()
    os.chmod(home, 0o777)
    monkeypatch.setenv("PW_HOME", str(home))
    with pytest.raises(RuntimeError, match="PW_HOME.*writable by other users"):
        at.resolve(None)
    assert not (home / "admin_token").exists()
    monkeypatch.setattr(at, "_is_private_group", lambda gid: True)
    with pytest.raises(RuntimeError, match="PW_HOME.*writable by other users"):   # never world-writable
        at.resolve(None)
    monkeypatch.setattr(at, "_is_private_group", lambda gid: False)
    os.chmod(home, 0o775)                           # shared group: refuse
    with pytest.raises(RuntimeError, match="PW_HOME.*writable by other users"):
        at.resolve(None)
    os.chmod(home, 0o755)                           # readable by others but not writable: fine
    assert at.resolve(None)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(home).st_uid + 1)
    with pytest.raises(RuntimeError, match="PW_HOME.*not owned by this user"):
        at.resolve(None)


def test_group_writable_home_is_accepted_for_a_private_group_with_a_warning(tmp_path, monkeypatch):
    """Debian/Ubuntu user-private-group (umask 002) makes ~/.passiveworkers 0775 with our own group."""
    import passiveworkers.net.admin_token as at
    home = tmp_path / "home"
    home.mkdir()
    os.chown(home, -1, os.getgid())                 # BSD/macOS dirs inherit the parent's group
    os.chmod(home, 0o775)
    monkeypatch.setenv("PW_HOME", str(home))
    monkeypatch.setattr(at, "_is_private_group", lambda gid: gid == os.getgid())
    assert at.resolve(None)
    assert "group-writable" in at.startup_note() and str(home) in at.startup_note()


def test_private_group_detection(monkeypatch):
    import grp
    import pwd

    import passiveworkers.net.admin_token as at

    me = pwd.getpwuid(os.getuid())
    gid = os.getgid()
    # a user-private group: our primary gid, named after us, nobody else listed as a member
    me_upg = pwd.struct_passwd((me.pw_name, "x", me.pw_uid, gid, "", "/", "/bin/sh"))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: me_upg)

    def no_getpwall():   # slow/incomplete under LDAP/NSS: must not be used
        raise AssertionError("pwd.getpwall must not be called")
    monkeypatch.setattr(pwd, "getpwall", no_getpwall)

    def group(name, members):
        monkeypatch.setattr(grp, "getgrgid", lambda g: grp.struct_group((name, "x", g, members)))

    group(me.pw_name, [])
    assert at._is_private_group(gid) is True
    group(me.pw_name, [me.pw_name])
    assert at._is_private_group(gid) is True                         # listing ourselves is fine
    assert at._is_private_group(gid + 1) is False                    # not our primary group
    group(me.pw_name, [me.pw_name, "other"])
    assert at._is_private_group(gid) is False                        # another member
    group("staff", [])                                               # macOS staff / Linux users:
    assert at._is_private_group(gid) is False                        # shared primary group, empty gr_mem
    monkeypatch.setattr(grp, "getgrgid", lambda g: (_ for _ in ()).throw(KeyError(g)))
    assert at._is_private_group(gid) is False                        # unknown group: not private
    group(me.pw_name, [])
    monkeypatch.setattr(pwd, "getpwuid",
                        lambda uid: pwd.struct_passwd((me.pw_name, "x", me.pw_uid, gid + 1, "", "/", "/bin/sh")))
    assert at._is_private_group(gid) is False                        # passwd disagrees on the primary gid


def _hold_lock(tmp_path):
    import fcntl
    fd = os.open(tmp_path / ".admin_token.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_resolve_waits_on_the_lock_not_a_timer(tmp_path, monkeypatch):
    """A writer (e.g. the no-hardlink fallback) holds the lock while the file is still empty; the
    reader must wait for the lock — however long — then read the finished token, never replace it."""
    import threading
    import time as _t

    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    (tmp_path / "admin_token").write_text("")
    os.chmod(tmp_path / "admin_token", 0o600)
    fd = _hold_lock(tmp_path)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("tok", at.resolve(None)))
    t.start()
    _t.sleep(1.6)                                   # longer than the old 1 s timer
    assert t.is_alive()                             # still waiting on the lock
    (tmp_path / "admin_token").write_text("s" * 40 + "\n")
    os.close(fd)                                    # writer done: releases the lock
    t.join(5)
    assert out["tok"] == "s" * 40
    assert (tmp_path / "admin_token").read_text().strip() == "s" * 40


def test_concurrent_rotations_converge_on_one_token(tmp_path, monkeypatch):
    import threading
    import time as _t

    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    at.resolve(None)
    p = tmp_path / "admin_token"
    os.chmod(p, 0o644)
    real = at._replace_private

    def slow(path, tok):
        _t.sleep(0.15)
        real(path, tok)
    monkeypatch.setattr(at, "_replace_private", slow)
    got = []
    ts = [threading.Thread(target=lambda: got.append(at.resolve(None))) for _ in range(5)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert len(got) == 5 and len(set(got)) == 1
    assert p.read_text().strip() == got[0] and stat.S_IMODE(p.stat().st_mode) == 0o600


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_unenforced_loose_token_file_is_refused_by_default(tmp_path, monkeypatch, mode):
    """fchmod(0600) did not take: on NFS root-squash/FUSE/bind mounts the loose mode is real, so
    others can read the token. Refuse (name the path and the fix) unless the operator opts in."""
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    monkeypatch.delenv("PW_ALLOW_LOOSE_TOKEN_FILE", raising=False)
    at.resolve(None)
    p = tmp_path / "admin_token"
    os.chmod(p, mode)
    monkeypatch.setattr(os, "fchmod", lambda fd, mode: None)      # the filesystem ignores it
    with pytest.raises(RuntimeError, match="PW_TOKEN") as e:
        at.resolve(None)
    assert str(p) in str(e.value) and "PW_ALLOW_LOOSE_TOKEN_FILE=1" in str(e.value)
    monkeypatch.setenv("PW_ALLOW_LOOSE_TOKEN_FILE", "0")           # only "1" opts in
    with pytest.raises(RuntimeError, match="PW_TOKEN"):
        at.resolve(None)


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_unenforced_loose_token_file_is_kept_only_with_explicit_opt_in(tmp_path, monkeypatch, mode):
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    first = at.resolve(None)
    os.chmod(tmp_path / "admin_token", mode)
    monkeypatch.setattr(os, "fchmod", lambda fd, mode: None)
    monkeypatch.setenv("PW_ALLOW_LOOSE_TOKEN_FILE", "1")
    assert at.resolve(None) == first                               # not rotated, stable across restarts
    note = at.startup_note()
    assert "filesystem does not enforce permissions; token not rotated" in note
    assert "READABLE BY OTHER USERS" in note and str(tmp_path / "admin_token") in note
    assert first not in note
    assert at.resolve(None) == first


@pytest.mark.parametrize("junk", ["", "short", "has space in it but is long enough 0123456789\nx"])
def test_corrupt_or_weak_file_is_replaced_not_trusted(tmp_path, monkeypatch, junk):
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    (tmp_path / "admin_token").write_text(junk)
    tok = at.resolve(None)
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


def test_token_value_never_appears_in_any_output_or_message(tmp_path, monkeypatch, capsys, caplog):
    """CodeQL py/clear-text-logging: the token must not reach stdout/stderr/logs/notes/exceptions on the
    generate, adopt, rotate, repair and legacy-dev-token paths (startup_note has no access to it)."""
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    p = tmp_path / "admin_token"
    seen = []

    def step(env=None):
        tok = at.resolve(env)
        seen.append(tok)
        note = at.startup_note()
        assert tok not in (note or "")
        return tok

    step()                                            # generate
    step()                                            # adopt
    step("dev-token")                                 # legacy dev-token
    os.chmod(p, 0o666)
    step()                                            # rotate
    p.write_text("")
    os.chmod(p, 0o600)
    monkeypatch.setattr(at.time, "sleep", lambda s: None)
    step()                                            # repair
    out = capsys.readouterr()
    boot_mod, _, _ = _boot(tmp_path / "boot", monkeypatch)   # the real coordinator startup path
    boot = capsys.readouterr()
    everything = out.out + out.err + boot.out + boot.err + caplog.text
    for tok in seen + [boot_mod.CONFIG.token]:
        assert tok not in everything


def test_no_chmod_filesystem_with_other_writable_token_file_is_refused(tmp_path, monkeypatch):
    """If the mode cannot be fixed AND others may write the file, a planted token cannot be ruled out:
    refuse (set PW_TOKEN) instead of adopting, even with PW_ALLOW_LOOSE_TOKEN_FILE=1."""
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    at.resolve(None)
    p = tmp_path / "admin_token"
    monkeypatch.setattr(os, "fchmod", lambda fd, mode: None)
    monkeypatch.setenv("PW_ALLOW_LOOSE_TOKEN_FILE", "1")           # the opt-in never covers other-writable
    for mode in (0o666, 0o662, 0o620):
        os.chmod(p, mode)
        with pytest.raises(RuntimeError, match="PW_TOKEN"):
            at.resolve(None)
    os.chmod(p, 0o644)
    assert at.resolve(None)


def test_lock_wait_is_bounded_and_fails_closed(tmp_path, monkeypatch):
    import passiveworkers.net.admin_token as at
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    monkeypatch.setattr(at, "_LOCK_TIMEOUT_S", 0.3)
    fd = _hold_lock(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="lock"):
            at.resolve(None)
    finally:
        os.close(fd)
    assert at.resolve(None)


def test_resolve_state_is_per_thread(tmp_path, monkeypatch):
    """resolve()'s event and warnings are per thread: a concurrent resolve() in another thread must
    not change (or clear) the note this thread gets for its own resolve()."""
    import threading

    import passiveworkers.net.admin_token as at
    home = tmp_path / "home"
    home.mkdir()
    os.chown(home, -1, os.getgid())
    os.chmod(home, 0o775)                                  # makes resolve() record a warning
    monkeypatch.setenv("PW_HOME", str(home))
    monkeypatch.setattr(at, "_is_private_group", lambda gid: gid == os.getgid())
    a_resolved, b_resolved = threading.Event(), threading.Event()
    out = {}

    def a():
        out["tok"] = at.resolve(None)                      # generates the file, with a warning
        a_resolved.set()
        b_resolved.wait(5)
        out["a_note"] = at.startup_note()

    def b():
        a_resolved.wait(5)
        at.resolve("an-explicit-strong-admin-token-0123456789")   # EXPLICIT, clears warnings
        out["b_note"] = at.startup_note()
        b_resolved.set()

    ts = [threading.Thread(target=a), threading.Thread(target=b)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert out["b_note"] is None
    assert out["a_note"] and "generated a random admin token" in out["a_note"]
    assert "group-writable" in out["a_note"] and out["tok"] not in out["a_note"]
    seen = {}
    t = threading.Thread(target=lambda: seen.setdefault("note", at.startup_note()))
    t.start()
    t.join(5)
    assert seen["note"] is None                            # a thread that never resolved has no note


def test_non_posix_platform_imports_cleanly_and_refuses_generation_with_a_clear_message(tmp_path):
    """Windows has no fcntl/grp/pwd: importing the module (and so the coordinator) must not crash,
    an explicit PW_TOKEN must still work, and only the generated-token path refuses, saying why."""
    import subprocess
    import sys
    import textwrap
    code = textwrap.dedent("""
        import sys
        for m in ("fcntl", "grp", "pwd"):
            sys.modules[m] = None          # `import fcntl` now raises ImportError, as on Windows
        import passiveworkers.net.admin_token as at
        assert at.resolve("an-explicit-strong-admin-token-0123456789")
        try:
            at.resolve(None)
        except RuntimeError as e:
            print("REFUSED:", e)
        else:
            print("GENERATED")
    """)
    env = {**os.environ, "PW_HOME": str(tmp_path / "home")}
    env.pop("PW_TOKEN", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("REFUSED:"), r.stdout
    assert "PW_TOKEN" in r.stdout and "POSIX" in r.stdout
    assert not (tmp_path / "home" / "admin_token").exists()
