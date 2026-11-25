"""passiveworkers/net/admin_token.py — the coordinator's admin token, with no guessable default (D56 / PW-3).

When ``PW_TOKEN`` is unset (or is the retired ``dev-token``), the coordinator generates a random token
on first start and persists it ``0600`` at ``<PW_HOME>/admin_token``. The token is NEVER logged: the
startup message only names the file to read. An explicit ``PW_TOKEN`` (including an explicitly empty
one, which keeps the admin routes disabled) is used as given.

Generating and persisting a token needs POSIX (``fcntl``/``grp``/``pwd``, ``os.fchmod``). On other
platforms (Windows) this module still imports cleanly and an explicit ``PW_TOKEN`` still works; only
the generated-token path refuses, with a message saying to set ``PW_TOKEN``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import os
import pathlib
import secrets
import stat
import threading
import time
from collections.abc import Iterator
from types import ModuleType
from typing import Optional

from passiveworkers.paths import home

# POSIX-only modules, imported defensively so that importing this module (and the coordinator, which
# imports it) never crashes on Windows; _require_posix() refuses the generated-token path there.
fcntl: Optional[ModuleType]
grp: Optional[ModuleType]
pwd: Optional[ModuleType]
try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import grp
except ImportError:
    grp = None
try:
    import pwd
except ImportError:
    pwd = None

LEGACY_DEV_TOKEN = "dev-token"


class Event(enum.Enum):
    EXPLICIT = "explicit"      # PW_TOKEN supplied by the operator
    GENERATED = "generated"    # first start: new token written
    ROTATED = "rotated"        # file had group/other bits: replaced
    REPAIRED = "repaired"      # file empty/malformed: replaced
    ADOPTED = "adopted"        # existing 0600 file reused


@dataclasses.dataclass
class _State:
    """What one resolve() did, for startup_note(). Never holds the token: paths and fixed text only."""
    event: Event = Event.EXPLICIT
    mode: int = 0                   # the token file's mode before a rotation
    legacy_dev: bool = False        # PW_TOKEN was the retired dev-token
    path: Optional[pathlib.Path] = None
    warnings: list[str] = dataclasses.field(default_factory=list)


# Per thread: each thread's startup_note() describes ITS last resolve(), never a concurrent one's.
_LOCAL = threading.local()
_MIN_LEN = 24


def token_path() -> pathlib.Path:
    return home() / "admin_token"


def _require_posix() -> None:
    if fcntl is None or grp is None or pwd is None or not hasattr(os, "fchmod") \
            or not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError(
            "PW_TOKEN is unset, and generating a persisted admin token needs a POSIX system "
            "(Linux/macOS: file locking and owner-only permissions). Set a strong PW_TOKEN, e.g. "
            "`python -c \"import secrets; print(secrets.token_urlsafe(32))\"`.")


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
        # create. Still never world-readable, and the caller holds the lock, so no other coordinator
        # can read the file while it is still empty.
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


_LOCK_TIMEOUT_S = 30.0
_NO_LOCK = ("the admin token lock ({lock}) is unavailable ({why}), so two coordinators starting "
            "together could end up with different admin tokens; refusing to generate, rotate or adopt "
            "one unserialised. Set a strong PW_TOKEN (e.g. `openssl rand -hex 24`), or move PW_HOME to "
            "a local filesystem that supports flock.")


def _flock_ex(fd: int) -> bool:
    """Take an exclusive flock on ``fd``, waiting up to ``_LOCK_TIMEOUT_S`` (a wedged holder must not
    hang startup forever: that raises, failing closed). False if locking is unsupported here."""
    if fcntl is None:
        return False
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "timed out waiting for the admin token lock (.admin_token.lock in PW_HOME); another "
                    "coordinator is starting or stuck. Retry, or set a strong PW_TOKEN.") from None
            time.sleep(0.05)
        except OSError:
            return False


@contextlib.contextmanager
def _locked(directory: pathlib.Path) -> Iterator[None]:
    """Serialise generate/rotate/repair/adopt across processes with an exclusive flock on a lock file
    in the token directory. FAIL CLOSED: if the lock file cannot be opened or flock is unsupported,
    raise before the token file is even read (reading may fchmod it), so no process ever writes or
    adopts a token unserialised and every coordinator sharing PW_HOME ends up with the same one."""
    lock = directory / ".admin_token.lock"
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as e:
        raise RuntimeError(_NO_LOCK.format(lock=lock, why=e.strerror or e)) from e
    try:
        if not _flock_ex(fd):
            raise RuntimeError(_NO_LOCK.format(lock=lock, why="flock is not supported here"))
        yield                     # closing the fd releases the lock
    finally:
        os.close(fd)


def _is_private_group(gid: int) -> bool:
    """True iff ``gid`` looks like this user's Debian/Ubuntu user-private group, so group-write on a
    directory grants nobody else anything: it is our primary gid (per the process AND passwd), it is
    named after us, and its member list names nobody else.

    Only ``getgrgid``/``getpwuid`` lookups: enumerating every user (``getpwall``) is slow or
    incomplete under LDAP/NSS. ``gr_mem`` does not list users whose PRIMARY group this is, so a
    shared primary group (macOS ``staff``, Linux ``users``) has an empty ``gr_mem``; the name check
    rejects those. Residual: another account deliberately given our private group as its primary gid
    is not detected (that takes an administrator, who can already read our files)."""
    if grp is None or pwd is None or gid != os.getgid():
        return False
    try:
        me = pwd.getpwuid(os.getuid())
        g = grp.getgrgid(gid)
    except (KeyError, OSError):
        return False
    if me.pw_gid != gid or g.gr_name != me.pw_name:
        return False
    return all(m == me.pw_name for m in g.gr_mem)


def _check_home(directory: pathlib.Path, state: _State) -> None:
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
        state.warnings.append(f"{directory} is group-writable (mode {mode:04o}) but its group is your "
                              "private group, so the admin token is kept there; `chmod g-w` it to "
                              "silence this")


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


def _load_or_create(path: pathlib.Path, state: _State) -> str:
    """The token. What happened (generated/rotated/repaired/adopted) is recorded in ``state``, a
    value that never holds the token, so no message is built from the secret's data flow."""
    _require_posix()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_home(path.parent, state)
    with _locked(path.parent):
        return _load_or_create_locked(path, state)


