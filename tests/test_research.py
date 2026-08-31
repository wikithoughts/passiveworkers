"""Research backend: row-shaping, host curation, SSRF guard, engine routing — all mocked
(no network in CI)."""
import passiveworkers.research as R


def test_quality_host_filter():
    assert R._is_quality_host("reuters.com")
    assert not R._is_quality_host("youtube.com")
    assert not R._is_quality_host("m.youtube.com")  # subdomain suffix match


def test_ssrf_guard_blocks_private(monkeypatch):
    assert R._host_is_public("example.com") in (True, False)  # depends on DNS; just no crash
    # loopback/metadata must be rejected without network
    import pytest
    with pytest.raises(Exception):
        R.fetch_extract("http://169.254.169.254/latest/meta-data/")


def test_search_structured_shapes_and_dedups(monkeypatch):
    monkeypatch.setenv("PW_WEB_BACKEND", "ddgs")
    monkeypatch.setattr(R, "_searxng_alive", lambda: False)
    rows = [
        {"title": "A", "href": "https://good.example/a", "body": "alpha"},
        {"title": "B", "href": "https://good.example/b", "body": "beta"},   # same host → dropped
        {"title": "Y", "href": "https://youtube.com/x", "body": "vid"},     # low-quality → dropped
        {"title": "C", "href": "https://other.example/c", "body": "gamma"},
    ]
    monkeypatch.setattr(R, "_ddgs", lambda q: rows)
    monkeypatch.setattr(R, "_host_is_public", lambda h: True)
    out = R.search_structured("q", max_results=5)
    hosts = [r["host"] for r in out]
    assert "good.example" in hosts and "other.example" in hosts
    assert hosts.count("good.example") == 1 and "youtube.com" not in hosts
    assert all(set(r) >= {"title", "url", "host", "snippet"} for r in out)


def test_extract_main_falls_back_without_trafilatura(monkeypatch):
    # even if trafilatura is absent, regex strip returns text
    text, date = R._extract_main("<html><body><p>Hello world</p><script>x</script></body></html>")
    assert "Hello world" in text and "x" not in text


# --------------------------------------------------------- SSRF redirect hardening (D34)
class _Raw:
    def __init__(self, data):
        self._d = data

    def read(self, n=-1, decode_content=True):
        return self._d


class _Resp:
    def __init__(self, location=None, body=b"<html><body><p>ok</p></body></html>"):
        self.is_redirect = location is not None
        self.is_permanent_redirect = False
        self.headers = {"Location": location} if location else {}
        self.raw = _Raw(body)

    def raise_for_status(self):
        pass

    def close(self):
        pass


def test_guarded_get_blocks_redirect_to_internal(monkeypatch):
    # a public URL must NOT be able to 30x-redirect to a metadata/private address (SSRF)
    import pytest
    import requests
    monkeypatch.setattr(R, "_host_is_public", lambda h: h != "169.254.169.254")
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp(location="http://169.254.169.254/latest/"))
    with pytest.raises(ValueError):
        R._guarded_get("http://good.example/page", timeout=5)


def test_guarded_get_returns_final_non_redirect(monkeypatch):
    import requests
    monkeypatch.setattr(R, "_host_is_public", lambda h: True)
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp())
    r = R._guarded_get("http://good.example/x", timeout=5)
    assert r.is_redirect is False


def test_guarded_get_bounds_redirect_chains(monkeypatch):
    import pytest
    import requests
    monkeypatch.setattr(R, "_host_is_public", lambda h: True)   # all public, but endless redirects
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp(location="http://good.example/next"))
    with pytest.raises(ValueError):
        R._guarded_get("http://good.example/start", timeout=5)


def test_fetch_extract_blocks_redirect_to_metadata(monkeypatch):
    import pytest
    import requests
    monkeypatch.setattr(R, "_host_is_public", lambda h: h != "169.254.169.254")
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp(location="http://169.254.169.254/"))
    with pytest.raises(ValueError):
        R.fetch_extract("http://good.example/page")


