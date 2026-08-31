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

RUN_DATE = "2026-07-10"     # the reference answers are "correct as of" this date
MAX_PAID_QUESTIONS = 40     # foot-gun guard: a paid run beyond this needs an explicit --max
_PLACEHOLDER = re.compile(r"\bVERIFY\b|\bTODO\b|<[^>]+>", re.IGNORECASE)

# Curated question set. window ∈ {static, recent, breaking}; each needs a reference (ground truth).
# STATIC entries are confidently answerable and act as a control (a memory-only baseline should ace
# them — a fair eval must not punish it where currency is irrelevant). RECENT/BREAKING references were
# curated from the LIVE WEB on 2026-07-10 with the source noted inline; re-verify before re-running,
# they go stale. The set is deliberately deeper on the two moving windows (7 recent + 5 breaking) so
# the paired sample clears the 'paired n < 3 = noise' floor render_matrix flags. (R35: expanded from
# the original 4/4/2 set, and re-verified — Fed held again on 2026-06-17, iPhone/macOS/kernel moved.)
DEFAULT_QUESTIONS = [
    # ── static control: currency irrelevant, tests the eval is fair to a memory-only baseline ──
    {"question": "What is the chemical symbol and atomic number of gold?",
     "window": "static", "category": "science", "reference": "Gold: symbol Au, atomic number 79."},
    {"question": "Who wrote 'Pride and Prejudice' and in what year was it first published?",
     "window": "static", "category": "culture", "reference": "Jane Austen; first published 1813."},
    {"question": "What is the capital city of Australia?",
     "window": "static", "category": "geography", "reference": "Canberra."},
    {"question": "At standard sea-level pressure, what is the boiling point of water in Celsius?",
     "window": "static", "category": "science", "reference": "100 °C (at 1 atm)."},
    # ── recent: changes over months; references curated from the LIVE WEB on 2026-07-10 ──
    {"question": "What is the latest stable Python version, and when was it released?",
     "window": "recent", "category": "tech",
     "reference": "Python 3.14 is the current stable series (3.14.0 released October 2025); the latest "
                  "patch as of 2026-07-10 is 3.14.6, released 2026-06-10 (a later 3.14.x patch is "
                  "equally acceptable). (python.org, as of 2026-07-10)"},
    {"question": "What is the current US federal funds rate target range?",
     "window": "recent", "category": "finance",
     "reference": "3.50%–3.75%, held at the June 17, 2026 FOMC meeting — the fourth consecutive hold. "
                  "(federalreserve.gov / FRED, as of 2026-07-10)"},
    {"question": "Who is the current Secretary-General of the United Nations?",
     "window": "recent", "category": "policy",
     "reference": "António Guterres of Portugal (in office since 2017-01-01); his second term ends "
                  "2026-12-31, and the selection of his successor (to take office January 2027) is "
                  "underway. (un.org, as of 2026-07-10)"},
    {"question": "What is the most recently released iPhone model and its US starting price?",
     "window": "recent", "category": "tech",
     "reference": "The most recently released model is the iPhone 17e at $599 (256GB), announced "
                  "2026-03-02; the current flagship iPhone 17 starts at $799 (256GB). The iPhone 18 "
                  "line is expected September 2026. (apple.com / MacRumors, as of 2026-07-10)"},
    {"question": "Which Node.js version is the current Active LTS release?",
     "window": "recent", "category": "tech",
     "reference": "Node.js 24 is the Active LTS line; Node.js 22 is in Maintenance LTS; Node.js 26 is "
                  "the 'Current' (non-LTS) line, scheduled to enter LTS in October 2026. "
                  "(nodejs.org, as of 2026-07-10)"},
    {"question": "What is the current major version of macOS, and its name?",
     "window": "recent", "category": "tech",
     "reference": "macOS 26 'Tahoe' is the current release (launched 2025-09-15; latest point release "
                  "26.5.2 on 2026-06-29). macOS 27 'Golden Gate' is expected September 2026. "
                  "(apple.com / Macworld, as of 2026-07-10)"},
    {"question": "What was NVIDIA's most recently reported quarterly revenue?",
     "window": "recent", "category": "finance",
     "reference": "A record $81.6 billion for Q1 of fiscal 2027 (quarter ended 2026-04-26), up 85% year "
                  "over year, reported 2026-05-20; Data Center revenue was $75.2 billion. "
                  "(NVIDIA SEC 8-K / CNBC, as of 2026-07-10)"},
    # ── breaking: changes within weeks; references curated from the LIVE WEB on 2026-07-10 ──
    {"question": "What is the next major enforcement milestone in the EU AI Act timeline, and its date?",
     "window": "breaking", "category": "policy",
     "reference": "2026-08-02: the European Commission's enforcement powers over general-purpose AI "
                  "(GPAI) providers become applicable — it can then impose fines (up to €15 million or "
                  "3% of global annual turnover) for GPAI non-compliance (the GPAI obligations "
                  "themselves have applied since 2025-08-02). "
                  "(artificialintelligenceact.eu / ec.europa.eu, as of 2026-07-10)"},
    {"question": "What was the outcome of the most recent completed US FOMC meeting?",
     "window": "breaking", "category": "finance",
     "reference": "The most recent completed meeting was June 17, 2026; it held the federal funds target "
                  "range at 3.50%–3.75% (a fourth consecutive hold). (federalreserve.gov, as of 2026-07-10)"},
    {"question": "When is the next scheduled US FOMC monetary-policy meeting?",
     "window": "breaking", "category": "finance",
     "reference": "July 28–29, 2026, with the interest-rate decision announced on 2026-07-29. "
                  "(federalreserve.gov, as of 2026-07-10)"},
    {"question": "What was the most recent completed SpaceX Starship test flight, and its outcome?",
     "window": "breaking", "category": "tech",
     "reference": "Flight 12, launched 2026-05-22 — the first test of Starship Version 3; SpaceX called "
                  "the suborbital test a success, though the Super Heavy booster missed its planned soft "
                  "ocean splashdown (Flight 13 was scheduled for 2026-07-15). "
                  "(SpaceX / Wikipedia 'List of Starship launches', as of 2026-07-10)"},
    {"question": "What is the latest stable Linux kernel point release?",
     "window": "breaking", "category": "tech",
     "reference": "7.1.3, released 2026-07-04 (the 7.1 stable series opened 2026-06-14). Patch releases "
                  "are frequent, so a later 7.1.x is equally acceptable. (kernel.org, as of 2026-07-10)"},
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
    from passiveworkers.judge import _extract_json
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
    """Aggregate per-question results into accuracy means. results: [{window, category,
    council_score, baseline_score}]. Returns means + the council−baseline gap, by window, by
    category, and overall. Missing scores (a failed/ungraded call) are excluded per-cell."""
    def _agg(rows: list[dict]) -> dict:
        c = [r["council_score"] for r in rows if r.get("council_score") is not None]
        f = [r["baseline_score"] for r in rows if r.get("baseline_score") is not None]
        # gap is PAIRED: averaged only over questions BOTH models answered, so it never
        # compares means taken over different question subsets (an honesty requirement).
        paired = [(r["council_score"], r["baseline_score"]) for r in rows
                  if r.get("council_score") is not None and r.get("baseline_score") is not None]
        cm = (sum(c) / len(c)) if c else None
        fm = (sum(f) / len(f)) if f else None
        gap = (sum(a - b for a, b in paired) / len(paired)) if paired else None
        return {"n": len(rows), "paired_n": len(paired),
                "council": round(cm, 2) if cm is not None else None,
                "baseline": round(fm, 2) if fm is not None else None,
                "gap": round(gap, 2) if gap is not None else None}

    by_window, by_category = {}, {}
    for r in results:
        by_window.setdefault(r["window"], []).append(r)
        by_category.setdefault(r["category"], []).append(r)
    return {"by_window": {k: _agg(v) for k, v in by_window.items()},
            "by_category": {k: _agg(v) for k, v in by_category.items()},
            "overall": _agg(results)}


_WINDOW_ORDER = ["static", "recent", "breaking"]


def render_matrix(matrix: dict, baseline_label: str = "frontier (memory)") -> str:
    """A readable accuracy matrix (0-10). Positive gap = council WITH live web outscored the
    baseline WITHOUT web. '⚠' marks small samples (paired n < 3) whose gap is noise, not signal.
    `baseline_label` names which baseline the 'baseline' column is (frontier vs local, from memory)."""
    lines = ["", "  Currency-gap — council WITH LIVE WEB  vs  baseline WITHOUT web, graded 0-10 vs reference",
             f"  (council = local models + live web;  baseline = {baseline_label})",
             "  ─────────────────────────────────────────────────────────────────────────────────────────",
             f"  {'window':<12}{'n':>4}{'paired':>8}{'council':>10}{'baseline':>10}{'gap':>9}"]

    def _row(label, a):
        def f(x):
            return "  —  " if x is None else f"{x:.2f}"
        gap = "  —  " if a["gap"] is None else f"{a['gap']:+.2f}"
        flag = " ⚠" if (a.get("paired_n", 0) < 3 and a["gap"] is not None) else ""
        lines.append(f"  {label:<12}{a['n']:>4}{a.get('paired_n', 0):>8}"
                     f"{f(a['council']):>10}{f(a['baseline']):>10}{gap:>9}{flag}")

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
        "    • 'gap' is the mean (council − baseline) over the 'paired' questions BOTH answered (a true",
        "      paired comparison); the council/baseline columns are each model's own average (possibly",
        "      over different subsets if a call failed) — read the gap, not the difference of columns.",
        "    • '⚠' = paired n < 3: the gap is noise at that size, not signal. Add questions for a real read.",
        "    • gap > 0 means the council's live-web answer scored higher vs the reference. Expect ~0 on",
        "      'static' (currency irrelevant — the fairness control) and positive on recent/breaking IF",
        "      the moat is real. A negative static gap just shows the baseline's raw capability.",
        "    • Grading is reference-based and blind, but an LLM grader is imperfect AND each reference is",
        "      a single curated 'correct' answer (it may not capture every defensible answer). Results",
        "      are only as good as that curation; a stale/wrong reference silently corrupts the gap.",
        f"    • The baseline ({baseline_label}) answers WITHOUT web BY DESIGN — this is not 'the baseline",
        "      is weak', it measures the value of LIVE GROUNDING. Giving it web would (correctly) erase",
        "      the gap. The LOCAL baseline is the fairest read: council vs the SAME model without web.",
    ]
    return "\n".join(lines)