def _load_or_create_locked(path: pathlib.Path, state: _State) -> str:
    """Runs only under the lock, so no writer can be mid-write: an empty file is corrupt, not racing."""
    for _ in range(3):
        try:
            existing, mode, enforced = _read_trusted(path)
        except FileNotFoundError:
            tok = secrets.token_urlsafe(32)
            if _publish_new(path, tok):
                state.event = Event.GENERATED
                return tok
            continue   # lost a race with another first start: read theirs
        except OSError as e:   # ELOOP: a symlink sits at the path
            raise RuntimeError(f"{path} cannot be opened safely ({e.strerror}); refusing to trust it. "
                               "Remove it or set a strong PW_TOKEN.") from e
        if mode & 0o077 and not enforced:
            # The filesystem ignores permissions (vfat/SMB/some docker volumes): the bits will show
            # forever, so rotating would change the token on every restart. Keep it and say so.
            if mode & 0o022:
                # Others may be able to WRITE it and we cannot clear that, so a planted token cannot
                # be ruled out: do not adopt it.
                raise RuntimeError(
                    f"{path} is writable by other users and this filesystem does not enforce permissions, "
                    "so its token cannot be trusted. Move PW_HOME to a POSIX filesystem or set a strong "
                    "PW_TOKEN.")
            if os.environ.get("PW_ALLOW_LOOSE_TOKEN_FILE") != "1":
                # On NFS root-squash, FUSE or bind mounts the loose mode is REAL: other users or
                # containers sharing the volume can read the admin token. Fail closed.
                raise RuntimeError(
                    f"{path} has mode {mode:04o} and this filesystem does not enforce permissions, so "
                    "other users of the volume may be able to read the admin token. Set a strong "
                    "PW_TOKEN (e.g. `openssl rand -hex 24`), move PW_HOME to a POSIX filesystem, or, "
                    "if the volume is private to you, set PW_ALLOW_LOOSE_TOKEN_FILE=1 to keep it.")
            state.warnings.append("filesystem does not enforce permissions; token not rotated, and the "
                                  f"token file {path} is READABLE BY OTHER USERS (mode {mode:04o}); set "
                                  "PW_TOKEN or move PW_HOME to a POSIX filesystem")
            if _valid(existing):
                state.event = Event.ADOPTED
                return existing
        elif mode & 0o077:
            # Group/other bits: a writable file may hold a token another user planted, a readable one
            # is already disclosed. Either way the content is compromised: rotate, never adopt.
            tok = secrets.token_urlsafe(32)
            _replace_private(path, tok)
            state.event, state.mode = Event.ROTATED, mode
            return tok
        if not _valid(existing):
            tok = secrets.token_urlsafe(32)
            _replace_private(path, tok)
            state.event = Event.REPAIRED
            return tok
        state.event = Event.ADOPTED
        return existing
    raise RuntimeError(f"could not read or create the admin token file {path}")


def resolve(env_token: str | None) -> str:
    """The admin token to use. Returns the token ONLY; ``startup_note()`` gives the operator message
    for this thread's last call (thread-safe: the record is per thread, never shared)."""
    _LOCAL.state = None                 # a call that raises leaves no stale note behind
    state = _State()
    if env_token is not None and env_token != LEGACY_DEV_TOKEN:
        _LOCAL.state = state            # EXPLICIT
        return env_token
    path = token_path()
    state.path = path
    try:
        tok = _load_or_create(path, state)
    except OSError as e:
        raise RuntimeError(
            f"cannot persist a generated admin token at {path} ({e.strerror or e}). "
            "Set a strong PW_TOKEN (e.g. `openssl rand -hex 24`) or make PW_HOME writable.") from e
    state.legacy_dev = env_token == LEGACY_DEV_TOKEN
    _LOCAL.state = state
    return tok


def startup_note() -> str | None:
    """Operator-facing message for this thread's last ``resolve()``, built from the token FILE PATH
    and the event only. It has no access to the token, so it cannot contain it. None if the token was
    explicit, or this thread has not resolved (or its last resolve raised)."""
    state: Optional[_State] = getattr(_LOCAL, "state", None)
    if state is None or state.event is Event.EXPLICIT:
        return None
    path, mode = state.path, state.mode
    msg = {
        Event.GENERATED: f"generated a random admin token on first start and saved it to {path} (mode 0600)",
        Event.ROTATED: (f"{path} was group/world accessible, so its token is treated as compromised: "
                        f"rotated (was mode {mode:04o}); a new admin token was saved there (mode 0600)"),
        Event.REPAIRED: f"{path} was empty or malformed; generated a new admin token there (mode 0600)",
        Event.ADOPTED: f"using the generated admin token in {path}",
    }[state.event]
    if state.legacy_dev:
        msg = ("PW_TOKEN=dev-token is no longer accepted. Using the generated admin token instead; "
               f"read it with `cat {path}` and use it (or set your own PW_TOKEN) wherever you used "
               f"dev-token. {msg}")
    if state.warnings:
        msg += "".join(f". WARNING: {w}" for w in state.warnings)
    return msg
