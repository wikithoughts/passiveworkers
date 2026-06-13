#!/usr/bin/env python3
"""
council/crypto.py — optional cryptographic delivery (D23, FEDERATION_V2 step 2)
================================================================================
Two real guarantees layered on top of content-addressed delivery (D22), both OPTIONAL
(the [crypto] extra; everything degrades gracefully to D22 integrity when PyNaCl is absent):

  • SIGNING (Ed25519): an operator signs its deliverable with a private key the coordinator
    never sees; the asker verifies against the operator's published public key. Detects any
    post-delivery tampering of the content and binds the deliverable to the operator's key.
    (Honest limit: a hostile coordinator that swaps content+key+sig together needs an
    out-of-band key check / PKI to defeat — documented, not papered over.)
  • ENCRYPTION (SealedBox / X25519): the asker publishes a public key with the job; the
    operator seals the payload to it; only the asker's private key can open it. The
    coordinator relays ciphertext it genuinely cannot read — a real confidentiality
    guarantee even against a hostile coordinator.

Keys are base64 strings so they travel as plain JSON. Helpers persist a keypair per identity
in ~/.passiveworkers so sign/verify and seal/unseal survive across CLI invocations.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib

try:
    from nacl.public import PrivateKey, PublicKey, SealedBox
    from nacl.signing import SigningKey, VerifyKey
    from nacl.exceptions import BadSignatureError, CryptoError
    HAVE_CRYPTO = True
except Exception:                       # pragma: no cover - optional dependency
    HAVE_CRYPTO = False


def available() -> bool:
    return HAVE_CRYPTO


def _require() -> None:
    if not HAVE_CRYPTO:
        raise RuntimeError("the crypto extra is not installed: pip install 'passiveworkers[crypto]'")


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode(), validate=True)   # reject whitespace/non-alphabet


# ------------------------------------------------------------------ keypairs
def new_signing_keypair() -> tuple[str, str]:
    """(private_b64, public_b64) for Ed25519 signing."""
    _require()
    sk = SigningKey.generate()
    return _b64e(bytes(sk)), _b64e(bytes(sk.verify_key))


def new_box_keypair() -> tuple[str, str]:
    """(private_b64, public_b64) for X25519 sealed-box encryption."""
    _require()
    sk = PrivateKey.generate()
    return _b64e(bytes(sk)), _b64e(bytes(sk.public_key))


def load_or_create(path: pathlib.Path, kind: str) -> dict:
    """Persist+reuse a keypair of `kind` ('sign' or 'box') at `path`. Returns {priv, pub}."""
    if path.exists():
        try:
            d = json.loads(path.read_text()).get(kind)
            if d and d.get("priv") and d.get("pub"):
                return d
        except Exception:
            pass
    priv, pub = new_signing_keypair() if kind == "sign" else new_box_keypair()
    path.parent.mkdir(parents=True, exist_ok=True)
    allk = {}
    if path.exists():
        try:
            allk = json.loads(path.read_text())
        except Exception:
            allk = {}
    allk[kind] = {"priv": priv, "pub": pub}
    # write with owner-only perms FROM CREATION (no world-readable window before chmod)
    blob = json.dumps(allk).encode()
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        os.chmod(path, 0o600)
    except Exception:
        path.write_text(json.dumps(allk))   # fallback (e.g. platforms without mode support)
        try:
            os.chmod(path, 0o600)            # a private key must never be world-readable
        except Exception:
            pass
    return allk[kind]


# ------------------------------------------------------------------ signing
def sign(priv_b64: str, data: bytes) -> str:
    """Detached Ed25519 signature (b64) over `data`."""
    _require()
    return _b64e(SigningKey(_b64d(priv_b64)).sign(data).signature)


def verify(pub_b64: str, data: bytes, sig_b64: str) -> bool:
    """True iff `sig` is a valid signature of `data` under `pub`. Never raises (returns False
    on any error, including when the crypto extra is absent)."""
    if not HAVE_CRYPTO:
        return False
    try:
        VerifyKey(_b64d(pub_b64)).verify(data, _b64d(sig_b64))
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ encryption
def seal(recipient_pub_b64: str, data: bytes) -> bytes:
    """Anonymous-sender encryption to a recipient public key (X25519 SealedBox)."""
    _require()
    return SealedBox(PublicKey(_b64d(recipient_pub_b64))).encrypt(data)


def unseal(priv_b64: str, ciphertext: bytes) -> bytes:
    """Open a sealed box with the recipient private key. Raises on wrong key / tampering."""
    _require()
    return SealedBox(PrivateKey(_b64d(priv_b64))).decrypt(ciphertext)
