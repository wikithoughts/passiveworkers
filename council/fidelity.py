#!/usr/bin/env python3
"""
council/fidelity.py — citation-grounding measurement (R15/D27, the honest eval core)
====================================================================================
Pure, dependency-free logic for the question the whole product rests on:

    when a report says X [S3], does source S3 actually support X?

This is a LEXICAL grounding *floor*, not a semantic judge. It reliably catches the
failure modes that matter and recur:
  • a citation pointing at an off-topic source (the claim and the cited source barely
    share vocabulary), and
  • a number / date / code in the claim that is ABSENT from the cited source — the
    classic fabricated statistic.
It CANNOT tell whether a source that shares the claim's vocabulary is being faithfully
represented; that needs an entailment model (an optional, off-by-default hook in
scripts/eval_citation_fidelity.py). So read a GROUNDED verdict as "not obviously
fabricated", never as "proven true". A high score is necessary, not sufficient.

No network, no Ollama, no new dependencies — it reuses council.retrieval.tokenize so
the scorer tokenizes EXACTLY like the retriever it is grading.
"""
from __future__ import annotations

import re

from council.retrieval import tokenize

# Compact English stop-word set: content-overlap must not be inflated by glue words.
# (Kept small and obvious on purpose — this is a floor, not a linguistics project.)
STOPWORDS = frozenset("""
a an the and or but nor so yet if then else of to in on at by for with from into onto
over under above below up down out off about as is are was were be been being am do does
did has have had having will would shall should can could may might must not no than that
this these those it its their there here they them he she his her you your we our us i my
me what which who whom whose when where why how all any both each few more most other some
such only own same too very s t just also against between through during before after while
""".split())

_MARKER = re.compile(r"[SL]\d+")                 # a single citation marker, e.g. S3 / L1
_BRACKET = re.compile(r"\[([^\]]*)\]")           # contents of every [...] span
_DIGIT = re.compile(r"\d")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")       # naive sentence split (good enough for an eval)
# the headers researcher.py appends to separate a draft from its source listing
_SOURCE_HEADER = re.compile(r"(?m)^\s*(?:WEB SOURCES|YOUR DOCUMENTS)\b")
# a listing line: "[S1] Title (date) — https://host/x"  /  "[L1] title — /path/to.md"
# greedy up to the LAST em-dash/hyphen separator so an em-dash inside the title is tolerated
_SRC_LINE = re.compile(r"(?m)^\s*\[([SL]\d+)\]\s+.*[—–-]\s+(\S+)\s*$")
# a contribution heading in a compiled report ("### model" / "### Country — model")
_SECTION_SPLIT = re.compile(r"(?m)^###\s+")


# ----------------------------------------------------------------------------- tokens
def _strip_markers(text: str) -> str:
    """Remove [...] spans so the markers themselves never count as claim content
    (otherwise every cited sentence carries a guaranteed-missing 's3' token)."""
    return _BRACKET.sub(" ", text or "")


def content_tokens(text: str) -> set[str]:
    """Meaningful tokens: drop stop-words and single chars, but KEEP digit tokens
    (a lone '5' or '18' is exactly the kind of fact we want to verify)."""
    return {t for t in tokenize(text)
            if t not in STOPWORDS and (len(t) > 1 or t.isdigit())}


def numeric_tokens(text: str) -> set[str]:
    """Tokens containing a digit — numbers, years, codes (INV-7731 → '7731')."""
    return {t for t in tokenize(text) if _DIGIT.search(t)}


def significant_numbers(text: str) -> set[str]:
    """The numeric tokens worth treating as checkable FACTS: pure multi-digit integers
    (statistics, years, codes — '18', '2026', '7731'). Deliberately EXCLUDES:
      • single digits ('5') — too common to be a reliable fabrication signal, and
      • decimals/alnum codes ('4.2'→'4','2'; 'v1') — token overlap can't match format
        variants ('4.2 million' vs '4,200,000'), so flagging them yields false positives.
    This is why num_cov is reported separately and never folded into the headline score
    (review: HONESTY-001, BUG-001)."""
    return {t for t in tokenize(text) if t.isdigit() and len(t) >= 2}


