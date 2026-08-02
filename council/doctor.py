#!/usr/bin/env python3
"""
council/doctor.py — `pw status` / `pw doctor`
=============================================
A one-second health check so the common first-run problems — Ollama not running, no models pulled,
an empty library — surface immediately, instead of only after a multi-minute research run fails deep
inside. Every check is independent and crash-safe: one broken probe never hides the others.
"""

from __future__ import annotations

import os

from council import paths


def main() -> int:
    from council import get_version
    print(f"Passive Workers v{get_version()} — status\n")
    healthy = True

    # 1. Ollama + the cast a research run would field right now
    try:
        from council.local import OLLAMA, detect_models, pick_cast
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
        from council import research as _R
        backend = _R._backend()
        keyed = [b for b in ("brave", "tavily", "serper") if os.environ.get(_R._KEY_ENV[b])]
        if backend == "off":
            print("  · Web search: off — a `pw research` run enables DuckDuckGo automatically")
        elif backend in _R._KEY_ENV:
            if os.environ.get(_R._KEY_ENV[backend]):
                print(f"  ✓ Web search: {backend} (keyed — central API, no egress moat)")
            else:
                others = [b for b in keyed if b != backend]     # other keyed backends that DO have a key
                floor = (", ".join(others) + ", then DuckDuckGo") if others else "DuckDuckGo"
                print(f"  ⚠ Web search: {backend} selected but {_R._KEY_ENV[backend]} not set "
                      f"— will fall back to {floor} (`pw config set {_R._KEY_ENV[backend]} …`)")
        else:
            print(f"  ✓ Web search: {backend}")
        if keyed and backend not in _R._KEY_ENV:
            print(f"      keyed fallback ready: {', '.join(keyed)} (used if DuckDuckGo rate-limits)")
    except Exception as e:
        print(f"  · Web search: unavailable ({type(e).__name__})")

    # 2. Private document library (local RAG)
    try:
        from council.library import LIB_DB, Library
        srcs = Library().sources()
        if srcs:
            chunks = sum(s["n"] for s in srcs)
            print(f"  ✓ Library: {len(srcs)} document(s), {chunks} chunks — {LIB_DB}")
        else:
            print("  · Library: empty — index your own files with `pw library add <path>`")
    except Exception as e:
        print(f"  · Library: unavailable ({type(e).__name__})")

    # 2b. The embedding model `pw library add`/--local research depends on (R12 review):
    # informational only — never flips overall `healthy`, matching the Library row's severity.
    try:
        from council.library import EMBED_MODEL, _embedder_installed
        installed = _embedder_installed()
        if installed is True:
            print(f"  ✓ Embedder: {EMBED_MODEL} pulled")
        elif installed is False:
            print(f"  · Embedder: {EMBED_MODEL} not pulled — `ollama pull {EMBED_MODEL}` "
                  "(needed for `pw library add` / `--local` research)")
        else:
            print("  · Embedder: could not check (Ollama unreachable)")
    except Exception as e:
        print(f"  · Embedder: unavailable ({type(e).__name__})")

    # 3. Network membership (have we joined a coordinator?)
    try:
        from council.net.agent import _load_join
        coordinators = paths.coordinator_entries(_load_join())
        if coordinators:
            for url, c in coordinators.items():
                print(f"  ✓ Joined {url} as {c.get('owner', '?')} "
                      f"({c.get('answer_model', '?')}) — resume with `pw work`")
        else:
            print("  · Not joined — `pw join <url> <token>` to contribute this machine")
    except Exception as e:
        # A user-facing status path must say what happened, never silently substitute a guess —
        # this except swallowing an AttributeError is exactly what let the "default" sentinel key
        # in join.json print a false "Not joined" alongside a true "Joined" line (F1).
        print(f"  · Not joined — `pw join <url> <token>` to contribute this machine "
              f"(membership check failed: {type(e).__name__})")

    # 4. Where reports live + how many
    rd = paths.reports_dir()
    try:
        n = len(list(rd.glob("*.md"))) if rd.exists() else 0
    except Exception:
        n = 0
    print(f"  · Reports: {n} in {rd}")

    print()
    print("  Ready to research." if healthy else "  Fix the ✗ item(s) above, then try again.")
    return 0 if healthy else 1
