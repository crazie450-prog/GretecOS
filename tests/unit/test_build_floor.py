"""WO-10.16 (D422): the sheltered floor a site build's machines stand on.

Made-up buildings as sets of solid positions; no game data.
"""

from __future__ import annotations

import random

from gregtec.build import fit, floor, layout
from gregtec.build.layout import Box

Cell = tuple[int, int, int]
SITE = Box((0, 0, 0), (11, 9, 11))


def hall(x0: int, z0: int, x1: int, z1: int, h: int) -> set[Cell]:
    """Walls round the columns x0..x1, z0..z1 (y 0..h-1) and a roof over them at y h; the
    air inside is x0+1..x1-1, z0+1..z1-1."""
    out: set[Cell] = set()
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            out.add((x, h, z))
            if x in (x0, x1) or z in (z0, z1):
                out.update((x, y, z) for y in range(h))
    return out


def test_a_roofed_hall() -> None:
    f = floor.derive(hall(0, 0, 6, 6, 4), SITE)
    assert f.h[(3, 3)] == 4 and f.h[(1, 1)] == 4 and f.h[(5, 5)] == 4
    assert f.h[(0, 3)] == 0 and f.h[(6, 6)] == 0  # walls: y 0 solid
    assert f.h[(8, 8)] == 0 and (8, 8) in f.empty  # open ground outside
    assert (0, 3) not in f.empty and (3, 3) not in f.empty
    assert f.blocked == ()
    assert set(f.h) == {(x, z) for x in range(12) for z in range(12)}


def test_an_open_courtyard_is_not_interior() -> None:
    ring = hall(0, 0, 10, 10, 4)
    # no roof over the yard
    ring -= {(x, 4, z) for x in range(3, 8) for z in range(3, 8)}
    f = floor.derive(ring, SITE)
    assert f.h[(5, 5)] == 0 and (5, 5) in f.empty
    assert f.h[(1, 5)] == 4 and f.h[(2, 2)] == 4


def test_a_floating_block_is_a_roof() -> None:
    f = floor.derive({(2, 5, 2)}, SITE)
    assert f.h[(2, 2)] == 5 and (2, 2) not in f.empty
    assert sum(1 for v in f.h.values() if v) == 1


def test_a_solid_floor_under_a_roof_is_blocked() -> None:
    cells = hall(0, 0, 6, 6, 4) | {(x, 0, z) for x in range(1, 6) for z in range(1, 6)}
    f = floor.derive(cells, SITE)
    assert f.h[(3, 3)] == 0
    assert f.blocked == tuple((x, z) for x in range(1, 6) for z in range(1, 6))
    # a wall is solid all the way up: no floor under a roof
    assert (0, 3) not in f.blocked


def test_an_arcade_without_walls() -> None:
    roof = {(x, 3, z) for x in range(6) for z in range(6)}
    pillars = {(x, y, z) for x in (0, 5) for z in (0, 5) for y in range(3)}
    f = floor.derive(roof | pillars, SITE)
    assert f.h[(0, 0)] == 0 and f.h[(5, 0)] == 0
    assert f.h[(0, 2)] == 3 and f.h[(2, 2)] == 3
    assert f.h[(6, 2)] == 0


def test_cells_outside_the_site_are_not_the_building() -> None:
    site = Box((0, 0, 0), (5, 3, 5))
    f = floor.derive({(2, 4, 2), (9, 1, 1), (1, -1, 1)}, site)  # above, beside, below
    assert all(v == 0 for v in f.h.values())
    assert f == floor.derive(set(), site)


def test_the_order_of_the_cells_does_not_matter() -> None:
    cells = sorted(hall(0, 0, 6, 6, 4) | {(3, 1, 3)})
    rnd = random.Random(7)
    other = cells[:]
    rnd.shuffle(other)
    assert floor.derive(cells, SITE) == floor.derive(other, SITE)


def test_measure_agrees_with_the_fit() -> None:
    from .test_build_fit import door, program_of, square

    prog = program_of(square(9001, 3, 3, 3), square(9002, 3, 3, 3))
    site = Box((0, 0, 0), (19, 10, 19))
    cells = door(hall(0, 0, 14, 10, 7), "west", 0, 4)
    opts = layout.Options()
    got = floor.measure(cells, site, fit.need(prog, opts))
    r = fit.fit(prog, cells, site, opts)
    assert r.ok, r.reasons
    assert got["volume"] == r.stats["volume"] and got["floor"] == r.stats["floor"]
    assert got["need_volume"] == r.stats["need"]["required"] == 250
    assert got["short_by"] == 250 - got["volume"] < 0
    # 13 x 9 inside, 5 high (the cap: a keep-out 4 high, the corridor and chase 5), and
    # the door's three columns 3 high under its lintel
    assert got["volume"] == 13 * 9 * 5 + 3 * 3
