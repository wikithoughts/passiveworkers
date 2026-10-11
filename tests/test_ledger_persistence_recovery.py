"""Ledger ⇄ SQLite recovery (Codex review P1/P2 on #29).

The Store moves escrow IN MEMORY (release on delivery, refund on expiry) and then persists the job
transition + ledger in one SQLite transaction. If that transaction fails, the in-memory ledger must
roll back with it: otherwise the job stays payable with the money already moved, and the retry pays
or refunds a second time out of ANOTHER offer's hold. Failures here are injected with real SQLite
temp triggers (RAISE(FAIL)), not by mocking the ledger.
"""
import sqlite3

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "r.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    import importlib
    import passiveworkers.ledger as led
    importlib.reload(led)
    import passiveworkers.net.config as cfg
    importlib.reload(cfg)
    import passiveworkers.net.store as st
    importlib.reload(st)
    s = st.Store()
    s._stop.set()   # no background reaper: the tests drive _reap_once themselves
    yield s
    s.conn.close()


ESCROW = "__escrow__"


def _fail_on(store, target):
    store.conn.execute(f"CREATE TEMP TRIGGER inject_fail BEFORE {target} "
                       "BEGIN SELECT RAISE(FAIL, 'injected storage failure'); END")
    store.conn.commit()


def _heal(store):
    store.conn.execute("DROP TRIGGER inject_fail")
    store.conn.commit()


def _memory(store):
    return {k: a.balance for k, a in store.ledger.accounts.items()}


def _persisted(store):
    return {k: a.balance for k, a in store._load_ledger().accounts.items()}


def _status(store, job_id):
    return store.conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0]


def _two_offers(store):
    a = store.create_job("alice", "first offer", job_type="assisted")
    c = store.create_job("charlie", "second offer", job_type="assisted")
    assert a["status"] == c["status"] == "pending_assist"
    return a["job_id"], c["job_id"], a["price"]


def _accept_alice(store, alice_job):
    tid = store.conn.execute("SELECT task_id FROM tasks WHERE job_id=?", (alice_job,)).fetchone()[0]
    assert store.accept_assisted(tid, "bobnode", "bob")["ok"]
    return tid


@pytest.mark.parametrize("target", [
    "UPDATE OF status ON tasks WHEN NEW.status='done'",   # Codex's probe: first write fails
    "UPDATE OF status ON jobs WHEN NEW.status='done'",    # second write fails
    "UPDATE ON ledger",                                   # the ledger save itself fails
])
def test_failed_delivery_then_retry_pays_exactly_once(store, target):
    alice, charlie, pool = _two_offers(store)
    tid = _accept_alice(store, alice)
    before = _memory(store)
    _fail_on(store, target)
    with pytest.raises(sqlite3.IntegrityError):
        store.deliver_assisted(tid, "bobnode", "work")
    # nothing moved: memory still matches the (rolled-back) DB, the job is still payable
    assert _memory(store) == before == _persisted(store)
    assert _status(store, alice) == "assisting"
    assert store.conservation_ok()
    _heal(store)
    assert store.deliver_assisted(tid, "bobnode", "retry")["ok"]
    assert store.ledger.balance("bob") == before["bob"] + pool        # paid ONCE (was 2x)
    assert store.ledger.balance(ESCROW) == pool                      # Charlie's hold untouched
    assert _status(store, alice) == "done" and _status(store, charlie) == "pending_assist"
    assert store.conservation_ok() and _memory(store) == _persisted(store)


class _CommitFails:
    """sqlite3.Connection proxy whose next commit() raises (a disk-full / I/O error at COMMIT)."""

    def __init__(self, conn):
        self._conn, self.armed = conn, True

    def commit(self):
        if self.armed:
            self.armed = False
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_failed_commit_on_delivery_rolls_the_ledger_back(store):
    alice, _charlie, pool = _two_offers(store)
    tid = _accept_alice(store, alice)
    before = _memory(store)
    real = store.conn
    store.conn = _CommitFails(real)
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.deliver_assisted(tid, "bobnode", "work")
    finally:
        store.conn = real
    assert _memory(store) == before == _persisted(store)
    assert _status(store, alice) == "assisting"
    assert store.deliver_assisted(tid, "bobnode", "retry")["ok"]
    assert store.ledger.balance("bob") == before["bob"] + pool
    assert store.ledger.balance(ESCROW) == pool and store.conservation_ok()


def test_failed_reaper_then_retry_refunds_exactly_once(store):
    alice, charlie, pool = _two_offers(store)
    store.conn.execute("UPDATE jobs SET created=0 WHERE job_id=?", (alice,))   # only Alice expired
    store.conn.commit()
    before = _memory(store)
    _fail_on(store, "UPDATE OF status ON jobs WHEN NEW.status='failed'")
    with pytest.raises(sqlite3.IntegrityError):
        store._reap_once()
    assert _memory(store) == before == _persisted(store)
    assert _status(store, alice) == "pending_assist"
    assert store.conservation_ok()
    _heal(store)
    store._reap_once()
    assert store.ledger.balance("alice") == before["alice"] + pool    # refunded ONCE (was 2x)
    assert store.ledger.balance(ESCROW) == pool                      # Charlie's hold untouched
    assert _status(store, alice) == "failed" and _status(store, charlie) == "pending_assist"
    assert store.conservation_ok() and _memory(store) == _persisted(store)


def test_failed_reaper_commit_rolls_the_ledger_back(store):
    alice, _charlie, pool = _two_offers(store)
    store.conn.execute("UPDATE jobs SET created=0 WHERE job_id=?", (alice,))
    store.conn.commit()
    before = _memory(store)
    real = store.conn
    store.conn = _CommitFails(real)
    try:
        with pytest.raises(sqlite3.OperationalError):
            store._reap_once()
    finally:
        store.conn = real
    assert _memory(store) == before == _persisted(store)
    store._reap_once()
    assert store.ledger.balance("alice") == before["alice"] + pool
    assert store.ledger.balance(ESCROW) == pool and store.conservation_ok()


# ------------------------------------------------------------ Store-level integrity (P2)
def test_store_conservation_tracks_the_normal_lifecycle(store):
    assert store.conservation_ok()                   # empty store: no escrow, nothing owed
    alice, _charlie, pool = _two_offers(store)
    assert store.ledger_integrity()["outstanding_holds"] == 2 * pool and store.conservation_ok()
    tid = _accept_alice(store, alice)
    assert store.deliver_assisted(tid, "bobnode", "work")["ok"]
    integ = store.ledger_integrity()
    assert integ["ok"] and integ["escrow"] == integ["outstanding_holds"] == pool


def test_store_conservation_flags_underfunded_escrow(store):
    # Codex's end state: one hold paid out twice. Total credit still balances and escrow is not
    # negative, so the ledger-only check passes — but Charlie's live offer is no longer funded.
    _alice, _charlie, pool = _two_offers(store)
    store.ledger.open_account("bob")
    store.ledger._escrow().balance -= pool
    store.ledger.accounts["bob"].balance += pool
    assert store.ledger.conservation_ok()
    integ = store.ledger_integrity()
    assert integ["escrow"] < integ["outstanding_holds"]
    assert not integ["ok"] and not store.conservation_ok()


def test_store_conservation_flags_credit_stranded_in_escrow(store):
    alice, _charlie, _pool = _two_offers(store)
    store.conn.execute("UPDATE jobs SET status='failed' WHERE job_id=?", (alice,))   # no refund
    store.conn.commit()
    integ = store.ledger_integrity()
    assert integ["escrow"] > integ["outstanding_holds"] and not store.conservation_ok()
