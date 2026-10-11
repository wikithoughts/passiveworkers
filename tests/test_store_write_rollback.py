"""Ledger ⇄ SQLite rollback on offer creation and council settlement (review batch 6, §7).

create_job(job_type="assisted") holds the reward in escrow IN MEMORY and then inserts the job and its
task. When a write failed it only refunded the hold — the already-executed `INSERT INTO jobs` stayed in
the open SQLite transaction and was committed by the next unrelated commit. That orphan
pending_assist job owed a pool escrow never held (ledger_integrity ok:false), and once it expired the
reaper refunded the asker a second time out of another offer's hold. _settle (council settlement) has
the same shape: ledger.settle_job moves credit in memory before the rows that make the job 'done'.

Failures are injected with real SQLite temp triggers (RAISE(FAIL)), not by mocking the ledger.
"""
import sqlite3

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "w.db"))
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


def _jobs_of(store, asker):
    return store.conn.execute("SELECT job_id, status FROM jobs WHERE asker=?", (asker,)).fetchall()


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


# ------------------------------------------------------------ assisted offer creation
def _seed_alice(store):
    """Alice has an account persisted (and an older, unrelated offer from dave keeps escrow live)."""
    store.ledger.open_account("alice")
    store._save_ledger(); store.conn.commit()
    assert store.create_job("dave", "older offer", job_type="assisted")["status"] == "pending_assist"
    return _memory(store)


def _assert_no_orphan_then_reap_once(store, before):
    """After the failed create: nothing of Alice's offer survives the NEXT unrelated commit, the
    ledger matches the DB, and expiring everything never refunds Alice a hold she doesn't have."""
    assert _memory(store) == before
    # another user creates an offer — this commit used to persist Alice's orphan job row
    assert store.create_job("charlie", "unrelated offer", job_type="assisted")["status"] == "pending_assist"
    assert _jobs_of(store, "alice") == []                            # no orphan pending_assist job
    assert store.conn.execute("SELECT COUNT(*) FROM tasks t LEFT JOIN jobs j ON j.job_id=t.job_id "
                              "WHERE j.job_id IS NULL").fetchone()[0] == 0
    assert store.ledger_integrity()["ok"]                            # escrow == outstanding holds
    assert _memory(store) == _persisted(store)
    assert store.ledger.balance("alice") == before["alice"]
    store.conn.execute("UPDATE jobs SET created=0")                  # every offer expires
    store.conn.commit()
    store._reap_once()
    assert store.ledger.balance("alice") == before["alice"]          # NOT refunded again (was +pool)
    assert store.ledger.balance(ESCROW) == 0                         # dave + charlie refunded, no debt
    assert store.ledger_integrity()["ok"] and _memory(store) == _persisted(store)


@pytest.mark.parametrize("target", [
    "INSERT ON tasks",    # the review probe: the job row is already written when the task fails
    "INSERT ON jobs",     # the first write fails
    "UPDATE ON ledger",   # the ledger save itself fails (after both inserts)
])
def test_failed_offer_create_leaves_no_orphan_job(store, target):
    before = _seed_alice(store)
    _fail_on(store, target)
    with pytest.raises(sqlite3.IntegrityError):
        store.create_job("alice", "my offer", job_type="assisted")
    _heal(store)
    _assert_no_orphan_then_reap_once(store, before)
    # and Alice can still post the offer once storage is healthy
    assert store.create_job("alice", "my offer", job_type="assisted")["status"] == "pending_assist"
    assert store.ledger_integrity()["ok"] and _memory(store) == _persisted(store)


def test_review_probe_expired_failed_offer_is_not_refunded_twice(store):
    # the exact review probe: fail the task INSERT for Alice's offer, another user creates an offer,
    # everything expires, the reaper runs. Alice must end where she started (was 150 instead of 100).
    store.ledger.open_account("alice")
    store._save_ledger(); store.conn.commit()
    start = store.ledger.balance("alice")
    _fail_on(store, "INSERT ON tasks")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_job("alice", "my offer", job_type="assisted")
    _heal(store)
    store.create_job("charlie", "unrelated offer", job_type="assisted")
    store.conn.execute("UPDATE jobs SET created=0")
    store.conn.commit()
    store._reap_once()
    assert store.ledger.balance("alice") == start
    assert store.ledger.balance(ESCROW) == 0
    assert store.ledger_integrity()["ok"]


