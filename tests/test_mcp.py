"""MCP trust boundary: arg clamping (_normalize_research_args) and the no-traceback contract for
the research()/library_add() tool bodies — an MCP client must always get a clean string, never an
exception (incl. SystemExit, which is BaseException and slips past `except Exception`)."""
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
def test_research_tool_systemexit_becomes_error_string(monkeypatch):
    # run() raises SystemExit on the #1 first-run failure (Ollama down) — must come back as a string
    def boom(*a, **k):
        raise SystemExit("Can't reach Ollama at http://x. Start it with `ollama serve`.")
    monkeypatch.setattr("council.local.run", boom)
    out = M._run_research_text("real question", "quick", 2, "both")
    assert out.startswith("error:") and "ollama serve" in out.lower()


def test_research_tool_generic_exception_becomes_error_string(monkeypatch):
    monkeypatch.setattr("council.local.run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    out = M._run_research_text("real question", "quick", 2, "both")
    assert out.startswith("error:") and "RuntimeError" in out and "disk full" in out


def test_research_tool_empty_brief_short_circuits(monkeypatch):
    # bad input never reaches run() — clean error, no exception
    monkeypatch.setattr("council.local.run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert M._run_research_text("", "quick", 2, "both").startswith("error:")


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
