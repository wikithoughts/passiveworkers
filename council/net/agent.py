#!/usr/bin/env python3
"""
council/net/agent.py — the networked worker daemon
==================================================
Runs on each contributor machine (this Mac, the VPS, …). It:
  • registers with the coordinator (declaring its model, lens, country, owner, judge ability),
  • heartbeats so the coordinator knows it's online and how loaded it is,
  • polls for tasks, runs them on its LOCAL Ollama, and submits OWNED results,
  • handles two task types: `answer` (a perspective) and `judge` (score + merge).

It only ever DIALS OUT to the coordinator — no inbound connections to this machine.

Config via env (or flags):
  PW_COORDINATOR   e.g. http://VPS_IP:8088      (required)
  PW_TOKEN         shared secret                 (required)
  PW_OWNER         account that earns credit     (default: hostname)
  PW_NAME, PW_COUNTRY, PW_ANSWER_MODEL, PW_LENS, PW_CAN_JUDGE, PW_JUDGE_MODEL

Run:  python -m council.net.agent
"""

from __future__ import annotations

import os
import platform
import signal
import socket
import sys
import threading
import time

import requests

from council.judge import Judge
from council.net.config import task_behavior
from council.sanitize import sanitize_brief
from council.worker import Answer, PerspectiveWorker

try:
    import psutil
except Exception:  # psutil optional
    psutil = None


def _env(k: str, default: str = "") -> str:
    return os.environ.get(k, default)


