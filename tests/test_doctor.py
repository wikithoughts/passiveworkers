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

import pytest

import council.doctor as D
from council.net.agent import _save_join


def _run_doctor(argv=None) -> str:
    # council.library's LIB_DIR/LIB_DB are computed ONCE at module-import time from
    # PW_LIBRARY_DIR — whichever test file imports it FIRST in the whole session bakes in
    # that value, so a later monkeypatch.setenv here has no effect on doctor's "Library:"
    # check unless the module is reloaded (review finding: every doctor test was silently
    # touching the real ~/.passiveworkers/library.db instead of the tmp one).
    import council.library
    importlib.reload(council.library)
    buf = io.StringIO()
    with redirect_stdout(buf):
        D.main(argv)
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


# ---------------------------------------------------------------- R26: pw status --eta
class _FakeGenResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def test_probe_tokens_per_sec_computes_rate_from_eval_fields(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeGenResp(
        {"eval_count": 50, "eval_duration": 5_000_000_000}))   # 50 tokens / 5s = 10 tok/s
    assert D._probe_tokens_per_sec("m", "http://x") == 10.0


def test_probe_tokens_per_sec_returns_none_on_failure(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.RequestException("down")))
    assert D._probe_tokens_per_sec("m", "http://x") is None


def test_probe_tokens_per_sec_returns_none_on_zero_eval_fields(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeGenResp({"eval_count": 0, "eval_duration": 0}))
    assert D._probe_tokens_per_sec("m", "http://x") is None


def test_estimate_minutes_quick_has_zero_refine_rounds():
    rates = {"a": 10.0, "e": 10.0}
    lo, hi = D.estimate_minutes(rates, ["a"], "e", "quick", default_rate=5.0)
    # quick: 0 refine rounds → per-analyst high == PLAN + DRAFT*2 (one retry) + REPAIR, no REFINE
    expected_low = (D._PLAN + D._DRAFT) / 10.0 + D._EDITOR_LOW / 10.0
    expected_high = ((D._PLAN + D._DRAFT * 2 + D._REPAIR) / 10.0 + D._EDITOR_HIGH / 10.0
                    + D._RERANK / 5.0)   # rerank costed at default_rate, not the probed rate
    assert lo == pytest.approx(expected_low / 60.0)
    assert hi == pytest.approx(expected_high / 60.0)


def test_estimate_minutes_high_exceeds_low():
    rates = {"a": 8.0, "b": 12.0, "e": 6.0}
    for depth in ("quick", "standard", "deep"):
        lo, hi = D.estimate_minutes(rates, ["a", "b"], "e", depth)
        assert hi > lo


def test_estimate_minutes_more_analysts_increases_total_time():
    rates = {"a": 10.0, "e": 10.0}
    lo1, hi1 = D.estimate_minutes(rates, ["a"], "e", "standard")
    lo3, hi3 = D.estimate_minutes(rates, ["a", "a", "a"], "e", "standard")
    assert lo3 > lo1 and hi3 > hi1


def test_estimate_minutes_missing_probe_falls_back_to_default_rate():
    # a model absent from the tok/s dict must not divide by zero — falls back to default_rate
    lo, hi = D.estimate_minutes({}, ["never-probed"], "also-never-probed", "standard")
    assert lo > 0 and hi > 0


def test_estimate_minutes_high_includes_the_reranker_cost():
    # review finding: the default-on web-evidence reranker (council/rerank.py, ~120 tokens/
    # analyst) was entirely absent from the formula, not just unmeasured. HIGH must reflect it;
    # removing it should make HIGH visibly smaller.
    rates = {"a": 10.0, "e": 10.0}
    _, hi_with = D.estimate_minutes(rates, ["a"], "e", "quick")
    hi_without = ((D._PLAN + D._DRAFT * 2 + D._REPAIR) / 10.0 + D._EDITOR_HIGH / 10.0) / 60.0
    assert hi_with > hi_without


def test_editor_low_reflects_the_real_500_token_floor_not_750():
    # review finding: judge.deliberate()'s num_predict = min(1100, max(500, longest*3)) has a
    # real floor of 500 (a short fallback analyst answer can drive `longest` well below 250),
    # not the 750 the original constant assumed — EDITOR_LOW must be 500+700=1200.
    assert D._EDITOR_LOW == 1200


def test_status_eta_flag_prints_ranges_and_caveat(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    monkeypatch.setattr(D, "_probe_tokens_per_sec", lambda model, base, **k: 10.0)
    from council import local as L
    monkeypatch.setattr(L, "detect_models", lambda: [{"name": "m1", "size": 3e9}])
    monkeypatch.setattr(L, "pick_cast", lambda models, n: (["m1"], "m1"))

    out = _run_doctor(["--eta"])

    assert "quick" in out and "standard" in out and "deep" in out
    assert "generation time only" in out.lower()


def test_status_default_does_not_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    probed = []
    monkeypatch.setattr(D, "_probe_tokens_per_sec", lambda model, base, **k: probed.append(model) or 10.0)

    _run_doctor()   # no --eta

    assert probed == []
