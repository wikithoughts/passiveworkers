#!/usr/bin/env python3
"""
scripts/eval_currency_gap.py — where does live-web research beat a frontier model's memory? (R16/D28)
====================================================================================================
The product's claimed edge (audit D26) is CURRENCY, not raw capability: a council of small local
models researching the LIVE WEB should beat a much larger frontier model answering from its frozen
training knowledge — on time-sensitive questions. This instrument measures exactly that gap, as an
accuracy matrix by **currency window × category**:

    council (local, live web)   vs   a BYOK frontier model (parametric, NO web)

graded blind against a curated REFERENCE answer (ground truth as of the run date).

──────────────────────────────────────────────────────────────────────────────────────────────────
COST & SAFETY: this is the one instrument that spends money — the frontier baseline calls a paid API
(OpenRouter). It therefore does NOTHING paid by default:

    python scripts/eval_currency_gap.py                 # DRY RUN: validate questions, estimate cost, print the command. $0.
    python scripts/eval_currency_gap.py --run           # ACTUALLY runs: council (free) + frontier (PAID) + grade.

The paid path needs OPENROUTER_API_KEY (or PW_BASELINE_API_KEY) in the environment — it is never read
from anywhere else. The grader is LOCAL (free) by default; --grader api grades with the frontier too.
──────────────────────────────────────────────────────────────────────────────────────────────────

GROUND TRUTH is a human-maintained, living input (time-sensitive facts change, and post-cutoff truth
is yours to confirm — your research loop is exactly this). Static questions ship ready; recent/breaking
ones carry a 'VERIFY' placeholder you must fill before --run. The dry run refuses to certify a paid run
until every reference is real.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

RUN_DATE = "2026-06-13"     # the reference answers are "correct as of" this date
MAX_PAID_QUESTIONS = 40     # foot-gun guard: a paid run beyond this needs an explicit --max
_PLACEHOLDER = re.compile(r"\bVERIFY\b|\bTODO\b|<[^>]+>", re.IGNORECASE)

# Curated question set. window ∈ {static, recent, breaking}; each needs a reference (ground truth).
# STATIC entries are confidently answerable and act as a control (the frontier should ace them — a
# fair eval must not punish it where currency is irrelevant). RECENT/BREAKING carry placeholders:
# fill the real current answer before a paid run (your post-cutoff knowledge / research loop).
DEFAULT_QUESTIONS = [
    # ── static control: currency irrelevant, tests the eval is fair to the frontier ──
    {"question": "What is the chemical symbol and atomic number of gold?",
     "window": "static", "category": "science", "reference": "Gold: symbol Au, atomic number 79."},
    {"question": "Who wrote 'Pride and Prejudice' and in what year was it first published?",
     "window": "static", "category": "culture", "reference": "Jane Austen; first published 1813."},
    {"question": "What is the capital city of Australia?",
     "window": "static", "category": "geography", "reference": "Canberra."},
    {"question": "At standard sea-level pressure, what is the boiling point of water in Celsius?",
     "window": "static", "category": "science", "reference": "100 °C (at 1 atm)."},
    # ── recent: changes over months; FILL the reference before --run ──
    {"question": "What is the latest stable Python version, and when was it released?",
     "window": "recent", "category": "tech",
     "reference": "VERIFY before run — confirm the current stable Python version and release date."},
    {"question": "What is the current US federal funds rate target range?",
     "window": "recent", "category": "finance",
     "reference": "VERIFY before run — confirm the current FOMC target range as of the run date."},
    {"question": "Who is the current Secretary-General of the United Nations?",
     "window": "recent", "category": "policy",
     "reference": "VERIFY before run — confirm who currently holds the post."},
    {"question": "What is the most recently released flagship iPhone model and its US starting price?",
     "window": "recent", "category": "tech",
     "reference": "VERIFY before run — confirm the latest model and starting price."},
    # ── breaking: changes within weeks; FILL the reference before --run ──
    {"question": "What is the most recent development in the EU AI Act's enforcement timeline?",
     "window": "breaking", "category": "policy",
     "reference": "VERIFY before run — confirm the latest enforcement milestone and date."},
    {"question": "What was the outcome of the most recent US FOMC meeting?",
     "window": "breaking", "category": "finance",
     "reference": "VERIFY before run — confirm the date and the rate decision."},
]


# ----------------------------------------------------------------------------- pure logic (tested)
def validate_questions(qs: list[dict]) -> tuple[list[dict], list[str]]:
    """Split into (ready, problems). A question is ready only with a non-empty question + window +
    category + a reference that is NOT a placeholder. Problems are human-readable strings."""
    ready, problems = [], []
    for i, q in enumerate(qs):
        missing = [k for k in ("question", "window", "category", "reference")
                   if not str(q.get(k, "")).strip()]
        if missing:
            problems.append(f"#{i}: missing {', '.join(missing)} — {str(q.get('question',''))[:60]!r}")
            continue
        if _PLACEHOLDER.search(str(q["reference"])):
            problems.append(f"#{i}: reference is a placeholder (fill it) — {q['question'][:60]!r}")
            continue
        ready.append(q)
    return ready, problems


def parse_grade(raw: str) -> float | None:
    """Pull a 0-10 score out of a grader response; clamp; None if unparseable."""
    from council.judge import _extract_json
    parsed = _extract_json(raw or "")
    if isinstance(parsed, list) and parsed:
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        return None
    try:
        return max(0.0, min(10.0, float(parsed.get("score"))))
    except (TypeError, ValueError):
        return None


def extract_summary(report_md: str) -> str:
    """The council's answer = the report's Executive summary section (the product's headline output)."""
    m = re.search(r"(?ms)^##\s+Executive summary\s*\n(.*?)(?=\n##\s|\Z)", report_md or "")
    return (m.group(1).strip() if m else (report_md or "").strip())


def build_matrix(results: list[dict]) -> dict:
    """Aggregate per-claim results into accuracy means. results: [{window, category,
    council_score, frontier_score}]. Returns means + the council−frontier gap, by window, by
    category, and overall. Missing scores (a failed/ungraded call) are excluded per-cell."""
    def _agg(rows: list[dict]) -> dict:
        c = [r["council_score"] for r in rows if r.get("council_score") is not None]
        f = [r["frontier_score"] for r in rows if r.get("frontier_score") is not None]
        # gap is PAIRED: averaged only over questions BOTH models answered, so it never
        # compares means taken over different question subsets (an honesty requirement).
        paired = [(r["council_score"], r["frontier_score"]) for r in rows
                  if r.get("council_score") is not None and r.get("frontier_score") is not None]
        cm = (sum(c) / len(c)) if c else None
        fm = (sum(f) / len(f)) if f else None
        gap = (sum(a - b for a, b in paired) / len(paired)) if paired else None
        return {"n": len(rows), "paired_n": len(paired),
                "council": round(cm, 2) if cm is not None else None,
                "frontier": round(fm, 2) if fm is not None else None,
                "gap": round(gap, 2) if gap is not None else None}

    by_window, by_category = {}, {}
    for r in results:
        by_window.setdefault(r["window"], []).append(r)
        by_category.setdefault(r["category"], []).append(r)
    return {"by_window": {k: _agg(v) for k, v in by_window.items()},
            "by_category": {k: _agg(v) for k, v in by_category.items()},
            "overall": _agg(results)}


_WINDOW_ORDER = ["static", "recent", "breaking"]


def render_matrix(matrix: dict) -> str:
    """A readable accuracy matrix (0-10). Positive gap = council WITH live web outscored the
    frontier WITHOUT web. '⚠' marks small samples (paired n < 3) whose gap is noise, not signal."""
    lines = ["", "  Currency-gap — council WITH LIVE WEB  vs  frontier WITHOUT web, graded 0-10 vs reference",
             "  ─────────────────────────────────────────────────────────────────────────────────────────",
             f"  {'window':<12}{'n':>4}{'paired':>8}{'council':>10}{'frontier':>10}{'gap':>9}"]

    def _row(label, a):
        def f(x):
            return "  —  " if x is None else f"{x:.2f}"
        gap = "  —  " if a["gap"] is None else f"{a['gap']:+.2f}"
        flag = " ⚠" if (a.get("paired_n", 0) < 3 and a["gap"] is not None) else ""
        lines.append(f"  {label:<12}{a['n']:>4}{a.get('paired_n', 0):>8}"
                     f"{f(a['council']):>10}{f(a['frontier']):>10}{gap:>9}{flag}")

    bw = matrix["by_window"]
    for w in _WINDOW_ORDER + [k for k in bw if k not in _WINDOW_ORDER]:
        if w in bw:
            _row(w, bw[w])
    _row("OVERALL", matrix["overall"])
    lines.append("")
    lines.append("  by category:")
    for cat, a in sorted(matrix["by_category"].items()):
        g = "  —  " if a["gap"] is None else f"{a['gap']:+.2f}"
        flag = " ⚠" if (a.get("paired_n", 0) < 3 and a["gap"] is not None) else ""
        lines.append(f"    {cat:<14} n={a['n']:<3} paired={a.get('paired_n', 0):<3} gap={g}{flag}")
    lines += [
        "",
        "  HOW TO READ — this measures CURRENCY on a specific question set, with caveats stated plainly:",
        "    • 'gap' is the mean (council − frontier) over the 'paired' questions BOTH answered (a true",
        "      paired comparison); the council/frontier columns are each model's own average (possibly",
        "      over different subsets if a call failed) — read the gap, not the difference of columns.",
        "    • '⚠' = paired n < 3: the gap is noise at that size, not signal. Add questions for a real read.",
        "    • gap > 0 means the council's live-web answer scored higher vs the reference. Expect ~0 on",
        "      'static' (currency irrelevant — the fairness control) and positive on recent/breaking IF",
        "      the moat is real. A negative static gap just shows the frontier's raw capability.",
        "    • Grading is reference-based and blind, but an LLM grader is imperfect AND each reference is",
        "      a single curated 'correct' answer (it may not capture every defensible answer). Results",
        "      are only as good as that curation; a stale/wrong reference silently corrupts the gap.",
        "    • The frontier answers WITHOUT web BY DESIGN — this is not 'the frontier model is weak', it",
        "      measures the value of LIVE GROUNDING. Giving it web would (correctly) erase the gap.",
    ]
    return "\n".join(lines)


def estimate_cost(n_ready: int, grader: str) -> str:
    calls = n_ready * (1 + (2 if grader == "api" else 0))   # 1 frontier answer (+2 grades if api grader)
    lo, hi = calls * 0.002, calls * 0.02
    return (f"{n_ready} questions → ~{calls} paid API call(s) "
            f"(frontier baseline{' + api grading' if grader == 'api' else ''}); "
            f"rough cost ${lo:.2f}–${hi:.2f} depending on the model.")


# ----------------------------------------------------------------------------- I/O paths (--run only)
def _frontier_answer(question: str, model: str, key: str, url: str) -> str:
    import requests
    r = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "max_tokens": 500, "messages": [
                          {"role": "system",
                           "content": ("You are a knowledgeable assistant answering from your own "
                                       "training knowledge. You have NO web access. Be concise and "
                                       "specific (names, numbers, dates). If the question needs "
                                       "information more recent than your training, answer what you "
                                       "know and explicitly flag the uncertainty.")},
                          {"role": "user", "content": question}]}, timeout=120)
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


