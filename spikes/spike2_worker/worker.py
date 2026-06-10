#!/usr/bin/env python3
"""
Spike 2 — Worker / Ollama Wrapper PoC for Passive Workers
==========================================================
Demonstrates the supply-side core:
  • Hardware profiling (RAM, CPU cores, GPU detection, available Ollama models)
  • Capability registration with a stub coordinator
  • Job polling loop: pull → run via Ollama → submit result + earned credits
  • Background-friendliness: CPU ceiling gate (pauses when system is busy)
  • Clean shutdown on Ctrl-C / SIGTERM

Success bar: end-to-end job round-trip with credits earned, daemon unobtrusive.

Requires: ollama running locally with gemma3:4b pulled.
          pip install psutil requests  (already in .venv)
Run:  python3 worker.py
"""

import json
import platform
import signal
import subprocess
import sys
import threading
import time
import uuid
from typing import Optional

import psutil
import requests

OLLAMA_BASE      = "http://localhost:11434"
DEFAULT_MODEL    = "gemma3:4b"
CPU_CEILING_PCT  = 70.0   # pause inference if system CPU% exceeds this
POLL_INTERVAL_S  = 2.0    # seconds between job-queue polls when idle

# Credit rate: 1 credit per 10 tokens produced (placeholder; real rate = dynamic)
CREDITS_PER_TOKEN = 0.10


# --------------------------------------------------------------------------- #
#  Hardware profiling
# --------------------------------------------------------------------------- #

def _detect_gpu_macos() -> Optional[str]:
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True, text=True, timeout=6,
        )
        data = json.loads(out.stdout)
        gpus = [d.get("sppci_model") or d.get("_name", "")
                for d in data.get("SPDisplaysDataType", [])]
        return ", ".join(g for g in gpus if g) or None
    except Exception:
        return None


def _detect_gpu_nvidia() -> Optional[str]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def detect_gpu() -> Optional[str]:
    return _detect_gpu_macos() or _detect_gpu_nvidia()


def profile_hardware() -> dict:
    mem = psutil.virtual_memory()
    cpu_phys = psutil.cpu_count(logical=False) or psutil.cpu_count()

    try:
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=10)
        models = [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        models = []

    return {
        "worker_id":          str(uuid.uuid4()),
        "os":                 platform.system(),
        "os_version":         platform.version()[:80],
        "cpu_brand":          platform.processor() or platform.machine(),
        "cpu_physical_cores": cpu_phys,
        "ram_total_gb":       round(mem.total / 1e9, 1),
        "ram_available_gb":   round(mem.available / 1e9, 1),
        "gpu":                detect_gpu(),
        "ollama_models":      models,
    }


# --------------------------------------------------------------------------- #
#  Stub coordinator (in-process; real = Supabase + Edge Functions)
# --------------------------------------------------------------------------- #

class StubCoordinator:
    """
    Simulates the coordinator's job queue and ledger.
    In production this is a Supabase project: Postgres rows + Realtime push.
    """

    def __init__(self):
        self._queue: list[dict] = []
        self._completed: list[dict] = []
        self._credits: dict[str, float] = {}
        self._lock = threading.Lock()

    def register_worker(self, profile: dict) -> str:
        wid = profile["worker_id"]
        self._credits[wid] = 0.0
        print(
            f"[coordinator] Worker registered\n"
            f"              id      = {wid[:8]}…\n"
            f"              os      = {profile['os']} {profile['os_version'][:40]}\n"
            f"              cpu     = {profile['cpu_brand'][:50]} × {profile['cpu_physical_cores']} cores\n"
            f"              ram     = {profile['ram_total_gb']} GB total, "
            f"{profile['ram_available_gb']} GB free\n"
            f"              gpu     = {profile['gpu'] or 'none detected'}\n"
            f"              models  = {profile['ollama_models'][:4]}"
        )
        return wid

    def enqueue(self, model: str, prompt: str) -> str:
        jid = str(uuid.uuid4())[:8]
        with self._lock:
            self._queue.append({"job_id": jid, "model": model, "prompt": prompt})
        print(f"[coordinator] Enqueued job {jid} | model={model} | prompt={prompt[:50]!r}")
        return jid

    def pull_job(self, worker_id: str) -> Optional[dict]:
        with self._lock:
            return self._queue.pop(0) if self._queue else None

    def submit_result(self, worker_id: str, job_id: str, result: str,
                      elapsed_s: float, tokens: int) -> float:
        earned = round(tokens * CREDITS_PER_TOKEN, 2)
        self._credits[worker_id] = round(self._credits.get(worker_id, 0.0) + earned, 2)
        rec = {
            "job_id":        job_id,
            "worker_id":     worker_id[:8],
            "elapsed_s":     round(elapsed_s, 2),
            "tokens":        tokens,
            "credits_earned": earned,
            "balance":       self._credits[worker_id],
            "result_preview": (result[:80] + "…") if len(result) > 80 else result,
        }
        with self._lock:
            self._completed.append(rec)
        print(
            f"[coordinator] Job {job_id} complete | "
            f"{elapsed_s:.1f}s | {tokens} tokens | "
            f"+{earned} credits → balance {self._credits[worker_id]}"
        )
        return earned

    @property
    def completed(self) -> list[dict]:
        with self._lock:
            return list(self._completed)

    def credit_balance(self, worker_id: str) -> float:
        return self._credits.get(worker_id, 0.0)


# --------------------------------------------------------------------------- #
#  Worker daemon
# --------------------------------------------------------------------------- #

class WorkerDaemon:
    """
    Background agent that polls the coordinator, runs jobs via Ollama,
    and submits results. Pauses when the system is under load so it
    remains unobtrusive to the machine's owner.
    """

    def __init__(self, coordinator: StubCoordinator):
        self.coordinator = coordinator
        self.profile = profile_hardware()
        self.worker_id = coordinator.register_worker(self.profile)
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self.jobs_run = 0

    # ---------------------------------------------------------------------- #
    #  Inference
    # ---------------------------------------------------------------------- #

    def _run_ollama(self, model: str, prompt: str) -> tuple[str, int, float]:
        """Returns (text, token_count, elapsed_seconds)."""
        t0 = time.monotonic()
        resp = requests.post(f"{OLLAMA_BASE}/api/generate", json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 300},
        }, timeout=240)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.monotonic() - t0
        text = data.get("response", "").strip()
        # Ollama returns eval_count = number of tokens generated
        tokens = data.get("eval_count") or len(text.split())
        return text, tokens, elapsed

    # ---------------------------------------------------------------------- #
    #  Backpressure
    # ---------------------------------------------------------------------- #

    def _system_overloaded(self) -> bool:
        """Sample CPU for 0.5 s; return True if above ceiling."""
        cpu = psutil.cpu_percent(interval=0.5)
        return cpu > CPU_CEILING_PCT

    # ---------------------------------------------------------------------- #
    #  Main loop
    # ---------------------------------------------------------------------- #

    def _loop(self):
        while self._running:
            if self._system_overloaded():
                if not self._paused:
                    print(f"[worker] CPU > {CPU_CEILING_PCT}% — pausing until load eases")
                    self._paused = True
                time.sleep(POLL_INTERVAL_S)
                continue
            if self._paused:
                print("[worker] CPU load eased — resuming")
                self._paused = False

            job = self.coordinator.pull_job(self.worker_id)
            if not job:
                time.sleep(POLL_INTERVAL_S)
                continue

            jid = job["job_id"]
            print(f"[worker]  Starting job {jid} | model={job['model']}")
            try:
                text, tokens, elapsed = self._run_ollama(job["model"], job["prompt"])
                self.coordinator.submit_result(
                    self.worker_id, jid, text, elapsed, tokens
                )
                self.jobs_run += 1
            except Exception as exc:
                print(f"[worker]  Job {jid} FAILED: {exc}")

    # ---------------------------------------------------------------------- #
    #  Lifecycle
    # ---------------------------------------------------------------------- #

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="worker-loop")
        self._thread.start()
        print(f"[worker] Started. ID: {self.worker_id[:8]}…  (CPU ceiling: {CPU_CEILING_PCT}%)")

    def stop(self):
        print("\n[worker] Shutting down cleanly…")
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        bal = self.coordinator.credit_balance(self.worker_id)
        print(f"[worker] Stopped. Jobs run: {self.jobs_run} | Final balance: {bal} credits")


