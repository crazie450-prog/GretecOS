"""The file watchers besides the logs and the player (plan 5.4, [B§1, B§3-4], WO-6.1, D257).

Each is a small reader with ``poll() -> list[Event]``: it stats what it watches, and once a file
(or a folder's set of files) changed and held still (:class:`files.Settle`) it reads it whole
through ``safe_open`` (retried on a partial write: VisualProspecting, JourneyMap and the stats
are written in place [B§6.2]) and diffs it with its last read. The first read is the baseline.

| watcher           | reads                                                     | events          |
|-------------------|-----------------------------------------------------------|-----------------|
| QuestWatch        | ``W/betterquesting/QuestProgress/*.json``, ``NameCache.json`` | ``quest_done``  |
| StatsWatch        | ``W/stats/<uuid>.json``                                   | ``item_delta`` (crafted, mined, used, broken), ``death`` |
| WorldLogWatch     | ServerUtilities' ``world.log`` (``MC/logs/``, V06)        | ``block_placed``, ``block_broken`` |
| VeinWatch         | VisualProspecting's client cache for this world (D27: the server cache is every vein, so it never makes a "found" event) | ``vein_found`` |
| WaypointWatch     | JourneyMap ``MC/journeymap/data/sp/<world>/waypoints/*.json`` | ``death`` (a Death waypoint, with where) |
| CrashWatch        | ``MC/crash-reports/crash-*.txt``                          | ``crash``       |
| StaleWatch        | GOS's own stale flag (WO-1.4, D120)                       | ``export_stale`` |
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest import fingerprint, vp
from gregtec.ingest import quests as bq
from gregtec.watchers import files
from gregtec.watchers.events import Event, clean

JSON_LIMIT = 16 << 20

Clock = Callable[[], float]


def folder_stamp(
    folder: Path, pattern: str
) -> tuple[tuple[str, files.Stamp | None], ...]:
    """The names and stamps of ``folder``'s files matching ``pattern`` (a listing opens no
    file)."""
    if not folder.is_dir():
        return ()
    return tuple((p.name, files.stamp(p)) for p in sorted(folder.glob(pattern)))


def read_json(path: Path) -> object:
    parsed: list[object] = []

    def check(data: bytes) -> None:
        parsed.append(json.loads(data.decode("utf-8")))

    read_bytes(path, validate=check, limit=JSON_LIMIT)
    return parsed[-1]


class QuestWatch:
    """New completions of the tracked player (BQ has no completion log line, [B§1.7])."""

    source = "quests"

    def __init__(
        self,
        world: Path,
        clock: Clock,
        player: str | None = None,
        settle_s: float = 1.0,
    ) -> None:
        self.world, self.clock, self.player = world, clock, player
        self.settle = files.Settle(settle_s)
        self.done: dict[str, bq.Completion] | None = None
        self.problem: str | None = None

    def poll(self) -> list[Event]:
        now = self.clock()
        folder = self.world / bq.WORLD_DIR
        sig = (
            folder_stamp(folder / bq.PROGRESS_DIR, "*.json"),
            files.stamp(folder / bq.NAME_CACHE),
        )
        if not sig[0] or not self.settle.changed("progress", sig, now):
            return []
        try:
            progress = bq.read_progress(self.world)
            player = bq.pick_player(progress, self.player)
        except (OSError, ValueError) as exc:
            self.problem = f"quest progress unreadable: {exc}"
            return []
        self.problem = None
        done = progress.done.get(player, {})
        old, self.done = self.done, dict(done)
        if old is None:
            return []
        events = []
        for qid in sorted(set(done) - set(old)):
            c = done[qid]
            events.append(
                Event(
                    "quest_done",
                    now,
                    self.source,
                    {"quest": f"q:{qid}", "completed_ms": c.ts, "claimed": c.claimed},
                )
            )
        return events


STAT = re.compile(
    r"^stat\.(?P<what>craftItem|mineBlock|useItem|breakItem)\.(?:autogen\.)?(?P<item>.+)$"
)
STAT_WORD = {
    "craftItem": "crafted",
    "mineBlock": "mined",
    "useItem": "used",
    "breakItem": "broken",
}


class StatsWatch:
    """``stats/<uuid>.json`` diffs: what was crafted, mined, used or broken, and deaths.

    Stats ignore metadata (all GT machines are one ``gregtech:gt.blockmachines``) [B§3.3], so an
    entry is a registry name with ``meta`` null. Hodgepodge's ``autogen.<modid:name>`` keys carry
    the name; a vanilla numeric key (``stat.mineBlock.1``) is named through this world's registry
    (``names``, from level.dat) and left out when it can't be, never kept as a number.
    """

    source = "stats"

    def __init__(
        self,
        world: Path,
        clock: Clock,
        names: Callable[[], dict[int, str]],
        player: str | None = None,
        settle_s: float = 1.0,
    ) -> None:
        self.world, self.clock, self.names, self.player = world, clock, names, player
        self.settle = files.Settle(settle_s)
        self.last: dict[str, int] | None = None
        self.problem: str | None = None

    def _file(self) -> Path | None:
        folder = self.world / "stats"
        if self.player:
            path = folder / f"{self.player}.json"
            return path if path.is_file() else None
        found = sorted(folder.glob("*.json")) if folder.is_dir() else []
        return found[0] if len(found) == 1 else None

    def poll(self) -> list[Event]:
        now = self.clock()
        path = self._file()
        if path is None or not self.settle.changed("stats", files.stamp(path), now):
            return []
        try:
            raw = read_json(path)
        except (OSError, ValueError) as exc:
            self.problem = f"{path.name} unreadable: {exc}"
            return []
        self.problem = None
        stats = {
            k: v
            for k, v in (raw.items() if isinstance(raw, dict) else ())
            if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
        }
        old, self.last = self.last, stats
        if old is None:
            return []
        events: list[Event] = []
        deaths = stats.get("stat.deaths", 0) - old.get("stat.deaths", 0)
        if deaths > 0:
            events.append(
                Event("death", now, self.source, {"count": deaths, "via": "stats"})
            )
        names = self.names()
        by_action: dict[str, dict[str, int]] = {}
        for key in sorted(set(stats) | set(old)):
            m = STAT.match(key)
            if m is None:
                continue
            delta = stats.get(key, 0) - old.get(key, 0)
            if not delta:
                continue
            item = m["item"]
            if item.isdigit():
                named = names.get(int(item))
                if named is None:
                    continue
                item = named
            elif ":" not in item:
                continue
            bucket = by_action.setdefault(STAT_WORD[m["what"]], {})
            bucket[item] = bucket.get(item, 0) + delta
        for action, items in sorted(by_action.items()):
            events.append(
                Event(
                    "item_delta",
                    now,
                    self.source,
                    {
                        "via": "stats",
                        "action": action,
                        "items": [
                            {"key": reg, "registry": reg, "meta": None, "delta": n}
                            for reg, n in sorted(items.items())
                        ],
                    },
                )
            )
        return events


# ServerUtilities' world log ([B§3.5]): "YYYY-MM-DD HH:MM:SS : <player> placed <Block>:<meta> at
# [x, y, z] in <Dimension>". The wording for breaking is VERIFY (V06), so the common ones are taken.
WORLD_LINE = re.compile(
    r"^(?P<when>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) : (?P<player>\S+) "
    r"(?P<action>placed|broke|broken|removed|destroyed) (?P<block>.+?):(?P<meta>\d+) "
    r"at \[(?P<x>-?\d+), (?P<y>-?\d+), (?P<z>-?\d+)\] in (?P<dim>.+)$"
)


class WorldLogWatch:
    """Tails ServerUtilities' ``world.log``. A block's display name and meta are all it gives
    (every GT machine looks alike there, [B§3.5]): kept as text, never as a key."""

    source = "worldlog"

    def __init__(
        self, paths: list[Path], clock: Clock, saved: dict[str, object]
    ) -> None:
        self.clock = clock
        self.tails = [
            files.LogTail(
                p, saved.get(str(p)) if isinstance(saved.get(str(p)), dict) else None
            )
            for p in paths
        ]  # type: ignore[arg-type]

    def poll(self) -> list[Event]:
        now = self.clock()
        events = []
        for tail in self.tails:
            for line in tail.poll():
                m = WORLD_LINE.match(line.strip())
                if m is None:
                    continue
                kind = "block_placed" if m["action"] == "placed" else "block_broken"
                events.append(
                    Event(
                        kind,
                        now,
                        self.source,
                        {
                            "logged": m["when"],
                            "player": clean(m["player"], 40),
                            "block": clean(m["block"], 120),
                            "meta": int(m["meta"]),
                            "x": int(m["x"]),
                            "y": int(m["y"]),
                            "z": int(m["z"]),
                            "dim": clean(m["dim"], 60),
                            "untrusted": True,
                        },
                    )
                )
        return events


class VeinWatch:
    """Veins new in this player's VisualProspecting client cache, which VP saves when he leaves
    the world or changes dimension; the live notices come from the chat lines (logparse)."""

    source = "vp"

    def __init__(
        self,
        world: Path,
        game: Path,
        clock: Clock,
        player: str | None = None,
        settle_s: float = 1.0,
    ) -> None:
        self.world, self.game, self.clock, self.player = world, game, clock, player
        self.settle = files.Settle(settle_s)
        self.known: set[tuple[int, int, int]] | None = None
        self.problem: str | None = None

    def poll(self) -> list[Event]:
        now = self.clock()
        clients = self.game / vp.VP_DIR / vp.CLIENT_DIR
        if not clients.is_dir():
            return []
        sig = tuple(
            (str(p.relative_to(clients)), files.stamp(p))
            for p in sorted(clients.glob("*/*/*.dat"))
        )
        if not self.settle.changed("client", sig, now):
            return []
        try:
            located = vp.locate(self.world, self.game, self.player)
            if located.client is None:
                return []
            cache = vp.read_cache(located.client)
        except (OSError, ValueError, vp.VpError) as exc:
            self.problem = f"VisualProspecting cache unreadable: {exc}"
            return []
        if cache.unreadable:
            self.problem = "; ".join(cache.unreadable)
            return []  # a partial picture would report its missing veins later as new
        self.problem = None
        veins = cache.veins
        old, self.known = self.known, set(veins)
        if old is None:
            return []
        events = []
        for key in sorted(set(veins) - old):
            v = veins[key]
            events.append(
                Event(
                    "vein_found",
                    now,
                    self.source,
                    {
                        "mix": v.mix,
                        "dim": v.dim,
                        "x": v.cx * 16 + 8,
                        "z": v.cz * 16 + 8,
                        "via": "cache",
                    },
                )
            )
        return events


class WaypointWatch:
    """New JourneyMap Death waypoints: where he died. Nether waypoints keep x and z at overworld
    scale (x8), so dimension -1's are divided by 8 [B§4.2]."""

    source = "journeymap"

    def __init__(
        self, game: Path, world_name: str, clock: Clock, settle_s: float = 1.0
    ) -> None:
        self.folder = game / "journeymap" / "data" / "sp" / world_name / "waypoints"
        self.clock = clock
        self.settle = files.Settle(settle_s)
        self.seen: set[str] | None = None

    def poll(self) -> list[Event]:
        now = self.clock()
        sig = folder_stamp(self.folder, "*.json")
        if not self.settle.changed("waypoints", sig, now):
            return []
        names = {name for name, _ in sig}
        old, self.seen = self.seen, names
        if old is None:
            return []
        events = []
        for name in sorted(names - old):
            try:
                wp = read_json(self.folder / name)
            except (OSError, ValueError):
                continue
            if not isinstance(wp, dict) or wp.get("type") != "Death":
                continue
            dims = wp.get("dimensions")
            dim = (
                dims[0]
                if isinstance(dims, list) and dims and isinstance(dims[0], int)
                else None
            )
            x, y, z = (wp.get(k) for k in ("x", "y", "z"))
            if not all(isinstance(v, int | float) for v in (x, y, z)):
                continue
            assert isinstance(x, int | float) and isinstance(z, int | float)
            if dim == -1:
                x, z = x / 8, z / 8
            events.append(
                Event(
                    "death",
                    now,
                    self.source,
                    {
                        "x": round(x),
                        "y": y,
                        "z": round(z),
                        "dim": dim,
                        "via": "journeymap",
                    },
                )
            )
        return events


