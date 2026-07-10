"""R35/D51 — inference-time citation-fidelity self-repair (ResearchWorker._repair_draft).

Pure/deterministic: the only model call (_generate) is stubbed, so these tests pin the ACCEPTANCE
GATE, not model quality. The gate's contract: a revision is accepted only if it strictly reduces the
unsupported-claim set AND keeps as many grounded claims AND doesn't gut the content — so by fidelity's
own measure the pass can never lower a report's grounding. On any doubt the original draft is returned.
No network, no Ollama."""
from council.researcher import ResearchWorker

# Two sources: S1 supports a rate claim; S2 is about apples (off-topic to anything we cite it for).
EVIDENCE = [
    {"title": "Fed", "url": "https://a.test/1", "host": "a.test",
     "snippet": "The federal funds rate target range is 3.50 to 3.75 percent, held since April 2026."},
    {"title": "Orchard", "url": "https://b.test/2", "host": "b.test",
     "snippet": "Apples grow on trees in orchards during the autumn harvest season each year."},
]
# One grounded sentence ([S1]) + one off-topic UNGROUNDED sentence ([S2]).
DIRTY = ("The federal funds rate is 3.75% held since April [S1]. "
         "The moon is made of green cheese [S2].")


def _worker(response):
    """A ResearchWorker whose _generate is stubbed to return `response` and count its calls."""
    w = ResearchWorker(worker_id="m", model="m", scope="web", page_evidence=True)
    calls = {"n": 0}

    def fake_generate(prompt, num_predict=None):
        calls["n"] += 1
        return (response, 5)

    w._generate = fake_generate      # instance attribute shadows the bound method (no self passed)
    w._calls = calls
    return w


def test_accepts_revision_that_removes_the_unsupported_claim():
    fixed = "The federal funds rate is 3.75% held since April [S1]."   # dropped the cheese sentence
    w = _worker(fixed)
    out = w._repair_draft("brief", DIRTY, EVIDENCE, None)
    assert out == fixed and w._calls["n"] == 1      # revision accepted, exactly one repair call


def test_keeps_original_when_revision_still_unsupported():
    # the model "fixed" nothing — the unsupported set didn't shrink → reject, keep the original draft
    w = _worker("The moon is made of green cheese [S2].")
    out = w._repair_draft("brief", DIRTY, EVIDENCE, None)
    assert out == DIRTY


def test_keeps_original_on_empty_revision():
    w = _worker("   ")
    assert w._repair_draft("brief", DIRTY, EVIDENCE, None) == DIRTY


def test_keeps_original_when_revision_guts_the_content():
    # this revision clears the unsupported claim AND stays grounded, but collapses the report to a
    # fragment (< 50% of the original word count) → the anti-gutting gate rejects it.
    w = _worker("Rate [S1].")
    assert w._repair_draft("brief", DIRTY, EVIDENCE, None) == DIRTY


def test_clean_draft_makes_no_model_call():
    clean = "The federal funds rate is 3.75% held since April [S1]."
    w = _worker("should never be used")
    out = w._repair_draft("brief", clean, EVIDENCE, None)
    assert out == clean and w._calls["n"] == 0      # nothing to fix → zero cost


def test_local_only_scope_repairs_L_markers():
    # a library ([L#]) citation is checked the same way as a web ([S#]) one
    local = [{"title": "memo.md", "source": "/memo.md",
              "text": "Q3 revenue was 12 million dollars, up from Q2."}]
    dirty = ("Q3 revenue was 12 million dollars [L1]. "
             "The company opened 400 new stores in Antarctica [L1].")
    fixed = "Q3 revenue was 12 million dollars [L1]."
    w = _worker(fixed)
    out = w._repair_draft("brief", dirty, [], local)
    assert out == fixed and w._calls["n"] == 1


# ── anti-gaming guards (review R35): the "unsupported" set must shrink by CORRECTING a claim, not by
#    stripping/mangling its citation so the fabrication falls out of the score. ────────────────────
def test_rejects_marker_strip_that_hides_a_fabrication():
    # the model keeps the fabricated "47 percent in 2026" text but DROPS its [S2] marker → the claim
    # becomes invisible to the scorer, so len(after) < len(before) despite nothing being fixed. Without
    # the guard the gate would accept and ship the fabrication un-cited (inflating the grounded rate).
    ev = [EVIDENCE[0],
          {"title": "Mkt", "url": "https://c.test/3", "host": "c.test",
           "snippet": "Quarterly market commentary on consumer sentiment and housing starts."}]
    dirty = ("The federal funds rate is 3.75% held since April [S1]. "
             "The market grew 47 percent in 2026 [S2].")
    stripped = ("The federal funds rate is 3.75% held since April [S1]. "
                "The market grew 47 percent in 2026.")   # same fabrication, citation removed
    w = _worker(stripped)
    assert w._repair_draft("brief", dirty, ev, None) == dirty


def test_rejects_revision_that_invents_a_dangling_marker():
    # the model re-cites the flagged claim to a source that doesn't exist ([S9]) → union='' →
    # UNVERIFIABLE → it leaves the flagged set, but the report would ship a citation to no source. Reject.
    invented = ("The federal funds rate is 3.75% held since April [S1]. "
                "The moon is made of green cheese [S9].")
    w = _worker(invented)
    assert w._repair_draft("brief", DIRTY, EVIDENCE, None) == DIRTY


# ── each guard pinned in isolation (review R35: a test must fail if its guard is deleted/inverted) ──
EVIDENCE2 = [
    {"title": "Fed", "url": "https://a.test/1", "host": "a.test",
     "snippet": "The federal funds rate target range is 3.50 to 3.75 percent, held since April 2026."},
    {"title": "CPI", "url": "https://b.test/2", "host": "b.test",
     "snippet": "Consumer price inflation rose to 2.4 percent over the trailing twelve months."},
]
DIRTY2 = ("The federal funds rate target range is 3.50 to 3.75 percent held since April [S1]. "
          "Consumer price inflation rose to 2.4 percent over the trailing twelve months [S2]. "
          "The moon is made of green cheese [S1].")


def test_rejects_revision_that_drops_a_grounded_claim():
    # removes the unsupported cheese sentence (guard A ok) and stays long enough (guard C ok), but
    # silently drops the grounded [S2] inflation sentence → ONLY guard B (g_after < g_before) rejects.
    revised = ("The federal funds rate target range set by the Federal Reserve is 3.50 to 3.75 "
               "percent and has been held steady since the April meeting this year [S1].")
    w = _worker(revised)
    assert w._repair_draft("brief", DIRTY2, EVIDENCE2, None) == DIRTY2


def test_rejects_revision_that_swaps_in_a_different_unsupported_claim():
    # replaces the flagged claim with a DIFFERENT still-unsupported claim: unsupported count unchanged
    # (guard A fails), grounded claim kept (B ok), length preserved (C ok) → ONLY guard A rejects.
    swapped = ("The federal funds rate is 3.75% held since April [S1]. "
               "Bananas are purple sea creatures native to Jupiter [S2].")
    w = _worker(swapped)
    assert w._repair_draft("brief", DIRTY, EVIDENCE, None) == DIRTY
