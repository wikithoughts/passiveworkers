"""Shared test helpers.

The star here is a `requests`→FastAPI-`TestClient` shim: it lets the REAL operator/asker CLI client
code in `passiveworkers.operator` / `passiveworkers.net.submit` (which call `requests.get/post`) run against the REAL
coordinator app with no socket — high-value integration coverage the repo previously lacked. Monkeypatch
a module's `requests` with `RequestsShim(client, base)` and its network calls hit the TestClient.
"""
from __future__ import annotations

import importlib
import sys
import threading

import pytest


class _ShimResp:
    """A requests-Response-shaped wrapper over an httpx (TestClient) response — exposes exactly the
    attributes the client code touches: status_code, ok, text, content, headers, json(), raise_for_status()."""

    def __init__(self, r):
        self._r = r

    @property
    def status_code(self):
        return self._r.status_code

    @property
    def ok(self):
        return self._r.status_code < 400        # match requests.Response.ok (2xx+3xx), not httpx is_success (2xx)

    @property
    def text(self):
        return self._r.text

    @property
    def content(self):
        return self._r.content

    @property
    def headers(self):
        return self._r.headers

    def json(self):
        return self._r.json()

    def raise_for_status(self):
        self._r.raise_for_status()


class RequestsShim:
    """Drop-in for the `requests` module that routes GET/POST to a TestClient. Translates the two
    requests idioms the client code uses: an absolute coordinator URL → app path, and `data=<str|bytes>`
    (a raw body, as requests sends it) → httpx `content=`. `timeout` is dropped (irrelevant in-process)."""

    def __init__(self, client, base: str):
        self.client = client
        self.base = base.rstrip("/")

    def _path(self, url: str) -> str:
        return url[len(self.base):] if url.startswith(self.base) else url

    def _kw(self, kw: dict) -> dict:
        kw = dict(kw)
        kw.pop("timeout", None)
        data = kw.pop("data", None)
        if data is not None:
            kw["content"] = data   # requests data=<str/bytes> is a raw body → httpx content=
        return kw

    def get(self, url, **kw):
        return _ShimResp(self.client.get(self._path(url), **self._kw(kw)))

    def post(self, url, **kw):
        return _ShimResp(self.client.post(self._path(url), **self._kw(kw)))


@pytest.fixture(autouse=True)
def _no_real_baseline_threads(monkeypatch):
    """Never start the coordinator's REAL baseline thread in tests. Every POST /jobs starts a
    `pw-baseline` daemon thread (net/coordinator_app.py) that polls for up to 900 s, tries a local
    Ollama on loopback and then prints. One still running when the suite ends can print into
    interpreter shutdown, and CPython aborts with "Fatal Python error: _enter_buffered_busy"
    (test (3.10), PR #48, CI run 38124448503). Setting PW_BASELINE_LOCAL_MODEL="" is not enough:
    the thread still starts, polls and prints. A module-level patch would not survive the
    importlib.reload() calls many tests make, so the guard sits on Thread.start and checks the
    module's CURRENT _baseline_async. Raising RuntimeError uses _start_baseline's own "can't start
    a thread" path, which releases the slot. Tests that swap in their own _baseline_async
    (tests/test_coordinator_limits.py) still get real threads."""
    real_start = threading.Thread.start

    def start(self):
        if self.name == "pw-baseline":
            capp = sys.modules.get("passiveworkers.net.coordinator_app")
            fn = getattr(capp, "_baseline_async", None)
            if getattr(fn, "__module__", None) == "passiveworkers.net.coordinator_app":
                raise RuntimeError("the real baseline thread is disabled in tests")
        return real_start(self)
    monkeypatch.setattr(threading.Thread, "start", start)


@pytest.fixture
def shim_factory():
    """Returns `make(client, base) -> RequestsShim` to patch onto a module's `requests`."""
    def make(client, base="http://coord"):
        return RequestsShim(client, base)
    return make


@pytest.fixture
def coord_client(tmp_path, monkeypatch):
    """A TestClient bound to a fresh coordinator (temp DB, token 'tok', 100 starter credits) — the same
    app+store the shimmed operator client will hit, so seeded state and client calls stay consistent."""
    monkeypatch.setenv("PW_DB", str(tmp_path / "coord.db"))
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    for m in ("passiveworkers.ledger", "passiveworkers.net.config", "passiveworkers.net.store", "passiveworkers.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))
    import passiveworkers.net.coordinator_app as capp
    from fastapi.testclient import TestClient
    return TestClient(capp.app)
