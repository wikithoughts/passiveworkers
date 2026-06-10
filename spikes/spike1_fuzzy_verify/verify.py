#!/usr/bin/env python3
"""
Spike 1 — Fuzzy Verification PoC for Passive Workers
=====================================================
Validates that a lightweight fuzzy comparator can distinguish:
  • Honest cross-machine outputs (same meaning, slightly different tokens)
  • Planted cheat A — canned/off-topic reply  (passes sanity, fails fuzzy)
  • Planted cheat B — garbage/corrupted bytes (fails sanity gate)
  • Planted cheat C — wrong-prompt output     (passes sanity, fails fuzzy)

The hypothesis: LLM outputs are NOT byte-identical across different hardware/
builds at temp=0 (FP-rounding diffs flip near-tie argmax tokens), but they ARE
semantically equivalent. We simulate this with different seeds on one machine.

Success bar: no honest-worker false-rejects AND 100% cheat catch.

Requires: ollama running locally with gemma3:4b and nomic-embed-text pulled.
Run:  python3 verify.py
"""

import re
import sys
import requests
import numpy as np

OLLAMA_BASE = "http://localhost:11434"
INFERENCE_MODEL = "gemma3:4b"
EMBED_MODEL = "nomic-embed-text"

# Combined score = EMBED_WEIGHT * cosine_sim + (1 - EMBED_WEIGHT) * token_overlap
EMBED_WEIGHT = 0.70
THRESHOLD = 0.82  # tuned in the "threshold sweep" section below

TEST_PROMPT = "Explain in 2-3 sentences why the sky appears blue during the day."
WRONG_PROMPT = "Write a haiku about autumn leaves falling in a forest."


# --------------------------------------------------------------------------- #
#  Ollama helpers
# --------------------------------------------------------------------------- #

def run_inference(prompt: str, seed: int = 42, temperature: float = 0.0) -> str:
    resp = requests.post(f"{OLLAMA_BASE}/api/generate", json={
        "model": INFERENCE_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"seed": seed, "temperature": temperature, "num_predict": 200},
    }, timeout=120)
    resp.raise_for_status()
    return resp.json()["response"].strip()


def embed(text: str) -> np.ndarray:
    resp = requests.post(f"{OLLAMA_BASE}/api/embeddings", json={
        "model": EMBED_MODEL,
        "prompt": text,
    }, timeout=60)
    resp.raise_for_status()
    vec = np.array(resp.json()["embedding"], dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))  # both are unit vectors


