"""The read-safety soak (plan 8 WO-6.1 acceptance, plan 5.5, V27; D257).

A fake game (``fakegame.py``, its own process) writes, renames and locks a Lab-shaped tree the way
Minecraft 1.7.10 and GTNH 2.9.0's mods do: logs appended through a held handle and rolled over,
level.dat saved both Hodgepodge's way (temp + replace) and vanilla's (``_new``/``_old``
renames), BetterQuesting's files by replace, stats, VisualProspecting and JourneyMap in place,
``session.lock`` held and locked. Meanwhile every watcher reads it, accelerated (a save every
0.5 s instead of 45 s). The checks:

- **no write is blocked**: the game records every write, rename or replace the OS refused; there
  must be none (on Windows a handle without share-delete, or held across a replace, refuses
  them: the Windows-only control below shows the soak would see that);
- **the forced long read** (WO-6.8, G11 finding 1): the first read of BetterQuesting's progress
  file and of level.dat (the files the game replaces) holds its handle until the game has run two
  whole save cycles, or until ``safe_open`` gives the file up to the game. A save of that file
  therefore always lands while GOS has it open: before WO-6.8 (no oplock) Windows refused it
  (``WinError 5``, deterministically); since, the game's replace breaks the oplock, the handle is
  closed at once and the save goes on, and the read is retried;
- **no handle is ever held**: every game-file open goes through ``safe_open`` (an open of a game
  file any other way fails the test), none is open when a poll returns, none lasts long; on
  Linux ``/proc/self/fd`` is also checked after every poll (and the control shows that check
  sees a held handle);
- the watchers still saw what the game did (each event kind the fake game causes).

A 2 h session on the real game is Clayton's runbook step (docs/runbooks/watchers.md).
"""

from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, BinaryIO

import pytest

from gregtec.gamestate import safe_open
from gregtec.watchers import runner

FAKEGAME = Path(__file__).with_name("fakegame.py")
SECONDS = 8.0
INTERVAL = 0.5  # one save cycle (the game's is 45 s)
POLL_S = 0.1
FORCED = (
    "QuestProgress",
    "level.dat",
)  # the replaced files whose first read is held long
FORCED_MAX_S = 4.0  # the forced hold's cap (two save cycles take about 1.1 s)


def held_fds(root: Path) -> list[str]:
    """This process's open file descriptors that point into ``root`` (Linux)."""
    out = []
    fd_dir = Path("/proc/self/fd")
    for fd in os.listdir(fd_dir):
        try:
            target = os.readlink(fd_dir / fd)
        except OSError:
            continue
        if target.startswith(str(root)):
            out.append(target)
    return out


