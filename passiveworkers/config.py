#!/usr/bin/env python3
"""
passiveworkers/config.py — `pworkers config`: persist your settings once, instead of re-exporting env vars
=============================================================================================
Every knob in Passive Workers is read live from the environment (``os.environ.get("PW_…",
default)``) at ~60 call sites. That is flexible but means a user has to ``export`` their
preferences — Ollama endpoint, models, web backend, country, a search API key — in every new
shell. This module gives them one owner-only file, ``~/.passiveworkers/config.json``, and one
command (``pworkers config``) to read/write it.

The keystone is :func:`apply_to_env`, called as the FIRST thing in ``passiveworkers.cli.main`` (before
any subcommand module is imported). It seeds ``os.environ`` from the file using ``setdefault``,
so the precedence is exactly:

    explicit shell env  >  this config file  >  the code default

An env var already exported in the shell is left untouched (explicit wins); anything unset falls
back to the file, then to each read site's own default. Because we seed the *environment* rather
than plumb a config object through the codebase, all ~60 existing read sites keep working with
zero changes.

Security: the file is written 0600 from creation (it may hold API keys / a coordinator token —
bearer credentials), mirroring ``passiveworkers.net.agent._save_join`` and ``passiveworkers.crypto``. Secret
values are masked in ``pworkers config list`` and never logged. This module is intentionally stdlib-only
and import-light — it runs on every ``pworkers`` invocation.
"""

from __future__ import annotations

import difflib
import json
import os
import re

from passiveworkers import paths

# ---- Recognized settings: name -> (help, secret?). This is the curated, documented surface
# `pworkers config list` shows and offers typo-correction against. `set` still accepts any other
# well-formed PW_* key (power users / ops vars) with a soft notice — the allowlist guards typos,
# it does not box anyone in. Secrets are masked on display.
KNOWN: dict[str, dict] = {
    # engine
    "PW_OLLAMA_BASE":       {"help": "Ollama endpoint (default http://localhost:11434)", "secret": False},
    "PW_OLLAMA_KEEP_ALIVE": {"help": "how long Ollama keeps a model warm (default 30m)",  "secret": False},
    "PW_OLLAMA_TIMEOUT":    {"help": "per-generation timeout seconds (default 300)",      "secret": False},
    "PW_INFERENCE_BACKEND":  {"help": "ollama | openai — which inference API shape to speak (default ollama)", "secret": False},
    "PW_INFERENCE_API_BASE": {"help": "OpenAI-compatible endpoint (llama.cpp-server/LM Studio) when PW_INFERENCE_BACKEND=openai", "secret": False},
    "PW_MODEL_CAP_GB":      {"help": "skip models larger than this many GB (0 = no cap)", "secret": False},
    "PW_PAGE_EVIDENCE":     {"help": "fetch full source pages for drafting, not just snippets (default 1)", "secret": False},
    "PW_COUNTRY":           {"help": "your location, tags the analyst vantage point",     "secret": False},
    "PW_EDITOR_MODEL":      {"help": "model for `pworkers research --editor api` (default openai/gpt-5-chat)", "secret": False},
    "PW_REPORTS_DIR":       {"help": "where reports are written (default ~/.passiveworkers/reports)",    "secret": False},
    # web search
    "PW_WEB_BACKEND":       {"help": "off | ddgs | searxng | brave | tavily | serper (default ddgs for research)", "secret": False},
    "PW_WEB_RESULTS":       {"help": "max search results per query (default 5)",          "secret": False},
    "PW_WEB_TIMEOUT":       {"help": "per-search-request timeout seconds (default 8)",     "secret": False},
    "PW_SEARXNG_URL":       {"help": "self-hosted SearXNG endpoint (auto-used if reachable)", "secret": False},
    "PW_SOURCE_ROUTING":    {"help": "augment web with arXiv/Wikipedia when apt: on | off (default on)", "secret": False},
    "PW_DDG_BREAKER":       {"help": "stop retrying DDG after N consecutive failures (default 3; 0 disables)", "secret": False},
    "PW_DDG_BREAKER_COOLDOWN": {"help": "seconds before the DDG breaker auto-clears and retries (default 300)", "secret": False},
    "PW_BRAVE_KEY":         {"help": "Brave Search API key (X-Subscription-Token)",        "secret": True},
    "PW_TAVILY_KEY":        {"help": "Tavily Search API key",                              "secret": True},
    "PW_SERPER_KEY":        {"help": "Serper.dev (Google) API key",                        "secret": True},
    # paid editor / baseline
    "OPENROUTER_API_KEY":   {"help": "OpenRouter key for the API editor / eval baseline",  "secret": True},
    "PW_BASELINE_API_KEY":  {"help": "alt key for the API editor / eval baseline",         "secret": True},
    "PW_BASELINE_API_URL":  {"help": "chat-completions endpoint for the API editor",       "secret": False},
    # private library (local RAG)
    "PW_EMBED_MODEL":       {"help": "embedding model for the library (default nomic-embed-text)", "secret": False},
    "PW_LIBRARY_DIR":       {"help": "where library.db + node identity live (default ~/.passiveworkers)", "secret": False},
    "PW_LIBRARY_ROOTS":     {"help": "colon-separated roots the library may index (default $HOME)", "secret": False},
    "PW_RERANK":            {"help": "enable cross-encoder rerank in library search: 1 | (empty)", "secret": False},
    "PW_CONTEXTUAL_CHUNKS": {"help": "prepend an LLM-written situating blurb per chunk before embedding (contextual retrieval): 1 | (empty)", "secret": False},
    # research pipeline (pworkers research)
    "PW_RESEARCH_RERANK":   {"help": "rerank web evidence by relevance on non-temporal briefs: 1 (default) | 0", "secret": False},
    "PW_RESEARCH_MAX_ROUNDS": {"help": "max adaptive research refine rounds (default 2; deep 4)", "secret": False},
    "PW_RESEARCH_DEADLINE": {"help": "wall-clock budget in s for a node's retrieval loop (default 240)", "secret": False},
    "PW_RESEARCH_MAX_SOURCES": {"help": "max web sources collected before refine stops (default 30)", "secret": False},
    # local research desk
    "PW_SERVE_HOST":        {"help": "bind host for `pworkers serve` (default 127.0.0.1)",       "secret": False},
    "PW_SERVE_PORT":        {"help": "port for `pworkers serve` (default 8770)",                 "secret": False},
    "PW_SERVE_MAX_JOBS":    {"help": "max concurrent desk research jobs (default 2)",       "secret": False},
}

