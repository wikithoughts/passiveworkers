"""R36/D52 — adaptive gap-driven deepening in ResearchWorker.research().

Pins the bounded refine loop's stop conditions (max rounds, wall-clock deadline, source ceiling,
model-says-done, search-saturated) deterministically — _refine_queries / search / draft are stubbed,
so no network, no Ollama."""
import council.researcher as RW


def _worker(monkeypatch, depth, refine_fn, search_fn):
    monkeypatch.setattr(RW.ResearchWorker, "_plan_queries", lambda self, brief: ["seed"])
    monkeypatch.setattr(RW.ResearchWorker, "_refine_queries", refine_fn)
    monkeypatch.setattr(RW, "search_structured", search_fn)
    monkeypatch.setattr(RW, "fetch_extract", lambda url, max_chars=1500, with_date=False: ("", ""))
    monkeypatch.setattr(RW.ResearchWorker, "_draft", lambda self, brief, evidence, local=None: ("draft.", 1))
    monkeypatch.setattr(RW.ResearchWorker, "_repair_draft", lambda self, brief, draft, ev, loc: draft)
    for k in ("PW_RESEARCH_MAX_ROUNDS", "PW_RESEARCH_DEADLINE", "PW_RESEARCH_MAX_SOURCES"):
        monkeypatch.delenv(k, raising=False)
    return RW.ResearchWorker(worker_id="m", model="m", depth=depth, scope="web", page_evidence=False)


BRIEF = "a stable non-temporal brief about basic chemistry facts"   # non-breaking → depth unchanged


def _always_refine(calls):
    def refine(self, brief, evidence):
        calls["r"] += 1
        return ["a distinct follow-up query"]
    return refine


def _unique_search(calls):
    def search(q, max_results=4, engine="web"):
        calls["s"] += 1
        return [{"title": f"T{calls['s']}", "url": f"https://x.test/{calls['s']}",
                 "host": "x.test", "snippet": "s"}]     # a fresh url every call → always new evidence
    return search


def test_stops_at_max_rounds_standard(monkeypatch):
    calls = {"r": 0, "s": 0}
    w = _worker(monkeypatch, "standard", _always_refine(calls), _unique_search(calls))
    w.research(BRIEF)
    assert calls["r"] == 2                      # standard default: up to 2 adaptive rounds


def test_stops_at_max_rounds_deep(monkeypatch):
    calls = {"r": 0, "s": 0}
    w = _worker(monkeypatch, "deep", _always_refine(calls), _unique_search(calls))
    w.research(BRIEF)
    assert calls["r"] == 4                      # deep default: up to 4


def test_env_override_caps_rounds(monkeypatch):
    calls = {"r": 0, "s": 0}
    w = _worker(monkeypatch, "deep", _always_refine(calls), _unique_search(calls))
    monkeypatch.setenv("PW_RESEARCH_MAX_ROUNDS", "1")
    w.research(BRIEF)
    assert calls["r"] == 1


def test_stops_when_model_says_done(monkeypatch):
    calls = {"r": 0, "s": 0}

    def refine(self, brief, evidence):
        calls["r"] += 1
        return [] if calls["r"] >= 2 else ["one more query"]     # empty list on the 2nd ask → stop

    w = _worker(monkeypatch, "deep", refine, _unique_search(calls))     # deep allows 4, but…
    w.research(BRIEF)
    assert calls["r"] == 2                      # round 1 collected, round 2's [] breaks before collecting


def test_stops_when_search_saturated(monkeypatch):
    calls = {"r": 0, "s": 0}

    def same_search(q, max_results=4, engine="web"):
        calls["s"] += 1
        return [{"title": "T", "url": "https://same.test/1", "host": "same.test", "snippet": "s"}]

    w = _worker(monkeypatch, "deep", _always_refine(calls), same_search)
    w.research(BRIEF)
    # plan seeds the one url; round 1 finds nothing new → break. refine asked exactly once.
    assert calls["r"] == 1


def test_respects_deadline(monkeypatch):
    calls = {"r": 0, "s": 0}
    w = _worker(monkeypatch, "deep", _always_refine(calls), _unique_search(calls))
    monkeypatch.setenv("PW_RESEARCH_DEADLINE", "0")      # deadline = t0 → loop never enters
    w.research(BRIEF)
    assert calls["r"] == 0


def test_respects_max_sources(monkeypatch):
    calls = {"r": 0, "s": 0}
    w = _worker(monkeypatch, "deep", _always_refine(calls), _unique_search(calls))
    monkeypatch.setenv("PW_RESEARCH_MAX_SOURCES", "1")   # plan already seeds ≥1 → loop guard trips
    w.research(BRIEF)
    assert calls["r"] == 0


def test_deadline_measured_from_post_plan_not_process_start(monkeypatch):
    # R16 review: the deadline used to be anchored at t0 (top of research(), before plan+first
    # search even ran) — a slow plan phase could burn most of the budget before refining starts.
    # Simulate a slow plan+first-search phase (250s, deliberately > the 240s default budget) by
    # jumping the SECOND time.monotonic() call (refine_start, captured right after plan+search) —
    # under the OLD t0-anchored code this alone would already exceed a 240s deadline before the
    # loop even starts; under the FIXED code the budget is re-anchored from there, so refining
    # still gets its full window.
    calls = {"r": 0, "s": 0}
    state = {"n": 0}

    def fake_monotonic():
        state["n"] += 1
        if state["n"] == 1:
            return 0.0            # t0
        if state["n"] == 2:
            return 250.0          # refine_start — simulates a slow plan+first-search phase
        return 250.0 + (state["n"] - 2) * 0.01

    monkeypatch.setattr(RW.time, "monotonic", fake_monotonic)
    w = _worker(monkeypatch, "deep", _always_refine(calls), _unique_search(calls))
    monkeypatch.setenv("PW_RESEARCH_DEADLINE", "240")
    out = w.research(BRIEF)
    assert calls["r"] > 0                    # refine still got its full window, not starved by t0
    assert out["research"]["deadline_hit"] is False


def test_deadline_hit_sets_flag_and_stops_refining(monkeypatch):
    calls = {"r": 0, "s": 0}
    w = _worker(monkeypatch, "deep", _always_refine(calls), _unique_search(calls))
    monkeypatch.setenv("PW_RESEARCH_DEADLINE", "0")
    out = w.research(BRIEF)
    assert calls["r"] == 0
    assert out["research"]["deadline_hit"] is True
    assert out["research"]["refine_rounds"] == 0
    assert out["research"]["refine_rounds_target"] == 4      # deep default


def test_no_deadline_hit_when_refine_stops_naturally(monkeypatch):
    calls = {"r": 0, "s": 0}

    def refine(self, brief, evidence):
        calls["r"] += 1
        return []   # model says covered on the first ask

    w = _worker(monkeypatch, "deep", refine, _unique_search(calls))
    out = w.research(BRIEF)
    assert out["research"]["deadline_hit"] is False


def test_blank_or_garbage_budget_env_falls_back_not_crash(monkeypatch):
    # R36 review: an empty/non-numeric PW_RESEARCH_* must fall back to the default, never raise mid-run.
    calls = {"r": 0, "s": 0}
    w = _worker(monkeypatch, "standard", _always_refine(calls), _unique_search(calls))
    monkeypatch.setenv("PW_RESEARCH_MAX_ROUNDS", "")        # blank
    monkeypatch.setenv("PW_RESEARCH_DEADLINE", "soon")      # garbage
    w.research(BRIEF)                                        # must not raise
    assert calls["r"] == 2                                  # fell back to the standard default (2 rounds)
