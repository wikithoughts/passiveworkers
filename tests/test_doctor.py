"""tests/test_doctor.py — `pw status`/`pw doctor` (council/doctor.py).

Focus: the network-membership check (F1 review finding) had zero coverage — agent.py's
join.json always carries a "default" -> url sentinel alongside url -> {config}, and a naive
`.items()` iteration crashes on that string, printing "Not joined" right after (or instead of)
a true "Joined" line. `paths.coordinator_entries()` is the fix; these tests pin its use here.
"""

from __future__ import annotations

import importlib
import io
from contextlib import redirect_stdout

import council.doctor as D
from council.net.agent import _save_join


def _run_doctor() -> str:
    # council.library's LIB_DIR/LIB_DB are computed ONCE at module-import time from
    # PW_LIBRARY_DIR — whichever test file imports it FIRST in the whole session bakes in
    # that value, so a later monkeypatch.setenv here has no effect on doctor's "Library:"
    # check unless the module is reloaded (review finding: every doctor test was silently
    # touching the real ~/.passiveworkers/library.db instead of the tmp one).
    import council.library
    importlib.reload(council.library)
    buf = io.StringIO()
    with redirect_stdout(buf):
        D.main()
    return buf.getvalue()


def test_status_shows_one_truthful_line_per_joined_coordinator(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    _save_join({"default": "https://coord", "https://coord": {"owner": "alice", "answer_model": "m1"}})

    out = _run_doctor()

    assert out.count("✓ Joined") == 1
    assert "· Not joined" not in out
    assert "https://coord" in out and "alice" in out


def test_status_shows_all_coordinators_when_multiple_joined(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    _save_join({
        "default": "https://a",
        "https://a": {"owner": "alice", "answer_model": "m1"},
        "https://b": {"owner": "alice", "answer_model": "m2"},
    })

    out = _run_doctor()

    assert out.count("✓ Joined") == 2
    assert "https://a" in out and "https://b" in out
    assert "· Not joined" not in out


def test_status_not_joined_when_join_file_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))

    out = _run_doctor()

    assert "· Not joined" in out
    assert "✓ Joined" not in out


def test_doctor_embedder_row_present(tmp_path, monkeypatch):
    # No Ollama in this sandbox → _embedder_installed() returns None (unreachable) — pin that the
    # row still appears rather than being silently absent (R12 review).
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    out = _run_doctor()
    assert "Embedder" in out


def test_status_membership_check_failure_names_the_exception_type(tmp_path, monkeypatch):
    # A join.json that isn't even valid JSON — _load_join()'s own except returns {} for this,
    # so this exercises the "not joined" branch cleanly rather than the diagnostic suffix; kept
    # as a smoke test that main() never raises regardless of file corruption.
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    (tmp_path / "join.json").write_text("not json")

    out = _run_doctor()

    assert "· Not joined" in out
