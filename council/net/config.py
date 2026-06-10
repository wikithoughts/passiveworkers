#!/usr/bin/env python3
"""
council/net/config.py — provider-agnostic configuration
=======================================================
Everything that ties the coordinator to a particular host is an environment variable,
so the service can be moved from this VPS to any rented host with NO code changes
(see docs/DECISIONS.md D8).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Where the coordinator listens. Loopback by default — expose only behind a tunnel/reverse
    # proxy you control (a non-loopback bind with a weak token is refused at startup).
    host: str = os.environ.get("PW_HOST", "127.0.0.1")
    port: int = int(os.environ.get("PW_PORT", "8088"))
    # Persistence (a single SQLite file → easy to relocate; swap to Postgres later via this seam).
    db_path: str = os.environ.get("PW_DB", "council_coordinator.db")
    # Shared secret required on every write endpoint (node register, poll, submit results).
    token: str = os.environ.get("PW_TOKEN", "dev-token")
    # Economy knobs (mirror council.coordinator).
    worker_pool: float = float(os.environ.get("PW_WORKER_POOL", "30"))
    judge_fee: float = float(os.environ.get("PW_JUDGE_FEE", "5"))
    # Orchestration.
    fleet_size: int = int(os.environ.get("PW_FLEET_SIZE", "3"))   # max answer-nodes per job
    node_ttl_s: float = float(os.environ.get("PW_NODE_TTL", "60"))  # node considered offline after this
    max_run_s: float = float(os.environ.get("PW_MAX_RUN", "300"))   # a job older than this is reaped → failed


CONFIG = Config()
