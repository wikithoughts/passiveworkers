"""Out-of-band operator key trust — TOFU + explicit pinning (D25)."""
import os
import stat

import pytest

from passiveworkers import trust


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    # trust._store_path() reads PW_LIBRARY_DIR at call time → just point it at a temp dir
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    return tmp_path


# a couple of realistic-looking base64 Ed25519 verify keys (32 bytes → 44 b64 chars)
KEY_A = "ABwQ8nF2k0pXq5sZ1d3e4f5g6h7i8j9k0lAbCdEfGhI="
KEY_B = "ZZ9y8x7w6v5u4t3s2r1q0pOnMlKjIhGfEdCbA0987654="


def test_fingerprint_is_deterministic_and_formatted():
    fp = trust.fingerprint(KEY_A)
    assert fp == trust.fingerprint(KEY_A)            # deterministic
    assert fp.startswith("PW-")
    groups = fp[3:].split("-")
    assert len(groups) == 4 and all(len(g) == 4 for g in groups)   # PW-XXXX-XXXX-XXXX-XXXX


def test_fingerprint_distinguishes_keys_and_handles_empty():
    assert trust.fingerprint(KEY_A) != trust.fingerprint(KEY_B)
    assert trust.fingerprint("") == ""
    # a non-base64 string must not crash (falls back to raw bytes)
    assert trust.fingerprint("not!base64!").startswith("PW-")


def test_pin_get_remove_roundtrip():
    rec = trust.pin("alice", KEY_A, source="manual")
    assert rec["pub"] == KEY_A and rec["fp"] == trust.fingerprint(KEY_A) and rec["source"] == "manual"
    assert trust.get("alice")["pub"] == KEY_A
    assert "alice" in trust.list_pins()
    assert trust.remove("alice") is True
    assert trust.get("alice") is None
    assert trust.remove("alice") is False          # idempotent removal


def test_classify_states():
    assert trust.classify("alice", "")[0] == trust.NO_KEY        # unsigned
    assert trust.classify("alice", KEY_A)[0] == trust.UNPINNED   # first contact
    trust.pin("alice", KEY_A)
    status, existing = trust.classify("alice", KEY_A)
    assert status == trust.PINNED_MATCH and existing["pub"] == KEY_A
    status, existing = trust.classify("alice", KEY_B)            # coordinator presents a NEW key
    assert status == trust.MISMATCH and existing["pub"] == KEY_A  # pin is unchanged → caller refuses


def test_classify_never_auto_repins_a_mismatch():
    trust.pin("alice", KEY_A)
    trust.classify("alice", KEY_B)            # a mismatch must NOT silently rotate the pin
    assert trust.get("alice")["pub"] == KEY_A


def test_pin_requires_handle_and_key():
    with pytest.raises(ValueError):
        trust.pin("", KEY_A)
    with pytest.raises(ValueError):
        trust.pin("alice", "")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_trust_store_is_owner_only():
    trust.pin("alice", KEY_A)
    mode = stat.S_IMODE(os.stat(trust._store_path()).st_mode)
    assert mode == 0o600


