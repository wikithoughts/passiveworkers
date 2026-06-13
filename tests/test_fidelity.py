"""R15/D27 citation-fidelity scorer (council/fidelity.py) — the pure grounding logic, plus
the env-gated evidence capture in researcher.py. No network, no Ollama (all boundaries mocked)."""
from council.fidelity import (classify, content_tokens, grounding_score, markers_in,
                              numeric_tokens, parse_cited_claims, parse_source_map,
                              score_claim, split_draft, split_sections)


# ----------------------------------------------------------------------------- token helpers
def test_content_tokens_drop_stopwords_keep_numbers():
    toks = content_tokens("The revenue rose 18 percent in 2026")
    assert "the" not in toks and "in" not in toks      # stop-words gone
    assert "revenue" in toks and "18" in toks and "2026" in toks  # facts + digits kept


def test_numeric_tokens_catch_codes_and_years():
    n = numeric_tokens("Invoice INV-7731 due 2026 at 4.2 million")
    assert "7731" in n and "2026" in n and "4" in n and "2" in n
    assert "invoice" not in n


# ----------------------------------------------------------------------------- grounding_score
def test_grounded_when_claim_appears_in_source():
    g = grounding_score("Revenue rose 18% in Q1 2026 [S1]",
                        "The company reported revenue rose 18% in Q1 2026 across all regions.")
    assert g["content_cov"] >= 0.8 and g["num_cov"] == 1.0 and g["missing_numbers"] == []
    assert classify(g) == "GROUNDED"


def test_markers_do_not_count_as_claim_content():
    # 's1' must never become a guaranteed-missing token that drags content_cov down
    with_marker = grounding_score("Revenue rose [S1]", "Revenue rose sharply.")
    assert with_marker["content_cov"] == 1.0


def test_missing_number_is_flagged_but_topic_still_overlaps():
    g = grounding_score("Revenue rose 42% in 2026 [S1]",
                        "Revenue rose in 2026 across all regions.")  # 42 absent
    assert "42" in g["missing_numbers"] and g["num_cov"] < 1.0
    assert g["content_cov"] >= 0.5                       # topic words still overlap → GROUNDED
    assert classify(g) == "GROUNDED"                     # number flagged separately, not folded in


def test_offtopic_citation_is_ungrounded():
    g = grounding_score("The new tax credit applies to electric vehicles [S1]",
                        "A recipe for sourdough bread requires flour, water, and salt.")
    assert g["content_cov"] < 0.3 and classify(g) == "UNGROUNDED"


def test_empty_source_is_unverifiable_not_a_failure():
    g = grounding_score("Some claim with a figure of 77 percent [S1]", "")
    assert g["source_empty"] is True and classify(g) == "UNVERIFIABLE"
    assert "77" in g["missing_numbers"]                  # still surfaced for the record


def test_number_check_ignores_format_drift_and_codes():
    from council.fidelity import significant_numbers
    # single digits / decimals / alnum codes are NOT treated as checkable numbers (review HONESTY-001/BUG-001)
    assert significant_numbers("grew 4.2 million via v1 plan") == set()
    assert significant_numbers("18% in 2026, invoice INV-7731") == {"18", "2026", "7731"}
    # '4.2 million' must NOT be flagged missing against a source written '4,200,000'
    g = grounding_score("Budget is 4.2 million dollars [S1]", "The budget is 4,200,000 dollars.")
    assert g["missing_numbers"] == [] and g["num_cov"] == 1.0
    # a genuinely fabricated multi-digit stat IS still flagged
    g2 = grounding_score("Revenue grew 42% in 2026 [S1]", "Revenue grew in 2026.")
    assert "42" in g2["missing_numbers"]


def test_marker_only_sentence_has_no_content():
    g = grounding_score("[S1]", "anything at all here")
    assert g["n_content"] == 0 and classify(g) == "NO_CONTENT"


# ----------------------------------------------------------------------------- parsing
def test_markers_in_handles_single_grouped_and_repeated():
    assert markers_in("a [S1] b [S2, S3] c [L1][S1] d [2024]") == ["S1", "S2", "S3", "L1"]


def test_parse_cited_claims_only_returns_sentences_with_markers():
    draft = ("Uncited background sentence here. "
             "GDP grew 3% last year [S1]. Another fact from a doc [L2].\n\n"
             "WEB SOURCES:\n[S1] Title — https://x\n")
    claims = parse_cited_claims(draft)
    texts = [c for c, _ in claims]
    assert any("GDP grew 3%" in t for t in texts)
    assert any("from a doc" in t for t in texts)
    assert not any("Uncited background" in t for t in texts)
    assert not any("https://x" in t for t in texts)      # source listing excluded from claims


def test_split_draft_removes_source_listing():
    body = split_draft("Finding [S1].\n\nWEB SOURCES (Germany):\n[S1] T — https://x")
    assert "Finding [S1]." in body and "WEB SOURCES" not in body and "https://x" not in body


def test_parse_source_map_reads_web_and_local_lines():
    listing = ("WEB SOURCES:\n"
               "[S1] Some Title (2026-01-01) — https://example.com/a\n"
               "[S2] Another — https://host.test/b\n"
               "YOUR DOCUMENTS:\n"
               "[L1] memo.md — /home/u/memo.md\n")
    m = parse_source_map(listing)
    assert m["S1"] == "https://example.com/a" and m["S2"] == "https://host.test/b"
    assert m["L1"] == "/home/u/memo.md"


def test_split_sections_drops_preamble_and_keeps_analyst_blocks():
    report = ("# Research report\n## Executive summary\nNo citations here.\n\n"
              "## Findings by analyst\n\n### qwen3:14b\nFinding [S1].\n\n### gemma3:4b\nOther [S1].\n")
    secs = split_sections(report)
    assert len(secs) == 2
    assert "qwen3:14b" in secs[0] and "gemma3:4b" in secs[1]
    assert all("Executive summary" not in s for s in secs)   # uncited preamble excluded


