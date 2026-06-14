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


_MAX_REDIRECTS = 3


def _guarded_get(url: str, timeout: float):
    """A streaming GET that re-validates the host on the INITIAL request AND on EVERY redirect hop.
    A public URL must never be able to 30x-redirect to a loopback/private/CGNAT/metadata address —
    so we disable requests' automatic redirects and follow them manually, bounded and re-checked.
    Raises ValueError on any non-http(s) or non-public hop, or on too many hops. Used for ANY
    asker-influenced fetch (page evidence, shard_map fetch:true, the download_extract task type).
    Residual (documented, Phase-3+): getaddrinfo runs again inside requests, so a DNS-rebinding
    attacker could in principle swap a public→private address between this check and the connect
    (TOCTOU); the allowlist narrows it, full IP-pinning needs a custom connector."""
    import requests
    cur = url
    for _hop in range(_MAX_REDIRECTS + 1):
        host = (urlparse(cur).hostname or "").lower()
        if not cur.startswith(("http://", "https://")) or not _host_is_public(host):
            raise ValueError(f"blocked non-public URL hop: {cur[:80]}")
        r = requests.get(cur, headers={"User-Agent": _UA}, timeout=timeout, stream=True,
                         allow_redirects=False)
        if r.is_redirect or r.is_permanent_redirect:
            loc = r.headers.get("Location") or ""
            r.close()
            if not loc:
                raise ValueError("redirect with no Location")
            cur = requests.compat.urljoin(cur, loc)   # resolve relative → re-checked next loop
            continue
        return r
    raise ValueError("too many redirects")


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


# ---- dynamic source routing (R17/D29): pick which keyless engines a query should hit ----
# academic signals → also query arXiv; definitional/background signals → also query Wikipedia.
# 'web' (egress-localized meta-search — the moat) is ALWAYS included; the extras AUGMENT it,
# they never replace it. Pure + env-gated (PW_SOURCE_ROUTING=off → web only).
_ACADEMIC_RE = re.compile(
    r"\b(arxiv|preprint|peer.?reviewed|research papers?|journal article|study|studies|"
    r"meta.?analysis|clinical trial|systematic review|algorithm|theorem|equation|dataset|"
    r"benchmark|state.of.the.art|sota|neural network|machine learning|deep learning|"
    r"reinforcement learning|transformer|quantum|genomic|proteomic|astrophysics)\b", re.I)
# 'who/what is/are' only counts at the start (or after sentence punctuation) so a mid-sentence
# 'what is it like' in prose doesn't over-route; the explicit phrases match anywhere.
_ENCYCLOPEDIC_RE = re.compile(
    r"(?:^|[.!?]\s+)(?:who|what)\s+(?:is|are|was|were)\b"
    r"|\b(?:definition of|meaning of|history of|biography of|overview of|background on|"
    r"capital of|where is)\b", re.I)


def route_engines(query: str) -> list[str]:
    """Ordered keyless engines for a query. Always starts with 'web' (the egress-localized
    moat); the extras AUGMENT it, never replace it. Appends 'academic' (arXiv) and/or
    'encyclopedic' (Wikipedia) when the query clearly calls for them. NOTE: arXiv/Wikipedia are
    CENTRAL APIs — they do not geo-localize (no egress moat) and are not deanonymizing beyond an
    ordinary HTTP request. PW_SOURCE_ROUTING=off|0|false (case-insensitive) pins it to web only."""
    if os.environ.get("PW_SOURCE_ROUTING", "on").lower() in ("off", "0", "false"):
        return ["web"]
    q = query or ""
    engines = ["web"]
    if _ACADEMIC_RE.search(q):
        engines.append("academic")
    if _ENCYCLOPEDIC_RE.search(q):
        engines.append("encyclopedic")
    return engines


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


# ---- freshness signals (R18/D30): the council's edge is currency, so lead with recent sources ----
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_DAY = r"(0?[1-9]|[12]\d|3[01])"                                  # 1-31 only (no 0/32+)
_ISO_RE = re.compile(r"(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])")
_URL_YM_RE = re.compile(r"/(20\d{2})/(0[1-9]|1[0-2])(?:/|\b)")
_URL_Y_RE = re.compile(r"/(20[1-3]\d)(?:/|\b)")
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
_MON_DMY = re.compile(rf"(?i)\b{_DAY}\s+{_MON}[a-z]*\.?,?\s+(20\d{{2}})\b")
_MON_MDY = re.compile(rf"(?i)\b{_MON}[a-z]*\.?\s+{_DAY},?\s+(20\d{{2}})\b")
_MON_MY = re.compile(rf"(?i)\b{_MON}[a-z]*\.?\s+(20\d{{2}})\b")

