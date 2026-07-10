#!/usr/bin/env python3
"""
council/ollama.py — one Ollama client for every generation call
================================================================
Single source of truth for the Ollama endpoint. `PW_OLLAMA_BASE` lets every heavy call — the
analysts (`researcher`), the editor/judge (`judge`), the network answer worker (`worker`), and the
batch shard worker (`batch`) — target a remote / GPU host.

Before this module those four each hardcoded ``OLLAMA_BASE = "http://localhost:11434"`` and only
model *detection* honored ``PW_OLLAMA_BASE`` — so pointing it at a GPU box listed that box's models
and then failed every generation against localhost (D48). Routing them all through `base()` /
`generate()` fixes that in one place and removes four near-identical HTTP clients.
"""

from __future__ import annotations

import os

import requests

DEFAULT_BASE = "http://localhost:11434"


def base() -> str:
    """The Ollama endpoint every call should use, resolved from ``PW_OLLAMA_BASE`` at call/instance
    time (so a process can target a remote host without editing code). Trailing slash stripped."""
    return (os.environ.get("PW_OLLAMA_BASE") or DEFAULT_BASE).rstrip("/")


def keep_alive() -> str:
    """Keep models warm across a run (R17); ``PW_OLLAMA_KEEP_ALIVE="0"`` unloads immediately."""
    return os.environ.get("PW_OLLAMA_KEEP_ALIVE", "30m")


def smallest_chat_model(base_url: str | None = None) -> str:
    """The smallest installed non-embedding model — cheap for auxiliary calls like listwise
    reranking (``council.rerank``). ``PW_SMALL_MODEL`` overrides; ``''`` if none reachable.
    Resolved at call time against ``base_url`` (or ``base()``) so a remote host is honored."""
    override = os.environ.get("PW_SMALL_MODEL", "")
    if override:
        return override
    try:
        r = requests.get(f"{(base_url or base()).rstrip('/')}/api/tags", timeout=10)
        models = [m for m in r.json().get("models", []) if "embed" not in m["name"].lower()]
        return sorted(models, key=lambda m: m.get("size", 0))[0]["name"]
    except Exception:
        return ""


def resolve_timeout(env_primary: str | None, default: float) -> float:
    """A generation timeout from ``env_primary`` (if set) else ``PW_OLLAMA_TIMEOUT`` else `default`.
    An empty/invalid value falls through instead of raising (previously an empty env var crashed)."""
    for key in (env_primary, "PW_OLLAMA_TIMEOUT"):
        if key:
            val = os.environ.get(key)
            if val:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return float(default)


def generate(prompt: str, *, model: str, base_url: str | None = None,
             temperature: float = 0.0, num_predict: int | None = None,
             timeout: float | None = None, timeout_env: str | None = None,
             timeout_default: float = 300.0) -> tuple[str, int]:
    """POST a non-streaming ``/api/generate`` and return ``(text, tokens)``.

    `text` is the model's response, stripped; `tokens` is Ollama's ``eval_count`` (0 if absent —
    callers that want a word-count fallback apply it themselves). `base_url` defaults to `base()`.
    Timeout precedence: explicit `timeout` → `timeout_env`/`PW_OLLAMA_TIMEOUT` → `timeout_default`.
    Raises ``requests.HTTPError`` on a non-2xx response (callers already handle that)."""
    options: dict = {"temperature": temperature}
    if num_predict is not None:
        options["num_predict"] = num_predict
    r = requests.post(
        f"{(base_url or base()).rstrip('/')}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False,
              "options": options, "keep_alive": keep_alive()},
        timeout=timeout if timeout is not None else resolve_timeout(timeout_env, timeout_default),
    )
    r.raise_for_status()
    d = r.json()
    return (d.get("response") or "").strip(), int(d.get("eval_count") or 0)
