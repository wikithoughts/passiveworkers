"""Assisted task lifecycle (D21): open offer → consent/accept → deliver → conserved settle."""

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "a.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")   # balance asserts below assume 100
    # fresh import of ledger/config/store bound to this env (STARTER_ALLOWANCE is read at import)
    import importlib
    import passiveworkers.ledger as led
    importlib.reload(led)
    import passiveworkers.net.config as cfg
    importlib.reload(cfg)
    import passiveworkers.net.store as st
    importlib.reload(st)
    return st.Store()


def _offer(store, asker="alice", ctx="ctx", requires=None):
    return store.create_job(asker, "Do a task a human handles", job_type="assisted",
                            context=ctx, requires=requires or {})


def test_create_assisted_is_open_offer(store):
    j = _offer(store)
    assert j["status"] == "pending_assist" and j["price"] > 0
    offers = store.assisted_offers({"profile": "{}", "answer_model": "", "owner": "bob"})
    assert len(offers) == 1 and offers[0]["brief"]


def test_accept_then_deliver_conserves_ledger(store):
    j = _offer(store)
    tid = store.assisted_offers({"profile": "{}", "answer_model": "", "owner": "bob"})[0]["task_id"]
    assert store.accept_assisted(tid, "bobnode", "bob")["ok"]
    pre = sum(a.balance for a in store.ledger.accounts.values())
    assert store.deliver_assisted(tid, "bobnode", "done: /out/x.png")["ok"]
    post = sum(a.balance for a in store.ledger.accounts.values())
    assert abs(pre - post) < 1e-6 and store.ledger.conservation_ok()
    assert store.ledger.accounts["alice"].balance == 50.0      # debited the pool
    assert store.ledger.accounts["bob"].balance == 150.0       # starter 100 + earned 50


def test_double_accept_blocked(store):
    j = _offer(store)
    tid = store.assisted_offers({"profile": "{}", "answer_model": "", "owner": "bob"})[0]["task_id"]
    assert store.accept_assisted(tid, "n1", "bob")["ok"]
    assert not store.accept_assisted(tid, "n2", "eve")["ok"]   # already claimed


def test_only_claimer_can_deliver(store):
    j = _offer(store)
    tid = store.assisted_offers({"profile": "{}", "answer_model": "", "owner": "bob"})[0]["task_id"]
    store.accept_assisted(tid, "bobnode", "bob")
    assert not store.deliver_assisted(tid, "evilnode", "hijack")["ok"]


def test_self_deal_blocked(store):
    j = _offer(store, asker="alice")
    tid = store.assisted_offers({"profile": "{}", "answer_model": "", "owner": "alice"})[0]["task_id"]
    assert not store.accept_assisted(tid, "alicenode", "alice")["ok"]   # can't do your own offer


def test_helped_counted_once(store):
    _offer(store)
    tid = store.assisted_offers({"profile": "{}", "answer_model": "", "owner": "bob"})[0]["task_id"]
    store.accept_assisted(tid, "bobnode", "bob")
    store.deliver_assisted(tid, "bobnode", "done")
    assert store.ledger.accounts["bob"].jobs_helped == 1   # not 3


def test_expiry_after_accept_refunds_and_blocks_late_deliver(store, monkeypatch):
    # _reap_once/_reap_loop live in _store_jobs (R37 mixin split) — that's the module whose
    # `_now` the reaper actually reads, so that's what needs patching (not passiveworkers.net.store).
    import passiveworkers.net._store_jobs as st
    j = _offer(store, asker="alice")
    tid = store.assisted_offers({"profile": "{}", "answer_model": "", "owner": "bob"})[0]["task_id"]
    store.accept_assisted(tid, "bobnode", "bob")
    assert store.ledger.accounts["alice"].balance == 50.0   # 50 held in escrow
    # force the reaper to see the offer as expired
    monkeypatch.setattr(st, "_now", lambda: 10**12)
    store._reap_once()
    assert store.job_status(j["job_id"]) == "failed"
    assert store.ledger.accounts["alice"].balance == 100.0  # escrow refunded
    # a late delivery must NOT settle (no surprise charge / double pay)
    out = store.deliver_assisted(tid, "bobnode", "too late")
    assert not out["ok"]
    assert store.ledger.accounts["alice"].balance == 100.0
    assert store.ledger.accounts.get("bob") is None or store.ledger.accounts["bob"].balance == 100.0
    assert store.ledger.conservation_ok()


def test_expiry_refund_failure_does_not_strand_hold(store, monkeypatch):
    # R36: a refund that raises (e.g. a ledger desync) must NOT fail-and-strand the asker's hold —
    # the reaper leaves the offer open and the NEXT tick self-heals once the refund path recovers.
    # _reap_once/_reap_loop live in _store_jobs (R37 mixin split) — patch `_now` there.
    import passiveworkers.net._store_jobs as st
    j = _offer(store, asker="alice")
    tid = store.assisted_offers({"profile": "{}", "answer_model": "", "owner": "bob"})[0]["task_id"]
    store.accept_assisted(tid, "bobnode", "bob")
    assert store.ledger.accounts["alice"].balance == 50.0        # 50 held in escrow
    monkeypatch.setattr(st, "_now", lambda: 10**12)              # offer is now expired

    real_refund = store.ledger.refund
    calls = {"n": 0}

    def flaky_refund(asker_id, amount):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ledger desync")                 # first tick fails
        return real_refund(asker_id, amount)                    # later ticks succeed

    monkeypatch.setattr(store.ledger, "refund", flaky_refund)

    store._reap_once()                                          # tick 1: refund raises
    assert store.job_status(j["job_id"]) != "failed"           # NOT failed-and-stranded
    assert store.ledger.accounts["alice"].balance == 50.0      # hold intact — nothing burned
    assert store.ledger.conservation_ok()

    store._reap_once()                                          # tick 2: refund succeeds → self-heals
    assert store.job_status(j["job_id"]) == "failed"
    assert store.ledger.accounts["alice"].balance == 100.0     # refunded
    assert store.ledger.conservation_ok()


def test_refund_to_missing_account_burns_no_escrow(store):
    # R36: refund() is atomic — a missing asker account raises BEFORE escrow is decremented, so
    # credit is never destroyed (previously escrow was debited first → a KeyError burned it).
    led = store.ledger
    led.open_account("alice")
    led.hold("alice", 20.0)
    escrow_before = led._escrow().balance
    with pytest.raises(Exception):
        led.refund("ghost_account", 20.0)
    assert led._escrow().balance == escrow_before              # escrow untouched on failure
    assert led.conservation_ok()


def test_capability_gate_hides_ineligible_offers(store):
    _offer(store, requires={"model": "qwen3:14b"})
    # operator without that model sees nothing
    assert store.assisted_offers({"profile": '{"models": []}', "answer_model": "", "owner": "bob"}) == []
    # operator with it sees the offer
    ok = store.assisted_offers({"profile": '{"models": ["qwen3:14b"]}', "answer_model": "", "owner": "b"})
    assert len(ok) == 1
