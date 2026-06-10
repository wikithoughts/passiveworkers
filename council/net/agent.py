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

    # ------------------------------------------------------------------ task handlers
    def _do_answer(self, task: dict) -> dict:
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
        a = w.answer(task["payload"]["question"])
        return {"text": a.text, "tokens": a.tokens, "elapsed_s": round(a.elapsed_s, 2)}

    def _do_judge(self, task: dict) -> dict:
        payload = task["payload"]
        answers = [
            Answer(worker_id=x["worker_id"], model=x.get("model", ""), lens=x.get("lens", ""),
                   country=x.get("country", ""), text=x["text"], tokens=0, elapsed_s=0.0)
            for x in payload["answers"]
        ]
        judge = Judge(model=self.judge_model or self.answer_model)
        return judge.deliberate(payload["question"], answers)   # scores + merge + council read

    # ------------------------------------------------------------------ loop
    def _heartbeat_loop(self) -> None:
        """Heartbeat in the background so a node stays 'alive' even mid-inference."""
        while self._running:
            self.heartbeat()
            time.sleep(self.poll_s)

    def run(self) -> None:
        self.register()
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
