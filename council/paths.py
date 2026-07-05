#!/usr/bin/env python3
"""
council/paths.py — where Passive Workers keeps its files, resolved in one place
===============================================================================
Reports default to ``~/.passiveworkers/reports`` so ``pw research``, ``pw serve`` and ``pw mcp``
share ONE history regardless of the process CWD. Previously each used a CWD-relative ``./reports``,
so a report written by the CLI in one directory was invisible to the desk started in another (and
under Claude Desktop the MCP server's CWD is often ``/``, scattering reports). ``PW_REPORTS_DIR``
overrides. Mirrors the private library's ``~/.passiveworkers`` home (see council/library.py).
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
