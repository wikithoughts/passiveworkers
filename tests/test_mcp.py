"""MCP trust boundary: arg clamping (_normalize_research_args) and the no-traceback contract for
the research()/library_add() tool bodies — an MCP client must always get a clean string, never an
exception (incl. SystemExit, which is BaseException and slips past `except Exception`)."""
import asyncio

import pytest

import council.mcp_server as M
from council.mcp_server import _normalize_research_args


def test_empty_brief_returns_clean_error():
    brief, depth, analysts, scope, err = _normalize_research_args("", "quick", 2, "both")
    assert err.startswith("error:") and "empty brief" in err


def test_valid_args_pass_through():
    brief, depth, analysts, scope, err = _normalize_research_args("research X", "deep", 3, "web")
    assert err == "" and depth == "deep" and analysts == 3 and scope == "web" and "research X" in brief


def test_bad_depth_and_scope_default_safely():
    _, depth, _, scope, err = _normalize_research_args("q", "ultra", 2, "galaxy")
    assert err == "" and depth == "quick" and scope == "both"


def test_analysts_are_clamped():
    assert _normalize_research_args("q", "quick", 99, "both")[2] == 4      # cap high
    assert _normalize_research_args("q", "quick", 0, "both")[2] == 1       # floor low
    assert _normalize_research_args("q", "quick", "x", "both")[2] == 2     # non-int → default 2


# ---------------------------------------------------------------- the no-traceback contract
def test_research_tool_systemexit_becomes_error_field(monkeypatch):
    # run() raises SystemExit on the #1 first-run failure (Ollama down) — must come back as a
    # structured error, never a traceback (SystemExit is a BaseException, slips past `except
    # Exception` if not caught explicitly).
    def boom(*a, **k):
        raise SystemExit("Can't reach Ollama at http://x. Start it with `ollama serve`.")
    monkeypatch.setattr("council.local.run", boom)
    out = M._run_research_structured("real question", "quick", 2, "both")
    assert out["error"] and "ollama serve" in out["error"].lower()
    assert out["report"] == ""


