"""council/paths.py — the single source of truth for ~/.passiveworkers layout. Previously exercised
only incidentally through other tests (test_doctor.py pins coordinator_entries() via council.doctor,
test_config.py isolates PW_HOME but never asserts on paths.home() directly). AGENTS.md names
write_private_json() as load-bearing/security-sensitive — it must never grow a world-readable window
before the chmod, since join.json/asker.json/operator.json all hold signing keys and secrets."""

from __future__ import annotations

import inspect
import json
import os
import pathlib
import stat

import council.paths as P


# ---------------------------------------------------------------- home()
def test_home_defaults_to_dot_passiveworkers(monkeypatch):
    monkeypatch.delenv("PW_HOME", raising=False)
    assert P.home() == pathlib.Path.home() / ".passiveworkers"


def test_home_honors_pw_home_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom-home"
    monkeypatch.setenv("PW_HOME", str(custom))
    assert P.home() == custom


def test_home_treats_empty_pw_home_as_unset(monkeypatch):
    # "" is falsy — `or` in the implementation must fall through to the real default, not
    # resolve to pathlib.Path("") (effectively cwd).
    monkeypatch.setenv("PW_HOME", "")
    assert P.home() == pathlib.Path.home() / ".passiveworkers"


# ---------------------------------------------------------------- reports_dir()
def test_reports_dir_defaults_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    monkeypatch.delenv("PW_REPORTS_DIR", raising=False)
    assert P.reports_dir() == tmp_path / "reports"


def test_reports_dir_honors_pw_reports_dir_override(tmp_path, monkeypatch):
    custom = tmp_path / "elsewhere" / "reports"
    monkeypatch.setenv("PW_REPORTS_DIR", str(custom))
    assert P.reports_dir() == custom


def test_reports_dir_override_wins_even_with_different_pw_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_HOME", str(tmp_path / "home"))
    custom = tmp_path / "reports-elsewhere"
    monkeypatch.setenv("PW_REPORTS_DIR", str(custom))
    assert P.reports_dir() == custom


# ---------------------------------------------------------------- write_private_json()
def test_write_private_json_file_is_mode_0600(tmp_path):
    target = tmp_path / "join.json"
    P.write_private_json(target, {"default": "https://coord"})
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600, oct(mode)


def test_write_private_json_roundtrips_content(tmp_path):
    target = tmp_path / "asker.json"
    state = {"https://coord": {"owner": "alice", "answer_model": "m1"}, "default": "https://coord"}
    P.write_private_json(target, state)
    with target.open() as f:
        loaded = json.load(f)
    assert loaded == state


def test_write_private_json_creates_missing_parent_dirs(tmp_path):
    target = tmp_path / "deeply" / "nested" / "operator.json"
    assert not target.parent.exists()
    P.write_private_json(target, {"key": "value"})
    assert target.exists()
    assert json.loads(target.read_text()) == {"key": "value"}
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600


def test_write_private_json_overwrites_existing_file_and_keeps_0600(tmp_path):
    target = tmp_path / "config.json"
    P.write_private_json(target, {"first": "write"})
    P.write_private_json(target, {"second": "write"})
    assert json.loads(target.read_text()) == {"second": "write"}
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600


def test_write_private_json_uses_os_open_with_mode_directly():
    # Source-level guard against a regression to open()+chmod() (which has a real
    # world-readable window between the two calls). Confirm the implementation opens the fd
    # with the 0o600 mode baked into os.open() itself, never a separate os.chmod() call.
    src = inspect.getsource(P.write_private_json)
    assert "os.open(" in src
    assert "0o600" in src
    assert "os.chmod(" not in src   # the substring "chmod" alone also matches the docstring's prose


# ---------------------------------------------------------------- coordinator_entries()
def test_coordinator_entries_excludes_default_sentinel():
    # The exact bug this function exists to prevent: a naive .items() iteration treats the
    # "default" -> url string as an entry dict and crashes (see tests/test_doctor.py).
    state = {
        "default": "https://coord",
        "https://coord": {"owner": "alice", "answer_model": "m1"},
    }
    entries = P.coordinator_entries(state)
    assert "default" not in entries
    assert entries == {"https://coord": {"owner": "alice", "answer_model": "m1"}}


def test_coordinator_entries_excludes_non_dict_values_defensively():
    state = {
        "default": "https://a",
        "https://a": {"owner": "alice"},
        "https://stray-string": "not-a-dict",
        "https://stray-list": ["not", "a", "dict"],
        "https://stray-none": None,
    }
    entries = P.coordinator_entries(state)
    assert entries == {"https://a": {"owner": "alice"}}


def test_coordinator_entries_returns_all_dict_entries_multi_coordinator():
    state = {
        "default": "https://a",
        "https://a": {"owner": "alice", "answer_model": "m1"},
        "https://b": {"owner": "alice", "answer_model": "m2"},
    }
    entries = P.coordinator_entries(state)
    assert entries == {
        "https://a": {"owner": "alice", "answer_model": "m1"},
        "https://b": {"owner": "alice", "answer_model": "m2"},
    }


def test_coordinator_entries_empty_state_returns_empty_dict():
    assert P.coordinator_entries({}) == {}


def test_coordinator_entries_does_not_mutate_input():
    state = {"default": "https://a", "https://a": {"owner": "alice"}}
    original = dict(state)
    P.coordinator_entries(state)
    assert state == original
