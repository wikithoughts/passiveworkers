"""The top-level `pw` dispatcher (council/cli.py) — previously untested: help, --version, unknown
command, and the new status / reports commands."""
import council.cli as cli


def test_help_lists_commands(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["pw"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "research" in out and "join" in out and "status" in out


def test_version(capsys, monkeypatch):
    for flag in ("version", "--version", "-V"):
        monkeypatch.setattr("sys.argv", ["pw", flag])
        assert cli.main() == 0
        assert "passiveworkers" in capsys.readouterr().out


def test_unknown_command_returns_2(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["pw", "frobnicate"])
    assert cli.main() == 2
    assert "unknown command" in capsys.readouterr().out


def test_reports_command(capsys, monkeypatch, tmp_path):
    d = tmp_path / "r"
    monkeypatch.setenv("PW_REPORTS_DIR", str(d))
    monkeypatch.setattr("sys.argv", ["pw", "reports"])
    assert cli.main() == 0
    assert "no reports yet" in capsys.readouterr().out
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-01-01-topic.md").write_text("hi")
    monkeypatch.setattr("sys.argv", ["pw", "reports"])
    assert cli.main() == 0
    assert "2026-01-01-topic.md" in capsys.readouterr().out


def test_status_routes_to_doctor_and_is_crash_safe(capsys, monkeypatch):
    # doctor must survive Ollama being down (return 1, not raise) and surface the fix message
    import council.local as local
    monkeypatch.setattr(local, "detect_models",
                        lambda: (_ for _ in ()).throw(SystemExit("start it with `ollama serve`")))
    for cmd in ("status", "doctor"):
        monkeypatch.setattr("sys.argv", ["pw", cmd])
        assert cli.main() == 1
        assert "ollama serve" in capsys.readouterr().out.lower()
