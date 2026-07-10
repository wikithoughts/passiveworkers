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


def test_default_questions_all_ready_with_real_references():
    ready, problems = CG.validate_questions(CG.DEFAULT_QUESTIONS)
    # references were curated from the live web (2026-06-13) — the shipped set is fully runnable,
    # no placeholders remain, and it spans all three currency windows for a meaningful matrix
    assert not problems and len(ready) == len(CG.DEFAULT_QUESTIONS)
    assert {q["window"] for q in ready} == {"static", "recent", "breaking"}


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
        {"window": "static", "category": "sci", "council_score": 8, "baseline_score": 9},
        {"window": "static", "category": "sci", "council_score": 6, "baseline_score": 9},
        {"window": "breaking", "category": "fin", "council_score": 9, "baseline_score": 2},
    ]
    m = CG.build_matrix(results)
    assert m["by_window"]["static"]["n"] == 2
    assert m["by_window"]["static"]["council"] == 7.0 and m["by_window"]["static"]["baseline"] == 9.0
    assert m["by_window"]["static"]["gap"] == -2.0          # baseline wins where currency is irrelevant
    assert m["by_window"]["breaking"]["gap"] == 7.0         # council wins on breaking (the moat)
    assert m["overall"]["n"] == 3
    assert set(m["by_category"]) == {"sci", "fin"}


def test_build_matrix_excludes_missing_scores_per_cell():
    results = [
        {"window": "recent", "category": "tech", "council_score": 7, "baseline_score": None},
        {"window": "recent", "category": "tech", "council_score": None, "baseline_score": 5},
    ]
    m = CG.build_matrix(results)["by_window"]["recent"]
    assert m["council"] == 7.0 and m["baseline"] == 5.0 and m["gap"] is None   # no paired cell → no gap
    assert m["n"] == 2


def test_render_matrix_smoke():
    matrix = CG.build_matrix([
        {"window": "breaking", "category": "fin", "council_score": 9, "baseline_score": 2}])
    out = CG.render_matrix(matrix)
    assert "Currency-gap" in out and "breaking" in out and "OVERALL" in out
    assert "baseline" in out and "frontier (memory)" in out           # default label
    # the local-baseline label flows through to the header caption + footnote
    local_out = CG.render_matrix(matrix, baseline_label="local (memory): qwen3:14b")
    assert "local (memory): qwen3:14b" in local_out


# ----------------------------------------------------------------------------- estimate_cost
def test_estimate_cost_scales_with_grader_and_baseline():
    # frontier baseline (default): 1 baseline call/q (+2 grades if api grader)
    assert "10 paid API call" in CG.estimate_cost(10, "local")          # 10 frontier calls
    assert "30 paid API call" in CG.estimate_cost(10, "api")            # +2 grades/q
    # local baseline + local grader spends nothing
    free = CG.estimate_cost(10, "local", "local")
    assert "$0 paid" in free and "own" in free
    # local baseline + api grader still pays for the 2 grades/question, but not for the baseline
    assert "20 paid API call" in CG.estimate_cost(10, "api", "local")
