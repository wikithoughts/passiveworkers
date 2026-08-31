"""tests/test_ollama.py — passiveworkers/ollama.py, the single shared HTTP client every generation call
(worker/researcher/judge/batch/rerank) goes through.

Covers: `base()` resolution for both the default Ollama backend and the OpenAI-compatible
backend (PW_INFERENCE_BACKEND=openai), `generate()`'s request shape on each backend (mocking
`requests.post` and asserting on call args, following tests/test_doctor.py's fake-response
convention), `resolve_timeout()` precedence, and that the new config knobs are registered in
`passiveworkers.config.KNOWN`.
"""
from __future__ import annotations

import passiveworkers.config as C
import passiveworkers.ollama as O


class _FakeResp:
    """Minimal stand-in for `requests.Response` (mirrors tests/test_doctor.py's _FakeGenResp)."""

    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


# ---- base() ----------------------------------------------------------------------------------

def test_base_default_is_localhost_ollama(monkeypatch):
    monkeypatch.delenv("PW_OLLAMA_BASE", raising=False)
    monkeypatch.delenv("PW_INFERENCE_BACKEND", raising=False)
    assert O.base() == "http://localhost:11434"


def test_base_honors_pw_ollama_base_override(monkeypatch):
    monkeypatch.delenv("PW_INFERENCE_BACKEND", raising=False)
    monkeypatch.setenv("PW_OLLAMA_BASE", "http://gpu-box:11434/")
    assert O.base() == "http://gpu-box:11434"          # trailing slash stripped


def test_base_openai_backend_resolves_from_inference_api_base(monkeypatch):
    monkeypatch.setenv("PW_INFERENCE_BACKEND", "openai")
    monkeypatch.setenv("PW_INFERENCE_API_BASE", "http://localhost:8080/")
    assert O.base() == "http://localhost:8080"          # trailing slash stripped


def test_base_openai_backend_with_no_api_base_set_is_empty_not_a_crash(monkeypatch):
    monkeypatch.setenv("PW_INFERENCE_BACKEND", "openai")
    monkeypatch.delenv("PW_INFERENCE_API_BASE", raising=False)
    assert O.base() == ""                                # no hardcoded default for openai


def test_backend_default_and_normalization(monkeypatch):
    monkeypatch.delenv("PW_INFERENCE_BACKEND", raising=False)
    assert O.backend() == "ollama"
    monkeypatch.setenv("PW_INFERENCE_BACKEND", " OpenAI ")
    assert O.backend() == "openai"


# ---- generate() --------------------------------------------------------------------------------

def test_generate_default_backend_posts_to_api_generate(monkeypatch):
    monkeypatch.delenv("PW_INFERENCE_BACKEND", raising=False)
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        return _FakeResp({"response": " hello ", "eval_count": 7})

    monkeypatch.setattr(O.requests, "post", fake_post)
    text, tokens = O.generate("hi", model="m1", base_url="http://x:11434", num_predict=64)

    assert (text, tokens) == ("hello", 7)
    assert len(calls) == 1
    url, body, _timeout = calls[0]
    assert url == "http://x:11434/api/generate"
    assert body["model"] == "m1"
    assert body["prompt"] == "hi"
    assert body["stream"] is False
    assert body["options"]["num_predict"] == 64
    assert "keep_alive" in body


def test_generate_openai_backend_posts_to_chat_completions(monkeypatch):
    monkeypatch.setenv("PW_INFERENCE_BACKEND", "openai")
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        return _FakeResp({
            "choices": [{"message": {"content": " world "}}],
            "usage": {"completion_tokens": 12},
        })

    monkeypatch.setattr(O.requests, "post", fake_post)
    text, tokens = O.generate("hi", model="m2", base_url="http://localhost:8080",
                               temperature=0.5, num_predict=32)

    assert (text, tokens) == ("world", 12)
    assert len(calls) == 1
    url, body, _timeout = calls[0]
    assert url == "http://localhost:8080/v1/chat/completions"
    assert body["model"] == "m2"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 32


def test_generate_openai_backend_missing_fields_fall_back_to_zero_and_empty(monkeypatch):
    monkeypatch.setenv("PW_INFERENCE_BACKEND", "openai")
    monkeypatch.setattr(O.requests, "post",
                         lambda url, json=None, timeout=None: _FakeResp({"choices": [{"message": {}}]}))
    text, tokens = O.generate("hi", model="m3", base_url="http://x")
    assert (text, tokens) == ("", 0)                    # mirrors the Ollama path's 0-if-absent contract


# ---- resolve_timeout() -------------------------------------------------------------------------

def test_resolve_timeout_prefers_env_primary(monkeypatch):
    monkeypatch.setenv("PW_JUDGE_TIMEOUT", "12")
    monkeypatch.setenv("PW_OLLAMA_TIMEOUT", "99")
    assert O.resolve_timeout("PW_JUDGE_TIMEOUT", 300.0) == 12.0


def test_resolve_timeout_falls_back_to_pw_ollama_timeout(monkeypatch):
    monkeypatch.delenv("PW_JUDGE_TIMEOUT", raising=False)
    monkeypatch.setenv("PW_OLLAMA_TIMEOUT", "45")
    assert O.resolve_timeout("PW_JUDGE_TIMEOUT", 300.0) == 45.0


def test_resolve_timeout_falls_back_to_default_when_unset(monkeypatch):
    monkeypatch.delenv("PW_JUDGE_TIMEOUT", raising=False)
    monkeypatch.delenv("PW_OLLAMA_TIMEOUT", raising=False)
    assert O.resolve_timeout("PW_JUDGE_TIMEOUT", 300.0) == 300.0


def test_resolve_timeout_falls_back_to_default_on_invalid_value(monkeypatch):
    # an empty/invalid value falls through instead of raising
    monkeypatch.delenv("PW_JUDGE_TIMEOUT", raising=False)
    monkeypatch.setenv("PW_OLLAMA_TIMEOUT", "not-a-number")
    assert O.resolve_timeout(None, 300.0) == 300.0


def test_resolve_timeout_with_no_env_primary_key(monkeypatch):
    monkeypatch.setenv("PW_OLLAMA_TIMEOUT", "7")
    assert O.resolve_timeout(None, 300.0) == 7.0


# ---- config registration ------------------------------------------------------------------------

def test_config_known_includes_inference_backend_keys():
    assert "PW_INFERENCE_BACKEND" in C.KNOWN
    assert "PW_INFERENCE_API_BASE" in C.KNOWN