class Guard:
    """Counts safe_open's handles and catches any other open of a game file."""

    def __init__(self, root: Path, log: Path) -> None:
        self.root = str(root)
        self.log = log
        self.open = 0
        self.opens = 0
        self.longest = 0.0
        self.violations: list[str] = []
        self.inside = threading.local()
        self.to_force = set(FORCED)
        self.forced: list[
            tuple[str, float, bool, int]
        ] = []  # name, held, given up, cycles

    def hold(self, path: Any, f: BinaryIO) -> None:  # noqa: ANN401 - StrPath
        """The forced long read: keep ``f`` open until the game ran two whole save cycles (its
        log grew twice) or safe_open gave the file up to it."""
        name = os.path.basename(os.fspath(path))
        key = next((k for k in self.to_force if k in os.fspath(path)), None)
        if key is None:
            return
        self.to_force.discard(key)
        start = time.perf_counter()
        last, cycles = self._log_stamp(), 0
        while cycles < 2 and not safe_open.interrupted(f):
            if time.perf_counter() - start > FORCED_MAX_S:
                break
            time.sleep(0.005)
            now = self._log_stamp()
            if now != last:
                last, cycles = now, cycles + 1
        self.forced.append(
            (name, time.perf_counter() - start, safe_open.interrupted(f), cycles)
        )

    def _log_stamp(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.log)
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_safe = safe_open.safe_open
        real_open = builtins.open
        guard = self

        @contextlib.contextmanager
        def counted(path: Any) -> Iterator[BinaryIO]:  # noqa: ANN401 - StrPath
            guard.open += 1
            guard.opens += 1
            guard.inside.on = True
            start = time.perf_counter()
            forced = len(guard.forced)
            try:
                with real_safe(path) as f:
                    guard.inside.on = False
                    guard.hold(path, f)
                    yield f
            finally:
                guard.inside.on = False
                guard.open -= 1
                if (
                    len(guard.forced) == forced
                ):  # the forced holds are the test's, not GOS's
                    guard.longest = max(guard.longest, time.perf_counter() - start)

        def checked_open(file: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            if (
                isinstance(file, str | os.PathLike)
                and os.fspath(file).startswith(guard.root)
                and not getattr(guard.inside, "on", False)
            ):
                guard.violations.append(f"opened outside safe_open: {os.fspath(file)}")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(safe_open, "safe_open", counted)
        monkeypatch.setattr(builtins, "open", checked_open)
        monkeypatch.setattr(io, "open", checked_open)


def test_soak_no_handle_held_no_write_blocked(
    tmp_path: Path, fg: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "prism"
    tree = fg.Tree(root)
    home = tmp_path / "home"
    result = tmp_path / "game.json"
    guard = Guard(root, tree.game / "logs" / "latest.log")
    game = subprocess.Popen(
        [
            sys.executable,
            str(FAKEGAME),
            str(root),
            str(SECONDS),
            str(result),
            str(INTERVAL),
        ]
    )
    watchers = runner.Watchers(tree.game, tree.world, home, settle_s=0.1)
    guard.install(monkeypatch)
    events = []
    polls = 0
    fd_problems: list[str] = []
    try:
        while game.poll() is None:
            events += watchers.poll_logs()
            events += watchers.poll_files()
            polls += 1
            assert guard.open == 0, "a poll returned with a game file open"
            if sys.platform.startswith("linux"):
                fd_problems += held_fds(root)
            time.sleep(POLL_S)
        time.sleep(0.3)  # the last save holds still
        events += watchers.poll_logs() + watchers.poll_files()
    finally:
        if game.poll() is None:
            game.kill()
        monkeypatch.undo()
    assert game.returncode == 0
    report = json.loads(result.read_text(encoding="utf-8"))
    assert report["errors"] == [], "the game's saves were blocked"
    assert report["cycles"] >= 10 and report["writes"] > 50
    assert guard.violations == []
    assert fd_problems == []
    assert guard.opens > 50 and guard.open == 0
    assert guard.longest < 1.0, f"a game file was held {guard.longest:.2f} s"
    # Every forced hold met a save of its file: given up to the game (Windows, D357) or held
    # through two save cycles (Linux, which never refuses one).
    assert sorted(f[0] for f in guard.forced) == sorted(
        [f"{fg.PLAYER_UUID}.json", "level.dat"]
    ), guard.forced
    for name, held, given_up, cycles in guard.forced:
        if sys.platform == "win32":
            assert given_up and held < 2.0, (name, held, given_up, cycles)
        else:
            assert cycles >= 2, (name, held, given_up, cycles)
    seen = {e.kind for e in events}
    expected = {
        "location",
        "item_delta",
        "dim_change",
        "quest_done",
        "chat",
        "vein_found",
        "death",
        "game_paused",
        "block_placed",
        "crash",
    }
    assert expected <= seen, f"missing {expected - seen} after {polls} polls"
    quests = [e for e in events if e.kind == "quest_done"]
    assert len({e.data["quest"] for e in quests}) == len(quests)  # each once


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux's")
def test_the_fd_check_sees_a_held_handle(tmp_path: Path) -> None:
    path = tmp_path / "level.dat"
    path.write_bytes(b"x")
    with open(path, "rb"):
        assert held_fds(tmp_path) == [str(path)]
    assert held_fds(tmp_path) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' share modes")
def test_windows_a_held_plain_handle_blocks_a_save_and_safe_open_does_not(
    tmp_path: Path,
) -> None:
    """The control: a plain ``open`` (no share-delete) blocks vanilla's rename save, so the soak
    would see a reader that held one; ``safe_open`` lets the rename through (V27), and since
    WO-6.8 gives the file up as it does (D357)."""
    target = tmp_path / "level.dat"
    target.write_bytes(b"old")
    with open(target, "rb"), pytest.raises(PermissionError):
        os.rename(target, tmp_path / "level.dat_old")
    with safe_open.safe_open(target) as f:
        assert f.read() == b"old"
        os.rename(target, tmp_path / "level.dat_old")  # vanilla's save renames it away
        assert safe_open.interrupted(
            f
        )  # the rename broke the oplock: GOS gave the file up
        with pytest.raises(safe_open.ReadInterrupted):
            f.read()
    assert (tmp_path / "level.dat_old").read_bytes() == b"old"
