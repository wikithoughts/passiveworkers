#!/usr/bin/env python3
"""
council/operator.py — the operator side of assisted tasks (D21)
================================================================
"Computers doing work for other computers", with a human in the loop. An operator who
has joined a coordinator can see OPEN assisted offers, give informed consent to one,
do the work themselves (with their own agentic AI — Claude, Codex — or by hand), and
deliver the owned result. Our software NEVER automates the operator's computer; the
human is always the agent and always consents to a bounded brief.

    pw tasks                       list open assisted offers you're eligible for
    pw accept <task_id>            consent to + claim an offer (prints the full brief)
    pw deliver <task_id> <text>    deliver your result (text, or @path to a file)

Requires (operator env): PW_COORDINATOR, PW_TOKEN, PW_OWNER (your handle). Optional:
PW_NAME, PW_COUNTRY. Identity (node_id + secret) is cached in ~/.passiveworkers/operator.json
per coordinator so accept→deliver use the same node.
"""

from __future__ import annotations

import json
import os
import pathlib
import platform
import socket
import sys

import requests

STATE = pathlib.Path(os.environ.get("PW_LIBRARY_DIR",
                                    str(pathlib.Path.home() / ".passiveworkers"))) / "operator.json"


def _profile() -> dict:
    prof = {"os": platform.system(), "machine": platform.machine()}
    try:
        import psutil
        prof["ram_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
        prof["cores"] = psutil.cpu_count(logical=False) or psutil.cpu_count()
    except Exception:
        pass
    try:
        base = os.environ.get("PW_OLLAMA_BASE", "http://localhost:11434")
        r = requests.get(f"{base}/api/tags", timeout=5)
        prof["models"] = sorted(m["name"] for m in r.json().get("models", []))[:40]
    except Exception:
        prof["models"] = []
    return prof


class Operator:
    def __init__(self):
        self.base = os.environ.get("PW_COORDINATOR", "").rstrip("/")
        self.token = os.environ.get("PW_TOKEN", "dev-token")
        if not self.base:
            raise SystemExit("set PW_COORDINATOR (e.g. http://127.0.0.1:8791)")
        self.owner = os.environ.get("PW_OWNER", socket.gethostname())
        self.node_id, self.secret = self._identity()

    def _headers(self) -> dict:
        h = {"X-PW-Token": self.token, "Content-Type": "application/json"}
        if self.secret:
            h["X-Node-Secret"] = self.secret
        return h

    def _identity(self) -> tuple[str, str]:
        """Reuse a cached node identity for this coordinator, else register one."""
        if STATE.exists():
            try:
                cache = json.loads(STATE.read_text()).get(self.base)
                if cache:
                    return cache["node_id"], cache["secret"]
            except Exception:
                pass
        body = {"name": os.environ.get("PW_NAME", socket.gethostname()),
                "country": os.environ.get("PW_COUNTRY", "local"), "owner": self.owner,
                "answer_model": "", "lens": "operator", "can_judge": False,
                "judge_model": "", "machine_id": os.environ.get("PW_MACHINE_ID", socket.gethostname()),
                "profile": _profile()}
        r = requests.post(f"{self.base}/nodes/register", json=body,
                          headers={"X-PW-Token": self.token}, timeout=15)
        r.raise_for_status()
        d = r.json()
        STATE.parent.mkdir(parents=True, exist_ok=True)
        all_state = {}
        if STATE.exists():
            try:
                all_state = json.loads(STATE.read_text())
            except Exception:
                all_state = {}
        all_state[self.base] = {"node_id": d["node_id"], "secret": d["node_secret"]}
        STATE.write_text(json.dumps(all_state))
        return d["node_id"], d["node_secret"]

    def tasks(self) -> int:
        offers = requests.get(f"{self.base}/tasks/offers", headers=self._headers(),
                              timeout=15).json().get("offers", [])
        if not offers:
            print("No open assisted offers you're eligible for right now.")
            return 0
        for o in offers:
            print(f"\n● {o['task_id']}   (reward {o['price']} cr · open {o['age_s']:.0f}s)")
            print(f"  brief: {o['brief']}")
            if o.get("requires"):
                print(f"  needs: {o['requires']}")
        print("\nAccept one with:  pw accept <task_id>")
        return 0

    def accept(self, task_id: str) -> int:
        r = requests.post(f"{self.base}/tasks/{task_id}/accept", headers=self._headers(), timeout=15)
        if not r.ok:
            print(f"✗ {r.json().get('detail', r.text)}"); return 1
        d = r.json()
        print(f"✓ accepted {task_id}\n\nBRIEF:\n{d['brief']}\n")
        if d.get("context"):
            print(f"CONTEXT:\n{d['context']}\n")
        print("Do the work (your own AI or by hand), then:\n"
              f"  pw deliver {task_id} \"<your result>\"   (or @path/to/file)")
        return 0

    def deliver(self, task_id: str, deliverable: str) -> int:
        if deliverable.startswith("@"):
            deliverable = pathlib.Path(deliverable[1:]).expanduser().read_text()
        r = requests.post(f"{self.base}/tasks/{task_id}/deliver",
                          headers=self._headers(),
                          data=json.dumps({"deliverable": deliverable[:200_000]}), timeout=30)
        if not r.ok:
            print(f"✗ {r.json().get('detail', r.text)}"); return 1
        print(f"✓ delivered — you've been paid. job {r.json().get('job_id', '')[:8]}")
        return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("usage: pw tasks | pw accept <id> | pw deliver <id> <text|@file>")
        return 2
    op = Operator()
    cmd, rest = args[0], args[1:]
    if cmd == "tasks":
        return op.tasks()
    if cmd == "accept" and rest:
        return op.accept(rest[0])
    if cmd == "deliver" and len(rest) >= 2:
        return op.deliver(rest[0], " ".join(rest[1:]))
    print("usage: pw tasks | pw accept <id> | pw deliver <id> <text|@file>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
