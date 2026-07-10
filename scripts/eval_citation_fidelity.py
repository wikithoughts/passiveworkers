#!/usr/bin/env python3
"""
scripts/eval_citation_fidelity.py — does each cited claim actually appear in its source? (R15/D27)
=================================================================================================
The product's whole promise is *grounded* research: every claim cites a real source. This
instrument measures whether that holds, by lexically checking each [S#]/[L#] claim against
the text of the source it points at (council/fidelity.py — a grounding *floor*, see its
docstring for what it can and cannot prove). It is the honest counterpart to the benchmarks:
not "did we get the trivia right" but "can a reader trust the citations".

Two modes — both keyless (NO API cost):

  # Mode A — score a report you already have, by RE-FETCHING its cited sources (needs network):
  python scripts/eval_citation_fidelity.py --report reports/2026-06-13-....md

  # Mode B — fresh run, scored against the EXACT extract each model saw (needs Ollama, no
  #          network re-fetch, reproducible — the truest measure of the engine's faithfulness):
  python scripts/eval_citation_fidelity.py --run --analysts 1 --depth quick

Options: --questions FILE.json (Mode B brief set) · --model NAME · --grounded/-weak thresholds
· --json OUT.json (dump raw per-claim results) · --top N (worst offenders to print).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from council.fidelity import classify, parse_cited_claims, parse_source_map, score_claim, split_sections

# brief set for Mode B: each has checkable numbers/dates/names so numeric grounding is exercised
DEFAULT_QUESTIONS = [
    "What is the latest stable version of Python and when was it released?",
    "What are the headline obligations of the EU AI Act and when do they take effect?",
    "What is the current US federal funds rate target range and when was it last changed?",
    "What is the most recent flagship iPhone model and its starting price in USD?",
    "What was NVIDIA's most recent reported quarterly revenue?",
    "Who won the most recent men's FIFA World Cup, in what year, and the final score?",
]


# ----------------------------------------------------------------------------- Mode A: report file
def _read_local(path: str, cap: int = 300_000) -> str:
    """Read a local [L#] source the report points at — the operator's own file, on their own
    machine. Bounded + decode-guarded + symlink-refusing; never executed, only read as text.
    A report is attacker-influenceable data, so a planted '[L1] x — /tmp/link→/etc/secret'
    must not be followed (review: SYMLINK-FOLLOW-TOCTOU)."""
    try:
        p = pathlib.Path(path).expanduser()
        if p.is_symlink():                       # never follow a symlink named by a report
            return ""
        if not p.is_file() or p.stat().st_size > cap:
            return ""
        return p.read_text(errors="ignore")
    except Exception:
        return ""


MAX_REPORT_URLS = 200      # cap re-fetches: a malicious report can't fan out 1000s of GETs
MAX_RESULTS = 20_000       # defensive ceiling on accumulated per-claim results


def score_report(report_text: str, *, grounded: float, weak: float) -> list[dict]:
    """Score every cited claim in a saved report by re-fetching its sources. Citations are
    per-contribution scoped, so each '### …' section is resolved against its OWN source list."""
    from council.research import fetch_extract   # imported lazily: needs network, not for tests

    sections = split_sections(report_text) or [report_text]
    web_cache: dict[str, str] = {}
    capped = False
    results: list[dict] = []
    for sec in sections:
        if len(results) >= MAX_RESULTS:
            print(f"  (stopping at {MAX_RESULTS} claims — report exceeds the cap)", file=sys.stderr)
            break
        src_urls = parse_source_map(sec)               # marker -> url / path
        src_text: dict[str, str] = {}
        for marker, loc in src_urls.items():
            if marker.startswith("L"):
                src_text[marker] = _read_local(loc)
                continue
            if loc not in web_cache:
                if len(web_cache) >= MAX_REPORT_URLS:  # don't let a report fan out unbounded GETs
                    if not capped:
                        print(f"  (URL cap {MAX_REPORT_URLS} reached — remaining sources "
                              "left unverifiable)", file=sys.stderr)
                        capped = True
                    src_text[marker] = ""
                    continue
                try:
                    txt, _ = fetch_extract(loc, max_chars=4000, with_date=True)
                except Exception:
                    txt = ""
                web_cache[loc] = txt or ""
            src_text[marker] = web_cache.get(loc, "")
        for claim, markers in parse_cited_claims(sec):
            r = score_claim(claim, markers, src_text)
            r["bucket"] = classify(r, grounded, weak)
            results.append(r)
    return results


# ----------------------------------------------------------------------------- Mode B: fresh run
def score_run(questions: list[str], *, model: str | None, depth: str, analysts: int,
              grounded: float, weak: float) -> list[dict]:
    """Run the engine fresh with evidence capture ON, score each analyst draft against the
    exact ~1500-char extract it read (no re-fetch, no page-drift). This measures faithfulness
    to the evidence the model SAW — not whether that evidence, or the full source page, is
    correct about the world (review: HONESTY-003)."""
    os.environ["PW_CAPTURE_EVIDENCE"] = "1"
    os.environ.setdefault("PW_WEB_BACKEND", "ddgs")
    from council.local import detect_models, pick_cast
    from council.researcher import ResearchWorker

    if model:
        cast = [model]
    else:
        cast, _editor = pick_cast(detect_models(), max(1, analysts))
    results: list[dict] = []
    for q in questions:
        for m in cast:
            print(f"  · {m}  ⟶  {q[:70]}", file=sys.stderr, flush=True)
            rw = ResearchWorker(worker_id=m, model=m, lens="independent analyst",
                                country="local", depth=depth, page_evidence=True, scope="web")
            try:
                out = rw.research(q)
            except Exception as exc:
                print(f"    (run failed: {exc})", file=sys.stderr)
                continue
            src_text = {s["id"]: s.get("extract", "")
                        for s in out["research"].get("sources", [])}
            src_text.update({s["id"]: s.get("extract", "")
                             for s in out["research"].get("local_sources", [])})
            for claim, markers in parse_cited_claims(out["text"]):
                r = score_claim(claim, markers, src_text)
                r["bucket"] = classify(r, grounded, weak)
                r["question"], r["model"] = q, m
                results.append(r)
            if len(results) >= MAX_RESULTS:
                print(f"  (stopping at {MAX_RESULTS} claims)", file=sys.stderr)
                return results
    return results


# ----------------------------------------------------------------------------- Mode B (paired)
def score_paired(questions: list[str], *, model: str | None, depth: str, analysts: int,
                 grounded: float, weak: float) -> tuple[list[dict], list[dict], int, int]:
    """Within-run A/B (R35): run each brief ONCE with self-repair on, and score the PRE-repair and
    POST-repair draft against the SAME captured evidence. Because both drafts see identical sources,
    this isolates the repair's effect with zero re-research variance — the honest measure of the
    feature (unlike --compare, whose two arms research different pages). Returns
    (pre_results, post_results, n_changed, n_drafts)."""
    os.environ["PW_CAPTURE_EVIDENCE"] = "1"
    os.environ["PW_FIDELITY_TRACE"] = "1"
    os.environ["PW_FIDELITY_REPAIR"] = "1"
    os.environ.setdefault("PW_WEB_BACKEND", "ddgs")
    from council.local import detect_models, pick_cast
    from council.researcher import ResearchWorker

    cast = [model] if model else pick_cast(detect_models(), max(1, analysts))[0]
    pre_results: list[dict] = []
    post_results: list[dict] = []
    changed = total = 0
    for q in questions:
        for m in cast:
            print(f"  · {m}  ⟶  {q[:70]}", file=sys.stderr, flush=True)
            rw = ResearchWorker(worker_id=m, model=m, lens="independent analyst",
                                country="local", depth=depth, page_evidence=True, scope="web")
            try:
                out = rw.research(q)
            except Exception as exc:
                print(f"    (run failed: {exc})", file=sys.stderr)
                continue
            trace = out["research"].get("repair_trace")
            if not trace:
                continue                      # repair didn't run (no draft/evidence for this brief)
            total += 1
            changed += bool(trace["changed"])
            src_text = {s["id"]: s.get("extract", "") for s in out["research"].get("sources", [])}
            src_text.update({s["id"]: s.get("extract", "")
                             for s in out["research"].get("local_sources", [])})
            for body, sink in ((trace["pre"], pre_results), (trace["post"], post_results)):
                for claim, markers in parse_cited_claims(body):
                    r = score_claim(claim, markers, src_text)
                    r["bucket"] = classify(r, grounded, weak)
                    r["question"], r["model"] = q, m
                    sink.append(r)
    return pre_results, post_results, changed, total


# ----------------------------------------------------------------------------- reporting
_ORDER = ["GROUNDED", "WEAK", "UNGROUNDED", "UNVERIFIABLE", "NO_CONTENT"]


def grounded_rate(results: list[dict]) -> tuple[int, int, float]:
    """(grounded, verifiable, rate) — the headline number, factored out so --compare can diff two arms."""
    verifiable = [r for r in results if r["bucket"] in ("GROUNDED", "WEAK", "UNGROUNDED")]
    grounded = sum(1 for r in verifiable if r["bucket"] == "GROUNDED")
    return grounded, len(verifiable), grounded / (len(verifiable) or 1)


def summarize(results: list[dict], top: int) -> None:
    if not results:
        print("No cited claims found — nothing to score "
              "(no sources reachable, or the report has no citations).")
        return
    counts = {b: 0 for b in _ORDER}
    for r in results:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
    total = len(results)
    # verifiable = had source text AND checkable content (the population fidelity is judged on)
    verifiable = [r for r in results if r["bucket"] in ("GROUNDED", "WEAK", "UNGROUNDED")]
    nv = len(verifiable) or 1
    grounded_rate = counts["GROUNDED"] / nv
    mean_cov = sum(r["content_cov"] for r in verifiable) / nv
    num_claims = [r for r in verifiable if r["missing_numbers"] or r["num_cov"] < 1.0]
    bad_nums = [r for r in verifiable if r["missing_numbers"]]

    print(f"\n  Citation-fidelity — {total} cited claims "
          f"({len(verifiable)} verifiable, "
          f"{counts['UNVERIFIABLE']} unverifiable, {counts['NO_CONTENT']} no-content)")
    print(f"  ── grounded rate (of verifiable): {counts['GROUNDED']}/{nv}  ({grounded_rate:.0%})")
    print(f"  ── mean content overlap         : {mean_cov:.0%}")
    for b in _ORDER:
        if counts[b]:
            print(f"       {b:<13} {counts[b]:>4}  ({counts[b]/total:.0%})")
    if num_claims:
        print(f"  ── numeric claims with a missing number: {len(bad_nums)}/{len(num_claims)} "
              "(a multi-digit number/year in the claim was absent from its cited source; "
              "decimals & single digits are NOT checked — token overlap can't match format variants)")

    offenders = sorted(verifiable, key=lambda r: (r["content_cov"], -len(r["missing_numbers"])))
    if offenders and top:
        print(f"\n  Weakest {min(top, len(offenders))} cited claims (lowest source overlap):")
        for r in offenders[:top]:
            miss = f"  ✗numbers:{','.join(r['missing_numbers'])}" if r["missing_numbers"] else ""
            cite = ",".join(r["markers"])
            print(f"    [{r['content_cov']:.0%} {r['bucket']}] {r['claim'][:120]}  «{cite}»{miss}")

    print(
        "\n  HOW TO READ THIS — lexical grounding is a FLOOR, not proof of truth:\n"
        "    • A GROUNDED claim is 'not obviously fabricated', NOT 'verified accurate'. It can\n"
        "      still misrepresent a source whose words it happens to reuse, or blend facts across\n"
        "      two cited sources that never state the relationship together.\n"
        "    • Mode B scores a claim against the SAME ~1500-char extract the model read — so it\n"
        "      measures faithfulness-to-the-evidence-seen, not whether that evidence (or the full\n"
        "      page) is correct about the world.\n"
        "    • Mode A re-fetches pages live: page-drift, paywalls, and deleted URLs cause false\n"
        "      UNGROUNDED/UNVERIFIABLE — an old report cites pages that may have changed. Mode B is\n"
        "      the more reliable measure of the engine's own fidelity.\n"
        "    • 'grounded rate' is OF VERIFIABLE claims; unreachable sources are counted separately\n"
        "      as 'unverifiable', never as failures. The GROUNDED threshold (--grounded, default\n"
        "      0.5 overlap) is a heuristic, not calibrated to human judgment — tune it to your bar.")


def main() -> int:
    ap = argparse.ArgumentParser(prog="eval_citation_fidelity",
                                 description="Measure whether cited claims appear in their sources.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--report", help="Mode A: score an existing report .md (re-fetches sources)")
    src.add_argument("--run", action="store_true",
                     help="Mode B: fresh run scored against captured evidence (Ollama, no re-fetch)")
    src.add_argument("--compare", action="store_true",
                     help="Mode B A/B: run the brief set with the citation self-repair OFF then ON "
                          "(PW_FIDELITY_REPAIR) and report the grounded-rate delta (between-runs)")
    src.add_argument("--paired", action="store_true",
                     help="Mode B paired: run each brief ONCE and score its pre- vs post-repair draft "
                          "against the SAME evidence — the confound-free measure of the self-repair")
    ap.add_argument("--questions", help="Mode B: JSON file with a list of brief strings")
    ap.add_argument("--model", help="Mode B: force one analyst model (else auto-pick the cast)")
    ap.add_argument("--analysts", type=int, default=1, help="Mode B: how many analyst models (1-4)")
    ap.add_argument("--depth", choices=["quick", "standard", "deep"], default="quick")
    ap.add_argument("--grounded", type=float, default=0.5, help="content-overlap ≥ this ⇒ GROUNDED")
    ap.add_argument("--weak", type=float, default=0.3, help="content-overlap ≥ this ⇒ WEAK")
    ap.add_argument("--top", type=int, default=10, help="how many weakest claims to print")
    ap.add_argument("--json", dest="json_out", help="write raw per-claim results to this path")
    a = ap.parse_args()

    def _questions() -> list[str]:
        if a.questions:
            loaded = json.loads(pathlib.Path(a.questions).expanduser().read_text())
            return [str(x) for x in loaded if str(x).strip()]
        return DEFAULT_QUESTIONS

    if a.compare:
        # A/B the inference-time self-repair (R35): same brief set, repair OFF then ON. Each arm
        # re-researches from scratch (repair lives inside the pipeline), so this is a between-runs
        # PRODUCT-level read — "are the reports users get more grounded with repair on?" — not a
        # per-claim paired test. That is exactly the question that matters for shipping it.
        questions = _questions()
        arms: dict[str, list[dict]] = {}
        for label, val in (("OFF", "0"), ("ON", "1")):
            os.environ["PW_FIDELITY_REPAIR"] = val
            print(f"\n=== self-repair {label} (PW_FIDELITY_REPAIR={val}) ===", file=sys.stderr)
            arms[label] = score_run(questions, model=a.model, depth=a.depth,
                                    analysts=max(1, min(4, a.analysts)), grounded=a.grounded, weak=a.weak)
            summarize(arms[label], a.top)
        go, no_, ro = grounded_rate(arms["OFF"])
        gn, nn, rn = grounded_rate(arms["ON"])
        print("\n  ── Self-repair A/B — grounded rate of verifiable cited claims ──")
        print(f"     repair OFF : {go}/{no_}  ({ro:.0%})")
        print(f"     repair ON  : {gn}/{nn}  ({rn:.0%})")
        print(f"     delta      : {(rn - ro) * 100:+.1f} pts")
        print("     (each arm re-researches → a between-runs product-level read, not a paired test)")
        if a.json_out:
            pathlib.Path(a.json_out).write_text(json.dumps(arms, indent=2, ensure_ascii=False))
            print(f"\n  raw results → {a.json_out}", file=sys.stderr)
        return 0

    if a.paired:
        pre, post, changed, total = score_paired(
            _questions(), model=a.model, depth=a.depth, analysts=max(1, min(4, a.analysts)),
            grounded=a.grounded, weak=a.weak)
        gp, np_, rp = grounded_rate(pre)
        go, no_, ro = grounded_rate(post)
        print(f"\n  ── Self-repair PAIRED A/B — {total} draft(s), repair changed {changed} ──")
        print(f"     pre-repair  grounded rate: {gp}/{np_}  ({rp:.0%})")
        print(f"     post-repair grounded rate: {go}/{no_}  ({ro:.0%})")
        print(f"     delta       : {(ro - rp) * 100:+.1f} pts   (same run, same evidence — confound-free)")
        print("     (post-repair can never score below pre-repair on a given draft, by the accept gate;\n"
              "      the repair only fires on drafts that carry an unsupported/fabricated cited claim.)")
        if a.json_out:
            pathlib.Path(a.json_out).write_text(json.dumps(
                {"pre": pre, "post": post, "changed": changed, "total": total}, indent=2, ensure_ascii=False))
            print(f"\n  raw results → {a.json_out}", file=sys.stderr)
        return 0

    if a.report:
        text = pathlib.Path(a.report).expanduser().read_text(errors="ignore")
        print(f"Scoring {a.report} by re-fetching its cited sources …", file=sys.stderr)
        results = score_report(text, grounded=a.grounded, weak=a.weak)
    else:
        results = score_run(_questions(), model=a.model, depth=a.depth,
                            analysts=max(1, min(4, a.analysts)), grounded=a.grounded, weak=a.weak)

    summarize(results, a.top)
    if a.json_out:
        pathlib.Path(a.json_out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"\n  raw results → {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