class CrashWatch:
    """New crash reports: the file name and its ``Description:`` line (text, data only)."""

    source = "crash"

    def __init__(self, game: Path, clock: Clock, settle_s: float = 1.0) -> None:
        self.folder = game / "crash-reports"
        self.clock = clock
        self.settle = files.Settle(settle_s)
        self.seen: set[str] | None = None

    def poll(self) -> list[Event]:
        now = self.clock()
        sig = folder_stamp(self.folder, "crash-*.txt")
        if not self.settle.changed("crashes", sig, now):
            return []
        names = {name for name, _ in sig}
        old, self.seen = self.seen, names
        if old is None:
            return []
        events = []
        for name in sorted(names - old):
            description = ""
            try:
                head = read_bytes(self.folder / name, limit=8192).decode(
                    "utf-8", "replace"
                )
                m = re.search(r"^Description: (.*)$", head, re.MULTILINE)
                description = clean(m.group(1), 200) if m else ""
            except OSError:
                pass
            events.append(
                Event(
                    "crash",
                    now,
                    self.source,
                    {
                        "report": clean(name, 200),
                        "description": description,
                        "via": "file",
                        "untrusted": True,
                    },
                )
            )
        return events


class StaleWatch:
    """``export_stale`` when the stale flag (Play's mods or configs changed since the last
    export, D120) turns on, and again with ``stale`` false when it clears."""

    source = "fingerprint"

    def __init__(self, home: Path, clock: Clock) -> None:
        self.home, self.clock = home, clock
        self.stale: bool | None = None

    def poll(self) -> list[Event]:
        state = fingerprint.load_state(self.home)
        stale = state.get("stale")
        if not isinstance(stale, bool) or stale == self.stale:
            return []
        old, self.stale = self.stale, stale
        if old is None and not stale:
            return []
        return [Event("export_stale", self.clock(), self.source, {"stale": stale})]
