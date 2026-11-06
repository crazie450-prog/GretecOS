"""The instance lock (WO-1.4, V25): an OS lock the OS drops when its holder dies."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gregtec import locks

HOLDER = """
import sys, time
from pathlib import Path
from gregtec import locks
lock = locks.InstanceLock(Path(sys.argv[1])).acquire("export", "child test")
print("held", flush=True)
time.sleep(60)
"""


def test_free_held_and_released(tmp_path: Path) -> None:
    assert locks.held(tmp_path) is None
    lock = locks.InstanceLock(tmp_path).acquire("export", "unit test")
    with lock:
        holder = locks.held(tmp_path)
        assert holder is not None
        assert (holder.holder, holder.pid, holder.reason) == (
            "export",
            os.getpid(),
            "unit test",
        )
        with pytest.raises(locks.LockHeld) as err:
            locks.InstanceLock(tmp_path).acquire("export", "second", wait_s=0.2)
        assert err.value.holder.reason == "unit test"
        assert locks.held(tmp_path) is not None  # looking doesn't free it
    assert locks.held(tmp_path) is None
    with locks.hold(tmp_path, "test", "again"):
        assert locks.held(tmp_path) is not None
    assert locks.held(tmp_path) is None


def test_entering_without_acquire_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), locks.InstanceLock(tmp_path):
        pass


def test_a_killed_holder_leaves_no_lock(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        holder = locks.held(tmp_path)
        # (not by pid: on Windows a venv's python.exe is a launcher with its own pid)
        assert holder is not None and holder.reason == "child test"
        with pytest.raises(locks.LockHeld):
            locks.InstanceLock(tmp_path).acquire("export", "parent", wait_s=0.2)
    finally:
        child.kill()
        child.wait()
    deadline = time.monotonic() + 10
    while locks.held(tmp_path) is not None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert locks.held(tmp_path) is None
    assert (
        tmp_path / locks.LOCK
    ).read_bytes()  # the record is still there; nobody holds it
    with locks.hold(tmp_path, "export", "after the crash"):
        assert locks.held(tmp_path) is not None


def test_unreadable_record_is_unknown(tmp_path: Path) -> None:
    lock = locks.InstanceLock(tmp_path).acquire("export", "x")
    with lock:
        with open(tmp_path / locks.LOCK, "r+b") as f:  # same file, another handle
            f.write(b"}not json")
        assert locks.held(tmp_path) == locks.UNKNOWN
