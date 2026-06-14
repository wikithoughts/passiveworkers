#!/usr/bin/env python3
"""
council/net/geoip.py — offline, no-egress IP→country resolution (D43)
=====================================================================
The coordinator can VERIFY an operator's self-reported country against the IP it actually saw at
registration — but only **offline**. The project's whole ethos is no-egress, so we never send an
operator's IP to a third-party GeoIP web API. Resolution is a local lookup against an
operator-supplied MaxMind GeoLite2 `.mmdb` (pointed at by `PW_GEOIP_DB`), behind the optional
`[geoip]` extra. Without the extra OR the DB file, this silently returns "" and the coordinator
falls back to the self-reported country — geo-verification is strictly an opt-in enrichment, never
a gate, and the default behaviour is unchanged.

Guarantees: `country_for_ip` NEVER raises and NEVER dials out. Private/loopback peers (e.g. a node
behind the standard SSH tunnel) resolve to "" — geo is meaningful only for directly-reachable
operators or those behind a trusted, XFF-setting reverse proxy.
"""

from __future__ import annotations

import ipaddress
import os

_reader = None          # cached geoip2.database.Reader (opened once; thread-safe, read-only)
_reader_path = None     # the db path the cached reader was opened from


def _db_path() -> str:
    return os.environ.get("PW_GEOIP_DB", "")


def available() -> bool:
    """True iff the geoip2 extra is importable AND a readable PW_GEOIP_DB is configured."""
    path = _db_path()
    if not path or not os.path.isfile(path) or not os.access(path, os.R_OK):
        return False
    try:
        import geoip2.database  # noqa: F401
        return True
    except Exception:
        return False


def _get_reader():
    global _reader, _reader_path
    path = _db_path()
    if _reader is not None and _reader_path == path:
        return _reader
    import geoip2.database
    _reader = geoip2.database.Reader(path)
    _reader_path = path
    return _reader


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def country_for_ip(ip: str) -> str:
    """ISO-3166-alpha-2 country for a PUBLIC ip via the offline DB, or "" on anything unresolvable
    (no extra/db, private/loopback/invalid ip, lookup miss). Never raises, never makes a network call."""
    if not ip or not _is_public(ip) or not available():
        return ""
    try:
        return (_get_reader().country(ip).country.iso_code or "").upper()
    except Exception:
        return ""
