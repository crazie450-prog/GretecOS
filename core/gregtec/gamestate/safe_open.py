"""Read game files without getting in the game's way (plan section 5.5, PM13, CLAUDE.md; D356-D358).

On Windows a plain ``open()`` doesn't allow delete sharing, so while GOS holds a file the game
can't rename it. :func:`safe_open` opens read-only with read, write *and* delete sharing. That
lets the game rename a file GOS holds out of the way: vanilla's save (``level.dat`` ->
``level.dat_old``, then ``level.dat_new`` -> ``level.dat``) and the log4j log rollover.

Sharing alone does not let the game replace a file GOS holds. GTNH 2.9.0 saves level.dat
(Hodgepodge) and every BetterQuesting file with ``Files.move(..., ATOMIC_MOVE)``, which is
``MoveFileExW(MOVEFILE_REPLACE_EXISTING)``: Windows refuses that while any handle to the target
is open, whatever its sharing, and BetterQuesting logs the error and does not retry (D356). So on
Windows every handle also asks for a **read-handle oplock** (``FSCTL_REQUEST_OPLOCK``, level
read + handle caching, D357), in the same step as the open (``FILE_OPEN_REQUIRING_OPLOCK``), so
the handle is never open without it. A game that replaces, renames or deletes the file, or opens it in a
way that would conflict, breaks the oplock and waits; a thread of ours that waits for exactly that
closes the handle at once (between two chunk reads, never during one), and the game's call goes
on. A game that opens the file to write it in place breaks the oplock too. Either way the read is
marked :attr:`LeasedFile.interrupted`, and the whole-file readers here raise
:class:`ReadInterrupted` (an ``OSError``) and retry, so a read never returns a file the game
changed under it. A write through a handle the game opened *before* ours (log4j appending to its
log) breaks the oplock too: :func:`read_range`, which reads logs and region headers (appended or
patched in place, never replaced), keeps what it read, since its callers read a range they
measured first and check what they read, as before (D357); the whole-file readers drop it.
When the oplock isn't granted (a file system without oplocks) the read is a plain shared read,
as before, and :func:`report` logs a warning for that file.

The rule this keeps (CLAUDE.md, ``gamestate.safe_open``): share-delete (the oplock is on top of
it), copy then parse (read whole into memory, close, then parse), retry x3 (an interrupted read
is one of the retried errors), never hold handles (a handle is closed as soon as the game wants
the file, and always at the end of the read).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import BinaryIO, Protocol, cast

RETRIES = 3
BACKOFF_S = 0.2
CHUNK = (
    1 << 20
)  # one ReadFile at most: the longest the game can wait for a handle to close

StrPath = str | os.PathLike[str]

log = logging.getLogger("gregtec.gamestate")
GRANTED = (
    "safe_open: Windows grants read-handle oplocks with the open: a game save waits for a "
    "GOS read instead of failing (D357)"
)
NOT_GRANTED = (
    "safe_open: no oplock on this file: a game save onto it can be refused while GOS reads "
    "it (D357)"
)
NOT_ATOMIC = (
    "safe_open: the oplock came after the open here (no FILE_OPEN_REQUIRING_OPLOCK): a game "
    "save in that moment can be refused (D357)"
)


class ReadInterrupted(OSError):
    """The game started to change the file while GOS read it; the read was dropped (retry)."""


class Handle(Protocol):
    """One open file, as :class:`LeasedFile` uses it (Win32 in ``_win32``; fakes in tests)."""

    leased: bool  # the OS granted the oplock

    def size(self) -> int: ...

    def read_at(self, offset: int, size: int) -> bytes: ...

    def broken(self) -> bool:
        """Whether the oplock has been broken (never blocks)."""
        ...

    def wait_break(self) -> bool:
        """Block until the oplock breaks (True) or :meth:`release` is called (False)."""
        ...

    def release(self) -> None: ...

    def close(self) -> None:
        """Close the file handle (this is what lets a waiting game go on)."""
        ...

    def dispose(self) -> None:
        """Free what is left once the handle is closed."""
        ...


class LeasedFile(io.RawIOBase):
    """A read-only file over a :class:`Handle` that gives way to the game (module docstring).

    While the oplock holds, a watcher thread waits for it to break; when it does, the thread
    takes the read lock (so never in the middle of a read), closes the handle and marks the file
    :attr:`interrupted`. Every later read raises :class:`ReadInterrupted`.
    """

    def __init__(self, handle: Handle) -> None:
        super().__init__()
        self._h = handle
        self._lock = threading.Lock()
        self._pos = 0
        self._handle_closed = False
        self._interrupted = False
        self._watch: threading.Thread | None = None
        if handle.leased:
            self._watch = threading.Thread(
                target=self._give_way, name="safe_open-oplock", daemon=True
            )
            self._watch.start()

    @property
    def leased(self) -> bool:
        return self._h.leased

    @property
    def interrupted(self) -> bool:
        """The game wanted the file while it was open: what was read may be stale or torn."""
        return self._interrupted or (self._h.leased and self._h.broken())

    def _give_way(self) -> None:
        if self._h.wait_break():
            with self._lock:
                self._interrupted = True
                self._close_handle()

    def _close_handle(self) -> None:
        if not self._handle_closed:
            self._handle_closed = True
            self._h.close()

    def _check(self) -> None:
        if self.interrupted or self._handle_closed:
            raise ReadInterrupted("the game changed the file while it was read; retry")

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            pos = offset
        elif whence == io.SEEK_CUR:
            pos = self._pos + offset
        elif whence == io.SEEK_END:
            with self._lock:
                self._check()
                pos = self._h.size() + offset
        else:
            raise ValueError(f"bad whence {whence}")
        if pos < 0:
            raise ValueError("negative seek position")
        self._pos = pos
        return pos

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        view = memoryview(buffer).cast("B")
        with self._lock:
            self._check()
            data = self._h.read_at(self._pos, min(len(view), CHUNK))
        n = len(data)
        view[:n] = data
        self._pos += n
        return n

    def readall(self) -> bytes:
        parts = []
        while True:
            buf = bytearray(CHUNK)
            n = self.readinto(buf)
            if not n:
                return b"".join(parts)
            parts.append(bytes(buf[:n]))

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._h.release()
            if self._watch is not None:
                self._watch.join()
            with self._lock:
                self._close_handle()
            self._h.dispose()
        finally:
            super().close()


class _Reader(io.BufferedReader):
    """A buffered :class:`LeasedFile` that still says :attr:`interrupted` and :attr:`leased`."""

    @property
    def interrupted(self) -> bool:
        return cast(LeasedFile, self.raw).interrupted

    @property
    def leased(self) -> bool:
        return cast(LeasedFile, self.raw).leased


def interrupted(f: object) -> bool:
    """Whether ``f`` (from :func:`safe_open`) was given up to the game while it was open."""
    return bool(getattr(f, "interrupted", False))


def is_leased(f: object) -> bool:
    """Whether ``f`` (from :func:`safe_open`) holds an oplock, so it gives way to the game."""
    return bool(getattr(f, "leased", False))


def check_whole(f: object) -> None:
    """Raise :class:`ReadInterrupted` if what was read from ``f`` can't be trusted whole."""
    if interrupted(f):
        raise ReadInterrupted("the game changed the file while it was read; retry")