def test_fetch_extract_happy_path_through_guarded_get(monkeypatch):
    import requests
    monkeypatch.setattr(R, "_host_is_public", lambda h: True)
    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: _Resp(body=b"<html><body><p>Hello SSRF-safe</p></body></html>"))
    assert "Hello SSRF-safe" in R.fetch_extract("http://good.example/page")


# --------------------------------------------------------- keyed web backends (R33/D49)
def _row(name):
    return [{"title": name, "href": "https://good.example/x", "body": name}]


# --------------------------------------------------------- SearXNG compose/LAN reachability (R6)
def test_searxng_reaches_private_compose_hostname(monkeypatch):
    # A self-hosted SearXNG legitimately lives on a Docker-compose bridge network (a private IP)
    # or a bare service name like "searxng" — PW_SEARXNG_URL is operator-supplied trusted config,
    # not attacker-influenced, so it must NOT be rejected by the public-only SSRF guard.
    import requests

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": "hit"}]}

    monkeypatch.setenv("PW_SEARXNG_URL", "http://searxng:8080")
    # "searxng" only resolves INSIDE an actual compose network — mock DNS to simulate that,
    # since the point of this test is the guard logic, not a live compose network.
    monkeypatch.setattr(R.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("172.19.0.2", 0))])
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    out = R._searxng("q")
    assert out and out[0]["title"] == "hit"


def test_searxng_rejects_garbage_url(monkeypatch):
    monkeypatch.setenv("PW_SEARXNG_URL", "not-a-url")
    assert R._searxng("q") == []


def test_searxng_rejects_non_http_scheme(monkeypatch):
    monkeypatch.setenv("PW_SEARXNG_URL", "ftp://searxng:8080")
    assert R._searxng("q") == []


# --------------------------------------------------------- content-type gate (R14 review)
def test_fetch_extract_skips_pdf_content_type(monkeypatch):
    import pytest
    import requests
    monkeypatch.setattr(R, "_host_is_public", lambda h: True)
    resp = _Resp(body=b"%PDF-1.4 binary garbage")
    resp.headers = {"Content-Type": "application/pdf"}
    monkeypatch.setattr(requests, "get", lambda url, **kw: resp)
    with pytest.raises(ValueError):
        R.fetch_extract("http://good.example/doc.pdf")


def test_fetch_extract_allows_text_plain(monkeypatch):
    import requests
    monkeypatch.setattr(R, "_host_is_public", lambda h: True)
    resp = _Resp(body=b"plain text body")
    resp.headers = {"Content-Type": "text/plain; charset=utf-8"}
    monkeypatch.setattr(requests, "get", lambda url, **kw: resp)
    assert "plain text body" in R.fetch_extract("http://good.example/page.txt")


def test_fetch_extract_allows_missing_content_type(monkeypatch):
    # some legitimately HTML-serving hosts omit Content-Type — stay permissive (regression-pin
    # the existing happy-path behavior, which relies on this).
    import requests
    monkeypatch.setattr(R, "_host_is_public", lambda h: True)
    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: _Resp(body=b"<html><body><p>Hello</p></body></html>"))
    assert "Hello" in R.fetch_extract("http://good.example/page")


# --------------------------------------------------------- DDG circuit breaker (R17 review)
def test_ddg_does_not_sleep_after_final_failed_attempt(monkeypatch):
    R.reset_ddg_breaker()
    sleeps = []
    monkeypatch.setattr(R.time, "sleep", lambda s: sleeps.append(s))

    class _BoomDDGS:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, *a, **k):
            raise RuntimeError("429")

    import sys
    import types
    fake_mod = types.ModuleType("ddgs")
    fake_mod.DDGS = _BoomDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)

    import pytest
    with pytest.raises(RuntimeError):
        R._ddgs("q")
    assert len(sleeps) == 2   # 3 attempts, sleep only between 1->2 and 2->3, never after the 3rd


