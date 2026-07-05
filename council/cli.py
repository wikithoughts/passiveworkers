#!/usr/bin/env python3
"""
council/cli.py — the `pw` command
==================================
Make your computer work for you (and, opt-in, for others). Research is the flagship task.

  Single-player — run jobs on your own machine:
    pw research "your brief" [--quick|--deep] [--editor api] [--analysts N] [--local|--web] [--json] [--html]
    pw serve                       # local research desk at http://127.0.0.1:8770
    pw status                      # is Ollama up? which models? library? joined?  (alias: pw doctor)
    pw reports                     # list past reports
    pw library add <path|dir>      # index your own documents (private, local RAG)
    pw library list | search <query> | remove <path> | clear
    pw mcp                         # run as an MCP server (Claude Desktop, Codex, …)
    pw version                     # print the installed version  (also: pw --version)

  The network — do work for / with other computers (opt-in):
    pw join <coordinator-url> <enrollment-token>   # contribute this machine (one command)
    pw work                        # resume contributing after you've joined once
    pw tasks                       # list open offers your machine can take on
    pw accept <id> | deliver <id> <text | @file <job>>
    pw fetch <job> <dir>           # download + verify a delivered file (asker)
    pw rate <job> <0-10>           # rate a deliverable → operator reputation (asker)
    pw keygen | fingerprint        # create / print your signing key + fingerprint (operator)
    pw trust add <op> <key> | list | remove <op>   # pin operator keys out of band (asker)
"""

from __future__ import annotations

import sys


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if args[0] in ("version", "--version", "-V"):
        from council import get_version
        print(f"passiveworkers {get_version()}")
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
    if cmd in ("status", "doctor"):
        from council.doctor import main as doctor_main
        return doctor_main()
    if cmd == "reports":
        return _list_reports()
    if cmd == "library":
        sys.argv = ["pw library"] + rest
        from council.library import main as library_main
        return library_main()
    if cmd == "mcp":
        from council.mcp_server import main as mcp_main
        mcp_main()
        return 0
    if cmd in ("join", "work"):
        # one-command operator onboarding / resume — starts the long-running worker loop
        from council.net.agent import join as join_main
        return join_main([cmd] + rest)
    if cmd in ("tasks", "accept", "deliver", "fetch", "keygen", "rate", "fingerprint", "trust"):
        sys.argv = ["pw", cmd] + rest   # operator.main dispatches on argv[1] (the verb)
        from council.operator import main as operator_main
        return operator_main()
    print(f"unknown command: {cmd}\n\n{__doc__.strip()}")
    return 2


def _list_reports() -> int:
    from council import paths
    rd = paths.reports_dir()
    files = sorted(rd.glob("*.md"), reverse=True) if rd.exists() else []
    if not files:
        print(f"no reports yet in {rd} — run `pw research \"...\"`")
        return 0
    for f in files:
        print(f"  {f.name}")
    print(f"\n{len(files)} report(s) in {rd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
