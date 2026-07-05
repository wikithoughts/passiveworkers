"""R17/D29 performance & quality: dynamic source routing, Ollama keep-alive on every model
call, and merge-prompt citation preservation. Pure logic + mocked boundaries (no Ollama/network)."""
import council.research as R


# ----------------------------------------------------------------------------- source routing
def test_route_plain_query_is_web_only():
    assert R.route_engines("best pizza near me tonight") == ["web"]


def test_route_adds_academic_on_research_signals():
    assert R.route_engines("recent deep learning benchmark for protein folding") == ["web", "academic"]
    assert "academic" in R.route_engines("peer-reviewed studies on statin side effects")


def test_route_adds_encyclopedic_on_definitional_signals():
    assert R.route_engines("what is the capital of Australia") == ["web", "encyclopedic"]
    assert "encyclopedic" in R.route_engines("history of the Ottoman Empire")


def test_route_can_add_both_and_always_keeps_web_first():
    eng = R.route_engines("what is a transformer neural network in machine learning")
    assert eng[0] == "web" and "academic" in eng and "encyclopedic" in eng


def test_route_respects_off_switch(monkeypatch):
    monkeypatch.setenv("PW_SOURCE_ROUTING", "off")
    assert R.route_engines("what is the definition of a peer-reviewed study") == ["web"]


def test_router_is_wired_into_researcher(monkeypatch):
    """An academic-signalled query must actually reach search_structured with engine='academic'."""
    import council.researcher as RW
    engines_seen = []

    monkeypatch.setattr(RW.ResearchWorker, "_generate",
                        lambda self, prompt, num_predict: ('["deep learning benchmark study","x","y"]', 3))

    def fake_search(q, max_results=5, engine="web"):
        engines_seen.append(engine)
        return [{"title": f"T-{engine}", "url": f"https://{engine}.test/x", "host": f"{engine}.test",
                 "snippet": "snippet"}]

    monkeypatch.setattr(RW, "search_structured", fake_search)
    monkeypatch.setattr(RW, "fetch_extract", lambda url, max_chars=1500, with_date=False: ("page", ""))
    w = RW.ResearchWorker(worker_id="m", model="m", depth="quick", scope="web", page_evidence=False)
    w.research("a brief")
    assert "web" in engines_seen and "academic" in engines_seen   # routing augmented web with arXiv


# ----------------------------------------------------------------------------- Ollama keep-alive
class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def test_worker_sends_keep_alive(monkeypatch):
    import council.ollama as O   # the POST now lives in the one shared Ollama client (D48)
    import council.worker as W
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return _FakeResp({"response": "ok", "eval_count": 2})

    monkeypatch.setattr(O.requests, "post", fake_post)
    W.PerspectiveWorker(worker_id="w", model="m").answer("q?")
    assert "keep_alive" in captured["body"] and captured["body"]["keep_alive"]


def test_keep_alive_is_env_overridable(monkeypatch):
    import council.ollama as O
    import council.researcher as RW
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return _FakeResp({"response": "x", "eval_count": 1})

    monkeypatch.setenv("PW_OLLAMA_KEEP_ALIVE", "0")
    monkeypatch.setattr(O.requests, "post", fake_post)
    RW.ResearchWorker(worker_id="m", model="m")._generate("p", num_predict=10)
    assert captured["body"]["keep_alive"] == "0"


def test_judge_sends_keep_alive(monkeypatch):
    import council.judge as J
    import council.ollama as O
    captured = {}
    monkeypatch.setattr(O.requests, "post",
                        lambda url, json=None, timeout=None: captured.update(body=json) or _FakeResp({"response": "{}"}))
    J.Judge(model="m")._generate("p")
    assert "keep_alive" in captured["body"]


def test_batch_worker_sends_keep_alive(monkeypatch):
    import council.batch as B
    import council.ollama as O
    captured = {}
    monkeypatch.setattr(O.requests, "post",
                        lambda url, json=None, timeout=None: captured.update(body=json) or _FakeResp({"response": "x", "eval_count": 1}))
    B.BatchWorker(worker_id="w", model="m")._generate("p")
    assert "keep_alive" in captured["body"]            # review R17-001: batch was missed in the first cut


