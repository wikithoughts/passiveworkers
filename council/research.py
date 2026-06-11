#!/usr/bin/env python3
"""
council/research.py — per-node, egress-localized web research (M4)
=================================================================
A worker's OWN agent researches the live web FROM ITS OWN egress and returns its OWN
findings (titles + snippets + sources) as a context string. It NEVER proxies someone
else's traffic — the worker model reads these findings and writes its own answer
(the legal bright line; see council/worker.py and docs/DECISIONS D4).

Why this is the moat: DuckDuckGo/metasearch localize on the *egress IP* and then discard
it. So the Helsinki VPS and the Gulf Mac get genuinely different result sets from their own
egress — diversity no central API can replicate. The lever: leave region at world
(`wt-wt`) and let egress drive locale; never force a region.

Config (per node, via env):
  PW_WEB_BACKEND  off (default) | ddgs | searxng
  PW_SEARXNG_URL  e.g. http://127.0.0.1:8080   (only used for searxng)
  PW_WEB_RESULTS  max results (default 5) · PW_WEB_TIMEOUT  seconds (default 8)

Wire-in: council/net/agent.py passes search() as PerspectiveWorker(web_search=…) when
PW_WEB_BACKEND != off. Best-effort: returns "" on any failure (never blocks the answer).
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import time
from functools import lru_cache
from urllib.parse import urlparse

_TIMEOUT = float(os.environ.get("PW_WEB_TIMEOUT", "8"))
_MAX_RESULTS = int(os.environ.get("PW_WEB_RESULTS", "5"))
def _backend() -> str:
    # Read at CALL time, not import time — callers (e.g. council.local) may enable the
    # web after this module is imported. Auto-prefer a local SearXNG when one is up:
    # the ecosystem's converged answer to DDG rate limiting (gpt-researcher #478,
    # local-deep-research #18, open-webui, CrewAI…), and better for privacy.
    b = os.environ.get("PW_WEB_BACKEND", "off")
    if b == "ddgs" and _searxng_alive():
        return "searxng"
    return b


@lru_cache(maxsize=1)
def _searxng_alive() -> bool:
    url = os.environ.get("PW_SEARXNG_URL") or "http://127.0.0.1:8080"
    try:
        import requests
        r = requests.get(f"{url.rstrip('/')}/search",
                         params={"q": "ping", "format": "json"},
                         headers={"User-Agent": _UA}, timeout=2)
        if r.ok:
            os.environ.setdefault("PW_SEARXNG_URL", url)
            return True
    except Exception:
        pass
    return False
_SEARX = os.environ.get("PW_SEARXNG_URL", "")
_UA = "PassiveWorkers-Research/0.1 (mutual-aid council; egress-localized)"

# Source curation: hosts that are video/social/link-farm — fine for browsing, weak as
# research citations. Suffix-matched so subdomains are covered.
_LOW_QUALITY_HOSTS = (
    "youtube.com", "youtu.be", "tiktok.com", "facebook.com", "instagram.com",
    "pinterest.com", "pinterest.co.uk", "x.com", "twitter.com", "threads.net",
    "quora.com", "slideshare.net", "scribd.com",
)


def _is_quality_host(host: str) -> bool:
    return not any(host == h or host.endswith("." + h) for h in _LOW_QUALITY_HOSTS)


# ---- SSRF / abuse guard: only public hosts (block loopback/private/link-local/CGNAT/metadata).
def _host_is_public(host: str) -> bool:
    if not host:
        return False
    try:
        for _fam, _t, _p, _c, sa in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(sa[0])
            if (ip.is_loopback or ip.is_private or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
            if ip in ipaddress.ip_network("100.64.0.0/10"):  # CGNAT
                return False
        return True
    except (OSError, ValueError):
        return False


def _clean(results: list[dict]) -> str:
    out, seen = [], set()
    for r in results:
        url = (r.get("href") or r.get("url") or "").strip()
        host = (urlparse(url).hostname or "").lower()
        if not host or host in seen or not _host_is_public(host) or not _is_quality_host(host):
            continue
        seen.add(host)
        title = (r.get("title") or "").strip()
        body = (r.get("body") or r.get("content") or "").strip()[:400]
        out.append(f"- {title} ({host})\n  {body}\n  source: {url}")
        if len(out) >= _MAX_RESULTS:
            break
    return "\n".join(out)


def _ddgs(question: str) -> list[dict]:
    try:
        from ddgs import DDGS            # current package name
    except ImportError:
        from duckduckgo_search import DDGS  # older name, same API
    # DDG rate-limits aggressively at scale (systemic across the ecosystem) —
    # 3 tries with exponential backoff + jitter before giving up.
    last: Exception | None = None
    for attempt in range(3):
        try:
            with DDGS(timeout=int(_TIMEOUT)) as ddg:
                # region world → engines localize on THIS node's egress IP (the moat).
                return list(ddg.text(question, region="wt-wt", safesearch="moderate",
                                     max_results=_MAX_RESULTS))
        except Exception as e:
            last = e
            time.sleep((2 ** attempt) + (hash(question) % 7) / 10)
    raise last  # type: ignore[misc]


def _searxng(question: str) -> list[dict]:
    import requests
    searx = os.environ.get("PW_SEARXNG_URL") or _SEARX or "http://127.0.0.1:8080"
    host = urlparse(searx).hostname or ""
    # SearXNG may legitimately run on loopback ON this node; allow that explicitly.
    if not (_host_is_public(host) or host in ("127.0.0.1", "localhost")):
        return []
    r = requests.get(f"{searx.rstrip('/')}/search",
                     params={"q": question, "format": "json", "safesearch": 1},
                     headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json().get("results", [])


# ---- keyless routable engines (engine routing v1: web | academic | encyclopedic) ----
def _arxiv(question: str, max_results: int = 5) -> list[dict]:
    """arXiv's free API — for academic queries. Returns the common row shape."""
    import requests
    import xml.etree.ElementTree as ET
    r = requests.get("https://export.arxiv.org/api/query",
                     params={"search_query": f"all:{question}", "max_results": max_results,
                             "sortBy": "relevance"},
                     headers={"User-Agent": _UA}, timeout=_TIMEOUT + 4)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    rows = []
    for e in ET.fromstring(r.text).findall("a:entry", ns):
        title = (e.findtext("a:title", "", ns) or "").strip()
        url = (e.findtext("a:id", "", ns) or "").strip()
        summary = (e.findtext("a:summary", "", ns) or "").strip()
        if title and url:
            rows.append({"title": title, "href": url, "body": summary})
    return rows


