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
    "shard_map": {
        "label": "Batch work — one big job split across computers",
        "eta": "scales with items ÷ computers",
        "pool_mult": 2.0,
        "deadline_s": float(os.environ.get("PW_BATCH_MAX_RUN", "3600")),
    },
    "assisted": {
        "label": "Assisted task — a person does it on their computer (with consent)",
        "eta": "minutes to hours (human-paced)",
        "pool_mult": 5.0,   # real human-mediated work; priced above automated jobs
        "deadline_s": float(os.environ.get("PW_ASSIST_MAX_RUN", "86400")),  # 24h to be claimed+done
    },
    # D33: a batch of PUBLIC URLs split across computers; each node fetches+extracts (D4: returns the
    # model's extraction, never raw bytes — compute-over-fetched-data, never a proxy).
    "download_extract": {
        "label": "Download + extract — split a list of URLs across computers",
        "eta": "scales with URLs ÷ computers",
        "pool_mult": 2.0,
        "deadline_s": float(os.environ.get("PW_BATCH_MAX_RUN", "3600")),
    },
    # D33: generate code units from specs, split across computers. GENERATION only — running/linking
    # the code is the gated track (D15/D18 → routes through `assisted`), never auto-executed here.
    "code_generation": {
        "label": "Code generation — split specs across computers (generation only)",
        "eta": "scales with specs ÷ computers",
        "pool_mult": 2.0,
        "deadline_s": float(os.environ.get("PW_BATCH_MAX_RUN", "3600")),
    },
}


# ---- task-type behavior registry (D33) ----------------------------------------------------------
# One source of truth for how each job type is orchestrated, so adding a type no longer means editing
# five scattered `if job_type == ...` sites (coordinator split/assemble/judge-sample + worker
# executor/judge dispatch). `JOB_TYPES` above is the economics catalog; this is the behavior.
@dataclass(frozen=True)
class TaskBehavior:
    executor: str = "perspective"   # which worker runs it: perspective | research | batch
    sharded: bool = False           # items split across workers (scatter), assembled in order (gather)
    fetch: bool = False             # ALWAYS fetch+extract each item as a PUBLIC URL (D4 compute-over-data)
    allow_user_fetch: bool = False  # may the asker's `fetch` flag turn fetching on for this type?
    judge: str = "deliberate"       # judge stage: deliberate | compile_report | spot_check
    assemble: str = "merge"         # how _settle builds the deliverable: merge | shards
    framing: str = ""               # trusted instruction prefix applied by the worker for this type


TASK_BEHAVIORS: dict[str, TaskBehavior] = {
    "chat": TaskBehavior(executor="perspective", judge="deliberate", assemble="merge"),
    "research_report": TaskBehavior(executor="research", judge="compile_report", assemble="merge"),
    # shard_map MAY fetch if the asker opts in (the original D15 fetch:true batch); the two typed
    # batch types below are NOT user-overridable — download always fetches, code never does (review).
    "shard_map": TaskBehavior(executor="batch", sharded=True, allow_user_fetch=True,
                              judge="spot_check", assemble="shards"),
    "download_extract": TaskBehavior(
        executor="batch", sharded=True, fetch=True, judge="spot_check", assemble="shards",
        framing=("For each SOURCE URL, read the page and extract ONLY what the task asks for. "
                 "Return the extracted facts in your own words — never the raw page, never code to "
                 "fetch it.")),
    "code_generation": TaskBehavior(
        executor="batch", sharded=True, judge="spot_check", assemble="shards",
        framing=("You are generating code. For each SPEC, output ONE self-contained, correct code "
                 "unit and nothing else — no prose, no explanation. Do NOT run or install anything.")),
}


def task_behavior(job_type: str | None) -> TaskBehavior:
    """The orchestration behavior for a job type (unknown/None → chat). `assisted` is a separate
    human-mediated lifecycle and never flows through this dispatch."""
    return TASK_BEHAVIORS.get(job_type or "chat", TASK_BEHAVIORS["chat"])