def test_ddg_breaker_opens_after_n_consecutive_failures(monkeypatch):
    R.reset_ddg_breaker()
    monkeypatch.setenv("PW_DDG_BREAKER", "2")
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class _BoomDDGS:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, *a, **k):
            calls["n"] += 1
            raise RuntimeError("429")

    import sys
    import types
    fake_mod = types.ModuleType("ddgs")
    fake_mod.DDGS = _BoomDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)

    import pytest
    with pytest.raises(RuntimeError):
        R._ddgs("q1")   # streak -> 1
    with pytest.raises(RuntimeError):
        R._ddgs("q2")   # streak -> 2, breaker now open
    n_before = calls["n"]
    with pytest.raises(RuntimeError, match="breaker open"):
        R._ddgs("q3")   # short-circuits — no DDGS call at all
    assert calls["n"] == n_before   # confirms the short-circuit never constructed DDGS


def test_ddg_breaker_resets_on_success(monkeypatch):
    R.reset_ddg_breaker()
    monkeypatch.setenv("PW_DDG_BREAKER", "1")
    monkeypatch.setattr(R.time, "sleep", lambda s: None)

    class _OkDDGS:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, *a, **k):
            return [{"title": "T"}]

    import sys
    import types
    fake_mod = types.ModuleType("ddgs")
    fake_mod.DDGS = _OkDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)

    R._ddgs("q")           # success resets the streak
    R._ddgs("q2")          # would raise "breaker open" if the streak hadn't reset


def test_reset_ddg_breaker_clears_streak(monkeypatch):
    R._ddg_fail_streak = 5
    R.reset_ddg_breaker()
    assert R._ddg_fail_streak == 0


def test_ddg_breaker_auto_clears_after_cooldown(monkeypatch):
    # Workflow-review finding: a long-lived process (the pworkers join/pworkers work agent daemon) never
    # calls reset_ddg_breaker() between tasks by design — without a cooldown, 3 failures spread
    # across a node's ENTIRE uptime would disable DDG PERMANENTLY, worse than no breaker at all.
    R.reset_ddg_breaker()
    monkeypatch.setenv("PW_DDG_BREAKER", "1")
    monkeypatch.setenv("PW_DDG_BREAKER_COOLDOWN", "10")
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class _BoomDDGS:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, *a, **k):
            calls["n"] += 1
            raise RuntimeError("429")

    import sys
    import types
    fake_mod = types.ModuleType("ddgs")
    fake_mod.DDGS = _BoomDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)

    clock = {"t": 0.0}
    monkeypatch.setattr(R.time, "monotonic", lambda: clock["t"])

    import pytest
    with pytest.raises(RuntimeError):
        R._ddgs("q1")   # streak -> 1, breaker opens, opened_at = 0.0
    with pytest.raises(RuntimeError, match="breaker open"):
        R._ddgs("q2")   # still within cooldown (t=0) -> short-circuits, no DDGS call
    n_before_cooldown = calls["n"]

    clock["t"] = 11.0   # advance past the 10s cooldown
    with pytest.raises(RuntimeError):
        R._ddgs("q3")   # cooldown elapsed -> streak reset -> retries for real (and fails again)
    assert calls["n"] > n_before_cooldown   # confirms it actually retried, not just re-raised


def test_keyed_backend_selected(monkeypatch):
    monkeypatch.setenv("PW_WEB_BACKEND", "brave")
    monkeypatch.setattr(R, "_searxng_alive", lambda: False)
    monkeypatch.setattr(R, "_host_is_public", lambda h: True)
    seen = []
    monkeypatch.setattr(R, "_brave", lambda q: (seen.append("brave"), _row("brave"))[1])
    monkeypatch.setattr(R, "_ddgs", lambda q: (seen.append("ddgs"), _row("ddgs"))[1])
    out = R.search_structured("q", max_results=5)
    assert seen == ["brave"] and out[0]["title"] == "brave"     # keyed primary used, DDG untouched