def test_research_tool_generic_exception_becomes_error_field(monkeypatch):
    monkeypatch.setattr("council.local.run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    out = M._run_research_structured("real question", "quick", 2, "both")
    assert "RuntimeError" in out["error"] and "disk full" in out["error"]


def test_research_tool_empty_brief_short_circuits(monkeypatch):
    # bad input never reaches run() — clean error, no exception
    monkeypatch.setattr("council.local.run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    out = M._run_research_structured("", "quick", 2, "both")
    assert out["error"]


def test_research_tool_success_returns_degradation_signals(monkeypatch, tmp_path):
    # R2 review: an MCP caller must see sources_web/sources_local/depth_achieved without
    # grepping report prose — this is the whole point of the structured variant.
    report_path = tmp_path / "r.md"
    report_path.write_text("# Report\n\nbody")
    json_path = tmp_path / "r.json"
    json_path.write_text('{"report": "# Report\\n\\nbody", "sources_web": 4, '
                         '"sources_local": 1, "n_sources": 5, "analysts_used": 2, '
                         '"depth": "quick", "depth_achieved": "quick"}')
    monkeypatch.setattr("council.local.run", lambda *a, **k: report_path)
    out = M._run_research_structured("real question", "quick", 2, "both")
    assert out["error"] is None
    assert out["sources_web"] == 4 and out["sources_local"] == 1
    assert out["analysts_used"] == 2
    assert out["depth_requested"] == "quick" and out["depth_achieved"] == "quick"
    assert "body" in out["report"]


def test_library_add_tool_systemexit_becomes_error_string(monkeypatch):
    # a missing optional extra ([docs]) raises SystemExit (BaseException) deep in add()
    class _Lib:
        def add(self, path):
            raise SystemExit("Reading PDFs needs: pip install 'passiveworkers[docs]'")
    monkeypatch.setattr("council.library.Library", _Lib)
    out = M._library_add_text("/some/file.pdf")
    assert out.startswith("error:") and "passiveworkers[docs]" in out


def test_library_add_tool_happy_path(monkeypatch):
    class _Lib:
        def add(self, path):
            return 7
    monkeypatch.setattr("council.library.Library", _Lib)
    assert M._library_add_text("/docs") == "Indexed 7 chunks from /docs."


# ---------------------------------------------------------------- R26: MCP progress bridge
def test_mcp_progress_bridge_schedules_report_progress_on_loop():
    calls = []

    class FakeCtx:
        async def report_progress(self, progress, total=None, message=None):
            calls.append((progress, total, message))

    async def go():
        loop = asyncio.get_running_loop()
        on_progress = M._make_mcp_progress(FakeCtx(), loop)
        # Call from a DIFFERENT thread, like the real asyncio.to_thread() worker does — calling
        # it directly on the loop's own thread would deadlock: on_progress blocks on
        # fut.result(), and the loop can't run the scheduled coroutine while its own thread
        # is the one blocked waiting for it.
        await asyncio.to_thread(on_progress, "a")
        await asyncio.to_thread(on_progress, "b")

    asyncio.run(go())
    assert calls == [(1.0, None, "a"), (2.0, None, "b")]


def test_mcp_progress_bridge_cancels_on_timeout_instead_of_abandoning(monkeypatch):
    # Review finding: a stalled client's report_progress must be CANCELLED on timeout, not just
    # stopped-waiting-on — an abandoned (uncancelled) send would sit forever in the stdio
    # transport's shared, zero-buffer write channel and could eventually block the real tool
    # response from ever being delivered, not just cost this one call its timeout.
    import asyncio as _asyncio

    class StalledCtx:
        async def report_progress(self, progress, total=None, message=None):
            await _asyncio.sleep(999)   # never returns — simulates a client that stopped reading

    async def go():
        loop = _asyncio.get_running_loop()
        on_progress = M._make_mcp_progress(StalledCtx(), loop, timeout=0.05)
        await _asyncio.to_thread(on_progress, "a")
        # give the loop a beat to actually process the cancellation
        await _asyncio.sleep(0.1)
        pending = [t for t in _asyncio.all_tasks() if not t.done() and t is not _asyncio.current_task()]
        assert pending == [], f"a stalled progress send was left running instead of cancelled: {pending}"

    asyncio.run(go())


def test_mcp_progress_bridge_ctx_none_is_a_noop():
    async def go():
        loop = asyncio.get_running_loop()
        on_progress = M._make_mcp_progress(None, loop)
        on_progress("a")   # must not raise — no ctx means no client ever asked for progress
    asyncio.run(go())


def test_research_tool_forwards_progress_and_return_value_unchanged(monkeypatch):
    def fake_structured(brief, depth, analysts, scope, on_progress=None):
        if on_progress:
            on_progress("stage one")
            on_progress("stage two")
        return {"report": "R", "sources_web": 1, "sources_local": 0, "n_sources": 1,
                "analysts_used": 1, "depth_requested": depth, "depth_achieved": depth,
                "error": None}
    monkeypatch.setattr(M, "_run_research_structured", fake_structured)

    calls = []

    class FakeCtx:
        async def report_progress(self, progress, total=None, message=None):
            calls.append(message)

    result = asyncio.run(M._research_tool("q", "quick", 2, "both", ctx=FakeCtx()))
    assert calls == ["stage one", "stage two"]
    assert result == {"report": "R", "sources_web": 1, "sources_local": 0, "n_sources": 1,
                      "analysts_used": 1, "depth_requested": "quick", "depth_achieved": "quick",
                      "error": None}


def test_build_server_registers_the_three_documented_tools():
    """G9 review: build_server()/tool registration had zero coverage — CI installs the [mcp]
    extra but never actually instantiates the server. Confirms the FastMCP instance exists with
    the exact name Claude Desktop's config expects, and advertises exactly the three tools
    documented in the module docstring/README — no more, no fewer, no typo'd rename."""
    try:
        server = M.build_server()
    except SystemExit:
        pytest.skip("mcp extra not installed")
    assert server.name == "passive-workers"
    tools = {t.name for t in asyncio.run(server.list_tools())}
    assert tools == {"research", "library_search", "library_add"}
