"""_extract_json must never crash callers on bad-shape model output (the bug class that
bit deliberate()/compare() — a model returning a bare list)."""
from passiveworkers.judge import _extract_json


def test_object():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_embedded_object():
    assert _extract_json('blah {"a": 1} trailing') == {"a": 1}


def test_bare_list_does_not_become_dict():
    # callers guard with isinstance; the parser must at least not raise
    out = _extract_json('["x", "y"]')
    assert out == ["x", "y"] or out is None


def test_garbage_returns_none_or_safe():
    assert _extract_json("not json at all") in (None, {}, [])


def test_empty():
    assert _extract_json("") in (None, {}, [])
