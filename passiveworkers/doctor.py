#!/usr/bin/env python3
"""
passiveworkers/doctor.py — `pworkers status` / `pworkers doctor`
=============================================
A one-second health check so the common first-run problems — Ollama not running, no models pulled,
an empty library — surface immediately, instead of only after a multi-minute research run fails deep
inside. Every check is independent and crash-safe: one broken probe never hides the others.
"""

from __future__ import annotations

import os

from passiveworkers import paths

# R26: real, structural token budgets each call site already uses — not an invented multiplier.
# researcher.py: PLAN=140 (_plan_queries), REFINE=100/round (_refine_queries), DRAFT=700 (_draft,
# whose own prompt targets 250-400 words, retried once in full on ANY exception — researcher.py's
# bare `except Exception` around the first _draft() call — so the worst case pays for it twice),
# RERANK=120 (passiveworkers/rerank.py's web-evidence reranker, default-on for any non-time-sensitive
# brief with >1 source — researcher.py's `PW_RESEARCH_RERANK` gate; review finding: this call was
# entirely missing from the formula, not just unmeasured — it runs on Ollama's smallest installed
# chat model, which pick_cast's family-diverse selection often does NOT include among the probed
# analysts/editor, so it's costed at `default_rate` below, not a probed rate), REPAIR=700 worst
# case (_repair_draft — only fires if citation-fidelity flags something). judge.py:
# deliberate()'s num_predict = min(1100, max(500, longest*3)) — the review found the assumed
# longest in [250,400] doesn't hold on real fallback paths (a zero-evidence/synthesis-failure
# analyst answer can be ~20 words), so the code's TRUE floor is max(500, 60)=500, not 750;
# EDITOR_LOW reflects that real floor (500+700=1200), EDITOR_HIGH keeps the hard cap (1100+700=1800).
# refine_rounds per depth mirrors researcher.py's own quick=0/standard=2/deep=4 AS REQUESTED —
# a caveat is printed separately for the (undisclosed in this formula) case where a breaking,
# time-sensitive "quick" brief gets silently bumped to "standard" rounds at runtime (researcher.py's
# _bumped_depth) — that depends on brief content this probe never has, so it can't be computed here.
_PLAN, _REFINE, _DRAFT, _RERANK, _REPAIR = 140, 100, 700, 120, 700
_REFINE_ROUNDS = {"quick": 0, "standard": 2, "deep": 4}
_EDITOR_LOW, _EDITOR_HIGH = 1200, 1800


def _probe_tokens_per_sec(model: str, base_url: str, num_predict: int = 64,
                          timeout: float = 30.0) -> float | None:
    """One short, capped generation; tok/s from Ollama's own eval_count/eval_duration (excludes
    model-load and prompt-eval time, unlike wall-clock elapsed on a cold/not-yet-loaded model).
    None on any failure — never raises into `pworkers status`."""
    import requests
    try:
        r = requests.post(f"{base_url}/api/generate", json={
            "model": model, "prompt": "Reply with one short sentence about the weather.",
            "stream": False, "options": {"num_predict": num_predict},
            "keep_alive": "30m"}, timeout=timeout)
        r.raise_for_status()
        d = r.json()
        eval_count = int(d.get("eval_count") or 0)
        eval_ns = int(d.get("eval_duration") or 0)
        if eval_count <= 0 or eval_ns <= 0:
            return None
        return eval_count / (eval_ns / 1e9)
    except Exception:
        return None