def test_corrupt_store_is_preserved_not_silently_wiped():
    # a garbled trust.json must not silently vanish (which would drop every pin)
    path = trust._store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json ")
    assert trust.get("alice") is None                       # load survives corruption
    assert (path.parent / (path.name + ".corrupt")).exists()  # but the bad file is kept for recovery
    # and a subsequent pin starts a fresh, usable store
    trust.pin("alice", KEY_A)
    assert trust.get("alice")["pub"] == KEY_A


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_save_fallback_still_enforces_owner_only(monkeypatch):
    # if the atomic os.open path fails, the fallback must STILL chmod 0600 (a trust anchor is
    # never world-readable) — regression for the insecure-fallback finding
    real_open = os.open
    monkeypatch.setattr(trust.os, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    trust.pin("alice", KEY_A)
    monkeypatch.setattr(trust.os, "open", real_open)
    mode = stat.S_IMODE(os.stat(trust._store_path()).st_mode)
    assert mode == 0o600
    assert trust.get("alice")["pub"] == KEY_A


# --------------------------------------------------------------- fetch() verification helper (D25)
def _vds(view, merged="x"):
    from passiveworkers.operator import _verify_delivery_signature
    return _verify_delivery_signature(view, merged)


def _signed_view(operator, priv, signer_pub, payload="payload"):
    crypto = pytest.importorskip("passiveworkers.crypto")
    return {"operator": operator, "signer_pub": signer_pub,
            "signature": crypto.sign(priv, payload.encode())}, payload


def test_fetch_refuses_unsigned_delivery_from_pinned_operator():
    trust.pin("alice", KEY_A)                                # we know alice signs
    ok, code = _vds({"operator": "alice", "signature": None, "signer_pub": None})
    assert ok is False and code == 1                         # an unsigned delivery from her is a downgrade


def test_fetch_refuses_signed_delivery_with_no_operator_handle():
    ok, code = _vds({"operator": "", "signature": "sig", "signer_pub": "key"})
    assert ok is False and code == 1                         # nothing to anchor trust on


def test_fetch_unsigned_unpinned_is_allowed_but_unverified():
    ok, code = _vds({"operator": "bob", "signature": None, "signer_pub": None})
    assert ok is True and code == 0                          # honest TOFU floor (warns, doesn't block)


def test_fetch_refuses_signed_when_crypto_absent(monkeypatch):
    crypto = pytest.importorskip("passiveworkers.crypto")
    if not crypto.available():
        pytest.skip("crypto extra not installed")
    priv, pub = crypto.new_signing_keypair()
    view, payload = _signed_view("alice", priv, pub)
    monkeypatch.setattr(crypto, "available", lambda: False)  # asker lacks the extra
    ok, code = _vds(view, payload)
    assert ok is False and code == 2                         # refuse, never silently downgrade


def test_fetch_tofu_pins_only_a_valid_signature():
    crypto = pytest.importorskip("passiveworkers.crypto")
    if not crypto.available():
        pytest.skip("crypto extra not installed")
    priv, pub = crypto.new_signing_keypair()
    view, payload = _signed_view("alice", priv, pub)
    ok, code = _vds(view, payload)
    assert ok is True and code == 0
    assert trust.get("alice")["pub"] == pub                 # first contact pinned (TOFU)


def test_fetch_does_not_pin_an_invalid_signature():
    crypto = pytest.importorskip("passiveworkers.crypto")
    if not crypto.available():
        pytest.skip("crypto extra not installed")
    priv, pub = crypto.new_signing_keypair()
    view, _ = _signed_view("alice", priv, pub, payload="real")
    ok, code = _vds(view, "TAMPERED")                        # verify against different content
    assert ok is False and code == 1
    assert trust.get("alice") is None                       # a bad signature must NOT be pinned


def test_fetch_refuses_pinned_operator_key_swap():
    crypto = pytest.importorskip("passiveworkers.crypto")
    if not crypto.available():
        pytest.skip("crypto extra not installed")
    good_priv, good_pub = crypto.new_signing_keypair()
    trust.pin("alice", good_pub, source="manual")           # pinned out of band
    evil_priv, evil_pub = crypto.new_signing_keypair()
    view, payload = _signed_view("alice", evil_priv, evil_pub)  # coordinator swaps the key
    ok, code = _vds(view, payload)
    assert ok is False and code == 1                         # refused before verifying
    assert trust.get("alice")["pub"] == good_pub            # pin unchanged


def test_pinned_key_defeats_coordinator_key_swap():
    """The D25 security property (closes the D23 gap): once an operator is pinned out of band, a
    hostile coordinator cannot forge a delivery for them. This mirrors how fetch() composes
    classify() with crypto.verify()."""
    crypto = pytest.importorskip("passiveworkers.crypto")
    if not crypto.available():
        pytest.skip("crypto extra not installed")
    # honest operator's stable signing key, pinned out of band by the asker
    priv, pub = crypto.new_signing_keypair()
    trust.pin("alice", pub, source="manual")
    deliverable = b"the real, signed deliverable"
    sig = crypto.sign(priv, deliverable)
    # honest path: pinned match + valid signature → trusted
    status, existing = trust.classify("alice", pub)
    assert status == trust.PINNED_MATCH and crypto.verify(existing["pub"], deliverable, sig)
    # attack 1 — coordinator substitutes ITS OWN key and re-signs: classify refuses pre-verify
    evil_priv, evil_pub = crypto.new_signing_keypair()
    assert trust.classify("alice", evil_pub)[0] == trust.MISMATCH
    # attack 2 — coordinator keeps reporting the pinned key but tampers content: signature fails
    assert not crypto.verify(pub, b"tampered deliverable", sig)
