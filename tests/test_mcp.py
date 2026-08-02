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
