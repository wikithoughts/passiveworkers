"""Flagship single-player orchestration (council/local.py): model detection + friendly first-run
errors, capacity capping, family-diverse cast selection, and a mocked end-to-end run (no Ollama)."""
import pytest

import council.local as L


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


# ---------------------------------------------------------------- first-run robustness
def test_detect_models_friendly_when_ollama_unreachable(monkeypatch):
    import requests
    monkeypatch.setattr(L.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError("refused")))
    with pytest.raises(SystemExit) as e:
        L.detect_models()
    assert "ollama serve" in str(e.value).lower()      # a fix, not a traceback


def test_detect_models_no_models_message(monkeypatch):
    monkeypatch.setattr(L.requests, "get", lambda *a, **k: _Resp({"models": []}))
    with pytest.raises(SystemExit) as e:
        L.detect_models()
    assert "ollama pull" in str(e.value).lower()


def test_detect_models_excludes_and_caps_and_sorts(monkeypatch):
    monkeypatch.setattr(L.requests, "get", lambda *a, **k: _Resp({"models": [
        {"name": "qwen3:14b", "size": 9_000_000_000},
        {"name": "gemma3:4b", "size": 3_000_000_000},
        {"name": "nomic-embed-text", "size": 300_000_000},   # excluded (embed)
        {"name": "llava-vision", "size": 5_000_000_000},     # excluded (vision)
    ]}))
    monkeypatch.setenv("PW_MODEL_CAP_GB", "5")               # 5 GB cap → drop qwen(9GB), keep gemma(3GB)
    names = [m["name"] for m in L.detect_models()]
    assert "nomic-embed-text" not in names and "llava-vision" not in names
    assert names == ["gemma3:4b"]                            # capped + the only survivor


def test_detect_models_cap_never_yields_zero_models(monkeypatch):
    # when EVERY model exceeds the cap, fall back to one model rather than crash with "no models"
    monkeypatch.setattr(L.requests, "get", lambda *a, **k: _Resp({"models": [
        {"name": "qwen3:14b", "size": 9_000_000_000},
        {"name": "gemma3:12b", "size": 8_000_000_000},
    ]}))
    monkeypatch.setenv("PW_MODEL_CAP_GB", "1")               # 1 GB cap → nothing fits
    names = [m["name"] for m in L.detect_models()]
    assert len(names) == 1 and names[0] in ("qwen3:14b", "gemma3:12b")   # fallback, not empty


# ---------------------------------------------------------------- cast selection
def test_pick_cast_is_family_diverse_with_largest_editor():
    models = sorted([{"name": "gemma3:4b", "size": 3e9}, {"name": "qwen3:7b", "size": 7e9},
                     {"name": "qwen3:14b", "size": 14e9}, {"name": "llama3.2:3b", "size": 3e9}],
                    key=lambda m: m["size"])
    analysts, editor = L.pick_cast(models, n_analysts=3)
    assert editor == "qwen3:14b"                             # largest = the quality anchor
    assert len(analysts) == len(set(analysts))              # distinct
    assert sum(1 for a in analysts if "qwen" in a) == 1     # family-deduped (one qwen, not two)


# ---------------------------------------------------------------- end-to-end run (mocked, no Ollama)
class _FakeRW:
    def __init__(self, *a, **k):
        pass

    def research(self, brief):
        return {"text": "a finding [S1]", "tokens": 10, "elapsed_s": 1.0,
                "research": {"country": "x", "sources": [{"id": "S1", "title": "t", "url": "u",
                                                          "host": "h"}], "local_sources": []}}


class _FakeJudge:
    def __init__(self, *a, **k):
        pass

    def deliberate(self, brief, answers):
        return {"scores": {}, "merged": "tldr", "council": {}}

    def compile_report(self, brief, contributions, read, local=True):
        return "# Report\n\nbody with [S1]"


def test_run_writes_report_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "detect_models", lambda: [{"name": "gemma3:4b", "size": 3e9},
                                                     {"name": "qwen3:14b", "size": 14e9}])
    monkeypatch.setattr(L, "plan_angles", lambda *a, **k: ["angle one", "angle two"])
    monkeypatch.setattr(L, "ResearchWorker", _FakeRW)
    monkeypatch.setattr(L, "Judge", _FakeJudge)
    out = L.run("what is X", depth="quick", out_dir=str(tmp_path / "reports"), n_analysts=2)
    assert out.exists() and out.suffix == ".md"
    assert "Report" in out.read_text()


def test_run_surfaces_friendly_no_ollama(monkeypatch):
    monkeypatch.setattr(L, "detect_models",
                        lambda: (_ for _ in ()).throw(SystemExit("Can't reach Ollama … `ollama serve`")))
    with pytest.raises(SystemExit) as e:
        L.run("hi", depth="quick")
    assert "ollama serve" in str(e.value).lower()