@contextlib.contextmanager
def leased(handle: Handle) -> Iterator[BinaryIO]:
    """``handle`` as a buffered file that gives way to the game (closed on exit)."""
    raw = LeasedFile(handle)
    with _Reader(raw, buffer_size=CHUNK) as f:
        yield cast(BinaryIO, f)


@contextlib.contextmanager
def safe_open(path: StrPath) -> Iterator[BinaryIO]:
    """Open ``path`` for reading in binary mode, sharing read, write and delete, and (Windows)
    giving way to the game the moment it wants the file (module docstring)."""
    if sys.platform != "win32":
        with open(path, "rb") as f:
            yield f
        return

    from gregtec.gamestate import _win32

    handle = _win32.open_handle(path)
    report(path, handle)
    with leased(handle) as f:
        yield f


_reported_pairs: set[tuple[str, str]] = (
    set()
)  # what report() has logged in this process
_report_lock = threading.Lock()


def report(path: StrPath, handle: object) -> None:
    """Say in the process's log (``watchers.log`` for the watchers) whether the oplock is there:
    once per process when Windows grants one with the open (``GRANTED``), and once per file when
    a read goes without it (``NOT_GRANTED``) or took it after the open (``NOT_ATOMIC``), the
    two cases where a game save can still be refused (D357). The Lab check reads these lines."""
    granted = bool(getattr(handle, "leased", False))
    atomic = bool(getattr(handle, "atomic", False))
    error = int(getattr(handle, "oplock_error", 0) or 0)
    if granted and atomic:
        key, level, message = "", logging.INFO, GRANTED
    else:
        key = os.fspath(path)
        level, message = logging.WARNING, NOT_ATOMIC if granted else NOT_GRANTED
    with _report_lock:
        if (key, message) in _reported_pairs:
            return
        _reported_pairs.add((key, message))
    log.log(level, message, extra={"path": os.fspath(path), "winerror": error})


