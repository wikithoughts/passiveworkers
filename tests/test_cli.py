"""The top-level `pworkers` dispatcher (passiveworkers/cli.py) — previously untested: help, --version, unknown
command, and the new status / reports commands."""
import passiveworkers.cli as cli


def test_help_lists_commands(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["pworkers"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "research" in out and "join" in out and "status" in out


def test_version(capsys, monkeypatch):
    for flag in ("version", "--version", "-V"):
        monkeypatch.setattr("sys.argv", ["pworkers", flag])
        assert cli.main() == 0
        assert "passiveworkers" in capsys.readouterr().out


def test_unknown_command_returns_2(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["pworkers", "frobnicate"])
    assert cli.main() == 2
    assert "unknown command" in capsys.readouterr().out


def test_reports_command(capsys, monkeypatch, tmp_path):
    d = tmp_path / "r"
    monkeypatch.setenv("PW_REPORTS_DIR", str(d))
    monkeypatch.setattr("sys.argv", ["pworkers", "reports"])
    assert cli.main() == 0
    assert "no reports yet" in capsys.readouterr().out
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-01-01-topic.md").write_text("hi")
    monkeypatch.setattr("sys.argv", ["pworkers", "reports"])
    assert cli.main() == 0
    assert "2026-01-01-topic.md" in capsys.readouterr().out


def test_status_routes_to_doctor_and_is_crash_safe(capsys, monkeypatch):
    # doctor must survive Ollama being down (return 1, not raise) and surface the fix message
    import passiveworkers.local as local
    monkeypatch.setattr(local, "detect_models",
                        lambda: (_ for _ in ()).throw(SystemExit("start it with `ollama serve`")))
    for cmd in ("status", "doctor"):
        monkeypatch.setattr("sys.argv", ["pworkers", cmd])
        assert cli.main() == 1
        assert "ollama serve" in capsys.readouterr().out.lower()


def test_config_command_dispatches(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["pworkers", "config", "set", "PW_WEB_BACKEND", "ddgs"])
    assert cli.main() == 0
    assert "PW_WEB_BACKEND" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["pworkers", "config"])          # bare → list
    assert cli.main() == 0
    assert "ddgs" in capsys.readouterr().out


def test_doctor_names_keyed_fallback_before_ddg(capsys, monkeypatch):
    # regression: when a keyed primary lacks its key but ANOTHER keyed backend has one, that keyed
    # backend is the real first fallback — the ⚠ line must name it, not just DuckDuckGo
    import passiveworkers.local as local
    import passiveworkers.doctor as doctor
    monkeypatch.setattr(local, "detect_models",
                        lambda: (_ for _ in ()).throw(SystemExit("start it with `ollama serve`")))
    monkeypatch.setenv("PW_WEB_BACKEND", "brave")
    monkeypatch.delenv("PW_BRAVE_KEY", raising=False)
    monkeypatch.setenv("PW_TAVILY_KEY", "tvly-x")
    doctor.main()
    assert "tavily, then DuckDuckGo" in capsys.readouterr().out


def test_config_apply_to_env_runs_before_dispatch(monkeypatch, tmp_path):
    # a value persisted via config becomes the env default for a later command (setdefault seam)
    import passiveworkers.config as C
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    monkeypatch.delenv("PW_WEB_BACKEND", raising=False)
    C.set("PW_WEB_BACKEND", "serper")
    monkeypatch.setattr("sys.argv", ["pworkers", "reports"])         # any cheap command triggers apply_to_env
    import os
    assert cli.main() == 0
    assert os.environ.get("PW_WEB_BACKEND") == "serper"
    monkeypatch.delenv("PW_WEB_BACKEND", raising=False)