def estimate_minutes(tok_per_sec: dict, analysts: list, editor: str, depth: str,
                     default_rate: float = 5.0) -> tuple:
    """(low_minutes, high_minutes) of GENERATION time only — does not model web search /
    page-fetch network latency, which no local probe can measure. LOW = a time-sensitive brief
    (skips the reranker — researcher.py orders by recency instead), no refine rounds, no draft
    retry, no citation-fidelity repair. HIGH = a non-time-sensitive brief (reranker fires), max
    refine rounds for this depth, one full draft retry (researcher.py retries _draft() once in
    full on any exception), and repair fires — real, reachable code paths, not a statistical
    guess at "typical" behavior, though LOW and HIGH describe different real briefs rather than
    one brief's best/worst case (whether the reranker runs depends on brief content, not depth).
    The reranker's own model is usually NOT one of the probed analysts/editor (it runs on Ollama's
    smallest installed chat model — see the module-level comment), so its cost uses
    `default_rate`, not a measured one. A model missing from `tok_per_sec` (the probe couldn't
    reach it) also falls back to `default_rate` rather than dividing by zero."""
    refine_rounds = _REFINE_ROUNDS.get(depth, 2)
    per_analyst_low = _PLAN + _DRAFT
    per_analyst_high = _PLAN + refine_rounds * _REFINE + _DRAFT * 2 + _REPAIR

    def secs(tokens: float, model: str) -> float:
        rate = tok_per_sec.get(model) or default_rate
        return tokens / rate

    low = (sum(secs(per_analyst_low, m) for m in analysts) + secs(_EDITOR_LOW, editor)) / 60.0
    # reranker: default-on for a non-time-sensitive brief, one call per analyst, at default_rate
    # (unprobed model) — added only to HIGH, since LOW models a time-sensitive brief that skips it.
    high_rerank_secs = sum(_RERANK / default_rate for _ in analysts)
    high = (sum(secs(per_analyst_high, m) for m in analysts) + secs(_EDITOR_HIGH, editor)
           + high_rerank_secs) / 60.0
    return (low, high)


