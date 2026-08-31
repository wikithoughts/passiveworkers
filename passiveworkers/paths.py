#!/usr/bin/env python3
"""
passiveworkers/paths.py — where Passive Workers keeps its files, resolved in one place
===============================================================================
Reports default to ``~/.passiveworkers/reports`` so ``pworkers research``, ``pworkers serve`` and ``pworkers mcp``
share ONE history regardless of the process CWD. Previously each used a CWD-relative ``./reports``,
so a report written by the CLI in one directory was invisible to the desk started in another (and
under Claude Desktop the MCP server's CWD is often ``/``, scattering reports). ``PW_REPORTS_DIR``
overrides. Mirrors the private library's ``~/.passiveworkers`` home (see passiveworkers/library.py).
"""

from __future__ import annotations

import os
import pathlib


def home() -> pathlib.Path:
    """The Passive Workers home directory (``~/.passiveworkers`` unless ``PW_HOME`` overrides)."""
    return pathlib.Path(os.environ.get("PW_HOME") or (pathlib.Path.home() / ".passiveworkers"))


def reports_dir() -> pathlib.Path:
    """Where research reports are written and read. ``PW_REPORTS_DIR`` overrides the default."""
    override = os.environ.get("PW_REPORTS_DIR")
    return pathlib.Path(override) if override else home() / "reports"


_RESERVED_KEYS = {"default"}


def coordinator_entries(state: dict) -> dict:
    """Per-coordinator entries in a join.json/asker.json-shaped ``{url: {...}, "default": url}``
    dict, excluding the ``"default"`` pointer key. Iterating ``state.items()`` directly crashes
    the moment it reaches ``"default"`` (a string, not a dict) — this is the one safe way to read
    these files."""
    return {k: v for k, v in state.items() if k not in _RESERVED_KEYS and isinstance(v, dict)}


def write_private_json(path: pathlib.Path, state: dict) -> None:
    """Owner-only (0600) JSON write, no world-readable window before chmod."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:      # fdopen owns fd; closed on exit even if json.dump raises
        json.dump(state, f, indent=2)
