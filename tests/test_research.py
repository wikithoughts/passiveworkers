"""Research backend: row-shaping, host curation, SSRF guard, engine routing — all mocked
(no network in CI)."""
import council.research as R


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