def main(argv: list | None = None) -> int:
    eta = "--eta" in (argv or [])
    from passiveworkers import get_version
    print(f"Passive Workers v{get_version()} — status\n")
    healthy = True

    # 1. Ollama + the cast a research run would field right now
    try:
        from passiveworkers.local import OLLAMA, detect_models, pick_cast
        models = detect_models()
        analysts, editor = pick_cast(models, 3)
        print(f"  ✓ Ollama reachable at {OLLAMA} — {len(models)} model(s)")
        print(f"      analysts (up to 3): {', '.join(analysts)}")
        print(f"      editor / judge:     {editor}")
    except SystemExit as e:            # detect_models raises SystemExit with a fix-it message
        healthy = False
        print(f"  ✗ Ollama: {e}")
    except Exception as e:
        healthy = False
        print(f"  ✗ Ollama: {type(e).__name__}: {e}")

    # 1b. Web search backend — grounds every research run. Surface a keyed backend selected
    #     without its key here (one-second check) rather than only when a run fails deep inside.
    try:
        from passiveworkers import research as _R
        backend = _R._backend()
        keyed = [b for b in ("brave", "tavily", "serper") if os.environ.get(_R._KEY_ENV[b])]
        if backend == "off":
            print("  · Web search: off — a `pworkers research` run enables DuckDuckGo automatically")
        elif backend in _R._KEY_ENV:
            if os.environ.get(_R._KEY_ENV[backend]):
                print(f"  ✓ Web search: {backend} (keyed — central API, no egress moat)")
            else:
                others = [b for b in keyed if b != backend]     # other keyed backends that DO have a key
                floor = (", ".join(others) + ", then DuckDuckGo") if others else "DuckDuckGo"
                print(f"  ⚠ Web search: {backend} selected but {_R._KEY_ENV[backend]} not set "
                      f"— will fall back to {floor} (`pworkers config set {_R._KEY_ENV[backend]} …`)")
        else:
            print(f"  ✓ Web search: {backend}")
        if keyed and backend not in _R._KEY_ENV:
            print(f"      keyed fallback ready: {', '.join(keyed)} (used if DuckDuckGo rate-limits)")
    except Exception as e:
        print(f"  · Web search: unavailable ({type(e).__name__})")

    # 2. Private document library (local RAG)
    try:
        from passiveworkers.library import LIB_DB, Library
        srcs = Library().sources()
        if srcs:
            chunks = sum(s["n"] for s in srcs)
            print(f"  ✓ Library: {len(srcs)} document(s), {chunks} chunks — {LIB_DB}")
        else:
            print("  · Library: empty — index your own files with `pworkers library add <path>`")
    except Exception as e:
        print(f"  · Library: unavailable ({type(e).__name__})")

    # 2b. The embedding model `pworkers library add`/--local research depends on (R12 review):
    # informational only — never flips overall `healthy`, matching the Library row's severity.
    try:
        from passiveworkers.library import EMBED_MODEL, _embedder_installed
        installed = _embedder_installed()
        if installed is True:
            print(f"  ✓ Embedder: {EMBED_MODEL} pulled")
        elif installed is False:
            print(f"  · Embedder: {EMBED_MODEL} not pulled — `ollama pull {EMBED_MODEL}` "
                  "(needed for `pworkers library add` / `--local` research)")
        else:
            print("  · Embedder: could not check (Ollama unreachable)")
    except Exception as e:
        print(f"  · Embedder: unavailable ({type(e).__name__})")

    # 3. Network membership (have we joined a coordinator?)
    try:
        from passiveworkers.net.agent import _load_join
        coordinators = paths.coordinator_entries(_load_join())
        if coordinators:
            for url, c in coordinators.items():
                print(f"  ✓ Joined {url} as {c.get('owner', '?')} "
                      f"({c.get('answer_model', '?')}) — resume with `pworkers work`")
        else:
            print("  · Not joined — `pworkers join <url> <token>` to contribute this machine")
    except Exception as e:
        # A user-facing status path must say what happened, never silently substitute a guess —
        # this except swallowing an AttributeError is exactly what let the "default" sentinel key
        # in join.json print a false "Not joined" alongside a true "Joined" line (F1).
        print(f"  · Not joined — `pworkers join <url> <token>` to contribute this machine "
              f"(membership check failed: {type(e).__name__})")

    # 4. Where reports live + how many
    rd = paths.reports_dir()
    try:
        n = len(list(rd.glob("*.md"))) if rd.exists() else 0
    except Exception:
        n = 0
    print(f"  · Reports: {n} in {rd}")

    # 5. Pre-run ETA (opt-in only, --eta): a handful of short generations to measure real tok/s,
    # then a structural low/high estimate per depth. Gated entirely behind the flag so default
    # `pworkers status` stays the documented ~1-second preflight — this genuinely costs a few seconds.
    if eta:
        print("\n  5. Deep-research ETA (--eta; generation time only — excludes web search / "
             "page-fetch latency, which varies by network and backend)")
        try:
            from passiveworkers.local import OLLAMA, detect_models, pick_cast
            models = detect_models()
            analysts, editor = pick_cast(models, 3)
            rates = {m: _probe_tokens_per_sec(m, OLLAMA) for m in set(analysts) | {editor}}
            missing = [m for m, r in rates.items() if r is None]
            if missing:
                print(f"      ⚠ could not measure tok/s for: {', '.join(missing)} — using a "
                     "conservative 5 tok/s fallback for those below")
            shown = {m: r for m, r in rates.items() if r is not None}
            if shown:
                print("      tok/s measured: " + ", ".join(f"{m}={r:.1f}" for m, r in shown.items()))
            for depth in ("quick", "standard", "deep"):
                lo, hi = estimate_minutes(rates, analysts, editor, depth)
                print(f"      {depth:8s} ~{lo:.1f}–{hi:.1f} min")
            print("      ⚠ these ranges assume the depth you request is the depth that runs — a "
                 "breaking, time-sensitive brief silently deepens 'quick' toward 'standard'-level "
                 "refine rounds regardless of the flag, since this probe has no brief to check "
                 "against")
        except Exception as e:
            print(f"      ✗ ETA probe failed: {type(e).__name__}: {e}")

    print()
    print("  Ready to research." if healthy else "  Fix the ✗ item(s) above, then try again.")
    return 0 if healthy else 1