def _wikipedia(question: str, max_results: int = 4) -> list[dict]:
    """Wikipedia's free full-text search API — for encyclopedic queries. Same row shape."""
    import requests
    r = requests.get("https://en.wikipedia.org/w/api.php",
                     params={"action": "query", "list": "search", "srsearch": question,
                             "srlimit": max_results, "format": "json"},
                     headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    r.raise_for_status()
    rows = []
    for hit in r.json().get("query", {}).get("search", []):
        title = hit.get("title", "")
        if title:
            rows.append({"title": title,
                         "href": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                         "body": re.sub(r"<[^>]+>", "", hit.get("snippet", ""))})
    return rows


def _wikipedia_fallback(question: str) -> str:
    """Clean official-API fallback when search yields nothing (no geo-signal, but reliable)."""
    try:
        import requests
        r = requests.get("https://en.wikipedia.org/w/api.php",
                         params={"action": "opensearch", "search": question,
                                 "limit": 3, "format": "json"},
                         headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        r.raise_for_status()
        _, titles, descs, urls = r.json()
        rows = [{"title": t, "body": d, "url": u} for t, d, u in zip(titles, descs, urls)]
        return _clean(rows)
    except Exception:
        return ""


@lru_cache(maxsize=256)
def _cached(question: str, _bucket: int, backend: str) -> str:
    if backend == "searxng":
        rows = _searxng(question)
    else:
        rows = _ddgs(question)
    found = _clean(rows)
    return found or _wikipedia_fallback(question)


def search(question: str) -> str:
    """The web_search hook: (question) -> findings text. Best-effort; '' on any failure."""
    if _backend() == "off":
        return ""
    q = (question or "").strip()[:300]
    if not q:
        return ""
    try:
        bucket = int(time.time() // 900)   # 15-minute cache window via the lru_cache key
        return _cached(q, bucket, _backend())
    except Exception:
        return ""


def search_structured(query: str, max_results: int = 5, engine: str = "web") -> list[dict]:
    """Structured variant for the researcher: [{title, url, host, snippet}], SSRF-guarded,
    deduped by host. Best-effort; [] on any failure. Same egress-localization as search().
    `engine`: web (meta-search) | academic (arXiv) | encyclopedic (Wikipedia) — keyless."""
    backend = _backend()
    if backend == "off":
        return []
    q = (query or "").strip()[:300]
    if not q:
        return []
    try:
        if engine == "academic":
            rows = _arxiv(q, max_results)
        elif engine == "encyclopedic":
            rows = _wikipedia(q, max_results)
        elif backend == "searxng":
            rows = _searxng(q)
        else:
            rows = _ddgs(q)
    except Exception:
        return []
    from council.sanitize import clean as _sanitize
    out, seen = [], set()
    for r in rows:
        url = (r.get("href") or r.get("url") or "").strip()
        host = (urlparse(url).hostname or "").lower()
        if not host or host in seen or not _host_is_public(host) or not _is_quality_host(host):
            continue
        seen.add(host)
        out.append({"title": _sanitize((r.get("title") or ""))[:160],
                    "url": url,
                    "host": host,
                    "snippet": _sanitize((r.get("body") or r.get("content") or ""))[:500]})
        if len(out) >= max_results:
            break
    return out


# ---- full-page evidence (R5/D17: the leaders draft from pages, not snippets) ----
_FETCH_CAP = 200_000   # bytes per page — extraction input, not archival
_HTML_JUNK = re.compile(r"(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>")
_TAGS = re.compile(r"(?s)<[^>]+>")


def _strip_html(html: str) -> str:
    text = _TAGS.sub(" ", _HTML_JUNK.sub(" ", html))
    return re.sub(r"\s+", " ", text).strip()


def _extract_main(html: str) -> tuple[str, str]:
    """(main_text, iso_date) from raw HTML. Prefer trafilatura (real boilerplate removal +
    metadata, Apache-2.0; see docs/PRIOR_ART.md); fall back to our regex strip if it's
    absent or yields nothing. Date is best-effort ('' when unknown)."""
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=True,
                                   favor_precision=True) or ""
        date = ""
        try:
            md = trafilatura.extract_metadata(html)
            date = (getattr(md, "date", "") or "") if md else ""
        except Exception:
            date = ""
        if text.strip():
            return text.strip(), date
    except Exception:
        pass
    return _strip_html(html), ""


def fetch_extract(url: str, max_chars: int = 6000, with_date: bool = False):
    """One polite, SSRF-guarded fetch of a PUBLIC http(s) page → sanitized main text.
    Shared by the researcher (page evidence) and batch fetch shards. Raises on failure —
    callers treat page evidence as best-effort. with_date=True → (text, iso_date)."""
    import requests
    from council.sanitize import clean
    host = (urlparse(url).hostname or "").lower()
    if not url.startswith(("http://", "https://")) or not _host_is_public(host):
        raise ValueError(f"not a public http(s) URL: {url[:80]}")
    r = requests.get(url, headers={"User-Agent": _UA}, timeout=15, stream=True)
    r.raise_for_status()
    raw = r.raw.read(_FETCH_CAP, decode_content=True).decode("utf-8", "replace")
    text, date = _extract_main(raw)
    text = clean(text)[:max_chars]
    return (text, date) if with_date else text
