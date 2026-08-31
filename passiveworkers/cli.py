#!/usr/bin/env python3
"""
passiveworkers/cli.py — the `pworkers` command
==================================
Make your computer work for you (and, opt-in, for others). Research is the flagship task.

  Single-player — run jobs on your own machine:
    pworkers research "your brief" [--quick|--deep] [--editor api] [--analysts N] [--local|--web] [--json] [--html]
    pworkers serve                       # local research desk at http://127.0.0.1:8770
    pworkers status [--eta]               # is Ollama up? which models? library? joined?  (alias: pworkers doctor)
                                     # --eta probes tok/s and prints a real time estimate per depth
    pworkers reports                     # list past reports
    pworkers library add <path|dir>      # index your own documents (private, local RAG)
    pworkers library list | search <query> | remove <path> | clear
    pworkers mcp                         # run as an MCP server (Claude Desktop, Codex, …)
    pworkers config [set <KEY> <VALUE>]  # persist settings (Ollama URL, web backend, API keys) — no more env vars
    pworkers version                     # print the installed version  (also: pworkers --version)

  The network — do work for / with other computers (opt-in):
    pworkers join <coordinator-url> <enrollment-token>   # contribute this machine (one command)
    pworkers work                        # resume contributing after you've joined once
    pworkers tasks                       # list open offers your machine can take on
    pworkers accept <id> | deliver <id> <text | @file <job>>
    pworkers ask "<brief>" [--type T] [--enroll-token TOK]   # submit a job to the network (asker)
    pworkers fetch <job> <dir>           # download + verify a delivered file (asker)
    pworkers rate <job> <0-10>           # rate a deliverable → operator reputation (asker)
    pworkers credit                      # this machine's own balance/reputation/jobs-helped (operator)
    pworkers invite [--owner N] [--kind any|node|user] [--grant N] [--max-uses N]  # mint an enrollment token (admin)
    pworkers keygen | fingerprint        # create / print your signing key + fingerprint (operator)
    pworkers trust add <op> <key> | list | remove <op>   # pin operator keys out of band (asker)
"""

from __future__ import annotations

import sys


def main() -> int:
    # FIRST, before any subcommand module is imported: seed os.environ from ~/.passiveworkers/
    # config.json via setdefault, so a user's persisted settings become the defaults for every
    # command while an explicitly exported env var still wins. Import-light + crash-safe.
    from passiveworkers import config
    config.apply_to_env()

    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if args[0] in ("version", "--version", "-V"):
        from passiveworkers import get_version
        print(f"passiveworkers {get_version()}")
        return 0
    cmd, rest = args[0], args[1:]
    if cmd == "research":
        sys.argv = ["pworkers research"] + rest
        from passiveworkers.local import main as research_main
        return research_main()
    if cmd == "serve":
        from passiveworkers.serve import main as serve_main
        serve_main()
        return 0
    if cmd in ("status", "doctor"):
        from passiveworkers.doctor import main as doctor_main
        return doctor_main(rest)
    if cmd == "reports":
        return _list_reports()
    if cmd == "library":
        sys.argv = ["pworkers library"] + rest
        from passiveworkers.library import main as library_main
        return library_main()
    if cmd == "mcp":
        from passiveworkers.mcp_server import main as mcp_main
        mcp_main()
        return 0
    if cmd == "config":
        from passiveworkers.config import main as config_main
        return config_main(rest)
    if cmd in ("join", "work"):
        # one-command operator onboarding / resume — starts the long-running worker loop
        from passiveworkers.net.agent import join as join_main
        return join_main([cmd] + rest)
    if cmd == "ask":
        sys.argv = ["pworkers ask"] + rest
        from passiveworkers.net.submit import ask_main
        return ask_main()
    if cmd in ("tasks", "accept", "deliver", "fetch", "keygen", "rate", "fingerprint", "trust",
               "credit", "invite"):
        sys.argv = ["pworkers", cmd] + rest   # operator.main dispatches on argv[1] (the verb)
        from passiveworkers.operator import main as operator_main
        return operator_main()
    print(f"unknown command: {cmd}\n\n{__doc__.strip()}")
    return 2


def _list_reports() -> int:
    from passiveworkers import paths
    rd = paths.reports_dir()
    files = sorted(rd.glob("*.md"), reverse=True) if rd.exists() else []
    if not files:
        print(f"no reports yet in {rd} — run `pworkers research \"...\"`")
        return 0
    for f in files:
        print(f"  {f.name}")
    print(f"\n{len(files)} report(s) in {rd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
