"""safe_open's give-way logic (WO-6.8, D357) on a fake handle, so it runs on every OS.

:class:`FakeHandle` stands for the Win32 handle: :meth:`FakeHandle.game_wants_it` is the game's
replace (or delete, rename, conflicting open) breaking the oplock and then waiting until the handle
is closed, as Windows makes it wait. The real Win32 path runs on Windows CI
(``test_save_collision.py`` and the soak).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import pytest

from gregtec.gamestate import safe_open as so

REAL_SLEEP = time.sleep  # the tests patch time.sleep to count safe_open's backoff


class FakeHandle:
    def __init__(self, data: bytes, leased: bool = True, read_s: float = 0.0) -> None:
        self.data = data
        self.leased = leased
        self.read_s = read_s
        self._broken = threading.Event()
        self._wake = threading.Event()
        self.closed = threading.Event()
        self.close_calls = 0
        self.disposed = False
        self.reads: list[tuple[int, int]] = []
        self.reading = False
        self.closed_mid_read = False
        self.on_read: Callable[[int], None] | None = None

    def size(self) -> int:
        return len(self.data)

    def read_at(self, offset: int, size: int) -> bytes:
        assert not self.closed.is_set(), "read after close"
        self.reading = True
        try:
            self.reads.append((offset, size))
            if self.on_read is not None:
                self.on_read(len(self.reads))
            if self.read_s:
                REAL_SLEEP(self.read_s)
            return self.data[offset : offset + size]
        finally:
            self.reading = False

    def broken(self) -> bool:
        return self._broken.is_set()

    def wait_break(self) -> bool:
        self._wake.wait()
        return self._broken.is_set()

    def release(self) -> None:
        self._wake.set()

    def close(self) -> None:
        self.closed_mid_read = self.closed_mid_read or self.reading
        self.close_calls += 1
        self.closed.set()

    def dispose(self) -> None:
        assert self.closed.is_set()
        self.disposed = True

    def game_wants_it(self, timeout: float = 5.0) -> float:
        """The game's replace: break the oplock, then wait for the handle to close. Returns how
        long the game waited."""
        start = time.perf_counter()
        self._broken.set()
        self._wake.set()
        assert self.closed.wait(timeout), "the game waited for ever"
        return time.perf_counter() - start


def test_a_whole_read_in_chunks_then_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(so, "CHUNK", 4)
    h = FakeHandle(b"0123456789")
    with so.leased(h) as f:
        assert so.is_leased(f)
        assert f.read() == b"0123456789"
        so.check_whole(f)
        assert not so.interrupted(f)
    assert h.close_calls == 1 and h.disposed
    assert all(n <= 4 for _, n in h.reads)


def test_seek_tell_and_ranges() -> None:
    h = FakeHandle(b"abcdefghij")
    with so.leased(h) as f:
        f.seek(3)
        assert f.read(4) == b"defg"
        assert f.tell() == 7
        f.seek(-2, 2)
        assert f.read() == b"ij"


def test_the_game_breaking_in_mid_read_closes_the_handle_between_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(so, "CHUNK", 2)
    h = FakeHandle(b"0123456789", read_s=0.05)
    waited: list[float] = []

    def on_read(n: int) -> None:
        if n == 2:  # the game replaces the file during the second chunk
            t = threading.Thread(target=lambda: waited.append(h.game_wants_it()))
            t.start()

    h.on_read = on_read
    with so.leased(h) as f, pytest.raises(so.ReadInterrupted):
        f.read()
    assert (
        h.close_calls == 1 and not h.closed_mid_read
    )  # closed after a chunk, never during
    assert len(h.reads) == 2  # nothing read after the break
    REAL_SLEEP(0.2)
    assert waited and waited[0] < 1.0


def test_an_idle_reader_still_gives_way_at_once() -> None:
    """A caller that holds the file and does nothing (a slow parse, a stuck thread) doesn't
    keep the game waiting: the oplock watcher closes the handle itself."""
    h = FakeHandle(b"data")
    with so.leased(h) as f:
        assert f.read(2) == b"da"
        waited = h.game_wants_it()
        assert waited < 1.0
        assert so.interrupted(f)
        with pytest.raises(so.ReadInterrupted):
            f.read()
        with pytest.raises(so.ReadInterrupted):
            so.check_whole(f)
    assert h.close_calls == 1


def test_a_break_after_the_last_chunk_still_drops_the_read() -> None:
    """The break came after the bytes were read but before the caller checked: the data may be
    from a file being rewritten in place, so it is not returned (retried instead)."""
    h = FakeHandle(b"data")
    with so.leased(h) as f:
        assert f.read() == b"data"
        h.game_wants_it()
        with pytest.raises(so.ReadInterrupted):
            so.check_whole(f)


def test_without_an_oplock_it_is_a_plain_read() -> None:
    h = FakeHandle(b"log line\n", leased=False)
    with so.leased(h) as f:
        assert not so.is_leased(f)
        assert f.read() == b"log line\n"
        so.check_whole(f)
    assert h.close_calls == 1


@contextmanager
def _opens(handles: list[FakeHandle]) -> Iterator[Callable[[so.StrPath], object]]:
    it = iter(handles)

    @contextmanager
    def fake_safe_open(_path: so.StrPath) -> Iterator[BinaryIO]:
        with so.leased(next(it)) as f:
            yield f

    yield fake_safe_open


def test_read_bytes_retries_a_read_the_game_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(so.time, "sleep", sleeps.append)
    first = FakeHandle(b'{"old": tr')
    first.on_read = lambda n: threading.Thread(target=first.game_wants_it).start()
    second = FakeHandle(b'{"new": true}')
    with _opens([first, second]) as fake:
        monkeypatch.setattr(so, "safe_open", fake)
        # A short real wait so the break lands before read_bytes checks.
        first.read_s = 0.05
        assert so.read_bytes(tmp_path / "QuestProgress.json") == b'{"new": true}'
    assert sleeps == [so.BACKOFF_S]
    assert first.close_calls == 1 and second.close_calls == 1


def test_read_bytes_gives_up_after_three_interrupted_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(so.time, "sleep", sleeps.append)
    handles = [FakeHandle(b"x", read_s=0.05) for _ in range(so.RETRIES)]
    for h in handles:
        h.on_read = lambda n, h=h: threading.Thread(target=h.game_wants_it).start()
    with _opens(handles) as fake:
        monkeypatch.setattr(so, "safe_open", fake)
        with pytest.raises(so.ReadInterrupted):
            so.read_bytes(tmp_path / "level.dat")
    assert sleeps == [so.BACKOFF_S, so.BACKOFF_S * 2]
    assert all(h.close_calls == 1 for h in handles)


def test_read_range_and_sha256_drop_interrupted_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(so.time, "sleep", lambda _s: None)
    a = FakeHandle(b"0123456789", read_s=0.05)
    a.on_read = lambda n: threading.Thread(target=a.game_wants_it).start()
    b = FakeHandle(b"0123456789")
    with _opens([a, b]) as fake:
        monkeypatch.setattr(so, "safe_open", fake)
        assert so.read_range(tmp_path / "r.0.0.mca", 2, 3) == b"234"
    c = FakeHandle(b"jar", read_s=0.05)
    c.on_read = lambda n: threading.Thread(target=c.game_wants_it).start()
    d = FakeHandle(b"jar")
    with _opens([c, d]) as fake:
        monkeypatch.setattr(so, "safe_open", fake)
        import hashlib

        assert so.sha256_file(tmp_path / "x.jar") == hashlib.sha256(b"jar").hexdigest()


def test_copy_to_raises_when_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    a = FakeHandle(b"region", read_s=0.05)
    a.on_read = lambda n: threading.Thread(target=a.game_wants_it).start()
    with _opens([a]) as fake:
        monkeypatch.setattr(so, "safe_open", fake)
        with pytest.raises(so.ReadInterrupted):
            so.copy_to(tmp_path / "x", io.BytesIO())
