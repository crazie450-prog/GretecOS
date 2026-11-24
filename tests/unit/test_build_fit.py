"""WO-10.16 (D422): site mode's machine fitter.

Buildings are made up here as sets of solid positions (walls round a set of columns and a roof
over them); machines are made-up footprints (box-shaped multis, ``fixture:casing`` blocks, a
controller in the middle of the north face, a maintenance and an input hatch), plus WO-1.8's
fixture DB for a program read from plans. No game data.

Every fitted layout is checked with test_build_layout's acceptance properties (schema, no
overlaps, keep-out = box +- clearance, reach by an independent flood through the building's
air) and against the building: nothing GOS keeps clear is solid or unroofed.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import sqlite3
import time
from collections.abc import Iterable, Mapping
from typing import Any

import pytest

from gregtec.build import fit, layout
from gregtec.build import footprints as fp
from gregtec.build.layout import Box, Instance
from gregtec.build.palette import GT_MACHINES
from gregtec.db import structures as structure_db

from .test_build_layout import (
    DATASET,
    _cells,
    _overlap,
    check_common,
    power,
    production,
)

Cell = tuple[int, int, int]
Column = tuple[int, int]
OPTS = layout.Options()


# --- made-up machines and buildings ----------------------------------------------------------


def square(mte: int, sx: int, sy: int, sz: int) -> fp.Footprint:
    """A box-shaped multi facing north: the controller in the middle of its north face."""
    cells = []
    x0 = -(sx // 2)
    for x, y, z in itertools.product(range(x0, x0 + sx), range(sy), range(sz)):
        d = (x, y, z)
        hatches: tuple[str, ...] = ()
        if d == (x0, 0, sz - 1):
            hatches = ("Maintenance",)
        elif d == (x0 + sx - 1, sy - 1, sz - 1):
            hatches = ("InputBus",)
        ctl = d == (0, 0, 0)
        cells.append(
            fp.Cell(
                d,
                GT_MACHINES if ctl else "fixture:casing",
                mte if ctl else 0,
                hatches,
            )
        )
    cells.sort(key=lambda c: (c.d[1], c.d[2], c.d[0]))
    return fp.Footprint(
        GT_MACHINES,
        mte,
        f"Test {mte}",
        None,
        "gtnh-extractor",
        "t1",
        (),
        "north",
        tuple(cells),
    )


CUBE = square(9001, 3, 3, 3)  # keep-out 5 x 4 x 5
LONG = square(9101, 5, 2, 2)  # keep-out 7 x 3 x 4 facing north or south, 4 x 3 x 7 else
TALL = square(9102, 3, 6, 3)  # keep-out 5 x 7 x 5
WIDE = square(9103, 9, 3, 3)  # keep-out 11 x 4 x 5
ONE = square(9201, 1, 1, 1)  # keep-out 3 x 2 x 3


def program_of(*fs: fp.Footprint, plan: str = "pl-a") -> layout.Program:
    """One machine per footprint, node n1, n2, ... of one plan, in that order."""
    return layout.Program(
        plans=({"id": plan, "name": "A", "kind": "production"},),
        gt=None,
        run=None,
        instances=tuple(
            Instance(
                plan, f"n{i + 1}", "machine", f.meta, "multiblock", f.name, "HV", f
            )
            for i, f in enumerate(fs)
        ),
        unplaced=(),
        variants={},
        services=(),
        service_warnings=(),
    )


def rect(x0: int, z0: int, x1: int, z1: int) -> set[Column]:
    return {(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)}


def building(cols: set[Column], h: int) -> set[Cell]:
    """A roof at y ``h`` over ``cols`` and walls (y 0..h-1) on its edge columns."""
    out = {(x, h, z) for x, z in cols}
    for x, z in cols:
        if any(
            (x + dx, z + dz) not in cols
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
        ):
            out.update((x, y, z) for y in range(h))
    return out


def door(
    cells: set[Cell], side: str, wall: int, start: int, width: int = 3, height: int = 3
) -> set[Cell]:
    """``cells`` with a doorway ``width`` wide and ``height`` high in the wall at x (west or
    east) or z (north or south) ``wall``, from ``start`` along it."""
    gone = {
        (wall, y, a) if side in ("west", "east") else (a, y, wall)
        for a in range(start, start + width)
        for y in range(height)
    }
    return cells - gone


def site_of(cells: Iterable[Cell], pad: int = 1) -> Box:
    cs = list(cells)
    return Box(
        (0, 0, 0),
        (
            max(c[0] for c in cs) + pad,
            max(c[1] for c in cs) + 2,
            max(c[2] for c in cs) + pad,
        ),
    )


# --- the invariants ---------------------------------------------------------------------------


def _air(cells: set[Cell], site: Box) -> set[Cell]:
    """Air with a block somewhere above it: the building's inside."""
    tops: dict[Column, int] = {}
    for x, y, z in cells:
        tops[(x, z)] = max(tops.get((x, z), -1), y)
    return {
        c for c in site.cells() if c not in cells and tops.get((c[0], c[2]), -1) > c[1]
    }


