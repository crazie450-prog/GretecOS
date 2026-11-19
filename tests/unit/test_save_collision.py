"""The Windows save collision, measured (WO-6.8, D356-D358; G11 finding 1, V27).

Each write pattern GTNH 2.9.0's game, mods and Java runtime really use (D356's facts table) is run
through the same Win32 calls Java makes, against a file a reader holds open, for each reader:

- ``none``: nobody holds the file (the control);
- ``plain``: Python's ``open()`` (shares read and write, not delete);
- ``shared``: ``safe_open`` before WO-6.8 (``CreateFileW`` sharing read, write and delete);
- ``oplock``: ``safe_open`` since WO-6.8 (the same handle plus a read-handle oplock, D357).

A held reader keeps its handle for :data:`HOLD_S` (the forced long read) unless the game's call
breaks its oplock, which closes it at once. Every cell records whether the writer's call was
refused (and the Windows error), how long it waited, and what the file held afterwards. The
expected table (:data:`EXPECTED`) is what Windows CI showed (D357 cites the runs); the test fails
with the whole measured table if any cell differs. On Linux the patterns that exist there (a
rename onto, a rename away, delete then rename, in place, append) run against a held plain
handle and none is refused; the Win32-only ones are skipped with a reason.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from gregtec.gamestate import safe_open as so

pytestmark = pytest.mark.serial

HOLD_S = 1.0  # the forced long read: far longer than any real read
WAITED_S = 0.15  # a writer call that took longer than this waited for the reader
OLD, NEW = b"old" * 100, b"new" * 100

Pattern = Callable[[Path, Path], None]  # (target, its temp file with NEW in it)


# --- the writers: the Win32 calls Java and the game make (D356) --------------------------------


def _k32() -> object:
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    k.MoveFileExW.restype = wintypes.BOOL
    k.DeleteFileW.argtypes = [wintypes.LPCWSTR]
    k.DeleteFileW.restype = wintypes.BOOL
    k.ReplaceFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    k.ReplaceFileW.restype = wintypes.BOOL
    k.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    k.CreateFileW.restype = wintypes.HANDLE
    k.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    k.WriteFile.restype = wintypes.BOOL
    k.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    k.SetFileInformationByHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return k


def _check(ok: int) -> None:
    import ctypes

    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]


MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_WRITE_THROUGH = 0x8


def p_replace(target: Path, tmp: Path) -> None:
    """Java ``Files.move(ATOMIC_MOVE[, REPLACE_EXISTING])``: ``MoveFileExW(REPLACE_EXISTING)``
    (BetterQuesting, Hodgepodge's level.dat; ``os.replace`` makes the same call)."""
    k = _k32()
    _check(k.MoveFileExW(str(tmp), str(target), MOVEFILE_REPLACE_EXISTING))  # type: ignore[attr-defined]


def p_replace_through(target: Path, tmp: Path) -> None:
    """``MoveFileExW(REPLACE_EXISTING | WRITE_THROUGH)``."""
    k = _k32()
    _check(
        k.MoveFileExW(  # type: ignore[attr-defined]
            str(tmp), str(target), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
        )
    )


def p_os_replace(target: Path, tmp: Path) -> None:
    """Python's ``os.replace`` (the fake game's save)."""
    os.replace(tmp, target)


def p_delete_move(target: Path, tmp: Path) -> None:
    """Java ``Files.move(REPLACE_EXISTING)`` without ATOMIC_MOVE: ``DeleteFileW(target)`` then
    ``MoveFileExW(tmp, target, 0)``; vanilla's player ``.dat`` save (``delete()`` then
    ``renameTo``) makes the same two calls."""
    if sys.platform == "win32":
        k = _k32()
        _check(k.DeleteFileW(str(target)))  # type: ignore[attr-defined]
        _check(k.MoveFileExW(str(tmp), str(target), 0))  # type: ignore[attr-defined]
    else:
        os.unlink(target)
        os.rename(tmp, target)


def p_rename_away(target: Path, tmp: Path) -> None:
    """Vanilla 1.7.10's level.dat save: ``level.dat`` -> ``level.dat_old``, then the new file in
    (``File.renameTo``, ``MoveFileExW`` without replace)."""
    old = target.with_name(target.name + "_old")
    if sys.platform == "win32":
        k = _k32()
        _check(k.MoveFileExW(str(target), str(old), 0))  # type: ignore[attr-defined]
        _check(k.MoveFileExW(str(tmp), str(target), 0))  # type: ignore[attr-defined]
    else:
        os.rename(target, old)
        os.rename(tmp, target)


def p_replacefile(target: Path, tmp: Path) -> None:
    """``ReplaceFileW`` (no Java call uses it; .NET's ``File.Replace`` does)."""
    k = _k32()
    _check(k.ReplaceFileW(str(target), str(tmp), None, 0, None, None))  # type: ignore[attr-defined]


def p_posix_rename(target: Path, tmp: Path) -> None:
    """``SetFileInformationByHandle(FileRenameInfoEx)`` with REPLACE_IF_EXISTS and
    POSIX_SEMANTICS (Windows 10 1709+; no Java version uses it)."""
    import ctypes

    k = _k32()
    delete, share_all, open_existing = 0x00010000, 0x7, 3
    h = k.CreateFileW(str(tmp), delete, share_all, None, open_existing, 0x80, None)  # type: ignore[attr-defined]
    if h is None or h == ctypes.c_void_p(-1).value:
        _check(0)
    try:
        name = str(target).encode("utf-16-le")
        buf = ctypes.create_string_buffer(20 + len(name) + 2)
        ctypes.c_uint32.from_buffer(buf, 0).value = (
            0x1 | 0x2
        )  # REPLACE_IF_EXISTS | POSIX
        ctypes.c_uint32.from_buffer(buf, 16).value = len(name)
        ctypes.memmove(ctypes.addressof(buf) + 20, name, len(name))
        file_rename_info_ex = 22
        _check(
            k.SetFileInformationByHandle(h, file_rename_info_ex, buf, len(buf))  # type: ignore[attr-defined]
        )
    finally:
        k.CloseHandle(h)  # type: ignore[attr-defined]


def p_in_place(target: Path, tmp: Path) -> None:
    """Java ``new FileOutputStream(target)``: ``CreateFileW(GENERIC_WRITE, share read|write,
    CREATE_ALWAYS)``, write, close (VisualProspecting, vanilla stats, commons-io)."""
    data = tmp.read_bytes()
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k = _k32()
        h = k.CreateFileW(str(target), 0x40000000, 0x1 | 0x2, None, 2, 0x80, None)  # type: ignore[attr-defined]
        if h is None or h == ctypes.c_void_p(-1).value:
            _check(0)
        try:
            n = wintypes.DWORD()
            _check(k.WriteFile(h, data, len(data), ctypes.byref(n), None))  # type: ignore[attr-defined]
        finally:
            k.CloseHandle(h)  # type: ignore[attr-defined]
    else:
        with open(target, "wb") as f:
            f.write(data)


def p_append(target: Path, tmp: Path) -> None:
    """An append (``FileWriter(file, true)``: ServerUtilities' world.log; log4j's own log)."""
    with open(target, "ab") as f:
        f.write(b"line\n")


PATTERNS: dict[str, Pattern] = {
    "replace": p_replace,
    "replace_through": p_replace_through,
    "os_replace": p_os_replace,
    "delete_move": p_delete_move,
    "rename_away": p_rename_away,
    "replacefile": p_replacefile,
    "posix_rename": p_posix_rename,
    "in_place": p_in_place,
    "append": p_append,
}
WIN32_ONLY = {"replace", "replace_through", "replacefile", "posix_rename"}


# --- the readers --------------------------------------------------------------------------------


def _hold_plain(path: Path, opened: threading.Event, out: dict[str, object]) -> None:
    with open(path, "rb") as f:
        opened.set()
        time.sleep(HOLD_S)
        out["read"] = f.read()


def _hold_win32(
    path: Path, opened: threading.Event, out: dict[str, object], oplock: bool
) -> None:
    from gregtec.gamestate import _win32

    with so.leased(_win32.open_handle(path, oplock=oplock)) as f:
        out["leased"] = so.is_leased(f)
        opened.set()
        end = time.monotonic() + HOLD_S
        while time.monotonic() < end and not so.interrupted(f):
            time.sleep(0.005)
        out["interrupted"] = so.interrupted(f)
        try:
            out["read"] = f.read()
        except so.ReadInterrupted:
            out["read"] = "interrupted"


READERS: dict[
    str, Callable[[Path, threading.Event, dict[str, object]], None] | None
] = {
    "none": None,
    "plain": _hold_plain,
    "shared": lambda p, e, o: _hold_win32(p, e, o, oplock=False),
    "oplock": lambda p, e, o: _hold_win32(p, e, o, oplock=True),
}


def run_cell(folder: Path, reader: str, pattern: str) -> str:
    """One reader against one pattern: ``refused:<winerror>``, ``ok`` or ``waited``, then
    ``/`` what the target holds after (``new``, ``old``, ``gone``)."""
    folder.mkdir(parents=True)
    target, tmp = folder / "QuestProgress.json", folder / "QuestProgress.json.tmp"
    target.write_bytes(OLD)
    tmp.write_bytes(NEW)
    hold = READERS[reader]
    opened, out = threading.Event(), {}
    t = None
    if hold is not None:
        t = threading.Thread(target=hold, args=(target, opened, out), daemon=True)
        t.start()
        assert opened.wait(5), "the reader didn't open"
    start = time.perf_counter()
    try:
        PATTERNS[pattern](target, tmp)
        took = time.perf_counter() - start
        outcome = "waited" if took > WAITED_S else "ok"
    except OSError as exc:
        outcome = f"refused:{getattr(exc, 'winerror', None) or exc.errno}"
    if t is not None:
        t.join(HOLD_S + 5)
        assert not t.is_alive(), "the reader hung"
    after = target.read_bytes() if target.exists() else None
    state = {None: "gone", OLD: "old", NEW: "new"}.get(after, "other")
    if pattern == "append" and after is not None:
        state = "new" if after.endswith(b"line\n") else "old"
    if pattern == "in_place" and after is not None and after == NEW:
        state = "new"
    return f"{outcome}/{state}"


# What Windows CI measured (D357). "waited" = the writer's call went on once the reader's oplock
# watcher closed the handle.
REFUSED = "refused:5/old"
EXPECTED: dict[str, dict[str, str]] = {
    "none": dict.fromkeys(PATTERNS, "ok/new"),
    "plain": {
        "replace": REFUSED,
        "replace_through": REFUSED,
        "os_replace": REFUSED,
        "delete_move": "refused:32/old",
        "rename_away": "refused:32/old",
        "replacefile": "refused:32/old",
        "posix_rename": REFUSED,
        "in_place": "ok/new",
        "append": "ok/new",
    },
    "shared": {
        "replace": REFUSED,
        "replace_through": REFUSED,
        "os_replace": REFUSED,
        "delete_move": "ok/new",
        "rename_away": "ok/new",
        "replacefile": REFUSED,
        "posix_rename": "ok/new",
        "in_place": "ok/new",
        "append": "ok/new",
    },
    "oplock": {
        "replace": "waited/new",
        "replace_through": "waited/new",
        "os_replace": "waited/new",
        "delete_move": "waited/new",
        "rename_away": "waited/new",
        "replacefile": "waited/new",
        "posix_rename": "ok/new",
        "in_place": "ok/new",
        "append": "ok/new",
    },
}


def _table(got: dict[str, dict[str, str]]) -> str:
    lines = [f"{'reader':8} " + " ".join(f"{p:>16}" for p in PATTERNS)]
    for r, row in got.items():
        lines.append(f"{r:8} " + " ".join(f"{row.get(p, '-'):>16}" for p in PATTERNS))
    return "\n".join(lines)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' share modes and oplocks")
def test_each_write_pattern_against_each_reader_on_windows(tmp_path: Path) -> None:
    got: dict[str, dict[str, str]] = {}
    for reader in READERS:
        got[reader] = {}
        for pattern in PATTERNS:
            got[reader][pattern] = run_cell(
                tmp_path / reader / pattern, reader, pattern
            )
    import warnings

    warnings.warn(f"save collision table:\n{_table(got)}", stacklevel=1)
    assert got == EXPECTED, f"measured:\n{_table(got)}\nexpected:\n{_table(EXPECTED)}"


@pytest.mark.skipif(sys.platform == "win32", reason="the Linux half of the table")
@pytest.mark.parametrize("pattern", sorted(PATTERNS), ids=lambda p: str(p))
def test_no_pattern_is_refused_on_linux(tmp_path: Path, pattern: str) -> None:
    """Linux never refuses a rename, delete or write because a file is open."""
    if pattern in WIN32_ONLY:
        pytest.skip(
            f"{pattern}: a Win32-only call (MoveFileExW, ReplaceFileW, FileRenameInfoEx)"
        )
    assert run_cell(tmp_path / "plain", "plain", pattern) == "ok/new"
    assert run_cell(tmp_path / "none", "none", pattern) == "ok/new"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' oplocks")
def test_oplock_granted_on_a_quiet_file_not_on_one_open_to_write(
    tmp_path: Path,
) -> None:
    """The oplock is the guard for the files the game replaces (quiet between saves); a log the
    game holds open to write gets none and is read plainly (nobody replaces a log)."""
    from gregtec.gamestate import _win32

    quiet = tmp_path / "QuestProgress.json"
    quiet.write_bytes(OLD)
    with so.leased(_win32.open_handle(quiet)) as f:
        assert so.is_leased(f)
        assert f.read() == OLD
    log = tmp_path / "latest.log"
    with open(log, "ab") as held:
        held.write(b"x\n")
        held.flush()
        with so.leased(_win32.open_handle(log)) as f:
            assert not so.is_leased(f)
            assert f.read() == b"x\n"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' oplocks")
def test_a_replace_from_the_reading_thread_itself_does_not_deadlock(
    tmp_path: Path,
) -> None:
    """The oplock watcher closes the handle on its own thread, so even a replace made while
    the reader's own thread is busy goes on (and the read says it was interrupted)."""
    target, tmp = tmp_path / "level.dat", tmp_path / "level.dat.tmp"
    target.write_bytes(OLD)
    tmp.write_bytes(NEW)
    with so.safe_open(target) as f:
        done = threading.Event()

        def save() -> None:
            os.replace(tmp, target)
            done.set()

        t = threading.Thread(target=save, daemon=True)
        t.start()
        assert done.wait(10), "the save waited on the reader"
        with pytest.raises(so.ReadInterrupted):
            f.read()
    assert target.read_bytes() == NEW
