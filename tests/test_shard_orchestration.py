"""D32 shard orchestration: capacity-weighted + user-specified split sizing, and in-input-order
reassembly of the assembled deliverable. Drives the Store directly (no network)."""
import json
import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "shard.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100000")
    import importlib
    import council.ledger as led
    importlib.reload(led)
    import council.net.config as cfg
    importlib.reload(cfg)
    import council.net.store as st
    importlib.reload(st)
    return st.Store()


def _reg(store, owner, answer_model="m", judge=False, cores=4, ram=16):
    out = store.register_node({
        "owner": owner, "name": owner, "country": "X", "lens": "neutral",
        "answer_model": answer_model, "can_judge": judge,
        "judge_model": ("m" if judge else ""),
        "profile": {"cores": cores, "ram_gb": ram, "models": [answer_model] if answer_model else []},
    })
    return out["node_id"]


def _answer_tasks(store, job_id):
    return list(store.conn.execute(
        "SELECT * FROM tasks WHERE job_id=? AND type='answer'", (job_id,)))


def _shard_sizes(store, job_id):
    return {t["node_id"]: len(json.loads(t["payload"])["shard"]) for t in _answer_tasks(store, job_id)}


# ---------------------------------------------------------------- capacity-weighted split
def test_capacity_weighted_split_gives_the_bigger_node_more(store):
    big = _reg(store, "big", cores=8, ram=32)
    small = _reg(store, "small", cores=1, ram=8)
    j = store.create_job("alice", "summarize each", job_type="shard_map",
                         items=[f"item{i}" for i in range(10)], minds=2)
    sizes = _shard_sizes(store, j["job_id"])
    assert sum(sizes.values()) == 10                 # every item assigned exactly once
    assert sizes[big] > sizes[small]                 # capacity drove the sizing


def test_user_split_overrides_capacity(store):
    _reg(store, "n1", cores=4)
    _reg(store, "n2", cores=4)
    j = store.create_job("alice", "do", job_type="shard_map",
                         items=[f"i{k}" for k in range(10)], minds=2, split=[4, 1])
    sizes = sorted(_shard_sizes(store, j["job_id"]).values())
    assert sizes == [2, 8]                            # 4:1 of 10 items, exact


def test_never_more_workers_than_items(store):
    for k in range(5):
        _reg(store, f"n{k}", cores=4)
    j = store.create_job("alice", "do", job_type="shard_map", items=["only", "two"], minds=5)
    tasks = _answer_tasks(store, j["job_id"])
    assert len(tasks) == 2                            # trimmed to item count — no idle nodes
    assert sum(len(json.loads(t["payload"])["shard"]) for t in tasks) == 2


# ---------------------------------------------------------------- in-order reassembly
def test_assembled_deliverable_is_in_input_order(store):
    _reg(store, "opA", cores=4)
    _reg(store, "opB", cores=4)
    _reg(store, "judge", answer_model="", judge=True)        # judge-only (not an answer candidate)
    items = [f"item{i}" for i in range(6)]
    j = store.create_job("alice", "echo", job_type="shard_map", items=items, minds=2)
    jid = j["job_id"]
    # each worker echoes its shard's items, tagged by global index
    for t in _answer_tasks(store, jid):
        shard = json.loads(t["payload"])["shard"]
        res = {"text": "done",
               "results": [{"i": e["i"], "item": e["item"], "output": f"OUT-{e['i']}"} for e in shard]}
        assert store.complete_task(t["task_id"], res, node_id=t["node_id"])
    # the judge task now exists — complete it to settle
    jt = store.conn.execute(
        "SELECT * FROM tasks WHERE job_id=? AND type='judge'", (jid,)).fetchone()
    assert store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
    view = store.job_view(jid)
    assert view["status"] == "done"
    merged = json.loads(view["merged"])
    assert [r["i"] for r in merged] == list(range(6))            # input order preserved
    assert [r["output"] for r in merged] == [f"OUT-{i}" for i in range(6)]
    assert store.ledger.conservation_ok()


