#!/usr/bin/env python3
"""
passiveworkers/net/ratelimit.py — a small in-process sliding-window rate limiter (D36)
==============================================================================
Bounds abuse on the coordinator's creation/mint endpoints (register, user signup, job submit,
progress) now that the network is moving toward a public launch — a single leaked operator token,
or unauthenticated user-minting, must not let one client flood the queue / inflate the ledger.

Deliberately tiny + dependency-free (one coordinator process today; swap for Redis behind the same
`allow()` seam when horizontally scaled). Per-key sliding window: at most `limit` events per
`window_s`. `limit <= 0` disables that key (operator escape hatch). Memory is bounded — a denied
event is NOT recorded (so a key's deque never exceeds `limit`), and once the table grows past
`max_keys` it is swept — at most once per `_SWEEP_EVERY_S` (F16 review: an unthrottled O(n) sweep
ran on EVERY call while the table stayed over the cap) — dropping idle keys first, then the
least-recently-hit keys until it is back at `max_keys`.
"""

from __future__ import annotations

import heapq
import threading
import time
from collections import deque

_SWEEP_HORIZON_S = 3600.0   # keys idle longer than this are dropped on overflow (>> any window)
_SWEEP_EVERY_S = 10.0       # at most one sweep per this many seconds, however far over the cap


class RateLimiter:
    def __init__(self, max_keys: int = 50_000):
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._max_keys = max_keys
        self._last_sweep = float("-inf")

    def allow(self, key: str, limit: int, window_s: float, now: float | None = None) -> bool:
        """True if an event for `key` is within `limit` per `window_s` (and records it); False if it
        would exceed the limit (NOT recorded). `limit <= 0` → always allowed (disabled).
        `now` is injectable for deterministic tests."""
        if limit <= 0:
            return True
        t = time.time() if now is None else now
        cutoff = t - window_s
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                dq = deque()
                self._hits[key] = dq
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= limit:
                return False              # denied — do not record, so the deque stays bounded
            dq.append(t)
            # abs(): a clock that stepped backwards must not suppress sweeping until it catches up
            if len(self._hits) > self._max_keys and abs(t - self._last_sweep) >= _SWEEP_EVERY_S:
                self._last_sweep = t
                self._sweep(t)
            return True

    def _sweep(self, now: float) -> None:
        """Drop keys that are empty or idle past the horizon (>> any real window, so a swept key's
        limit window has certainly elapsed). If that still leaves the table over `max_keys` (key
        rotation keeps every key fresh), evict the least-recently-hit keys down to the cap: memory
        stays bounded, and an evicted key only loses its own history, so it is never MORE limited."""
        horizon = now - _SWEEP_HORIZON_S
        for k in [k for k, d in self._hits.items() if not d or d[-1] < horizon]:
            del self._hits[k]
        excess = len(self._hits) - self._max_keys
        if excess > 0:
            for k in heapq.nsmallest(excess, self._hits, key=lambda k: self._hits[k][-1]):
                del self._hits[k]