def test_ddgs_failure_falls_back_to_configured_keyed(monkeypatch):
    monkeypatch.setenv("PW_WEB_BACKEND", "ddgs")
    monkeypatch.setenv("PW_TAVILY_KEY", "tvly-x")               # a key is present → eligible fallback
    monkeypatch.setattr(R, "_searxng_alive", lambda: False)
    monkeypatch.setattr(R, "_host_is_public", lambda h: True)
    seen = []
    monkeypatch.setattr(R, "_ddgs", lambda q: (seen.append("ddgs"), (_ for _ in ()).throw(RuntimeError("429")))[1])
    monkeypatch.setattr(R, "_tavily", lambda q: (seen.append("tavily"), _row("tavily"))[1])
    out = R.search_structured("q", max_results=5)
    assert seen == ["ddgs", "tavily"] and out[0]["title"] == "tavily"


def test_empty_result_does_not_trigger_paid_fallback(monkeypatch):
    # a legitimate EMPTY result (no exception) must NOT cascade to a paid keyed backend
    monkeypatch.setenv("PW_WEB_BACKEND", "ddgs")
    monkeypatch.setenv("PW_BRAVE_KEY", "b")
    monkeypatch.setattr(R, "_searxng_alive", lambda: False)
    seen = []
    monkeypatch.setattr(R, "_ddgs", lambda q: (seen.append("ddgs"), [])[1])
    monkeypatch.setattr(R, "_brave", lambda q: (seen.append("brave"), _row("brave"))[1])
    R.search_structured("q", max_results=5)
    assert seen == ["ddgs"]      # brave never called on a legit empty


def test_keyed_primary_without_key_degrades_to_ddgs(monkeypatch):
    monkeypatch.setenv("PW_WEB_BACKEND", "brave")
    monkeypatch.delenv("PW_BRAVE_KEY", raising=False)           # selected but no key
    monkeypatch.delenv("PW_TAVILY_KEY", raising=False)
    monkeypatch.delenv("PW_SERPER_KEY", raising=False)
    monkeypatch.setattr(R, "_searxng_alive", lambda: False)
    monkeypatch.setattr(R, "_host_is_public", lambda h: True)
    seen = []
    monkeypatch.setattr(R, "_ddgs", lambda q: (seen.append("ddgs"), _row("ddgs"))[1])
    out = R.search_structured("q", max_results=5)
    assert seen == ["ddgs"] and out[0]["title"] == "ddgs"      # _brave raises (no key) → DDG floor


def test_keyed_result_still_ssrf_filtered(monkeypatch):
    # a hostile keyed API returning a loopback URL must be dropped by the existing post-processing
    monkeypatch.setenv("PW_WEB_BACKEND", "serper")
    monkeypatch.setattr(R, "_searxng_alive", lambda: False)
    monkeypatch.setattr(R, "_serper", lambda q: [
        {"title": "ok", "href": "https://1.1.1.1/a", "body": "public"},
        {"title": "evil", "href": "http://127.0.0.1:8080/admin", "body": "ssrf"},
    ])
    out = R.search_structured("q", max_results=5)
    hosts = [r["host"] for r in out]
    assert hosts == ["1.1.1.1"]        # loopback dropped, public kept (real _host_is_public, offline)


def test_keyed_fns_raise_without_key(monkeypatch):
    import pytest
    for fn, env in [(R._brave, "PW_BRAVE_KEY"), (R._tavily, "PW_TAVILY_KEY"), (R._serper, "PW_SERPER_KEY")]:
        monkeypatch.delenv(env, raising=False)
        with pytest.raises(Exception):
            fn("q")


def test_fallback_chain_ordering(monkeypatch):
    monkeypatch.setenv("PW_BRAVE_KEY", "b")
    monkeypatch.setenv("PW_SERPER_KEY", "s")
    monkeypatch.delenv("PW_TAVILY_KEY", raising=False)
    assert R._fallback_chain("ddgs") == ["brave", "serper"]        # keyed-with-key, no ddgs (it's primary)
    assert R._fallback_chain("brave") == ["serper", "ddgs"]        # exclude self; ddgs floor appended