# ----------------------------------------------------------------------------- score_claim (union over cited sources)
def test_score_claim_uses_union_of_cited_sources():
    sources = {"S1": "The bridge opened in 2026.", "S2": "It cost 4 billion dollars."}
    # the fact is split across both cited sources — union grounds it
    r = score_claim("The bridge opened in 2026 costing 4 billion [S1, S2]", ["S1", "S2"], sources)
    assert r["content_cov"] >= 0.8 and r["missing_numbers"] == []
    assert r["cited_present"] == ["S1", "S2"]


def test_score_claim_unverifiable_only_when_all_sources_empty():
    r_all_empty = score_claim("x costs 5 [S1]", ["S1"], {"S1": ""})
    assert classify(r_all_empty) == "UNVERIFIABLE"
    r_partial = score_claim("the tower is 5 tall [S1, S2]", ["S1", "S2"],
                            {"S1": "", "S2": "the tower is 5 tall"})
    assert classify(r_partial) == "GROUNDED" and r_partial["cited_present"] == ["S2"]


# ----------------------------------------------------------------------------- evidence capture (researcher.py)
def _capture_run(monkeypatch, env_on: bool):
    import council.researcher as RW

    monkeypatch.setattr(RW.ResearchWorker, "_generate",
                        lambda self, prompt, num_predict: ('["q1","q2","q3"]', 3))
    # 3 results, but quick depth only fetches the top 2 pages → the 3rd must fall back to its snippet
    monkeypatch.setattr(RW, "search_structured",
                        lambda q, max_results=4: [
                            {"title": "T1", "url": "https://a.test/1", "host": "a.test",
                             "snippet": "snippet one about the topic"},
                            {"title": "T2", "url": "https://b.test/2", "host": "b.test",
                             "snippet": "snippet two about the topic"},
                            {"title": "T3", "url": "https://c.test/3", "host": "c.test",
                             "snippet": "snippet three about the topic"}])
    monkeypatch.setattr(RW, "fetch_extract",
                        lambda url, max_chars=1500, with_date=False: ("FULL PAGE EXTRACT here", "2026-01-01"))
    if env_on:
        monkeypatch.setenv("PW_CAPTURE_EVIDENCE", "1")
    else:
        monkeypatch.delenv("PW_CAPTURE_EVIDENCE", raising=False)
    w = RW.ResearchWorker(worker_id="m", model="m", depth="quick", scope="web", page_evidence=True)
    return w.research("a brief about the topic")


def test_evidence_capture_off_by_default(monkeypatch):
    out = _capture_run(monkeypatch, env_on=False)
    assert out["research"]["sources"]
    assert all("extract" not in s for s in out["research"]["sources"])   # no payload leak by default


def test_evidence_capture_attaches_seen_text_when_enabled(monkeypatch):
    out = _capture_run(monkeypatch, env_on=True)
    srcs = out["research"]["sources"]
    assert all("extract" in s for s in srcs)
    # the page we fetched is captured for the top result; the rest fall back to the snippet
    assert any("FULL PAGE EXTRACT" in s["extract"] for s in srcs)
    assert any("snippet" in s["extract"] for s in srcs)


def test_evidence_capture_suppressed_for_federated_worker(monkeypatch):
    # review EXTRACT-FEDERATION-LEAK: a federated worker (PW_COORDINATOR set) must NEVER attach
    # extracts — they would be POSTed to the coordinator. Capture is local-only by construction.
    monkeypatch.setenv("PW_COORDINATOR", "http://coordinator.example:8088")
    out = _capture_run(monkeypatch, env_on=True)
    assert all("extract" not in s for s in out["research"]["sources"])


# ----------------------------------------------------------------------------- runner I/O safety
import importlib.util as _u  # noqa: E402
import pathlib as _pl        # noqa: E402

_spec = _u.spec_from_file_location(
    "ev_runner", _pl.Path(__file__).resolve().parents[1] / "scripts" / "eval_citation_fidelity.py")
_EV = _u.module_from_spec(_spec)
_spec.loader.exec_module(_EV)


def test_read_local_refuses_symlinks(tmp_path):
    real = tmp_path / "real.md"
    real.write_text("secret library content")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    assert _EV._read_local(str(real)) == "secret library content"   # real file reads
    assert _EV._read_local(str(link)) == ""                          # symlink refused


def test_read_local_bounds_size(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("x" * 10)
    assert _EV._read_local(str(big), cap=5) == ""                    # over the size cap → empty


def test_score_report_caps_url_refetches(monkeypatch):
    import council.research as R
    calls = {"n": 0}

    def fake_fetch(url, max_chars=4000, with_date=True):
        calls["n"] += 1
        return ("relevant page text about the claim", "2026-01-01")

    monkeypatch.setattr(R, "fetch_extract", fake_fetch)
    monkeypatch.setattr(_EV, "MAX_REPORT_URLS", 2)
    report = ("Claim one [S1]. Claim two [S2]. Claim three [S3]. Claim four [S4].\n\n"
              "WEB SOURCES:\n"
              "[S1] T1 — https://a.test/1\n[S2] T2 — https://b.test/2\n"
              "[S3] T3 — https://c.test/3\n[S4] T4 — https://d.test/4\n")
    results = _EV.score_report(report, grounded=0.5, weak=0.3)
    assert calls["n"] <= 2                                           # never fanned out past the cap
    assert any(r["bucket"] == "UNVERIFIABLE" for r in results)       # over-cap sources unverifiable
