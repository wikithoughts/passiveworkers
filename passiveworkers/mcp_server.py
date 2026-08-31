#!/usr/bin/env python3
"""
passiveworkers/mcp_server.py — Passive Workers as an MCP tool (D19)
============================================================
Exposes Passive Workers' flagship task — local deep research — over the Model Context Protocol
(stdio), so your OWN agentic AI — Claude Desktop, Codex, any MCP client — can call it as a tool
(more task types will follow as the network grows). This is the
project's interop play and the founder's worldview made real: the human's assistant
orchestrates; our multi-model, live-web + private-library research engine is the capability
it reaches for. Everything still runs locally; nothing leaves the machine but web searches.

Run:  pworkers mcp        (or: python -m passiveworkers.mcp_server)

Claude Desktop config (claude_desktop_config.json):
    {"mcpServers": {"passive-workers": {"command": "pworkers", "args": ["mcp"]}}}

Tools:
  research(brief, depth="quick", analysts=2, scope="both") -> {report, sources_web,
      sources_local, n_sources, analysts_used, depth_requested, depth_achieved, error}
      Sends MCP progress notifications as research advances, if the client sent a
      progressToken (R26) — a side channel; the returned dict is unaffected.
  library_search(query, k=5)                               -> your private-document hits
  library_add(path)                                        -> index a file/dir into the library
"""

from __future__ import annotations

import asyncio

try:
    from mcp.server.fastmcp import Context, FastMCP
except ImportError:
    # Kept optional (pworkers mcp / build_server() give the actual install hint) — but `Context` must
    # still be a real MODULE-level name when the extra IS installed: `from __future__ import
    # annotations` makes every annotation a lazy string, and FastMCP resolves tool signatures via
    # `inspect.signature(fn, eval_str=True)` against `fn.__globals__` — a name only ever imported
    # inside build_server()'s own local scope is invisible to that lookup and raises
    # InvalidSignature at registration time, not at import time (found via the actual test run).
    Context = None    # type: ignore[assignment]
    FastMCP = None    # type: ignore[assignment]


def _normalize_research_args(brief: str, depth: str, analysts, scope):
    """Validate + clamp research() args at the MCP trust boundary. Returns
    (brief, depth, analysts, scope, error): on a bad brief, error is a clean string and the rest
    are unset; otherwise error is "". Every out-of-range value is clamped to a safe default rather
    than raising — an MCP client never sees a traceback."""
    from passiveworkers.sanitize import sanitize_brief
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


def _run_research_structured(brief: str, depth: str, analysts, scope, on_progress=None) -> dict:
    """The research() tool body: normalize the args, run, and return the machine-readable
    degradation signals (sources_web/sources_local/analysts_used/depth_requested/depth_achieved)
    an agent caller can act on (R2 review) instead of grepping report prose. Honors the module
    contract: an MCP client never sees a traceback — SystemExit (e.g. "Can't reach Ollama…") is
    a BaseException, so it must be caught explicitly."""
    from passiveworkers.local import run
    import json as _json
    brief, depth, analysts, scope, err = _normalize_research_args(brief, depth, analysts, scope)
    if err:
        return {"report": "", "error": err}
    try:
        path = run(brief, depth=depth, n_analysts=analysts, scope=scope, as_json=True,
                   on_progress=on_progress)
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