# ---------------------------------------------------------------- multi-producer file reassembly (D38)
def test_as_file_delivers_one_reassemblable_signed_file(store, tmp_path):
    from council.artifacts import read_artifact, reassemble, verify_manifest
    _reg(store, "opA")
    _reg(store, "opB")
    _reg(store, "judge", answer_model="", judge=True)
    j = store.create_job("alice", "generate code", job_type="code_generation",
                         items=[f"spec{i}" for i in range(4)], minds=2, as_file=True)
    jid = j["job_id"]
    for t in _answer_tasks(store, jid):
        shard = json.loads(t["payload"])["shard"]
        res = {"text": "done",
               "results": [{"i": e["i"], "item": e["item"], "output": f"code_{e['i']}"} for e in shard]}
        store.complete_task(t["task_id"], res, node_id=t["node_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'", (jid,)).fetchone()
    store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
    view = store.job_view(jid)
    assert view["status"] == "done"
    manifest = read_artifact(view["merged"])              # the deliverable is a file artifact, not JSON
    assert manifest is not None and verify_manifest(manifest)
    # the asker reassembles it from the per-job blob store, integrity-verified, in input order
    dest = reassemble(manifest, lambda h: store.get_blob(jid, h), str(tmp_path))
    assert dest.read_text() == "\n\n".join(f"code_{i}" for i in range(4))
    assert store.ledger.conservation_ok()


def test_without_as_file_deliverable_stays_json(store):
    _reg(store, "opA")
    _reg(store, "judge", answer_model="", judge=True)
    j = store.create_job("alice", "do", job_type="shard_map", items=["a", "b"], minds=1)  # no as_file
    jid = j["job_id"]
    for t in _answer_tasks(store, jid):
        shard = json.loads(t["payload"])["shard"]
        store.complete_task(t["task_id"], {"results": [{"i": e["i"], "item": e["item"], "output": "x"}
                                                       for e in shard]}, node_id=t["node_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'", (jid,)).fetchone()
    store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
    from council.artifacts import read_artifact
    assert read_artifact(store.job_view(jid)["merged"]) is None   # plain JSON, not a file artifact


# ---------------------------------------------------------------- malformed shard index (review: HIGH)
def test_settle_tolerates_mixed_index_types(store):
    # a worker returning a STRING 'i' must not crash _settle's sort (→ conservation break) — review
    _reg(store, "opA")
    _reg(store, "judge", answer_model="", judge=True)
    j = store.create_job("alice", "do", job_type="shard_map", items=["a", "b"], minds=1)
    jid = j["job_id"]
    t = _answer_tasks(store, jid)[0]
    res = {"results": [{"i": "1", "item": "b", "output": "B"},   # string index
                       {"i": 0, "item": "a", "output": "A"}]}     # int index
    assert store.complete_task(t["task_id"], res, node_id=t["node_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'", (jid,)).fetchone()
    assert store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
    view = store.job_view(jid)
    assert view["status"] == "done"                              # did NOT crash
    assert [r["output"] for r in json.loads(view["merged"])] == ["A", "B"]   # sorted 0 then "1"
    assert store.ledger.conservation_ok()


def test_settle_tolerates_infinity_index(store):
    # review (CRITICAL): int(float('inf')) raises OverflowError (not Value/TypeError) — the sort
    # coercion must catch it too, or a worker returning i=inf crashes settle / breaks conservation.
    _reg(store, "opA")
    _reg(store, "judge", answer_model="", judge=True)
    j = store.create_job("alice", "do", job_type="shard_map", items=["a", "b"], minds=1)
    jid = j["job_id"]
    t = _answer_tasks(store, jid)[0]
    res = {"results": [{"i": float("inf"), "item": "b", "output": "B"},
                       {"i": 0, "item": "a", "output": "A"}]}
    assert store.complete_task(t["task_id"], res, node_id=t["node_id"])
    jt = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'", (jid,)).fetchone()
    assert store.complete_task(jt["task_id"], {"scores": {}}, node_id=jt["node_id"])
    assert store.job_view(jid)["status"] == "done"      # inf index did NOT crash settle
    assert store.ledger.conservation_ok()


# ---------------------------------------------------------------- malformed split (review: critical)
def test_non_finite_split_does_not_crash_falls_back_to_capacity(store):
    _reg(store, "n1", cores=4)
    _reg(store, "n2", cores=4)
    # inf/nan weights bypass a naive `w > 0` check but would crash _apportion's int(raw) — they must
    # be rejected and fall back to capacity weighting, never erroring the job (review).
    for bad in ([float("inf"), 1.0], [float("nan"), 1.0], [1.0, -float("inf")]):
        j = store.create_job("alice", "do", job_type="shard_map",
                             items=[f"i{k}" for k in range(6)], minds=2, split=bad)
        assert j["status"] == "pending_answers"
        assert sum(_shard_sizes(store, j["job_id"]).values()) == 6


# ---------------------------------------------------------------- apportionment unit
def test_apportion_sums_exactly_and_is_nonnegative(store):
    ap = store._apportion
    assert sum(ap(10, [1, 1, 1])) == 10
    assert sum(ap(7, [3, 1])) == 7
    assert ap(0, [1, 2]) == [0, 0]
    assert all(c >= 0 for c in ap(100, [5, 3, 2, 0.0001]))
    assert sum(ap(100, [5, 3, 2, 1])) == 100