def verify(r: fit.FitResult, cells: set[Cell], site: Box) -> dict[str, Any]:
    assert r.ok, r.reasons
    lay = r.layout
    assert lay is not None and r.reasons == []
    air = _air(cells, site)
    bodies = {c for p in lay["placements"] for c in _cells(p)}
    check_common(lay, frozenset(air - bodies))
    for d in [*lay["keepouts"], *lay["corridors"], *lay["chases"], *lay["entrances"]]:
        assert all(c in air for c in _cells(d)), d["id"]  # never solid, always roofed
    assert lay["bounds"] == {**site.to_json(), "size": list(site.size)}
    n = len(lay["placements"])
    assert [p["id"] for p in lay["placements"]] == [f"m{i}" for i in range(1, n + 1)]
    assert lay["fit"]["placed"] == lay["fit"]["of"] == n == r.stats["placed"]
    assert lay["corridors"][0]["id"] == "c1" and lay["corridors"][0]["kind"] == "hall"
    assert all(c["kind"] == "aisle" for c in lay["corridors"][1:])
    (e,) = lay["entrances"]
    assert e["id"] == "e1" and e["corridor"] == "c1"
    return lay


def run(
    fs: Iterable[fp.Footprint],
    cells: Iterable[Cell],
    site: Box | None = None,
    opts: layout.Options = OPTS,
    hint: Mapping[str, Any] | None = None,
    zones: list[dict[str, Any]] | None = None,
    prior: Mapping[str, Any] | None = None,
) -> fit.FitResult:
    cells = list(cells)
    return fit.fit(
        program_of(*fs), cells, site or site_of(cells), opts, hint, zones or (), prior
    )


