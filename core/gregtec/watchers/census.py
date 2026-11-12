"""The tile-entity census of a world's changed chunks (WO-6.2, plan 5.4, 5.5, [B§3.4], D260).

GT machines keep their identity in their tile entity, which a region file stores as plain NBT:
``Level.TileEntities`` holds, for each machine, ``id`` ``BaseMetaTileEntity`` with ``mID`` (GT's
MetaTileEntity id, also the machine item's meta) and ``x``, ``y``, ``z``, ``mActive`` and
``mWasShutdown`` (GT 5.09.54.265's ``BaseMetaTileEntity.writeToNBT``). So the census reads only
tile entities and never decodes a block section: no EndlessIDs block decoder, and no numeric block
or item id anywhere. Cables and pipes (``BaseMetaPipeEntity``) are not machines and are skipped.

**Region files** (Anvil, ``<world>/region/r.<rx>.<rz>.mca`` for the overworld and
``<world>/DIM<n>/region/`` for dimension n): an 8 KB header of 1024 chunk locations (3-byte sector
offset, 1-byte sector count) and 1024 save times (epoch seconds), then each chunk as a 4-byte
length, a compression byte (1 gzip, 2 zlib) and the data. Re-implemented from the format; nothing
is vendored.

**Reads** (plan 5.5): every read through ``safe_open`` (``files.read_range``: open, seek, read,
close, 3 tries), never a held handle. A region file is looked at only once its size and mtime
have held still (:class:`files.Settle`), then only its header is read and only the chunks whose
location or save time changed since the last look ("changed chunks only"). A chunk read is checked
(length within its sectors, a known compression, a whole NBT compound, ``Level.xPos/zPos`` the
chunk's own); one that fails is read again on the next poll, and after :data:`TRIES` it is left
until it changes again and reported as a problem. A chunk without ``BaseMetaTileEntity`` in its
bytes is not parsed at all.

**Events** (``machine_census``, source ``census``): per region, the chunks looked at (their
indices in the region, 0-1023: ``(cx & 31) + 32 * (cz & 31)``) and every GT machine in them as
``[x, y, z, mte_id, active, shutdown]``; the core replaces its census of those chunks with them.
The first look at a world is the baseline: every chunk of every region, the empty ones included,
then one ``scope: world`` event listing the regions that exist, so the core drops what it had
for regions that are gone. A poll spends at most ``budget_s`` reading (a big world's baseline takes
several polls), so the log readers it shares a loop with are never held up for long.
"""

from __future__ import annotations

import re
import struct
import time
import zlib
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from gregtec.ingest import nbt
from gregtec.watchers import files
from gregtec.watchers.events import Event

SECTOR = 4096
HEADER = 2 * SECTOR
CHUNKS = 1024
MACHINE_TE = "BaseMetaTileEntity"
_MARK = MACHINE_TE.encode("ascii")
TRIES = 3
CHUNK_MAX = (
    4 << 20
)  # a chunk's compressed bytes (Minecraft writes at most 255 sectors, ~1 MB)
CHUNK_UNPACKED_MAX = 64 << 20
MACHINES_PER_EVENT = 400  # [x, y, z, id, a, s] rows per event: ~16 KB of JSON
MAX_REGIONS_EVENT = 4000  # regions listed in the baseline's world event
REGION_NAME = re.compile(r"^r\.(-?\d+)\.(-?\d+)\.mca$")
DIM_DIR = re.compile(r"^DIM(-?\d+)$")

Clock = Callable[[], float]
Reader = Callable[[Path, int, int], bytes]


class ChunkError(ValueError):
    """A chunk that couldn't be read whole (a partial write, or damage)."""


@dataclass(frozen=True)
class Machine:
    x: int
    y: int
    z: int
    mte_id: int
    active: bool = False
    shutdown: bool = False

    def row(self) -> list[int]:
        return [
            self.x,
            self.y,
            self.z,
            self.mte_id,
            int(self.active),
            int(self.shutdown),
        ]


def chunk_index(cx: int, cz: int) -> int:
    return (cx & 31) + 32 * (cz & 31)


def chunk_coords(rx: int, rz: int, index: int) -> tuple[int, int]:
    return rx * 32 + (index & 31), rz * 32 + (index >> 5)


@dataclass(frozen=True)
class Header:
    """A region file's header: per chunk index its (sector offset, sector count, save time)."""

    slots: tuple[tuple[int, int, int], ...]

    @classmethod
    def parse(cls, data: bytes) -> Header:
        if len(data) < HEADER:
            raise ChunkError(f"region header is {len(data)} bytes, not {HEADER}")
        locs = struct.unpack(">1024I", data[:SECTOR])
        times = struct.unpack(">1024i", data[SECTOR:HEADER])
        return cls(
            tuple((loc >> 8, loc & 0xFF, t) for loc, t in zip(locs, times, strict=True))
        )


