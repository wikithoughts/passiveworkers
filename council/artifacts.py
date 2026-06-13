#!/usr/bin/env python3
"""
council/artifacts.py — content-addressed, chunked, integrity-verified file delivery (D22)
=========================================================================================
Real marketplace work produces FILES, not just text. This is the lean, dependency-free
codec for moving them between machines safely (FEDERATION_V2 step 3):

  • split a file into fixed-size chunks, hash each (sha256) → the chunk is its own address
  • a manifest records {name, size, chunk_size, root, chunks:[hashes]}; `root` = sha256 of
    the ordered chunk hashes (a flat Merkle root) — the tamper-evident fingerprint
  • the coordinator stores chunks as OPAQUE content-addressed blobs (dedup for free)
  • the receiver fetches each chunk by hash, verifies it against the manifest, and only
    reassembles if every chunk and the root check out — a corrupted or swapped chunk is
    detected, not written

Encryption + producer signatures layer on top of this later (the [crypto] extra); the
content-addressing here already gives integrity. Stdlib only.
"""

from __future__ import annotations

import hashlib
import pathlib

CHUNK_SIZE = 256 * 1024          # 256 KiB chunks
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MiB per deliverable file (v1 cap)


_ARTIFACT_TAG = "__pw_artifact__"


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wrap_artifact(manifest: dict) -> str:
    """Serialize a manifest as a file-deliverable, tagged so it can't be confused with a
    user's text deliverable that merely happens to be JSON."""
    import json
    return json.dumps({_ARTIFACT_TAG: 1, "manifest": manifest})


def read_artifact(deliverable: str):
    """Return the manifest if `deliverable` is a tagged file artifact, else None (it's text)."""
    import json
    try:
        d = json.loads(deliverable)
    except Exception:
        return None
    if isinstance(d, dict) and d.get(_ARTIFACT_TAG) == 1 and isinstance(d.get("manifest"), dict):
        return d["manifest"]
    return None


def manifest_root(chunk_hashes: list[str]) -> str:
    """Flat Merkle root: sha256 over the ordered chunk hashes. Order matters (reassembly)."""
    return hashlib.sha256("".join(chunk_hashes).encode()).hexdigest()


def chunk_file(path: str) -> tuple[dict, dict[str, bytes]]:
    """(manifest, {hash: bytes}). Raises if the file is missing or over the size cap."""
    p = pathlib.Path(path).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"not a file: {path}")
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"file too large ({size // 1_000_000} MB > {MAX_FILE_BYTES // 1_000_000} MB)")
    blobs: dict[str, bytes] = {}
    order: list[str] = []
    with p.open("rb") as f:
        while True:
            buf = f.read(CHUNK_SIZE)
            if not buf:
                break
            h = _h(buf)
            blobs[h] = buf
            order.append(h)
    manifest = {"name": p.name, "size": size, "chunk_size": CHUNK_SIZE,
                "chunks": order, "root": manifest_root(order)}
    return manifest, blobs


_HEX64 = __import__("re").compile(r"^[0-9a-f]{64}$")


def chunk_file_encrypted(path: str, seal_fn) -> tuple[dict, dict[str, bytes]]:
    """Like chunk_file, but each plaintext chunk is encrypted via seal_fn(bytes)->bytes before
    hashing/storing. The blob address is the hash of the CIPHERTEXT (so content-addressing and
    integrity still apply); the manifest is flagged `encrypted` and its size is the PLAINTEXT
    size. The coordinator only ever stores ciphertext it cannot read (D23)."""
    p = pathlib.Path(path).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"not a file: {path}")
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"file too large ({size // 1_000_000} MB > {MAX_FILE_BYTES // 1_000_000} MB)")
    blobs: dict[str, bytes] = {}
    order: list[str] = []
    with p.open("rb") as f:
        while True:
            buf = f.read(CHUNK_SIZE)
            if not buf:
                break
            ct = seal_fn(buf)
            h = _h(ct)
            blobs[h] = ct
            order.append(h)
    manifest = {"name": p.name, "size": size, "chunk_size": CHUNK_SIZE,
                "chunks": order, "root": manifest_root(order), "encrypted": True}
    return manifest, blobs


def verify_manifest(manifest: dict) -> bool:
    """The manifest's declared root must match its ordered chunk list, chunk hashes must be
    well-formed, and size must be a non-negative int (catches a doctored manifest before we
    fetch anything). An empty file (size 0, no chunks) is valid."""
    chunks = manifest.get("chunks")
    size = manifest.get("size")
    if not isinstance(chunks, list) or not isinstance(size, int) or size < 0:
        return False
    if not all(isinstance(h, str) and _HEX64.match(h) for h in chunks):
        return False
    return manifest.get("root") == manifest_root(chunks)


def reassemble(manifest: dict, fetch_chunk, out_dir: str, decrypt=None) -> pathlib.Path:
    """Fetch each chunk via fetch_chunk(hash)->bytes, VERIFY each against its hash, and write
    the file into out_dir only if everything checks out. fetch_chunk failures or any hash
    mismatch raise — a corrupted/swapped chunk never reaches disk. Path-safe: the manifest
    name is reduced to a basename inside out_dir. If the manifest is `encrypted`, `decrypt`
    (bytes->bytes) is applied AFTER the ciphertext hash is verified."""
    if manifest.get("encrypted") and decrypt is None:
        raise ValueError("manifest is encrypted but no decrypt key provided")
    if not verify_manifest(manifest):
        raise ValueError("manifest root does not match its chunk list")
    out = pathlib.Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    name = pathlib.Path(str(manifest.get("name", ""))).name or "deliverable.bin"  # strip traversal
    dest = (out / name).resolve()
    # segment-aware containment (not a bare startswith, which sibling-prefixes could fool)
    if dest != out and out not in dest.parents:
        raise ValueError("unsafe output path")
    total = 0
    encrypted = bool(manifest.get("encrypted"))
    with dest.open("wb") as f:
        for h in manifest["chunks"]:
            data = fetch_chunk(h)
            if data is None or _h(data) != h:   # verify the (cipher)text against its address
                raise ValueError(f"chunk {h[:12]}… missing or corrupted — aborting")
            if encrypted:
                try:
                    data = decrypt(data)        # decrypt AFTER integrity check
                except Exception:
                    dest.unlink(missing_ok=True)
                    raise ValueError("decryption failed (wrong key or tampered chunk)")
            f.write(data)
            total += len(data)
    if manifest.get("size") is not None and total != manifest["size"]:
        dest.unlink(missing_ok=True)
        raise ValueError("reassembled size mismatch")
    return dest
