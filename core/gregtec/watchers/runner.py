"""The game watchers' process: every reader polled on its cadence, events spooled and sent to the
core (plan 4.2, 5.4, 7.5, D77, WO-6.1, D257-D259).

``gos watch run --instance play|lab`` (the supervisor runs it as ``python -m gregtec.watchers``
while the instance plays, D259). Each loop (:data:`TICK_S`):

- the two client logs every second (plan 5.4's poll-tail; the pause line, chat, deaths, veins,
  crashes and errors, :mod:`logparse`), and through them the pause tracker (:mod:`pause`, D258);
- the save files every :data:`FILES_EVERY_S` (level.dat, quest progress, stats, the VP client
  cache, JourneyMap, crash reports, ServerUtilities' world log, the stale flag), each read only
  after it changed and held still.

Events go to ``GOS_HOME/data/events/<YYYY-MM-DD>.jsonl`` (GOS's own spool, kept for WO-6.3's
event store) and to the core's ``POST /api/game/events`` from a sender thread, so a slow or
stopped core never delays a read; a heartbeat with the paused state goes every
:data:`HEARTBEAT_S`, so the core can tell the watchers stopped. The core is reached with the
``watchers`` client token from this account's keyring (``gos token new watchers``); without one,
events are only spooled. A world that isn't configured (``[paths] world`` / ``lab_world``) leaves
only the log readers running. Chat and every other game text is data, never instructions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from gregtec import applog, config, control
from gregtec.ingest import fingerprint
from gregtec.watchers import files, logparse, pause, player, sources
from gregtec.watchers.events import Event

log = logging.getLogger("gregtec.watchers")

TICK_S = 0.25
LOG_EVERY_S = 1.0  # plan 5.4: poll-tail 1 s
FILES_EVERY_S = 2.0
HEARTBEAT_S = 10.0
DEATH_MERGE_S = 120.0  # deaths this close are one death seen by several readers
QUEUE_MAX = 2000
POST_MAX = 100  # events per POST
POST_BYTES = 128 << 10  # and about this much JSON (the core takes 256 KB)
TOKEN = "watchers"  # the client token's name (gos token new watchers)
EXIT_SETUP = 2  # the supervisor leaves the watchers stopped on this (D259)
LOGS = (("latest.log", False), ("fml-client-latest.log", True))
WORLD_LOGS = (
    "logs/world.log",
)  # ServerUtilities, in the game folder (V06: path VERIFY)


class Poller(Protocol):
    def poll(self) -> list[Event]: ...


class LogWatch:
    """``latest.log`` and ``fml-client-latest.log`` -> events and the pause tracker's input."""

    source = "log"

    def __init__(
        self,
        game: Path,
        clock: Callable[[], float],
        tracker: pause.PauseTracker,
        saved: dict[str, Any],
    ) -> None:
        self.clock = clock
        self.tracker = tracker
        self.player: str | None = None
        self.dedupe = logparse.Dedupe()
        self.tails = [
            (
                files.LogTail(
                    game / "logs" / name, _saved(saved, game / "logs" / name)
                ),
                fml,
            )
            for name, fml in LOGS
        ]

    def poll(self) -> list[Event]:
        events: list[Event] = []
        for tail, fml in self.tails:
            for line in tail.poll():
                p = logparse.parse(line)
                if p is None:
                    continue  # a stack trace's continuation, or a line from before log4j
                for found in logparse.read_line(p, fml, self.player):
                    if not self.dedupe.keep(p.time, found, self.clock()):
                        continue
                    events.extend(self._event(found))
        return events

    def _event(self, found: logparse.Found) -> list[Event]:
        now = self.clock()
        if found.kind == "pause":
            return _paused(self.tracker.pause_line())
        if found.kind == "server_tick":
            return _paused(self.tracker.server_line())
        if found.kind == "quit":
            return _paused(self.tracker.world_closed())
        if found.kind == "login":
            data = dict(found.data)
            self.player = str(data.pop("player"))
            return [Event("location", now, self.source, {**data, "via": "login"})]
        return [Event(found.kind, now, self.source, dict(found.data))]


def _paused(change: pause.Change | None) -> list[Event]:
    if change is None:
        return []
    return [
        Event(
            "game_paused",
            change.since,
            "pause",
            {"paused": change.paused, "why": change.why},
        )
    ]


def _saved(saved: dict[str, Any], path: Path) -> dict[str, Any] | None:
    value = saved.get(str(path))
    return value if isinstance(value, dict) else None


