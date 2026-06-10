#!/usr/bin/env python3
"""
scripts/cross_hardware_verify.py — close the Spike-1 gap (for real this time)
============================================================================
Spike 1 only proved cheat-rejection on ONE machine. The unverified question was:
do HONEST answers from genuinely different hardware (Mac/Metal vs VPS/CPU) agree by
MEANING more strongly than a MODEL-DOWNGRADE cheat — enough for one fixed threshold?

This runs the SAME model (gemma3:12b, present on both) on the same prompts on the Mac
(Metal) and the VPS (CPU, via SSH to its local Ollama), and compares each honest pair
against a downgrade (gemma3:12b vs the smaller gemma3:4b on the Mac).

Reports the honest cross-hardware FLOOR vs the downgrade CEILING. If floor > ceiling
with margin, a single fuzzy threshold works; if they converge, thresholds must be
prompt-class-dependent (which is itself the finding).

Run:  python scripts/cross_hardware_verify.py
Light by design: short outputs, 2 prompts (gentle on the VPS's other workloads).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import numpy as np
import requests

OLLAMA = "http://localhost:11434"
VPS_HOST = "wikiclaw-1"
# Agent-independent SSH (the 1Password SSH agent can re-lock); use the key file directly.
SSH_KEY = os.environ.get("PW_SSH_KEY", os.path.expanduser("~/.ssh/hetzner_ssh"))
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "IdentityAgent=none",
            "-o", "IdentitiesOnly=yes", "-i", SSH_KEY]
COMMON_MODEL = "gemma3:12b"     # on both Mac and VPS
DOWNGRADE_MODEL = "gemma3:4b"   # Mac-only; stands in for a lazy/quantized worker
EMBED = "nomic-embed-text"
NUM_PREDICT = 160
EMBED_W = 0.70

PROMPTS = [
    ("factual", "In one short paragraph, explain what causes ocean tides."),
    ("reasoning", "A bat and ball cost $1.10 total. The bat costs $1.00 more than the ball. "
                  "How much is the ball? Give the answer and one line of reasoning."),
]


def gen_local(model: str, prompt: str) -> str:
    r = requests.post(f"{OLLAMA}/api/generate", json={
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.0, "num_predict": NUM_PREDICT}}, timeout=240)
    r.raise_for_status()
    return r.json()["response"].strip()


def gen_vps(model: str, prompt: str) -> str:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False,
                          "options": {"temperature": 0.0, "num_predict": NUM_PREDICT}})
    out = subprocess.run(
        ["ssh", *SSH_OPTS, VPS_HOST,
         "curl -s http://127.0.0.1:11434/api/generate -d @-"],
        input=payload, capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(f"ssh/curl failed: {out.stderr[:200]}")
    return json.loads(out.stdout)["response"].strip()


def embed(text: str) -> np.ndarray:
    r = requests.post(f"{OLLAMA}/api/embeddings", json={"model": EMBED, "prompt": text}, timeout=60)
    r.raise_for_status()
    v = np.array(r.json()["embedding"], dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n else v


def tok_overlap(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"\b\w+\b", a.lower())), set(re.findall(r"\b\w+\b", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def score(a: str, b: str) -> float:
    cos = float(np.dot(embed(a), embed(b)))
    return EMBED_W * cos + (1 - EMBED_W) * tok_overlap(a, b)


def main() -> int:
    print("=" * 74)
    print("CROSS-HARDWARE VERIFICATION  —  Mac (Metal) vs VPS (CPU)")
    print(f"  honest pair : {COMMON_MODEL} on Mac  vs  {COMMON_MODEL} on VPS")
    print(f"  downgrade   : {COMMON_MODEL} on Mac  vs  {DOWNGRADE_MODEL} on Mac")
    print("=" * 74)
    honest, downgrade = [], []
    for kind, prompt in PROMPTS:
        print(f"\n[{kind}] {prompt}")
        mac_ref = gen_local(COMMON_MODEL, prompt)
        vps_ref = gen_vps(COMMON_MODEL, prompt)
        down = gen_local(DOWNGRADE_MODEL, prompt)
        h = score(mac_ref, vps_ref)
        d = score(mac_ref, down)
        honest.append(h)
        downgrade.append(d)
        print(f"   honest cross-hardware (Mac12b vs VPS12b) : {h:.4f}")
        print(f"   model-downgrade       (Mac12b vs Mac4b)  : {d:.4f}")

    floor = min(honest)
    ceiling = max(downgrade)
    print("\n" + "=" * 74)
    print(f"  honest cross-hardware FLOOR  : {floor:.4f}  (lowest honest agreement)")
    print(f"  model-downgrade CEILING      : {ceiling:.4f}  (highest cheat agreement)")
    margin = floor - ceiling
    if margin > 0:
        thr = round((floor + ceiling) / 2, 3)
        print(f"  ✅ SEPARABLE — gap {margin:.4f}; one fixed threshold ≈ {thr} works "
              f"(accept honest cross-hardware, reject downgrade).")
    else:
        print(f"  ⚠️ CONVERGE — floor ≤ ceiling (gap {margin:.4f}); a single threshold can't "
              f"separate honest cross-hardware from downgrade → thresholds must be prompt-class-aware.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