def test_pw_ollama_base_reaches_every_generation_call(monkeypatch):
    # D48 regression: PW_OLLAMA_BASE must reach the ANALYST/EDITOR/WORKER/BATCH generation calls,
    # not just model detection. Before the shared client these hardcoded localhost, so pointing at a
    # GPU box listed its models then failed every generation against localhost.
    import council.batch as B
    import council.judge as J
    import council.ollama as O
    import council.researcher as RW
    import council.worker as W
    monkeypatch.setenv("PW_OLLAMA_BASE", "http://gpu-box:11434")
    urls = []
    monkeypatch.setattr(O.requests, "post",
                        lambda url, json=None, timeout=None: urls.append(url) or _FakeResp({"response": "x", "eval_count": 1}))
    RW.ResearchWorker(worker_id="m", model="m")._generate("p", num_predict=5)
    J.Judge(model="m")._generate("p")
    W.PerspectiveWorker(worker_id="w", model="m").answer("q?")
    B.BatchWorker(worker_id="w", model="m")._generate("p")
    assert len(urls) == 4
    assert all(u == "http://gpu-box:11434/api/generate" for u in urls)


def test_baseline_ollama_sends_keep_alive(monkeypatch):
    import council.net.baseline as BL
    captured = {}
    monkeypatch.setattr(BL.requests, "post",
                        lambda url, json=None, timeout=None: captured.update(body=json) or _FakeResp({"response": "ok"}))
    BL._via_ollama("q?")
    assert "keep_alive" in captured["body"]            # review R17-002: baseline was missed in the first cut


# ----------------------------------------------------------------------------- merge citation preservation
def test_merge_prompt_preserves_citations(monkeypatch):
    from council.judge import Judge
    from council.worker import Answer
    captured = {}
    monkeypatch.setattr(Judge, "_generate",
                        lambda self, prompt, num_predict=None: captured.update(p=prompt) or "merged")
    j = Judge(model="m")
    j.merge("q", [Answer("a", "m", "x", "c", "one [S1]", 1, 0.1),
                  Answer("b", "m", "y", "c", "two [S2]", 1, 0.1)])
    assert "[S#]" in captured["p"] and "never renumber" in captured["p"].lower()


def test_deliberate_prompt_preserves_citations(monkeypatch):
    from council.judge import Judge
    from council.worker import Answer
    captured = {}
    monkeypatch.setattr(Judge, "_generate",
                        lambda self, prompt, num_predict=None: captured.update(p=prompt) or '{"merge":"m"}')
    j = Judge(model="m")
    j.deliberate("q", [Answer("a", "m", "x", "c", "one [S1]", 1, 0.1)])
    assert "[S#]" in captured["p"]


def test_merge_drops_invented_citation_markers(monkeypatch):
    # review CITATION_MERGE_001: even if the model ignores the prompt rule, an INVENTED marker
    # (not present in any source answer) must never survive into the merged output.
    from council.judge import Judge
    from council.worker import Answer
    monkeypatch.setattr(Judge, "_generate",
                        lambda self, prompt, num_predict=None: "Claim grounded [S1] but fabricated [S9].")
    j = Judge(model="m")
    out = j.merge("q", [Answer("a", "m", "x", "c", "fact [S1]", 1, 0.1),
                        Answer("b", "m", "y", "c", "other [S2]", 1, 0.1)])
    assert "[S1]" in out and "[S9]" not in out         # known kept, invented dropped


def test_known_markers_preserved_in_group(monkeypatch):
    from council.judge import _drop_invented_markers
    # in a grouped cite, drop only the invented member; keep the real one
    assert _drop_invented_markers("x [S1, S9] y", {"S1"}) == "x [S1] y"
    assert _drop_invented_markers("all bogus [S8, S9]", {"S1"}) == "all bogus "


def test_search_structured_engine_defaults_to_web():
    import inspect
    sig = inspect.signature(R.search_structured)
    assert sig.parameters["engine"].default == "web"   # backward compatibility for old callers
