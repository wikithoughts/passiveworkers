"""passiveworkers/net/agent._fix_hint — maps task-execution exceptions to actionable hints instead of
raw exception text (R10 review): the worker daemon's own console is often the only place an
operator ever sees a failure. Pure function, no network."""

import requests

import passiveworkers.net.agent as A


def test_fix_hint_connection_refused():
    exc = requests.exceptions.ConnectionError("Connection refused")
    assert "ollama serve" in A._fix_hint(exc).lower()


def test_fix_hint_model_not_pulled_404():
    exc = RuntimeError("404 Client Error: Not Found for url: http://x/api/generate")
    assert "isn't pulled" in A._fix_hint(exc).lower()
    assert "ollama pull" in A._fix_hint(exc)


def test_fix_hint_timeout():
    exc = requests.exceptions.Timeout("timed out")
    assert "timed out" in A._fix_hint(exc).lower()


def test_fix_hint_falls_back_to_raw_message():
    exc = ValueError("something unexpected")
    hint = A._fix_hint(exc)
    assert "ValueError" in hint and "something unexpected" in hint