def fitted(
    fs: Iterable[fp.Footprint],
    cells: set[Cell],
    site: Box | None = None,
    opts: layout.Options = OPTS,
    hint: Mapping[str, Any] | None = None,
    zones: list[dict[str, Any]] | None = None,
    prior: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    site = site or site_of(cells)
    return verify(run(fs, cells, site, opts, hint, zones, prior), cells, site)


def hall_with_doors(*doors: tuple[str, int, int, int]) -> set[Cell]:
    """A 13 x 13 hall (x and z 0..12), 5 high, with doors (side, wall, start, width)."""
    cells = building(rect(0, 0, 12, 12), 5)
    for side, wall, start, width in doors:
        cells = door(cells, side, wall, start, width)
    return cells


# --- the door ---------------------------------------------------------------------------------


def test_the_widest_doorway_is_the_entrance() -> None:
    lay = fitted([CUBE], hall_with_doors(("west", 0, 5, 1), ("north", 0, 4, 3)))
    (e,) = lay["entrances"]
    assert e["side"] == "north" and e["lo"] == [4, 0, 0] and e["hi"] == [6, 2, 0]
    assert lay["fit"]["entrance_from"] == "auto"


@pytest.mark.parametrize(
    ("doors", "side"),
    [
        ((("east", 12, 5, 3), ("west", 0, 5, 3)), "west"),
        ((("east", 12, 5, 3), ("south", 12, 5, 3)), "south"),
        ((("north", 0, 5, 3), ("east", 12, 5, 3)), "east"),
    ],
)
def test_equal_doorways_go_west_south_east_north(
    doors: tuple[tuple[str, int, int, int], ...], side: str
) -> None:
    lay = fitted([CUBE], hall_with_doors(*doors))
    assert lay["entrances"][0]["side"] == side


def test_a_wide_opening_gets_a_door_in_its_middle() -> None:
    lay = fitted([CUBE], hall_with_doors(("west", 0, 2, 9)))
    (e,) = lay["entrances"]
    assert e["lo"] == [0, 0, 5] and e["hi"] == [0, 2, 7]  # 3 wide, centred in 2..10


def test_the_lobby_lies_inside_the_door() -> None:
    lay = fitted([CUBE], hall_with_doors(("west", 0, 5, 3)))
    c1 = lay["corridors"][0]
    assert c1["lo"] == [0, 0, 5] and c1["hi"] == [2, 2, 7]  # corridor_width deep
    assert lay["entrances"][0]["lo"] == [0, 0, 5]
    # a narrow door: a lobby as wide as the door
    lay = fitted([CUBE], hall_with_doors(("west", 0, 6, 1)))
    c1 = lay["corridors"][0]
    assert c1["lo"] == [0, 0, 6] and c1["hi"] == [2, 2, 6]


def test_the_answers_entrance_is_used() -> None:
    cells = hall_with_doors(("west", 0, 5, 3), ("east", 12, 4, 5))
    lay = fitted([CUBE], cells, hint={"side": "east", "at": [12, 5]})
    (e,) = lay["entrances"]
    assert e["side"] == "east" and e["lo"] == [12, 0, 4] and e["hi"] == [12, 2, 6]
    assert lay["fit"]["entrance_from"] == "answer"
    # the answer's door at the end of a run: the door stays inside the opening
    lay = fitted([CUBE], cells, hint={"side": "east", "at": [12, 8]})
    assert lay["entrances"][0]["lo"] == [12, 0, 6]


@pytest.mark.parametrize(
    ("hint", "why"),
    [
        ({"side": "east", "at": [6, 6]}, "it is not on the building's east edge"),
        ({"side": "west", "at": [0, 2]}, "it is not roofed floor at least 3 high"),
        (
            {"side": "up", "at": [0, 6]},
            "the side is not one of north, east, south, west",
        ),
        ({"side": "west", "at": [0, 6], "x": 1}, None),  # extra keys are the caller's
    ],
)
def test_a_bad_entrance_is_refused(hint: dict[str, Any], why: str | None) -> None:
    r = run([CUBE], hall_with_doors(("west", 0, 5, 3)), hint=hint)
    if why is None:
        assert r.ok
        return
    x, z = hint["at"]
    assert r.reasons == [f"entrance at [{x},{z}] on the {hint['side']}: {why}"]
    assert r.layout is None


@pytest.mark.parametrize(
    "hint",
    [
        {"side": ["west"], "at": [0, 6]},
        {"side": {"w": 1}, "at": [0, 6]},
        {"side": None, "at": [0, 6]},
        {"side": "west", "at": [0.9, 6.2]},
        {"side": "west", "at": "06"},
        {"side": "west", "at": [True, 6]},
        {"side": "west", "at": [0, 6, 1]},
        {"side": "west", "at": None},
    ],
)
def test_a_malformed_entrance_is_refused_not_raised(hint: dict[str, Any]) -> None:
    r = run([CUBE], hall_with_doors(("west", 0, 5, 3)), hint=hint)
    assert r.reasons == [
        "the entrance is not {side: north|east|south|west, at: [x, z]}"
    ]


def test_a_door_slides_along_a_wide_opening_past_a_pillar() -> None:
    # a pillar 2 blocks behind the middle of a 9-wide arcade: the middle window's lobby hits
    # it, so the door moves along the opening (nearest the middle first)
    pillar = {(2, y, 6) for y in range(5)}
    cells = hall_with_doors(("west", 0, 2, 9)) | pillar
    lay = fitted([CUBE], cells)
    e = lay["entrances"][0]
    assert e["side"] == "west" and e["lo"] == [0, 0, 3] and e["hi"] == [0, 2, 5]
    # an answer's door in the same opening slides too, keeping its column
    lay = fitted([CUBE], cells, hint={"side": "west", "at": [0, 7]})
    assert lay["entrances"][0]["lo"] == [0, 0, 7]


def test_a_canopy_does_not_take_the_door_from_the_hall() -> None:
    hall = door(building(rect(10, 0, 22, 12), 5), "west", 10, 5, 3)
    # a free-standing canopy west of it: a roof at y 4 on corner posts, open on every side
    canopy = {(x, 4, z) for x in range(1, 6) for z in range(13)}
    canopy |= {(x, y, z) for x in (1, 5) for z in (0, 12) for y in range(4)}
    cells = hall | canopy
    site = Box((0, 0, 0), (24, 8, 13))
    lay = fitted([CUBE, CUBE], cells, site=site)
    assert lay["entrances"][0]["lo"][0] == 10  # the hall's door, not the canopy's face
    assert lay["fit"]["entrance_from"] == "auto"
    # with no door into the hall, the canopy's floor is what is measured
    shut = building(rect(10, 0, 22, 12), 5) | canopy
    r = run([CUBE, CUBE], shut, site=site)
    assert r.reasons[0].startswith("the roofed floor holds ")


def test_two_machines_in_a_square_hall_with_a_middle_door() -> None:
    # the first keep-out nearest the lobby splits the hall in two 4-wide strips; packing from
    # a corner fits both
    cells = door(building(rect(0, 0, 12, 12), 5), "west", 0, 5, 3)
    lay = fitted([CUBE, CUBE], cells)
    assert lay["fit"]["pass"] == "area"


def test_a_porch_in_the_way_closes_a_doorway() -> None:
    cells = building(rect(3, 0, 15, 12), 5)
    cells = door(door(cells, "west", 3, 5), "east", 15, 5)
    porch = {(1, 1, z) for z in range(5, 8)}  # in front of the west door, below its top
    site = Box((0, 0, 0), (18, 7, 13))
    lay = fitted([CUBE], cells | porch, site=site)
    assert lay["entrances"][0]["side"] == "east"
    # the door's straight way out to the site's edge is air: the gate never takes a block
    e = lay["entrances"][0]
    assert all((x, y, 6) not in cells for x in range(16, 19) for y in range(3))
    assert e["lo"][0] == 15
    # with the porch in front of the only door, there is none
    only = door(building(rect(3, 0, 15, 12), 5), "west", 3, 5)
    r = run([CUBE], only | porch, site=site)
    assert r.reasons == [
        (
            "no doorway: no ground opening at least 3 high has a straight way out of the site "
            "and 3 blocks of floor inside it; open one on an outer face"
        )
    ]
    # an answer naming it is told why
    r = run([CUBE], only | porch, site=site, hint={"side": "west", "at": [3, 6]})
    assert r.reasons == [
        (
            "entrance at [3,6] on the west: there is no straight way out to the site's west "
            "edge at y 0..2"
        )
    ]


# --- capacity -------------------------------------------------------------------------------


def volume_hall(h: int) -> set[Cell]:
    """13 x 5 inside, the door's three columns 3 high under the lintel."""
    return door(building(rect(0, 0, 14, 6), h), "west", 0, 2)


def test_exactly_the_volume_needed_passes() -> None:
    # two 3 x 3 x 3 machines: keep-outs 5 x 4 x 5 = 100 each; +67% = 334 needed. The hall
    # holds 13 x 5 columns x 5 high (the cap: corridor 3 + chase 2) + 3 door columns x 3
    opts = layout.Options(buffer=67)
    cells = volume_hall(5)
    lay = fitted([CUBE, CUBE], cells, opts=opts)
    assert lay["fit"]["need"] == {"keepout_volume": 200, "required": 334}
    assert lay["fit"]["have"] == {"volume": 334, "volume_raw": 334, "floor": 68}
    assert lay["fit"]["spare_pct"] == 0
    assert lay["options"]["buffer"] == 67
    # one block less of roofed air
    r = run([CUBE, CUBE], cells | {(13, 4, 5)}, opts=opts)
    assert r.reasons == [
        (
            "the roofed floor holds 333 usable blocks (air up to 5 high under your roof; 333 "
            "counting all headroom); the machines need 334 (keep-outs 200 + 67%): add at least 1"
        )
    ]
    assert r.stats["volume"] == 333 and r.stats["need"]["required"] == 334


def test_headroom_over_the_cap_is_not_counted() -> None:
    lay = fitted([CUBE, CUBE], volume_hall(9), opts=layout.Options(buffer=67))
    assert lay["fit"]["have"] == {
        "volume": 334,
        "volume_raw": 13 * 5 * 9 + 9,
        "floor": 68,
    }


def test_a_solid_floor_is_named() -> None:
    cells = volume_hall(5) | {(x, 0, z) for x in range(5, 14) for z in range(1, 6)}
    r = run([CUBE, CUBE], cells)
    (why,) = r.reasons
    assert why.endswith(
        ". y 0 is solid in 45 columns under your roof, e.g. [5,0,1]: the machine floor is "
        "y 0; leave it air"
    )


def test_the_tallest_machine_needs_a_high_enough_floor() -> None:
    cells = door(building(rect(0, 0, 21, 21), 5), "west", 0, 9)
    r = run([CUBE, TALL], cells)
    assert r.reasons == [
        "m2 Test 9102 needs a 5x5 floor at least 7 high; the tallest such floor is 5"
    ]
    # a high bay where it fits
    high = door(building(rect(0, 0, 21, 21), 8), "west", 0, 9)
    fitted([CUBE, TALL], high)


def test_the_widest_machine_needs_a_wide_enough_floor() -> None:
    cells = door(building(rect(0, 0, 5, 41), 5), "north", 0, 2)
    r = run([WIDE], cells)
    assert r.reasons == [
        "m1 Test 9103 needs 11x5 free floor in some facing; the widest roofed run is 4x38"
    ]


def test_a_low_ceiling_has_no_chase() -> None:
    cells = door(building(rect(0, 0, 12, 12), 4), "west", 0, 5)
    r = run([ONE, ONE], cells)
    assert r.reasons == [
        (
            "no walkway has a ceiling at least 5 high for the cable chase; raise part of the "
            "roof over open floor"
        )
    ]


# --- packing --------------------------------------------------------------------------------


def test_a_long_hall_turns_machines() -> None:
    # 11 wide: a long machine's keep-out is 7 wide facing north, 4 facing east or west
    cells = door(building(rect(0, 0, 12, 41), 6), "north", 0, 2)
    lay = fitted([LONG] * 10, cells)
    assert {p["facing"] for p in lay["placements"]} == {"north", "east", "west"}


def test_all_four_facings_round_a_courtyard() -> None:
    ring = rect(0, 0, 24, 24) - rect(10, 10, 14, 14)
    cells = door(building(ring, 6), "west", 0, 11)
    lay = fitted([CUBE] * 12, cells)
    assert {p["facing"] for p in lay["placements"]} == set(fp.FACINGS)
    # nothing stands in the open courtyard
    for k in lay["keepouts"]:
        assert not any(10 <= x <= 14 and 10 <= z <= 14 for x, _, z in _cells(k))


def test_an_l_shaped_plan() -> None:
    cols = rect(0, 0, 24, 12) | rect(0, 0, 12, 24)
    cells = door(building(cols, 6), "west", 0, 5)
    lay = fitted([CUBE, LONG, CUBE, LONG, CUBE, LONG, CUBE, LONG, CUBE], cells)
    # down the south wing and along the east one
    assert any(p["lo"][2] > 13 for p in lay["placements"])
    assert any(p["lo"][0] > 13 for p in lay["placements"])


def test_columns_inside_are_avoided() -> None:
    pillars = {
        (x, y, z) for x in range(5, 20, 7) for z in range(5, 20, 7) for y in range(6)
    }
    cells = door(building(rect(0, 0, 22, 22), 6), "west", 0, 10) | pillars
    lay = fitted([CUBE, LONG, ONE, CUBE, LONG, ONE, CUBE], cells)
    for k in lay["keepouts"]:
        assert not any(c in pillars for c in _cells(k))


def test_flow_falls_back_to_area() -> None:
    cells = door(building(rect(0, 0, 12, 30), 6), "west", 0, 14)
    lay = fitted([LONG] * 8, cells)
    assert lay["fit"]["pass"] == "area"


def test_machines_follow_their_plan_from_the_door() -> None:
    lay = fitted([CUBE] * 4, door(building(rect(0, 0, 30, 30), 6), "west", 0, 14))
    assert lay["fit"]["pass"] == "flow"
    # m1 is the nearest the lobby: its front opens onto it
    m1 = lay["placements"][0]
    assert m1["corridor"] == "c1"


def test_spacing_is_kept_between_keep_outs() -> None:
    cells = door(building(rect(0, 0, 30, 30), 6), "west", 0, 14)
    lay = fitted([CUBE, LONG, CUBE, ONE, LONG], cells, opts=layout.Options(spacing=2))
    for a, b in itertools.combinations(lay["keepouts"], 2):
        gap_x = max(a["lo"][0] - b["hi"][0], b["lo"][0] - a["hi"][0]) - 1
        gap_z = max(a["lo"][2] - b["hi"][2], b["lo"][2] - a["hi"][2]) - 1
        assert max(gap_x, gap_z) >= 2, (a["id"], b["id"])
    assert lay["options"]["spacing"] == 2


def test_a_packing_refusal_names_what_did_not_fit() -> None:
    r = run([CUBE] * 3, volume_hall(5), opts=layout.Options(buffer=0))
    assert r.reasons == [
        (
            "placed 2 of 3: m3 did not fit (keep-outs m3 5x5 floor 4 high); largest free floor "
            "left is 1x5 (h 5) at [13,1]; widen a hall or add one at least 5x5, 4 high, joined "
            "to the door"
        )
    ]
    assert r.stats["placed"] == 2 and r.stats["of"] == 3


# --- zones ----------------------------------------------------------------------------------


def big_hall() -> set[Cell]:
    return door(building(rect(0, 0, 24, 24), 7), "west", 0, 11)


def test_an_open_zone_stays_empty() -> None:
    foyer = {"kind": "open", "lo": [0, 0, 0], "hi": [12, 6, 24]}
    lay = fitted([CUBE] * 6, big_hall(), zones=[foyer])
    for k in lay["keepouts"]:
        assert not _overlap(k, foyer), k["id"]
    assert isinstance(lay["fit"]["zones"], str)
    # an atrium above machine height takes nothing away
    atrium = {"kind": "open", "lo": [1, 5, 1], "hi": [23, 6, 23]}
    assert fitted([CUBE] * 6, big_hall(), zones=[atrium])["placements"]


def test_machines_stay_in_their_zones() -> None:
    zone = {"kind": "machines", "lo": [14, 0, 1], "hi": [23, 6, 23]}
    lay = fitted([CUBE] * 6, big_hall(), zones=[zone])
    for k in lay["keepouts"]:
        assert all(14 <= c[0] <= 23 and 1 <= c[2] <= 23 for c in _cells(k)), k["id"]


@pytest.mark.parametrize(
    ("zone", "why"),
    [
        (
            {"kind": "open", "lo": [0, 0, 0], "hi": [30, 3, 3]},
            "zone 1 (open) [0, 0, 0]..[30, 3, 3] is not inside the site [0, 0, 0]..[25, 9, 25]",
        ),
        (
            {"kind": "machines", "lo": [5, 0, 5], "hi": [1, 3, 9]},
            "zone 1 (machines): lo [5, 0, 5] is above hi [1, 3, 9]",
        ),
        (
            {"kind": "garden", "lo": [0, 0, 0], "hi": [1, 1, 1]},
            "zone 1 is not {kind: machines or open, lo: [x,y,z], hi: [x,y,z]}",
        ),
    ],
)
def test_a_bad_zone_is_refused(zone: dict[str, Any], why: str) -> None:
    assert run([CUBE], big_hall(), zones=[zone]).reasons == [why]


# --- prior ----------------------------------------------------------------------------------


def _where(lay: Mapping[str, Any]) -> list[tuple[str, list[int], str]]:
    return [(p["id"], p["lo"], p["facing"]) for p in lay["placements"]]


def test_a_prior_fit_keeps_its_places() -> None:
    fs = [CUBE, LONG, CUBE, ONE]
    cells = door(building(rect(0, 0, 30, 30), 6), "west", 0, 14)
    first = fitted(fs, cells)
    # a pillar far from every machine: everything stays
    far = {(28, y, 28) for y in range(6)}
    assert not any((28, 0, 28) in set(_cells(k)) for k in first["keepouts"])
    again = fitted(fs, cells | far, prior=first)
    assert _where(again) == _where(first) and again["fit"]["pass"] == "prior"
    assert again["id"] != first["id"]  # a new building
    # a pillar in m2's keep-out: m2 moves, the rest stay
    k2 = next(k for k in first["keepouts"] if k["of"] == "m2")
    x, _, z = k2["lo"]
    moved = fitted(fs, cells | {(x, y, z) for y in range(6)}, prior=first)
    assert moved["fit"]["pass"] == "flow"
    before, after = _where(first), _where(moved)
    assert after[1] != before[1]
    assert [after[i] for i in (0, 2, 3)] == [before[i] for i in (0, 2, 3)]


def test_a_prior_of_other_machines_is_ignored() -> None:
    cells = door(building(rect(0, 0, 30, 30), 6), "west", 0, 14)
    first = fitted([CUBE, CUBE], cells)
    lay = fitted([LONG, LONG], cells, prior=first)
    assert lay["fit"]["pass"] == "flow"
    assert lay == fitted([LONG, LONG], cells)


# --- determinism, ids and the program -------------------------------------------------------


def test_the_same_building_gives_the_same_layout() -> None:
    cells = sorted(
        door(building(rect(0, 0, 24, 24) - rect(10, 10, 14, 14), 6), "west", 0, 11)
    )
    a = run([CUBE, LONG, ONE] * 3, set(cells))
    shuffled = cells[:]
    random.Random(3).shuffle(shuffled)
    b = run([CUBE, LONG, ONE] * 3, shuffled, site=site_of(cells))
    assert json.dumps(a.layout, sort_keys=True) == json.dumps(b.layout, sort_keys=True)
    # positions only: the blocks they hold (the palette) don't matter
    stone = dict.fromkeys(cells, "minecraft:stone@0")
    brick = dict.fromkeys(cells, "minecraft:brick_block@0")
    assert run([CUBE, LONG, ONE] * 3, stone, site=site_of(cells)).layout == a.layout
    assert run([CUBE, LONG, ONE] * 3, brick, site=site_of(cells)).layout == a.layout
    assert a.layout is not None
    text = json.dumps([list(c) for c in cells], separators=(",", ":"))
    assert a.layout["fit"]["building"] == hashlib.sha256(text.encode()).hexdigest()[:12]


def test_the_id_follows_the_options_and_the_hint() -> None:
    cells = hall_with_doors(("west", 0, 5, 3), ("east", 12, 5, 3))
    a = fitted([CUBE], cells)
    assert "buffer" not in a["options"]  # at its default, as before D422
    b = fitted([CUBE], cells, opts=layout.Options(buffer=30))
    c = fitted([CUBE], cells, hint={"side": "west", "at": [0, 6]})
    assert len({a["id"], b["id"], c["id"]}) == 3
    assert _where(a) == _where(c)


def test_a_program_from_plans_fits_with_its_services() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    structure_db.load(conn, DATASET)
    try:
        plans = [production(), power()]
        prog = layout.program(conn, plans)
        laid = layout.plan_layout(conn, plans)
    finally:
        conn.close()
    cells = door(building(rect(0, 0, 26, 26), 7), "west", 0, 12)
    site = site_of(cells)
    lay = verify(fit.fit(prog, cells, site, OPTS), cells, site)
    assert lay["plans"] == laid["plans"] and lay["gt"] == laid["gt"]
    assert lay["services"] == laid["services"]  # m<i> is instance i in both
    assert lay["unplaced"] == laid["unplaced"]
    assert [p["node"] for p in lay["placements"]] == [
        p["node"] for p in laid["placements"]
    ]
    laid_warnings = [w for w in laid["warnings"] if "no hatch data" not in w]
    assert [w for w in lay["warnings"] if "no hatch data" not in w] == laid_warnings
    # program.json round trip: the same fit without the DB
    saved = json.loads(json.dumps(prog.to_json(OPTS)))
    assert saved["need"] == fit.need(prog, OPTS)
    back, opts = layout.Program.from_json(saved)
    assert opts == OPTS
    assert fit.fit(back, cells, site, opts).layout == lay


def test_a_program_with_nothing_to_place() -> None:
    r = fit.fit(
        program_of(), building(rect(0, 0, 9, 9), 5), Box((0, 0, 0), (10, 7, 10))
    )
    assert r.reasons == ["no placements: none of the plans' machines has a footprint"]


def test_need() -> None:
    n = fit.need(program_of(CUBE, TALL, WIDE, ONE), OPTS)
    assert n == {
        "keepout_volume": 100 + 175 + 220 + 18,
        "required": -(-(513 * 125) // 100),
        "floor_area": 25 + 25 + 55 + 9,
        "tallest": [5, 7, 5],
        "widest": [11, 4, 5],
        "door_height": 3,
        "aisle_height": 5,
        "cap_height": 7,
    }


def test_sixty_machines_on_a_wide_site_in_good_time() -> None:
    fs = [LONG, CUBE, square(9003, 3, 4, 3), ONE, square(9301, 7, 5, 5)]
    prog = program_of(*[fs[i % 5] for i in range(60)])
    cells = door(building(rect(0, 0, 63, 63), 8), "west", 0, 30)
    start = time.monotonic()
    r = fit.fit(prog, cells, Box((0, 0, 0), (63, 10, 63)), OPTS)
    assert r.ok, r.reasons
    assert time.monotonic() - start < 30  # about 1 s on a dev box