def _grade(grader, question: str, answer: str, reference: str) -> float | None:
    prompt = (
        "You are grading a candidate answer for factual accuracy and CURRENCY against a REFERENCE "
        f"answer that is correct as of {RUN_DATE}. Judge ONLY against the reference.\n\n"
        f"QUESTION:\n{question}\n\nREFERENCE (ground truth):\n{reference}\n\n"
        f"CANDIDATE ANSWER:\n{answer}\n\n"
        "Score 0-10: 10 = states the reference's key facts correctly and currently; 5 = partially "
        "correct or outdated; 0 = wrong, contradicts the reference, or omits the key fact. "
        'Reply STRICT JSON only: {"score": 0-10, "reason": "..."}')
    try:
        return parse_grade(grader._generate(prompt, num_predict=200))
    except Exception as exc:
        print(f"    (grade failed: {exc})", file=sys.stderr)
        return None


def run_eval(questions: list[dict], *, frontier_model: str, grader_kind: str, grader_model: str,
             depth: str, analysts: int) -> list[dict]:
    from council.judge import Judge
    from council.local import _ApiEditor, run as council_run

    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("PW_BASELINE_API_KEY")
    if not key:
        raise SystemExit("--run needs OPENROUTER_API_KEY (or PW_BASELINE_API_KEY) in the environment.")
    url = os.environ.get("PW_BASELINE_API_URL", "https://openrouter.ai/api/v1/chat/completions")
    grader = (_ApiEditor(grader_model, key, url) if grader_kind == "api" else Judge(model=grader_model))

    results = []
    for q in questions:
        print(f"  · [{q['window']}/{q['category']}] {q['question'][:64]}", file=sys.stderr, flush=True)
        # council (local, live web — FREE)
        try:
            path = council_run(q["question"], depth=depth, n_analysts=analysts, scope="web")
            council_ans = extract_summary(path.read_text())
        except Exception as exc:
            print(f"    (council failed: {exc})", file=sys.stderr)
            council_ans = ""
        # frontier (parametric, no web — PAID)
        try:
            frontier_ans = _frontier_answer(q["question"], frontier_model, key, url)
        except Exception as exc:
            print(f"    (frontier failed: {exc})", file=sys.stderr)
            frontier_ans = ""
        results.append({
            "window": q["window"], "category": q["category"], "question": q["question"],
            "council_score": _grade(grader, q["question"], council_ans, q["reference"]) if council_ans else None,
            "frontier_score": _grade(grader, q["question"], frontier_ans, q["reference"]) if frontier_ans else None,
        })
    return results


