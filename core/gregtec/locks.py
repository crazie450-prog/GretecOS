"""The instance lock (WO-1.4, plan sections 4.3 and 5.1, V25, D121).

``GOS_HOME/data/locks/instance.lock`` is held by the export pipeline (``gos export run``) from
before it syncs Export until Export has quit. Play's pre-launch hook refuses the launch while it is
held, so Play and Export never run at once [A§5.5].

It is an OS file lock (``msvcrt.locking`` on one byte far past the end of the file, ``fcntl.flock``
elsewhere), held by the pipeline's process for its whole life. The OS drops it when that process
ends, however it ends, so a crashed export can never leave Play locked out. The file itself holds
the holder's description (JSON), readable while the lock is held; a description left by a dead
holder means nothing.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self

LOCK = Path("data", "locks", "instance.lock")  # in GOS_HOME
# Windows locks a byte range for reads too, so the lock sits far past the description at offset 0.
_LOCK_OFFSET = 0x7FFF_0000
MAX_RECORD = 64 << 10


@dataclass(frozen=True)
class Holder:
    holder: str  # what holds it, e.g. "export"
    pid: int
    since: str  # UTC
    reason: str
    # Set by whoever started the holder, to know it when it holds the lock (a Windows venv's
    # python.exe is a launcher, so the pid Popen returns isn't the holder's).
    ticket: str = ""

    def describe(self) -> str:
        return f"{self.holder} (pid {self.pid}, since {self.since}): {self.reason}"


UNKNOWN = Holder("unknown", 0, "?", "the lock's holder left no readable description")


class LockHeld(Exception):
    def __init__(self, holder: Holder) -> None:
        super().__init__(f"the instance lock is held by {holder.describe()}")
        self.holder = holder


def _try_lock(f: BinaryIO) -> bool:
    if sys.platform == "win32":
        import msvcrt

        f.seek(_LOCK_OFFSET)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(f: BinaryIO) -> None:
    if sys.platform == "win32":
        import msvcrt

        f.seek(_LOCK_OFFSET)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _open(path: Path) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    return os.fdopen(fd, "r+b")


def _read(path: Path) -> Holder:
    try:
        with open(path, "rb") as f:
            data = json.loads(f.read(MAX_RECORD))
        return Holder(
            str(data["holder"]),
            int(data["pid"]),
            str(data["since"]),
            str(data["reason"]),
            str(data.get("ticket", "")),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return UNKNOWN


class InstanceLock:
    """Hold the lock with ``with InstanceLock(home).acquire(...)`` or acquire/release."""

    def __init__(self, home: Path) -> None:
        self.path = home / LOCK
        self._f: BinaryIO | None = None

    def acquire(
        self, holder: str, reason: str, wait_s: float = 2.0, ticket: str = ""
    ) -> Self:
        """Take the lock or raise :class:`LockHeld`. Retries for ``wait_s`` (a checker holds it
        for a moment when it looks)."""
        f = _open(self.path)
        deadline = time.monotonic() + wait_s
        while not _try_lock(f):
            if time.monotonic() >= deadline:
                f.close()
                raise LockHeld(_read(self.path))
            time.sleep(0.1)
        record = {
            "holder": holder,
            "pid": os.getpid(),
            "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reason": reason,
            "ticket": ticket,
        }
        f.seek(0)
        f.truncate()
        f.write(json.dumps(record).encode("utf-8"))
        f.flush()
        self._f = f
        return self

    def release(self) -> None:
        f, self._f = self._f, None
        if f is None:
            return
        try:
            f.seek(0)
            f.truncate()
            f.flush()
            _unlock(f)
        finally:
            f.close()

    def __enter__(self) -> Self:
        if self._f is None:
            raise RuntimeError("acquire() the lock before entering it")
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def held(home: Path) -> Holder | None:
    """Who holds the lock, or None. Takes it for a moment to find out."""
    path = home / LOCK
    if not path.exists():
        return None
    with contextlib.ExitStack() as stack:
        f = stack.enter_context(_open(path))
        if _try_lock(f):
            _unlock(f)
            return None
    return _read(path)


@contextlib.contextmanager
def hold(home: Path, holder: str, reason: str) -> Iterator[InstanceLock]:
    lock = InstanceLock(home).acquire(holder, reason)
    with lock:
        yield lock
