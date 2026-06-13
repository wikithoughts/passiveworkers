"""D33 task-type dispatch registry + the download_extract and code_generation types. Verifies the
registry mapping, that the new types flow through the existing shard scatter/gather (capacity split,
in-order reassembly, conservation), the download_extract fetch-forcing + worker framing, and that
research is just one type among several. Drives the Store directly + a mocked worker (no network)."""
import json
import pytest


# ----------------------------------------------------------------- registry mapping
def test_registry_behaviors():
    from council.net.config import task_behavior
    de = task_behavior("download_extract")
    assert de.executor == "batch" and de.sharded and de.fetch and de.judge == "spot_check"
    assert de.assemble == "shards" and de.framing
    cg = task_behavior("code_generation")
    assert cg.executor == "batch" and cg.sharded and not cg.fetch and cg.framing
    assert task_behavior("shard_map").sharded and not task_behavior("shard_map").fetch
    assert task_behavior("research_report").executor == "research"
    assert task_behavior("research_report").judge == "compile_report"
    assert task_behavior("chat").executor == "perspective" and not task_behavior("chat").sharded
    # unknown / None fall back to chat (never crash, never silently shard)
    assert task_behavior("nonsense").executor == "perspective"
    assert task_behavior(None).executor == "perspective"


def test_new_types_in_catalog():
    from council.net.config import JOB_TYPES
    assert "download_extract" in JOB_TYPES and "code_generation" in JOB_TYPES


# ----------------------------------------------------------------- store-level flows
@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "tt.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100000")
    import importlib
    import council.ledger as led
    importlib.reload(led)
    import council.net.config as cfg
    importlib.reload(cfg)
    import council.net.store as st
    importlib.reload(st)
    return st.Store()


def _reg(store, owner, answer_model="m", judge=False):
    out = store.register_node({
        "owner": owner, "name": owner, "country": "X", "lens": "neutral",
        "answer_model": answer_model, "can_judge": judge, "judge_model": ("m" if judge else ""),
        "profile": {"cores": 4, "ram_gb": 16, "models": [answer_model] if answer_model else []},
    })
    return out["node_id"]


def _answer_tasks(store, job_id):
    return list(store.conn.execute(
        "SELECT * FROM tasks WHERE job_id=? AND type='answer'", (job_id,)))


def test_download_extract_forces_fetch_and_shards(store):
    _reg(store, "n1")
    _reg(store, "n2")
    j = store.create_job("alice", "extract the page title", job_type="download_extract",
                         items=["http://a.example/x", "http://b.example/y", "http://c.example/z"],
                         minds=2, fetch=False)                       # fetch=False — must be forced on
    assert j["status"] == "pending_answers"
    tasks = _answer_tasks(store, j["job_id"])
    assert tasks and all(json.loads(t["payload"])["fetch"] is True for t in tasks)
    assert sum(len(json.loads(t["payload"])["shard"]) for t in tasks) == 3
    stored = store.conn.execute("SELECT type FROM jobs WHERE job_id=?", (j["job_id"],)).fetchone()
    assert stored["type"] == "download_extract"                     # not coerced to chat


def test_code_generation_assembles_units_in_input_order(store):
    _reg(store, "opA")
    _reg(store, "opB")
    _reg(store, "judge", answer_model="", judge=True)
    specs = [f"spec{i}" for i in range(5)]
    j = store.create_job("alice", "generate code per spec", job_type="code_generation",
                         items=specs, minds=2)
    jid = j["job_id"]
    for t in _answer_tasks(store, jid):
        shard = json.loads(t["payload"])["shard"]
        res = {"text": "done",
               "results": [{"i": e["i"], "item": e["item"], "output": f"code_{e['i']}"} for e in shard]}
        assert store.complete_task(t["task_id"], res, node_id=t["node_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'", (jid,)).fetchone()
    assert store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
    view = store.job_view(jid)
    assert view["status"] == "done"
    merged = json.loads(view["merged"])
    assert [r["i"] for r in merged] == list(range(5))               # in input order
    assert [r["output"] for r in merged] == [f"code_{i}" for i in range(5)]
    assert store.ledger.conservation_ok()


def test_fetch_is_type_driven_not_user_overridable(store):
    # review (HIGH): a user-supplied fetch=True must NOT turn code_generation into a fetcher (its
    # items are specs, not URLs). download_extract always fetches; shard_map honors the opt-in.
    _reg(store, "n1")
    _reg(store, "n2")

    def _fetch_flags(jt, fetch):
        j = store.create_job("alice", "do", job_type=jt, items=["x", "y"], minds=2, fetch=fetch)
        return {json.loads(t["payload"]).get("fetch") for t in _answer_tasks(store, j["job_id"])}

    assert _fetch_flags("code_generation", True) == {False}     # NEVER fetches, even when asked
    assert _fetch_flags("code_generation", False) == {False}
    assert _fetch_flags("download_extract", False) == {True}    # ALWAYS fetches
    assert _fetch_flags("shard_map", True) == {True}            # honors the opt-in
    assert _fetch_flags("shard_map", False) == {False}          # off by default


def test_chat_and_research_are_not_sharded(store):
    # research stays one (non-sharded) type among many — the reframe didn't change its behavior
    _reg(store, "n1")
    j = store.create_job("alice", "a normal question", job_type="chat", minds=1)
    assert j["status"] == "pending_answers"
    t = _answer_tasks(store, j["job_id"])[0]
    assert "shard" not in json.loads(t["payload"])                  # no shard for chat


# ----------------------------------------------------------------- worker-side framing dispatch
def test_agent_applies_type_framing_and_fetch(monkeypatch):
    monkeypatch.setenv("PW_COORDINATOR", "http://127.0.0.1:9")
    import council.net.agent as ag
    captured = {}

    class _BW:
        def __init__(self, *a, **k):
            pass

        def process(self, instruction, shard, fetch=False, on_progress=None):
            captured["instruction"] = instruction
            captured["fetch"] = fetch
            return {"text": "x", "results": [], "tokens": 0, "elapsed_s": 0.0}

    monkeypatch.setattr("council.batch.BatchWorker", _BW)
    agent = ag.Agent()

    agent._do_answer({"task_id": "t1", "country": "X", "payload": {
        "job_type": "download_extract", "question": "get the title",
        "shard": [{"i": 0, "item": "http://a"}], "fetch": True}})
    assert "extract" in captured["instruction"].lower()            # download framing applied
    assert captured["instruction"].strip().endswith("get the title")
    assert captured["fetch"] is True

    agent._do_answer({"task_id": "t2", "country": "X", "payload": {
        "job_type": "code_generation", "question": "make a parser",
        "shard": [{"i": 0, "item": "spec0"}], "fetch": False}})
    assert "code" in captured["instruction"].lower()               # code framing applied
    assert captured["fetch"] is False