def machines_in(
    data: bytes, cx: int | None = None, cz: int | None = None
) -> list[Machine]:
    """The GT machines in one chunk's stored bytes (length, compression, data), checked whole."""
    if len(data) < 5:
        raise ChunkError("chunk shorter than its 5-byte head")
    (length,) = struct.unpack(">i", data[:4])
    if length < 1 or length > len(data) - 4:
        raise ChunkError(f"chunk length {length} doesn't fit its {len(data) - 4} bytes")
    kind, body = data[4], data[5 : 4 + length]
    if kind == 1:
        raw = nbt.decompress(body, CHUNK_UNPACKED_MAX)
        if raw is body:
            raise ChunkError("gzip chunk without the gzip header")
    elif kind == 2:
        d = zlib.decompressobj()
        try:
            raw = d.decompress(body, CHUNK_UNPACKED_MAX + 1)
        except zlib.error as exc:
            raise ChunkError(f"bad zlib data: {exc}") from None
        if len(raw) > CHUNK_UNPACKED_MAX or not d.eof:
            raise ChunkError("zlib data too long or cut short")
    else:
        raise ChunkError(f"unknown chunk compression {kind}")
    if _MARK not in raw:
        return []  # no machine: not parsed (the whole compressed stream was there)
    try:
        _, root = nbt.read(raw)
    except ValueError as exc:
        raise ChunkError(str(exc)) from None
    level = root.get("Level")
    if not isinstance(level, dict):
        raise ChunkError("chunk without a Level compound")
    if cx is not None and (level.get("xPos"), level.get("zPos")) != (cx, cz):
        raise ChunkError(
            f"chunk says it is {level.get('xPos')},{level.get('zPos')}, not {cx},{cz}"
        )
    out = []
    tes = level.get("TileEntities")
    for te in tes if isinstance(tes, list) else []:
        if not isinstance(te, dict) or te.get("id") != MACHINE_TE:
            continue
        mid, x, y, z = (te.get(k) for k in ("mID", "x", "y", "z"))
        if not all(isinstance(v, int) for v in (mid, x, y, z)):
            continue
        assert isinstance(mid, int) and isinstance(x, int)
        assert isinstance(y, int) and isinstance(z, int)
        if mid <= 0:
            continue
        out.append(
            Machine(x, y, z, mid, bool(te.get("mActive")), bool(te.get("mWasShutdown")))
        )
    return out


def regions(world: Path) -> Iterator[tuple[int, Path]]:
    """(dimension, region file) for every region of the world, overworld first."""
    folders: list[tuple[int, Path]] = [(0, world / "region")]
    if world.is_dir():
        for d in sorted(world.iterdir()):
            m = DIM_DIR.match(d.name)
            if m and d.is_dir():
                folders.append((int(m[1]), d / "region"))
    for dim, folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("r.*.mca")):
            if REGION_NAME.match(path.name):
                yield dim, path


@dataclass
class _Region:
    dim: int
    path: Path
    rx: int
    rz: int
    known: dict[int, tuple[int, int, int]] = field(
        default_factory=dict
    )  # index -> slot
    seen: bool = False  # looked at whole at least once
    fails: dict[int, int] = field(default_factory=dict)