class Watchers:
    """Every reader for one instance, and what ties them together (the pause tracker, deaths)."""

    def __init__(
        self,
        game: Path,
        world: Path | None,
        home: Path,
        clock: Callable[[], float] = time.time,
        settle_s: float = 1.0,
    ) -> None:
        self.game, self.world, self.home, self.clock = game, world, home, clock
        self.tails_file = home / "data" / "watchers" / "tails.json"
        saved = files.load_tails(self.tails_file)
        self.tracker = pause.PauseTracker(clock)
        self.logs = LogWatch(game, clock, self.tracker, saved)
        self.worldlog = sources.WorldLogWatch(
            [game / p for p in WORLD_LOGS], clock, saved
        )
        self.player: player.PlayerWatch | None = None
        self.others: list[Poller] = [
            sources.CrashWatch(game, clock, settle_s),
            sources.StaleWatch(home, clock),
            self.worldlog,
        ]
        if world is not None:
            self.player = player.PlayerWatch(world, clock, settle_s=settle_s)
            pw = self.player
            self.others += [
                sources.QuestWatch(world, clock, settle_s=settle_s),
                sources.StatsWatch(world, clock, lambda: pw.names, settle_s=settle_s),
                sources.VeinWatch(world, game, clock, settle_s=settle_s),
                sources.WaypointWatch(game, world.name, clock, settle_s),
            ]
        self._last_death: tuple[float, str] | None = None

    def poll_logs(self) -> list[Event]:
        return self._merge(self.logs.poll())

    def poll_files(self) -> list[Event]:
        events: list[Event] = []
        if self.player is not None:
            try:
                events += self.player.poll()
            except Exception:  # one reader's bug must not stop the others
                log.exception("watcher failed", extra={"reader": "PlayerWatch"})
            if self.player.saved:
                events += _paused(self.tracker.world_saved())
        for reader in self.others:
            try:
                events += reader.poll()
            except Exception:  # one reader's bug must not stop the others
                log.exception("watcher failed", extra={"reader": type(reader).__name__})
        return self._merge(events)

    def _merge(self, events: list[Event]) -> list[Event]:
        """A death the chat, the stats and JourneyMap each report is one death: later ones
        say ``same_as`` the first."""
        out = []
        for e in events:
            if e.kind == "death":
                if self._last_death and e.ts - self._last_death[0] < DEATH_MERGE_S:
                    e = Event(
                        e.kind,
                        e.ts,
                        e.source,
                        {**e.data, "same_as": self._last_death[1]},
                    )
                else:
                    self._last_death = (e.ts, e.id)
            out.append(e)
        return out

    def save_tails(self) -> None:
        tails: dict[str, Any] = {}
        for tail, _ in self.logs.tails:
            if (state := tail.state()) is not None:
                tails[str(tail.path)] = state
        for tail in self.worldlog.tails:
            if (state := tail.state()) is not None:
                tails[str(tail.path)] = state
        try:
            files.save_tails(self.tails_file, tails)
        except OSError as exc:
            log.warning("tail offsets not saved", extra={"error": str(exc)})

    def problems(self) -> list[str]:
        out = []
        for reader in [self.player, *self.others]:
            problem = getattr(reader, "problem", None)
            if problem:
                out.append(f"{type(reader).__name__}: {problem}")
        return out


