#!/usr/bin/env python3
"""
passiveworkers/artifacts.py — content-addressed, chunked, integrity-verified file delivery (D22)
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
import os
import pathlib
import tempfile

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


def chunk_bytes(data: bytes, name: str) -> tuple[dict, dict[str, bytes]]:
    """(manifest, {hash: bytes}) for an IN-MEMORY deliverable — e.g. a multi-producer deliverable
    the coordinator reassembled from several nodes' parts (D38). Same content-addressed/Merkle
    format as chunk_file, so the existing fetch+reassemble+verify path handles it unchanged."""
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"deliverable too large ({len(data) // 1_000_000} MB > "
                         f"{MAX_FILE_BYTES // 1_000_000} MB)")
    blobs: dict[str, bytes] = {}
    order: list[str] = []
    for i in range(0, len(data), CHUNK_SIZE):
        buf = data[i:i + CHUNK_SIZE]
        h = _h(buf)
        blobs[h] = buf
        order.append(h)
    name = pathlib.Path(str(name or "deliverable.bin")).name or "deliverable.bin"
    manifest = {"name": name, "size": len(data), "chunk_size": CHUNK_SIZE,
                "chunks": order, "root": manifest_root(order)}
    return manifest, blobs


def chunk_file(path: str) -> tuple[dict, dict[str, bytes]]:
    """(manifest, {hash: bytes}). Raises if the file is missing or over the size cap."""
    p = pathlib.Path(path).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"not a file: {path}")
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"file too large ({size // 1_000_000} MB > {MAX_FILE_BYTES // 1_000_000} MB)")
    manifest, blobs = chunk_bytes(p.read_bytes(), p.name)
    manifest["name"] = p.name
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


_MAX_NAME_BYTES = 200   # leaves room for ".<name>.<random>.part" and " (n)" inside a 255-byte limit


# PW-5: names (compared case-insensitively) that an agent, shell, build tool or interpreter loads or
# executes automatically from a directory. A delivered file may not take one of them even in a fresh
# directory: planting `CLAUDE.md` / `conftest.py` / `evil.pth` there is code or instruction injection.
_AUTOLOAD_NAMES = frozenset({
    "claude.md", "claude.local.md", "agents.md", "agent.md", "gemini.md", "skill.md", "copilot-instructions.md",
    "conftest.py", "sitecustomize.py", "usercustomize.py", "setup.py", "__init__.py", "__main__.py",
    "pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "noxfile.py", "manage.py",
    "makefile", "gnumakefile", "justfile", "rakefile", "gemfile", "dockerfile", "docker-compose.yml",
    "package.json", "package-lock.json", "gulpfile.js", "gruntfile.js", "webpack.config.js",
    "build.gradle", "pom.xml", "cargo.toml", "build.rs",
})
_AUTOLOAD_SUFFIXES = (".pth", ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bat", ".cmd", ".ps1", ".desktop")


def _safe_name(raw) -> str:
    """The deliverable's file name, taken from a manifest the OTHER party controls. Refuses (never
    silently rewrites) anything that could address a different file than a plain new one in the
    output directory: path separators, dotfiles (.zshrc, .gitconfig, .env, ...), "." / "..",
    control characters, and over-long names. An absent or blank name falls back to deliverable.bin."""
    name = str(raw if raw is not None else "")
    if not name.strip():
        return "deliverable.bin"
    if "/" in name or "\\" in name:
        raise ValueError("unsafe deliverable name: path separators are not allowed")
    if name.startswith(".") or name != name.strip():
        raise ValueError("unsafe deliverable name: hidden files and padded names are not allowed")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("unsafe deliverable name: control characters are not allowed")
    if len(name.encode("utf-8", "replace")) > _MAX_NAME_BYTES:
        raise ValueError("unsafe deliverable name: too long")
    if name.endswith((".", " ")) or ":" in name:    # Windows trailing-dot/space folding, ADS streams
        raise ValueError("unsafe deliverable name: trailing dot/space and ':' are not allowed")
    low = name.lower()
    if low in _AUTOLOAD_NAMES or low.endswith(_AUTOLOAD_SUFFIXES):
        raise ValueError("unsafe deliverable name: auto-loaded or executable file names are not allowed")
    return name


def _claim_unused_name(out: pathlib.Path, name: str) -> pathlib.Path:
    """Atomically reserve a destination that does not exist yet (O_CREAT|O_EXCL, which also refuses
    a dangling or live symlink): `name`, else `stem (1).ext`, `stem (2).ext`, ... An existing file is
    never replaced. The empty placeholder is overwritten by the caller's os.replace."""
    stem, ext = os.path.splitext(name)
    for n in range(0, 10_000):
        cand = out / (name if n == 0 else f"{stem} ({n}){ext}")
        try:
            os.close(os.open(cand, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            continue
        return cand
    raise ValueError("no free file name in the output directory")


def reassemble(manifest: dict, fetch_chunk, out_dir: str, decrypt=None) -> pathlib.Path:
    """Fetch each chunk via fetch_chunk(hash)->bytes, VERIFY each against its hash, and write
    the file into out_dir only if everything checks out. fetch_chunk failures or any hash
    mismatch raise — a corrupted/swapped chunk never reaches disk. Path-safe: a manifest name with
    a path separator, a leading dot or a control character is REFUSED (the manifest comes from the
    other party), and an existing file is NEVER overwritten: the result is written to
    `name (1).ext`, `name (2).ext`, ... instead. If the manifest is `encrypted`, `decrypt`
    (bytes->bytes) is applied AFTER the ciphertext hash is verified.
    Chunks stream into a hidden temp `.part` file beside the destination, capped as they arrive
    at the manifest's declared size (and MAX_FILE_BYTES); only a complete, verified file is
    renamed into place, so a failure leaves neither a partial file nor a clobbered earlier one."""
    if manifest.get("encrypted") and decrypt is None:
        raise ValueError("manifest is encrypted but no decrypt key provided")
    if not verify_manifest(manifest):
        raise ValueError("manifest root does not match its chunk list")
    if manifest["size"] > MAX_FILE_BYTES:
        raise ValueError(f"file too large ({manifest['size'] // 1_000_000} MB > "
                         f"{MAX_FILE_BYTES // 1_000_000} MB)")
    out = pathlib.Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    name = _safe_name(manifest.get("name"))
    total = 0
    encrypted = bool(manifest.get("encrypted"))
    fd, tmp = tempfile.mkstemp(dir=out, prefix=f".{name}.", suffix=".part")
    part = pathlib.Path(tmp)
    try:
        with os.fdopen(fd, "wb") as f:
            for h in manifest["chunks"]:
                data = fetch_chunk(h)
                if data is None or _h(data) != h:   # verify the (cipher)text against its address
                    raise ValueError(f"chunk {h[:12]}… missing or corrupted — aborting")
                if encrypted:
                    try:
                        data = decrypt(data)        # decrypt AFTER integrity check
                    except Exception:
                        raise ValueError("decryption failed (wrong key or tampered chunk)") from None
                total += len(data)
                if total > manifest["size"]:        # running cap: never write past the declared size
                    raise ValueError("reassembled size mismatch")
                f.write(data)
        if total != manifest["size"]:
            raise ValueError("reassembled size mismatch")
        dest = _claim_unused_name(out, name)
        try:
            os.replace(part, dest)     # replaces only the empty placeholder we just created
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
    finally:
        part.unlink(missing_ok=True)   # no-op after a successful replace
    return dest
