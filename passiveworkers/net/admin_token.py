"""passiveworkers/net/admin_token.py — the coordinator's admin token, with no guessable default (D56 / PW-3).

When ``PW_TOKEN`` is unset (or is the retired ``dev-token``), the coordinator generates a random token
on first start and persists it ``0600`` at ``<PW_HOME>/admin_token``. The token is NEVER logged: the
startup message only names the file to read. An explicit ``PW_TOKEN`` (including an explicitly empty
one, which keeps the admin routes disabled) is used as given.
"""

from __future__ import annotations

import contextlib
import enum
import grp
import os
import pathlib
import pwd
import secrets
import stat
import time
from collections.abc import Iterator

from passiveworkers.paths import home

try:
    import fcntl
except ImportError:   # pragma: no cover - non-POSIX
    fcntl = None   # type: ignore[assignment]

LEGACY_DEV_TOKEN = "dev-token"


class Event(enum.Enum):
    EXPLICIT = "explicit"      # PW_TOKEN supplied by the operator
    GENERATED = "generated"    # first start: new token written
    ROTATED = "rotated"        # file had group/other bits: replaced
    REPAIRED = "repaired"      # file empty/malformed: replaced
    ADOPTED = "adopted"        # existing 0600 file reused


_EVENT: tuple[Event, int, bool] = (Event.EXPLICIT, 0, False)   # (event, file mode, legacy dev-token); never the token
_WARNINGS: list[str] = []   # operator-facing warnings from the last resolve(); paths and fixed text only
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


_WRITER_WAIT_TRIES = 10     # ONLY when flock is unavailable: the no-hardlink fallback creates the file
_WRITER_WAIT_S = 0.1        # empty, then writes it, so wait ~1 s for a concurrent first start


def _flock_ex(fd: int) -> bool:
    """Take an exclusive flock on ``fd`` (blocking). False if locking is unsupported here."""
    if fcntl is None:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return True
    except OSError:
        return False


