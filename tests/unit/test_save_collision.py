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
OLD, NEW = b"old" * 100, b"new" * 100

Pattern = Callable[[Path, Path], None]  # (target, its temp file with NEW in it)
WAITS: dict[
    tuple[str, str], float
] = {}  # how long each writer call that went through took
READS: dict[
    tuple[str, str], str
] = {}  # what each held reader saw (:func:`_reader_outcome`)


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

    handle = _win32.open_handle(path, oplock=oplock)
    out["atomic"] = handle.atomic
    with so.leased(handle) as f:
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


def _reader_outcome(out: dict[str, object]) -> str:
    """``dropped`` when the reader was told the game changed the file and its read raised
    :class:`ReadInterrupted` (never a torn file), ``whole`` when it read on undisturbed; with
    ``+lease`` / ``+atomic`` when the handle held the oplock / took it with the open."""
    if "read" not in out:
        return "-"
    if out.get("interrupted") and out["read"] == "interrupted":
        seen = "dropped"
    elif not out.get("interrupted") and out["read"] != "interrupted":
        seen = "whole"
    else:
        seen = f"odd:{out.get('interrupted')}"
    return (
        seen
        + ("+lease" if out.get("leased") else "")
        + ("+atomic" if out.get("atomic") else "")
    )


def run_cell(folder: Path, reader: str, pattern: str) -> str:
    """One reader against one pattern: ``refused:<winerror>`` or ``ok``, then
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
        WAITS[(reader, pattern)] = time.perf_counter() - start
        outcome = "ok"
    except OSError as exc:
        outcome = f"refused:{getattr(exc, 'winerror', None) or exc.errno}"
    if t is not None:
        t.join(HOLD_S + 5)
        assert not t.is_alive(), "the reader hung"
        READS[(reader, pattern)] = _reader_outcome(out)
    after = target.read_bytes() if target.exists() else None
    state = {None: "gone", OLD: "old", NEW: "new"}.get(after, "other")
    if pattern == "append" and after is not None:
        state = "new" if after.endswith(b"line\n") else "old"
    if pattern == "in_place" and after is not None and after == NEW:
        state = "new"
    return f"{outcome}/{state}"


# What Windows CI measured (D357; run 44516182476, job 135532648226, on the commit before the fix
# was wired in, where ``safe_open`` was the ``shared`` reader). Under the oplock no writer was
# refused; the longest any writer call waited is checked separately (:data:`WAIT_MAX_S`).
REFUSED = "refused:5/old"
SHARING = "refused:32/old"
EXPECTED: dict[str, dict[str, str]] = {
    "none": dict.fromkeys(PATTERNS, "ok/new"),
    "plain": {
        "replace": REFUSED,
        "replace_through": REFUSED,
        "os_replace": REFUSED,
        "delete_move": SHARING,
        "rename_away": SHARING,
        "replacefile": SHARING,
        "posix_rename": SHARING,
        "in_place": "ok/new",
        "append": "ok/new",
    },
    "shared": {
        "replace": REFUSED,
        "replace_through": REFUSED,
        "os_replace": REFUSED,
        "delete_move": "ok/new",
        "rename_away": "ok/new",
        "replacefile": "ok/new",
        "posix_rename": "ok/new",
        "in_place": "ok/new",
        "append": "ok/new",
    },
    "oplock": dict.fromkeys(PATTERNS, "ok/new"),
}
WAIT_MAX_S = 0.5  # the longest a writer may wait for an oplocked reader to give way


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

    waits = {
        p: round(WAITS[("oplock", p)], 3) for p in PATTERNS if ("oplock", p) in WAITS
    }
    reads = {
        r: {p: READS.get((r, p), "-") for p in PATTERNS} for r in ("shared", "oplock")
    }
    warnings.warn(
        f"save collision table:\n{_table(got)}\noplock waits (s): {waits}\n"
        f"what the held reader saw:\n{_table(reads)}",
        stacklevel=1,
    )
    assert got == EXPECTED, f"measured:\n{_table(got)}\nexpected:\n{_table(EXPECTED)}"
    assert max(waits.values()) < WAIT_MAX_S, waits
    # Review L3: under the oplock every write pattern, in place and appends included, reaches
    # the reader as a dropped read, never as bytes the game changed under it.
    assert reads["oplock"] == dict.fromkeys(PATTERNS, "dropped+lease+atomic"), _table(
        reads
    )


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
def test_a_log_the_game_holds_open_is_read_and_its_appends_go_on(
    tmp_path: Path,
) -> None:
    """log4j holds its logs open and appends through that handle. Windows grants the oplock
    beside it, and an append through that handle breaks it (both measured on CI, D357). The
    appends go on while GOS reads, and ``read_range`` (``LogTail``) keeps a range it read."""
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
            assert f.read(2) == b"x\n"
            held.write(b"y\n")
            held.flush()
        assert so.read_range(log, 0, 4) == b"x\ny\n"
        held.write(b"z\n")
        held.flush()
    assert log.read_bytes() == b"x\ny\nz\n"


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


# --- the moment between the open and the oplock (review M1) -------------------------------------

GAP_S = 0.3  # how long the reader is held between its open and its oplock request
GAP_PATTERNS = (
    "replace",
    "replace_through",
    "os_replace",
    "delete_move",
    "rename_away",
    "in_place",
)
REPLACES = ("replace", "replace_through", "os_replace")


def run_gap(folder: Path, atomic: bool, pattern: str) -> str:
    """A reader stopped between its open and its oplock request for :data:`GAP_S`, and the
    game's call made in that moment: ``<the call after GAP_S>/<its end>/<target>/<reader>``,
    where the call is ``waiting``, ``ok`` or ``refused:<winerror>``."""
    from gregtec.gamestate import _win32

    folder.mkdir(parents=True)
    target, tmp = folder / "QuestProgress.json", folder / "QuestProgress.json.tmp"
    target.write_bytes(OLD)
    tmp.write_bytes(NEW)
    in_gap, go_on = threading.Event(), threading.Event()

    class Paused(_win32.Win32Handle):
        def _request(self) -> None:
            in_gap.set()
            go_on.wait(5)
            super()._request()

    out: dict[str, object] = {}

    def reader() -> None:
        handle = Paused(target, oplock=True, atomic=atomic)
        out["atomic"] = handle.atomic
        with so.leased(handle) as f:
            out["leased"] = so.is_leased(f)
            end = time.monotonic() + HOLD_S
            while time.monotonic() < end and not so.interrupted(f):
                time.sleep(0.005)
            out["interrupted"] = so.interrupted(f)
            try:
                out["read"] = f.read()
            except so.ReadInterrupted:
                out["read"] = "interrupted"

    wrote: dict[str, str] = {}

    def writer() -> None:
        try:
            PATTERNS[pattern](target, tmp)
            wrote["call"] = "ok"
        except OSError as exc:
            wrote["call"] = f"refused:{getattr(exc, 'winerror', None) or exc.errno}"

    rt = threading.Thread(target=reader, daemon=True)
    rt.start()
    assert in_gap.wait(5), "the reader didn't open"
    wt = threading.Thread(target=writer, daemon=True)
    wt.start()
    wt.join(GAP_S)
    during = wrote.get("call", "waiting")
    go_on.set()
    wt.join(HOLD_S + 5)
    rt.join(HOLD_S + 5)
    assert not wt.is_alive() and not rt.is_alive(), "hung"
    after = target.read_bytes() if target.exists() else None
    state = {None: "gone", OLD: "old", NEW: "new"}.get(after, "other")
    return f"{during}/{wrote['call']}/{state}/{_reader_outcome(out)}"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' oplocks")
def test_the_open_and_the_oplock_are_one_step(tmp_path: Path) -> None:
    """Review M1. With ``CreateFileW`` and then the request (``atomic=False``), a replace made
    between the two is refused, as for the old reader. With ``FILE_OPEN_REQUIRING_OPLOCK``
    (safe_open's open), every replace, rename or delete made in that moment waits until the
    oplock is requested, then breaks it and goes on, and an in-place write goes on at once:
    there is no moment when a save can be refused (:data:`EXPECTED_GAP`)."""
    import warnings

    got = {
        mode: {
            p: run_gap(tmp_path / mode / p, mode == "atomic", p) for p in GAP_PATTERNS
        }
        for mode in ("createfile", "atomic")
    }
    table = "\n".join(
        f"{m:10} {p:16} {c}" for m, row in got.items() for p, c in row.items()
    )
    warnings.warn(f"open-then-oplock table:\n{table}", stacklevel=1)
    for p in REPLACES:
        assert got["createfile"][p] == "refused:5/refused:5/old/whole+lease", table
    assert got["atomic"] == EXPECTED_GAP, table


# What Windows CI measured (D357; run 44545311387, job 135634178974). Every call that needs the
# file gone waits for the request, then breaks the oplock. An in-place write (an open to write
# that shares with ours) isn't held: it is done before the request, and the oplock then covers
# the new bytes (a later write through that handle breaks it, as an append does).
EXPECTED_GAP = {
    **dict.fromkeys(GAP_PATTERNS, "waiting/ok/new/dropped+lease+atomic"),
    "in_place": "ok/ok/new/whole+lease+atomic",
}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' oplocks")
def test_safe_open_takes_the_oplock_with_the_open_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review M2: safe_open's handle is the atomic one, and the process log says so once (the
    line the Lab check looks for in watchers.log)."""
    import logging

    monkeypatch.setattr(so, "_reported_pairs", set())
    caplog.set_level(logging.INFO, logger="gregtec.gamestate")
    target = tmp_path / "level.dat"
    target.write_bytes(OLD)
    for _ in range(2):
        with so.safe_open(target) as f:
            assert so.is_leased(f)
            assert f.read() == OLD
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("INFO", so.GRANTED)
    ]
