"""Machine events and the census on the listener harness's real output (WO-6.2 acceptance).

``./gradlew runServer -Pharness -PharnessListener`` (after ``runServer -Pharness``, whose export
run this also reads; mod/README.md) has a fake player place 10 GT machine kinds (the EBF, a hatch
and 8 single-block machines) and break four, one machine placed and one removed without a player
event, and leaves in ``mod/run/server/gregteclink/``: the listener's ``events/machines.jsonl``, the
world's region files after each phase (``listener-harness/census-a``, ``census-b``) and
``expected.json``, the base after each phase. Here:

- the census of each phase's saved chunks matches that phase's base exactly (the acceptance's
  "the census matches the base"), the machine the listener never saw included;
- the events file, read by the watchers, says exactly what the player did, by mte id and
  registry name + meta;
- through the watchers and the core's apply, on a DB built from the harness's export, My base
  counts the census's machines by D211's precedence and names them from the real catalog.

Runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job) and then fails without the harness output;
writes nothing outside tmp_path.
"""

from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gregtec import store
from gregtec.base import seen, state
from gregtec.db import access
from gregtec.watchers import census, machines, runner

REPO = Path(__file__).resolve().parents[2]
GAME = REPO / "mod" / "run" / "server"
OUT = GAME / "gregteclink" / "listener-harness"


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    path = OUT / "expected.json"
    if not path.is_file():
        pytest.fail(
            f"no listener harness output in {OUT} (runServer -Pharness -PharnessListener)"
        )
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def base(rows: list[dict[str, int]]) -> dict[tuple[int, int, int, int], int]:
    return {(r["dim"], r["x"], r["y"], r["z"]): r["mte_id"] for r in rows}


class Clock:
    now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def test_kinds_include_a_controller_and_a_hatch(expected: dict[str, Any]) -> None:
    kinds = expected["kinds"]
    assert len({k["mte_id"] for k in kinds}) == 10
    assert {k["kind"] for k in kinds} >= {"multiblock_controller", "hatch", "machine"}
    assert expected["registry"] == "gregtech:gt.blockmachines"


@pytest.mark.parametrize("phase", ["a", "b"])
def test_census_matches_the_base(expected: dict[str, Any], phase: str) -> None:
    want = base(expected[f"census_{phase}"])
    assert census.census(OUT / f"census-{phase}") == want
    assert len(want) == (11 if phase == "a" else 6)


def test_events_say_what_the_player_did(
    expected: dict[str, Any], tmp_path: Path
) -> None:
    clock = Clock()
    watch = machines.MachineEventWatch(GAME, clock, {})
    events = watch.poll()
    assert watch.refused == 0
    kinds = [k["mte_id"] for k in expected["kinds"]]
    built = [e.data for e in events if e.kind == "machine_built"]
    removed = [e.data for e in events if e.kind == "machine_removed"]
    assert [d["mte_id"] for d in built] == kinds
    assert [d["mte_id"] for d in removed] == kinds[:4]
    for d in built + removed:
        assert d["key"] == f"{expected['registry']}@{d['mte_id']}"
        assert d["ref"] == f"mte:{d['mte_id']}"
    # where each was built is where the census found it in phase a
    a = base(expected["census_a"])
    assert all(a[(d["dim"], d["x"], d["y"], d["z"])] == d["mte_id"] for d in built)


def test_my_base_counts_through_the_watchers(
    expected: dict[str, Any],
    tmp_path_factory: pytest.TempPathFactory,
    built_home: Callable[[Path, Path], Path],
) -> None:
    home = tmp_path_factory.mktemp("machine-events-home")
    built_home(GAME, home)
    lines = (GAME / machines.EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    game = tmp_path_factory.mktemp("game")
    world = game / "saves" / "harness"
    events_file = game / machines.EVENTS_FILE
    events_file.parent.mkdir(parents=True)
    clock = Clock()
    watchers = runner.Watchers(game, world, home, clock, settle_s=0)
    conn = store.connect(access.db_path(home))
    try:
        for phase, upto in (("a", 10), ("b", 14)):
            # the save: the phase's region files and the listener's lines so far
            if (world / "region").exists():
                shutil.rmtree(world / "region")
            shutil.copytree(OUT / f"census-{phase}" / "region", world / "region")
            for f in (world / "region").iterdir():
                os.utime(f, (clock.now + 10, clock.now + 10))
            events_file.write_text(
                "".join(x + "\n" for x in lines[:upto]), encoding="utf-8"
            )
            clock.now += 20
            got = []
            for _ in range(50):
                got += watchers.poll_files()
            seen.apply(conn, [e.as_dict() for e in got])
            want = Counter(base(expected[f"census_{phase}"]).values())
            listed = {m.mte_id: m for m in state.machines(conn)}
            assert {i: m.count for i, m in listed.items() if m.count} == dict(want)
            assert all(listed[i].source == "census" for i in want)
            assert all(listed[i].name for i in want)  # named from the harness's catalog
            assert state.MyBase.from_db(conn).owned_machines() == dict(want)
        # the events' own count differs from the census by exactly what the listener missed
        events = Counter(seen.positions(conn, "event").values())
        census_b = Counter(base(expected["census_b"]).values())
        silent_placed = base(expected["census_a"])[_key(expected["silent_placed"])]
        missed = census_b - events
        extra = events - census_b
        assert missed == Counter({silent_placed: 1})
        assert sum(extra.values()) == 1  # the machine removed without an event
    finally:
        conn.close()


def _key(text: str) -> tuple[int, int, int, int]:
    dim, xyz = text.split(":")
    x, y, z = (int(v) for v in xyz.split(","))
    return int(dim), x, y, z
