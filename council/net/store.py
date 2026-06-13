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
import secrets as _secrets
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

from council.ledger import Account, InsufficientCredit, Ledger
from council.net.config import CONFIG, JOB_TYPES

_now = time.time  # server runtime (not a workflow script)
_FIELD_MAX = 80   # cap node/owner string lengths (defense-in-depth vs. abuse)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _clip(s: Any) -> str:
    return str(s if s is not None else "")[:_FIELD_MAX]


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
        # migrations (ALTER TABLE on boot — re-installs must never wipe the DB)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        if "baseline" not in cols:   # independent single-model baseline (council/net/baseline.py)
            self.conn.execute("ALTER TABLE jobs ADD COLUMN baseline TEXT")
        if "pool" not in cols:       # per-job worker pool (responder dial: cost scales with minds)
            self.conn.execute("ALTER TABLE jobs ADD COLUMN pool REAL")
        if "type" not in cols:       # job type — async work marketplace (D13); null/legacy = chat
            self.conn.execute("ALTER TABLE jobs ADD COLUMN type TEXT")
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

    # ------------------------------------------------------------------ nodes
    def register_node(self, body: dict, ip: str = "") -> dict:
        """Returns {node_id, node_secret}. The secret is shown ONCE; only its hash is stored."""
        with self.lock:
            node_id = str(uuid.uuid4())
            secret = _secrets.token_urlsafe(24)
            self.ledger.open_account(_clip(body["owner"]))
            self.conn.execute(
                "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    node_id, _clip(body.get("name", "node")), _clip(body.get("country", "?")),
                    _clip(body["owner"]), _clip(body.get("answer_model", "")),
                    _clip(body.get("lens", "neutral")), int(bool(body.get("can_judge", False))),
                    _clip(body.get("judge_model", "")), json.dumps(body.get("profile", {})),
                    _now(), 0.0, "online", ip, _hash(secret), _clip(body.get("machine_id", "?")),
                ),
            )
            self._save_ledger()
            self.conn.commit()
            return {"node_id": node_id, "node_secret": secret}

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
    def register_user(self, handle: str) -> dict:
        handle = _clip(handle).strip() or "anon"
        with self.lock:
            if self.conn.execute("SELECT 1 FROM users WHERE handle=?", (handle,)).fetchone():
                return {"error": "handle taken"}
            secret = _secrets.token_urlsafe(24)
            self.conn.execute("INSERT INTO users(handle, secret_hash, created) VALUES(?,?,?)",
                              (handle, _hash(secret), _now()))
            self.ledger.open_account(handle)
            self._save_ledger()
            self.conn.commit()
            return {"handle": handle, "user_secret": secret, **self.user_balance(handle)}

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
            if not self.conn.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone():
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

    def create_job(self, asker: str, question: str, minds: int | None = None,
                   job_type: str = "chat", items: Optional[list] = None,
                   requires: Optional[dict] = None, fetch: bool = False,
                   context: str = "") -> dict:
        with self.lock:
            asker = _clip(asker)
            if job_type not in JOB_TYPES:
                job_type = "chat"
            # assisted (D21): human-in-the-loop work. NOT pre-assigned — it's an OPEN offer
            # any consenting, capable operator may claim, do (with their own AI or by hand),
            # and deliver. No autonomous computer-use by us; the human is the agent.
            if job_type == "assisted":
                return self._create_assisted(asker, question, context, requires)
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
            pool = round(CONFIG.worker_pool / CONFIG.fleet_size * len(workers)
                         * JOB_TYPES[job_type]["pool_mult"], 4)
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

            # shard_map: split the items across the selected workers (round-robin, keeping
            # each item's global index so the merged output preserves input order).
            shards: dict = {}
            if job_type == "shard_map":
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
                shards = {w["node_id"]: [] for w in workers}
                for idx, it in enumerate(clean):
                    shards[workers[idx % len(workers)]["node_id"]].append({"i": idx, "item": it})

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

            self.conn.execute(
                "INSERT INTO jobs(job_id,asker,question,status,created,merged,receipt,error,council,pool,type)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, asker, question, "pending_answers", _now(), None, None, None, None, pool, job_type))
            for n in workers:
                payload = {"question": question, "job_type": job_type}
                if job_type == "shard_map":
                    payload["shard"] = shards.get(n["node_id"], [])
                    payload["fetch"] = bool(fetch)
                self.conn.execute(
                    "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), job_id, "answer", n["node_id"], "queued",
                     json.dumps(payload), None,
                     n["node_id"], n["owner"],
                     n["lens"], n["country"], n["answer_model"], _now(), None, None))
            self._save_ledger()   # persist the new asker account before acknowledging
            self.conn.commit()
            return {"job_id": job_id, "status": "pending_answers",
                    "assigned": [n["node_id"] for n in workers]}

    # ------------------------------------------------------------------ assisted (D21)
    def _create_assisted(self, asker: str, question: str, context: str,
                         requires: Optional[dict]) -> dict:
        """Create an OPEN assisted offer (called under self.lock from create_job)."""
        job_id = str(uuid.uuid4())
        pool = round(CONFIG.worker_pool / CONFIG.fleet_size * JOB_TYPES["assisted"]["pool_mult"], 4)
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
        self.conn.execute(
            "INSERT INTO jobs(job_id,asker,question,status,created,merged,receipt,error,council,pool,type)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, asker, question, "pending_assist", _now(), None, None, None, None, pool, "assisted"))
        # ONE open task, no node_id — any consenting capable operator may claim it.
        payload = {"question": question, "job_type": "assisted",
                   "context": (context or "")[:4000], "requires": requires or {}, "price": pool}
        self.conn.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), job_id, "assisted", None, "open",
             json.dumps(payload), None, None, None, "", "", "", _now(), None, None))
        self._save_ledger(); self.conn.commit()
        return {"job_id": job_id, "status": "pending_assist", "price": pool}

    def assisted_offers(self, node: dict) -> list:
        """Open assisted offers this operator's node is eligible for (capability-matched).
        Returns the brief + BOUNDED context + price so the operator can give informed consent."""
        with self.lock:
            out = []
            for t in self.conn.execute(
                    "SELECT * FROM tasks WHERE type='assisted' AND status='open' ORDER BY created"):
                payload = json.loads(t["payload"]) if t["payload"] else {}
                if not self._meets(node, payload.get("requires") or None):
                    continue
                out.append({"task_id": t["task_id"], "job_id": t["job_id"],
                            "brief": payload.get("question", ""),
                            "context": payload.get("context", ""),
                            "requires": payload.get("requires") or {},
                            "price": payload.get("price"),
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
            job = self.conn.execute("SELECT asker FROM jobs WHERE job_id=?", (t["job_id"],)).fetchone()
            if job and owner == job["asker"]:
                return {"ok": False, "error": "cannot accept your own assisted offer"}
            payload = json.loads(t["payload"]) if t["payload"] else {}
            node = self.get_node(node_id)
            if node is not None and not self._meets(dict(node), payload.get("requires") or None):
                return {"ok": False, "error": "your node does not meet this offer's requirements"}
            self.ledger.open_account(owner)   # operator must have an account to be paid
            self.conn.execute(
                "UPDATE tasks SET status='claimed', node_id=?, owner=?, claimed_at=? WHERE task_id=?",
                (node_id, owner, _now(), task_id))
            self.conn.execute("UPDATE jobs SET status='assisting' WHERE job_id=?", (t["job_id"],))
            self._save_ledger()
            self.conn.commit()
            return {"ok": True, "task_id": task_id, "job_id": t["job_id"],
                    "brief": payload.get("question", ""), "context": payload.get("context", "")}

    def deliver_assisted(self, task_id: str, node_id: str, deliverable: str) -> dict:
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
            result = {"text": deliverable}
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
            self.conn.execute("UPDATE tasks SET status='done', result=?, score=? WHERE task_id=?",
                              (json.dumps(result), 10.0, task_id))
            self.conn.execute("UPDATE jobs SET status='done', merged=?, receipt=? WHERE job_id=?",
                              (deliverable, json.dumps(receipt), t["job_id"]))
            self._save_ledger(); self.conn.commit()
            return {"ok": True, "job_id": t["job_id"]}

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
            if (job["type"] or "") == "shard_map":
                # deterministic per-node sample for the judge's QA spot-check
                rows = res.get("results") or []
                seed = int(hashlib.sha256(f"{job_id}:{a['worker_id']}".encode()).hexdigest(), 16)
                idx = sorted({seed % max(1, len(rows)), (seed // 7) % max(1, len(rows)),
                              (seed // 131) % max(1, len(rows))})
                entry["sample"] = [rows[i] for i in idx][:3] if rows else []
            payload_answers.append(entry)
        self.conn.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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

        # shard_map: the deliverable is the ASSEMBLED shards in input order (the judge only
        # spot-checks quality); overwrite merged with the full results array (JSON).
        if (job["type"] or "") == "shard_map":
            allr = []
            for a in answers:
                res = json.loads(a["result"]) if a["result"] else {}
                allr.extend(res.get("results") or [])
            allr.sort(key=lambda r: r.get("i", 0))
            result = dict(result)
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
                fail = None
                deadline = JOB_TYPES.get(job["type"] or "chat", JOB_TYPES["chat"])["deadline_s"]
                if now - job["created"] > deadline:
                    fail = f"deadline exceeded ({int(deadline)}s)"
                else:
                    # If any not-done task's assigned node has gone stale, the job can't progress.
                    tasks = self.conn.execute(
                        "SELECT node_id, status FROM tasks WHERE job_id=? AND status!='done'",
                        (job["job_id"],)).fetchall()
                    for t in tasks:
                        nd = self.conn.execute("SELECT last_seen FROM nodes WHERE node_id=?",
                                               (t["node_id"],)).fetchone()
                        if not nd or now - nd["last_seen"] > CONFIG.node_ttl_s:
                            fail = "assigned node went offline"
                            break
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
                            "digest": res.get("_digest"),   # tamper-evidence (FEDERATION_V2)
                            "is_baseline": False})
            baseline = json.loads(job["baseline"]) if job["baseline"] else None
            done = [x for x in ans if x["status"] == "done" and x["score"] is not None]
            if done and not baseline:   # fallback only: best single COUNCIL answer as baseline
                max(done, key=lambda x: x["score"])["is_baseline"] = True
            jt = self.conn.execute(
                "SELECT node_id, country, status FROM tasks WHERE job_id=? AND type='judge' LIMIT 1",
                (job_id,)).fetchone()
            return {
                "job_id": job["job_id"], "asker": job["asker"], "question": job["question"],
                "type": job["type"] or "chat",
                "status": job["status"], "error": job["error"], "merged": job["merged"],
                "council": json.loads(job["council"]) if job["council"] else None,
                "judge_country": jt["country"] if jt else None,
                "judge_status": jt["status"] if jt else None,
                "judge_machine_key": mkey(jt["node_id"]) if jt else None,
                "receipt": json.loads(job["receipt"]) if job["receipt"] else None,
                "baseline": baseline,
                "answers": ans,
            }

    def status(self) -> dict:
        with self.lock:
            nodes = []
            machines = set()
            for n in self.online_nodes():
                acct = self.ledger.accounts.get(n["owner"])
                machines.add(n["machine_id"] or n["node_id"])
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
                })
            jobs = [dict(j) for j in self.conn.execute(
                "SELECT job_id, asker, status, created FROM jobs ORDER BY created DESC LIMIT 10")]
            from council.ledger import ESCROW_ID
            accounts = [(u, a) for u, a in self.ledger.accounts.items()
                        if u != ESCROW_ID]   # hide the internal escrow holding account
            return {
                "online_nodes": nodes,
                "machines": len(machines),          # distinct physical computers online
                "minds": len(nodes),                # worker processes (a computer can run several)
                "recent_jobs": jobs,
                "ledger_total": self.ledger.total_credit(),
                "ledger_conserved": self.ledger.conservation_ok(),
                "accounts": {u: {"balance": round(a.balance, 1), "reputation": a.avg_quality,
                                 "helped": a.jobs_helped, "asked": a.jobs_asked}
                             for u, a in accounts},
            }
