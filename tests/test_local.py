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

    def research(self, brief, on_progress=None):
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


# ---------------------------------------------------------------- R26: per-analyst progress prefix
def test_run_prefixes_stage_progress_with_correct_analyst_index(monkeypatch, tmp_path):
    # Regression guard for the classic closure-over-loop-variable bug: without binding `prefix`
    # via a default argument at closure-creation time, EVERY analyst's stage messages would show
    # under whichever analyst's prefix happens to be live when the callback actually fires.
    class _StageRW:
        def __init__(self, worker_id, **k):
            self.worker_id = worker_id

        def research(self, brief, on_progress=None):
            if on_progress:
                on_progress(f"stage-from-{self.worker_id}")
            return {"text": "a finding [S1]", "tokens": 5, "elapsed_s": 0.1,
                    "research": {"country": "x", "sources": [{"id": "S1", "title": "t",
                                                              "url": "u", "host": "h"}],
                                "local_sources": []}}

    monkeypatch.setattr(L, "detect_models", lambda: [{"name": "model-a", "size": 3e9},
                                                     {"name": "model-b", "size": 4e9}])
    monkeypatch.setattr(L, "plan_angles", lambda *a, **k: [])
    monkeypatch.setattr(L._research, "reset_ddg_breaker", lambda: None)
    monkeypatch.setattr(L, "ResearchWorker", _StageRW)
    monkeypatch.setattr(L, "Judge", _FakeJudge)

    lines = []
    L.run("what is X", depth="quick", out_dir=str(tmp_path / "reports"), n_analysts=2,
         on_progress=lines.append)

    # pick_cast may reorder the cast (family/size-diverse selection) — don't assume which
    # analyst index either model lands at. The regression this guards against is a stage
    # line's prefix naming the WRONG model, not a specific ordering.
    a_lines = [ln for ln in lines if "stage-from-model-a" in ln]
    b_lines = [ln for ln in lines if "stage-from-model-b" in ln]
    assert a_lines and b_lines
    assert all("model-a" in ln.split(":")[0] for ln in a_lines)   # prefix (before the colon) names model-a
    assert all("model-b" in ln.split(":")[0] for ln in b_lines)   # prefix names model-b, never model-a's


def test_run_surfaces_friendly_no_ollama(monkeypatch):
    monkeypatch.setattr(L, "detect_models",
                        lambda: (_ for _ in ()).throw(SystemExit("Can't reach Ollama … `ollama serve`")))
    with pytest.raises(SystemExit) as e:
        L.run("hi", depth="quick")
    assert "ollama serve" in str(e.value).lower()


# ---------------------------------------------------------------- R2: honest-output-contract
class _ZeroSourceRW:
    def __init__(self, *a, **k):
        pass

    def research(self, brief, on_progress=None):
        return {"text": "(No web or local sources reachable for this brief; no findings to report.)",
                "tokens": 0, "elapsed_s": 0.1,
                "research": {"country": "x", "sources": [], "local_sources": []}}


def _base_run_mocks(monkeypatch):
    monkeypatch.setattr(L, "detect_models", lambda: [{"name": "gemma3:4b", "size": 3e9},
                                                      {"name": "qwen3:14b", "size": 14e9}])
    monkeypatch.setattr(L, "plan_angles", lambda *a, **k: [])
    monkeypatch.setattr(L._research, "reset_ddg_breaker", lambda: None)


def test_run_raises_on_zero_sources(monkeypatch):
    _base_run_mocks(monkeypatch)
    monkeypatch.setattr(L, "ResearchWorker", _ZeroSourceRW)
    monkeypatch.setattr(L, "Judge", _FakeJudge)
    with pytest.raises(SystemExit) as e:
        L.run("what is X", depth="quick", n_analysts=2)
    msg = str(e.value).lower()
    assert "no web or library sources" in msg
    assert "pw_web_backend" in msg.lower() or "searxng" in msg


def test_run_local_scope_with_library_sources_does_not_raise(monkeypatch, tmp_path):
    _base_run_mocks(monkeypatch)

    class _LibOnlyRW:
        def __init__(self, *a, **k):
            pass

        def research(self, brief, on_progress=None):
            return {"text": "a library finding [L1]", "tokens": 5, "elapsed_s": 0.1,
                    "research": {"country": "x", "sources": [],
                                "local_sources": [{"id": "L1", "title": "notes", "source": "/n.md"}]}}

    monkeypatch.setattr(L, "ResearchWorker", _LibOnlyRW)
    monkeypatch.setattr(L, "Judge", _FakeJudge)
    out = L.run("what is X", depth="quick", out_dir=str(tmp_path / "reports"),
               n_analysts=1, scope="local")
    assert out.exists()


# ---------------------------------------------------------------- R3: resilient-run
def test_analyst_crash_does_not_discard_other_analysts_contributions(monkeypatch, tmp_path):
    _base_run_mocks(monkeypatch)

    class _FlakyRW:
        calls = 0

        def __init__(self, *a, **k):
            pass

        def research(self, brief, on_progress=None):
            _FlakyRW.calls += 1
            if _FlakyRW.calls == 2:
                raise RuntimeError("simulated Ollama timeout")
            return {"text": f"finding-{_FlakyRW.calls} [S1]", "tokens": 5, "elapsed_s": 0.1,
                    "research": {"country": "x", "sources": [{"id": "S1", "title": "t",
                                                              "url": "u", "host": "h"}],
                                "local_sources": []}}

    monkeypatch.setattr(L, "ResearchWorker", _FlakyRW)
    monkeypatch.setattr(L, "Judge", _FakeJudge)
    out = L.run("what is X", depth="quick", out_dir=str(tmp_path / "reports"), n_analysts=3)
    assert out.exists()   # 2 of 3 analysts succeeded — a report is still produced


