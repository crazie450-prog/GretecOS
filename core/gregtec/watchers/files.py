"""Reading the game's files the way plan 5.5 asks, for the watchers (WO-6.1, D257).

Every read goes through :mod:`gregtec.gamestate.safe_open` (share read, write and delete on
Windows; open, read, close at once; 3 tries with backoff). Nothing here keeps a handle between
calls: a change is noticed with ``os.stat`` (on Windows a stat opens no handle that could block
a rename: it asks for attributes only, and Python 3.12 uses ``GetFileInformationByName`` where it
can), and a file is read only once its size and mtime have held still for a moment
(:class:`Settle`), so a reader never lands in the middle of the game's save burst.

**Log tails** (:class:`LogTail`) poll: stat, open, seek to the saved offset, read what is new,
close. A rotated log (log4j renames ``latest.log`` at every start and at midnight) shows as a new
file identity or a smaller size and is read from its start. The first look at a log starts at its
end, unless the tail's saved offset (``GOS_HOME/data/watchers/tails.json``) is for that same file,
so a watcher restarted mid-session goes on where it stopped and the last session's lines never
come back as new.
"""

from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec.gamestate import safe_open

MAX_READ = 1 << 20  # one poll reads at most this much of a log
MAX_LINE = (
    64 << 10
)  # a "line" longer than this is cut (a runaway stack trace without newlines)


@dataclass(frozen=True)
class Stamp:
    size: int
    mtime_ns: int
    ident: tuple[int, int]  # (st_dev, st_ino); (0, 0) where the file system has none


def stamp(path: Path) -> Stamp | None:
    """The file's size, mtime and identity, or None if it isn't there (or isn't a file)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return Stamp(st.st_size, st.st_mtime_ns, (st.st_dev, st.st_ino))


class Settle:
    """Says when a watched file (or a folder's set of files) changed and then held still.

    ``changed(key, value, now)`` is True once per change: when ``value`` differs from the last
    value it reported for ``key`` and has stayed the same for ``settle_s``. The first value seen
    for a key is its baseline and is reported too (``first`` tells a caller it is one).
    """

    def __init__(self, settle_s: float) -> None:
        self.settle_s = settle_s
        self._seen: dict[str, tuple[object, float]] = {}  # value, since
        self._done: dict[str, object] = {}

    def changed(self, key: str, value: object, now: float) -> bool:
        seen = self._seen.get(key)
        if seen is None or seen[0] != value:
            self._seen[key] = (value, now)
            if self.settle_s > 0:
                return False
            seen = self._seen[key]
        if key in self._done and self._done[key] == value:
            return False
        if now - seen[1] < self.settle_s:
            return False
        self._done[key] = value
        return True

    def first(self, key: str) -> bool:
        return key not in self._done


def read_range(path: Path, offset: int, size: int) -> bytes:
    """``size`` bytes of ``path`` from ``offset``: one open, seek, read and close per try."""
    error: OSError | None = None
    for attempt in range(safe_open.RETRIES):
        try:
            with safe_open.safe_open(path) as f:
                f.seek(offset)
                return f.read(size)
        except FileNotFoundError:
            raise
        except OSError as exc:
            error = exc
            if attempt < safe_open.RETRIES - 1:
                time.sleep(safe_open.BACKOFF_S * 2**attempt)
    assert error is not None
    raise error


class LogTail:
    """New complete lines of one log, by polling (never a held handle)."""

    def __init__(
        self,
        path: Path,
        saved: dict[str, Any] | None = None,
        *,
        from_start: bool = False,
    ) -> None:
        self.path = path
        self.offset = 0
        self.ident: tuple[int, int] | None = None
        self.pending = b""
        self.from_start = from_start
        if (
            saved
            and isinstance(saved.get("offset"), int)
            and isinstance(saved.get("ident"), list)
        ):
            self._saved: tuple[int, tuple[int, int]] | None = (
                saved["offset"],
                (int(saved["ident"][0]), int(saved["ident"][1])),
            )
        else:
            self._saved = None

    def state(self) -> dict[str, Any] | None:
        """What to save so a restart goes on from here (the offset of the last whole line)."""
        if self.ident is None:
            return None
        return {"offset": self.offset - len(self.pending), "ident": list(self.ident)}

    def poll(self) -> list[str]:
        st = stamp(self.path)
        if st is None:
            return []  # not there yet, or between log4j's rename and its new file
        if self.ident is None:
            self.ident = st.ident
            if (
                self._saved is not None
                and self._saved[1] == st.ident
                and self._saved[0] <= st.size
            ):
                self.offset = self._saved[0]
            elif not self.from_start:
                self.offset = st.size
                return []
        elif st.ident != self.ident or st.size < self.offset:
            # rotated (renamed away and a new file made) or truncated: the new file from its start
            self.ident, self.offset, self.pending = st.ident, 0, b""
        if st.size <= self.offset:
            return []
        try:
            data = read_range(
                self.path, self.offset, min(st.size - self.offset, MAX_READ)
            )
        except FileNotFoundError:
            return []
        self.offset += len(data)
        parts = (self.pending + data).split(b"\n")
        self.pending = parts.pop()
        if len(self.pending) > MAX_LINE:
            parts.append(self.pending[:MAX_LINE])
            self.pending = b""
        return [
            p[:MAX_LINE].decode("utf-8", errors="replace").rstrip("\r") for p in parts
        ]


def load_tails(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_tails(path: Path, tails: dict[str, Any]) -> None:
    """GOS's own file (never a game file): a temp file, then a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(tails), encoding="utf-8")
    os.replace(tmp, path)