def test_failed_offer_commit_leaves_no_orphan_job(store):
    before = _seed_alice(store)
    real = store.conn
    store.conn = _CommitFails(real)
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.create_job("alice", "my offer", job_type="assisted")
    finally:
        store.conn = real
    _assert_no_orphan_then_reap_once(store, before)


def test_failed_first_offer_drops_the_unpersisted_account(store):
    # a brand-new asker: the account (and its starter grant) is opened in memory by the same call;
    # if the call fails, the ledger must not keep an account the DB never saw.
    _fail_on(store, "INSERT ON tasks")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_job("erin", "first ever offer", job_type="assisted")
    _heal(store)
    assert "erin" not in store.ledger.accounts
    assert store.ledger.conservation_ok() and _memory(store) == _persisted(store)
    assert _jobs_of(store, "erin") == []


# ------------------------------------------------------------ council settlement
def _reg(store, owner, judge=False):
    return store.register_node({
        "owner": owner, "name": owner, "country": "X", "lens": "neutral",
        "answer_model": "m", "can_judge": judge, "judge_model": ("m" if judge else ""),
        "profile": {"cores": 4, "ram_gb": 16, "models": ["m"]},
    })["node_id"]


def _judging_job(store):
    _reg(store, "op1")
    _reg(store, "judgeop", judge=True)
    j = store.create_job("alice", "q", job_type="chat", minds=1)
    assert j["status"] == "pending_answers"
    t = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='answer'",
                           (j["job_id"],)).fetchone()
    assert store.complete_task(t["task_id"], {"text": "an answer"}, node_id=t["node_id"])
    assert store.job_status(j["job_id"]) == "judging"
    judge = store.conn.execute("SELECT * FROM tasks WHERE job_id=? AND type='judge'",
                               (j["job_id"],)).fetchone()
    return j["job_id"], judge


def _assert_settlement_rolled_back(store, job_id, before):
    assert _memory(store) == before == _persisted(store)              # nobody charged or paid
    assert store.job_status(job_id) == "judging"
    assert store.ledger.conservation_ok()
    # the next unrelated commit must not persist half a settlement either
    store.create_job("charlie", "unrelated offer", job_type="assisted")
    assert store.job_status(job_id) == "judging"
    assert store.conn.execute("SELECT COUNT(*) FROM tasks WHERE job_id=? AND score IS NOT NULL",
                              (job_id,)).fetchone()[0] == 0
    assert store.ledger.balance("alice") == before["alice"]
    assert _memory(store) == _persisted(store) and store.ledger_integrity()["ok"]


@pytest.mark.parametrize("target", [
    "UPDATE OF score ON tasks",                          # the first write after settle_job fails
    "UPDATE OF status ON jobs WHEN NEW.status='done'",   # the job transition fails
    "UPDATE ON ledger",                                  # the ledger save itself fails
])
def test_failed_settlement_rolls_the_ledger_back(store, target):
    job_id, judge = _judging_job(store)
    before = _memory(store)
    _fail_on(store, target)
    with pytest.raises(sqlite3.IntegrityError):
        store.complete_task(judge["task_id"], {"scores": {}}, node_id=judge["node_id"])
    _heal(store)
    _assert_settlement_rolled_back(store, job_id, before)


def test_failed_settlement_commit_rolls_the_ledger_back(store):
    job_id, judge = _judging_job(store)
    before = _memory(store)

    class _SettleCommitFails(_CommitFails):
        # complete_task commits the judge task first; fail the SECOND commit (settlement's own)
        def __init__(self, conn):
            super().__init__(conn)
            self.armed, self._skip = True, 1

        def commit(self):
            if self._skip:
                self._skip -= 1
                return self._conn.commit()
            return super().commit()

    real = store.conn
    store.conn = _SettleCommitFails(real)
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.complete_task(judge["task_id"], {"scores": {}}, node_id=judge["node_id"])
    finally:
        store.conn = real
    _assert_settlement_rolled_back(store, job_id, before)