def test_all_analysts_failing_raises_systemexit_naming_them(monkeypatch):
    _base_run_mocks(monkeypatch)

    class _AlwaysFailRW:
        def __init__(self, *a, **k):
            pass

        def research(self, brief, on_progress=None):
            raise RuntimeError("Ollama down")

    monkeypatch.setattr(L, "ResearchWorker", _AlwaysFailRW)
    monkeypatch.setattr(L, "Judge", _FakeJudge)
    with pytest.raises(SystemExit) as e:
        L.run("what is X", depth="quick", n_analysts=2)
    assert "analyst" in str(e.value).lower() and "--analysts 1" in str(e.value)


def test_judge_deliberate_failure_falls_back_to_raw_findings(monkeypatch, tmp_path):
    _base_run_mocks(monkeypatch)

    class _BoomJudge:
        def __init__(self, *a, **k):
            pass

        def deliberate(self, brief, answers):
            raise RuntimeError("judge model crashed")

        def compile_report(self, brief, contributions, read, local=True):
            assert read == {"scores": {}, "merged": "", "council": {}}   # the degraded fallback
            return "# Report\n\nbody with [S1]"

    monkeypatch.setattr(L, "ResearchWorker", _FakeRW)
    monkeypatch.setattr(L, "Judge", _BoomJudge)
    out = L.run("what is X", depth="quick", out_dir=str(tmp_path / "reports"), n_analysts=1)
    assert out.exists()


def test_editor_compile_failure_uses_fallback_report(monkeypatch, tmp_path):
    _base_run_mocks(monkeypatch)

    class _BoomEditorJudge:
        def __init__(self, *a, **k):
            pass

        def deliberate(self, brief, answers):
            return {"scores": {}, "merged": "tldr", "council": {}}

        def compile_report(self, brief, contributions, read, local=True):
            raise RuntimeError("editor model crashed")

    monkeypatch.setattr(L, "ResearchWorker", _FakeRW)
    monkeypatch.setattr(L, "Judge", _BoomEditorJudge)
    out = L.run("what is X", depth="quick", out_dir=str(tmp_path / "reports"), n_analysts=1)
    assert out.exists()
    text = out.read_text()
    assert "editor pass failed" in text.lower()
    assert "a finding" in text   # the raw analyst finding survived into the fallback report


# ---------------------------------------------------------------- R15: unfuse-knobs
def test_page_evidence_on_by_default_even_with_model_cap(monkeypatch):
    monkeypatch.delenv("PW_PAGE_EVIDENCE", raising=False)
    monkeypatch.setenv("PW_MODEL_CAP_GB", "3")
    assert L._page_evidence_enabled() is True


def test_page_evidence_off_via_own_knob(monkeypatch):
    monkeypatch.delenv("PW_MODEL_CAP_GB", raising=False)
    monkeypatch.setenv("PW_PAGE_EVIDENCE", "0")
    assert L._page_evidence_enabled() is False


def test_page_evidence_off_notice_emitted(monkeypatch, tmp_path):
    _base_run_mocks(monkeypatch)
    monkeypatch.setenv("PW_PAGE_EVIDENCE", "0")
    monkeypatch.setattr(L, "ResearchWorker", _FakeRW)
    monkeypatch.setattr(L, "Judge", _FakeJudge)
    seen = []
    L.run("what is X", depth="quick", out_dir=str(tmp_path / "reports"), n_analysts=1,
         on_progress=seen.append)
    assert any("page evidence off" in m.lower() or "PW_PAGE_EVIDENCE" in m for m in seen)


# ---------------------------------------------------------------- R11: local source count
def test_local_run_final_summary_counts_local_sources(monkeypatch, tmp_path):
    _base_run_mocks(monkeypatch)

    class _LocalOnlyRW:
        def __init__(self, *a, **k):
            pass

        def research(self, brief, on_progress=None):
            return {"text": "a finding [L1]", "tokens": 5, "elapsed_s": 0.1,
                    "research": {"country": "x", "sources": [],
                                "local_sources": [{"id": "L1", "title": "notes", "source": "/n.md"}]}}

    monkeypatch.setattr(L, "ResearchWorker", _LocalOnlyRW)
    monkeypatch.setattr(L, "Judge", _FakeJudge)
    seen = []
    L.run("what is X", depth="quick", out_dir=str(tmp_path / "reports"), n_analysts=1,
         scope="local", on_progress=seen.append)
    final = [m for m in seen if m.startswith("📄 Report ready")][0]
    assert "0 sources" not in final   # was always "0 sources" for --local runs before R11
    assert "1 sources" in final
    # Workflow-review finding: the PER-ANALYST progress line was fixed separately from the
    # final summary line above — pin both, since they previously disagreed within one run.
    per_analyst = [m for m in seen if "words" in m and "sources ·" in m][0]
    assert "0 sources" not in per_analyst
    assert "1 sources" in per_analyst