class Spool:
    """Every event as one JSON line in ``GOS_HOME/data/events/<local date>.jsonl`` (open, append,
    close: GOS's own file)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def write(self, events: Sequence[Event]) -> None:
        if not events:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{dt.date.today().isoformat()}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.writelines(
                json.dumps(e.as_dict(), ensure_ascii=False) + "\n" for e in events
            )


Send = Callable[[dict[str, Any]], None]


class Sender:
    """Posts batches to the core from its own thread; a failed post is retried with back-off,
    oldest events first, and the queue drops its oldest when full (the spool keeps them)."""

    def __init__(self, send: Send | None, instance: str) -> None:
        self.send = send
        self.instance = instance
        self.q: deque[Event] = deque(maxlen=QUEUE_MAX)
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {"paused": None, "since": None, "why": None}
        self.problems: list[str] = []
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.sent = 0
        self._thread: threading.Thread | None = None

    def put(self, events: Sequence[Event]) -> None:
        with self.lock:
            for e in events:
                if e.kind == "game_paused":
                    self.state = {
                        "paused": e.data["paused"],
                        "since": e.ts,
                        "why": e.data["why"],
                    }
                self.q.append(e)
        if events:
            self.wake.set()

    def pending(self) -> int:
        with self.lock:
            return len(self.q)

    def start(self) -> None:
        if self.send is None:
            return
        self._thread = threading.Thread(
            target=self._run, name="watchers-sender", daemon=True
        )
        self._thread.start()

    def flush_once(self) -> bool:
        """One POST of up to :data:`POST_MAX` queued events (or a heartbeat). False if it failed:
        the events go back to the front of the queue."""
        assert self.send is not None
        with self.lock:
            batch: list[Event] = []
            rows: list[dict[str, Any]] = []
            size = 0
            while self.q and len(batch) < POST_MAX:
                row = self.q[0].as_dict()
                n = len(json.dumps(row))
                if batch and size + n > POST_BYTES:
                    break
                batch.append(self.q.popleft())
                rows.append(row)
                size += n
            body = {
                "instance": self.instance,
                "events": rows,
                "paused": dict(self.state),
                "problems": self.problems[:20],
            }
        try:
            self.send(body)
        except Exception as exc:  # noqa: BLE001 - any failure: keep the events, retry
            log.info(
                "core not reached; keeping events", extra={"error": str(exc)[:200]}
            )
            with self.lock:
                room = QUEUE_MAX - len(self.q)
                self.q.extendleft(reversed(batch[-room:] if room > 0 else []))
            return False
        self.sent += len(batch)
        return True

    def _run(self) -> None:
        backoff = 1.0
        last = 0.0
        while not self.stop.is_set():
            if self.pending() or time.monotonic() - last >= HEARTBEAT_S:
                if self.flush_once():
                    backoff, last = 1.0, time.monotonic()
                    if self.pending():
                        continue
                else:
                    self.stop.wait(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue
            self.wake.wait(1.0)
            self.wake.clear()

    def close(self, timeout: float = 3.0) -> None:
        """Stop the thread, then send what is left once (best effort)."""
        self.stop.set()
        self.wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self.send is not None and self.pending():
                self.flush_once()


def core_sender(cfg: config.Config, token: str) -> Send:
    def send(body: dict[str, Any]) -> None:
        control.call(
            cfg.core_port,
            "POST",
            "/api/game/events",
            token=token,
            body=body,
            timeout=3.0,
        )

    return send


def run_loop(
    watchers: Watchers,
    spool: Spool,
    sender: Sender,
    stop: threading.Event,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    next_logs = next_files = 0.0
    next_save = clock() + 30.0
    while not stop.is_set():
        now = clock()
        events: list[Event] = []
        if now >= next_logs:
            next_logs = now + LOG_EVERY_S
            events += watchers.poll_logs()
        if now >= next_files:
            next_files = now + FILES_EVERY_S
            events += watchers.poll_files()
            sender.problems = watchers.problems()
        if events:
            try:
                spool.write(events)
            except OSError as exc:
                log.warning("event spool not written", extra={"error": str(exc)})
            sender.put(events)
        if now >= next_save:
            next_save = now + 30.0
            watchers.save_tails()
        stop.wait(TICK_S)
    watchers.save_tails()


def _stop_on_stdin(stop: threading.Event) -> None:
    try:
        for line in sys.stdin.buffer:
            if line.strip() == b"stop":
                break
    except (OSError, ValueError):
        pass
    stop.set()


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--instance",
        choices=("play", "lab"),
        # the supervisor says which instance launched (D259)
        default=os.environ.get("GOS_PLAY_INSTANCE") or "play",
    )
    parser.add_argument(
        "--world",
        default=None,
        help="the world's folder in saves/ (default: [paths] world or lab_world)",
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--supervised",
        action="store_true",
        help="stop on a 'stop' line or end of stdin",
    )
    parser.add_argument(
        "--seconds", type=float, default=0, help="stop after this long (default: never)"
    )


def run(args: argparse.Namespace) -> int:
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"can't read gos.toml: {exc}", file=sys.stderr)
        return EXIT_SETUP
    applog.setup("watchers", cfg.logs_dir)
    if args.instance == "play" and config.running_as_dev():
        # CLAUDE.md: gos-dev reads Play only when a WO says so; the watchers are Clayton's.
        print("gos-dev doesn't watch Play: use --instance lab", file=sys.stderr)
        return EXIT_SETUP
    try:
        game = fingerprint.game_folder(cfg.instance(args.instance))
    except fingerprint.FingerprintError as exc:
        log.error("no game folder", extra={"error": str(exc)})
        print(f"no game folder: {exc}", file=sys.stderr)
        return EXIT_SETUP
    name = args.world or (cfg.world if args.instance == "play" else cfg.lab_world)
    world = game / "saves" / name if name else None
    if world is not None and not world.is_dir():
        log.warning(
            "world folder missing; watching the logs only", extra={"world": str(world)}
        )
        world = None
    token: str | None
    try:
        from gregtec.llm import keys

        token = keys.get_key(f"token-{TOKEN}")
    except Exception:  # noqa: BLE001 - no keyring backend or no token: spool only
        token = None
        log.warning(
            "no watchers token stored (gos token new watchers): events are only spooled"
        )
    watchers = Watchers(game, world, cfg.gos_home)
    spool = Spool(cfg.data_dir / "events")
    sender = Sender(core_sender(cfg, token) if token else None, args.instance)
    stop = threading.Event()
    if args.supervised:
        threading.Thread(target=_stop_on_stdin, args=(stop,), daemon=True).start()
    if args.seconds:
        threading.Timer(args.seconds, stop.set).start()
    log.info(
        "watchers started",
        extra={
            "instance": args.instance,
            "game": str(game),
            "world": str(world) if world else None,
        },
    )
    sender.start()
    try:
        run_loop(watchers, spool, sender, stop)
    except KeyboardInterrupt:
        pass
    finally:
        sender.close()
        log.info("watchers stopped", extra={"sent": sender.sent})
    return 0
