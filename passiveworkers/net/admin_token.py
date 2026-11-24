"""passiveworkers/net/admin_token.py — the coordinator's admin token, with no guessable default (D56 / PW-3).

When ``PW_TOKEN`` is unset (or is the retired ``dev-token``), the coordinator generates a random token
on first start and persists it ``0600`` at ``<PW_HOME>/admin_token``. The token is NEVER logged: the
startup message only names the file to read. An explicit ``PW_TOKEN`` (including an explicitly empty
one, which keeps the admin routes disabled) is used as given.
"""

from __future__ import annotations

import os
import pathlib
import secrets
import stat
import time

from passiveworkers.paths import home

LEGACY_DEV_TOKEN = "dev-token"
_MIN_LEN = 24


def token_path() -> pathlib.Path:
    return home() / "admin_token"


def _valid(tok: str) -> bool:
    return len(tok) >= _MIN_LEN and tok.isascii() and tok.isprintable() and not any(c.isspace() for c in tok)


def _write_temp(path: pathlib.Path, tok: str) -> pathlib.Path:
    """Write ``tok`` fully to a fresh 0600 sibling temp file (O_EXCL: no world-readable window)."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(tok + "\n")
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        os.unlink(tmp)
        raise
    return tmp


def _publish_new(path: pathlib.Path, tok: str) -> bool:
    """Atomically create ``path`` with complete content, only if absent (``os.link`` fails if it
    exists), so a concurrent first start never reads a half-written file."""
    tmp = _write_temp(path, tok)
    try:
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    except OSError:
        # Filesystem without hard links (some FUSE/SMB/volume mounts): fall back to a plain O_EXCL
        # create. Still never world-readable; only the narrow partial-read window returns.
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as f:
            f.write(tok + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    finally:
        os.unlink(tmp)


def _replace_private(path: pathlib.Path, tok: str) -> None:
    tmp = _write_temp(path, tok)
    try:
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


_WRITER_WAIT_TRIES = 10     # the no-hardlink fallback creates the file empty, then writes it:
_WRITER_WAIT_S = 0.1        # wait up to ~1 s for a concurrent first start before calling it corrupt


def _check_home(directory: pathlib.Path) -> None:
    """The directory holding the token must be ours and not writable by group/other (otherwise
    another local user could swap or pre-create the token file)."""
    st = os.stat(directory)
    if st.st_uid != os.getuid():
        raise RuntimeError(f"{directory} is not owned by this user; refusing to keep the admin token "
                           "there. Use a PW_HOME you own or set a strong PW_TOKEN.")
    if stat.S_IMODE(st.st_mode) & 0o022:
        raise RuntimeError(f"{directory} is writable by other users (mode {stat.S_IMODE(st.st_mode):04o}); "
                           "refusing to keep the admin token there. `chmod 700` it or set a strong PW_TOKEN.")


def _read_trusted(path: pathlib.Path) -> tuple[str, int]:
    """(content, mode) of the token file, opened without following a symlink and only if it
    is a regular file owned by us; anything else is refused (an attacker-planted token must never be
    adopted as the admin token)."""
    # O_NONBLOCK: a planted FIFO must fail the regular-file check below, not hang startup in open().
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise RuntimeError(f"{path} is not a regular file owned by this user; refusing to trust it. "
                               "Remove it or set a strong PW_TOKEN.")
        with os.fdopen(os.dup(fd), "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip(), stat.S_IMODE(st.st_mode)
    finally:
        os.close(fd)


def _load_or_create(path: pathlib.Path) -> tuple[str, str | None]:
    """(token, note). note describes a first-start generation or a repair, else None."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_home(path.parent)
    for _ in range(3):
        try:
            existing, mode = _read_trusted(path)
            for _w in range(_WRITER_WAIT_TRIES):
                if mode & 0o077 or _valid(existing):
                    break
                time.sleep(_WRITER_WAIT_S)          # maybe a first start is still writing it
                existing, mode = _read_trusted(path)
        except FileNotFoundError:
            tok = secrets.token_urlsafe(32)
            if _publish_new(path, tok):
                return tok, f"generated a random admin token on first start and saved it to {path} (mode 0600)"
            continue   # lost a race with another first start: read theirs
        except OSError as e:   # ELOOP: a symlink sits at the path
            raise RuntimeError(f"{path} cannot be opened safely ({e.strerror}); refusing to trust it. "
                               "Remove it or set a strong PW_TOKEN.") from e
        if mode & 0o077:
            # Group/other bits: a writable file may hold a token another user planted, a readable one
            # is already disclosed. Either way the content is compromised: rotate, never adopt.
            tok = secrets.token_urlsafe(32)
            _replace_private(path, tok)
            return tok, (f"{path} was group/world accessible, so its token is treated as compromised: "
                         f"rotated (was mode {mode:04o}); a new admin token was saved there (mode 0600)")
        if not _valid(existing):
            tok = secrets.token_urlsafe(32)
            _replace_private(path, tok)
            return tok, f"{path} was empty or malformed; generated a new admin token there (mode 0600)"
        return existing, None
    raise RuntimeError(f"could not read or create the admin token file {path}")


def resolve(env_token: str | None) -> tuple[str, str | None]:
    """The admin token to use and an optional operator-facing note (never containing the token)."""
    if env_token is not None and env_token != LEGACY_DEV_TOKEN:
        return env_token, None
    path = token_path()
    try:
        tok, note = _load_or_create(path)
    except OSError as e:
        raise RuntimeError(
            f"cannot persist a generated admin token at {path} ({e.strerror or e}). "
            "Set a strong PW_TOKEN (e.g. `openssl rand -hex 24`) or make PW_HOME writable.") from e
    if env_token == LEGACY_DEV_TOKEN:
        legacy = ("PW_TOKEN=dev-token is no longer accepted. Using the generated admin token instead; "
                  f"read it with `cat {path}` and use it (or set your own PW_TOKEN) wherever you used dev-token")
        note = f"{legacy}. {note}" if note else legacy
    elif note is None:
        note = f"using the generated admin token in {path}"
    return tok, note