# does a brief/query actually care about recency? If not, recency reordering is noise (and could
# bury an authoritative older source under a recent repost), so we leave relevance order alone.
_TEMPORAL_RE = re.compile(
    r"(?i)\b(current|currently|latest|newest|recent|recently|now|today|as of|up.?to.?date|"
    r"most recent|breaking|so far|to date|right now|when|next|upcoming|deadline|date|"
    r"this (year|month|week)|202\d)\b")


def _valid_ymd(y, mo, d) -> bool:
    try:
        import datetime
        datetime.date(int(y), int(mo), int(d))
        return True
    except (ValueError, TypeError):
        return False


def extract_date_hint(url: str, text: str = "") -> str:
    """Best-effort publication date as 'YYYY-MM-DD' / 'YYYY-MM' / 'YYYY' (most precise first),
    or '' if none. A freshness RANKING signal, not an authority claim. Trust is stratified
    (review R18): URL-path dates are intentional publication dates (trusted at all granularities);
    from free TEXT only FULL, valid dates are trusted — a bare year in prose ('the 2008 crisis')
    is usually a TOPIC year, not the publish date, so it is ignored. All full dates are validated
    (no impossible days like 2026-02-30)."""
    url, text = url or "", text or ""

    def _full(y, mo, d):
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}" if _valid_ymd(y, mo, d) else ""

    # 1) full ISO date — URL first, then text (both are precise, low ambiguity)
    for blob in (url, text):
        m = _ISO_RE.search(blob)
        if m and (v := _full(m.group(1), m.group(2), m.group(3))):
            return v
    # 2) month-name full dates — text only (URLs don't carry these), validated
    m = _MON_DMY.search(text)
    if m and (v := _full(m.group(3), _MONTHS[m.group(2).lower()[:3]], m.group(1))):
        return v
    m = _MON_MDY.search(text)
    if m and (v := _full(m.group(3), _MONTHS[m.group(1).lower()[:3]], m.group(2))):
        return v
    # 3) URL year/month (intentional path date)
    m = _URL_YM_RE.search(url)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # 4) month-year in text ('Aug 2026' — usually an as-of/publish signal, less so a topic date)
    m = _MON_MY.search(text)
    if m:
        return f"{m.group(2)}-{_MONTHS[m.group(1).lower()[:3]]:02d}"
    # 5) bare year ONLY from a URL path (a bare year in free text is too often a topic year)
    m = _URL_Y_RE.search(url)
    if m:
        return m.group(1)
    return ""


def is_time_sensitive(text: str) -> bool:
    """True when a brief/query shows recency intent — the gate for freshness reordering (R18)."""
    return bool(_TEMPORAL_RE.search(text or ""))


# ---- current-year query injection (R19/D31): the fix recency RANKING can't make ----
# R18 reorders evidence by date, but you cannot reorder a fresh source that search never
# returned. On a time-sensitive query a small planner model often OMITS the year (or hallucinates
# a STALE one), so a meta-search returns the SEO-dominant HISTORICAL page (e.g. the famous 2023
# FOMC meeting for "current federal funds rate"). Pinning the current year INTO the web query
# forces the engine to surface this year's results, which R18 then orders freshest-first. WEB
# ONLY — arXiv sorts by relevance and Wikipedia is full-text, where a bare year pollutes instead.
#
# Review R19 hardening:
#  • "Already pinned" must mean a *standalone, plausible* year — never a price ($2000), a fused
#    token (fy2025), or a bare large count (2048); else injection is silently suppressed on
#    exactly the concrete current-fact queries it targets (finding 1).
#  • A RECENTLY-stale year (hallucinated "2023") gets the current year APPENDED alongside it, not
#    no-op'd — so the engine still sees the fresh signal and R18 picks it (finding 2). We APPEND,
#    never string-replace, so a deliberately historical query is never corrupted into nonsense.
#  • Historical / timeless queries are skipped per-query, so a historical sub-query of a
#    time-sensitive brief isn't poisoned with the current year (findings 3/7/9).
#  • Accepted trade-offs (documented, not fixed): a year fused to a word (fy2025) can still get a
#    second year appended (rare; original intent preserved) [finding 5]; a literal year is a
#    soft-AND term, so an evergreen page that omits the year string can rank lower — the page
#    fetch + order_by_recency + the 12-16 evidence cap keep it in play and re-rank by real date
#    [finding 6].
_QUERY_CAP = 300   # search_structured/search truncate to this; never let the year be the cut tail
# A *standalone* calendar year — not fused to a word (fy2025), not a currency amount ($2000).
_STANDALONE_YEAR_RE = re.compile(r"(?<![\w$£€¥])((?:19|20)\d{2})(?![\w%])")
_STALE_WINDOW = 4   # a past year within N years of today reads as "recently stale" (refresh it);
                    # an older standalone year reads as a deliberate historical reference (respect).