def _make_mcp_progress(ctx, loop, timeout: float = 5.0):
    """R26: bridges passiveworkers.local.run()'s synchronous on_progress(msg) callback — invoked from
    the worker thread asyncio.to_thread() runs the blocking research() call in — to an async
    ctx.report_progress() call on the event loop. asyncio.run_coroutine_threadsafe is the
    standard way to schedule a coroutine onto a loop from a different thread; blocking (bounded
    by `timeout`) on the returned future makes delivery deterministic rather than fire-and-forget
    (cheap: a stdio write). A stuck/broken client must never stall the actual research run — on
    timeout we CANCEL the scheduled future, not just stop waiting on it (review finding): the
    stdio transport serializes every write, including the eventual tool response, through one
    zero-buffer channel (mcp.server.stdio's anyio memory stream) — an abandoned-but-not-cancelled
    send from a stalled client would sit in that channel forever and could block the finished
    report from ever reaching the client, not just cost this one call its timeout. Progress is a
    plain increasing step counter with total=None (indeterminate) — the real step count varies
    with scope/depth/whether the citation-fidelity repair fires, so a precomputed total would be
    false precision dressed up as a number. ctx=None (a client that never opens a Context, or
    report_progress() with no progressToken) is a safe no-op either way — report_progress()
    itself no-ops without a token."""
    counter = {"n": 0}
    pending = {"fut": None}

    def on_progress(msg: str) -> None:
        counter["n"] += 1
        if ctx is None:
            return
        # Bound the backlog to at most one outstanding notification — if an earlier one is
        # still in flight (shouldn't happen in today's single-worker-thread call pattern, but
        # cheap to guard), cancel it before scheduling another rather than letting them pile up.
        prev = pending["fut"]
        if prev is not None and not prev.done():
            prev.cancel()
        fut = asyncio.run_coroutine_threadsafe(
            ctx.report_progress(progress=float(counter["n"]), total=None, message=msg), loop)
        pending["fut"] = fut
        try:
            fut.result(timeout=timeout)
        except Exception:
            fut.cancel()
    return on_progress


async def _research_tool(brief: str, depth: str, analysts, scope, ctx=None) -> dict:
    """Async body of the research() MCP tool, extracted for testing without a real FastMCP
    transport/session — same pattern as _run_research_structured. The blocking call chain
    (passiveworkers.local.run -> ResearchWorker.research) runs in a thread so progress notifications
    (and any other concurrent MCP traffic) aren't stalled behind it."""
    loop = asyncio.get_running_loop()
    on_progress = _make_mcp_progress(ctx, loop)
    return await asyncio.to_thread(_run_research_structured, brief, depth, analysts, scope,
                                   on_progress)


def _library_add_text(path: str) -> str:
    """The library_add() tool body, extracted for testing: index a path and convert any failure
    into a clean 'error: …' string. Crucially, a missing optional extra (pypdf / python-docx)
    raises SystemExit — a BaseException, NOT caught by `except Exception` — so we catch it
    explicitly here, or the MCP client would see a raw traceback / a killed server."""
    from passiveworkers.library import Library
    try:
        n = Library().add(path)
        return f"Indexed {n} chunks from {path}."
    except SystemExit as exc:        # e.g. pip install 'passiveworkers[docs]'
        return f"error: {exc}"
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"


def build_server():
    if FastMCP is None:
        raise SystemExit("The MCP server needs the optional extra: pip install 'passiveworkers[mcp]'")

    mcp = FastMCP("passive-workers")

    @mcp.tool()
    async def research(brief: str, depth: str = "quick", analysts: int = 2,
                       scope: str = "both", ctx: Context | None = None) -> dict:
        """Run multi-model local deep research (live web + your private library) and return a
        cited markdown report plus degradation signals. depth: quick|standard|deep (this tool
        defaults lower than `pworkers research`'s standard/3 to respect client timeouts — see
        depth_requested vs depth_achieved). scope: both|web|local. Takes minutes. Sends MCP
        progress notifications as research advances if the client provided a progressToken —
        a pure side channel; the returned dict's shape never changes based on it.
        Fields: report, sources_web, sources_local, n_sources, analysts_used, depth_requested,
        depth_achieved, error (null on success — always check this before trusting `report`)."""
        return await _research_tool(brief, depth, analysts, scope, ctx)

    @mcp.tool()
    def library_search(query: str, k: int = 5) -> str:
        """Search your private document library; returns the top matching passages with sources."""
        from passiveworkers.library import Library
        from passiveworkers.sanitize import spotlight
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
