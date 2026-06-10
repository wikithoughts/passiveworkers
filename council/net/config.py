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
    # Honest compare baseline — the answer the council is judged AGAINST in the demand metric.
    # A frontier API model if a key is set (the asker's real-world alternative), else a strong
    # local model. Without either, the compare falls back to the best single council answer
    # (labelled as such — that only measures merge-vs-ingredient, not real demand).
    baseline_api_key: str = os.environ.get("PW_BASELINE_API_KEY", "")
    baseline_api_url: str = os.environ.get("PW_BASELINE_API_URL",
                                           "https://openrouter.ai/api/v1/chat/completions")
    baseline_model: str = os.environ.get("PW_BASELINE_MODEL", "openai/gpt-4o-mini")
    baseline_local_model: str = os.environ.get("PW_BASELINE_LOCAL_MODEL", "qwen3:14b")
    # CPU inference on a busy box is slow; the local baseline is generated AFTER the council
    # finishes (no core contention) and may take a few minutes. API baselines run immediately.
    baseline_timeout_s: float = float(os.environ.get("PW_BASELINE_TIMEOUT", "600"))
    ollama_base: str = os.environ.get("PW_OLLAMA_BASE", "http://127.0.0.1:11434")


CONFIG = Config()

# ---- Job-type catalog ("Upwork for computers" — see docs/DECISIONS.md D13) ----
# Each type is a different latency class with its own price and deadline. `pool_mult`
# scales the per-mind worker pool (real work costs real credits); `deadline_s` replaces
# the single global reaper wall for that job's lifetime.
JOB_TYPES: dict = {
    "chat": {
        "label": "Ask the council",
        "eta": "3–8 min",
        "pool_mult": 1.0,
        "deadline_s": float(os.environ.get("PW_MAX_RUN", "600")),
    },
    "research_report": {
        "label": "Deep research — many computers, many countries",
        "eta": "20–40 min",
        "pool_mult": 3.0,
        "deadline_s": float(os.environ.get("PW_RESEARCH_MAX_RUN", "3600")),
    },
}
