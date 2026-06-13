#!/usr/bin/env python3
"""
council/mcp_server.py — Passive Workers as an MCP tool (D19)
============================================================
Exposes the local research engine over the Model Context Protocol (stdio), so your OWN
agentic AI — Claude Desktop, Codex, any MCP client — can call it as a tool. This is the
project's interop play and the founder's worldview made real: the human's assistant
orchestrates; our multi-model, live-web + private-library research engine is the capability
it reaches for. Everything still runs locally; nothing leaves the machine but web searches.

Run:  pw mcp        (or: python -m council.mcp_server)

Claude Desktop config (claude_desktop_config.json):
    {"mcpServers": {"passive-workers": {"command": "pw", "args": ["mcp"]}}}

Tools:
  research(brief, depth="quick", analysts=2, scope="both") -> cited markdown report
  library_search(query, k=5)                               -> your private-document hits
  library_add(path)                                        -> index a file/dir into the library
"""

from __future__ import annotations


def _normalize_research_args(brief: str, depth: str, analysts, scope):
    """Validate + clamp research() args at the MCP trust boundary. Returns
    (brief, depth, analysts, scope, error): on a bad brief, error is a clean string and the rest
    are unset; otherwise error is "". Every out-of-range value is clamped to a safe default rather
    than raising — an MCP client never sees a traceback."""
    from council.sanitize import sanitize_brief
    brief = sanitize_brief(brief)
    if not brief:
        return "", "", 2, "both", "error: empty brief — provide a question to research."
    depth = depth if depth in ("quick", "standard", "deep") else "quick"
    scope = scope if scope in ("both", "web", "local") else "both"
    try:
        analysts = max(1, min(4, int(analysts)))
    except (TypeError, ValueError):
        analysts = 2
    return brief, depth, analysts, scope, ""


def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("passive-workers")

    @mcp.tool()
    def research(brief: str, depth: str = "quick", analysts: int = 2,
                 scope: str = "both") -> str:
        """Run multi-model local deep research (live web + your private library) and return a
        cited markdown report. depth: quick|standard|deep. scope: both|web|local. Takes minutes."""
        from council.local import run
        brief, depth, analysts, scope, err = _normalize_research_args(brief, depth, analysts, scope)
        if err:
            return err
        path = run(brief, depth=depth, n_analysts=analysts, scope=scope)
        return path.read_text()

    @mcp.tool()
    def library_search(query: str, k: int = 5) -> str:
        """Search your private document library; returns the top matching passages with sources."""
        from council.library import Library
        from council.sanitize import spotlight
        hits = Library().search(query, k=max(1, min(20, int(k))))
        if not hits:
            return "(no matches — the library may be empty; add files with library_add)"
        # document text is untrusted at this model-facing boundary → sanitize + spotlight
        return "\n\n".join(f"[{h['title']}] (score {h['score']:.2f})\n{spotlight(h['text'][:800])}"
                           for h in hits)

    @mcp.tool()
    def library_add(path: str) -> str:
        """Index a local file or directory (PDF/docx/txt/md) into your private library."""
        from council.library import Library
        n = Library().add(path)
        return f"Indexed {n} chunks from {path}."

    return mcp


def main() -> None:
    build_server().run()   # stdio transport by default


if __name__ == "__main__":
    main()