def _retried[T](read: Callable[[], T], also: tuple[type[Exception], ...] = ()) -> T:
    """``read()`` up to :data:`RETRIES` times with backoff; a missing file is not retried."""
    error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            return read()
        except FileNotFoundError:
            raise
        except (OSError, *also) as exc:
            error = exc
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF_S * 2**attempt)
    assert error is not None
    raise error


def read_bytes(
    path: StrPath,
    validate: Callable[[bytes], object] | None = None,
    limit: int | None = None,
) -> bytes:
    """Read a whole file with :func:`safe_open`, retrying up to 3 times with backoff.

    ``validate`` may raise ``ValueError`` (a partial or corrupt read); that is retried too, and
    so is a read the game interrupted (:class:`ReadInterrupted`). A missing file is not retried.
    ``limit`` reads at most that many bytes, so a file that is far bigger than expected can't
    fill memory.
    """

    def once() -> bytes:
        with safe_open(path) as f:
            data = f.read() if limit is None else f.read(limit)
            check_whole(f)
        if validate is not None:
            validate(data)
        return data

    return _retried(once, (ValueError,))


def read_range(path: StrPath, offset: int, size: int) -> bytes:
    """``size`` bytes of ``path`` from ``offset``: one open, seek, read and close per try.

    For files the game appends to or patches in place (logs, region files): a break that came
    while the range was read doesn't drop it (an append breaks the oplock, and the caller read a
    range it measured first and checks what it read); a handle the game took back mid-read does.
    """

    def once() -> bytes:
        with safe_open(path) as f:
            f.seek(offset)
            return f.read(size)

    return _retried(once)


def read_text(path: StrPath, encoding: str = "utf-8") -> str:
    """:func:`read_bytes`, decoded; undecodable bytes become U+FFFD."""
    return read_bytes(path).decode(encoding, errors="replace")


def copy_to(path: StrPath, out: BinaryIO, chunk: int = CHUNK) -> None:
    """Stream ``path`` into ``out`` (one try; :func:`ReadInterrupted` if the game wanted it)."""
    with safe_open(path) as f:
        while block := f.read(chunk):
            out.write(block)
        check_whole(f)


def sha256_file(path: StrPath, chunk: int = CHUNK) -> str:
    """Hex SHA-256 of a file, streamed through :func:`safe_open` (for mod jars). Retried like
    :func:`read_bytes` (antivirus can hold a file for a moment); a missing file is not."""

    def once() -> str:
        digest = hashlib.sha256()
        with safe_open(path) as f:
            while block := f.read(chunk):
                digest.update(block)
            check_whole(f)
        return digest.hexdigest()

    return _retried(once)
