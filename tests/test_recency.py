"""R19/D31 currency injection: pin the current year into time-sensitive WEB queries (the fix
R18's recency RANKING can't make — you cannot reorder a fresh source search never returned), and
auto-deepen genuinely BREAKING briefs. Pure logic + mocked boundaries (no network)."""
from council.research import inject_recency, is_breaking, is_time_sensitive


# ----------------------------------------------------------------------------- inject_recency
def test_injects_year_for_time_sensitive_query_without_one():
    assert inject_recency("current US federal funds rate", "2026-06-13") == \
        "current US federal funds rate 2026"
    assert inject_recency("latest FOMC decision", "2026-06-13") == "latest FOMC decision 2026"


def test_uses_year_from_today_not_wall_clock():
    # the year comes from the passed `today`, so reports are reproducible "as of" a date
    assert inject_recency("who leads the agency", "2030-01-01").endswith(" 2030")


def test_noop_when_current_or_future_year_already_present():
    # current year already pinned → don't double-pin; a near-future (forecast) year → respect it
    assert inject_recency("FOMC rate decision 2026", "2026-06-13") == "FOMC rate decision 2026"
    assert inject_recency("fed funds rate 2027 forecast", "2026-06-13") == \
        "fed funds rate 2027 forecast"


def test_recently_stale_year_gets_current_appended_not_replaced():
    # review R19 finding 2: a hallucinated/stale RECENT year (FOMC "2023") must not silently recur —
    # append the current year alongside it (never string-replace → never corrupt a query)
    assert inject_recency("US federal funds rate 2023", "2026-06-13") == \
        "US federal funds rate 2023 2026"
    assert inject_recency("rate decision 2024", "2026-06-13") == "rate decision 2024 2026"


def test_deep_historical_year_is_respected():
    # a deliberately old standalone year (> _STALE_WINDOW back) signals historical intent → leave it
    assert inject_recency("federal funds rate in the 2008 crisis", "2026-06-13") == \
        "federal funds rate in the 2008 crisis"


def test_price_or_count_does_not_suppress_injection():
    # review R19 finding 1: $2000 / a bare large count are NOT years — injection must still fire
    assert inject_recency("current iPhone price under $2000", "2026-06-13") == \
        "current iPhone price under $2000 2026"
    assert inject_recency("fastest server at 2048 requests", "2026-06-13") == \
        "fastest server at 2048 requests 2026"


def test_historical_or_timeless_query_is_not_injected():
    # review R19 findings 3/7/9: a historical/definitional sub-query of a time-sensitive brief
    # must not be poisoned with the current year
    for q in ("history of the federal funds rate",
              "federal funds rate definition",
              "fed funds rate trends in the 1970s",
              "meaning of quantitative tightening"):
        assert inject_recency(q, "2026-06-13") == q, q


def test_noop_when_not_time_sensitive():
    # a stable-fact query must not get a (meaningless, possibly harmful) year appended
    assert inject_recency("capital city of Australia", "2026-06-13", time_sensitive=False) == \
        "capital city of Australia"


def test_noop_on_empty_or_unparseable_today():
    assert inject_recency("", "2026-06-13") == ""
    assert inject_recency("   ", "2026-06-13") == ""
    assert inject_recency("latest rate", "") == "latest rate"          # no year to inject
    assert inject_recency("latest rate", "not-a-date") == "latest rate"


def test_noop_when_appending_would_overflow_query_cap():
    # search truncates to 300 chars; a year tacked past that would just be cut off → don't bother
    long_q = "x" * 299
    assert inject_recency(long_q, "2026-06-13") == long_q             # 299 + " 2026" > 300
    fits = "y" * 294
    assert inject_recency(fits, "2026-06-13") == fits + " 2026"        # 294 + 5 == 299 <= 300


def test_year_parsed_from_iso_today_with_leading_space():
    assert inject_recency("breaking market news", "  2026-06-13").endswith(" 2026")


def test_year_parsed_from_non_iso_today():
    # review R19 finding 8: _year_of must not depend on the year being the FIRST token — a
    # non-ISO `today` would otherwise silently disable the whole lever
    assert inject_recency("latest market rate", "June 13, 2026").endswith(" 2026")
    assert inject_recency("latest market rate", "13 Jun 2026").endswith(" 2026")


# ----------------------------------------------------------------------------- is_breaking
def test_breaking_detects_happening_now_signals():
    assert is_breaking("breaking: what just happened in the markets")
    assert is_breaking("live updates on the election right now")
    assert is_breaking("the fire developing situation as of today")
    assert is_breaking("what is happening now at the summit")
    assert is_breaking("today's announcement from the central bank")


def test_breaking_is_strict_subset_excludes_mild_recency():
    # 'latest'/'current'/'recent' are handled by the CHEAP year injection, not the expensive
    # depth bump — so they must NOT trip is_breaking (else every dated query doubles its compute)
    for q in ("what is the latest stable Python version",
              "current US federal funds rate",
              "most recent iPhone model",
              "recent developments in fusion energy"):
        assert is_time_sensitive(q), q          # still time-sensitive (gets year injection)
        assert not is_breaking(q), q            # but NOT breaking (no depth bump)


