"""Council-review correctness/security fixes (Track B):
- judge blind-order rotation was a no-op (`len % len == 0`); now content-derived + actually rotates.
- baseline _via_api shape guard (don't let a KeyError masquerade as "no baseline").
- record_feedback is asker-only (the demand metric must not be ballot-stuffable).
Pure logic + mocked boundaries (no network)."""
import pytest


# ----------------------------------------------------------------- judge blind-order rotation
def test_blind_order_is_a_valid_permutation():
    from council.judge import _blind_order
    for n in (0, 1, 2, 3, 5, 8, 13):
        assert sorted(_blind_order(n, "a question")) == list(range(n))


def test_blind_order_actually_rotates_not_a_noop():
    # regression: the old `len(answers) % max(1,len(answers))` was ALWAYS 0 → identity for every n.
    # A content-derived rotation must produce a non-identity order for at least some inputs.
    from council.judge import _blind_order
    seen = {tuple(_blind_order(6, f"seed-{i}")) for i in range(40)}
    assert len(seen) > 1, "rotation produced a single constant order — still a no-op"
    assert any(tuple(o) != tuple(range(6)) for o in seen), "rotation never leaves identity"


def test_blind_order_is_deterministic_and_stable():
    # same seed → same order every call (reproducible/testable, sha256 not the salted builtin hash)
    from council.judge import _blind_order
    assert _blind_order(7, "abc") == _blind_order(7, "abc")
    assert _blind_order(1, "abc") == [0] and _blind_order(0, "abc") == []


# ----------------------------------------------------------------- baseline API shape guard
class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _cfg(monkeypatch, bl):
    # CONFIG is a frozen dataclass — swap the whole reference for a stand-in (no per-field setattr)
    import types
    monkeypatch.setattr(bl, "CONFIG", types.SimpleNamespace(
        baseline_api_key="k", baseline_api_url="http://example/v1/chat",
        baseline_model="m", baseline_local_model=""))


def test_via_api_raises_on_malformed_body(monkeypatch):
    import council.net.baseline as bl
    _cfg(monkeypatch, bl)
    monkeypatch.setattr(bl.requests, "post", lambda *a, **k: _Resp({"error": "rate limited"}))
    with pytest.raises(RuntimeError):
        bl._via_api("q")
    # and the public entry swallows it (never fails the job) → None, no crash
    assert bl.generate_baseline("q") is None


def test_via_api_ok_on_well_formed_body(monkeypatch):
    import council.net.baseline as bl
    _cfg(monkeypatch, bl)
    monkeypatch.setattr(bl.requests, "post",
                        lambda *a, **k: _Resp({"choices": [{"message": {"content": " hi "}}]}))
    out = bl._via_api("q")
    assert out["text"] == "hi" and out["source"] == "api"


# ----------------------------------------------------------------- record_feedback asker-only
@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "fb.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "1000")
    import importlib
    import council.ledger as led
    importlib.reload(led)
    import council.net.config as cfg
    importlib.reload(cfg)
    import council.net.store as st
    importlib.reload(st)
    return st.Store()


def test_feedback_only_accepted_from_the_asker(store):
    j = store.create_job("alice", "do a task", job_type="assisted", requires={})
    jid = j["job_id"]
    assert store.record_feedback(jid, "council", who="alice") is True       # the asker may vote
    assert store.record_feedback(jid, "single", who="alice") is True        # and may change it
    assert store.record_feedback(jid, "council", who="mallory") is False    # nobody else may
    assert store.record_feedback(jid, "council", who="") is False           # anonymous may not
    assert store.record_feedback("no-such-job", "council", who="alice") is False
    assert store.record_feedback(jid, "bogus", who="alice") is False        # bad verdict rejected
