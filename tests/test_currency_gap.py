"""R16/D28 currency-gap eval — the PURE logic (question validation, grade parsing, summary
extraction, matrix aggregation, cost estimate). No network, no Ollama, no paid calls.
The script is importlib-loaded because scripts/ is not a package."""
import importlib.util as _u
import pathlib as _pl

_spec = _u.spec_from_file_location(
    "cg_runner", _pl.Path(__file__).resolve().parents[1] / "scripts" / "eval_currency_gap.py")
CG = _u.module_from_spec(_spec)
_spec.loader.exec_module(CG)


# ----------------------------------------------------------------------------- validate_questions
def test_validate_splits_ready_from_placeholders_and_missing():
    qs = [
        {"question": "Q1", "window": "static", "category": "sci", "reference": "A real answer."},
        {"question": "Q2", "window": "recent", "category": "tech", "reference": "VERIFY before run — fill it"},
        {"question": "Q3", "window": "breaking", "category": "fin", "reference": "see <source>"},
        {"question": "Q4", "window": "recent", "category": "x", "reference": ""},          # missing ref
        {"question": "", "window": "static", "category": "x", "reference": "ok"},           # missing question
    ]
    ready, problems = CG.validate_questions(qs)
    assert [q["question"] for q in ready] == ["Q1"]
    assert len(problems) == 4
    assert any("placeholder" in p for p in problems)       # VERIFY and <source> both flagged
    assert any("missing" in p for p in problems)


def test_default_questions_static_ready_recent_are_placeholders():
    ready, problems = CG.validate_questions(CG.DEFAULT_QUESTIONS)
    # the static control set ships ready; the recent/breaking set needs references filled
    assert ready and all(q["window"] == "static" for q in ready)
    assert all(p for p in problems)                        # every non-static default needs attention
    assert len(problems) >= 4


# ----------------------------------------------------------------------------- parse_grade
def test_parse_grade_extracts_and_clamps():
    assert CG.parse_grade('{"score": 8, "reason": "good"}') == 8.0
    assert CG.parse_grade('garbage {"score": 99} trailing') == 10.0    # clamped to 10
    assert CG.parse_grade('{"score": -3}') == 0.0                       # clamped to 0
    assert CG.parse_grade('[{"score": 6.5}]') == 6.5                    # bare-list tolerated
    assert CG.parse_grade("no json at all") is None
    assert CG.parse_grade('{"reason":"x"}') is None                     # no score key


# ----------------------------------------------------------------------------- extract_summary
def test_extract_summary_pulls_the_executive_section():
    report = ("# Research report\n**Brief:** Q\n\n_byline_\n\n"
              "## Executive summary\n\nThe key finding is X happening in 2026.\n\n"
              "## Where the analysts agree — and differ\n\nstuff\n")
    s = CG.extract_summary(report)
    assert "key finding is X" in s
    assert "Where the analysts" not in s and "Research report" not in s


def test_extract_summary_falls_back_to_whole_text():
    assert CG.extract_summary("no headings here, just text") == "no headings here, just text"


# ----------------------------------------------------------------------------- build_matrix
def test_build_matrix_aggregates_by_window_and_gap():
    results = [
        {"window": "static", "category": "sci", "council_score": 8, "frontier_score": 9},
        {"window": "static", "category": "sci", "council_score": 6, "frontier_score": 9},
        {"window": "breaking", "category": "fin", "council_score": 9, "frontier_score": 2},
    ]
    m = CG.build_matrix(results)
    assert m["by_window"]["static"]["n"] == 2
    assert m["by_window"]["static"]["council"] == 7.0 and m["by_window"]["static"]["frontier"] == 9.0
    assert m["by_window"]["static"]["gap"] == -2.0          # frontier wins where currency is irrelevant
    assert m["by_window"]["breaking"]["gap"] == 7.0         # council wins on breaking (the moat)
    assert m["overall"]["n"] == 3
    assert set(m["by_category"]) == {"sci", "fin"}


def test_build_matrix_excludes_missing_scores_per_cell():
    results = [
        {"window": "recent", "category": "tech", "council_score": 7, "frontier_score": None},
        {"window": "recent", "category": "tech", "council_score": None, "frontier_score": 5},
    ]
    m = CG.build_matrix(results)["by_window"]["recent"]
    assert m["council"] == 7.0 and m["frontier"] == 5.0 and m["gap"] is None   # no paired cell → no gap
    assert m["n"] == 2


def test_render_matrix_smoke():
    out = CG.render_matrix(CG.build_matrix([
        {"window": "breaking", "category": "fin", "council_score": 9, "frontier_score": 2}]))
    assert "Currency-gap" in out and "breaking" in out and "OVERALL" in out


# ----------------------------------------------------------------------------- estimate_cost
def test_estimate_cost_scales_with_grader():
    local = CG.estimate_cost(10, "local")
    api = CG.estimate_cost(10, "api")
    assert "10 paid API call" in local           # local grader → only frontier baseline calls
    assert "30 paid API call" in api             # api grader → +2 grades per question