def test_breaking_false_for_stable_facts():
    assert not is_breaking("who wrote Pride and Prejudice")
    assert not is_breaking("history of the Roman Empire")


# ----------------------------------------------------------------------------- _bumped_depth
def test_breaking_brief_bumps_depth_one_notch():
    from council.researcher import ResearchWorker
    assert ResearchWorker(worker_id="m", model="m", depth="quick")._bumped_depth(
        "live updates on the vote right now") == "standard"
    assert ResearchWorker(worker_id="m", model="m", depth="standard")._bumped_depth(
        "breaking: markets just crashed") == "deep"
    assert ResearchWorker(worker_id="m", model="m", depth="deep")._bumped_depth(
        "breaking news") == "deep"   # capped at deep


def test_non_breaking_brief_keeps_configured_depth():
    from council.researcher import ResearchWorker
    w = ResearchWorker(worker_id="m", model="m", depth="standard")
    assert w._bumped_depth("what is the latest stable python version") == "standard"
    assert w._bumped_depth("history of the roman empire") == "standard"


def test_depth_not_bumped_when_breaking_but_not_time_sensitive():
    # review R19 finding 4: is_breaking has false positives ('developing story', 'live updates',
    # 'this morning') that are NOT time-sensitive — a 'quick' caller must not be silently pushed
    # into an extra refine round by an incidental word. Bump requires time-sensitive AND breaking.
    from council.research import is_breaking, is_time_sensitive
    from council.researcher import ResearchWorker
    for brief in ("developing story structure in screenwriting",
                  "live updates plugin for wordpress",
                  "this morning yoga flow routine"):
        assert is_breaking(brief), brief                 # the false-positive surface...
        assert not is_time_sensitive(brief), brief       # ...but no genuine recency intent
        assert ResearchWorker(worker_id="m", model="m", depth="quick")._bumped_depth(brief) \
            == "quick", brief                            # so depth is NOT escalated


def test_bump_uses_angle_too():
    from council.researcher import ResearchWorker
    w = ResearchWorker(worker_id="m", model="m", depth="standard", angle="breaking developments")
    assert w._bumped_depth("a neutral brief") == "deep"


# ----------------------------------------------------------------------------- wiring into research()
def test_research_injects_year_into_web_query(monkeypatch):
    """A time-sensitive brief → the web query that hits search_structured carries the year."""
    from council import researcher as R
    seen = []
    monkeypatch.setattr(R, "search_structured",
                        lambda q, max_results=5, engine="web": (seen.append((engine, q)) or []))
    monkeypatch.setattr(R.ResearchWorker, "_plan_queries",
                        lambda self, brief: ["US federal funds rate decision"])
    w = R.ResearchWorker(worker_id="m", model="m", scope="web", depth="quick",
                         page_evidence=False, today="2026-06-13")
    w.research("what is the current US federal funds rate")
    assert any(eng == "web" and q.endswith(" 2026") for eng, q in seen), seen


def test_research_no_year_for_stable_brief(monkeypatch):
    from council import researcher as R
    seen = []
    monkeypatch.setattr(R, "search_structured",
                        lambda q, max_results=5, engine="web": (seen.append((engine, q)) or []))
    monkeypatch.setattr(R.ResearchWorker, "_plan_queries",
                        lambda self, brief: ["capital city of Australia"])
    w = R.ResearchWorker(worker_id="m", model="m", scope="web", depth="quick",
                         page_evidence=False, today="2026-06-13")
    w.research("what is the capital city of Australia")
    assert seen and all(not q.endswith(" 2026") for eng, q in seen), seen


def test_year_injected_for_web_only_not_central_apis(monkeypatch):
    """arXiv/Wikipedia are relevance/full-text — a bare year pollutes them, so injection is web-only."""
    from council import researcher as R
    seen = []
    monkeypatch.setattr(R, "search_structured",
                        lambda q, max_results=5, engine="web": (seen.append((engine, q)) or []))
    monkeypatch.setattr(R, "route_engines", lambda q: ["web", "encyclopedic"])
    monkeypatch.setattr(R.ResearchWorker, "_plan_queries",
                        lambda self, brief: ["who is the current UN secretary general"])
    w = R.ResearchWorker(worker_id="m", model="m", scope="web", depth="quick",
                         page_evidence=False, today="2026-06-13")
    w.research("who is the current UN secretary general")
    web_qs = [q for eng, q in seen if eng == "web"]
    enc_qs = [q for eng, q in seen if eng == "encyclopedic"]
    assert web_qs and all(q.endswith(" 2026") for q in web_qs), seen
    assert enc_qs and all(not q.endswith(" 2026") for q in enc_qs), seen
