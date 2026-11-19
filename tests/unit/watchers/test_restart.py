"""The watchers restarted after the log rotated while they were down (D355, closing D337's
finding F1), on a fake Prism tree: the new file is read from its start, its old pause lines
never move the paused state, and nothing is counted twice. Nothing here is Play."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Protocol

import pytest

from gregtec.watchers import files, runner
from gregtec.watchers.events import Event
from gregtec.watchers.runner import Watchers

FML = "fml-client-latest.log"


class FakeClock(Protocol):
    def __call__(self) -> float: ...
    def advance(self, s: float) -> None: ...


def kinds(events: list[Event]) -> list[str]:
    return [e.kind for e in events]


def server(stamp: str, msg: str) -> str:
    return f"[{stamp}] [Server thread/INFO]: {msg}\n"


def test_a_log_rotated_while_the_watchers_were_down_is_read_from_its_start(
    tmp_path: Path, fg: ModuleType, clock: FakeClock
) -> None:
    tree = fg.Tree(tmp_path)
    tree.log("")
    tree.log("", FML)
    home = tmp_path / "home"
    w = Watchers(tree.game, None, home, clock, settle_s=0)
    assert w.poll_logs() == []
    tree.log(fg.stamp_line("[CHAT] <Clayton> before"))
    assert [e.data["text"] for e in w.poll_logs()] == ["<Clayton> before"]
    w.save_tails()  # the watchers stop here
    # down: the game restarts, log4j rolls both logs and the new ones get the session's start
    tree.rotate()
    tree.rotate(FML, n=2)
    start = fg.stamp_line(
        "Clayton[local:E:1] logged in with entity id 7 at (1.0, 2.0, 3.0)",
        "Server thread",
    ) + fg.stamp_line("[CHAT] <Clayton> while you were down")
    tree.log(start)
    tree.log(start, FML)
    w2 = Watchers(tree.game, None, home, clock, settle_s=0)
    events = w2.poll_logs()
    # the new files from their start, a line both logs have counted once, the old file's
    # "before" not again
    assert kinds(events) == ["location", "chat"]
    assert events[1].data["text"] == "<Clayton> while you were down"
    assert w2.poll_logs() == []
    # the login line was read, so the player is known and a death counts
    tree.log(fg.stamp_line("[CHAT] Clayton drowned"))
    assert kinds(w2.poll_logs()) == ["death"]


def test_replayed_old_pause_lines_leave_the_pause_state_unchanged(
    tmp_path: Path, fg: ModuleType, clock: FakeClock
) -> None:
    tree = fg.Tree(tmp_path)
    tree.log("")
    home = tmp_path / "home"
    w = Watchers(tree.game, None, home, clock, settle_s=0)
    w.poll_logs()
    w.save_tails()
    tree.rotate()  # while the watchers are down
    tree.log(
        server("00:00:05", "Saving and pausing game...")
        + server("00:03:00", "<Clayton> back")
        + server("00:10:00", "Saving and pausing game...")
    )
    w2 = Watchers(tree.game, None, home, clock, settle_s=0)
    assert w2.poll_logs() == []  # no game_paused event from the old lines
    assert w2.tracker.paused is None and w2.tracker.since is None
    # a pause written after the restart is a pause
    tree.log(server("00:20:00", "Saving and pausing game..."))
    assert [e.data["paused"] for e in w2.poll_logs()] == [True]


def test_replayed_old_resume_and_quit_lines_leave_a_pause_alone(
    tmp_path: Path, fg: ModuleType, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """latest.log rolled at midnight while the watchers were down; the FML log (rolled at a
    start only) is the same file. A pause read live from the FML log stays while latest.log's
    old lines are still being caught up."""
    tree = fg.Tree(tmp_path)
    tree.log("")
    tree.log("", FML)
    home = tmp_path / "home"
    w = Watchers(tree.game, None, home, clock, settle_s=0)
    w.poll_logs()
    w.save_tails()
    tree.rotate()
    first = f"[12:00:00] [Client thread/INFO]: {'loading ' * 12}\n"
    tree.log(
        first
        + server("12:40:00", "<Clayton> back")
        + server("12:41:00", "Stopping server")
    )
    monkeypatch.setattr(files, "MAX_READ", len(first))  # the catch-up takes two polls
    w2 = Watchers(tree.game, None, home, clock, settle_s=0)
    tree.log(
        "[12:30:00] [Server thread/INFO] [IntegratedServer]: Saving and pausing game...\n",
        FML,
    )
    assert [e.data["paused"] for e in w2.poll_logs()] == [True]
    # the rest of the old lines: the world's end is still an event, the pause stands
    assert kinds(w2.poll_logs()) == ["world_closed"]
    assert w2.tracker.paused is True
    monkeypatch.undo()
    tree.log(server("12:50:00", "<Clayton> really back"))  # written after the restart
    assert [e.data["paused"] for e in w2.poll_logs()] == [False]


class Killed(BaseException):
    """The process dies: nothing is saved on the way out."""


def test_nothing_is_counted_twice_across_a_rotation_and_hard_kills(
    tmp_path: Path, fg: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kills come right after a rotation is first read, where the offsets saved at once
    (D355) are what keeps a restart from reading the file from its start again. (A kill later
    on re-reads the same file's lines since the last save, as it always did: D355's known 1.)"""
    tree = fg.Tree(tmp_path)
    tree.log("")
    home = tmp_path / "home"
    spool = runner.Spool(home / "data" / "events")
    monkeypatch.setattr(runner, "TICK_S", 0)

    def run(
        passes: int,
        kill: bool = False,
        before: dict[int, Callable[[], None]] | None = None,
    ) -> None:
        """A watchers process for ``passes`` passes of its loop (each polls the logs), then
        stopped (offsets saved) or killed (not saved). ``before[n]`` runs before pass n."""
        w = Watchers(tree.game, None, home, settle_s=0)
        stop = threading.Event()
        calls = iter(range(passes + 1))  # run_loop asks once before its loop

        def loop_clock() -> float:
            n = next(calls, None)
            if n is None:
                raise Killed
            if before and n in before:
                before[n]()
            if n == passes and not kill:
                stop.set()
            return n * 2.0  # every pass polls; the 30 s save never comes due

        try:
            runner.run_loop(
                w, spool, runner.Sender(None, "lab"), stop, clock=loop_clock
            )
        except Killed:
            assert kill

    def say(text: str) -> None:
        tree.log(fg.stamp_line(f"[CHAT] <Clayton> {text}"))

    run(2, before={2: lambda: say("one")})  # a clean stop
    tree.rotate()  # down: the game restarts
    say("two")
    run(1, kill=True)  # reads the new log from its start, dies before the 30 s save
    # up again: "two" is not read a second time. The log rolls under them, they follow it
    # from its start and die again
    run(2, kill=True, before={2: lambda: (tree.rotate(n=2), say("three"))[1]})
    run(2, before={2: lambda: say("four")})
    spooled = [
        json.loads(line)
        for p in sorted((home / "data" / "events").glob("*.jsonl"))
        for line in p.read_text("utf-8").splitlines()
    ]
    assert [e["data"]["text"] for e in spooled if e["kind"] == "chat"] == [
        f"<Clayton> {n}" for n in ("one", "two", "three", "four")
    ]
