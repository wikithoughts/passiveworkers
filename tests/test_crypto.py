"""Cryptographic delivery (D23): Ed25519 signing + SealedBox encryption, and the encrypted
artifact round-trip. Skipped cleanly if the [crypto] extra isn't installed."""
import os
import pathlib

import pytest

crypto = pytest.importorskip("council.crypto")
if not crypto.available():
    pytest.skip("PyNaCl not installed", allow_module_level=True)

from council import artifacts as A


def test_sign_verify_and_tamper():
    priv, pub = crypto.new_signing_keypair()
    data = b"deliverable bytes"
    sig = crypto.sign(priv, data)
    assert crypto.verify(pub, data, sig)
    assert not crypto.verify(pub, b"tampered", sig)          # content tamper
    _, pub2 = crypto.new_signing_keypair()
    assert not crypto.verify(pub2, data, sig)                # wrong signer
    assert not crypto.verify(pub, data, "not-a-sig")         # garbage sig never raises


def test_seal_unseal_and_wrong_key():
    priv, pub = crypto.new_box_keypair()
    ct = crypto.seal(pub, b"secret")
    assert ct != b"secret"
    assert crypto.unseal(priv, ct) == b"secret"
    other, _ = crypto.new_box_keypair()
    with pytest.raises(Exception):
        crypto.unseal(other, ct)


def test_encrypted_artifact_roundtrip(tmp_path):
    priv, pub = crypto.new_box_keypair()
    src = tmp_path / "secret.bin"
    plain = os.urandom(500_000)
    src.write_bytes(plain)
    man, blobs = A.chunk_file_encrypted(str(src), lambda b: crypto.seal(pub, b))
    assert man["encrypted"] and A.verify_manifest(man)
    # stored blobs are ciphertext, not plaintext
    assert all(v != plain[:len(v)] for v in blobs.values())
    out = A.reassemble(man, lambda h: blobs.get(h), str(tmp_path / "out"),
                       decrypt=lambda ct: crypto.unseal(priv, ct))
    assert pathlib.Path(out).read_bytes() == plain


def test_encrypted_manifest_requires_decrypt(tmp_path):
    priv, pub = crypto.new_box_keypair()
    src = tmp_path / "s.bin"; src.write_bytes(os.urandom(100_000))
    man, blobs = A.chunk_file_encrypted(str(src), lambda b: crypto.seal(pub, b))
    with pytest.raises(ValueError):                          # no decrypt key → refuse
        A.reassemble(man, lambda h: blobs.get(h), str(tmp_path / "o"))


def test_keypair_persistence(tmp_path):
    p = tmp_path / "k.json"
    a = crypto.load_or_create(p, "sign")
    b = crypto.load_or_create(p, "sign")
    assert a == b and a["priv"] and a["pub"]                 # stable across loads


def test_keyfile_is_owner_only(tmp_path):
    import os, stat
    p = tmp_path / "k.json"
    crypto.load_or_create(p, "box")
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode & 0o077 == 0                                 # no group/other access


def test_sign_truncated_payload_parity():
    # the operator must sign EXACTLY the bytes that are stored/verified (>200k text case)
    priv, pub = crypto.new_signing_keypair()
    payload = ("X" * 250000)[:200_000]
    sig = crypto.sign(priv, payload.encode())
    assert crypto.verify(pub, payload.encode(), sig)


def test_b64_strict_rejects_whitespace():
    priv, pub = crypto.new_signing_keypair()
    sig = crypto.sign(priv, b"data")
    assert not crypto.verify(pub, b"data", sig[:4] + "\n" + sig[4:])  # mangled sig rejected