def token_overlap(a: str, b: str) -> float:
    """Jaccard similarity on word tokens (case-insensitive)."""
    def tok(s):
        return set(re.findall(r"\b\w+\b", s.lower()))
    ta, tb = tok(a), tok(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def combined_score(ref: str, cand: str) -> tuple[float, float, float]:
    """Returns (cosine, token_overlap, combined)."""
    cos = cosine_sim(embed(ref), embed(cand))
    tok = token_overlap(ref, cand)
    return cos, tok, EMBED_WEIGHT * cos + (1 - EMBED_WEIGHT) * tok


# --------------------------------------------------------------------------- #
#  Sanity gates
# --------------------------------------------------------------------------- #

MIN_LEN = 30        # chars
MAX_LEN = 2000      # chars
PRINTABLE_RATIO = 0.90


def sanity_check(text: str) -> tuple[bool, str]:
    if len(text) < MIN_LEN:
        return False, f"too short ({len(text)})"
    if len(text) > MAX_LEN:
        return False, f"too long ({len(text)})"
    n_printable = sum(1 for c in text if c.isprintable() or c in "\n\t")
    ratio = n_printable / len(text)
    if ratio < PRINTABLE_RATIO:
        return False, f"garbage ({ratio:.0%} printable)"
    return True, "ok"


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #

def main():
    print("=" * 70)
    print("Spike 1 — Fuzzy Verification PoC")
    print(f"  Inference model : {INFERENCE_MODEL}")
    print(f"  Embedding model : {EMBED_MODEL}")
    print(f"  Embed weight    : {EMBED_WEIGHT}")
    print(f"  Threshold       : {THRESHOLD}")
    print(f"  Prompt          : {TEST_PROMPT!r}")
    print("=" * 70)

    # ------------------------------------------------------------------ #
    #  1. Reference output (coordinator's reference node)
    # ------------------------------------------------------------------ #
    print("\n[1/4] Generating REFERENCE output (seed=42, temp=0)…")
    reference = run_inference(TEST_PROMPT, seed=42, temperature=0.0)
    print(f"  → {reference[:100]}…")

    # ------------------------------------------------------------------ #
    #  2. Honest worker outputs (cross-machine divergence simulation)
    # ------------------------------------------------------------------ #
    # On the SAME machine+seed+temp=0, outputs are byte-identical.
    # We simulate cross-machine byte divergence three ways:
    #   (a) identical run (best case: same hardware)
    #   (b) different seed (simulates different RNG init across machines)
    #   (c) tiny temperature (simulates FP rounding drift at near-tie tokens)
    print("\n[2/4] Generating honest WORKER outputs (simulating cross-machine byte divergence)…")
    honest = {
        "same_machine_seed42_t0":    run_inference(TEST_PROMPT, seed=42,  temperature=0.00),
        "different_seed99_t0":       run_inference(TEST_PROMPT, seed=99,  temperature=0.00),
        "same_seed_tiny_temp_0.05":  run_inference(TEST_PROMPT, seed=42,  temperature=0.05),
    }

    # ------------------------------------------------------------------ #
    #  3. Planted cheats
    # ------------------------------------------------------------------ #
    print("\n[3/4] Preparing planted CHEATS…")
    wrong_answer = run_inference(WRONG_PROMPT, seed=42, temperature=0.0)

    cheats = {
        # A: canned reply unrelated to the prompt
        "cheat_A_canned":
            "I'm sorry, I'm not able to answer that. Please contact support.",
        # B: binary garbage that should fail the printable-ratio sanity gate
        "cheat_B_garbage":
            ("\x00\x01\x02\x03" * 25) + "text",     # ~3% printable → gate fails
        # C: correct-length fluent answer to a DIFFERENT prompt (hardest cheat to catch)
        "cheat_C_wrong_prompt": wrong_answer,
    }

    # ------------------------------------------------------------------ #
    #  4. Score everything
    # ------------------------------------------------------------------ #
    print("\n[4/4] Scoring…\n")

    HDR = f"  {'Label':<32} {'Sanity':>7}  {'Cos':>7} {'Tok':>7} {'Score':>7}  {'Verdict'}"
    print(HDR)
    print("  " + "-" * 74)

    results: dict[str, dict] = {}

    def evaluate(label: str, output: str, kind: str):
        ok, msg = sanity_check(output)
        if ok:
            cos, tok, score = combined_score(reference, output)
        else:
            cos, tok, score = 0.0, 0.0, 0.0
        accept = ok and score >= THRESHOLD
        verdict = "✅ ACCEPT" if accept else "❌ REJECT"
        byte_note = ""
        if kind == "honest":
            byte_note = " [IDENTICAL]" if output == reference else " [DIFFERS]"
        print(f"  {label:<32} {msg:>7}  {cos:>7.4f} {tok:>7.4f} {score:>7.4f}  {verdict}{byte_note}")
        results[label] = {"kind": kind, "accept": accept, "score": score, "sane": ok}

    for label, output in honest.items():
        evaluate(label, output, "honest")

    for label, output in cheats.items():
        evaluate(label, output, "cheat")

    # ------------------------------------------------------------------ #
    #  Summary
    # ------------------------------------------------------------------ #
    honest_accepted = sum(1 for v in results.values() if v["kind"] == "honest" and v["accept"])
    honest_total    = sum(1 for v in results.values() if v["kind"] == "honest")
    cheats_rejected = sum(1 for v in results.values() if v["kind"] == "cheat" and not v["accept"])
    cheats_total    = sum(1 for v in results.values() if v["kind"] == "cheat")

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Honest workers accepted : {honest_accepted}/{honest_total}")
    print(f"  Cheats rejected         : {cheats_rejected}/{cheats_total}")

    spike_pass = (honest_accepted == honest_total) and (cheats_rejected == cheats_total)
    print(f"\n  SPIKE 1: {'✅ PASS' if spike_pass else '❌ FAIL — inspect scores and re-tune THRESHOLD'}")

    if not spike_pass:
        # Suggest threshold adjustments
        scores = [(v["score"], v["kind"]) for v in results.values() if v["sane"]]
        honest_scores = sorted(s for s, k in scores if k == "honest")
        cheat_scores  = sorted(s for s, k in scores if k == "cheat")
        if honest_scores and cheat_scores:
            gap_lo = min(honest_scores)
            gap_hi = max(cheat_scores)
            if gap_lo > gap_hi:
                suggest = round((gap_lo + gap_hi) / 2, 3)
                print(f"  [!] Clean gap detected — try THRESHOLD = {suggest}")
            else:
                print(f"  [!] No clean gap — lowest honest={min(honest_scores):.4f}, "
                      f"highest cheat={max(cheat_scores):.4f}")
                print("      Consider increasing EMBED_WEIGHT or using a stronger embed model.")

    return 0 if spike_pass else 1


if __name__ == "__main__":
    sys.exit(main())
