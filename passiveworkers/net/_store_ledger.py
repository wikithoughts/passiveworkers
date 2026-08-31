#!/usr/bin/env python3
"""passiveworkers/net/_store_ledger.py — Store's ledger/economy + node/user identity methods.

Extracted from passiveworkers/net/store.py (R37, step 2/4): ledger persistence, enrollment
tokens (D37), node registration/heartbeat/lookup, and asker (user) registration/
lookup/balance.

Same invariant as store.py: every method here runs under `self.lock` (the single
`threading.RLock()` created in Store.__init__) and reads/writes via `self.conn`/
`self.ledger` — never a locally-constructed lock or a second connection. This is a
mixin: it is never instantiated on its own, only composed into passiveworkers.net.store.Store
alongside the other Store mixins.
"""

from __future__ import annotations

import json
import math
import secrets as _secrets
import sqlite3
import uuid
from typing import Optional

from passiveworkers.ledger import STARTER_ALLOWANCE, Account, Ledger
from passiveworkers.net._store_base import _StoreProtocol, _clip, _hash, _now
from passiveworkers.net.config import CONFIG


class _LedgerMixin(_StoreProtocol):
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
        token = _secrets.token_hex(24)
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
                secret = _secrets.token_hex(24)
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

    def heartbeat(self, node_id: str, load: float = 0.0, tasks_ok: int = 0,
                 tasks_failed: int = 0) -> bool:
        with self.lock:
            cur = self.conn.execute(
                "UPDATE nodes SET last_seen=?, load=?, status='online', ok_count=?, fail_count=? "
                "WHERE node_id=?",
                (_now(), load, tasks_ok, tasks_failed, node_id))
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
            secret = _secrets.token_hex(24)
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
