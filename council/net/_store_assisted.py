#!/usr/bin/env python3
"""council/net/_store_assisted.py — Store's assisted (human-in-the-loop) marketplace.

Extracted from council/net/store.py (R37, step 3/4): the D21 assisted offer lifecycle
(create → open → accept → deliver), stage chaining (D35/D39), the content-addressed
blob store (D22) that backs file deliverables, and operator rating/reputation (D24).

Uses `self._meets` / `self._meets_reputation` / `self._sane_score` / `self.result_digest`
/ `self.create_job` — all defined on `_JobsMixin`, composed alongside this mixin on the
final `Store` class (never on this mixin alone).

Same invariant as store.py: every method here runs under `self.lock` (the single
`threading.RLock()` created in Store.__init__) and reads/writes via `self.conn`/
`self.ledger` — never a locally-constructed lock or a second connection. This is a
mixin: it is never instantiated on its own, only composed into council.net.store.Store
alongside the other Store mixins.
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Optional

from council.ledger import InsufficientCredit
from council.net._store_base import _StoreProtocol, _clip, _now
from council.net.config import JOB_TYPES, pool_for
from council.sanitize import sanitize_brief


class _AssistedMixin(_StoreProtocol):
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