# --------------------------------------------------------------------------- #
#  Spike harness
# --------------------------------------------------------------------------- #

TEST_JOBS = [
    (DEFAULT_MODEL, "What is the capital of Japan? Answer in one sentence."),
    (DEFAULT_MODEL, "List two benefits of regular exercise. Be concise."),
    (DEFAULT_MODEL, "Write a one-line Python function that returns the factorial of n."),
]


def main():
    print("=" * 65)
    print("Spike 2 — Worker / Ollama Wrapper PoC")
    print("=" * 65)

    coordinator = StubCoordinator()
    worker = WorkerDaemon(coordinator)

    def _shutdown(sig, frame):
        worker.stop()
        _report(coordinator, worker)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    worker.start()

    print(f"\n[spike] Enqueueing {len(TEST_JOBS)} test jobs…")
    for model, prompt in TEST_JOBS:
        coordinator.enqueue(model, prompt)

    # Wait for all jobs to complete (or timeout)
    timeout_s = 3 * 60   # 3 min per job × 3 jobs
    deadline  = time.monotonic() + timeout_s
    while len(coordinator.completed) < len(TEST_JOBS):
        if time.monotonic() > deadline:
            print("[spike] TIMEOUT — not all jobs finished in time")
            break
        time.sleep(1)

    worker.stop()
    return _report(coordinator, worker)


def _report(coordinator: StubCoordinator, worker: WorkerDaemon) -> int:
    done = coordinator.completed
    expected = len(TEST_JOBS)

    print()
    print("=" * 65)
    print("SPIKE 2 RESULTS")
    print("=" * 65)
    for rec in done:
        print(f"  job={rec['job_id']} | {rec['elapsed_s']}s | "
              f"{rec['tokens']} tokens | +{rec['credits_earned']} credits")
        print(f"    → {rec['result_preview']}")

    bal = coordinator.credit_balance(worker.worker_id)
    print(f"\n  Jobs completed : {len(done)}/{expected}")
    print(f"  Total credits  : {bal}")
    spike_pass = len(done) == expected
    print(f"\n  SPIKE 2: {'✅ PASS' if spike_pass else '❌ FAIL — check logs above'}")
    if spike_pass:
        print()
        print("  ✅  Worker profiled hardware, registered, polled for jobs,")
        print("      ran each through Ollama, submitted results, earned credits,")
        print("      and shut down cleanly.  Background-friendliness: CPU gate")
        print(f"     active (ceiling = {CPU_CEILING_PCT}%); daemon is non-interactive.")
    return 0 if spike_pass else 1


if __name__ == "__main__":
    sys.exit(main())