# ----------------------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser(
        prog="eval_currency_gap",
        description="Measure the currency gap: council (live web) vs a frontier model (parametric, no "
                    "web) on time-sensitive questions, graded vs curated references. Sample- and "
                    "reference-dependent — a signal, not a general 'who is better' verdict.")
    ap.add_argument("--run", action="store_true", help="actually execute (PAID frontier calls). Default: dry run, $0.")
    ap.add_argument("--questions", help="JSON file: list of {question, window, category, reference}")
    ap.add_argument("--frontier-model", default=os.environ.get("PW_EDITOR_MODEL", "openai/gpt-5-chat"))
    ap.add_argument("--grader", choices=["local", "api"], default="local",
                    help="local = free Ollama grader (default); api = grade with the FRONTIER as judge "
                         "(paid; WARNING: it then grades its OWN baseline answer — conflict of interest)")
    ap.add_argument("--grader-model", default="", help="override grader model (else auto)")
    ap.add_argument("--depth", choices=["quick", "standard", "deep"], default="quick")
    ap.add_argument("--analysts", type=int, default=2, help="council analyst models (1-4)")
    ap.add_argument("--max", type=int, default=0, help="cap questions (bounds cost)")
    ap.add_argument("--json", dest="json_out", help="write raw results to this path")
    a = ap.parse_args()

    questions = DEFAULT_QUESTIONS
    if a.questions:
        questions = json.loads(pathlib.Path(a.questions).expanduser().read_text())
    if a.max > 0:
        questions = questions[:a.max]
    ready, problems = validate_questions(questions)

    if not a.run:
        print(f"DRY RUN — nothing paid. ({len(ready)} ready, {len(problems)} need attention)\n")
        if problems:
            print("  Fill these references before a paid run:")
            for p in problems:
                print(f"    ⚠ {p}")
            print()
        print("  Ready questions:")
        for q in ready:
            print(f"    [{q['window']}/{q['category']}] {q['question']}")
        print(f"\n  Frontier baseline model: {a.frontier_model}  (set --frontier-model / PW_EDITOR_MODEL)")
        print(f"  Cost if you run now: {estimate_cost(len(ready), a.grader)}")
        print(f"\n  To execute (spends API credit): "
              f"OPENROUTER_API_KEY=... python {pathlib.Path(__file__).name} --run"
              + (f" --max {a.max}" if a.max else ""))
        print("  (grading is local/free by default; --grader api grades with the frontier too — "
              "but then it grades its own answer, a conflict of interest)")
        print("  Reminder: references are human ground truth; a stale/wrong reference silently "
              "corrupts the result — verify them, especially recent/breaking.")
        return 0

    if not ready:
        raise SystemExit("No ready questions — every reference is a placeholder. Fill them first (dry run lists them).")
    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("PW_BASELINE_API_KEY")):
        raise SystemExit("--run needs OPENROUTER_API_KEY (or PW_BASELINE_API_KEY) in the environment. "
                         "(Run without --run for a free dry run.)")
    if a.max == 0 and len(ready) > MAX_PAID_QUESTIONS:
        raise SystemExit(f"{len(ready)} ready questions exceeds the {MAX_PAID_QUESTIONS}-question safety "
                         f"ceiling for a paid run. Pass --max N to confirm the spend (cost ≈ N paid calls).")
    if problems:
        print(f"  NOTE: SKIPPING {len(problems)} question(s) with placeholder/missing references "
              "(they will NOT be graded):", file=sys.stderr)
        for p in problems:
            print(f"    ⚠ {p}", file=sys.stderr)
    grader_model = a.grader_model or (a.frontier_model if a.grader == "api"
                                      else os.environ.get("PW_GRADER_MODEL", "qwen2.5:14b"))
    if a.grader == "api" and grader_model == a.frontier_model:
        print("  ⚠ CONFLICT OF INTEREST: the frontier model is grading its OWN baseline answer "
              "(--grader api, same model). Use --grader local or --grader-model for an independent "
              "judge.", file=sys.stderr)
    print(f"RUNNING (paid) — {len(ready)} questions · frontier={a.frontier_model} · "
          f"grader={a.grader}:{grader_model}", file=sys.stderr)
    results = run_eval(ready, frontier_model=a.frontier_model, grader_kind=a.grader,
                       grader_model=grader_model, depth=a.depth, analysts=max(1, min(4, a.analysts)))
    print(render_matrix(build_matrix(results)))
    if a.json_out:
        pathlib.Path(a.json_out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"\n  raw results → {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
