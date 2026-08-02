#!/usr/bin/env python3
"""
council/mcp_server.py — Passive Workers as an MCP tool (D19)
============================================================
Exposes Passive Workers' flagship task — local deep research — over the Model Context Protocol
(stdio), so your OWN agentic AI — Claude Desktop, Codex, any MCP client — can call it as a tool
(more task types will follow as the network grows). This is the
project's interop play and the founder's worldview made real: the human's assistant
orchestrates; our multi-model, live-web + private-library research engine is the capability
it reaches for. Everything still runs locally; nothing leaves the machine but web searches.

Run:  pw mcp        (or: python -m council.mcp_server)

Claude Desktop config (claude_desktop_config.json):
    {"mcpServers": {"passive-workers": {"command": "pw", "args": ["mcp"]}}}

Tools:
  research(brief, depth="quick", analysts=2, scope="both") -> {report, sources_web,
      sources_local, n_sources, analysts_used, depth_requested, depth_achieved, error}
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


def _run_research_structured(brief: str, depth: str, analysts, scope) -> dict:
    """The research() tool body: normalize the args, run, and return the machine-readable
    degradation signals (sources_web/sources_local/analysts_used/depth_requested/depth_achieved)
    an agent caller can act on (R2 review) instead of grepping report prose. Honors the module
    contract: an MCP client never sees a traceback — SystemExit (e.g. "Can't reach Ollama…") is
    a BaseException, so it must be caught explicitly."""
    from council.local import run
    import json as _json
    brief, depth, analysts, scope, err = _normalize_research_args(brief, depth, analysts, scope)
    if err:
        return {"report": "", "error": err}
    try:
        path = run(brief, depth=depth, n_analysts=analysts, scope=scope, as_json=True)
        payload = _json.loads(path.with_suffix(".json").read_text())
        return {
            "report": payload.get("report", ""),
            "sources_web": payload.get("sources_web", 0),
            "sources_local": payload.get("sources_local", 0),
            "n_sources": payload.get("n_sources", 0),
            "analysts_used": payload.get("analysts_used", 0),
            "depth_requested": payload.get("depth", depth),
            "depth_achieved": payload.get("depth_achieved", depth),
            "error": None,
        }
    except SystemExit as exc:        # e.g. "Can't reach Ollama…" / the R2 zero-source gate
        return {"report": "", "error": str(exc)}
    except Exception as exc:
        return {"report": "", "error": f"{type(exc).__name__}: {exc}"}


def _library_add_text(path: str) -> str:
    """The library_add() tool body, extracted for testing: index a path and convert any failure
    into a clean 'error: …' string. Crucially, a missing optional extra (pypdf / python-docx)
    raises SystemExit — a BaseException, NOT caught by `except Exception` — so we catch it
    explicitly here, or the MCP client would see a raw traceback / a killed server."""
    from council.library import Library
    try:
        n = Library().add(path)
        return f"Indexed {n} chunks from {path}."
    except SystemExit as exc:        # e.g. pip install 'passiveworkers[docs]'
        return f"error: {exc}"
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"


def build_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit("The MCP server needs the optional extra: pip install 'passiveworkers[mcp]'"
                         f"  [{exc}]")

    mcp = FastMCP("passive-workers")

    @mcp.tool()
    def research(brief: str, depth: str = "quick", analysts: int = 2,
                 scope: str = "both") -> dict:
        """Run multi-model local deep research (live web + your private library) and return a
        cited markdown report plus degradation signals. depth: quick|standard|deep (this tool
        defaults lower than `pw research`'s standard/3 to respect client timeouts — see
        depth_requested vs depth_achieved). scope: both|web|local. Takes minutes.
        Fields: report, sources_web, sources_local, n_sources, analysts_used, depth_requested,
        depth_achieved, error (null on success — always check this before trusting `report`)."""
        return _run_research_structured(brief, depth, analysts, scope)

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
        return _library_add_text(path)

    return mcp


def main() -> None:
    build_server().run()   # stdio transport by default


if __name__ == "__main__":
    main()
