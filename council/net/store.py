#!/usr/bin/env python3
"""
council/net/store.py — SQLite persistence + orchestration state (hardened)
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

import hashlib
import json
import math
import os
import secrets as _secrets
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

from council.ledger import STARTER_ALLOWANCE, Account, InsufficientCredit, Ledger
from council.net.config import CONFIG, JOB_TYPES, pool_for, task_behavior
from council.sanitize import sanitize_brief

_now = time.time  # server runtime (not a workflow script)
_FIELD_MAX = 80   # cap node/owner string lengths (defense-in-depth vs. abuse)
# Failover (D32): how many times a task may be reassigned before the job fails, and how long a
# CLAIMED-but-undelivered task may sit (as a fraction of the job's deadline) before it's "stalled"
# even though its node still heartbeats. node-OFFLINE stalls are reassigned immediately.
_MAX_TASK_RETRIES = int(os.environ.get("PW_MAX_TASK_RETRIES", "2"))
_CLAIM_TIMEOUT_FRAC = float(os.environ.get("PW_CLAIM_TIMEOUT_FRAC", "0.6"))


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _clip(s: Any) -> str:
    return str(s if s is not None else "")[:_FIELD_MAX]


_MAX_CHAIN_STAGES = 8   # cap a `then` pipeline length (abuse bound)


def _norm_then(then: Any) -> Optional[str]:
    """Normalize a `then` chain — a single stage dict OR a list of stage dicts — into a JSON list of
    sanitized stage specs (or None). Each stage: {type, question(required), requires?, items?, split?,
    as_file?}; an unknown/absent type defaults to 'assisted' (the human-mediated hand-off). D39."""
    if isinstance(then, dict):
        then = [then]
    if not isinstance(then, list):
        return None
    stages = []
    for s in then[:_MAX_CHAIN_STAGES]:
        if not isinstance(s, dict):
            continue
        q = str(s.get("question") or "").strip()
        if not q:
            continue
        stage = {"type": s.get("type") if s.get("type") in JOB_TYPES else "assisted",
                 "question": q[:4000]}
        if isinstance(s.get("requires"), dict):
            stage["requires"] = s["requires"]
        if isinstance(s.get("items"), list):
            stage["items"] = [str(x)[:2000] for x in s["items"]][:200]
        if isinstance(s.get("split"), list):
            stage["split"] = s["split"]
        if s.get("as_file"):
            stage["as_file"] = True
        stages.append(stage)
    return json.dumps(stages) if stages else None


class Store:
    def __init__(self, path: str = None):
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
        if "baseline" not in cols:   # independent single-model baseline (council/net/baseline.py)
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
        self.conn.commit()

    # ------------------------------------------------------------------ ledger persistence
    def _load_ledger(self) -> Ledger:
        row = self.conn.execute("SELECT data FROM ledger WHERE id=1").fetchone()
        led = Ledger()
        if row:
            d = json.loads(row["data"])
            led._granted_total = d.get("granted", 0.0)
            led._job_count = d.get("jobs", 0)
            for a in d.get("accounts", []):
                led.accounts[a["user_id"]] = Account(**a)
        return led

    def _save_ledger(self) -> None:
        d = {
            "granted": self.ledger._granted_total,
            "jobs": self.ledger._job_count,
            "accounts": [vars(a) for a in self.ledger.accounts.values()],
        }
        self.conn.execute(
            "INSERT INTO ledger(id, data) VALUES(1, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (json.dumps(d),),
        )

    # ------------------------------------------------------------------ enrollment tokens (D37)
    def mint_enrollment(self, owner: str = "", kind: str = "any",
                        grant: float | None = None, max_uses: int = 1) -> dict:
        """Admin-only: mint an enrollment token. Returns the plaintext ONCE (only its hash is
        stored). `kind` ∈ any|node|user; `grant` is the starter credit a redemption confers
        (default STARTER_ALLOWANCE); `max_uses` bounds redemptions."""
        kind = kind if kind in ("any", "node", "user") else "any"
        if grant is None:
            amount = STARTER_ALLOWANCE
        else:                                  # never store a non-finite/negative grant (review)
            f = float(grant)
            amount = f if (math.isfinite(f) and f >= 0.0) else 0.0
        max_uses = max(1, int(max_uses))
        token = _secrets.token_urlsafe(24)
        with self.lock:
            self.conn.execute(
                "INSERT INTO enroll_tokens VALUES(?,?,?,?,?,?,?)",
                (_hash(token), _clip(owner), kind, amount, max_uses, 0, _now()))
            self.conn.commit()
        return {"enroll_token": token, "owner": owner, "kind": kind,
                "grant": amount, "max_uses": max_uses}

    def _redeem_enrollment_locked(self, token: str, kind: str = "any") -> dict:
        """Consume one use of an enrollment token WITHOUT committing — the caller already holds
        `self.lock` and owns the commit, so a redemption can be made atomic with the node INSERT it
        gates (a failed register then rolls the `uses+1` back, instead of burning a single-use token
        without producing a node). See redeem_enrollment for the standalone/committing wrapper."""
        if not token:
            return {"ok": False, "error": "missing enrollment token"}
        row = self.conn.execute(
            "SELECT * FROM enroll_tokens WHERE token_hash=?", (_hash(token),)).fetchone()
        if not row:
            return {"ok": False, "error": "invalid enrollment token"}
        if (row["uses"] or 0) >= (row["max_uses"] or 1):
            return {"ok": False, "error": "enrollment token exhausted"}
        if row["kind"] not in ("any", kind):
            return {"ok": False, "error": f"enrollment token is for '{row['kind']}', not '{kind}'"}
        self.conn.execute("UPDATE enroll_tokens SET uses=uses+1 WHERE token_hash=?", (_hash(token),))
        return {"ok": True, "owner": row["owner"], "grant": row["grant_amount"]}

    def redeem_enrollment(self, token: str, kind: str = "any") -> dict:
        """Consume one use of an enrollment token (atomic). Returns {ok, owner, grant} on success,
        else {ok:False, error}. Validates existence, remaining uses, and kind match."""
        with self.lock:
            r = self._redeem_enrollment_locked(token, kind)
            if r.get("ok"):
                self.conn.commit()
            return r

    def _open_account_reversible(self, owner: str, grant_amount):
        """open_account (an IN-MEMORY ledger mutation), returning a `revert()` that undoes it if the
        surrounding DB transaction rolls back. A bare conn.rollback() would leave the ledger holding a
        phantom granted account never written to the DB (D48 review: ledger==DB divergence)."""
        new = owner not in self.ledger.accounts
        granted_before = self.ledger._granted_total
        self.ledger.open_account(owner, grant_amount=grant_amount)

        def _revert():
            if new:
                self.ledger.accounts.pop(owner, None)
                self.ledger._granted_total = granted_before
        return _revert

    # ------------------------------------------------------------------ nodes
    def register_node(self, body: dict, ip: str = "", grant_amount: float | None = None,
                      geo_country: str = "", enroll_token: str | None = None,
                      enroll_kind: str = "node") -> dict:
        """Returns {node_id, node_secret}. The secret is shown ONCE; only its hash is stored.
        `grant_amount` (D37): the owner's starter credit — None → STARTER_ALLOWANCE (default), 0 →
        none (enrollment-gated callers pass 0 / the token's amount). `geo_country` (D43): the
        coordinator's offline IP→country lookup ("" when unavailable → falls back to self-reported).
        Named-column INSERT (NOT positional) so migration-added columns can't be silently skipped.

        `enroll_token` (D37/D48): when given, the token is redeemed IN THE SAME transaction as the
        node INSERT and supplies `grant_amount`. If the INSERT fails the redemption is rolled back
        with it — so a single-use enrollment token is never burned without producing a node. Returns
        {"error": ...} (no node) if the token is invalid/exhausted."""
        with self.lock:
            if enroll_token is not None:
                red = self._redeem_enrollment_locked(enroll_token, enroll_kind)
                if not red.get("ok"):
                    return {"error": red.get("error", "valid enrollment token required")}
                grant_amount = red.get("grant")
            owner = _clip(body["owner"])
            revert = self._open_account_reversible(owner, grant_amount)
            try:
                node_id = str(uuid.uuid4())
                secret = _secrets.token_urlsafe(24)
                self.conn.execute(
                    "INSERT INTO nodes(node_id, name, country, owner, answer_model, lens, can_judge, "
                    "judge_model, profile, last_seen, load, status, ip, secret_hash, machine_id, "
                    "geo_country) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        node_id, _clip(body.get("name", "node")), _clip(body.get("country", "?")),
                        owner, _clip(body.get("answer_model", "")),
                        _clip(body.get("lens", "neutral")), int(bool(body.get("can_judge", False))),
                        _clip(body.get("judge_model", "")), json.dumps(body.get("profile", {})),
                        _now(), 0.0, "online", ip, _hash(secret), _clip(body.get("machine_id", "?")),
                        _clip((geo_country or "").upper()),
                    ),
                )
                self._save_ledger()
                self.conn.commit()
                return {"node_id": node_id, "node_secret": secret}
            except Exception:
                self.conn.rollback()   # roll the enrollment redemption + node INSERT back…
                revert()               # …and the in-memory ledger open_account (D48 review)
                raise

    def node_for_secret(self, secret: str) -> Optional[str]:
        """Resolve the authenticated node_id from its secret (None if unknown)."""
        if not secret:
            return None
        with self.lock:
            row = self.conn.execute(
                "SELECT node_id FROM nodes WHERE secret_hash=?", (_hash(secret),)).fetchone()
            return row["node_id"] if row else None

    def heartbeat(self, node_id: str, load: float = 0.0) -> bool:
        with self.lock:
            cur = self.conn.execute(
                "UPDATE nodes SET last_seen=?, load=?, status='online' WHERE node_id=?",
                (_now(), load, node_id))
            self.conn.commit()
            return cur.rowcount > 0

    def online_nodes(self, judge_only: bool = False) -> list[sqlite3.Row]:
        with self.lock:
            cutoff = _now() - CONFIG.node_ttl_s
            q = "SELECT * FROM nodes WHERE last_seen >= ?"
            if judge_only:
                q += " AND can_judge = 1"
            return list(self.conn.execute(q + " ORDER BY last_seen DESC", (cutoff,)))

    def get_node(self, node_id: str) -> Optional[sqlite3.Row]:
        with self.lock:
            return self.conn.execute("SELECT * FROM nodes WHERE node_id=?", (node_id,)).fetchone()

    # ------------------------------------------------------------------ users (askers)
    def register_user(self, handle: str, grant_amount: float | None = None,
                      enroll_token: str | None = None, enroll_kind: str = "user") -> dict:
        """`grant_amount` (D37): None → STARTER_ALLOWANCE (default); 0 → account with no starter
        credit (an un-enrolled signup gets zero, so it can't be Sybil-farmed for free credits).
        `enroll_token` (D48): redeemed IN THE SAME transaction and only AFTER the handle-availability
        check, so a 'handle taken' 409 (or an insert failure) can't burn a single-use signup token.
        An invalid/absent token keeps signup OPEN with a 0 grant (D37) — it is not an error."""
        handle = _clip(handle).strip() or "anon"
        with self.lock:
            if self.conn.execute("SELECT 1 FROM users WHERE handle=?", (handle,)).fetchone():
                return {"error": "handle taken"}   # checked BEFORE any redeem → no token burn (D48)
            if enroll_token is not None:
                red = self._redeem_enrollment_locked(enroll_token, enroll_kind)
                grant_amount = red.get("grant") if red.get("ok") else 0.0   # bad token → 0 grant, still open
            secret = _secrets.token_urlsafe(24)
            revert = self._open_account_reversible(handle, grant_amount)
            try:
                self.conn.execute("INSERT INTO users(handle, secret_hash, created) VALUES(?,?,?)",
                                  (handle, _hash(secret), _now()))
                self._save_ledger()
                self.conn.commit()
                return {"handle": handle, "user_secret": secret, **self.user_balance(handle)}
            except Exception:
                self.conn.rollback()   # roll the redeem + user INSERT back…
                revert()               # …and the in-memory ledger open_account (D48 review)
                raise

    def user_for_secret(self, secret: str) -> Optional[str]:
        if not secret:
            return None
        with self.lock:
            row = self.conn.execute("SELECT handle FROM users WHERE secret_hash=?",
                                    (_hash(secret),)).fetchone()
            return row["handle"] if row else None

    def user_balance(self, handle: str) -> dict:
        a = self.ledger.accounts.get(handle)
        if not a:
            return {"handle": handle, "balance": 0.0, "reputation": 0.0, "helped": 0, "asked": 0}
        return {"handle": handle, "balance": round(a.balance, 1), "reputation": a.avg_quality,
                "helped": a.jobs_helped, "asked": a.jobs_asked}

    def record_feedback(self, job_id: str, verdict: str, who: str = "") -> bool:
        if verdict not in ("council", "single", "tie"):
            return False
        with self.lock:
            row = self.conn.execute("SELECT asker FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                return False
            # Only the job's ASKER may vote on its council-vs-single outcome. This is the project's
            # headline demand metric, so it must not be ballot-stuffable (or re-votable) by any other
            # signed-in handle — mirror rate_assisted's asker-only rule (review).
            if not who or who != row["asker"]:
                return False
            self.conn.execute(
                "INSERT INTO feedback(job_id, verdict, who, created) VALUES(?,?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET verdict=excluded.verdict, who=excluded.who",
                (job_id, verdict, _clip(who), _now()))
            self.conn.commit()
            return True

    def metrics(self) -> dict:
        with self.lock:
            by = {r["verdict"]: r["c"] for r in
                  self.conn.execute("SELECT verdict, COUNT(*) c FROM feedback GROUP BY verdict")}
            council, single, tie = by.get("council", 0), by.get("single", 0), by.get("tie", 0)
            decisive = council + single
            return {"council": council, "single": single, "tie": tie,
                    "total": council + single + tie,
                    "council_win_rate": round(council / decisive, 3) if decisive else None}

    # ------------------------------------------------------------------ jobs / tasks
    @staticmethod
    def _meets(n: Any, requires: Optional[dict]) -> bool:
        """Capability match (D15 v1): required model installed, minimum RAM. Nodes report
        their profile at register; jobs may declare `requires` — not all tasks are open
        to all nodes."""
        if not requires:
            return True
        try:
            prof = json.loads(n["profile"]) if isinstance(n["profile"], str) else (n["profile"] or {})
        except Exception:
            prof = {}
        want = requires.get("model")
        if want and want != n["answer_model"] and want not in (prof.get("models") or []):
            return False
        min_ram = requires.get("min_ram_gb")
        if min_ram:
            try:
                if float(prof.get("ram_gb") or 0) < float(min_ram):
                    return False
            except (TypeError, ValueError):
                return False
        return True

    @staticmethod
    def _capacity(node: Any) -> float:
        """A positive capacity weight for load-aware split sizing (D32): cores + a RAM bonus,
        scaled down by current CPU load. Faster, less-loaded machines get a bigger shard. Always
        >0 so every selected worker can take at least its apportioned share."""
        try:
            prof = json.loads(node["profile"]) if isinstance(node["profile"], str) else (node["profile"] or {})
        except Exception:
            prof = {}
        try:
            cores = float(prof.get("cores") or 1) or 1.0
        except (TypeError, ValueError):
            cores = 1.0
        try:
            ram = float(prof.get("ram_gb") or 0)
        except (TypeError, ValueError):
            ram = 0.0
        try:
            load = float(node["load"] or 0.0)
        except (TypeError, ValueError):
            load = 0.0
        return max(0.1, (cores + ram / 8.0) * max(0.1, 1.0 - min(1.0, max(0.0, load))))

    @staticmethod
    def _apportion(total: int, weights: list[float]) -> list[int]:
        """Largest-remainder (Hamilton) apportionment: split `total` items across len(weights)
        buckets proportional to weights, with sum(result) == total exactly and each >= 0."""
        n = len(weights)
        if n == 0 or total <= 0:
            return [0] * n
        s = sum(w for w in weights if w > 0) or float(n)
        raw = [total * (w if w > 0 else 0.0) / s for w in weights]
        base = [int(x) for x in raw]
        rem = total - sum(base)
        order = sorted(range(n), key=lambda i: raw[i] - base[i], reverse=True)
        for k in range(rem):
            base[order[k % n]] += 1
        return base

    def create_job(self, asker: str, question: str, minds: int | None = None,
                   job_type: str = "chat", items: Optional[list] = None,
                   requires: Optional[dict] = None, fetch: bool = False,
                   context: str = "", encrypt_to: str = "",
                   split: Optional[list] = None, then: Optional[dict] = None,
                   as_file: bool = False) -> dict:
        with self.lock:
            asker = _clip(asker)
            # The single networked choke point for the brief/instruction (D26): scrub invisible/bidi
            # injection vectors + length-bound here so EVERY downstream prompt (worker, researcher,
            # judge.score/merge/deliberate/compile_report/spot_check) and the report get a clean value,
            # regardless of which API endpoint or caller created the job.
            question = sanitize_brief(question)
            context = sanitize_brief(context) if context else context
            if job_type not in JOB_TYPES:
                job_type = "chat"
            # assisted (D21): human-in-the-loop work. NOT pre-assigned — it's an OPEN offer
            # any consenting, capable operator may claim, do (with their own AI or by hand),
            # and deliver. No autonomous computer-use by us; the human is the agent.
            if job_type == "assisted":
                return self._create_assisted(asker, question, context, requires, encrypt_to,
                                             then_spec=_norm_then(then))
            # Answer-workers = online nodes that declare a model AND meet the job's
            # capability requirements; prefer higher reputation.
            candidates = [n for n in self.online_nodes()
                          if n["answer_model"] and self._meets(n, requires)]

            def _rep(n):
                acct = self.ledger.accounts.get(n["owner"])
                return acct.avg_quality if acct else 0.0

            candidates.sort(key=lambda n: (_rep(n), n["last_seen"]), reverse=True)
            # Responder dial: the asker picks how many minds; cost scales with the count
            # (per-mind price = worker_pool / fleet_size, so the default stays unchanged).
            n_minds = max(1, min(int(minds), len(candidates))) if minds else CONFIG.fleet_size
            workers = candidates[:n_minds]
            pool = pool_for(job_type, len(workers))
            job_id = str(uuid.uuid4())

            if not workers:
                why = ("no online node meets the job's requirements"
                       if requires else "no worker nodes online")
                self.conn.execute(
                    "INSERT INTO jobs(job_id,asker,question,status,created,merged,receipt,error,council)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (job_id, asker, question, "failed", _now(), None, None, why, None))
                self.conn.commit()
                return {"job_id": job_id, "status": "failed", "error": why}

            # shard_map: split the items across the selected workers, sized by CAPACITY
            # (cores/RAM/load) or an explicit user `split`, keeping each item's GLOBAL index so the
            # merged output preserves input order (D32). Never select more workers than items, so
            # no node sits idle and the asker isn't charged for an empty shard.
            shards: dict = {}
            if task_behavior(job_type).sharded:
                clean = [str(x).strip()[:2000] for x in (items or []) if str(x).strip()][:200]
                if not clean:
                    self.conn.execute(
                        "INSERT INTO jobs(job_id,asker,question,status,created,merged,receipt,error,council)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        (job_id, asker, question, "failed", _now(), None, None,
                         "batch job needs a non-empty items list", None))
                    self.conn.commit()
                    return {"job_id": job_id, "status": "failed",
                            "error": "batch job needs a non-empty items list"}
                if len(workers) > len(clean):
                    workers = workers[:len(clean)]
                    pool = pool_for(job_type, len(workers))
                # weights: an explicit, FINITE, positive, worker-count-matching `split` wins; else
                # capacity. (Non-finite weights — inf/nan — would make _apportion's int(raw) crash;
                # an invalid split silently falls back to capacity rather than erroring the job. review)
                if (isinstance(split, list) and len(split) == len(workers)
                        and all(isinstance(w, (int, float)) and not isinstance(w, bool)
                                and math.isfinite(w) and w > 0 for w in split)):
                    weights = [float(w) for w in split]
                else:
                    weights = [self._capacity(w) for w in workers]
                counts = self._apportion(len(clean), weights)
                shards = {w["node_id"]: [] for w in workers}
                pos = 0
                for w, c in zip(workers, counts):
                    for _ in range(c):
                        shards[w["node_id"]].append({"i": pos, "item": clean[pos]})
                        pos += 1

            self.ledger.open_account(asker)
            cost = self.ledger.quote(pool, CONFIG.judge_fee)
            if not self.ledger.can_afford(asker, cost):
                self.conn.execute(
                    "INSERT INTO jobs(job_id,asker,question,status,created,merged,receipt,error,council)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (job_id, asker, question, "failed", _now(), None, None,
                     "insufficient credit — help on a job first", None))
                self._save_ledger()
                self.conn.commit()
                return {"job_id": job_id, "status": "failed",
                        "error": "insufficient credit — help on a job first"}

            # stage chaining (D35/D39): persist a `then` PIPELINE (one stage, or a list of stages of
            # any type) so that when THIS job completes, _maybe_chain materializes the next stage
            # seeded with the deliverable and passes the rest of the chain down. Re-sanitized here.
            then_spec = _norm_then(then)
            self.conn.execute(
                "INSERT INTO jobs(job_id,asker,question,status,created,merged,receipt,error,council,"
                "pool,type,then_spec,as_file) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, asker, question, "pending_answers", _now(), None, None, None, None,
                 pool, job_type, then_spec, 1 if (as_file and task_behavior(job_type).sharded) else 0))
            beh = task_behavior(job_type)
            for n in workers:
                payload = {"question": question, "job_type": job_type}
                if beh.sharded:
                    payload["shard"] = shards.get(n["node_id"], [])
                    # Type drives fetching, not the asker (review): download_extract ALWAYS fetches
                    # (beh.fetch); shard_map fetches only if the asker opted in (allow_user_fetch);
                    # code_generation NEVER fetches — its items are specs, not URLs (D15), so a
                    # user-supplied fetch=True can't turn it into a fetcher/proxy.
                    payload["fetch"] = bool(beh.fetch or (fetch and beh.allow_user_fetch))
                self.conn.execute(
                    "INSERT INTO tasks(task_id,job_id,type,node_id,status,payload,result,worker_id,"
                    "owner,lens,country,model,created,score,claimed_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), job_id, "answer", n["node_id"], "queued",
                     json.dumps(payload), None,
                     n["node_id"], n["owner"],
                     n["lens"], n["country"], n["answer_model"], _now(), None, None))
            self._save_ledger()   # persist the new asker account before acknowledging
            self.conn.commit()
            return {"job_id": job_id, "status": "pending_answers",
                    "assigned": [n["node_id"] for n in workers]}

    # ------------------------------------------------------------------ assisted (D21)
    def _meets_reputation(self, owner: str, requires: Optional[dict]) -> bool:
        """Reputation gate (D24): when an offer sets `min_reputation`, only operators whose
        rating average meets it AND who have at least one rating qualify (newcomers take the
        ungated offers — cold-start isn't blocked). FAIL CLOSED on a malformed threshold
        (matches the capability gate _meets), so a fat-fingered gate never silently admits
        unqualified operators."""
        if not requires or "min_reputation" not in requires:
            return True            # genuinely ungated → open to everyone (incl. newcomers)
        try:
            need = float(requires["min_reputation"])
        except (TypeError, ValueError):
            return False           # malformed gate → admit no one (fail closed)
        if not math.isfinite(need):
            return False
        a = self.ledger.accounts.get(_clip(owner))
        return bool(a and a.quality_n > 0 and a.avg_quality >= need)

    def _create_assisted(self, asker: str, question: str, context: str,
                         requires: Optional[dict], encrypt_to: str = "",
                         then_spec: Optional[str] = None) -> dict:
        """Create an OPEN assisted offer (called under self.lock from create_job). `then_spec` (D39):
        a JSON chain of follow-on stages this job spawns when delivered."""
        job_id = str(uuid.uuid4())
        # validate a reputation gate up front so a fat-fingered value surfaces to the asker
        if requires and "min_reputation" in requires:
            try:
                mr = float(requires["min_reputation"])
                bad = not math.isfinite(mr) or not (0 <= mr <= 10)
            except (TypeError, ValueError):
                bad = True
            if bad:
                return {"job_id": job_id, "status": "failed",
                        "error": "min_reputation must be a number 0-10"}
        pool = pool_for("assisted")
        self.ledger.open_account(asker)
        # HOLD the reward in escrow now so it can't be spent before the operator delivers.
        try:
            self.ledger.hold(asker, pool)
        except InsufficientCredit:
            self.conn.execute(
                "INSERT INTO jobs(job_id,asker,question,status,created,merged,receipt,error,council)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, asker, question, "failed", _now(), None, None,
                 "insufficient credit — help on a job first", None))
            self._save_ledger(); self.conn.commit()
            return {"job_id": job_id, "status": "failed",
                    "error": "insufficient credit — help on a job first"}
        # The escrow hold is now applied IN MEMORY. If persisting the offer fails, undo it so the
        # in-memory ledger can't diverge from the DB (review D35) — otherwise a phantom hold would
        # later persist, stranding the asker's credit in escrow for a job that never existed.
        try:
            self.conn.execute(
                "INSERT INTO jobs(job_id,asker,question,status,created,merged,receipt,error,council,"
                "pool,type,then_spec) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, asker, question, "pending_assist", _now(), None, None, None, None,
                 pool, "assisted", then_spec))
            # ONE open task, no node_id — any consenting capable operator may claim it.
            payload = {"question": question, "job_type": "assisted",
                       "context": (context or "")[:4000], "requires": requires or {}, "price": pool,
                       "encrypt_to": (encrypt_to or "")[:100]}
            self.conn.execute(
                "INSERT INTO tasks(task_id,job_id,type,node_id,status,payload,result,worker_id,"
                "owner,lens,country,model,created,score,claimed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), job_id, "assisted", None, "open",
                 json.dumps(payload), None, None, None, "", "", "", _now(), None, None))
            self._save_ledger(); self.conn.commit()
        except Exception:
            self.ledger.refund(asker, pool)   # roll the in-memory hold back to keep ledger==DB
            raise
        return {"job_id": job_id, "status": "pending_assist", "price": pool}

    def _maybe_chain(self, job_id: str, asker: str, deliverable: str) -> None:
        """Stage chaining (D35/D39): if the just-completed `job_id` declares a `then` PIPELINE,
        materialize the NEXT stage — a job of ANY type, seeded with this job's deliverable — and pass
        the REMAINING stages down so the chain self-propagates (e.g. code_generation → assisted
        integrate, or research → chat → assisted). The deliverable seeds the next stage as `context`
        for an assisted stage, else as an "upstream result" appended to its instruction. Charged to
        the same asker; links parent↔child. Best-effort: a stage that can't start (bad spec, no
        affordable escrow, no workers, create error) just isn't chained — the parent's result stands.
        Runs under the caller's (reentrant) lock; create_job re-acquires it safely."""
        row = self.conn.execute("SELECT then_spec FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row or not row["then_spec"]:
            return
        try:
            stages = json.loads(row["then_spec"])
        except Exception:
            return
        if not isinstance(stages, list) or not stages:
            return
        stage, rest = stages[0], (stages[1:] or None)
        jtype = stage.get("type") if stage.get("type") in JOB_TYPES else "assisted"
        q = sanitize_brief(str(stage.get("question") or ""))
        if not q:
            return
        ctx = (deliverable or "")[:4000]
        requires = stage.get("requires") if isinstance(stage.get("requires"), dict) else None
        try:
            if jtype == "assisted":
                # only the assisted escrow needs a pre-check (avoid an orphan failed offer); create_job
                # handles affordability for the automated types itself.
                assisted_pool = pool_for("assisted")
                if not self.ledger.can_afford(asker, assisted_pool):
                    return
                child = self.create_job(asker, q, job_type="assisted", context=ctx,
                                        requires=requires, then=rest)
            else:
                qfull = f"{q}\n\nUpstream result to build on:\n{ctx}" if ctx else q
                child = self.create_job(asker, qfull, job_type=jtype, items=stage.get("items"),
                                        requires=requires, split=stage.get("split"),
                                        as_file=bool(stage.get("as_file")), then=rest)
        except Exception as exc:
            print(f"[chain] follow-on for job {job_id} failed: {type(exc).__name__}: {exc}", flush=True)
            return
        if child.get("status") in ("pending_assist", "pending_answers"):
            try:   # linking is also best-effort — a link failure must not fail the committed parent
                self.conn.execute("UPDATE jobs SET child=? WHERE job_id=?", (child["job_id"], job_id))
                self.conn.execute("UPDATE jobs SET parent=? WHERE job_id=?", (job_id, child["job_id"]))
                self.conn.commit()
            except Exception as exc:
                print(f"[chain] link parent {job_id}↔child {child['job_id']} failed: {exc}", flush=True)

    def assisted_offers(self, node: dict) -> list:
        """Open assisted offers this operator's node is eligible for (capability-matched).
        Returns the brief + BOUNDED context + price so the operator can give informed consent."""
        with self.lock:
            out = []
            for t in self.conn.execute(
                    "SELECT * FROM tasks WHERE type='assisted' AND status='open' ORDER BY created"):
                payload = json.loads(t["payload"]) if t["payload"] else {}
                req = payload.get("requires") or None
                if not self._meets(node, req) or not self._meets_reputation(node["owner"], req):
                    continue
                out.append({"task_id": t["task_id"], "job_id": t["job_id"],
                            "brief": payload.get("question", ""),
                            "context": payload.get("context", ""),
                            "requires": payload.get("requires") or {},
                            "price": payload.get("price"),
                            "encrypt_to": payload.get("encrypt_to", ""),
                            "age_s": round(_now() - t["created"], 1)})
            return out

    def accept_assisted(self, task_id: str, node_id: str, owner: str) -> dict:
        """Operator consents to + claims an open offer (atomic, under the lock). Returns the
        full brief+context. Guards: not your own offer (no self-deal), capability still met."""
        with self.lock:
            t = self.conn.execute(
                "SELECT * FROM tasks WHERE task_id=? AND type='assisted'", (task_id,)).fetchone()
            if not t:
                return {"ok": False, "error": "no such assisted task"}
            if t["status"] != "open":
                return {"ok": False, "error": f"already {t['status']}"}
            owner = _clip(owner)
            if not owner:
                # every assisted claim must bind to a non-empty operator identity — it's the
                # handle an asker pins for out-of-band key trust (D25); an empty one has no anchor.
                return {"ok": False, "error": "operator identity (owner) required to accept"}
            job = self.conn.execute("SELECT asker FROM jobs WHERE job_id=?", (t["job_id"],)).fetchone()
            if job and owner == job["asker"]:
                return {"ok": False, "error": "cannot accept your own assisted offer"}
            payload = json.loads(t["payload"]) if t["payload"] else {}
            req = payload.get("requires") or None
            node = self.get_node(node_id)
            # Capability gate: only enforced when the offer actually sets requirements. An
            # unregistered node can take a no-requirement task, but it can NEVER bypass a
            # capability requirement (unknown node → cannot prove capability → ineligible).
            if req and (node is None or not self._meets(dict(node), req)):
                return {"ok": False, "error": "your node does not meet this offer's requirements"}
            if not self._meets_reputation(owner, req):
                return {"ok": False, "error": "this offer requires a higher operator reputation"}
            self.ledger.open_account(owner)   # operator must have an account to be paid
            self.conn.execute(
                "UPDATE tasks SET status='claimed', node_id=?, owner=?, claimed_at=? WHERE task_id=?",
                (node_id, owner, _now(), task_id))
            self.conn.execute("UPDATE jobs SET status='assisting' WHERE job_id=?", (t["job_id"],))
            self._save_ledger()
            self.conn.commit()
            return {"ok": True, "task_id": task_id, "job_id": t["job_id"],
                    "brief": payload.get("question", ""), "context": payload.get("context", ""),
                    "encrypt_to": payload.get("encrypt_to", "")}

    def deliver_assisted(self, task_id: str, node_id: str, deliverable: str,
                         signature: str = "", signer_pub: str = "") -> dict:
        """Operator returns the owned deliverable; settle (operator paid the pool, conserved)."""
        with self.lock:
            t = self.conn.execute(
                "SELECT * FROM tasks WHERE task_id=? AND type='assisted'", (task_id,)).fetchone()
            if not t:
                return {"ok": False, "error": "no such assisted task"}
            if t["node_id"] != node_id:
                return {"ok": False, "error": "not your task"}
            if t["status"] == "done":
                return {"ok": False, "error": "already delivered"}
            job = self.conn.execute("SELECT * FROM jobs WHERE job_id=?", (t["job_id"],)).fetchone()
            # guard against settling a job the reaper already expired/failed (no double-pay,
            # no charging an asker for an offer they were told had lapsed).
            if job is None or job["status"] != "assisting":
                return {"ok": False,
                        "error": f"offer no longer open for delivery ({job and job['status']})"}
            # if it's a file artifact, refuse to pay unless every chunk was actually uploaded
            from council.artifacts import read_artifact, verify_manifest
            manifest = read_artifact(deliverable)
            if manifest is not None:
                if not verify_manifest(manifest) or not self.blobs_present(t["job_id"], manifest["chunks"]):
                    return {"ok": False, "error": "file incomplete — upload all chunks before delivering"}
            result = {"text": deliverable}
            if signature and signer_pub:   # D23: operator's signature over the deliverable
                result["signature"] = signature[:200]
                result["signer_pub"] = signer_pub[:100]
            result["_digest"] = self.result_digest(result)
            pool = job["pool"] or 0.0
            # release the escrow hold to the operator (asker was already debited at offer
            # creation, so this can't fail on asker balance and pays exactly once).
            self.ledger.release(t["owner"], pool)
            acct = self.ledger.accounts.get(t["owner"])
            if acct:
                acct.jobs_helped += 1
            receipt = {"job_id": t["job_id"], "asker_id": job["asker"], "total_cost": pool,
                       "payouts": {t["owner"]: pool}, "judge_fee": 0.0}
            # score stays NULL — it's the asker's rating slot (set by rate_assisted, D24)
            self.conn.execute("UPDATE tasks SET status='done', result=? WHERE task_id=?",
                              (json.dumps(result), task_id))
            self.conn.execute("UPDATE jobs SET status='done', merged=?, receipt=? WHERE job_id=?",
                              (deliverable, json.dumps(receipt), t["job_id"]))
            self._save_ledger(); self.conn.commit()
            # stage chaining (D35): spawn a `then` follow-on if this assisted job declared one
            # (currently only automated parents set then_spec, so this is a no-op for now —
            # symmetry + future assisted→assisted chains).
            self._maybe_chain(t["job_id"], job["asker"], deliverable)
            return {"ok": True, "job_id": t["job_id"]}

    # ------------------------------------------------------------------ blobs (D22)
    def put_blob(self, job_id: str, blob_hash: str, data: bytes) -> dict:
        """Store a content-addressed chunk for a job. Verifies the hash (content IS the
        address), enforces per-chunk + per-job-total caps. Operators upload here before
        delivering a manifest that references these blobs."""
        import hashlib as _hl
        if len(data) > 512 * 1024:   # chunk cap (codec uses 256 KiB; allow headroom)
            return {"ok": False, "error": "chunk too large"}
        if _hl.sha256(data).hexdigest() != blob_hash:
            return {"ok": False, "error": "hash does not match content"}
        with self.lock:
            # per-job total-bytes cap bounds a hostile operator filling the store (aligned to
            # the codec's per-file cap, with headroom for a few files per job).
            total = self.conn.execute(
                "SELECT COALESCE(SUM(LENGTH(data)),0) s FROM blobs WHERE job_id=?",
                (job_id,)).fetchone()["s"]
            if total + len(data) > 200 * 1024 * 1024:
                return {"ok": False, "error": "per-job storage cap reached"}
            self.conn.execute(
                "INSERT OR IGNORE INTO blobs(hash,job_id,data,created) VALUES(?,?,?,?)",
                (blob_hash, job_id, data, _now()))
            self.conn.commit()
            # confirm it's actually stored for THIS job (don't report false success)
            ok = self.conn.execute("SELECT 1 FROM blobs WHERE hash=? AND job_id=?",
                                   (blob_hash, job_id)).fetchone() is not None
            return {"ok": ok, "hash": blob_hash} if ok else {"ok": False, "error": "not stored"}

    def blobs_present(self, job_id: str, hashes: list) -> bool:
        """True iff every hash is stored for this job (used to gate payment on full upload)."""
        with self.lock:
            for h in hashes:
                if not self.conn.execute("SELECT 1 FROM blobs WHERE hash=? AND job_id=?",
                                         (h, job_id)).fetchone():
                    return False
            return True

    def get_blob(self, job_id: str, blob_hash: str) -> Optional[bytes]:
        """Fetch a chunk that belongs to this job (job-scoped so one asker can't read
        another job's blobs)."""
        with self.lock:
            row = self.conn.execute("SELECT data FROM blobs WHERE hash=? AND job_id=?",
                                    (blob_hash, job_id)).fetchone()
            return bytes(row["data"]) if row else None

    def job_asker(self, job_id: str) -> Optional[str]:
        with self.lock:
            row = self.conn.execute("SELECT asker FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return row["asker"] if row else None

    def assisted_claimant(self, job_id: str) -> Optional[str]:
        """node_id of the operator who claimed this job's assisted task (for blob-upload auth)."""
        with self.lock:
            row = self.conn.execute(
                "SELECT node_id FROM tasks WHERE job_id=? AND type='assisted'", (job_id,)).fetchone()
            return row["node_id"] if row else None

    def rate_assisted(self, job_id: str, asker: str, score: float) -> dict:
        """The asker rates a completed assisted deliverable (0-10). Feeds the operator's
        reputation (the same quality signal as council judge scores). One rating per job."""
        with self.lock:
            job = self.conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not job or (job["type"] or "") != "assisted":
                return {"ok": False, "error": "not an assisted job"}
            if _clip(asker) != job["asker"]:
                return {"ok": False, "error": "only the asker can rate this job"}
            if job["status"] != "done":
                return {"ok": False, "error": "job not delivered yet"}
            t = self.conn.execute(
                "SELECT task_id, owner, score FROM tasks WHERE job_id=? AND type='assisted' LIMIT 1",
                (job_id,)).fetchone()
            if not t or not t["owner"]:
                return {"ok": False, "error": "no operator to rate"}
            if t["score"] is not None:          # score stays NULL until rated → idempotent
                return {"ok": False, "error": "already rated"}
            s = self._sane_score(score)
            self.conn.execute("UPDATE tasks SET score=? WHERE task_id=?", (s, t["task_id"]))
            # Anti-farming (D24 review): a rating moves the operator's REPUTATION only if the
            # rater has independent earned standing (give/take — not a throwaway starter handle),
            # and at most once per (asker, operator) pair. The rating is always recorded above;
            # this only governs whether it counts toward the gate metric.
            acct = self.ledger.accounts.get(t["owner"])
            asker_acct = self.ledger.accounts.get(_clip(asker))
            counted = False
            if acct and asker_acct and asker_acct.lifetime_earned > 0:
                dup = self.conn.execute(
                    "SELECT 1 FROM rater_pairs WHERE asker=? AND operator=?",
                    (_clip(asker), t["owner"])).fetchone()
                if not dup:
                    acct.quality_sum = round(acct.quality_sum + s, 4)
                    acct.quality_n += 1
                    self.conn.execute("INSERT OR IGNORE INTO rater_pairs(asker,operator) VALUES(?,?)",
                                      (_clip(asker), t["owner"]))
                    counted = True
            self._save_ledger(); self.conn.commit()
            return {"ok": True, "counted_toward_reputation": counted,
                    "operator_reputation": acct.avg_quality if acct else 0.0}

    def operator_reputation(self, owner: str):
        """(avg_quality, num_ratings) for an owner — the marketplace trust signal."""
        a = self.ledger.accounts.get(_clip(owner))
        return (a.avg_quality, a.quality_n) if a else (0.0, 0)

    def job_status(self, job_id: str) -> Optional[str]:
        with self.lock:
            row = self.conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return row["status"] if row else None

    def jobs_for_asker(self, asker: str, limit: int = 25) -> list:
        """The asker's own question history (newest first) — powers the app's history list."""
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT job_id, question, status, created, type FROM jobs WHERE asker=?"
                " ORDER BY created DESC LIMIT ?", (asker, limit))]

    def set_baseline(self, job_id: str, data: dict) -> None:
        """Attach the independent single-model baseline (generated off-thread)."""
        with self.lock:
            self.conn.execute("UPDATE jobs SET baseline=? WHERE job_id=?",
                              (json.dumps(data), job_id))
            self.conn.commit()

    def next_task(self, node_id: str) -> Optional[dict]:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM tasks WHERE node_id=? AND status='queued' ORDER BY created LIMIT 1",
                (node_id,)).fetchone()
            if not row:
                return None
            self.conn.execute("UPDATE tasks SET status='claimed', claimed_at=? WHERE task_id=?",
                              (_now(), row["task_id"]))
            self.conn.commit()
            return {"task_id": row["task_id"], "job_id": row["job_id"], "type": row["type"],
                    "payload": json.loads(row["payload"]), "lens": row["lens"],
                    "country": row["country"], "model": row["model"]}

    def update_task_progress(self, node_id: str, task_id: str, done: int, total: int) -> bool:
        """A worker reports mid-flight progress on its CLAIMED task (D32). Node-ownership enforced
        (a node may only report on its own task). Stored as JSON {"done","total"}; job_view turns it
        into a job completion fraction. Review-hardened:
          • FORWARD progress (done strictly increases) resets the claim clock (`claimed_at`), so a
            slow-but-honest node keeps its work and isn't reassigned out from under itself.
          • A non-advancing report (same/lower done) is IGNORED — no write, no claim reset — so a
            node can neither spam writes (lock-contention DoS) nor keep a stalled claim alive forever
            without actually progressing.
          • `done > total` is rejected (a bounded, ordered contract — no misleading records).
        Best-effort — a rejected report never affects the result."""
        with self.lock:
            t = self.conn.execute("SELECT node_id, status, progress FROM tasks WHERE task_id=?",
                                  (task_id,)).fetchone()
            if not t or t["node_id"] != node_id or t["status"] == "done":
                return False
            try:
                d, tot = int(done), int(total)
            except (TypeError, ValueError):
                return False
            if tot <= 0 or d < 0 or d > tot:
                return False
            prev = 0
            if t["progress"]:
                try:
                    prev = int(json.loads(t["progress"]).get("done") or 0)
                except Exception:
                    prev = 0
            if d <= prev:
                return True   # accepted but ignored: no forward progress → no write, no claim reset
            self.conn.execute("UPDATE tasks SET progress=?, claimed_at=? WHERE task_id=?",
                              (json.dumps({"done": d, "total": tot}), _now(), task_id))
            self.conn.commit()
            return True

    @staticmethod
    def result_digest(result: dict) -> str:
        """Canonical SHA-256 of a task result — tamper-evidence (FEDERATION_V2 trust step 1).
        Any later alteration of a stored deliverable becomes detectable. Canonical = sorted
        keys, compact separators, so the hash is stable across serializations."""
        canonical = json.dumps(result, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def complete_task(self, task_id: str, result: dict, node_id: Optional[str] = None) -> bool:
        """node_id (when provided) must own the task — blocks task hijacking."""
        with self.lock:
            t = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not t or t["status"] == "done":
                return False
            if node_id is not None and t["node_id"] != node_id:
                return False  # not your task
            # stamp a tamper-evident digest into the stored result before persisting
            result = dict(result)
            result["_digest"] = self.result_digest({k: v for k, v in result.items()
                                                     if k != "_digest"})
            self.conn.execute("UPDATE tasks SET status='done', result=? WHERE task_id=?",
                              (json.dumps(result), task_id))
            self.conn.commit()
            if t["type"] == "answer":
                self._maybe_start_judging(t["job_id"])
            elif t["type"] == "judge":
                self._settle(t["job_id"], t["owner"], result)
            return True

    def _maybe_start_judging(self, job_id: str) -> None:
        answers = list(self.conn.execute(
            "SELECT * FROM tasks WHERE job_id=? AND type='answer'", (job_id,)))
        if any(a["status"] != "done" for a in answers):
            return
        if self.conn.execute("SELECT 1 FROM tasks WHERE job_id=? AND type='judge'",
                             (job_id,)).fetchone():
            return
        job = self.conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        judges = self.online_nodes(judge_only=True)
        if not judges:
            self.conn.execute("UPDATE jobs SET status='failed', error=? WHERE job_id=?",
                              ("no judge node online", job_id))
            self.conn.commit()
            return
        # Self-dealing guard: prefer a judge that did NOT answer this job.
        answer_owners = {a["owner"] for a in answers}
        external = [j for j in judges if j["owner"] not in answer_owners]
        judge = external[0] if external else judges[0]
        payload_answers = []
        for a in answers:
            res = json.loads(a["result"]) if a["result"] else {}
            entry = {"worker_id": a["worker_id"], "text": res.get("text", ""),
                     "model": a["model"], "lens": a["lens"], "country": a["country"],
                     # structured research contribution (findings + sources) for the editor pass
                     "research": res.get("research")}
            if task_behavior(job["type"]).sharded:
                # deterministic per-node sample for the judge's QA spot-check
                rows = res.get("results") or []
                seed = int(hashlib.sha256(f"{job_id}:{a['worker_id']}".encode()).hexdigest(), 16)
                idx = sorted({seed % max(1, len(rows)), (seed // 7) % max(1, len(rows)),
                              (seed // 131) % max(1, len(rows))})
                entry["sample"] = [rows[i] for i in idx][:3] if rows else []
            payload_answers.append(entry)
        self.conn.execute(
            "INSERT INTO tasks(task_id,job_id,type,node_id,status,payload,result,worker_id,"
            "owner,lens,country,model,created,score,claimed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), job_id, "judge", judge["node_id"], "queued",
             json.dumps({"question": job["question"], "answers": payload_answers,
                         "job_type": job["type"] or "chat"}), None,
             judge["node_id"], judge["owner"], "", judge["country"], judge["judge_model"],
             _now(), None, None))
        self.conn.execute("UPDATE jobs SET status='judging' WHERE job_id=?", (job_id,))
        self.conn.commit()

    @staticmethod
    def _sane_score(raw: Any) -> float:
        """Clamp to [0,10]; non-finite / non-numeric → 0.0. isfinite BEFORE min/max."""
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(v):
            return 0.0
        return max(0.0, min(10.0, v))

    def _settle(self, job_id: str, judge_owner: str, result: dict) -> None:
        job = self.conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not job or job["status"] not in ("judging", "pending_answers"):
            return
        answers = list(self.conn.execute(
            "SELECT * FROM tasks WHERE job_id=? AND type='answer'", (job_id,)))
        raw_scores = result.get("scores", {}) if isinstance(result.get("scores"), dict) else {}
        self_judged = any(a["owner"] == judge_owner for a in answers)

        # Compute sanitized per-answer scores (empty/errored answer → 0, no reputation).
        per_answer = []   # (task_id, owner, score, count_reputation)
        for a in answers:
            res = json.loads(a["result"]) if a["result"] else {}
            text = (res.get("text") or "").strip()
            errored = bool(res.get("error")) or not text
            if errored:
                s, rep = 0.0, False
            else:
                s, rep = self._sane_score(raw_scores.get(a["worker_id"], 5.0)), True
                # Self-dealing fallback: a judge that judged its own answer can't score itself high.
                if self_judged and a["owner"] == judge_owner:
                    s = 5.0
            per_answer.append((a["task_id"], a["owner"], s, rep))

        # D48: if NO answer produced usable output (workers crashed or returned empty), there is
        # nothing to pay for. Skip settlement entirely — otherwise settle_job charges the asker for a
        # blank result AND the degenerate even-split pays the failed workers. Mark the job failed,
        # uncharged. (A regular council job is charged only here at settle time, so skipping = no
        # charge; assisted escrow is handled separately by the reaper's refund path.) "Usable" is
        # type-aware: answer/report answers carry `text`; sharded answers (shard_map/download_extract/
        # code_generation) carry `results` and legitimately have no `text`, so must not be misflagged.
        sharded = task_behavior(job["type"]).assemble == "shards"

        def _produced(row) -> bool:
            r = json.loads(row["result"]) if row["result"] else {}
            if r.get("error"):
                return False
            if sharded:
                # a sharded worker returns a NON-EMPTY results list even when every item failed
                # (each item carries {"error": True, "output": "(error: …)"}), so bool(results) is
                # not enough — require at least one item that actually produced output (D48 review).
                return any(not it.get("error") for it in (r.get("results") or []))
            return bool((r.get("text") or "").strip())

        if answers and not any(_produced(a) for a in answers):
            self.conn.execute("UPDATE jobs SET status='failed', error=? WHERE job_id=?",
                              ("all answers failed — the job was not charged", job_id))
            self.conn.commit()
            return

        score_by_owner: dict[str, float] = {}
        for _, owner, s, _rep in per_answer:
            score_by_owner[owner] = score_by_owner.get(owner, 0.0) + s

        # FAIL-CLOSED: settle the ledger FIRST. Only on success do we write scores/rep/done.
        try:
            receipt = self.ledger.settle_job(
                job_id=job_id, asker_id=job["asker"], score_by_worker=score_by_owner,
                worker_pool=job["pool"] if job["pool"] else CONFIG.worker_pool,
                judge_id=judge_owner, judge_fee=CONFIG.judge_fee)
        except InsufficientCredit as exc:
            self.conn.execute("UPDATE jobs SET status='failed', error=? WHERE job_id=?",
                              (f"settlement failed: {exc}", job_id))
            self._save_ledger()
            self.conn.commit()
            return

        # sharded types (shard_map/download_extract/code_generation): the deliverable is the
        # ASSEMBLED shards in input order (the judge only spot-checks quality); overwrite merged
        # with the full results array (JSON).
        if task_behavior(job["type"]).assemble == "shards":
            allr = []
            for a in answers:
                res = json.loads(a["result"]) if a["result"] else {}
                allr.extend(res.get("results") or [])

            def _shard_i(r):   # coerce a malformed/foreign 'i' so the sort can't crash (review):
                try:           # TypeError(None)/ValueError("x"/nan)/OverflowError(inf) → 0
                    return int(r.get("i", 0))
                except (TypeError, ValueError, OverflowError):
                    return 0
            allr.sort(key=_shard_i)
            result = dict(result)
            if job["as_file"]:
                # D38 multi-producer file reassembly: combine the producers' parts (in input order)
                # into ONE document, chunk it into the per-job content-addressed blob store, and
                # deliver a manifest the asker fetches+verifies as a single signed file (pw fetch).
                # Runs under complete_task's lock (an RLock, reentrant — see __init__); we insert blobs
                # directly to defer to settle's single commit, and enforce the per-job storage cap here
                # ourselves (put_blob's cap is otherwise bypassed — review D38).
                from council.artifacts import chunk_bytes, wrap_artifact
                content = "\n\n".join(str(r.get("output", "")) for r in allr).encode("utf-8")
                try:
                    manifest, blobs = chunk_bytes(content, f"{job_id[:8]}-deliverable.txt")
                    used = self.conn.execute(
                        "SELECT COALESCE(SUM(LENGTH(data)),0) s FROM blobs WHERE job_id=?",
                        (job_id,)).fetchone()["s"]
                    if used + sum(len(b) for b in blobs.values()) > 200 * 1024 * 1024:
                        raise ValueError("per-job storage cap reached")
                    for h, b in blobs.items():
                        self.conn.execute(
                            "INSERT OR IGNORE INTO blobs(hash,job_id,data,created) VALUES(?,?,?,?)",
                            (h, job_id, b, _now()))
                    result["merged"] = wrap_artifact(manifest)
                except Exception:
                    result["merged"] = json.dumps(allr, ensure_ascii=False)   # fall back to JSON
            else:
                result["merged"] = json.dumps(allr, ensure_ascii=False)

        for task_id, owner, s, rep in per_answer:
            self.conn.execute("UPDATE tasks SET score=? WHERE task_id=?", (s, task_id))
            if rep:
                acct = self.ledger.accounts.get(owner)
                if acct:
                    acct.quality_sum = round(acct.quality_sum + s, 4)
                    acct.quality_n += 1
        council = result.get("council") if isinstance(result.get("council"), dict) else None
        self.conn.execute(
            "UPDATE jobs SET status='done', merged=?, receipt=?, council=? WHERE job_id=?",
            (result.get("merged", ""), json.dumps(receipt.__dict__),
             json.dumps(council) if council else None, job_id))
        self._save_ledger()
        self.conn.commit()
        # stage chaining (D35): now that this job is settled+committed, spawn its `then` follow-on
        # (no-op unless one was declared). After the commit so the parent is durable first.
        self._maybe_chain(job_id, job["asker"], result.get("merged", ""))

    # ------------------------------------------------------------------ failover (D32)
    def _pick_replacement(self, job: Any, task: Any) -> Optional[sqlite3.Row]:
        """A fresh node to take over a stalled task: a distinct, online, capable node, highest
        reputation first. Answer tasks → a new answer node not already in this job; judge tasks →
        an online judge node, preferring one that didn't answer (self-deal guard). None if there
        is no eligible replacement."""
        job_id = job["job_id"]
        assigned = {r["node_id"] for r in self.conn.execute(
            "SELECT node_id FROM tasks WHERE job_id=?", (job_id,))}

        def _rep(n):
            a = self.ledger.accounts.get(n["owner"])
            return a.avg_quality if a else 0.0

        if task["type"] == "judge":
            answer_owners = {r["owner"] for r in self.conn.execute(
                "SELECT owner FROM tasks WHERE job_id=? AND type='answer'", (job_id,))}
            cands = [n for n in self.online_nodes(judge_only=True) if n["node_id"] != task["node_id"]]
            external = [n for n in cands
                        if n["owner"] not in answer_owners and n["node_id"] not in assigned]
            pool_c = external or [n for n in cands if n["node_id"] not in assigned] or cands
        else:
            pool_c = [n for n in self.online_nodes()
                      if n["answer_model"] and n["node_id"] != task["node_id"]
                      and n["node_id"] not in assigned]
        if not pool_c:
            return None
        pool_c.sort(key=lambda n: (_rep(n), n["last_seen"]), reverse=True)
        return pool_c[0]

    def _reassign_task(self, task: Any, repl: sqlite3.Row) -> None:
        """Re-queue a stalled task to `repl`: new node/owner/locale, clear the claim+progress, and
        bump retries. The payload (the shard or the judge's answer set) is preserved, so the new
        node does exactly the same work. Pre-settle → no ledger effect (conservation holds)."""
        model = repl["judge_model"] if task["type"] == "judge" else repl["answer_model"]
        self.conn.execute(
            "UPDATE tasks SET node_id=?, worker_id=?, owner=?, lens=?, country=?, model=?, "
            "status='queued', claimed_at=NULL, progress=NULL, retries=? WHERE task_id=?",
            (repl["node_id"], repl["node_id"], repl["owner"], repl["lens"], repl["country"],
             model, (task["retries"] or 0) + 1, task["task_id"]))

    # ------------------------------------------------------------------ reaper
    def _reap_loop(self) -> None:
        interval = max(10.0, CONFIG.node_ttl_s / 2)
        while not self._stop.wait(interval):
            try:
                self._reap_once()
            except Exception:
                pass  # never let the reaper die

    def _reap_once(self) -> None:
        with self.lock:
            now = _now()
            # reclaim blobs of long-finished jobs (retention window gives the asker time to
            # fetch); bounds unbounded SQLite growth from delivered files.
            retain = float(os.environ.get("PW_BLOB_RETAIN_S", str(7 * 86400)))
            self.conn.execute(
                "DELETE FROM blobs WHERE job_id IN ("
                "  SELECT job_id FROM jobs WHERE status IN ('done','failed') AND created < ?)",
                (now - retain,))
            # assisted jobs are human-paced: only expire them on their (long) deadline,
            # never on node-liveness (no node is assigned until a human accepts).
            for job in list(self.conn.execute(
                    "SELECT * FROM jobs WHERE status IN ('pending_assist','assisting')")):
                deadline = JOB_TYPES["assisted"]["deadline_s"]
                if now - job["created"] > deadline:
                    # refund the held reward to the asker before failing (escrow → asker)
                    try:
                        if job["pool"]:
                            self.ledger.refund(job["asker"], job["pool"])
                    except Exception:
                        pass
                    self.conn.execute("UPDATE jobs SET status='failed', error=? WHERE job_id=?",
                                      (f"assisted offer expired ({int(deadline)}s)", job["job_id"]))
                    self._save_ledger()
            stuck = list(self.conn.execute(
                "SELECT * FROM jobs WHERE status IN ('pending_answers','judging')"))
            for job in stuck:
                jt = job["type"] or "chat"
                deadline = JOB_TYPES.get(jt, JOB_TYPES["chat"])["deadline_s"]
                if now - job["created"] > deadline:
                    self.conn.execute("UPDATE jobs SET status='failed', error=? WHERE job_id=?",
                                      (f"deadline exceeded ({int(deadline)}s)", job["job_id"]))
                    continue
                # Per-task FAILOVER (D32): a not-done task whose node went OFFLINE, or whose claim
                # has been held past the claim timeout WITHOUT a result, is "stalled". Reassign it
                # to a fresh capable node (status→queued, new owner, claim cleared, retries+1)
                # rather than failing the whole job. The job fails ONLY when no replacement exists
                # or a task's retries are exhausted. Settlement still runs once at the end (when the
                # judge completes), so the ledger is never touched here → conservation is preserved.
                claim_timeout = max(CONFIG.node_ttl_s, deadline * _CLAIM_TIMEOUT_FRAC)
                fail = None
                for t in list(self.conn.execute(
                        "SELECT * FROM tasks WHERE job_id=? AND status!='done'", (job["job_id"],))):
                    nd = self.conn.execute("SELECT last_seen FROM nodes WHERE node_id=?",
                                           (t["node_id"],)).fetchone()
                    offline = (not nd) or (now - nd["last_seen"] > CONFIG.node_ttl_s)
                    claim_stuck = bool(t["status"] == "claimed" and t["claimed_at"]
                                       and now - t["claimed_at"] > claim_timeout)
                    if not (offline or claim_stuck):
                        continue
                    if (t["retries"] or 0) >= _MAX_TASK_RETRIES:
                        fail = (f"task could not be completed after {_MAX_TASK_RETRIES} "
                                f"reassignment(s)")
                        break
                    repl = self._pick_replacement(job, t)
                    if not repl:
                        fail = ("no replacement node available "
                                + ("(assigned node offline)" if offline else "(node stalled)"))
                        break
                    self._reassign_task(t, repl)
                if fail:
                    self.conn.execute("UPDATE jobs SET status='failed', error=? WHERE job_id=?",
                                      (fail, job["job_id"]))
            self.conn.commit()

    # ------------------------------------------------------------------ views
    def job_view(self, job_id: str) -> Optional[dict]:
        with self.lock:
            job = self.conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not job:
                return None
            answers = list(self.conn.execute(
                "SELECT * FROM tasks WHERE job_id=? AND type='answer'", (job_id,)))
            nm = {r["node_id"]: (r["machine_id"] or r["node_id"])
                  for r in self.conn.execute("SELECT node_id, machine_id FROM nodes")}
            mkey = lambda nid: hashlib.sha256((nm.get(nid) or nid or "").encode()).hexdigest()[:12]
            label = {"queued": "assigned", "claimed": "thinking", "done": "answered"}
            ans = []
            for a in answers:
                res = json.loads(a["result"]) if a["result"] else {}
                ans.append({"worker_id": a["worker_id"], "owner": a["owner"], "model": a["model"],
                            "lens": a["lens"], "country": a["country"],
                            "node_key": hashlib.sha256((a["node_id"] or "").encode()).hexdigest()[:12],
                            "machine_key": mkey(a["node_id"]),
                            "status": a["status"], "status_label": label.get(a["status"], a["status"]),
                            "score": a["score"], "text": res.get("text", ""),
                            "tokens": res.get("tokens"), "elapsed_s": res.get("elapsed_s"),
                            # COORDINATOR-computed content hash of this result. It detects later
                            # alteration of the STORED answer; it is NOT a node signature and does
                            # NOT prove authorship (council answers are unsigned — verification is
                            # by quality, D5/D10). The real cryptographic guarantee is the operator
                            # Ed25519 signature on `assisted` deliverables (D23, the `sig` field).
                            "digest": res.get("_digest"),
                            "is_baseline": False})
            baseline = json.loads(job["baseline"]) if job["baseline"] else None
            done = [x for x in ans if x["status"] == "done" and x["score"] is not None]
            if done and not baseline:   # fallback only: best single COUNCIL answer as baseline
                max(done, key=lambda x: x["score"])["is_baseline"] = True
            jt = self.conn.execute(
                "SELECT node_id, country, status FROM tasks WHERE job_id=? AND type='judge' LIMIT 1",
                (job_id,)).fetchone()
            # assisted: surface the operator's signature over the deliverable (D23). The asker
            # verifies it against an OUT-OF-BAND PINNED key (D25), not a coordinator-reported one,
            # so the coordinator's view of the key is no longer part of the trust decision.
            sig = signer = None
            encrypt_to = ""
            operator = op_rep = op_ratings = None
            rated = False
            at = self.conn.execute(
                "SELECT result, payload, node_id, owner, score FROM tasks"
                " WHERE job_id=? AND type='assisted' LIMIT 1", (job_id,)).fetchone()
            if at:
                operator = at["owner"]
                rated = at["score"] is not None
                if operator:
                    op_rep, op_ratings = self.operator_reputation(operator)
                if at["result"]:
                    ares = json.loads(at["result"])
                    sig, signer = ares.get("signature"), ares.get("signer_pub")
                if at["payload"]:
                    encrypt_to = json.loads(at["payload"]).get("encrypt_to", "")
            # Overall completion fraction (D32): each answer task contributes its own done/total
            # (finished = 1.0, in-flight = its reported progress, queued = 0); the judge is one final
            # unit. A done job is 1.0; a failed job reports whatever fraction it reached.
            def _afrac(row):
                if row["status"] == "done":
                    return 1.0
                p = row["progress"]
                if p:
                    try:
                        pj = json.loads(p)
                        tot = float(pj.get("total") or 0)
                        return max(0.0, min(1.0, float(pj.get("done") or 0) / tot)) if tot > 0 else 0.0
                    except Exception:
                        return 0.0
                return 0.0
            if job["status"] == "done":
                progress = 1.0
            else:
                n_units = len(answers) + 1   # answer tasks + the judge stage
                judge_done = 1.0 if (jt and jt["status"] == "done") else 0.0
                progress = round((sum(_afrac(a) for a in answers) + judge_done) / n_units, 3)
            return {
                "job_id": job["job_id"], "asker": job["asker"], "question": job["question"],
                "type": job["type"] or "chat", "progress": progress,
                "parent": job["parent"], "child": job["child"],   # stage chain links (D35)
                "status": job["status"], "error": job["error"], "merged": job["merged"],
                "council": json.loads(job["council"]) if job["council"] else None,
                "judge_country": jt["country"] if jt else None,
                "judge_status": jt["status"] if jt else None,
                "judge_machine_key": mkey(jt["node_id"]) if jt else None,
                "receipt": json.loads(job["receipt"]) if job["receipt"] else None,
                "baseline": baseline,
                "signature": sig, "signer_pub": signer, "encrypt_to": encrypt_to,
                "operator": operator, "operator_reputation": op_rep,
                "operator_ratings": op_ratings, "rated": rated,
                "answers": ans,
            }

    def status(self) -> dict:
        with self.lock:
            nodes = []
            machines = set()
            for n in self.online_nodes():
                acct = self.ledger.accounts.get(n["owner"])
                machines.add(n["machine_id"] or n["node_id"])
                geo = (n["geo_country"] or "") if "geo_country" in n.keys() else ""
                nodes.append({
                    # opaque key (not the raw node_id, which authenticates nothing now but
                    # shouldn't be freely enumerable) — stable for the map's jitter.
                    "node_key": hashlib.sha256(n["node_id"].encode()).hexdigest()[:12],
                    "machine_key": hashlib.sha256((n["machine_id"] or n["node_id"]).encode()).hexdigest()[:12],
                    "name": n["name"], "country": n["country"], "owner": n["owner"],
                    "answer_model": n["answer_model"], "lens": n["lens"],
                    "can_judge": n["can_judge"], "load": n["load"],
                    "age_s": round(_now() - n["last_seen"], 1),
                    "reputation": acct.avg_quality if acct else 0.0,
                    "jobs_helped": acct.jobs_helped if acct else 0,
                    # D43: offline geo-verification ("" if unavailable). The raw IP is NEVER exposed.
                    "geo_country": geo,
                    "geo_mismatch": bool(geo and n["country"] and geo.upper() != (n["country"] or "").upper()),
                })
            # D48 privacy: the recent-jobs pulse is PSEUDONYMOUS — no asker handle and no job_id
            # (a job_id would be a readable capability into /jobs/{id}). Just the kind of work + its
            # state + how long ago, so the public map can breathe without leaking who asked what.
            now = _now()
            jobs = [{"type": (j["type"] or "chat"), "status": j["status"],
                     "age_s": round(now - (j["created"] or now), 0)}
                    for j in self.conn.execute(
                        "SELECT type, status, created FROM jobs ORDER BY created DESC LIMIT 10")]
            return {
                "online_nodes": nodes,
                "machines": len(machines),          # distinct physical computers online
                "minds": len(nodes),                # worker processes (a computer can run several)
                "recent_jobs": jobs,
                "ledger_total": self.ledger.total_credit(),
                "ledger_conserved": self.ledger.conservation_ok(),
                # D48: the raw per-account balance sheet (every handle's balance/reputation/jobs) is
                # NOT public. Pseudonymous OPERATOR rankings live at /leaderboard; a user reads their
                # OWN balance at /me. /status stays a public, non-identifying view of the network.
            }

    def leaderboard(self, limit: int = 20, sort: str = "reputation") -> dict:
        """Top operators (D44) — pseudonymous owners, aggregated across all their nodes (an Account
        is already the per-owner aggregate). A recruiting board, so:
          • reputation = mean judge score, and an owner needs >=1 rating to appear (a 0/0 newcomer
            must not tie the top contributor at 0.0);
          • credits = lifetime EARNED, never `balance` — an untouched starter grant must not top it.
        Returns only `owner` (never node_id/ip/secret) — see /status's no-IP-leak contract."""
        with self.lock:
            from council.ledger import ESCROW_ID
            online: dict[str, set] = {}      # owner -> set of countries they're serving from now
            for n in self.online_nodes():
                geo = (n["geo_country"] or "") if "geo_country" in n.keys() else ""
                cc = (geo or n["country"] or "").upper()
                bucket = online.setdefault(n["owner"], set())
                if cc and cc not in ("?", "LOCAL"):
                    bucket.add(cc)
            rows = []
            for owner, a in self.ledger.accounts.items():
                if owner == ESCROW_ID:
                    continue
                if sort == "reputation" and a.quality_n <= 0:
                    continue
                rows.append({
                    "owner": owner, "reputation": a.avg_quality, "ratings": a.quality_n,
                    "jobs_helped": a.jobs_helped, "credits_earned": round(a.lifetime_earned, 1),
                    "countries": sorted(online.get(owner, set())), "online": owner in online,
                })
            keys = {
                "reputation": lambda r: (r["reputation"], r["jobs_helped"]),
                "helped": lambda r: (r["jobs_helped"], r["credits_earned"]),
                "credits": lambda r: (r["credits_earned"], r["jobs_helped"]),
            }
            rows.sort(key=keys.get(sort, keys["reputation"]), reverse=True)
            return {"sort": sort, "operators": rows[:max(1, limit)], "total_operators": len(rows)}
