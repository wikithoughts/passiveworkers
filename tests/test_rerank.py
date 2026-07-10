"""R36/D52 — shared listwise reranker (council/rerank.py) + the web-evidence hook in researcher.py.

Pure/deterministic: the single model call (council.ollama.generate) and the model-pick are stubbed,
so these tests pin the permutation guarantees and the researcher wiring, not model quality. No network,
no Ollama."""
import council.ollama as _ollama
import council.rerank as R


def _stub_model(monkeypatch, response):
    """Force a model name (so rerank doesn't short-circuit) and return `response` as the raw text."""
    monkeypatch.setattr(_ollama, "smallest_chat_model", lambda base_url="": "stub-model")
    monkeypatch.setattr(_ollama, "generate", lambda prompt, **kw: (response, 5))


# ----------------------------------------------------------------------------- rerank_listwise
def test_applies_model_order(monkeypatch):
    _stub_model(monkeypatch, '{"order":[2,0,1]}')
    assert R.rerank_listwise("q", ["alpha", "bravo", "charlie"]) == [2, 0, 1]


def test_appends_omitted_indices_never_drops(monkeypatch):
    # the model names only index 2 → the rest are appended in original order, nothing lost
    _stub_model(monkeypatch, '{"order":[2]}')
    assert R.rerank_listwise("q", ["a", "b", "c", "d"]) == [2, 0, 1, 3]


def test_clamps_out_of_range_and_dedups(monkeypatch):
    # 9 is out of range (dropped), duplicate 0 collapsed → [0,1], then append the omitted 2
    _stub_model(monkeypatch, '{"order":[9,0,0,1]}')
    assert R.rerank_listwise("q", ["a", "b", "c"]) == [0, 1, 2]


def test_identity_on_garbage_json(monkeypatch):
    _stub_model(monkeypatch, "not json at all")
    assert R.rerank_listwise("q", ["a", "b", "c"]) == [0, 1, 2]


def test_identity_when_model_raises(monkeypatch):
    monkeypatch.setattr(_ollama, "smallest_chat_model", lambda base_url="": "stub-model")

    def boom(prompt, **kw):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(_ollama, "generate", boom)
    assert R.rerank_listwise("q", ["a", "b", "c"]) == [0, 1, 2]


def test_identity_when_no_model_installed(monkeypatch):
    monkeypatch.setattr(_ollama, "smallest_chat_model", lambda base_url="": "")
    assert R.rerank_listwise("q", ["a", "b", "c"]) == [0, 1, 2]


def test_k_truncates(monkeypatch):
    _stub_model(monkeypatch, '{"order":[2,1,0]}')
    assert R.rerank_listwise("q", ["a", "b", "c"], k=2) == [2, 1]


def test_trivial_sizes_short_circuit(monkeypatch):
    _stub_model(monkeypatch, '{"order":[0]}')
    assert R.rerank_listwise("q", []) == []
    assert R.rerank_listwise("q", ["only"]) == [0]     # n<=1 never calls the model


# ----------------------------------------------------------------------------- researcher hook
def _wire_worker(monkeypatch, rows):
    import council.researcher as RW
    monkeypatch.setattr(RW.ResearchWorker, "_generate",
                        lambda self, prompt, num_predict: ('["q1"]', 3))
    monkeypatch.setattr(RW, "search_structured",
                        lambda q, max_results=4, engine="web": list(rows))
    monkeypatch.setattr(RW, "fetch_extract",
                        lambda url, max_chars=1500, with_date=False: ("", ""))
    return RW


ROWS = [
    {"title": "T1", "url": "https://a.test/1", "host": "a.test", "snippet": "s1"},
    {"title": "T2", "url": "https://b.test/2", "host": "b.test", "snippet": "s2"},
    {"title": "T3", "url": "https://c.test/3", "host": "c.test", "snippet": "s3"},
]


def test_nontemporal_brief_is_reranked(monkeypatch):
    RW = _wire_worker(monkeypatch, ROWS)
    import council.rerank as RK
    # reverse the evidence order so the effect is unmistakable
    monkeypatch.setattr(RK, "rerank_listwise", lambda q, passages, **kw: list(range(len(passages)))[::-1])
    monkeypatch.setenv("PW_RESEARCH_RERANK", "1")
    w = RW.ResearchWorker(worker_id="m", model="m", depth="quick", scope="web", page_evidence=False)
    out = w.research("the boiling point of water at standard sea-level pressure")   # non-temporal
    urls = [s["url"] for s in out["research"]["sources"]]
    assert urls == ["https://c.test/3", "https://b.test/2", "https://a.test/1"]


def test_temporal_brief_skips_rerank(monkeypatch):
    RW = _wire_worker(monkeypatch, ROWS)
    import council.rerank as RK
    called = {"n": 0}

    def flag(*a, **k):
        called["n"] += 1
        return [0]

    monkeypatch.setattr(RK, "rerank_listwise", flag)
    w = RW.ResearchWorker(worker_id="m", model="m", depth="quick", scope="web", page_evidence=False)
    w.research("the latest iPhone released this week")   # temporal → recency path owns ordering
    assert called["n"] == 0


def test_rerank_env_off_skips(monkeypatch):
    RW = _wire_worker(monkeypatch, ROWS)
    import council.rerank as RK
    called = {"n": 0}
    monkeypatch.setattr(RK, "rerank_listwise", lambda *a, **k: (called.__setitem__("n", called["n"] + 1) or [0]))
    monkeypatch.setenv("PW_RESEARCH_RERANK", "0")
    w = RW.ResearchWorker(worker_id="m", model="m", depth="quick", scope="web", page_evidence=False)
    w.research("the boiling point of water at standard sea-level pressure")
    assert called["n"] == 0