def estimate_cost(n_ready: int, grader: str, baseline: str = "frontier") -> str:
    # paid calls = 1 frontier answer/question (only if baseline=frontier) + 2 grades/question (only if
    # grader=api). A local baseline + local grader spends nothing — it runs entirely on your Ollama.
    per_q = (1 if baseline == "frontier" else 0) + (2 if grader == "api" else 0)
    calls = n_ready * per_q
    if calls == 0:
        return (f"{n_ready} questions → $0 paid (local baseline + local grader — all on your own "
                f"Ollama). Not free of TIME: ~{n_ready} council research runs, minutes each.")
    paid_parts = ([f"{'frontier baseline'}"] if baseline == "frontier" else []) + \
                 (["api grading"] if grader == "api" else [])
    lo, hi = calls * 0.002, calls * 0.02
    return (f"{n_ready} questions → ~{calls} paid API call(s) ({' + '.join(paid_parts)}); "
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


def _local_memory_answer(question: str, model: str, ollama_base: str) -> str:
    """The FREE 'frozen knowledge' baseline: the SAME class of local model the council uses, answering
    from parametric memory with NO web and NO retrieval. Unlike the frontier baseline (which also
    differs in scale), this isolates the value of LIVE GROUNDING on a single model — and it needs no
    API key and spends nothing. It is the apples-to-apples counterpart to the product's actual claim:
    local models WITH live web beat the SAME local models answering from their own memory."""
    from passiveworkers import ollama as _ollama
    prompt = ("Answer from your own training knowledge only. You have NO web access. Be concise and "
              "specific (names, numbers, dates). If the answer may have changed after your training, "
              "answer what you know and explicitly flag the uncertainty.\n\n"
              f"QUESTION: {question}\n\nANSWER:")
    out, _ = _ollama.generate(prompt, model=model, base_url=ollama_base, temperature=0.0,
                              num_predict=400, timeout_env="PW_OLLAMA_TIMEOUT", timeout_default=300)
    return out


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


def run_eval(questions: list[dict], *, baseline_kind: str, baseline_model: str, frontier_model: str,
             grader_kind: str, grader_model: str, depth: str, analysts: int) -> list[dict]:
    from passiveworkers.judge import Judge
    from passiveworkers.local import _ApiEditor, run as council_run

    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("PW_BASELINE_API_KEY")
    url = os.environ.get("PW_BASELINE_API_URL", "https://openrouter.ai/api/v1/chat/completions")
    # A key is needed only for a PAID resource — the frontier baseline and/or the api grader. A fully
    # local run (baseline local + grader local) needs none.
    if (baseline_kind == "frontier" or grader_kind == "api") and not key:
        raise SystemExit("--run with --baseline frontier / --grader api needs OPENROUTER_API_KEY "
                         "(or PW_BASELINE_API_KEY). For a free run: --baseline local --grader local.")
    grader = (_ApiEditor(grader_model, key, url) if grader_kind == "api" else Judge(model=grader_model))

    results = []
    for q in questions:
        print(f"  · [{q['window']}/{q['category']}] {q['question'][:64]}", file=sys.stderr, flush=True)
        # council (local, live web — always FREE)
        try:
            path = council_run(q["question"], depth=depth, n_analysts=analysts, scope="web")
            council_ans = extract_summary(path.read_text())
        except Exception as exc:
            print(f"    (council failed: {exc})", file=sys.stderr)
            council_ans = ""
        # baseline (parametric, NO web): a paid frontier model, or the FREE local model from memory
        try:
            if baseline_kind == "local":
                from passiveworkers import ollama as _ollama
                baseline_ans = _local_memory_answer(q["question"], baseline_model, _ollama.base())
            else:
                baseline_ans = _frontier_answer(q["question"], frontier_model, key, url)
        except Exception as exc:
            print(f"    (baseline failed: {exc})", file=sys.stderr)
            baseline_ans = ""
        results.append({
            "window": q["window"], "category": q["category"], "question": q["question"],
            "council_score": _grade(grader, q["question"], council_ans, q["reference"]) if council_ans else None,
            "baseline_score": _grade(grader, q["question"], baseline_ans, q["reference"]) if baseline_ans else None,
        })
    return results


# ----------------------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser(
        prog="eval_currency_gap",
        description="Measure the currency gap: council (live web) vs a baseline WITHOUT web (parametric) "
                    "on time-sensitive questions, graded vs curated references. The baseline is a paid "
                    "frontier model OR — with --baseline local — the SAME local models from memory, free. "
                    "Sample- and reference-dependent — a signal, not a general 'who is better' verdict.")
    ap.add_argument("--run", action="store_true", help="actually execute. Default: dry run, $0.")
    ap.add_argument("--baseline", choices=["frontier", "local"], default="frontier",
                    help="frontier = a paid API model answering from memory (default); local = the largest "
                         "LOCAL model from memory (FREE, no API key) — isolates web-vs-no-web on one model, "
                         "the apples-to-apples read for the product's actual claim.")
    ap.add_argument("--baseline-model", default="", help="override the baseline model (else auto: the "
                                                         "largest installed model for --baseline local)")
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
        print(f"DRY RUN — nothing executed. ({len(ready)} ready, {len(problems)} need attention)\n")
        if problems:
            print("  Fill these references before running:")
            for p in problems:
                print(f"    ⚠ {p}")
            print()
        print("  Ready questions:")
        for q in ready:
            print(f"    [{q['window']}/{q['category']}] {q['question']}")
        script = pathlib.Path(__file__).name
        if a.baseline == "local":
            maxarg = f" --max {a.max}" if a.max else ""
            print("\n  Baseline: --baseline local (the largest installed model from memory"
                  + (f", forced {a.baseline_model}" if a.baseline_model else "") + ")")
            print(f"  Cost if you run now: {estimate_cost(len(ready), a.grader, a.baseline)}")
            if a.grader == "api":
                # --grader api still spends + needs a key even with a free local baseline — don't
                # advertise "FREE, no API key" for it (review R35). Offer the fully-free path too.
                print(f"\n  To execute (--grader api spends + needs a key): "
                      f"OPENROUTER_API_KEY=... python {script} --run --baseline local --grader api{maxarg}")
                print(f"  For a fully FREE run: python {script} --run --baseline local --grader local{maxarg}")
            else:
                print(f"\n  To execute (FREE, no API key): python {script} --run --baseline local{maxarg}")
        else:
            print(f"\n  Frontier baseline model: {a.frontier_model}  (set --frontier-model / PW_EDITOR_MODEL)")
            print(f"  Cost if you run now: {estimate_cost(len(ready), a.grader, a.baseline)}")
            print(f"\n  To execute (spends API credit): "
                  f"OPENROUTER_API_KEY=... python {script} --run"
                  + (f" --max {a.max}" if a.max else ""))
            print(f"  For a FREE run instead: python {script} --run --baseline local")
        print("  (grading is local/free by default; --grader api grades with the frontier too — "
              "but then it grades its own answer, a conflict of interest)")
        print("  Reminder: references are human ground truth; a stale/wrong reference silently "
              "corrupts the result — verify them, especially recent/breaking.")
        return 0

    if not ready:
        raise SystemExit("No ready questions — every reference is a placeholder. Fill them first (dry run lists them).")
    # A key and the paid-question ceiling apply only when a PAID resource is used (frontier baseline
    # and/or api grader). A fully local run (--baseline local --grader local) spends nothing.
    spends = a.baseline == "frontier" or a.grader == "api"
    if spends and not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("PW_BASELINE_API_KEY")):
        raise SystemExit("--run with --baseline frontier / --grader api needs OPENROUTER_API_KEY "
                         "(or PW_BASELINE_API_KEY). For a free run: --run --baseline local --grader local. "
                         "(Run without --run for a dry run.)")
    if spends and a.max == 0 and len(ready) > MAX_PAID_QUESTIONS:
        raise SystemExit(f"{len(ready)} ready questions exceeds the {MAX_PAID_QUESTIONS}-question safety "
                         f"ceiling for a paid run. Pass --max N to confirm the spend (cost ≈ N paid calls).")
    if problems:
        print(f"  NOTE: SKIPPING {len(problems)} question(s) with placeholder/missing references "
              "(they will NOT be graded):", file=sys.stderr)
        for p in problems:
            print(f"    ⚠ {p}", file=sys.stderr)
    grader_model = a.grader_model or (a.frontier_model if a.grader == "api"
                                      else os.environ.get("PW_GRADER_MODEL", "qwen2.5:14b"))
    if a.baseline == "frontier" and a.grader == "api" and grader_model == a.frontier_model:
        # Only a FRONTIER baseline is graded by the frontier itself — a local baseline answer comes
        # from Ollama, so the api grader never judges its own output (review R35).
        print("  ⚠ CONFLICT OF INTEREST: the frontier model is grading its OWN baseline answer "
              "(--grader api, same model). Use --grader local or --grader-model for an independent "
              "judge.", file=sys.stderr)
    # Resolve the local baseline model (the largest installed = the fair same-family baseline).
    baseline_model = a.baseline_model
    if a.baseline == "local" and not baseline_model:
        from passiveworkers.local import detect_models, pick_cast
        _cast, baseline_model = pick_cast(detect_models())
    baseline_label = f"{a.baseline} (memory)" if a.baseline == "frontier" else f"local (memory): {baseline_model}"
    tag = f"baseline={a.baseline}" + (f":{baseline_model}" if a.baseline == "local" else f":{a.frontier_model}")
    print(f"RUNNING{' (paid)' if spends else ' (FREE, local)'} — {len(ready)} questions · {tag} · "
          f"grader={a.grader}:{grader_model}", file=sys.stderr)
    results = run_eval(ready, baseline_kind=a.baseline, baseline_model=baseline_model,
                       frontier_model=a.frontier_model, grader_kind=a.grader,
                       grader_model=grader_model, depth=a.depth, analysts=max(1, min(4, a.analysts)))
    print(render_matrix(build_matrix(results), baseline_label=baseline_label))
    if a.json_out:
        pathlib.Path(a.json_out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"\n  raw results → {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
