"""Tamper-evident result digest (FEDERATION_V2 trust step 1)."""
from council.net.store import Store


def test_digest_is_order_independent():
    assert Store.result_digest({"a": 1, "b": 2}) == Store.result_digest({"b": 2, "a": 1})


def test_digest_changes_on_tamper():
    assert Store.result_digest({"text": "hi"}) != Store.result_digest({"text": "hi."})


def test_digest_is_sha256_hex():
    d = Store.result_digest({"x": 1})
    assert len(d) == 64 and all(c in "0123456789abcdef" for c in d)
