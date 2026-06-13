#!/usr/bin/env python3
"""
council/trust.py — out-of-band operator key trust (D25, FEDERATION_V2)
=====================================================================
Closes the honest limitation documented in D23. Signed deliverables were verified against the
signing key the COORDINATOR reports for an operator (`registered_sign_pub`). That defeats a
passive or honest-but-curious coordinator, but NOT a fully hostile one: a coordinator that
controls the node record can swap the content, swap the key, and re-sign — the asker would
still "verify" successfully against the rewritten key.

The fix is an out-of-band root of trust the coordinator does not control: the asker PINS an
operator's signing key and verifies every later deliverable against the PINNED key.

  • Trust On First Use (TOFU) — like SSH `known_hosts`: on the first delivery from an operator,
    pin whatever key the coordinator reports and warn that this first contact is unverified
    (compare the printed fingerprint with the operator over a side channel to upgrade trust).
  • Explicit pin — `pw trust add <operator> <pubkey>`: the operator shares their PUBLIC signing
    key (safe to share) over a trusted channel; the asker pins it and the fingerprint is shown
    to confirm. From then on a coordinator cannot forge a delivery for that operator.
  • Mismatch — if a pinned operator's delivery presents a different key, fetch REFUSES and shows
    both fingerprints (key rotation, a second machine, or coordinator tampering — verify before
    trusting).

Pure standard library: managing pins never requires the [crypto] extra (verifying a signature
still does). Pins live in ~/.passiveworkers/trust.json, owner-only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import sys

# status codes returned by classify()
PINNED_MATCH = "pinned-match"   # key matches an existing out-of-band/TOFU pin → trusted
UNPINNED = "unpinned"           # first contact — no pin yet (caller TOFU-pins after verifying)
MISMATCH = "mismatch"           # key differs from the pin → REFUSE (rotation or tampering)
NO_KEY = "no-key"               # nothing to pin/verify (unsigned delivery)


def _store_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("PW_LIBRARY_DIR",
                                       str(pathlib.Path.home() / ".passiveworkers"))) / "trust.json"


def _key_bytes(pub_b64: str) -> bytes:
    """Canonical key material for fingerprinting — decode the base64 so the fingerprint is
    independent of incidental string formatting; fall back to the raw bytes if it isn't b64."""
    try:
        return base64.b64decode(pub_b64.encode(), validate=True)
    except Exception:
        return pub_b64.encode()


def fingerprint(pub_b64: str) -> str:
    """A short, human-comparable fingerprint of a public key (80-bit truncated SHA-256, base32).
    Formatted `PW-XXXX-XXXX-XXXX-XXXX` so two people can read it to each other out of band."""
    if not pub_b64:
        return ""
    digest = hashlib.sha256(_key_bytes(pub_b64)).digest()[:10]   # 80 bits
    b32 = base64.b32encode(digest).decode().rstrip("=")          # 16 chars, no padding
    groups = [b32[i:i + 4] for i in range(0, len(b32), 4)]
    return "PW-" + "-".join(groups)


def _load() -> dict:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text())
        if not isinstance(d, dict):
            raise ValueError("trust store is not a JSON object")
        return d
    except Exception as e:
        # Never silently drop pins: preserve the unreadable file (so a later _save doesn't blindly
        # overwrite recoverable data) and tell the user, then start fresh.
        try:
            backup = path.parent / (path.name + ".corrupt")
            os.replace(str(path), str(backup))
            sys.stderr.write(f"⚠ trust store {path} was unreadable ({e}); moved to {backup}. "
                             f"Starting fresh — re-pin operators or restore from that file.\n")
        except Exception:
            pass
        return {}


def _save(d: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(d, indent=2, sort_keys=True).encode()
    # Atomic + owner-only: write a temp file in the same dir (0600 from creation), then replace.
    # No torn writes and no world-readable window. Single-user store; last writer wins on a truly
    # concurrent write (the lost pin simply re-establishes via TOFU on the next fetch).
    tmp = path.parent / (path.name + ".tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        os.chmod(tmp, 0o600)
        os.replace(str(tmp), str(path))
    except Exception:
        # last-resort fallback: still force owner-only perms (a trust anchor must never be world-readable)
        path.write_text(json.dumps(d, indent=2, sort_keys=True))
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


def get(operator: str) -> dict | None:
    """The pin for an operator handle, or None. {pub, fp, source}."""
    return _load().get((operator or "").strip()) or None


def list_pins() -> dict:
    return _load()


def pin(operator: str, pub_b64: str, source: str = "manual") -> dict:
    """Pin (or re-pin) an operator's signing key. Returns the stored record. Caller decides
    when re-pinning is appropriate (e.g. after verifying a rotated key out of band)."""
    operator = (operator or "").strip()
    if not operator:
        raise ValueError("operator handle required")
    if not pub_b64:
        raise ValueError("public key required")
    d = _load()
    rec = {"pub": pub_b64, "fp": fingerprint(pub_b64), "source": source}
    d[operator] = rec
    _save(d)
    return rec


def remove(operator: str) -> bool:
    d = _load()
    if (operator or "").strip() in d:
        del d[(operator or "").strip()]
        _save(d)
        return True
    return False


def classify(operator: str, presented_pub: str) -> tuple[str, dict | None]:
    """PURE trust-state decision (no side effects) for a delivery's presented signing key.

    Returns (status, existing_pin_or_None):
      • NO_KEY       — unsigned delivery, nothing to decide.
      • MISMATCH     — operator is pinned to a DIFFERENT key → caller must refuse.
      • PINNED_MATCH — presented key equals the pin → verify against the pinned key.
      • UNPINNED     — first contact → caller verifies, then TOFU-pins only if the signature is
                       valid (we never pin a key taken from an invalid signature).

    Pinning a rotated key is always an explicit, human-verified action (`pw trust add`), never
    automatic — so a coordinator can't silently rotate a pinned operator's key.
    """
    operator = (operator or "").strip()
    if not presented_pub:
        return NO_KEY, None
    existing = get(operator) if operator else None
    if existing is None:
        return UNPINNED, None
    if existing["pub"] == presented_pub:
        return PINNED_MATCH, existing
    return MISMATCH, existing