# Query-level historical / timeless intent — pinning the current year would mis-steer it. Keyword
# based, not exhaustive; deliberately EXCLUDES "what is/are" (too common in legitimate current Qs).
_HISTORICAL_RE = re.compile(
    r"(?i)(\b(history|historical|historically|origins?|originally|founded|inception|evolution|"
    r"timeline|etymology|biography|retrospective|definition|defined\s+as)\b"
    r"|\bmeaning\s+of\b|\bover\s+the\s+years\b|\bback\s+in\b|\bsince\s+\d{4}\b|\b\d{2,4}0s\b)")


def _year_of(today: str) -> str:
    # search (not anchored match): robust to whatever _today() emits — '2026-06-13' OR
    # 'June 13, 2026' both yield '2026' (review R19, finding 8). '' when no plausible year.
    m = _STANDALONE_YEAR_RE.search(today or "")
    return m.group(1) if m else ""


def inject_recency(query: str, today: str, time_sensitive: bool = True) -> str:
    """Pin the current year into a time-sensitive WEB query so search returns CURRENT results
    instead of the SEO-dominant historical page (R19/D31) — the fix R18's recency RANKING can't
    make. No-op when: not time-sensitive (brief-level), query empty, the query shows historical/
    timeless intent, the current (or a near-future forecast) year is already present, a deliberate
    deep-historical year is present, `today` has no parseable year, or appending would exceed the
    300-char cap. A RECENTLY-stale year is kept and the current year APPENDED alongside it (never
    replaced) so the fresh signal reaches search without corrupting a historical query."""
    q = (query or "").strip()
    if not time_sensitive or not q or _HISTORICAL_RE.search(q):
        return q
    year = _year_of(today)
    if not year:
        return q
    cur = int(year)
    # plausible standalone year tokens already in the query (ignore prices / fused / out-of-range)
    years = [int(m.group(1)) for m in _STANDALONE_YEAR_RE.finditer(q)
             if 1990 <= int(m.group(1)) <= cur + 1]
    if any(y >= cur for y in years):
        return q   # current year already pinned, or a deliberate near-future (forecast) year
    if any(y < cur - _STALE_WINDOW for y in years):
        return q   # a deliberate, deep-historical year — respect it, don't append a second year
    # else: no year, or only a RECENTLY-stale year (e.g. a hallucinated "2023") → append current
    if len(q) + 1 + len(year) > _QUERY_CAP:
        return q
    return f"{q} {year}"


# ---- breaking-news auto-deepen (R19/D31): heavier retrieval for fast-moving topics ----
# A STRICTER subset of recency intent: "happening now" signals where the answer is volatile and
# SEO favors stale pages, so more queries + more page fetches earn their keep. Deliberately
# excludes plain "latest"/"current"/"recent" (those are handled by year injection above, which
# is cheap) so we don't over-deepen — and double the local compute on — every dated query.
_BREAKING_RE = re.compile(
    r"(?i)\b(breaking|just\s+(announced|released|happened|reported|now)|right\s+now|"
    r"as\s+of\s+(today|now)|today'?s|developing\s+(story|news|situation)|live\s+updates?|"
    r"happening\s+now|this\s+(morning|afternoon|evening))\b")


def is_breaking(text: str) -> bool:
    """True for the strongest 'happening now' signals — warrants a depth bump (R19). A strict
    subset of is_time_sensitive(): every breaking brief is time-sensitive, but not vice-versa."""
    return bool(_BREAKING_RE.search(text or ""))


def _pad_date(d: str) -> str:
    """'2026' -> '2026-00-00', '2026-06' -> '2026-06-00' so ISO strings compare chronologically."""
    parts = (d or "").split("-")
    while len(parts) < 3:
        parts.append("00")
    return "-".join(parts[:3])


def order_by_recency(evidence: list[dict]) -> list[dict]:
    """Reorder evidence so the most-recently-dated sources come first (using each item's
    'date' if fetched, else 'date_hint', else a sniff of url+snippet); undated items keep their
    original relative (relevance) order and sort last. Stable; returns a new list."""
    def _key(item: dict) -> str:
        d = item.get("date") or item.get("date_hint") \
            or extract_date_hint(item.get("url", ""), item.get("snippet", ""))
        return _pad_date(d) if d else ""
    return sorted(evidence, key=_key, reverse=True)


def fetch_extract(url: str, max_chars: int = 6000, with_date: bool = False):
    """One polite, SSRF-guarded fetch of a PUBLIC http(s) page → sanitized main text.
    Shared by the researcher (page evidence) and batch fetch shards. Raises on failure —
    callers treat page evidence as best-effort. with_date=True → (text, iso_date)."""
    from council.sanitize import clean
    # SSRF-guarded GET that re-validates the host on every redirect hop (a public URL must not be
    # able to bounce to an internal address). Raises on a non-public hop or too many redirects.
    r = _guarded_get(url, timeout=15)
    r.raise_for_status()
    raw = r.raw.read(_FETCH_CAP, decode_content=True).decode("utf-8", "replace")
    text, date = _extract_main(raw)
    text = clean(text)[:max_chars]
    return (text, date) if with_date else text
