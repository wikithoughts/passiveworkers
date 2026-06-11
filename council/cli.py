#!/usr/bin/env python3
"""
council/cli.py — the `pw` command
==================================
    pw research "your brief" [--quick|--deep] [--editor api] [--analysts N]
    pw serve                       # local research desk at http://127.0.0.1:8770
"""

from __future__ import annotations

import sys


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    cmd, rest = args[0], args[1:]
    if cmd == "research":
        sys.argv = ["pw research"] + rest
        from council.local import main as research_main
        return research_main()
    if cmd == "serve":
        from council.serve import main as serve_main
        serve_main()
        return 0
    print(f"unknown command: {cmd}\n\n{__doc__.strip()}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
