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

import json
import os
import pathlib
import platform
import signal
import socket
import sys
import threading
import time
from typing import Callable

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


def _fix_hint(exc: Exception) -> str:
    """Map a task-execution exception to an actionable hint instead of the raw string (R10
    review) — the worker daemon's own console is often the only place an operator ever sees
    a failure."""
    msg, name = str(exc), type(exc).__name__
    low = msg.lower()
    if isinstance(exc, requests.exceptions.ConnectionError) or "connection refused" in low:
        return "can't reach Ollama — is it running? `ollama serve`"
    if "404" in msg and ("/api/generate" in msg or "/api/chat" in msg):
        return "the declared model isn't pulled — `ollama pull <model>`"
    if isinstance(exc, requests.exceptions.Timeout) or "timed out" in low:
        return "Ollama timed out — the model may be too slow/loaded for this task's deadline"
    return f"{name}: {msg}"


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
        self.enroll_token = _env("PW_ENROLL_TOKEN", "")   # D42: per-operator token for `pw join`
        self.node_id: str | None = None
        self.node_secret: str | None = None
        # D42: a callback (node_id, secret) -> None, set by `pw join` so a freshly minted identity is
        # persisted to ~/.passiveworkers/join.json the moment register() succeeds.
        self._on_identity: Callable[[str, str | None], None] | None = None
        self._running = True
        self._tasks_ok = 0
        self._tasks_failed = 0

    # ------------------------------------------------------------------ http
    def _headers(self) -> dict:
        return {"X-PW-Token": self.token}

    def _node_headers(self) -> dict:
        h = {"X-PW-Token": self.token}
        if self.node_secret:
            h["X-Node-Secret"] = self.node_secret
        return h

    def _profile(self) -> dict:
        prof: dict[str, object] = {"os": platform.system(), "machine": platform.machine()}
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
        headers = self._headers()
        if self.enroll_token:   # D42/D37: redeem a per-operator enrollment token at register
            headers = {**headers, "X-Enroll-Token": self.enroll_token}
        r = requests.post(f"{self.base}/nodes/register", json=body, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        self.node_id = data["node_id"]
        assert self.node_id is not None
        self.node_secret = data.get("node_secret")
        if self._on_identity:   # D42: persist the minted identity (best-effort; never break register)
            try:
                self._on_identity(self.node_id, self.node_secret)
            except Exception as exc:
                print(f"[agent] could not persist identity: {exc}")
        print(f"[agent] registered {self.name} ({self.answer_model}/{self.lens}/{self.country}) "
              f"judge={self.can_judge} → node {self.node_id[:8]}…  @ {self.base}")

    def heartbeat(self) -> None:
        load = (psutil.cpu_percent(interval=None) / 100.0) if psutil else 0.0
        try:
            r = requests.post(f"{self.base}/nodes/heartbeat",
                              json={"load": load, "tasks_ok": self._tasks_ok,
                                    "tasks_failed": self._tasks_failed},
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
        # In the normal run() loop, node_id is always set here (register() has already succeeded
        # at startup) — but pyright can't carry that narrowing across methods, and some tests call
        # _do_answer directly on a fresh, unregistered Agent (node_id is None). An assert would
        # crash those; `or ""` narrows for pyright without changing runtime behavior either way.
        worker_id = self.node_id or ""
        payload = task.get("payload") or {}
        # defense-in-depth (D26): the coordinator is not fully trusted (cf. D25) — re-scrub the
        # brief/instruction here so a hostile coordinator can't slip a hidden payload into a prompt.
        question = sanitize_brief(payload.get("question", ""))
        beh = task_behavior(payload.get("job_type"))   # D33: registry-driven executor dispatch
        if beh.executor == "batch":
            # D13/D33: batch shard — apply the instruction to THIS node's slice. A trusted per-type
            # framing (download_extract / code_generation) is prepended AFTER sanitization.
            from council.batch import BatchWorker
            bw = BatchWorker(worker_id, self.answer_model,
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
            rw = ResearchWorker(worker_id, self.answer_model,
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
        w = PerspectiveWorker(worker_id, self.answer_model, lens=task.get("lens", self.lens),
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
        # D42 resume: a `pw join` that already has a cached identity skips the initial register
        # (which, under enrollment mode, would need a fresh single-use token) and reuses its node
        # secret directly. If the coordinator has forgotten it, the heartbeat loop re-registers.
        if not (self.node_id and self.node_secret):
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
                print(f"[agent] task {task['task_id'][:8]} FAILED: {_fix_hint(exc)}")
                result = {"text": "", "error": str(exc), "scores": {}, "merged": ""}
            if result.get("error"):
                self._tasks_failed += 1
            else:
                self._tasks_ok += 1
            try:
                requests.post(f"{self.base}/tasks/{task['task_id']}/result", json=result,
                              headers=self._node_headers(), timeout=30)
            except requests.RequestException as exc:
                print(f"[agent] result POST failed (task {task['task_id'][:8]}): {exc}")
            outcome = "FAILED" if result.get("error") else "done"
            print(f"[agent] {kind} {outcome} in {time.monotonic() - t0:.0f}s")

    def stop(self, *_):
        print("\n[agent] shutting down…")
        self._running = False


# ---------------------------------------------------------------- D42: one-command `pw join`
def _join_state_path() -> pathlib.Path:
    base = os.environ.get("PW_LIBRARY_DIR", str(pathlib.Path.home() / ".passiveworkers"))
    return pathlib.Path(base) / "join.json"


def _load_join() -> dict:
    p = _join_state_path()
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_join(state: dict) -> None:
    """Persist join.json owner-only FROM CREATION — the node secret is a bearer credential
    (mirrors council.crypto.load_or_create; no world-readable window before chmod)."""
    p = _join_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(state, indent=2).encode()
    try:
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        os.chmod(p, 0o600)
    except Exception:
        # Fallback (platforms without os.open mode support): still avoid a world-readable window
        # by forcing a restrictive umask around the create — write_text() alone would land 0o644
        # before chmod, briefly exposing the node secret (review).
        old = os.umask(0o077)
        try:
            p.write_text(json.dumps(state, indent=2))
        finally:
            os.umask(old)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass


def _installed_models(base_url: str) -> set[str] | None:
    """Models actually pulled in this node's local Ollama. None = couldn't check (Ollama
    unreachable) — distinct from an empty/mismatched set (checked, and it's missing). R10/F34
    review: the coordinator only verifies the DECLARED model name, never that it's installed."""
    try:
        r = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        return {m["name"] for m in r.json().get("models", [])}
    except Exception:
        return None


def _parse_join_args(rest: list) -> tuple[list, dict]:
    """Split `pw join` args into positionals (url, token) and flags
    (--owner/--country/--model/--lens/--judge|--no-judge/--judge-model/--web).
    Judging is ON by default so a lone operator can serve a job end-to-end; --no-judge opts out."""
    pos: list = []
    flags: dict = {}
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--judge":
            flags["can_judge"] = True
            i += 1
        elif a == "--no-judge":
            flags["can_judge"] = False
            i += 1
        elif a.startswith("--") and i + 1 < len(rest):
            flags[a[2:]] = rest[i + 1]
            i += 2
        else:
            pos.append(a)
            i += 1
    return pos, flags


def join(argv: list) -> int:
    """`pw join <coordinator-url> <enrollment-token>` (first run) or `pw join` / `pw work` (resume):
    one command to contribute a machine. Persists identity+config to ~/.passiveworkers/join.json,
    seeds the env the agent reads, and starts the worker loop. Backward-compatible with the env flow."""
    pos, flags = _parse_join_args(argv[1:] if argv else [])
    url = pos[0].rstrip("/") if pos else ""
    token = pos[1] if len(pos) > 1 else ""

    state = _load_join()
    if not url:                                   # resume: fall back to the last coordinator joined
        url = (state.get("default") or "").rstrip("/")
        if not url:
            raise SystemExit("usage: pw join <coordinator-url> <enrollment-token>   (first time)\n"
                             "       pw join | pw work                              (resume)")

    cfg = dict(state.get(url, {}))                # existing cached config for this coordinator, if any
    cfg["owner"] = flags.get("owner", cfg.get("owner") or socket.gethostname())
    cfg["name"] = flags.get("name", cfg.get("name") or socket.gethostname())
    cfg["country"] = flags.get("country", cfg.get("country", "local"))
    cfg["answer_model"] = flags.get("model", cfg.get("answer_model", "gemma3:4b"))
    cfg["lens"] = flags.get("lens", cfg.get("lens", "neutral"))
    # Default judge ON: a lone contributor must be able to BOTH answer and judge, or a small
    # deployment fails every job with "no judge node online" (found dogfooding pw join on the VPS).
    # `--no-judge` opts out. The judge reuses the answer model unless --judge-model is given.
    cfg["can_judge"] = bool(flags.get("can_judge", cfg.get("can_judge", True)))
    cfg["judge_model"] = (flags.get("judge-model", cfg.get("judge_model", ""))
                          or (cfg["answer_model"] if cfg["can_judge"] else ""))
    cfg["web_backend"] = flags.get("web", cfg.get("web_backend", "ddgs"))   # joined nodes research by default

    # Model preflight (R10/F34 review): a node used to join cleanly and then fail every task at
    # inference because nothing checked the declared model was actually pulled. Run BEFORE any
    # side effects (env-seeding, _save_join, Agent construction) so a bad join never persists.
    base_ollama = os.environ.get("PW_OLLAMA_BASE", "http://localhost:11434")
    installed = _installed_models(base_ollama)
    wanted = sorted({m for m in (cfg["answer_model"], cfg.get("judge_model") or "") if m})
    if installed is not None:
        missing = [m for m in wanted if m not in installed]
        if missing:
            pulls = "\n".join(f"  ollama pull {m}" for m in missing)
            raise SystemExit(
                f"✗ model(s) not pulled: {', '.join(missing)}\nPull them, then re-run `pw join`:\n"
                f"{pulls}\n(otherwise this machine joins cleanly, then fails every task at "
                "inference)")
    elif wanted:
        print(f"  ⚠ could not reach Ollama at {base_ollama} to verify {', '.join(wanted)} is "
              "pulled — continuing, but tasks will fail if it isn't. `ollama serve` first if "
              "unsure.")

    # The chosen seam: the Agent reads PW_* from os.environ (incl. PW_WEB_BACKEND in hot paths) —
    # seed them from the resolved config BEFORE constructing it, so web research isn't silently off.
    os.environ["PW_COORDINATOR"] = url
    os.environ["PW_OWNER"] = cfg["owner"]
    os.environ["PW_NAME"] = cfg["name"]
    os.environ["PW_COUNTRY"] = cfg["country"]
    os.environ["PW_ANSWER_MODEL"] = cfg["answer_model"]
    os.environ["PW_LENS"] = cfg["lens"]
    os.environ["PW_CAN_JUDGE"] = "1" if cfg["can_judge"] else "0"
    os.environ["PW_JUDGE_MODEL"] = cfg["judge_model"]
    os.environ["PW_WEB_BACKEND"] = cfg["web_backend"]
    if token:
        os.environ["PW_ENROLL_TOKEN"] = token

    # Persist the config (sans identity) up front so `default` + cfg survive even a pre-register crash.
    state[url] = {**state.get(url, {}), **cfg}
    state["default"] = url
    _save_join(state)

    agent = Agent()
    if not token and cfg.get("node_id") and cfg.get("node_secret"):
        # resume: reuse the cached identity (register under enroll mode needs a fresh single-use token)
        agent.node_id, agent.node_secret = cfg["node_id"], cfg["node_secret"]

    def _persist(node_id: str, secret: str | None) -> None:
        s = _load_join()
        entry = {**s.get(url, {}), **cfg, "node_id": node_id, "node_secret": secret}
        entry.pop("enroll_token", None)           # never persist the (single-use) token
        s[url] = entry
        s["default"] = url
        _save_join(s)
    agent._on_identity = _persist

    print(f"[join] {url} as {cfg['owner']} "
          f"({cfg['answer_model']}/{cfg['country']}, web={cfg['web_backend']}) — Ctrl-C to stop")
    signal.signal(signal.SIGINT, agent.stop)
    signal.signal(signal.SIGTERM, agent.stop)
    agent.run()
    return 0


def main() -> int:
    agent = Agent()
    signal.signal(signal.SIGINT, agent.stop)
    signal.signal(signal.SIGTERM, agent.stop)
    agent.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
