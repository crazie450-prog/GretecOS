"""The sheltered floor of a building (WO-10.16, D422 "site mode").

A site build's machines stand on y 0 (the first layer above the world floor) under the
building's own roof. From the building's blocks alone (their positions; the block a cell holds
doesn't matter) this finds, for every column ``(x, z)`` of the site, how much air stands on y 0
under a roof: ``h(x, z)`` is the run of air from y 0 up to the first solid block above it, and 0
when y 0 is solid or nothing is above (open sky is not interior). Any block above counts as a
roof: an arcade's lintel, an overhang, a floating slab.

Pure and deterministic, no I/O: :mod:`gregtec.build.fit` and the shell sandbox's ``covered()``
helper both read it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from gregtec.build.layout import Box, Cell

type Column = tuple[int, int]


@dataclass(frozen=True)
class Floor:
    site: Box
    # every site column -> its sheltered air height from y 0 (0: y 0 solid, or no roof)
    h: dict[Column, int]
    # columns with a roof over them whose y 0 is solid (a floor laid on the machine floor)
    blocked: tuple[Column, ...]
    # columns with no block at all (open ground: a way out)
    empty: frozenset[Column]


def derive(cells: Iterable[Cell], site: Box) -> Floor:
    """The sheltered floor of the blocks ``cells`` inside ``site`` (cells outside it are not the
    building's). O(cells + columns)."""
    solid: dict[Column, list[int]] = {}
    for x, y, z in cells:
        if site.contains((x, y, z)):
            solid.setdefault((x, z), []).append(y)
    h: dict[Column, int] = {}
    blocked: list[Column] = []
    for x in range(site.lo[0], site.hi[0] + 1):
        for z in range(site.lo[2], site.hi[2] + 1):
            ys = sorted(solid.get((x, z), ()))
            y0 = site.lo[1]
            if not ys:
                h[(x, z)] = 0
            elif ys[0] != y0:
                h[(x, z)] = ys[0] - y0
            else:
                h[(x, z)] = 0
                # solid at y 0 with air and then a block above: a floor under a roof
                if any(b - a > 1 for a, b in pairwise(ys)):
                    blocked.append((x, z))
    empty = frozenset(c for c in h if c not in solid)
    return Floor(site, h, tuple(sorted(blocked)), empty)


def measure(
    cells: Iterable[Cell], site: Box, need: Mapping[str, Any]
) -> dict[str, int]:
    """The sheltered volume of ``cells`` against a program's ``need`` (``door_height``,
    ``cap_height``, ``required``; :func:`gregtec.build.fit.need`): every roofed column at least
    the door height high counts its air up to the cap. Fit counts only the columns joined to
    its door, so this is what fit gets for a building in one piece, and an upper bound
    otherwise."""
    f = derive(cells, site)
    ch, cap, want = need["door_height"], need["cap_height"], need["required"]
    cols = [v for _, v in sorted(f.h.items()) if v >= ch]
    volume = sum(min(v, cap) for v in cols)
    return {
        "volume": volume,
        "floor": len(cols),
        "need_volume": want,
        "short_by": want - volume,
    }