# ----------------------------------------------------------------------------- scoring
def grounding_score(claim: str, source: str) -> dict:
    """Lexical grounding of one claim against the text of its cited source(s).

    Returns a dict:
      content_cov   fraction of the claim's content tokens present in the source [0..1]
      num_cov       fraction of the claim's numeric tokens present (1.0 if it has none)
      missing_numbers   numeric tokens in the claim absent from the source (the red flags)
      n_content     how many content tokens the claim had (0 ⇒ nothing checkable)
      source_empty  True when there was no source text to check against
      score         headline = content_cov (numbers are surfaced separately, not folded
                    in harshly, because legitimate format drift — '4.2 million' vs
                    '4,200,000' — would otherwise read as fabrication)
    """
    claim = _strip_markers(claim)
    claim_c = content_tokens(claim)
    if not (source or "").strip():
        return {"score": 0.0, "content_cov": 0.0, "num_cov": 0.0,
                "missing_numbers": sorted(significant_numbers(claim)),
                "n_content": len(claim_c), "source_empty": True}
    src_all = set(tokenize(source))
    if not claim_c:
        # a sentence with a marker but no checkable content (e.g. a pure transition) —
        # not a fidelity failure; flag it as such so the runner can exclude it.
        return {"score": 1.0, "content_cov": 1.0, "num_cov": 1.0,
                "missing_numbers": [], "n_content": 0, "source_empty": False}
    content_cov = len(claim_c & src_all) / len(claim_c)
    claim_nums = significant_numbers(claim)
    if claim_nums:
        present = claim_nums & src_all
        num_cov = len(present) / len(claim_nums)
        missing = sorted(claim_nums - present)
    else:
        num_cov, missing = 1.0, []
    return {"score": round(content_cov, 3), "content_cov": round(content_cov, 3),
            "num_cov": round(num_cov, 3), "missing_numbers": missing,
            "n_content": len(claim_c), "source_empty": False}


def classify(g: dict, grounded: float = 0.5, weak: float = 0.3) -> str:
    """Bucket a grounding result. UNVERIFIABLE keeps fetch failures out of the
    fabrication count — an unreachable source is not the same as a fabricated one."""
    if g.get("source_empty"):
        return "UNVERIFIABLE"
    if g["n_content"] == 0:
        return "NO_CONTENT"
    if g["content_cov"] >= grounded:
        return "GROUNDED"
    if g["content_cov"] >= weak:
        return "WEAK"
    return "UNGROUNDED"


# ----------------------------------------------------------------------------- parsing
def split_draft(text: str) -> str:
    """The draft body, with the appended WEB SOURCES / YOUR DOCUMENTS listing removed."""
    m = _SOURCE_HEADER.search(text or "")
    return (text[:m.start()] if m else (text or "")).rstrip()


def markers_in(text: str) -> list[str]:
    """Citation markers inside any [...] span, de-duplicated, order-preserving.
    Handles [S1], [S1, S2], [S1][S2], [L1]; ignores non-marker brackets like [2024]."""
    out, seen = [], set()
    for span in _BRACKET.findall(text or ""):
        for m in _MARKER.findall(span):
            if m not in seen:
                seen.add(m)
                out.append(m)
    return out


def parse_cited_claims(draft: str) -> list[tuple[str, list[str]]]:
    """Sentences carrying at least one [S#]/[L#] marker → (sentence, [markers])."""
    out = []
    for sent in _SENT_SPLIT.split(split_draft(draft)):
        sent = sent.strip()
        if not sent:
            continue
        markers = markers_in(sent)
        if markers:
            out.append((sent, markers))
    return out


def parse_source_map(text: str) -> dict[str, str]:
    """Marker → URL/path, read from the source-listing lines ('[S1] Title — https://x')."""
    return {m.group(1): m.group(2) for m in _SRC_LINE.finditer(text or "")}


def split_sections(report: str) -> list[str]:
    """A compiled report's per-analyst '### …' blocks (each self-contained: draft +
    its own source list). Anything before the first heading (summary, agree/differ) is
    dropped — the executive summary carries no citations to grade."""
    parts = _SECTION_SPLIT.split(report or "")
    return [p for p in parts[1:] if p.strip()] if len(parts) > 1 else []


# ----------------------------------------------------------------------------- one claim
def score_claim(claim: str, markers: list[str], sources: dict[str, str]) -> dict:
    """Grounding of a claim against the UNION of the sources IT cites — a sentence is
    fairly judged against every source it points at (the fact may be split across them).
    A claim is UNVERIFIABLE only when ALL of its cited sources are empty/unreachable.

    Limitation (review: HONESTY-005): checking the union means a claim asserting a
    RELATIONSHIP across sources ("X founded Y [S1, S2]") can pass when S1 mentions X and
    S2 mentions Y, even if neither source states the relationship. Union grounding proves
    the terms are attributable to the cited set, not that the claim's logic is supported —
    another reason a GROUNDED verdict is a floor, not proof of truth."""
    union = "\n".join(sources.get(m, "") for m in markers)
    g = grounding_score(claim, union)
    g["markers"] = list(markers)
    g["cited_present"] = [m for m in markers if (sources.get(m) or "").strip()]
    g["claim"] = claim.strip()
    return g