def test_successful_settlement_still_charges_once(store):
    job_id, judge = _judging_job(store)
    before = _memory(store)
    assert store.complete_task(judge["task_id"], {"scores": {}}, node_id=judge["node_id"])
    assert store.job_status(job_id) == "done"
    assert store.ledger.balance("alice") < before["alice"]
    assert store.ledger.conservation_ok() and _memory(store) == _persisted(store)


# ------------------------------------------------------------ automated (council) job creation
# create_job for an automated type opened the asker's account IN MEMORY, then inserted the job and
# its answer tasks, then saved the ledger and committed — with no snapshot and no rollback. A failed
# task INSERT left the already-executed `INSERT INTO jobs` in the open transaction: the next unrelated
# commit persisted an orphan pending_answers job with no tasks, and a brand-new asker's account (with
# its starter grant) stayed in memory although the DB never saw it.
def _assert_automated_create_rolled_back(store, asker, before):
    assert _memory(store) == before                                  # ledger equal to pre-call
    # another user creates a job — this commit used to persist the orphan job row
    assert store.create_job("charlie", "unrelated q", job_type="chat", minds=1)["status"] == \
        "pending_answers"
    assert _jobs_of(store, asker) == []                              # no orphan pending_answers job
    assert store.conn.execute("SELECT COUNT(*) FROM jobs j LEFT JOIN tasks t ON t.job_id=j.job_id "
                              "WHERE t.task_id IS NULL AND j.status='pending_answers'"
                              ).fetchone()[0] == 0
    assert store.ledger.conservation_ok() and store.ledger_integrity()["ok"]
    assert _memory(store) == _persisted(store)


@pytest.mark.parametrize("target", [
    "INSERT ON tasks",    # the review probe: the job row is already written when the task fails
    "INSERT ON jobs",     # the first write fails
    "UPDATE ON ledger",   # the ledger save itself fails (after both inserts)
])
@pytest.mark.parametrize("job_type,items", [("chat", None), ("shard_map", ["a", "b"])])
def test_failed_automated_create_leaves_no_orphan_job(store, target, job_type, items):
    _reg(store, "op1")
    store.ledger.open_account("alice")
    store._save_ledger(); store.conn.commit()
    before = _memory(store)
    _fail_on(store, target)
    with pytest.raises(sqlite3.IntegrityError):                      # exception type unchanged
        store.create_job("alice", "my q", job_type=job_type, items=items, minds=1)
    _heal(store)
    _assert_automated_create_rolled_back(store, "alice", before)
    # and Alice can still create the job once storage is healthy
    assert store.create_job("alice", "my q", job_type=job_type, items=items,
                            minds=1)["status"] == "pending_answers"
    assert store.ledger_integrity()["ok"] and _memory(store) == _persisted(store)


def test_failed_first_automated_create_drops_the_unpersisted_account(store):
    # a brand-new asker: open_account (and its starter grant) runs in the same call; if the call
    # fails, the ledger must not keep an account the DB never saw.
    _reg(store, "op1")
    before = _memory(store)
    _fail_on(store, "INSERT ON tasks")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_job("erin", "first ever q", job_type="chat", minds=1)
    _heal(store)
    assert "erin" not in store.ledger.accounts
    _assert_automated_create_rolled_back(store, "erin", before)


def test_failed_automated_create_commit_leaves_no_orphan_job(store):
    _reg(store, "op1")
    before = _memory(store)
    real = store.conn
    store.conn = _CommitFails(real)
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.create_job("erin", "first ever q", job_type="chat", minds=1)
    finally:
        store.conn = real
    assert "erin" not in store.ledger.accounts
    _assert_automated_create_rolled_back(store, "erin", before)
