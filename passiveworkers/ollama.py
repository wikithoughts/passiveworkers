#!/usr/bin/env python3
"""
passiveworkers/ollama.py — one Ollama client for every generation call
================================================================
Single source of truth for the Ollama endpoint. `PW_OLLAMA_BASE` lets every heavy call — the
analysts (`researcher`), the editor/judge (`judge`), the network answer worker (`worker`), and the
batch shard worker (`batch`) — target a remote / GPU host.

Before this module those four each hardcoded ``OLLAMA_BASE = "http://localhost:11434"`` and only
model *detection* honored ``PW_OLLAMA_BASE`` — so pointing it at a GPU box listed that box's models
and then failed every generation against localhost (D48). Routing them all through `base()` /
`generate()` fixes that in one place and removes four near-identical HTTP clients.

Inference backend: ``PW_INFERENCE_BACKEND`` selects which HTTP API shape this module speaks —
``ollama`` (default) for Ollama's native ``/api/generate`` + ``/api/tags``, or ``openai`` for the
OpenAI-compatible ``/v1/chat/completions`` (+ ``/v1/models``) shape that both llama.cpp-server and
LM Studio expose out of the box as of 2026. On ``openai`` the endpoint comes from
``PW_INFERENCE_API_BASE`` (no hardcoded default — there is no sane guess for an arbitrary
llama.cpp-server/LM Studio port the way ``localhost:11434`` is for Ollama). This is purely a
local-inference-runtime choice; it does not touch D1/D4/D18. Known/intentional limitation: only
callers that go through *this module's* `generate()` / `base()` / `smallest_chat_model()` get the
new backend for free (`worker`, `researcher`, `judge`, `batch`, `rerank`). Other call sites that
talk to Ollama directly elsewhere in the codebase (e.g. `passiveworkers/local.py`, `passiveworkers/library.py`,
`passiveworkers/doctor.py`, `passiveworkers/net/agent.py`, `passiveworkers/net/baseline.py`, `passiveworkers/operator.py`) are
NOT routed through this dispatch and remain Ollama-only — that's an intentional scope boundary for
now, not an oversight.
"""

from __future__ import annotations

import os

import requests

DEFAULT_BASE = "http://localhost:11434"


def backend() -> str:
    """Which inference API shape to speak: ``ollama`` (default) or ``openai``, from
    ``PW_INFERENCE_BACKEND``. Normalized (stripped, lowercased) so ``" OpenAI "`` still matches."""
    return (os.environ.get("PW_INFERENCE_BACKEND") or "ollama").strip().lower()


def base() -> str:
    """The inference endpoint every call should use. On the default ``ollama`` backend, resolved
    from ``PW_OLLAMA_BASE`` (or `DEFAULT_BASE`) at call/instance time (so a process can target a
    remote host without editing code). On the ``openai`` backend, resolved from
    ``PW_INFERENCE_API_BASE`` — there is no hardcoded default for it (no sane guess for an
    arbitrary llama.cpp-server/LM Studio port); if unset, an empty string is returned and the
    subsequent `requests` call fails naturally with a clear connection error, matching this
    module's existing style of not pre-validating env vars. Trailing slash stripped."""
    if backend() == "openai":
        return (os.environ.get("PW_INFERENCE_API_BASE") or "").rstrip("/")
    return (os.environ.get("PW_OLLAMA_BASE") or DEFAULT_BASE).rstrip("/")


def keep_alive() -> str:
    """Keep models warm across a run (R17); ``PW_OLLAMA_KEEP_ALIVE="0"`` unloads immediately."""
    return os.environ.get("PW_OLLAMA_KEEP_ALIVE", "30m")


def smallest_chat_model(base_url: str | None = None) -> str:
    """The smallest installed non-embedding model — cheap for auxiliary calls like listwise
    reranking (``passiveworkers.rerank``). ``PW_SMALL_MODEL`` overrides; ``''`` if none reachable.
    Resolved at call time against ``base_url`` (or ``base()``) so a remote host is honored.

    On the ``openai`` backend this queries ``/v1/models`` (supported by both llama.cpp-server and
    LM Studio) and picks the first entry in ``data["data"]`` — that endpoint carries no per-model
    size, so there is no "smallest" to sort by; any lookup failure (unreachable host, no models,
    malformed response) falls through to the same ``except Exception: return ""`` used by the
    Ollama path."""
    override = os.environ.get("PW_SMALL_MODEL", "")
    if override:
        return override
    resolved = (base_url or base()).rstrip("/")
    try:
        if backend() == "openai":
            r = requests.get(f"{resolved}/v1/models", timeout=10)
            return r.json()["data"][0]["id"]
        r = requests.get(f"{resolved}/api/tags", timeout=10)
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


def _generate_ollama(prompt: str, *, model: str, base_url: str | None,
                      temperature: float, num_predict: int | None,
                      timeout: float | None, timeout_env: str | None,
                      timeout_default: float) -> tuple[str, int]:
    """POST a non-streaming ``/api/generate`` and return ``(text, tokens)``.

    `text` is the model's response, stripped; `tokens` is Ollama's ``eval_count`` (0 if absent —
    callers that want a word-count fallback apply it themselves)."""
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


def _generate_openai(prompt: str, *, model: str, base_url: str | None,
                      temperature: float, num_predict: int | None,
                      timeout: float | None, timeout_env: str | None,
                      timeout_default: float) -> tuple[str, int]:
    """POST a non-streaming ``/v1/chat/completions`` (OpenAI-compatible: llama.cpp-server, LM
    Studio) and return ``(text, tokens)``.

    `text` is ``choices[0].message.content``, stripped; `tokens` is
    ``usage.completion_tokens`` (0 if absent — mirrors the Ollama path's "0 if absent"
    error-tolerance contract exactly)."""
    body: dict = {"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": temperature}
    if num_predict is not None:
        body["max_tokens"] = num_predict
    r = requests.post(
        f"{(base_url or base()).rstrip('/')}/v1/chat/completions",
        json=body,
        timeout=timeout if timeout is not None else resolve_timeout(timeout_env, timeout_default),
    )
    r.raise_for_status()
    d = r.json()
    text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    tokens = d.get("usage", {}).get("completion_tokens", 0)
    return text.strip(), int(tokens or 0)


def generate(prompt: str, *, model: str, base_url: str | None = None,
             temperature: float = 0.0, num_predict: int | None = None,
             timeout: float | None = None, timeout_env: str | None = None,
             timeout_default: float = 300.0) -> tuple[str, int]:
    """Generate a completion and return ``(text, tokens)``, dispatching to the configured
    `backend()` — Ollama's native ``/api/generate`` (default) or an OpenAI-compatible
    ``/v1/chat/completions`` (``PW_INFERENCE_BACKEND=openai``).

    `text` is the model's response, stripped; `tokens` is 0 if the backend didn't report a count
    (callers that want a word-count fallback apply it themselves). `base_url` defaults to `base()`.
    Timeout precedence: explicit `timeout` → `timeout_env`/`PW_OLLAMA_TIMEOUT` → `timeout_default`.
    Raises ``requests.HTTPError`` on a non-2xx response (callers already handle that)."""
    impl = _generate_openai if backend() == "openai" else _generate_ollama
    return impl(prompt, model=model, base_url=base_url, temperature=temperature,
                num_predict=num_predict, timeout=timeout, timeout_env=timeout_env,
                timeout_default=timeout_default)