class Agent:
    def __init__(self):
        self.base = _env("PW_COORDINATOR").rstrip("/")
        self.token = _env("PW_TOKEN", "dev-token")
        if not self.base:
            raise SystemExit("set PW_COORDINATOR (e.g. http://127.0.0.1:8088)")
        host = socket.gethostname()
        self.machine_id = _env("PW_MACHINE_ID", host)   # processes on one computer share this
        self.owner = _env("PW_OWNER", host)
        self.name = _env("PW_NAME", host)
        self.country = _env("PW_COUNTRY", "local")
        self.answer_model = _env("PW_ANSWER_MODEL", "gemma3:4b")
        self.lens = _env("PW_LENS", "neutral")
        self.can_judge = _env("PW_CAN_JUDGE", "0") in ("1", "true", "True")
        self.judge_model = _env("PW_JUDGE_MODEL", self.answer_model if self.can_judge else "")
        self.poll_s = float(_env("PW_POLL", "2"))
        self.node_id: str | None = None
        self.node_secret: str | None = None
        self._running = True

    # ------------------------------------------------------------------ http
    def _headers(self) -> dict:
        return {"X-PW-Token": self.token}

    def _node_headers(self) -> dict:
        h = {"X-PW-Token": self.token}
        if self.node_secret:
            h["X-Node-Secret"] = self.node_secret
        return h

    def _profile(self) -> dict:
        prof = {"os": platform.system(), "machine": platform.machine()}
        if psutil:
            prof["ram_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
            prof["cores"] = psutil.cpu_count(logical=False) or psutil.cpu_count()
        try:  # capability matching (D15 v1): which models this node can actually run
            r = requests.get("http://localhost:11434/api/tags", timeout=5)
            prof["models"] = sorted(m["name"] for m in r.json().get("models", []))[:40]
        except Exception:
            prof["models"] = []
        return prof

    def register(self) -> None:
        body = {
            "name": self.name, "country": self.country, "owner": self.owner,
            "answer_model": self.answer_model, "lens": self.lens,
            "can_judge": self.can_judge, "judge_model": self.judge_model,
            "machine_id": self.machine_id, "profile": self._profile(),
        }
        r = requests.post(f"{self.base}/nodes/register", json=body, headers=self._headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        self.node_id = data["node_id"]
        self.node_secret = data.get("node_secret")
        print(f"[agent] registered {self.name} ({self.answer_model}/{self.lens}/{self.country}) "
              f"judge={self.can_judge} → node {self.node_id[:8]}…  @ {self.base}")

    def heartbeat(self) -> None:
        load = (psutil.cpu_percent(interval=None) / 100.0) if psutil else 0.0
        try:
            r = requests.post(f"{self.base}/nodes/heartbeat", json={"load": load},
                              headers=self._node_headers(), timeout=10)
            if r.status_code in (401, 404):  # coordinator restarted / forgot us
                self.register()
        except requests.RequestException as exc:
            print(f"[agent] heartbeat failed: {exc}")

    def _report_progress(self, task_id: str, done: int, total: int) -> None:
        """Best-effort mid-flight progress for a claimed task (D32). Never raises — a dropped
        progress ping must not affect the actual work or its result."""
        if not task_id:
            return
        try:
            requests.post(f"{self.base}/tasks/{task_id}/progress",
                          json={"done": int(done), "total": int(total)},
                          headers=self._node_headers(), timeout=10)
        except requests.RequestException:
            pass

    # ------------------------------------------------------------------ task handlers
    def _do_answer(self, task: dict) -> dict:
        payload = task.get("payload") or {}
        # defense-in-depth (D26): the coordinator is not fully trusted (cf. D25) — re-scrub the
        # brief/instruction here so a hostile coordinator can't slip a hidden payload into a prompt.
        question = sanitize_brief(payload.get("question", ""))
        beh = task_behavior(payload.get("job_type"))   # D33: registry-driven executor dispatch
        if beh.executor == "batch":
            # D13/D33: batch shard — apply the instruction to THIS node's slice. A trusted per-type
            # framing (download_extract / code_generation) is prepended AFTER sanitization.
            from council.batch import BatchWorker
            bw = BatchWorker(self.node_id, self.answer_model,
                             country=task.get("country", self.country))
            instruction = f"{beh.framing}\n\n{question}".strip() if beh.framing else question
            # D32: report progress as items complete (throttled) so the coordinator can show a job
            # completion % and the reaper sees a live, advancing claim instead of guessing.
            last = [0.0]

            def _on_progress(done: int, total: int) -> None:
                t = time.monotonic()
                if done >= total or t - last[0] >= 3.0:
                    last[0] = t
                    self._report_progress(task.get("task_id", ""), done, total)

            return bw.process(instruction, payload.get("shard") or [],
                              fetch=bool(payload.get("fetch")), on_progress=_on_progress)
        if beh.executor == "research" \
                and os.environ.get("PW_WEB_BACKEND", "off") != "off":
            # D13: async deep-research job — this node's own multi-round, egress-localized
            # research with citations (council/researcher.py).
            from council.researcher import ResearchWorker
            rw = ResearchWorker(self.node_id, self.answer_model,
                                lens=task.get("lens", self.lens),
                                country=task.get("country", self.country),
                                scope="web")  # federation = web only; no operator's private library
            return rw.research(question)
        web = None
        if os.environ.get("PW_WEB_BACKEND", "off") != "off":
            try:
                from council.research import search as web   # egress-localized web research
            except Exception as exc:
                print(f"[agent] web research unavailable: {exc}")
                web = None
        w = PerspectiveWorker(self.node_id, self.answer_model, lens=task.get("lens", self.lens),
                              country=task.get("country", self.country), web_search=web,
                              num_predict=int(os.environ.get("PW_NUM_PREDICT", "400")))
        a = w.answer(question)
        return {"text": a.text, "tokens": a.tokens, "elapsed_s": round(a.elapsed_s, 2)}

    def _do_judge(self, task: dict) -> dict:
        payload = task["payload"]
        question = sanitize_brief(payload.get("question", ""))   # defense-in-depth (D26), see _do_answer
        answers = [
            Answer(worker_id=x["worker_id"], model=x.get("model", ""), lens=x.get("lens", ""),
                   country=x.get("country", ""), text=x["text"], tokens=0, elapsed_s=0.0)
            for x in payload["answers"]
        ]
        judge = Judge(model=self.judge_model or self.answer_model)
        beh = task_behavior(payload.get("job_type"))   # D33: registry-driven judge dispatch
        if beh.judge == "spot_check":
            # Batch QA: spot-check sampled outputs per node; the store assembles the merged
            # deliverable from the shards itself.
            return judge.spot_check(question, payload["answers"])
        out = judge.deliberate(question, answers)   # scores + merge + council read
        if beh.judge == "compile_report":
            # Editor pass: merged becomes the full cited multi-country report.
            out["merged"] = judge.compile_report(question, payload["answers"], out)
        return out

    # ------------------------------------------------------------------ loop
    def _heartbeat_loop(self) -> None:
        """Heartbeat in the background so a node stays 'alive' even mid-inference."""
        while self._running:
            self.heartbeat()
            time.sleep(self.poll_s)

    def run(self) -> None:
        # Never die because the coordinator/tunnel is briefly down at boot — keep trying.
        while self._running:
            try:
                self.register()
                break
            except requests.RequestException as exc:
                print(f"[agent] register failed ({exc}); retrying in 10s…")
                time.sleep(10)
        threading.Thread(target=self._heartbeat_loop, daemon=True, name="pw-hb").start()
        while self._running:
            try:
                r = requests.get(f"{self.base}/tasks/next",
                                 headers=self._node_headers(), timeout=20)
            except requests.RequestException as exc:
                print(f"[agent] poll failed: {exc}")
                time.sleep(self.poll_s)
                continue
            if r.status_code == 401:          # secret invalid; the heartbeat loop re-registers
                time.sleep(self.poll_s)
                continue
            if r.status_code == 204 or not r.content:
                time.sleep(self.poll_s)
                continue
            task = r.json()
            kind = task["type"]
            print(f"[agent] {kind} task {task['task_id'][:8]}… …")
            t0 = time.monotonic()
            try:
                result = self._do_answer(task) if kind == "answer" else self._do_judge(task)
            except Exception as exc:
                print(f"[agent] task {task['task_id'][:8]} FAILED: {exc}")
                result = {"text": "", "error": str(exc), "scores": {}, "merged": ""}
            try:
                requests.post(f"{self.base}/tasks/{task['task_id']}/result", json=result,
                              headers=self._node_headers(), timeout=30)
            except requests.RequestException as exc:
                print(f"[agent] result POST failed (task {task['task_id'][:8]}): {exc}")
            print(f"[agent] {kind} done in {time.monotonic() - t0:.0f}s")

    def stop(self, *_):
        print("\n[agent] shutting down…")
        self._running = False


def main() -> int:
    agent = Agent()
    signal.signal(signal.SIGINT, agent.stop)
    signal.signal(signal.SIGTERM, agent.stop)
    agent.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
