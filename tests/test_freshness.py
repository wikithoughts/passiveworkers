"""R18/D30 freshness-biased research: date-hint sniffing, recency ordering, and date-aware
research prompts. Pure logic + mocked Ollama (no network)."""
from passiveworkers.research import extract_date_hint, is_time_sensitive, order_by_recency


# ----------------------------------------------------------------------------- extract_date_hint
def test_date_hint_iso_in_url_or_text():
    assert extract_date_hint("https://x.com/2026/06/13/story") == "2026-06-13"
    assert extract_date_hint("https://x.com/a", "published 2026-06-10") == "2026-06-10"


def test_date_hint_month_name_forms():
    assert extract_date_hint("", "June 10, 2026 release") == "2026-06-10"
    assert extract_date_hint("", "released 10 June 2026") == "2026-06-10"
    assert extract_date_hint("", "as of Aug 2026") == "2026-08"


def test_date_hint_url_year_month_and_url_bare_year():
    assert extract_date_hint("https://x.com/2026/06/a") == "2026-06"
    assert extract_date_hint("https://x.com/2025/report") == "2025"   # bare year OK from a URL path


def test_date_hint_ignores_bare_year_in_prose():
    # review R18: a bare year in free text is usually a TOPIC year, not the publish date
    assert extract_date_hint("https://news.site/article", "a 2026 review of the 2008 crisis") == ""
    assert extract_date_hint("https://x.com/a", "sometime in 2025 things changed") == ""


def test_date_hint_rejects_impossible_days():
    # review R18: malformed dates like 2026-02-30 must never be emitted (they corrupt the sort)
    assert extract_date_hint("", "30 February 2026") == "2026-02"   # falls back to month-year
    assert extract_date_hint("", "31 April 2026") == "2026-04"
    assert "-32" not in extract_date_hint("", "32 Jan 2026")


def test_date_hint_none():
    assert extract_date_hint("https://x.com/page", "no dates here") == ""
    assert extract_date_hint("https://x.com/v1234", "id 9999") == ""   # not a 20xx year


def test_date_hint_prefers_most_precise():
    # full ISO beats a bare year present in the same blob
    assert extract_date_hint("https://x.com/a", "2026-06-13 (year 2024 archive)") == "2026-06-13"


# ----------------------------------------------------------------------------- order_by_recency
def test_order_leads_with_freshest_undated_last_stable():
    ev = [
        {"url": "https://x/2019/03/a", "snippet": "old"},              # 2019-03 (url path)
        {"url": "u2", "snippet": "no date here"},                      # undated
        {"url": "https://x/2026/06/13/p", "snippet": "fresh"},         # 2026-06-13
        {"url": "u4", "snippet": "background, also no date"},          # undated
        {"url": "https://x/2026/05/r", "snippet": "may report"},       # 2026-05
    ]
    out = order_by_recency(ev)
    assert out[0]["url"] == "https://x/2026/06/13/p"     # freshest first
    assert out[1]["url"] == "https://x/2026/05/r"         # 2026-05 next
    assert out[2]["url"] == "https://x/2019/03/a"         # 2019 next
    assert [e["url"] for e in out[3:]] == ["u2", "u4"]    # undated last, original order preserved


def test_order_prefers_real_date_over_hint_and_snippet():
    ev = [
        {"url": "https://x/2020/01/a", "snippet": "old", "date_hint": "2020-01"},
        {"url": "https://y/old", "snippet": "stuff", "date": "2026-06-12"},  # fetched real date wins
    ]
    out = order_by_recency(ev)
    assert out[0]["url"] == "https://y/old"               # real 2026 date outranks the 2020 hint


# ----------------------------------------------------------------------------- is_time_sensitive (recency gate)
def test_time_sensitive_detects_recency_intent():
    assert is_time_sensitive("what is the latest stable Python version")
    assert is_time_sensitive("the next EU AI Act enforcement milestone and its date")
    assert is_time_sensitive("current US federal funds rate")
    assert is_time_sensitive("most recently released iPhone")


def test_time_sensitive_false_for_stable_facts():
    # the eval's static control questions must NOT trigger recency reordering
    assert not is_time_sensitive("what is the chemical symbol and atomic number of gold")
    assert not is_time_sensitive("who wrote Pride and Prejudice and in what year")
    assert not is_time_sensitive("what is the capital city of Australia")


def test_time_sensitive_detects_recency_intent_arabic():
    # R30: scoped Arabic keyword coverage — additive alongside the English alternation
    assert is_time_sensitive("ما هو أحدث إصدار من نظام التشغيل")        # "latest/newest version"
    assert is_time_sensitive("ما هو السعر الحالي للذهب اليوم")          # "current price ... today"
    assert is_time_sensitive("متى الموعد النهائي لتقديم الطلبات")       # "when ... deadline"


def test_time_sensitive_false_for_stable_facts_arabic():
    # an unrelated Arabic sentence with no temporal keywords must NOT trigger recency reordering
    assert not is_time_sensitive("الباندا حيوان يعيش في الغابات ويأكل الخيزران")
    assert not is_time_sensitive("قواعد اللغة العربية تحتوي على أبواب كثيرة للنحو والصرف")


# ----------------------------------------------------------------------------- date-aware prompts
def test_plan_prompt_includes_today(monkeypatch):
    from passiveworkers.researcher import ResearchWorker
    captured = {}
    monkeypatch.setattr(ResearchWorker, "_generate",
                        lambda self, prompt, num_predict: (captured.update(p=prompt) or ('["a","b","c"]', 3)))
    ResearchWorker(worker_id="m", model="m", today="2026-06-13")._plan_queries("brief")
    assert "2026-06-13" in captured["p"]


def test_draft_prompt_includes_today_and_recency_rule(monkeypatch):
    from passiveworkers.researcher import ResearchWorker
    captured = {}
    monkeypatch.setattr(ResearchWorker, "_generate",
                        lambda self, prompt, num_predict: (captured.update(p=prompt) or ("draft", 5)))
    ev = [{"title": "T", "host": "h", "url": "u", "snippet": "s", "date": "2026-06-10"}]
    ResearchWorker(worker_id="m", model="m", today="2026-06-13")._draft("brief", ev)
    p = captured["p"]
    assert "2026-06-13" in p and "MOST RECENT" in p
    assert "2026-06-10" in p          # the source's date is shown to the model
    assert "training-time memory" in p
