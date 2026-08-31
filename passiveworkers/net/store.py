#!/usr/bin/env python3
"""
passiveworkers/net/store.py — SQLite persistence + orchestration state (hardened)
=========================================================================
Holds nodes, jobs, tasks, and the (reused, tested) credit Ledger. All mutations AND
reads go through one re-entrant lock so the FastAPI thread pool can't race.

Security/correctness invariants (see docs/DECISIONS + the M4 hardening review):
  • Per-node SECRET: register mints a secret (returned once, only its hash stored); node
    operations are authenticated by that secret, and a node can only complete its OWN tasks.
  • Settle is FAIL-CLOSED: the ledger is settled FIRST; only on success do we write scores,
    reputation, and 'done'. An over-budget job fails cleanly instead of stranding/​corrupting.
  • Scores are sanitized: non-finite (inf/NaN) or out-of-range judge scores → 0; an empty or
    errored answer scores 0 and earns no reputation. min(10, NaN)==10, so isfinite comes first.
  • A REAPER thread fails jobs whose assigned node went stale or that exceed the run deadline,
    so a dead worker/judge can never wedge a job forever.

Job lifecycle:
    submit → N `answer` tasks → all answers done → one `judge` task → settle → done.
"""

from __future__ import annotations

import sqlite3
import threading

from passiveworkers.net._store_assisted import _AssistedMixin
from passiveworkers.net._store_base import _hash as _hash
from passiveworkers.net._store_jobs import _JobsMixin
from passiveworkers.net._store_ledger import _LedgerMixin
from passiveworkers.net._store_reporting import _ReportingMixin
from passiveworkers.net.config import CONFIG

# _hash is re-exported (`from passiveworkers.net._store_base import _hash as _hash`) so
# `from passiveworkers.net.store import _hash` — used directly by tests/test_security_privacy.py —
# keeps working unchanged now that the helper itself lives in _store_base.py.


class Store(_JobsMixin, _AssistedMixin, _LedgerMixin, _ReportingMixin):
    """Composes the four Store mixins (jobs/orchestration, assisted marketplace, ledger/
    identity, reporting — see their own module docstrings) around the schema + the two
    resources every one of them shares: `self.lock` and `self.conn`, both created here.

    All mutations AND reads across every mixin go through this one re-entrant lock so the
    FastAPI thread pool can't race, and there is exactly one sqlite3 connection for the
    life of the Store (see the module docstring above for the full invariant + why it
    matters). Every mixin method uses `self.lock`/`self.conn`/`self.ledger` — never a
    locally-constructed lock or a second connection.
    """

    def __init__(self, path: str | None = None):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path or CONFIG.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self.ledger = self._load_ledger()
        # Reaper: fail stuck jobs so a dead node can't wedge the queue forever.
        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True, name="pw-reaper")
        self._reaper.start()

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes(
              node_id TEXT PRIMARY KEY, name TEXT, country TEXT, owner TEXT,
              answer_model TEXT, lens TEXT, can_judge INT, judge_model TEXT,
              profile TEXT, last_seen REAL, load REAL, status TEXT, ip TEXT, secret_hash TEXT,
              machine_id TEXT);
            CREATE TABLE IF NOT EXISTS jobs(
              job_id TEXT PRIMARY KEY, asker TEXT, question TEXT, status TEXT,
              created REAL, merged TEXT, receipt TEXT, error TEXT, council TEXT);
            CREATE TABLE IF NOT EXISTS users(
              handle TEXT PRIMARY KEY, secret_hash TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS feedback(
              job_id TEXT PRIMARY KEY, verdict TEXT, who TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS tasks(
              task_id TEXT PRIMARY KEY, job_id TEXT, type TEXT, node_id TEXT,
              status TEXT, payload TEXT, result TEXT, worker_id TEXT, owner TEXT,
              lens TEXT, country TEXT, model TEXT, created REAL, score REAL, claimed_at REAL);
            CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, data TEXT);
            """
        )
        # one row per (hash, job_id): content-addressed within a job, but each job keeps its
        # own copy so cross-job content collisions never strand a second asker (D22 review).
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS blobs("
            "hash TEXT, job_id TEXT, data BLOB, created REAL, PRIMARY KEY(hash, job_id))")
        # which (asker, operator) pairs have ALREADY moved reputation — anti-farming (D24 review):
        # one rater can lift a given operator's gate-average at most once.
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS rater_pairs(asker TEXT, operator TEXT, PRIMARY KEY(asker, operator))")
        # per-operator enrollment tokens (D37): when PW_ENROLL is on, the STARTER GRANT (and node
        # registration) requires redeeming one of these — minted by the admin, so Sybil identities
        # can't mint free credits. `uses`/`max_uses` bound redemptions; `grant_amount` is the credit.
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS enroll_tokens(token_hash TEXT PRIMARY KEY, owner TEXT, "
            "kind TEXT, grant_amount REAL, max_uses INTEGER, uses INTEGER, created REAL)")
        # migrations (ALTER TABLE on boot — re-installs must never wipe the DB)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        if "baseline" not in cols:   # independent single-model baseline (passiveworkers/net/baseline.py)
            self.conn.execute("ALTER TABLE jobs ADD COLUMN baseline TEXT")
        if "pool" not in cols:       # per-job worker pool (responder dial: cost scales with minds)
            self.conn.execute("ALTER TABLE jobs ADD COLUMN pool REAL")
        if "type" not in cols:       # job type — async work marketplace (D13); null/legacy = chat
            self.conn.execute("ALTER TABLE jobs ADD COLUMN type TEXT")
        # stage chaining (D35): a `then` follow-on spec + parent/child links between chained jobs.
        if "then_spec" not in cols:  # JSON {"question","requires"} → an assisted follow-on at completion
            self.conn.execute("ALTER TABLE jobs ADD COLUMN then_spec TEXT")
        if "parent" not in cols:     # this job was spawned as the `then` follow-on of parent
            self.conn.execute("ALTER TABLE jobs ADD COLUMN parent TEXT")
        if "child" not in cols:      # the follow-on job this job spawned on completion
            self.conn.execute("ALTER TABLE jobs ADD COLUMN child TEXT")
        if "as_file" not in cols:    # D38: deliver the assembled sharded output as a downloadable file
            self.conn.execute("ALTER TABLE jobs ADD COLUMN as_file INTEGER DEFAULT 0")
        # tasks migrations (D32 orchestration): reassignment counter + per-task progress.
        tcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        if "retries" not in tcols:   # how many times this task has been reassigned on failover
            self.conn.execute("ALTER TABLE tasks ADD COLUMN retries INTEGER DEFAULT 0")
        if "progress" not in tcols:  # JSON {"done":N,"total":M} a worker reports mid-flight
            self.conn.execute("ALTER TABLE tasks ADD COLUMN progress TEXT")
        # nodes migration (D43): offline-resolved country, to verify against the self-reported one.
        # (The existing `ip` column already holds the client IP and is never exposed via /status.)
        ncols = {r["name"] for r in self.conn.execute("PRAGMA table_info(nodes)")}
        if "geo_country" not in ncols:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN geo_country TEXT")
        # R10 review: a node that fails 100% of its tasks still looked fully healthy — heartbeat
        # carried only `load`. These counters make failure visible on /status.
        if "ok_count" not in ncols:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN ok_count INTEGER DEFAULT 0")
        if "fail_count" not in ncols:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN fail_count INTEGER DEFAULT 0")
        self.conn.commit()
