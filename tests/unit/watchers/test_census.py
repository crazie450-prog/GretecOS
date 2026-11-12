"""The tile-entity census of changed chunks (WO-6.2, plan 5.4, D260): region files written as
Minecraft 1.7.10 writes them (``regions.py``), read the way plan 5.5 asks."""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

import pytest

from gregtec.watchers import census, files


class FakeClock(Protocol):
    def __call__(self) -> float: ...
    def advance(self, s: float) -> None: ...


GT = "BaseMetaTileEntity"


class Timer:
    def __init__(self, step: float = 0.0) -> None:
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def world_with(fg: ModuleType, rg: ModuleType, root: Path, ts: int = 100) -> Path:
    world = root / "world"
    rg.region_file(
        world / "region" / "r.0.0.mca",
        {
            (0, 0): (
                rg.chunk_bytes(fg, 0, 0, [rg.machine_te(fg, 1, 5, 2, 1000, True)]),
                ts,
            ),
            (3, 1): (
                rg.chunk_bytes(
                    fg,
                    3,
                    1,
                    [
                        rg.machine_te(fg, 50, 5, 20, 66),
                        rg.machine_te(fg, 51, 5, 20, 66),
                        {
                            "id": "BaseMetaPipeEntity",
                            "x": 52,
                            "y": 5,
                            "z": 20,
                            "mID": 5000,
                        },
                        {"id": "Chest", "x": 53, "y": 5, "z": 20},
                        {"id": GT, "x": 54, "y": 5, "z": 20, "mID": 0},
                    ],
                    compression=1,
                ),
                ts,
            ),
            (5, 5): (rg.chunk_bytes(fg, 5, 5, []), ts),  # no machine: never parsed
        },
    )
    rg.region_file(
        world / "DIM-1" / "region" / "r.-1.-1.mca",
        {
            (-1, -1): (
                rg.chunk_bytes(fg, -1, -1, [rg.machine_te(fg, -3, 40, -7, 1001)]),
                ts,
            )
        },
    )
    (world / "DIM-1" / "data").mkdir()
    (world / "DIMweird" / "region").mkdir(parents=True)
    return world


def touch(path: Path, later_s: int) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + later_s * 10**9))


def chunk_events(events: list[Any]) -> list[dict[str, Any]]:
    return [e.data for e in events if e.data["scope"] == "chunks"]


def machines(events: list[Any]) -> dict[tuple[int, int, int, int], int]:
    out = {}
    for d in chunk_events(events):
        for x, y, z, mte, _a, _s in d["machines"]:
            out[(d["dim"], x, y, z)] = mte
    return out


def test_machines_in_reads_gt_tile_entities_only(
    fg: ModuleType, rg: ModuleType
) -> None:
    tes = [
        rg.machine_te(fg, 1, 2, 3, 1000, active=True),
        {"id": "BaseMetaPipeEntity", "x": 1, "y": 3, "z": 3, "mID": 5000},
        {
            "id": GT,
            "x": 1,
            "y": 4,
            "z": 3,
            "mID": fg.T(fg.SHORT, 31),
        },  # an older short mID
        {"id": GT, "x": "bad", "y": 4, "z": 3, "mID": 7},
    ]
    for compression in (1, 2):
        found = census.machines_in(rg.chunk_bytes(fg, 0, 0, tes, compression), 0, 0)
        assert found == [
            census.Machine(1, 2, 3, 1000, active=True, shutdown=False),
            census.Machine(1, 4, 3, 31),
        ]
        assert found[0].row() == [1, 2, 3, 1000, 1, 0]


def test_machines_in_refuses_broken_chunks(fg: ModuleType, rg: ModuleType) -> None:
    good = rg.chunk_bytes(fg, 2, 3, [rg.machine_te(fg, 32, 1, 48, 1000)])
    with pytest.raises(census.ChunkError, match="not 2,4"):
        census.machines_in(good, 2, 4)
    with pytest.raises(census.ChunkError, match="doesn't fit"):
        census.machines_in(
            good[:40], 2, 3
        )  # a torn write: shorter than its length says
    with pytest.raises(census.ChunkError, match="compression"):
        census.machines_in(good[:4] + b"\x07" + good[5:], 2, 3)
    with pytest.raises(census.ChunkError):
        census.machines_in(good[:5] + bytes(len(good) - 5), 2, 3)  # zeroed data


