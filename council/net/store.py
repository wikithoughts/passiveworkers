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
import sqlite3
import threading
import uuid
from typing import Any, Optional

from council.ledger import InsufficientCredit
from council.net._store_assisted import _AssistedMixin
from council.net._store_base import _CLAIM_TIMEOUT_FRAC, _MAX_TASK_RETRIES, _clip, _hash as _hash, _norm_then, _now
from council.net._store_ledger import _LedgerMixin
from council.net._store_reporting import _ReportingMixin
from council.net.config import CONFIG, JOB_TYPES, pool_for, task_behavior
from council.sanitize import sanitize_brief

# _hash is re-exported (`from council.net._store_base import _hash as _hash`) so
# `from council.net.store import _hash` — used directly by tests/test_security_privacy.py —
# keeps working unchanged now that the helper itself lives in _store_base.py.


class Store(_AssistedMixin, _LedgerMixin, _ReportingMixin):
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
        # R10 review: a node that fails 100% of its tasks still looked fully healthy — heartbeat
        # carried only `load`. These counters make failure visible on /status.
        if "ok_count" not in ncols:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN ok_count INTEGER DEFAULT 0")
        if "fail_count" not in ncols:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN fail_count INTEGER DEFAULT 0")
        self.conn.commit()

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
                   split: Optional[list] = None, then: dict | list | None = None,
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
                payload: dict[str, Any] = {"question": question, "job_type": job_type}
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
                    # Refund the held reward to the asker BEFORE failing (escrow → asker). If the
                    # refund can't complete (e.g. a ledger desync), DON'T mark the job failed — leave
                    # it open so the next reap tick retries, rather than fail-and-strand the asker's
                    # hold. refund() is atomic (mutates nothing on failure), so the retry can't
                    # double-credit (R36 hardening; was `except: pass` → failed regardless).
                    try:
                        if job["pool"]:
                            self.ledger.refund(job["asker"], job["pool"])
                    except Exception:
                        continue   # refund failed → keep the offer open, retry next tick
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

