"""The machine fitter of site mode (WO-10.16, D422; docs/design/building_first_site_mode.md).

In site mode the building comes first: the model (or the procedural generator) builds freely
inside a SITE box, and GOS fits every machine of a :class:`~gregtec.build.layout.Program` into
the building afterwards, deterministically. Machine positions stay code-owned (D389): the
building drives them, the model never places one.

1. **Floor.** :func:`gregtec.build.floor.derive` gives each site column its sheltered air height
   ``h`` above y 0 (the machine floor).
2. **Door.** A ground opening at least ``corridor_height`` high with a straight way out to the
   site's edge (:func:`_door`): the answer's entrance when it gives one, else the widest run of
   such openings whose roofed floor holds the machines (else the one holding most). A
   ``corridor_width``-deep lobby (corridor c1, the hall) lies inside it.
3. **Capacity.** The usable region R is every column at least ``corridor_height`` high under a
   roof, 4-joined to the lobby, less what zones mask. Its volume V counts each column's air up
   to H (the tallest keep-out, or corridor plus chase if higher). V must reach
   M = ceil((1 + buffer/100) * sum of the keep-out volumes); aisles, the lobby and the chases
   come out of the buffer.
4. **Packing.** Each machine's keep-out (its box plus ``clearance`` round and ``clearance_top``
   above) goes on R's free floor in one of four facings, nearest (by walking distance) the
   front of the plan's previous machine, else the lobby (the *flow* pass); when one doesn't
   fit, all are packed again largest first nearest the lobby, and when that fails too, from
   each corner of the site in turn (the *area* pass). Placements kept from an earlier fit
   (``prior``) go first and stay where they are while they still fit.
5. **Walkways.** The free floor left becomes aisles (corridors c2...), with chases above them
   where the ceiling is high enough; each machine takes the nearest corridor and its chase.
6. **Check.** Every front and hatch kind must be reached from the door
   (:func:`gregtec.build.layout.reach_all`, the open air under the roof included).

The result is an ordinary gos-layout v1 with a ``fit`` block (bounds = the site), or the reasons
it can't be made, cheapest first. Deterministic: no randomness, every set read sorted, ties
broken by (distance, z, x, facing). Game numbers aren't made here: sizes are the footprints'.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gregtec.build import footprints as fp
from gregtec.build import layout as lay_lib
from gregtec.build.floor import Column, Floor, derive
from gregtec.build.layout import Box, Cell, Instance, Options, Program

MAX_ZONES = 12
ZONE_KINDS = ("machines", "open")
SIDES: dict[str, Column] = {
    "north": (0, -1),
    "east": (1, 0),
    "south": (0, 1),
    "west": (-1, 0),
}
_DOOR_ORDER = ("west", "south", "east", "north")  # the tie order of two equal doorways
_STEPS: tuple[Column, ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))
_FAR = 1 << 30
# the area pass's fallbacks: corner-first from each corner of the site (signs of z and x)
_CORNERS: tuple[tuple[int, int], ...] = ((1, 1), (1, -1), (-1, 1), (-1, -1))
# the area pass's fallbacks, in order: from each corner, then hugging walls and machines
# (the most blocked columns round the keep-out; ties from each corner)
_FALLBACKS: tuple[tuple[str, int, int], ...] = tuple(
    (how, sz, sx) for how in ("corner", "contact") for sz, sx in _CORNERS
)

type Size = tuple[int, int, int]


@dataclass(frozen=True)
class FitResult:
    layout: dict[str, Any] | None
    reasons: list[str]
    stats: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.layout is not None


def keep_size(f: fp.Footprint, opts: Options) -> Size:
    """The keep-out of ``f`` as it faces: (x, y, z) in blocks."""
    sx, sy, sz = f.size
    return (sx + 2 * opts.clearance, sy + opts.clearance_top, sz + 2 * opts.clearance)


def need(program: Program, opts: Options) -> dict[str, Any]:
    """What a building must hold for ``program`` (the capacity rule, D422)."""
    sizes = [keep_size(i.footprint, opts) for i in program.instances]
    k = sum(x * y * z for x, y, z in sizes)
    ch, chh = opts.corridor_height, opts.chase_height
    tallest = max(sizes, key=lambda s: (s[1], s[0] * s[2]), default=(0, 0, 0))
    widest = max(sizes, key=lambda s: (max(s[0], s[2]), s[0] * s[2]), default=(0, 0, 0))
    return {
        "keepout_volume": k,
        "required": -(-k * (100 + opts.buffer) // 100),
        "floor_area": (fa := sum(x * z for x, _, z in sizes)),
        # the same buffer on the floor: air counted up to cap_height can make up for a
        # floor smaller than the keep-outs themselves, and the machines stand on the floor
        "floor_required": -(-fa * (100 + opts.buffer) // 100),
        "tallest": list(tallest),
        "widest": list(widest),
        "door_height": ch,
        "aisle_height": ch + chh,
        "cap_height": max([ch + chh, *(s[1] for s in sizes)]),
    }


def _name(inst: Instance) -> str:
    return inst.name or inst.footprint.name or f"mte:{inst.mte}"


def _sha(obj: object) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# --- zones ----------------------------------------------------------------------------------


def _zones(
    zones: Sequence[Mapping[str, Any]], site: Box
) -> tuple[list[Box], list[Box], list[str]]:
    """The "machines" and "open" zones, and why any is refused."""
    machines: list[Box] = []
    open_: list[Box] = []
    why: list[str] = []
    if len(zones) > MAX_ZONES:
        return [], [], [f"{len(zones)} zones: at most {MAX_ZONES}"]
    for n, z in enumerate(zones, 1):
        kind = z.get("kind")
        try:
            b = lay_lib.box_of(z)
            ok = all(isinstance(v, int) for v in (*b.lo, *b.hi))
        except (KeyError, IndexError, TypeError):
            ok = False
        if kind not in ZONE_KINDS or not ok:
            why.append(
                f"zone {n} is not {{kind: {' or '.join(ZONE_KINDS)}, lo: [x,y,z], "
                "hi: [x,y,z]}"
            )
            continue
        if any(b.lo[i] > b.hi[i] for i in range(3)):
            why.append(f"zone {n} ({kind}): lo {list(b.lo)} is above hi {list(b.hi)}")
        elif not (site.contains(b.lo) and site.contains(b.hi)):
            why.append(
                f"zone {n} ({kind}) {list(b.lo)}..{list(b.hi)} is not inside the site "
                f"{list(site.lo)}..{list(site.hi)}"
            )
        else:
            (machines if kind == "machines" else open_).append(b)
    return machines, open_, why


def _usable(
    floor: Floor, cols: Iterable[Column], machines: Sequence[Box], open_: Sequence[Box]
) -> dict[Column, int]:
    """Each column's air a keep-out may fill (from y 0): its sheltered height, below every
    "open" zone over it, and within the "machines" zones when there are any."""
    y0 = floor.site.lo[1]
    out: dict[Column, int] = {}
    for x, z in cols:
        u = floor.h[(x, z)]
        for b in open_:
            if b.lo[0] <= x <= b.hi[0] and b.lo[2] <= z <= b.hi[2]:
                u = min(u, b.lo[1] - y0)
        if machines:
            top = 0
            for lo, hi in sorted(
                (b.lo[1] - y0, b.hi[1] - y0)
                for b in machines
                if b.lo[0] <= x <= b.hi[0] and b.lo[2] <= z <= b.hi[2]
            ):
                if lo > top:
                    break
                top = max(top, hi + 1)
            u = min(u, top)
        out[(x, z)] = max(u, 0)
    return out


# --- the door -------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Door:
    side: str
    cols: tuple[Column, ...]  # the doorway's columns, on the building's edge
    lobby: tuple[Column, ...]  # corridor_width deep inside it


def _runs(cands: Sequence[tuple[str, Column]]) -> list[tuple[str, list[Column]]]:
    """Doorway candidates in runs: side by side on one face, along it."""
    groups: dict[tuple[str, int], list[int]] = {}
    for side, (x, z) in cands:
        along_x = side in ("north", "south")
        groups.setdefault((side, z if along_x else x), []).append(x if along_x else z)
    out: list[tuple[str, list[Column]]] = []
    for (side, normal), alongs in sorted(groups.items()):
        along_x = side in ("north", "south")
        run: list[int] = []
        for a in sorted(alongs):
            if run and a != run[-1] + 1:
                out.append(
                    (side, [(v, normal) if along_x else (normal, v) for v in run])
                )
                run = []
            run.append(a)
        out.append((side, [(v, normal) if along_x else (normal, v) for v in run]))
    return out


def _door(
    floor: Floor, tall: set[Column], opts: Options, hint: Mapping[str, Any] | None
) -> tuple[list[_Door], list[str]]:
    """The answer's door (one, validated), or every candidate door in rank order: per run of
    openings (widest first, ties west, south, east, north, then the lowest), its
    ``corridor_width``-wide window nearest the run's middle with a full lobby behind it."""
    ch, cw = opts.corridor_height, opts.corridor_width
    site = floor.site

    def inside(c: Column) -> bool:
        return site.lo[0] <= c[0] <= site.hi[0] and site.lo[2] <= c[1] <= site.hi[2]

    def way_out(col: Column, side: str) -> bool:
        dx, dz = SIDES[side]
        c = (col[0] + dx, col[1] + dz)
        while inside(c):
            if c not in tall and c not in floor.empty:
                return False
            c = (c[0] + dx, c[1] + dz)
        return True

    def edge(col: Column, side: str) -> bool:
        dx, dz = SIDES[side]
        return (col[0] + dx, col[1] + dz) not in tall

    def lobby(cols: Sequence[Column], side: str) -> tuple[Column, ...] | None:
        dx, dz = SIDES[side]
        out = tuple(
            sorted((x - k * dx, z - k * dz) for x, z in cols for k in range(cw))
        )
        return out if all(c in tall for c in out) else None

    def windows(run: list[Column], at: int) -> list[list[Column]]:
        """The run's windows ``corridor_width`` wide (or the run's width), the one centred on
        ``at`` first, then outward (ties: the lower)."""
        w = min(len(run), cw)
        first = min(max(at - (w - 1) // 2, 0), len(run) - w)
        starts = sorted(range(len(run) - w + 1), key=lambda s: (abs(s - first), s))
        return [run[s : s + w] for s in starts]

    def found(side: str, cols: list[Column]) -> _Door | None:
        got = lobby(cols, side)
        return None if got is None else _Door(side, tuple(cols), got)

    cands = [
        (side, col)
        for col in sorted(tall)
        for side in SIDES
        if edge(col, side) and way_out(col, side)
    ]
    runs = _runs(cands)
    if hint is not None:
        side, at = hint.get("side"), hint.get("at")
        shape = ["the entrance is not {side: north|east|south|west, at: [x, z]}"]
        if (
            not isinstance(side, str)
            or not isinstance(at, list | tuple)
            or len(at) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in at)
        ):
            return [], shape
        col = (at[0], at[1])
        head = f"entrance at [{col[0]},{col[1]}] on the {side}"
        if side not in SIDES:
            return [], [f"{head}: the side is not one of {', '.join(SIDES)}"]
        if col not in tall:
            return [], [f"{head}: it is not roofed floor at least {ch} high"]
        if not edge(col, side):
            return [], [f"{head}: it is not on the building's {side} edge"]
        if not way_out(col, side):
            way = f"there is no straight way out to the site's {side} edge at y 0..{ch - 1}"
            return [], [f"{head}: {way}"]
        run = next(r for s, r in runs if s == side and col in r)
        for cols in windows(run, run.index(col)):
            if col in cols and (door := found(side, cols)) is not None:
                return [door], []
        return [], [f"{head}: the floor inside it is less than {cw} deep"]
    ranked = sorted(
        runs, key=lambda r: (-len(r[1]), _DOOR_ORDER.index(r[0]), r[1][0][::-1])
    )
    doors = []
    for side, run in ranked:
        door = next(
            (
                d
                for cols in windows(run, (len(run) - 1) // 2)
                if (d := found(side, cols))
            ),
            None,
        )
        if door is not None:
            doors.append(door)
    if doors:
        return doors, []
    why = (
        f"no doorway: no ground opening at least {ch} high has a straight way out of the "
        f"site and {cw} blocks of floor inside it; open one on an outer face"
    )
    return [], [why]


# --- grids ----------------------------------------------------------------------------------


def _flood(cols: set[Column], starts: Iterable[Column]) -> dict[Column, int]:
    """Walking distance (4-joined) over ``cols`` from ``starts``."""
    dist = {s: 0 for s in sorted(starts) if s in cols}
    todo = deque(sorted(dist))
    while todo:
        cur = todo.popleft()
        for dx, dz in _STEPS:
            n = (cur[0] + dx, cur[1] + dz)
            if n in cols and n not in dist:
                dist[n] = dist[cur] + 1
                todo.append(n)
    return dist


def _nearest(cols: set[Column], sources: Mapping[Column, int]) -> dict[Column, int]:
    """Each column of ``cols`` labelled with the nearest source's label (ties: the lowest)."""
    label = dict(sources)
    frontier = sorted(sources)
    while frontier:
        nxt: dict[Column, int] = {}
        for cur in frontier:
            for dx, dz in _STEPS:
                n = (cur[0] + dx, cur[1] + dz)
                if n in cols and n not in label:
                    nxt[n] = min(nxt.get(n, _FAR), label[cur])
        label.update(nxt)
        frontier = sorted(nxt)
    return label


class _Sat:
    """A summed-area table of free columns over the site: is a rectangle all free, in O(1)."""

    def __init__(self, site: Box, free: set[Column]) -> None:
        self.x0, self.z0 = site.lo[0], site.lo[2]
        self.w, self.d = site.size[0], site.size[2]
        w1 = self.w + 1
        s = [0] * (w1 * (self.d + 1))
        for j in range(self.d):
            run = 0
            z = self.z0 + j
            base, prev = (j + 1) * w1, j * w1
            for i in range(self.w):
                run += (self.x0 + i, z) in free
                s[base + i + 1] = s[prev + i + 1] + run
        self.s = s

    def full(self, x: int, z: int, kx: int, kz: int) -> bool:
        i, j = x - self.x0, z - self.z0
        if i < 0 or j < 0 or i + kx > self.w or j + kz > self.d:
            return False
        w1, s = self.w + 1, self.s
        total = (
            s[(j + kz) * w1 + i + kx]
            - s[j * w1 + i + kx]
            - s[(j + kz) * w1 + i]
            + s[j * w1 + i]
        )
        return total == kx * kz

    def count(self, x: int, z: int, kx: int, kz: int) -> int:
        """The free columns in the rectangle, clipped to the site."""
        i0, j0 = max(x - self.x0, 0), max(z - self.z0, 0)
        i1, j1 = min(x - self.x0 + kx, self.w), min(z - self.z0 + kz, self.d)
        if i0 >= i1 or j0 >= j1:
            return 0
        w1, s = self.w + 1, self.s
        return s[j1 * w1 + i1] - s[j0 * w1 + i1] - s[j1 * w1 + i0] + s[j0 * w1 + i0]


def _rects(cols: set[Column]) -> list[tuple[Column, Column]]:
    """``cols`` cut into rectangles greedily: row by row (z, then x), each as long along x and
    then as deep along z as it goes. (lo, hi) inclusive."""
    left = set(cols)
    out: list[tuple[Column, Column]] = []
    for x, z in sorted(cols, key=lambda c: (c[1], c[0])):
        if (x, z) not in left:
            continue
        x1 = x
        while (x1 + 1, z) in left:
            x1 += 1
        z1 = z
        while all((i, z1 + 1) in left for i in range(x, x1 + 1)):
            z1 += 1
        for i in range(x, x1 + 1):
            for k in range(z, z1 + 1):
                left.discard((i, k))
        out.append(((x, z), (x1, z1)))
    return out


def _largest(cols: set[Column], site: Box) -> tuple[int, int, int, int]:
    """The largest rectangle of ``cols`` (by area; ties: the first row by row): x, z, w, l."""
    best = (0, 0, 0, 0)
    area = 0
    heights = [0] * site.size[0]
    for z in range(site.lo[2], site.hi[2] + 1):
        for i in range(site.size[0]):
            heights[i] = heights[i] + 1 if (site.lo[0] + i, z) in cols else 0
        stack: list[int] = []
        for i in range(site.size[0] + 1):
            hgt = heights[i] if i < site.size[0] else 0
            while stack and heights[stack[-1]] >= hgt:
                top = stack.pop()
                left = stack[-1] + 1 if stack else 0
                a = heights[top] * (i - left)
                if a > area:
                    area = a
                    best = (
                        site.lo[0] + left,
                        z - heights[top] + 1,
                        i - left,
                        heights[top],
                    )
            stack.append(i)
    return best


# --- the fit --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Spot:
    facing: fp.Facing
    body: Box
    keep: Box


class _Packer:
    """The free floor at each keep-out height, as machines take it."""

    def __init__(
        self,
        site: Box,
        usable: Mapping[Column, int],
        floor_cols: set[Column],
        heights: Iterable[int],
        opts: Options,
    ) -> None:
        self.site, self.usable, self.opts = site, usable, opts
        self.floor_cols = floor_cols  # R less the lobby
        self.heights = sorted(set(heights))
        self.taken: set[Column] = set()
        self._rebuild()

    def free(self, ky: int) -> set[Column]:
        return {
            c for c in self.floor_cols if self.usable[c] >= ky and c not in self.taken
        }

    def _rebuild(self) -> None:
        self.sat = {ky: _Sat(self.site, self.free(ky)) for ky in self.heights}

    def fits(self, ky: int, keep: Box) -> bool:
        kx, _, kz = keep.size
        return self.sat[ky].full(keep.lo[0], keep.lo[2], kx, kz)

    def occupy(self, keep: Box) -> None:
        s = self.opts.spacing
        self.taken.update(
            (x, z)
            for x in range(keep.lo[0] - s, keep.hi[0] + s + 1)
            for z in range(keep.lo[2] - s, keep.hi[2] + s + 1)
        )
        self._rebuild()


def _spot(turned: fp.Footprint, x: int, z: int, opts: Options) -> _Spot:
    """The machine turned as ``turned`` with its keep-out's corner at column (x, z)."""
    c = opts.clearance
    sx, sy, sz = turned.size
    body = Box((x + c, 0, z + c), (x + c + sx - 1, sy - 1, z + c + sz - 1))
    return _Spot(turned.facing, body, lay_lib.keepout_of(body, c, opts.clearance_top))


def _front(turned: fp.Footprint, body: Box) -> Column:
    f = fp.FRONT[turned.facing]
    return (body.lo[0] - turned.lo[0] + f[0], body.lo[2] - turned.lo[2] + f[2])


def _best(
    inst: Instance,
    packer: _Packer,
    dist: Mapping[Column, int],
    opts: Options,
    turns: dict[tuple[str, str], fp.Footprint],
    corner: tuple[str, int, int] | None = None,
) -> _Spot | None:
    """The free spot for ``inst`` whose front is nearest by ``dist`` (ties: z, x, facing); with
    ``corner`` ("corner" and the signs of z and x), the one nearest that corner of the site
    instead (by z, then x, then facing; ``dist`` unused); with ("contact", z sign, x sign) the
    one with the most blocked columns round its keep-out, ties as for that corner."""
    site, c = packer.site, opts.clearance
    best: tuple[tuple[int, ...], fp.Footprint, int, int] | None = None
    for fi, facing in enumerate(fp.FACINGS):
        turned = _turn(inst, facing, turns)
        kx, ky, kz = keep_size(turned, opts)
        sat = packer.sat[ky]
        # the front's column less the keep-out's corner (see _spot and _front)
        lo, f = turned.lo, fp.FRONT[facing]
        fx, fz = c - lo[0] + f[0], c - lo[2] + f[2]
        for z in range(site.lo[2], site.hi[2] - kz + 2):
            for x in range(site.lo[0], site.hi[0] - kx + 2):
                if not sat.full(x, z, kx, kz):
                    continue
                if corner is None:
                    key: tuple[int, ...] = (dist.get((x + fx, z + fz), _FAR), z, x, fi)
                else:
                    how, sz, sx = corner
                    key = (
                        z if sz > 0 else -(z + kz - 1),
                        x if sx > 0 else -(x + kx - 1),
                        fi,
                    )
                    if how == "contact":
                        ring = (kx + 2) * (kz + 2) - kx * kz
                        free = sat.count(x - 1, z - 1, kx + 2, kz + 2) - kx * kz
                        key = (free - ring, *key)
                if best is None or key < best[0]:
                    best = (key, turned, x, z)
    if best is None:
        return None
    _, turned, x, z = best
    return _spot(turned, x, z, opts)


def _turn(
    inst: Instance, facing: fp.Facing, turns: dict[tuple[str, str], fp.Footprint]
) -> fp.Footprint:
    key = (inst.footprint.key, facing)
    if key not in turns:
        turns[key] = inst.footprint.rotated(facing)
    return turns[key]


def _prior_spots(
    program: Program,
    prior: Mapping[str, Any],
    packer: _Packer,
    opts: Options,
    turns: dict[tuple[str, str], fp.Footprint],
) -> dict[int, _Spot]:
    """The placements of an earlier layout that still fit, by instance index."""
    groups: dict[tuple[str, str, str], list[int]] = {}
    for n, inst in enumerate(program.instances):
        base = inst.footprint.key.rsplit("|", 1)[0]
        groups.setdefault((inst.plan, inst.node, base), []).append(n)
    out: dict[int, _Spot] = {}
    old = [p for p in prior.get("placements", []) if isinstance(p, Mapping)]
    old.sort(key=lambda p: lay_lib._node_number(str(p.get("id", ""))))
    for p in old:
        try:
            base = str(p["footprint"]).rsplit("|", 1)[0]
            todo = groups.get((str(p["plan"]), str(p["node"]), base))
            if not todo:
                continue
            n = todo.pop(0)
            facing = fp.parse_facing(str(p["facing"]))
            body = lay_lib.box_of(p)
        except (KeyError, IndexError, TypeError, fp.FootprintError):
            continue
        turned = _turn(program.instances[n], facing, turns)
        if body.size != turned.size or body.lo[1] != 0:
            continue
        keep = lay_lib.keepout_of(body, opts.clearance, opts.clearance_top)
        if packer.fits(keep_size(turned, opts)[1], keep):
            packer.occupy(keep)
            out[n] = _Spot(facing, body, keep)
    return out


def _pack(
    program: Program,
    order: Sequence[int],
    packer: _Packer,
    placed: dict[int, _Spot],
    lobby: Sequence[Column],
    region: set[Column],
    opts: Options,
    turns: dict[tuple[str, str], fp.Footprint],
    *,
    flow: bool,
    corner: tuple[str, int, int] | None = None,
) -> list[int]:
    """Place the instances ``order`` into ``placed`` (one already there stays, and in the flow
    pass leads its plan's next machine); the ones that didn't fit. ``corner``: each goes
    nearest that corner of the site, not nearest the lobby (see :func:`_best`)."""
    cache: dict[tuple[Column, ...], dict[Column, int]] = {}

    def dist_from(starts: tuple[Column, ...]) -> dict[Column, int]:
        if starts not in cache:
            cache[starts] = _flood(region, starts)
        return cache[starts]

    last: dict[str, Column] = {}  # plan -> the front of its latest machine
    failed: list[int] = []
    for n in order:
        inst = program.instances[n]
        if n in placed:
            last[inst.plan] = _front(
                _turn(inst, placed[n].facing, turns), placed[n].body
            )
            continue
        src = (last[inst.plan],) if flow and inst.plan in last else tuple(lobby)
        spot = _best(
            inst,
            packer,
            {} if corner is not None else dist_from(src),
            opts,
            turns,
            corner,
        )
        if spot is None:
            failed.append(n)
            continue
        packer.occupy(spot.keep)
        placed[n] = spot
        last[inst.plan] = _front(_turn(inst, spot.facing, turns), spot.body)
    return failed


def fit(
    program: Program,
    cells: Iterable[Cell],
    site: Box,
    opts: Options | None = None,
    hint: Mapping[str, Any] | None = None,
    zones: Sequence[Mapping[str, Any]] = (),
    prior: Mapping[str, Any] | None = None,
) -> FitResult:
    """Fit ``program``'s machines into the building ``cells`` (solid positions) inside ``site``.

    ``hint`` is the answer's entrance (``{"side", "at": [x, z]}``), ``zones`` its optional
    steering boxes, ``prior`` an earlier layout whose placements stay where they still fit.
    """
    opts = opts or Options()
    if site.lo[1] != 0:
        raise lay_lib.LayoutError("a site's floor is y 0")
    cells = sorted(set(cells))
    want = need(program, opts)
    ch, chh = opts.corridor_height, opts.chase_height
    cap = want["cap_height"]
    stats: dict[str, Any] = {"need": want, "placed": 0, "of": len(program.instances)}

    def refuse(*why: str) -> FitResult:
        return FitResult(None, list(why), stats)

    if not program.instances:
        return refuse("no placements: none of the plans' machines has a footprint")
    machines, open_, bad = _zones(zones, site)
    if bad:
        return refuse(*bad)
    floor = derive(cells, site)
    tall = {c for c, v in floor.h.items() if v >= ch}
    doors, why = _door(floor, tall, opts, hint)
    if not doors:
        return refuse(*why)
    k, m = want["keepout_volume"], want["required"]

    def capacity(
        d: _Door,
    ) -> tuple[set[Column], dict[Column, int], set[Column], int, int]:
        joined = set(_flood(tall, d.lobby))
        usable = _usable(floor, sorted(joined), machines, open_)
        region = {c for c in joined if usable[c] >= 1}
        volume = sum(min(usable[c], cap) for c in region)
        return joined, usable, region, volume, sum(floor.h[c] for c in region)

    # --- capacity: the first door (in rank order) whose roofed floor holds NEED, else the
    # one that holds the most (a doorway into a porch or a canopy never hides the hall's)
    door, got = doors[0], capacity(doors[0])
    for d in doors[1:]:
        if got[3] >= m:
            break
        other = capacity(d)
        if other[3] > got[3]:
            door, got = d, other
    joined, usable, region, volume, raw = got
    stats["entrance_from"] = "auto" if hint is None else "answer"
    stats["door"] = {"side": door.side, "at": list(door.cols[0])}
    stats |= {"volume": volume, "volume_raw": raw, "floor": len(region)}
    if volume < m:
        msg = (
            f"the roofed floor holds {volume} usable blocks (air up to {cap} high under "
            f"your roof; {raw} counting all headroom); the machines need {m} (keep-outs "
            f"{k} + {opts.buffer}%): add at least {m - volume}"
        )
        if floor.blocked:
            x, z = floor.blocked[0]
            msg += (
                f". y 0 is solid in {len(floor.blocked)} columns under your roof, e.g. "
                f"[{x},0,{z}]: the machine floor is y 0; leave it air"
            )
        return refuse(msg)
    fa, fm = want["floor_area"], want["floor_required"]
    if len(region) < fm:
        return refuse(
            f"the roofed floor has {len(region)} columns at least {ch} high joined to the "
            f"door; the machines' keep-outs cover {fa} columns and need {fm} with "
            f"{opts.buffer}% more: add at least {fm - len(region)} columns of roofed floor"
        )

    # --- the tallest and widest machines must each have a spot
    turns: dict[tuple[str, str], fp.Footprint] = {}
    lobby = set(door.lobby)
    floor_cols = region - lobby
    sizes = {i.footprint.key: keep_size(i.footprint, opts) for i in program.instances}
    packer = _Packer(site, usable, floor_cols, [s[1] for s in sizes.values()], opts)
    seen: set[str] = set()
    short: list[str] = []
    for n, inst in enumerate(program.instances, 1):
        key = inst.footprint.key
        if key in seen:
            continue
        seen.add(key)
        kx, ky, kz = sizes[key]

        def room(t: int, kx: int = kx, kz: int = kz) -> bool:
            sat = packer.sat.get(t) or _Sat(site, packer.free(t))
            return any(
                sat.full(x, z, a, b)
                for a, b in ((kx, kz), (kz, kx))
                for z in range(site.lo[2], site.hi[2] + 1)
                for x in range(site.lo[0], site.hi[0] + 1)
            )

        if room(ky):
            continue
        if room(1):
            best = next(t for t in range(ky - 1, 0, -1) if room(t))
            short.append(
                f"m{n} {_name(inst)} needs a {kx}x{kz} floor at least {ky} high; the "
                f"tallest such floor is {best}"
            )
        else:
            _, _, w, ln = _largest(packer.free(1), site)
            short.append(
                f"m{n} {_name(inst)} needs {kx}x{kz} free floor in some facing; the widest "
                f"roofed run is {w}x{ln}"
            )
    if short:
        return refuse(*short)

    # --- packing: prior, then flow, else area
    every = list(range(len(program.instances)))
    placed = _prior_spots(program, prior, packer, opts, turns) if prior else {}
    kept = dict(placed)
    taken = set(packer.taken)
    todo = [n for n in every if n not in placed]
    passed = "prior"
    if todo:
        passed = "flow"
        failed = _pack(
            program, every, packer, placed, door.lobby, joined, opts, turns, flow=True
        )
        if failed:
            passed = "area"
            placed = dict(kept)
            packer.taken = set(taken)
            packer._rebuild()

            def area(n: int) -> tuple[int, int]:
                kx, _, kz = sizes[program.instances[n].footprint.key]
                return (-kx * kz, n)

            def area_pass(corner: tuple[str, int, int] | None) -> list[int]:
                return _pack(
                    program,
                    sorted(todo, key=area),
                    packer,
                    placed,
                    door.lobby,
                    joined,
                    opts,
                    turns,
                    flow=False,
                    corner=corner,
                )

            failed = area_pass(None)
            # nearest the lobby can split a hall in two; corner-first packs it from a corner
            if failed:
                first = (dict(placed), list(failed), set(packer.taken))
                for corner in _FALLBACKS:
                    placed = dict(kept)
                    packer.taken = set(taken)
                    packer._rebuild()
                    failed = area_pass(corner)
                    if not failed:
                        break
                else:  # none did: the refusal tells of the lobby-first try
                    placed, failed, packer.taken = first
                    packer._rebuild()
        if failed:
            stats["placed"] = len(placed)
            x, z, w, ln = _largest(packer.free(1), site)
            h = min(
                (usable[(x + i, z + j)] for i in range(w) for j in range(ln)), default=0
            )
            un = [sizes[program.instances[n].footprint.key] for n in failed]
            return refuse(
                f"placed {len(placed)} of {len(every)}: "
                + ", ".join(f"m{n + 1}" for n in failed)
                + " did not fit (keep-outs "
                + ", ".join(
                    f"m{n + 1} {a}x{c} floor {b} high"
                    for n, (a, b, c) in zip(failed, un, strict=True)
                )
                + f"); largest free floor left is {w}x{ln} (h {h}) at [{x},{z}]; widen a "
                f"hall or add one at least {max(s[0] for s in un)}x"
                f"{max(s[2] for s in un)}, {max(s[1] for s in un)} high, joined to the door"
            )
    stats["placed"] = len(placed)
    stats["pass"] = passed

    # --- walkways: the lobby (c1), aisles over the free floor, chases where high enough
    keep_cols = {
        (x, z)
        for s in placed.values()
        for x in range(s.keep.lo[0], s.keep.hi[0] + 1)
        for z in range(s.keep.lo[2], s.keep.hi[2] + 1)
    }
    walk = [sorted(lobby)] + [
        [(x, z) for x in range(lo[0], hi[0] + 1) for z in range(lo[1], hi[1] + 1)]
        for lo, hi in _rects(joined - keep_cols - lobby)
    ]
    corridors: list[dict[str, Any]] = []
    chases: list[dict[str, Any]] = []
    for i, cols in enumerate(walk):
        cid = f"c{i + 1}"
        lo = (min(c[0] for c in cols), 0, min(c[1] for c in cols))
        hi = (max(c[0] for c in cols), ch - 1, max(c[1] for c in cols))
        kind = "hall" if i == 0 else "aisle"
        corridors.append({"id": cid, "kind": kind, **Box(lo, hi).to_json()})
        high = {c for c in cols if floor.h[c] >= ch + chh}
        for (x0, z0), (x1, z1) in _rects(high):
            box = Box((x0, ch, z0), (x1, ch + chh - 1, z1))
            chases.append({"id": f"h{len(chases) + 1}", "over": cid, **box.to_json()})
    if not chases:
        return refuse(
            f"no walkway has a ceiling at least {ch + chh} high for the cable chase; "
            "raise part of the roof over open floor"
        )
    by_corridor = _nearest(joined, {c: i for i, cols in enumerate(walk) for c in cols})
    by_chase = _nearest(
        joined,
        {
            (x, z): i
            for i, h in enumerate(chases)
            for x in range(h["lo"][0], h["hi"][0] + 1)
            for z in range(h["lo"][2], h["hi"][2] + 1)
        },
    )
    first_chase = {}
    for h in chases:
        first_chase.setdefault(h["over"], h["id"])

    # --- the layout
    keepouts: list[dict[str, Any]] = []
    placements: list[dict[str, Any]] = []
    boxes: dict[str, Box] = {}
    for n in every:
        inst, spot = program.instances[n], placed[n]
        turned = _turn(inst, spot.facing, turns)
        pid, kid = f"m{n + 1}", f"k{n + 1}"
        front = _front(turned, spot.body)
        cid = corridors[by_corridor.get(front, 0)]["id"]
        hid = first_chase.get(cid) or chases[by_chase.get(front, 0)]["id"]
        keepouts.append({"id": kid, "of": pid, **spot.keep.to_json()})
        boxes[pid] = spot.body
        placements.append(
            lay_lib._placement(pid, kid, cid, hid, inst, turned, spot.body)
        )
    entrance = Box(
        (min(c[0] for c in door.cols), 0, min(c[1] for c in door.cols)),
        (max(c[0] for c in door.cols), ch - 1, max(c[1] for c in door.cols)),
    )
    entrances = [
        {"id": "e1", "side": door.side, "corridor": "c1", **entrance.to_json()}
    ]
    bodies = {cell for b in boxes.values() for cell in b.cells()}
    air = frozenset(
        (x, y, z)
        for x, z in sorted(joined)
        for y in range(min(floor.h[(x, z)], cap))
        if (x, y, z) not in bodies
    )
    lay_lib.reach_all(placements, keepouts, corridors, entrances, boxes, air)
    unreached = [
        f"{p['id']} {p['name'] or 'mte:' + str(p['mte'])}: {w}"
        for p in placements
        for w in lay_lib._reach_problems(p["reach"])
    ]
    if unreached:
        return refuse(*unreached)
    warnings = [
        f"{p['id']} {p['name'] or 'mte:' + str(p['mte'])}: no hatch data, "
        "hatches not checked"
        for p in placements
        if p["kind"] == "multiblock" and not p["hatches"]
    ]
    warnings += lay_lib._variant_warnings(placements, program.variants)
    warnings += [
        f"{u['plan']} {u['node']}: {u['count']}x mte:{u['mte']} not placed: no footprint"
        for u in program.unplaced
    ]
    warnings += program.service_warnings
    spare = (volume - m) * 100 // m if m else 0
    stats["spare_pct"] = spare
    body = {
        "plans": [dict(p) for p in program.plans],
        "gt": program.gt,
        "run": program.run,
        "axes": lay_lib.AXES,
        "options": lay_lib.options_json(opts),
        "bounds": {**site.to_json(), "size": list(site.size)},
        "placements": placements,
        "keepouts": keepouts,
        "corridors": corridors,
        "chases": chases,
        "entrances": entrances,
        "services": [dict(s) for s in program.services],
        "unplaced": [dict(u) for u in program.unplaced],
        "warnings": warnings,
        "fit": {
            "mode": "site",
            "site": site.to_json(),
            "building": _sha([list(c) for c in cells if site.contains(c)]),
            "entrance_from": stats["entrance_from"],
            "pass": passed,
            "need": {"keepout_volume": k, "required": m},
            "have": {"volume": volume, "volume_raw": raw, "floor": len(region)},
            "spare_pct": spare,
            "placed": len(placed),
            "of": len(every),
            "zones": _sha([dict(z) for z in zones]) if zones else None,
        },
    }
    out = {
        "schema": lay_lib.SCHEMA,
        "v": lay_lib.VERSION,
        "id": lay_lib._layout_id(body),
        **body,
    }
    return FitResult(out, [], stats)