def test_baseline_reads_every_region_then_lists_them(
    fg: ModuleType, rg: ModuleType, tmp_path: Path, clock: FakeClock
) -> None:
    world = world_with(fg, rg, tmp_path)
    watch = census.CensusWatch(world, clock, settle_s=1.0)
    assert watch.poll() == []  # not settled yet
    clock.advance(1.5)
    events = watch.poll()
    assert machines(events) == {
        (0, 1, 5, 2): 1000,
        (0, 50, 5, 20): 66,
        (0, 51, 5, 20): 66,
        (-1, -3, 40, -7): 1001,
    }
    overworld = [d for d in chunk_events(events) if d["dim"] == 0]
    assert overworld[0]["region"] == [0, 0]
    assert sorted({i for d in overworld for i in d["chunks"]}) == list(range(1024))
    assert all(e.kind == "machine_census" and e.source == "census" for e in events)
    world_event = events[-1].data
    assert world_event == {
        "scope": "world",
        "regions": [[-1, -1, -1], [0, 0, 0]],
        "complete": True,
    }
    assert watch.problem is None
    # nothing changed: nothing read, nothing sent
    clock.advance(5)
    assert watch.poll() == []


def test_only_changed_chunks_are_read_again(
    fg: ModuleType, rg: ModuleType, tmp_path: Path, clock: FakeClock
) -> None:
    world = world_with(fg, rg, tmp_path)
    reads: list[tuple[str, int, int]] = []

    def read(path: Path, offset: int, size: int) -> bytes:
        reads.append((path.name, offset, size))
        return files.read_range(path, offset, size)

    watch = census.CensusWatch(world, clock, settle_s=0, read=read)
    watch.poll()
    # the save: chunk (3,1) loses a machine, (0,0) is rewritten unchanged, (5,5) is gone
    region = world / "region" / "r.0.0.mca"
    rg.region_file(
        region,
        {
            (0, 0): (
                rg.chunk_bytes(fg, 0, 0, [rg.machine_te(fg, 1, 5, 2, 1000, True)]),
                100,
            ),
            (3, 1): (rg.chunk_bytes(fg, 3, 1, [rg.machine_te(fg, 50, 5, 20, 66)]), 160),
        },
    )
    touch(region, 60)
    reads.clear()
    events = watch.poll()
    assert [d["chunks"] for d in chunk_events(events)] == [
        [census.chunk_index(3, 1), census.chunk_index(5, 5)]
    ]
    assert machines(events) == {(0, 50, 5, 20): 66}
    assert len(reads) == 2  # the header and the one changed chunk
    assert not [e for e in events if e.data["scope"] == "world"]


def test_budget_spreads_a_baseline_over_polls(
    fg: ModuleType, rg: ModuleType, tmp_path: Path, clock: FakeClock
) -> None:
    world = world_with(fg, rg, tmp_path)
    watch = census.CensusWatch(world, clock, settle_s=0, budget_s=1.0, timer=Timer(0.4))
    seen: dict[tuple[int, int, int, int], int] = {}
    worlds = []
    for _ in range(20):
        events = watch.poll()
        seen.update(machines(events))
        worlds += [e for e in events if e.data["scope"] == "world"]
    assert len(seen) == 4
    assert len(worlds) == 1


def test_a_torn_chunk_is_read_again_then_reported(
    fg: ModuleType, rg: ModuleType, tmp_path: Path, clock: FakeClock
) -> None:
    world = tmp_path / "world"
    good = rg.chunk_bytes(fg, 0, 0, [rg.machine_te(fg, 1, 5, 2, 1000)])
    rg.region_file(
        world / "region" / "r.0.0.mca", {(0, 0): (good[:5] + bytes(len(good) - 5), 100)}
    )
    watch = census.CensusWatch(world, clock, settle_s=0)
    for _ in range(census.TRIES - 1):
        assert machines(watch.poll()) == {}
        assert watch.problem is None
    events = watch.poll()
    assert watch.problem is not None and "chunk 0,0" in watch.problem
    assert [e.data["scope"] for e in events][-1] == "world"
    # the game writes it whole: read, and the problem goes
    rg.region_file(world / "region" / "r.0.0.mca", {(0, 0): (good, 101)})
    touch(world / "region" / "r.0.0.mca", 5)
    assert machines(watch.poll()) == {(0, 1, 5, 2): 1000}
    assert watch.problem is None


def test_census_helper_counts_a_whole_world(
    fg: ModuleType, rg: ModuleType, tmp_path: Path
) -> None:
    world = world_with(fg, rg, tmp_path)
    assert census.census(world) == {
        (0, 1, 5, 2): 1000,
        (0, 50, 5, 20): 66,
        (0, 51, 5, 20): 66,
        (-1, -3, 40, -7): 1001,
    }
    assert census.census(tmp_path / "nothing") == {}


def test_header_and_coords() -> None:
    assert census.chunk_index(-1, -1) == 1023
    assert census.chunk_coords(-1, -1, 1023) == (-1, -1)
    assert census.chunk_coords(2, 0, 33) == (65, 1)
    with pytest.raises(census.ChunkError):
        census.Header.parse(b"\x00" * 100)
