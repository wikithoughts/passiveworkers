#!/usr/bin/env python3
"""
council/cli.py — the `pw` command
==================================
    pw research "your brief" [--quick|--deep] [--editor api] [--analysts N] [--local|--web]
    pw serve                       # local research desk at http://127.0.0.1:8770
    pw library add <path|dir>      # index your own documents (private, local RAG)
    pw library list|remove|clear
    pw mcp                         # run as an MCP server (Claude Desktop, Codex, …)
    pw tasks                       # list open assisted offers you can do (federation)
    pw accept <id> | deliver <id> <text|@file>
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
    if cmd == "library":
        sys.argv = ["pw library"] + rest
        from council.library import main as library_main
        return library_main()
    if cmd == "mcp":
        from council.mcp_server import main as mcp_main
        mcp_main()
        return 0
    if cmd in ("tasks", "accept", "deliver", "fetch", "keygen"):
        sys.argv = ["pw", cmd] + rest   # operator.main dispatches on argv[1] (the verb)
        from council.operator import main as operator_main
        return operator_main()
    print(f"unknown command: {cmd}\n\n{__doc__.strip()}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
