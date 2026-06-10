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
import socket
import time
from functools import lru_cache
from urllib.parse import urlparse

_TIMEOUT = float(os.environ.get("PW_WEB_TIMEOUT", "8"))
_MAX_RESULTS = int(os.environ.get("PW_WEB_RESULTS", "5"))
_BACKEND = os.environ.get("PW_WEB_BACKEND", "off")
_SEARX = os.environ.get("PW_SEARXNG_URL", "")
_UA = "PassiveWorkers-Research/0.1 (mutual-aid council; egress-localized)"


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
        if not host or host in seen or not _host_is_public(host):
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
    with DDGS(timeout=int(_TIMEOUT)) as ddg:
        # region world → engines localize on THIS node's egress IP (the moat).
        return list(ddg.text(question, region="wt-wt", safesearch="moderate",
                             max_results=_MAX_RESULTS))


def _searxng(question: str) -> list[dict]:
    import requests
    host = urlparse(_SEARX).hostname or ""
    # SearXNG may legitimately run on loopback ON this node; allow that explicitly.
    if not (_host_is_public(host) or host in ("127.0.0.1", "localhost")):
        return []
    r = requests.get(f"{_SEARX.rstrip('/')}/search",
                     params={"q": question, "format": "json", "safesearch": 1},
                     headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json().get("results", [])


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
def _cached(question: str, _bucket: int) -> str:
    if _BACKEND == "searxng":
        rows = _searxng(question)
    else:
        rows = _ddgs(question)
    found = _clean(rows)
    return found or _wikipedia_fallback(question)


def search(question: str) -> str:
    """The web_search hook: (question) -> findings text. Best-effort; '' on any failure."""
    if _BACKEND == "off":
        return ""
    q = (question or "").strip()[:300]
    if not q:
        return ""
    try:
        bucket = int(time.time() // 900)   # 15-minute cache window via the lru_cache key
        return _cached(q, bucket)
    except Exception:
        return ""


def search_structured(query: str, max_results: int = 5) -> list[dict]:
    """Structured variant for the researcher: [{title, url, host, snippet}], SSRF-guarded,
    deduped by host. Best-effort; [] on any failure. Same egress-localization as search()."""
    if _BACKEND == "off":
        return []
    q = (query or "").strip()[:300]
    if not q:
        return []
    try:
        rows = _searxng(q) if _BACKEND == "searxng" else _ddgs(q)
    except Exception:
        return []
    out, seen = [], set()
    for r in rows:
        url = (r.get("href") or r.get("url") or "").strip()
        host = (urlparse(url).hostname or "").lower()
        if not host or host in seen or not _host_is_public(host):
            continue
        seen.add(host)
        out.append({"title": (r.get("title") or "").strip()[:160],
                    "url": url,
                    "host": host,
                    "snippet": (r.get("body") or r.get("content") or "").strip()[:500]})
        if len(out) >= max_results:
            break
    return out