class CensusWatch:
    """The census of one world's GT machines, a changed chunk at a time (see the module text)."""

    source = "census"

    def __init__(
        self,
        world: Path,
        clock: Clock,
        settle_s: float = 1.0,
        budget_s: float = 0.5,
        read: Reader = files.read_range,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self.world, self.clock, self.budget_s = world, clock, budget_s
        self.read, self.timer = read, timer
        self.settle = files.Settle(settle_s)
        self.regions: dict[Path, _Region] = {}
        self.queue: deque[Path] = deque()
        self.baseline_sent = False
        self.problem: str | None = None
        self._problems: dict[str, str] = {}

    def _note(self, key: str, problem: str | None) -> None:
        if problem is None:
            self._problems.pop(key, None)
        else:
            self._problems[key] = problem
        self.problem = "; ".join(sorted(self._problems.values())[:5]) or None

    def poll(self) -> list[Event]:
        now = self.clock()
        present: set[Path] = set()
        for dim, path in regions(self.world):
            present.add(path)
            region = self.regions.get(path)
            if region is None:
                m = REGION_NAME.match(path.name)
                assert m is not None
                region = self.regions[path] = _Region(dim, path, int(m[1]), int(m[2]))
            if (
                self.settle.changed(str(path), files.stamp(path), now)
                and path not in self.queue
            ):
                self.queue.append(path)
        for gone in set(self.regions) - present:
            del self.regions[gone]
        events: list[Event] = []
        start = self.timer()
        for _ in range(len(self.queue)):
            if self.timer() - start >= self.budget_s:
                break
            path = self.queue.popleft()
            region = self.regions.get(path)
            if region is not None and not self._look(region, start, events):
                self.queue.append(path)  # chunks left (the budget) or to read again
        if (
            not self.baseline_sent
            and not self.queue
            and self.regions
            and all(r.seen for r in self.regions.values())
        ):
            self.baseline_sent = True
            events.append(self._world_event())
        return events

    def _world_event(self) -> Event:
        listed = sorted((r.dim, r.rx, r.rz) for r in self.regions.values())
        if len(listed) > MAX_REGIONS_EVENT:
            self._note(
                "world", f"{len(listed)} regions: too many to list, none dropped"
            )
            listed = []
        return Event(
            "machine_census",
            self.clock(),
            self.source,
            {
                "scope": "world",
                "regions": [list(r) for r in listed],
                "complete": bool(listed),
            },
        )

    def _look(self, region: _Region, start: float, events: list[Event]) -> bool:
        """Reads the region's changed chunks (within the budget). True when none are left."""
        try:
            header = Header.parse(self.read(region.path, 0, HEADER))
        except FileNotFoundError:
            return True
        except (OSError, ChunkError) as exc:
            self._note(
                str(region.path), f"{region.path.name}: header unreadable: {exc}"
            )
            return True
        todo = self._todo(region, header)
        looked: list[int] = []
        rows: list[list[int]] = []
        failed = False
        read_any = False
        for i in todo:
            offset, sectors, _ = header.slots[i]
            if (
                offset == 0
            ):  # no chunk there (never generated, or trimmed): nothing to read
                looked.append(i)
                region.known.pop(i, None)
                continue
            if read_any and self.timer() - start >= self.budget_s:
                break
            read_any = True
            cx, cz = chunk_coords(region.rx, region.rz, i)
            try:
                data = self.read(region.path, offset * SECTOR, sectors * SECTOR)
                if len(data) > CHUNK_MAX:
                    raise ChunkError("chunk too large")
                found = machines_in(data, cx, cz)
            except FileNotFoundError:
                return True
            except (OSError, ChunkError) as exc:
                tries = region.fails.get(i, 0) + 1
                region.fails[i] = tries
                if tries < TRIES:
                    failed = True
                    continue
                region.fails.pop(i, None)
                region.known[i] = header.slots[i]  # left until it changes again
                self._note(
                    f"{region.path}#{i}", f"{region.path.name} chunk {cx},{cz}: {exc}"
                )
                continue
            region.fails.pop(i, None)
            self._note(f"{region.path}#{i}", None)
            region.known[i] = header.slots[i]
            looked.append(i)
            rows.extend(m.row() for m in found)
            if len(rows) >= MACHINES_PER_EVENT:
                events.append(self._event(region, looked, rows))
                looked, rows = [], []
        self._note(str(region.path), None)
        if looked or rows:
            events.append(self._event(region, looked, rows))
        left = self._todo(region, header)
        if not failed and not any(header.slots[i][0] for i in left):
            region.seen = True
            left = self._todo(region, header)
        return not left and not failed

    @staticmethod
    def _todo(region: _Region, header: Header) -> list[int]:
        """Chunk indices to look at: changed since the last look, and either holding a chunk, gone
        since then, or not yet looked at in the baseline."""
        return [
            i
            for i, slot in enumerate(header.slots)
            if region.known.get(i) != slot
            and (slot[0] or not region.seen or i in region.known)
        ]

    def _event(
        self, region: _Region, chunks: list[int], rows: list[list[int]]
    ) -> Event:
        return Event(
            "machine_census",
            self.clock(),
            self.source,
            {
                "scope": "chunks",
                "dim": region.dim,
                "region": [region.rx, region.rz],
                "chunks": sorted(chunks),
                "machines": rows,
            },
        )


def census(world: Path) -> dict[tuple[int, int, int, int], int]:
    """A whole census of ``world`` at once: (dim, x, y, z) -> mte id (``gos watch census``)."""
    watch = CensusWatch(world, time.time, settle_s=0, budget_s=float("inf"))
    found: dict[tuple[int, int, int, int], int] = {}
    for e in watch.poll():
        if e.data.get("scope") != "chunks":
            continue
        for x, y, z, mid, _a, _s in e.data["machines"]:
            found[(e.data["dim"], x, y, z)] = mid
    if watch.problem:
        raise ChunkError(watch.problem)
    return found
