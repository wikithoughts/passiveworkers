"""Report export: the dependency-free markdown→HTML renderer (council/render.py) and the structured
--json payload builder (council/local._report_payload)."""
from council.local import _report_payload
from council.render import md_to_html, report_html


def test_md_to_html_headings_bold_links_lists():
    h = md_to_html("# Title\n\nSome **bold** and http://x.com\n\n- one\n- two")
    assert "<h1>Title</h1>" in h
    assert "<strong>bold</strong>" in h
    assert '<a href="http://x.com"' in h
    assert "<ul>" in h and "<li>one</li>" in h and "<li>two</li>" in h and "</ul>" in h


def test_md_to_html_escapes_markup():
    h = md_to_html("<script>alert(1)</script>")
    assert "<script>" not in h and "&lt;script&gt;" in h


def test_report_html_is_self_contained_and_labeled():
    doc = report_html("My brief", "# R\n\nbody text",
                      {"n_sources": 5, "words": 100, "generated": "2026-07-05"})
    assert doc.startswith("<!doctype html>")
    assert "My brief" in doc and "5 sources" in doc and "body text" in doc
    # a portable single file: no external CSS/JS/font/CDN loads in the document
    for token in ("unpkg", "cdn", "<link", "<script"):
        assert token not in doc


def test_report_payload_dedupes_sources_and_keeps_report():
    contributions = [
        {"model": "qwen:7b", "text": "a b c",
         "research": {"sources": [{"id": "S1", "title": "T", "url": "http://a", "host": "a"},
                                  {"id": "S2", "title": "U", "url": "http://a", "host": "a"},
                                  {"id": "S3", "title": "V", "url": "http://b", "host": "b"}]}},
    ]
    p = _report_payload("brief", "standard", "gpt-5-chat", "the report words here", contributions, 3)
    assert p["brief"] == "brief" and p["depth"] == "standard" and p["editor"] == "gpt-5-chat"
    assert p["analysts"][0]["model"] == "qwen:7b" and p["analysts"][0]["n_sources"] == 3
    assert len(p["sources"]) == 2                    # deduped by url (a appears twice)
    assert p["report"] == "the report words here"
    assert p["words"] == 4


# --------------------------------------------------------------------- review-found (D48) fixes
def test_link_keeps_balanced_parens_in_url():
    # Wikipedia disambiguation URLs (Mercury_(planet)) must keep their closing paren in the href
    h = md_to_html("See https://en.wikipedia.org/wiki/Mercury_(planet) for details")
    assert 'href="https://en.wikipedia.org/wiki/Mercury_(planet)"' in h


def test_link_trims_unbalanced_trailing_paren():
    h = md_to_html("(see https://example.com/x)")
    assert 'href="https://example.com/x"' in h        # the wrapping ')' is NOT swallowed into the href


def test_report_html_title_cannot_leak_body():
    doc = report_html("brief about __BODY__ markers", "# Real Heading\n\nbody", {"words": 3})
    title = doc.split("<title>")[1].split("</title>")[0]
    assert "Real Heading" not in title and "__BODY__" in title   # body not leaked into the title


def test_payload_includes_library_sources():
    contributions = [{"model": "m", "text": "a b", "research": {"sources": [], "local_sources": [
        {"id": "L1", "title": "My Notes", "source": "/docs/notes.md"}]}}]
    p = _report_payload("b", "local", "ed", "report text", contributions, 0)
    libs = [s for s in p["sources"] if s.get("kind") == "library"]
    assert len(libs) == 1 and libs[0]["source"] == "/docs/notes.md"
    assert p["n_sources"] == 1                        # library provenance is counted (was 0)
