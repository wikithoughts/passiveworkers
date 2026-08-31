"""R26 — stage-level progress in ResearchWorker.research(). Private stage methods
(_plan_queries/_refine_queries/_draft/_repair_draft) are stubbed at fixed arity, same
pattern as tests/test_recursion.py — research()'s own on_progress wrapping must never
touch those signatures (several other test files monkeypatch them the same fixed way)."""
import passiveworkers.researcher as RW

BRIEF = "a stable non-temporal brief about basic chemistry facts"


def _worker(monkeypatch, scope="web", refine=None):
    monkeypatch.setattr(RW.ResearchWorker, "_plan_queries", lambda self, brief: ["seed"])
    monkeypatch.setattr(RW.ResearchWorker, "_refine_queries", refine or (lambda self, brief, ev: []))
    monkeypatch.setattr(RW, "search_structured",
                        lambda q, max_results=4, engine="web": [
                            {"title": "T", "url": "https://x.test/1", "host": "x.test", "snippet": "s"}])
    monkeypatch.setattr(RW, "fetch_extract", lambda url, max_chars=1500, with_date=False: ("", ""))
    monkeypatch.setattr(RW.ResearchWorker, "_draft",
                        lambda self, brief, evidence, local=None: ("draft [S1].", 5))
    monkeypatch.setattr(RW.ResearchWorker, "_repair_draft",
                        lambda self, brief, draft, ev, loc: draft)
    for k in ("PW_RESEARCH_MAX_ROUNDS", "PW_RESEARCH_DEADLINE", "PW_RESEARCH_MAX_SOURCES"):
        monkeypatch.delenv(k, raising=False)
    return RW.ResearchWorker(worker_id="m", model="m", depth="standard", scope=scope,
                             page_evidence=True)


def test_research_emits_stage_messages_in_order(monkeypatch):
    def refine(self, brief, evidence):
        return [] if refine.calls else ["one more"]
    refine.calls = 0

    def refine_once(self, brief, evidence):
        refine.calls += 1
        return [] if refine.calls > 1 else ["one more"]

    w = _worker(monkeypatch, refine=refine_once)
    messages = []
    w.research(BRIEF, on_progress=messages.append)
    joined = " || ".join(messages)
    # every stage substring present, in the right relative order
    stages = ["planning search queries", "searching the web", "refining search",
             "fetching", "drafting findings", "checking citation fidelity"]
    positions = [joined.find(s) for s in stages]
    assert all(p != -1 for p in positions), messages
    assert positions == sorted(positions), messages


def test_research_progress_callback_exception_does_not_crash(monkeypatch):
    w = _worker(monkeypatch)

    def bad_callback(msg):
        raise RuntimeError("a broken progress sink")

    out = w.research(BRIEF, on_progress=bad_callback)   # must not raise
    assert out["text"]


def test_research_without_on_progress_still_works(monkeypatch):
    # backward-compat: passiveworkers/net/agent.py and scripts/eval_citation_fidelity.py call
    # .research(question) with no on_progress kwarg at all.
    w = _worker(monkeypatch)
    out = w.research(BRIEF)
    assert out["text"]


def test_research_scope_local_emits_library_stage_not_web_stages(monkeypatch):
    w = _worker(monkeypatch, scope="local")
    messages = []
    w.research(BRIEF, on_progress=messages.append)
    joined = " || ".join(messages)
    assert "searching your library" in joined
    assert "planning search queries" not in joined
    assert "fetching" not in joined