_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_SECRET_HINT = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)$")
# Redact an inline `user:pass@` / `token@` credential from a URL value even when the KEY itself is
# not classified secret (PW_SEARXNG_URL / PW_BASELINE_API_URL / PW_OLLAMA_BASE can carry basic-auth).
_USERINFO_RE = re.compile(r"(://)[^/@\s]+@")


def path():
    """The config file location (``~/.passiveworkers/config.json``; ``PW_HOME`` moves the home)."""
    return paths.home() / "config.json"


def load() -> dict:
    """The persisted config as a plain dict. Tolerant: returns ``{}`` on missing/corrupt file so a
    hand-mangled config can never break the CLI (the value of a config store is that it fails soft)."""
    try:
        data = json.loads(path().read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(state: dict) -> None:
    """Persist owner-only (0600) FROM CREATION — the file may hold API keys / a bearer token, so it
    must never have a world-readable window. Same atomic pattern as passiveworkers.net.agent._save_join."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(state, indent=2, sort_keys=True).encode()
    try:
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        os.chmod(p, 0o600)
    except Exception:
        # Fallback for platforms without os.open mode support: a restrictive umask around the
        # create avoids the world-readable window that write_text() alone (0o644) would open.
        old = os.umask(0o077)
        try:
            p.write_text(json.dumps(state, indent=2, sort_keys=True))
        finally:
            os.umask(old)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass


def get(key: str):
    """The persisted value for ``key``, or ``None`` if unset."""
    return load().get(key)


def set(key: str, value: str) -> None:  # noqa: A001 — deliberately named for the `pworkers config set` verb
    """Persist ``key=value``. Raises ``ValueError`` for a malformed key name."""
    if not is_wellformed(key):
        raise ValueError(key)
    state = load()
    state[key] = value
    _save(state)


def unset(key: str) -> bool:
    """Remove ``key`` from the config. Returns True if it was present."""
    state = load()
    if key in state:
        del state[key]
        _save(state)
        return True
    return False


def apply_to_env() -> None:
    """Seed ``os.environ`` from the config file via ``setdefault`` — the keystone (see module doc).
    Call this ONCE, first thing, before any subcommand module is imported. Crash-safe: a corrupt
    config or an odd key never aborts the CLI (best-effort per key)."""
    try:
        for k, v in load().items():
            if not k:
                continue
            try:
                os.environ.setdefault(str(k), str(v))
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------- helpers
def is_wellformed(key: str) -> bool:
    """A settable key is either a recognized name or an UPPER_SNAKE env-style name. This rejects
    lowercase/typo'd/whitespace keys (so ``pworkers config set ollama_base …`` errors helpfully) while
    still allowing power users to persist any well-formed PW_* / env var not in KNOWN."""
    return bool(key) and (key in KNOWN or bool(_KEY_RE.fullmatch(key)))


def is_secret(key: str) -> bool:
    """Whether a value should be masked on display — a KNOWN secret, or any key whose NAME ends in
    KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL (so an unrecognized secret is still never shown in clear)."""
    if key in KNOWN:
        return bool(KNOWN[key]["secret"])
    return bool(_SECRET_HINT.search(key))


def mask(value: str) -> str:
    """Mask a secret for display: the last 4 chars survive (credit-card style) ONLY when the value is
    long enough that this still hides most of it; a short value is fully bulleted, never partly shown
    (a 4-char reveal on a 5-char secret would disclose almost everything)."""
    s = str(value)
    return ("••••" + s[-4:]) if len(s) >= 12 else "••••"


def suggest(key: str) -> str:
    """Closest KNOWN key(s) for a typo'd input — the 'did you mean' hint."""
    near = difflib.get_close_matches(key.upper(), list(KNOWN), n=2, cutoff=0.6)
    return f" — did you mean {' or '.join(near)}?" if near else ""


# ---------------------------------------------------------------- `pworkers config` CLI
_USAGE = (
    "usage:\n"
    "  pworkers config [list]              show current settings (secrets masked)\n"
    "  pworkers config get <KEY>           print one value\n"
    "  pworkers config set <KEY> <VALUE>   persist a setting\n"
    "  pworkers config unset <KEY>         remove a setting\n"
    "  pworkers config path                print the config file location"
)


def _display(key: str, value: str) -> str:
    if is_secret(key):
        return mask(value)
    return _USERINFO_RE.sub(r"\1••••@", str(value))   # strip basic-auth creds from URL-ish values


def _cmd_list() -> int:
    state = load()
    p = path()
    if state:
        print(f"Current config — {p}\n")
        for k in sorted(state):
            tag = "" if k in KNOWN else "   (custom)"
            print(f"  {k} = {_display(k, state[k])}{tag}")
    else:
        print(f"No config set yet — {p} does not exist.\n"
              f"Set one with e.g. `pworkers config set PW_WEB_BACKEND ddgs`.")
    print("\nRecognized settings (override with an exported env var any time):")
    for k in KNOWN:
        marker = "●" if k in state else "○"
        print(f"  {marker} {k:<22} {KNOWN[k]['help']}")
    print("\n  ● = set here   ○ = using default")
    return 0


def main(argv: list | None = None) -> int:
    args = list(argv if argv is not None else [])
    verb = args[0] if args else "list"

    if verb in ("list", "-h", "--help", "help"):
        if verb != "list":
            print(_USAGE + "\n")
        return _cmd_list()

    if verb == "path":
        print(path())
        return 0

    if verb == "get":
        if len(args) < 2:
            print("usage: pworkers config get <KEY>")
            return 2
        key = args[1]
        val = get(key)
        if val is None:
            print(f"{key} is not set (using the built-in default)")
            return 1
        print(_display(key, val))
        return 0

    if verb == "set":
        if len(args) < 3:
            print("usage: pworkers config set <KEY> <VALUE>")
            return 2
        key = args[1]
        value = " ".join(args[2:])          # forgiving: tolerate an unquoted multi-word value
        if not is_wellformed(key):
            print(f"invalid key name: {key!r}{suggest(key)}\n"
                  f"keys are UPPER_SNAKE_CASE — see `pworkers config list` for recognized settings")
            return 2
        set(key, value)
        note = "" if key in KNOWN else "  (not a recognized setting — stored anyway; typo? see `pworkers config list`)"
        print(f"set {key} = {_display(key, value)}{note}")
        return 0

    if verb == "unset":
        if len(args) < 2:
            print("usage: pworkers config unset <KEY>")
            return 2
        key = args[1]
        print(f"unset {key}" if unset(key) else f"{key} was not set")
        return 0

    print(f"unknown config command: {verb}\n\n{_USAGE}")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