@contextlib.contextmanager
def _locked(directory: pathlib.Path) -> Iterator[bool]:
    """Serialise generate/rotate/repair across processes with an exclusive flock on a lock file in the
    token directory. Yields whether the lock is held; if flock is unsupported, yields False (the caller
    falls back to the old, unserialised behaviour and warns)."""
    try:
        fd = os.open(directory / ".admin_token.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        yield False
        return
    try:
        yield _flock_ex(fd)       # closing the fd releases the lock
    finally:
        os.close(fd)


def _is_private_group(gid: int) -> bool:
    """True iff ``gid`` is this user's primary group and nobody else belongs to it (the Debian/Ubuntu
    user-private-group), so group-write on a directory grants nobody else anything."""
    if gid != os.getgid():
        return False
    try:
        me = pwd.getpwuid(os.getuid()).pw_name
        if any(m != me for m in grp.getgrgid(gid).gr_mem):
            return False
        return not any(u.pw_gid == gid and u.pw_uid != os.getuid() for u in pwd.getpwall())
    except (KeyError, OSError):
        return False


def _check_home(directory: pathlib.Path) -> None:
    """The directory holding the token must be ours and not writable by other users (otherwise another
    local user could swap or pre-create the token file). Group-writable is accepted only when the group
    is our own private group (a warning is recorded); world-writable is never accepted."""
    st = os.stat(directory)
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid != os.getuid():
        raise RuntimeError(f"PW_HOME ({directory}) is not owned by this user; refusing to keep the admin "
                           f"token there. Point PW_HOME at a directory you own, `chown` it to this user, "
                           "or set a strong PW_TOKEN.")
    if mode & 0o002 or (mode & 0o020 and not _is_private_group(st.st_gid)):
        raise RuntimeError(f"PW_HOME ({directory}) is writable by other users (mode {mode:04o}); refusing "
                           "to keep the admin token there. `chmod go-w` it or set a strong PW_TOKEN.")
    if mode & 0o020:
        _WARNINGS.append(f"{directory} is group-writable (mode {mode:04o}) but its group is your private "
                         "group, so the admin token is kept there; `chmod g-w` it to silence this")


def _read_trusted(path: pathlib.Path) -> tuple[str, int, bool]:
    """(content, mode, enforced) of the token file, opened without following a symlink and only if it
    is a regular file owned by us; anything else is refused (an attacker-planted token must never be
    adopted as the admin token). A file with group/other bits is chmodded 0600 here; ``enforced`` is
    whether that took (False on filesystems that ignore permissions, e.g. vfat/SMB)."""
    # O_NONBLOCK: a planted FIFO must fail the regular-file check below, not hang startup in open().
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise RuntimeError(f"{path} is not a regular file owned by this user; refusing to trust it. "
                               "Remove it or set a strong PW_TOKEN.")
        mode = stat.S_IMODE(st.st_mode)
        enforced = True
        if mode & 0o077:
            try:
                os.fchmod(fd, 0o600)
                enforced = not (stat.S_IMODE(os.fstat(fd).st_mode) & 0o077)
            except OSError:
                enforced = False
        with os.fdopen(os.dup(fd), "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip(), mode, enforced
    finally:
        os.close(fd)


def _load_or_create(path: pathlib.Path) -> str:
    """The token. What happened (generated/rotated/repaired/adopted) is recorded in ``_EVENT``, a
    value that never holds the token, so no message is built from the secret's data flow."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_home(path.parent)
    with _locked(path.parent) as locked:
        if not locked:
            _WARNINGS.append("file locking is unavailable here, so two coordinators starting at the same "
                             "moment could disagree on the admin token; start them one at a time")
        return _load_or_create_locked(path, locked)


def _load_or_create_locked(path: pathlib.Path, locked: bool) -> str:
    global _EVENT
    for _ in range(3):
        try:
            existing, mode, enforced = _read_trusted(path)
            # Under the lock no writer can be mid-write, so an empty file is corrupt. Without it, the
            # no-hardlink fallback may still be writing: wait a moment.
            for _w in range(0 if locked else _WRITER_WAIT_TRIES):
                if mode & 0o077 or _valid(existing):
                    break
                time.sleep(_WRITER_WAIT_S)
                existing, mode, enforced = _read_trusted(path)
        except FileNotFoundError:
            tok = secrets.token_urlsafe(32)
            if _publish_new(path, tok):
                _EVENT = (Event.GENERATED, 0, False)
                return tok
            continue   # lost a race with another first start: read theirs
        except OSError as e:   # ELOOP: a symlink sits at the path
            raise RuntimeError(f"{path} cannot be opened safely ({e.strerror}); refusing to trust it. "
                               "Remove it or set a strong PW_TOKEN.") from e
        if mode & 0o077 and not enforced:
            # The filesystem ignores permissions (vfat/SMB/some docker volumes): the bits will show
            # forever, so rotating would change the token on every restart. Keep it and say so.
            _WARNINGS.append("filesystem does not enforce permissions; token not rotated "
                             f"(keep {path} on a filesystem that does, or set PW_TOKEN)")
            if _valid(existing):
                _EVENT = (Event.ADOPTED, 0, False)
                return existing
        elif mode & 0o077:
            # Group/other bits: a writable file may hold a token another user planted, a readable one
            # is already disclosed. Either way the content is compromised: rotate, never adopt.
            tok = secrets.token_urlsafe(32)
            _replace_private(path, tok)
            _EVENT = (Event.ROTATED, mode, False)
            return tok
        if not _valid(existing):
            tok = secrets.token_urlsafe(32)
            _replace_private(path, tok)
            _EVENT = (Event.REPAIRED, 0, False)
            return tok
        _EVENT = (Event.ADOPTED, 0, False)
        return existing
    raise RuntimeError(f"could not read or create the admin token file {path}")


def resolve(env_token: str | None) -> str:
    """The admin token to use. Returns the token ONLY; ``startup_note()`` gives the operator message."""
    global _EVENT
    _WARNINGS.clear()
    if env_token is not None and env_token != LEGACY_DEV_TOKEN:
        _EVENT = (Event.EXPLICIT, 0, False)
        return env_token
    path = token_path()
    try:
        tok = _load_or_create(path)
    except OSError as e:
        raise RuntimeError(
            f"cannot persist a generated admin token at {path} ({e.strerror or e}). "
            "Set a strong PW_TOKEN (e.g. `openssl rand -hex 24`) or make PW_HOME writable.") from e
    if env_token == LEGACY_DEV_TOKEN:
        _EVENT = (_EVENT[0], _EVENT[1], True)
    return tok


def startup_note() -> str | None:
    """Operator-facing message for the last ``resolve()``, built from the token FILE PATH and the event
    only. It has no access to the token, so it cannot contain it."""
    kind, mode, legacy_dev = _EVENT
    if kind is Event.EXPLICIT:
        return None
    path = token_path()
    msg = {
        Event.GENERATED: f"generated a random admin token on first start and saved it to {path} (mode 0600)",
        Event.ROTATED: (f"{path} was group/world accessible, so its token is treated as compromised: "
                        f"rotated (was mode {mode:04o}); a new admin token was saved there (mode 0600)"),
        Event.REPAIRED: f"{path} was empty or malformed; generated a new admin token there (mode 0600)",
        Event.ADOPTED: f"using the generated admin token in {path}",
    }[kind]
    if legacy_dev:
        msg = ("PW_TOKEN=dev-token is no longer accepted. Using the generated admin token instead; "
               f"read it with `cat {path}` and use it (or set your own PW_TOKEN) wherever you used "
               f"dev-token. {msg}")
    if _WARNINGS:
        msg += "".join(f". WARNING: {w}" for w in _WARNINGS)
    return msg
