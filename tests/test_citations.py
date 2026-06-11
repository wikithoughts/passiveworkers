"""Citation hygiene: dangling [S#] AND [L#] markers are stripped (R7 review findings 4–5)."""
from council.local import fix_dangling_citations


def test_keeps_resolved_web_and_local():
    t = ("Finding A [S1] and doc point [L1].\n"
         "[S1] Title — http://x\n[L1] memo.md — /p/memo.md")
    out = fix_dangling_citations(t)
    assert "[S1]" in out and "[L1]" in out


def test_strips_dangling_local_marker():
    # [L3] is cited but only [L1] is listed → [L3] must be dropped
    t = "Claim [L1] and bogus [L3].\n[L1] memo.md — /p/memo.md"
    out = fix_dangling_citations(t)
    assert "[L1]" in out and "[L3]" not in out


def test_strips_dangling_web_marker():
    t = "Real [S1], fake [S9].\n[S1] T — http://x"
    out = fix_dangling_citations(t)
    assert "[S1]" in out and "[S9]" not in out


def test_no_listing_leaves_text_untouched():
    t = "no sources here at all"
    assert fix_dangling_citations(t) == t
