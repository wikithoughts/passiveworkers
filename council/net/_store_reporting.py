#!/usr/bin/env python3
"""council/net/_store_reporting.py — Store's read-only reporting methods.

Extracted from council/net/store.py (R37, step 1/4): job status/history lookups, the
public /status and /leaderboard views, and the council-vs-single feedback tally. All
read-only — no ledger or task-table mutation lives here.

Same invariant as store.py: every method here runs under `self.lock` (the single
`threading.RLock()` created in Store.__init__) and reads via `self.conn`/`self.ledger`
— never a locally-constructed lock or a second connection. This is a mixin: it is
never instantiated on its own, only composed into council.net.store.Store alongside
the other Store mixins.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from council.net._store_base import _now


class _ReportingMixin:
    def metrics(self) -> dict:
        with self.lock:
            by = {r["verdict"]: r["c"] for r in
                  self.conn.execute("SELECT verdict, COUNT(*) c FROM feedback GROUP BY verdict")}
            council, single, tie = by.get("council", 0), by.get("single", 0), by.get("tie", 0)
            decisive = council + single
            return {"council": council, "single": single, "tie": tie,
                    "total": council + single + tie,
                    "council_win_rate": round(council / decisive, 3) if decisive else None}

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
                    # R10 review: task success/failure counts (self-reported by the node's own
                    # heartbeat) so a 100%-failing node stops looking indistinguishable from a
                    # healthy one on the public map.
                    "tasks_ok": (n["ok_count"] or 0) if "ok_count" in n.keys() else 0,
                    "tasks_failed": (n["fail_count"] or 0) if "fail_count" in n.keys() else 0,
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
