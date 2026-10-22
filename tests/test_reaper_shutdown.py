"""The pw-reaper daemon thread must never be left printing into interpreter shutdown.

A reaper whose pass fails prints a log line to stdout. If it is mid-write when the interpreter
finalizes, it holds stdout's buffer lock, the final flush can't take it, and CPython aborts with
"Fatal Python error: _enter_buffered_busy ... possibly due to daemon threads": the same crash #48
fixed for the pw-baseline thread (test (3.10), CI run 38124448503). store.py now stops and joins
every reaper at exit.

Each test runs a real child interpreter. The child shortens the reaper tick (it patches
threading.Event.wait so the 10 s floor in _reap_loop doesn't apply) and makes every pass fail.
The parent holds off reading the child's stdout, so the child's reaper is blocked inside its write
(holding the lock) when the child's main thread returns. The parent then drains. On the old code
this aborts every time (SIGABRT, rc -6) on 3.10-3.13; with the fix the child exits 0.
No network: the store is a temp SQLite file.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_CHILD_PRELUDE = r"""
import sys, threading, time
_Event = threading.Event
class _FastEvent(_Event):
    def wait(self, timeout=None):
        return super().wait(None if timeout is None else min(timeout, 0.001))
threading.Event = _FastEvent   # shorten the reaper tick only; Thread.start's untimed wait is unchanged
import passiveworkers.net.store as store_mod
in_pass = _Event()
"""

# Every pass fails with a 2 MB message: one print overfills the 64 KB pipe and blocks in write().
_CHILD_FAILING_PASS = _CHILD_PRELUDE + r"""
def failing_pass(self):
    in_pass.set()
    raise RuntimeError("x" * 2_000_000)
store_mod.Store._reap_once = failing_pass
store = store_mod.Store(sys.argv[1])
if sys.argv[2:] == ["reload"]:   # tests reload this module constantly; a Store made before must still stop
    import importlib
    importlib.reload(store_mod)
in_pass.wait(10)
time.sleep(0.3)   # the reaper is now blocked in print() holding stdout's lock
sys.stderr.write("main-exiting\n"); sys.stderr.flush()
"""

# A pass that never returns: the exit join must be bounded, never wedge shutdown.
_CHILD_STUCK_PASS = _CHILD_PRELUDE + r"""
store_mod._REAPER_EXIT_JOIN_S = 0.5
def stuck_pass(self):
    in_pass.set()
    _Event().wait()
store_mod.Store._reap_once = stuck_pass
store_mod.Store(sys.argv[1])
in_pass.wait(10)
sys.stderr.write("main-exiting\n"); sys.stderr.flush()
"""


def _run_child(code: str, db: Path, read_delay_s: float, *args: str):
    env = {**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    p = subprocess.Popen([sys.executable, "-c", code, str(db), *args], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env, cwd=str(ROOT))
    try:
        first = p.stderr.readline()      # wait until the child's main thread is about to return
        time.sleep(read_delay_s)         # ...and keep its stdout pipe full meanwhile
        _out, err = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
            p.communicate()
    return p.returncode, (first + err).decode("utf-8", "replace")


@pytest.mark.parametrize("reload", [False, True], ids=["plain", "after-module-reload"])
def test_reaper_printing_at_exit_does_not_abort_the_interpreter(tmp_path, reload):
    rc, err = _run_child(_CHILD_FAILING_PASS, tmp_path / "coord.db", 1.5, *(["reload"] if reload else []))
    assert "main-exiting" in err, err[-2000:]
    assert "Fatal Python error" not in err, err[-2000:]
    assert rc == 0, (rc, err[-2000:])


def test_a_stuck_reaper_cannot_wedge_interpreter_exit(tmp_path):
    t0 = time.monotonic()
    rc, err = _run_child(_CHILD_STUCK_PASS, tmp_path / "coord.db", 0.0)
    elapsed = time.monotonic() - t0
    assert "main-exiting" in err, err[-2000:]
    assert rc == 0, (rc, err[-2000:])
    assert elapsed < 30, elapsed     # bounded by _REAPER_EXIT_JOIN_S (0.5 s here) plus startup
