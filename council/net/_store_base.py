#!/usr/bin/env python3
"""council/net/_store_base.py — shared helpers/constants for the Store mixins.

Kept as its own module (rather than living in council/net/store.py) to avoid a
circular import: store.py composes the four Store mixins (`_store_reporting.py`,
`_store_ledger.py`, `_store_assisted.py`, `_store_jobs.py`), and those mixins need
these same small helpers — if the helpers lived in store.py itself, each mixin's
`from council.net.store import ...` would try to import a module that is still
mid-import (store.py imports the mixins near its own top).

store.py re-imports `_hash` from here (`from council.net._store_base import _hash as
_hash`) so `from council.net.store import _hash` — used directly by
tests/test_security_privacy.py — keeps working unchanged.

No lock, no DB connection: this module holds only pure helpers/constants, so it has
no bearing on the "exactly one RLock(), exactly one sqlite3.connect()" invariant
documented in store.py's module docstring.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Optional

from council.net.config import JOB_TYPES

_now = time.time  # server runtime (not a workflow script)
_FIELD_MAX = 80   # cap node/owner string lengths (defense-in-depth vs. abuse)
_MAX_CHAIN_STAGES = 8   # cap a `then` pipeline length (abuse bound)
# Failover (D32): how many times a task may be reassigned before the job fails, and how long a
# CLAIMED-but-undelivered task may sit (as a fraction of the job's deadline) before it's "stalled"
# even though its node still heartbeats. node-OFFLINE stalls are reassigned immediately.
_MAX_TASK_RETRIES = int(os.environ.get("PW_MAX_TASK_RETRIES", "2"))
_CLAIM_TIMEOUT_FRAC = float(os.environ.get("PW_CLAIM_TIMEOUT_FRAC", "0.6"))


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _clip(s: Any) -> str:
    return str(s if s is not None else "")[:_FIELD_MAX]


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
