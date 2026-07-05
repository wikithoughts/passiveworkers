#!/usr/bin/env python3
"""
council/doctor.py — `pw status` / `pw doctor`
=============================================
A one-second health check so the common first-run problems — Ollama not running, no models pulled,
an empty library — surface immediately, instead of only after a multi-minute research run fails deep
inside. Every check is independent and crash-safe: one broken probe never hides the others.
"""

from __future__ import annotations

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

    # 3. Network membership (have we joined a coordinator?)
    try:
        from council.net.agent import _load_join
        joined = _load_join()
        if joined:
            for url, c in joined.items():
                print(f"  ✓ Joined {url} as {c.get('owner', '?')} "
                      f"({c.get('answer_model', '?')}) — resume with `pw work`")
        else:
            print("  · Not joined — `pw join <url> <token>` to contribute this machine")
    except Exception:
        print("  · Not joined — `pw join <url> <token>` to contribute this machine")

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
