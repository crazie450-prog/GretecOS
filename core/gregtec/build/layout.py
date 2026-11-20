"""The line layout planner (WO-10.5, plan 7.10 "Shells" and "Placement", 7.1 step 9, D35, D312).

From one or more saved plans (a production plan and the power plan that feeds it, say), place
every machine footprint on a block grid with corridors, so each machine's front, hatches and
maintenance hatch can be reached from an entrance, and with chases for pipes and cables. The
result is a layout JSON (:data:`SCHEMA`, version :data:`VERSION`; ``layout_schema.json`` beside
this file) that WO-10.7's shell designer and WO-10.8's build guide read.

**Sizes.** A multiblock's footprint is :mod:`gregtec.build.footprints`'s (the structure table: the
extractor's rows or a Lab capture) at the controller's *largest* variant by volume (D312: a
keep-out must hold the machine as built, and which variant a recipe needs, a tower's height say,
isn't in the DB; a warning names every variant so the size can be checked), looked up by ``gregtech:gt.blockmachines`` + its MTE id or the ``machine`` row a structure row is linked
to; a single block is one block (a GT single block is one block by construction). A multiblock
with no footprint is not placed and is listed under ``unplaced`` (it needs a Lab capture, D306):
no size is ever estimated.

**Geometry** (D312). Axes: x east, y up, z south, in blocks; y 0 is the first layer above the
floor; every box is ``lo``/``hi`` inclusive.

- The *hall*: a corridor along z at the west end (x 0 to ``corridor_width`` - 1), with the one
  entrance in its west face.
- *Bands* east of the hall, stacked along z. Each band is a *spine* corridor along x with a row
  of machines on each side, every machine facing the spine (north row faces south, south row
  north), its keep-out flush with the spine. A row is at most ``max_run`` blocks long; past it
  a new band starts.
- A machine's *keep-out* is its footprint's bounding box grown by ``clearance`` blocks on every
  horizontal side and ``clearance_top`` above. Keep-outs never overlap one another, a corridor or
  a chase. The ring between the box and the keep-out is open service space: walk-around room
  for hatches on the sides and back, and room above for hatches on top.
- *Chases* run above every corridor (``corridor_height`` up, ``chase_height`` high): the trunk
  for pipes, conveyors and cables. A machine's ``chase`` is the one over its spine; its runs drop
  through its keep-out ring to its hatches. A machine lower than ``corridor_height`` has its
  keep-out end below the chase, so its runs drop through corridor headroom (WO-10.7 decides how).

**Reach.** Service space is the corridors plus every keep-out minus its machine's box. A flood
fill (6-connected) from the entrance's cells marks what can be reached. A machine is ``ok`` when
the cell in front of its controller is reached and every hatch kind its footprint takes
(``Maintenance`` included) has a reached cell: a hatch cell is reached when one of its faces
opens onto reached service space. Hatch cells with no face out of the box (a bottom-centre cell
under the hollow) are counted as ``enclosed``. Vertical movement inside service space (ladder,
scaffold, flight) is Clayton's; the shell designer may not fill it. A multiblock whose footprint
has no hatch cells is not checked for hatches, and a warning says so.

**Services** (logistics and power): every flow between two placed nodes (the plan's ``edges``,
items and fluids), the plan's boundary inputs and outputs at the entrance, and EU per tier from
the power plan's generators of that tier to the consumers, each with the plan it comes from.
A tier with no generator of its own is fed from the layout's generators of other tiers with
``via_transformer`` set and a warning (the transformer step is WO-4.5's, not placed), or from the
entrance, the base grid, when the layout holds no generator. A flow to or from a node that isn't
placed is not routed, and a warning names it. Numbers in them are the plan's, copied. The id
hashes the plans and the whole layout, so new footprint data gives a new id.

Deterministic: the same plans and options give the same JSON (no clock, no randomness; the order
is the plans', then each plan's flow from upstream to downstream, ties by node number). Nothing
here writes a world or a schematic; GOS never pastes machines (D35).
"""

from __future__ import annotations

import hashlib
import heapq
import json
import sqlite3
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from gregtec.build import coverage
from gregtec.build import footprints as fp
from gregtec.build.palette import GT_MACHINES
from gregtec.cards import checked
from gregtec.cards import schema as card_schema
from gregtec.planner.outputs.footprint import CLEARANCE_XZ, CLEARANCE_Y

SCHEMA = "gos-layout"
VERSION = 1
SINGLE = "single block"
MAINTENANCE = "Maintenance"
AXES = (
    "x east, y up, z south, in blocks; y 0 is the first layer above the floor; "
    "boxes are lo/hi inclusive"
)

type Cell = tuple[int, int, int]


class LayoutError(ValueError):
    """Options or plans a layout can't be made from (shown as is)."""


# Each option's (default, lowest, highest). Defaults: D312.
LIMITS: dict[str, tuple[int, int, int]] = {
    "corridor_width": (3, 1, 8),
    "corridor_height": (3, 2, 8),
    "clearance": (CLEARANCE_XZ, 1, 4),
    "clearance_top": (CLEARANCE_Y, 1, 4),
    "chase_height": (2, 1, 4),
    "max_run": (64, 8, 512),
}


@dataclass(frozen=True)
class Options:
    corridor_width: int = LIMITS["corridor_width"][0]
    corridor_height: int = LIMITS["corridor_height"][0]
    clearance: int = LIMITS["clearance"][0]
    clearance_top: int = LIMITS["clearance_top"][0]
    chase_height: int = LIMITS["chase_height"][0]
    max_run: int = LIMITS["max_run"][0]

    def __post_init__(self) -> None:
        for name, (_, lo, hi) in LIMITS.items():
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise LayoutError(f"{name} must be a whole number from {lo} to {hi}")

    @classmethod
    def parse(cls, items: Iterable[str]) -> Options:
        """``NAME=VALUE`` pairs (the CLI's ``--option``)."""
        out: dict[str, int] = {}
        for item in items:
            name, sep, value = item.partition("=")
            name = name.strip().replace("-", "_")
            if not sep or name not in LIMITS:
                raise LayoutError(
                    f"option {item!r} is not NAME=VALUE with NAME one of {', '.join(LIMITS)}"
                )
            try:
                out[name] = int(value)
            except ValueError:
                raise LayoutError(f"option {name} must be a whole number") from None
        return cls(**out)


@dataclass(frozen=True)
class Box:
    lo: Cell
    hi: Cell

    @property
    def size(self) -> Cell:
        return (
            self.hi[0] - self.lo[0] + 1,
            self.hi[1] - self.lo[1] + 1,
            self.hi[2] - self.lo[2] + 1,
        )

    def contains(self, c: Cell) -> bool:
        return all(self.lo[i] <= c[i] <= self.hi[i] for i in range(3))

    def overlaps(self, other: Box) -> bool:
        return all(
            self.lo[i] <= other.hi[i] and other.lo[i] <= self.hi[i] for i in range(3)
        )

    def cells(self) -> Iterator[Cell]:
        for x in range(self.lo[0], self.hi[0] + 1):
            for y in range(self.lo[1], self.hi[1] + 1):
                for z in range(self.lo[2], self.hi[2] + 1):
                    yield (x, y, z)

    def to_json(self) -> dict[str, list[int]]:
        return {"lo": list(self.lo), "hi": list(self.hi)}


def box_of(d: Mapping[str, Any]) -> Box:
    lo, hi = d["lo"], d["hi"]
    return Box((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]))


@dataclass(frozen=True)
class Instance:
    """One machine to place: a node's machine, one of its count."""

    plan: str
    node: str
    role: str  # machine (a recipe or farm node) or generator (a power plan's generator)
    mte: int
    kind: str  # multiblock, singleblock or steam
    name: str | None
    tier: str | None
    footprint: fp.Footprint  # facing north


@dataclass
class _Row:
    side: str  # north (faces south) or south (faces north)
    # (layout order, instance, x offset along the spine)
    items: list[tuple[int, Instance, int]] = field(default_factory=list)
    length: int = 0

    def depth(self, kz: Mapping[str, int]) -> int:
        return max((kz[i.footprint.key] for _, i, _ in self.items), default=0)


@dataclass
class _Band:
    north: _Row = field(default_factory=lambda: _Row("north"))
    south: _Row = field(default_factory=lambda: _Row("south"))


# --- what to place --------------------------------------------------------------------------


def _single(mte: int, name: str | None) -> fp.Footprint:
    return fp.Footprint(
        registry_name=GT_MACHINES,
        meta=mte,
        name=name,
        class_name=None,
        source=SINGLE,
        variant="",
        tiers=(),
        facing=fp.BUILT_FACING,
        cells=(fp.Cell((0, 0, 0), GT_MACHINES, mte),),
    )


class _Footprints:
    """MTE id -> its footprint facing north (or None), read once per layout."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.lib = fp.library(conn)
        self.linked = coverage._linked(conn, self.lib)
        self._cache: dict[tuple[int, str], fp.Footprint | None] = {}
        # controller (registry@meta) -> its variants and sizes, when it has more than one
        self.variants: dict[str, list[tuple[str, fp.Offset]]] = {}

    def get(self, mte: int, kind: str, name: str | None) -> fp.Footprint | None:
        key = (mte, kind)
        if key not in self._cache:
            self._cache[key] = self._find(mte, kind, name)
        return self._cache[key]

    def _find(self, mte: int, kind: str, name: str | None) -> fp.Footprint | None:
        if kind != "multiblock":
            return _single(mte, name)
        found = self.lib.for_mte(mte) or self.linked.get(mte, [])
        if not found:
            return None
        f = found[0]
        every = self.lib.for_controller(f.registry_name, f.meta)
        # the largest variant by volume (D312 "Sizes"): a keep-out must hold the machine as
        # built, and which variant a recipe needs (a tower's height) isn't in the DB
        pick = max(every, key=lambda v: (v.size[0] * v.size[1] * v.size[2], v.variant))
        try:
            built = self.lib.get(f.registry_name, f.meta, variant=pick.variant)
        except fp.FootprintError:
            return None
        if len(every) > 1:
            self.variants[built.controller] = [(v.variant, v.size) for v in every]
        return built


def _flow_order(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The plan's nodes from upstream to downstream (Kahn's order, ties by node number; nodes
    left in a loop follow by number)."""
    nodes = [n for n in plan.get("nodes", []) if isinstance(n, Mapping) and "n" in n]
    num = {str(n["n"]): _node_number(str(n["n"])) for n in nodes}
    by = {str(n["n"]): n for n in nodes}
    preds: dict[str, set[str]] = {k: set() for k in by}
    succs: dict[str, set[str]] = {k: set() for k in by}
    for e in plan.get("edges", []):
        a, b = str(e.get("from")), str(e.get("to"))
        if a in by and b in by and a != b:
            preds[b].add(a)
            succs[a].add(b)
    ready = [(num[k], k) for k, p in preds.items() if not p]
    heapq.heapify(ready)
    out: list[str] = []
    seen: set[str] = set()
    while ready:
        _, k = heapq.heappop(ready)
        out.append(k)
        seen.add(k)
        for s in sorted(succs[k], key=num.__getitem__):
            preds[s].discard(k)
            if not preds[s] and s not in seen:
                heapq.heappush(ready, (num[s], s))
    out += sorted((k for k in by if k not in seen), key=num.__getitem__)
    return [by[k] for k in out]


def _node_number(n: str) -> int:
    return int(n[1:]) if n[1:].isdigit() else 0


def _machines_of(
    plan: Mapping[str, Any], node: Mapping[str, Any]
) -> list[tuple[int, str, str | None, str | None, int, str]]:
    """(mte, kind, name, tier, count, role) of each machine the node needs."""
    m = node.get("machine")
    tier = m.get("tier") if isinstance(m, Mapping) else None
    if node.get("kind") == "generator" and node.get("machines"):
        kinds = {
            int(u["machine"]["mte"]): str(u["machine"].get("kind") or "multiblock")
            for u in plan.get("machines_used", [])
            if isinstance(u.get("machine"), Mapping)
            and u["machine"].get("mte") is not None
        }
        out = []
        for g in node["machines"]:
            if g.get("mte") is None or not g.get("count"):
                continue
            mte = int(g["mte"])
            kind = kinds.get(
                mte,
                str(m.get("kind"))
                if isinstance(m, Mapping) and m.get("mte") == mte
                else "multiblock",
            )
            out.append((mte, kind, g.get("name"), tier, int(g["count"]), "generator"))
        return out
    if not isinstance(m, Mapping) or m.get("mte") is None or not node.get("count"):
        return []
    return [
        (
            int(m["mte"]),
            str(m.get("kind") or "multiblock"),
            m.get("name"),
            tier,
            int(node["count"]),
            "machine",
        )
    ]


def instances(
    plans: Sequence[Mapping[str, Any]], footprints: _Footprints
) -> tuple[list[Instance], list[dict[str, Any]]]:
    """Every machine to place, in layout order, and the nodes whose machine has no footprint."""
    out: list[Instance] = []
    unplaced: list[dict[str, Any]] = []
    for p in plans:
        pid = str(p.get("id", "?"))
        for node in _flow_order(p):
            for mte, kind, name, tier, count, role in _machines_of(p, node):
                f = footprints.get(mte, kind, name)
                if f is None:
                    unplaced.append(
                        {
                            "plan": pid,
                            "node": str(node["n"]),
                            "mte": mte,
                            "name": name,
                            "count": count,
                            "why": f"mte:{mte} has no footprint: it needs a Lab capture "
                            "(gos footprints coverage)",
                        }
                    )
                    continue
                out.extend(
                    Instance(pid, str(node["n"]), role, mte, kind, name, tier, f)
                    for _ in range(count)
                )
    return out, unplaced


# --- packing --------------------------------------------------------------------------------


def _pack(
    items: Sequence[Instance], kx: Mapping[str, int], opts: Options
) -> list[_Band]:
    """Rows along each spine: each machine to the shorter row of the band (north on a tie);
    past ``max_run`` to the other row, else a new band."""
    bands = [_Band()]
    for idx, inst in enumerate(items):
        w = kx[inst.footprint.key]
        band = bands[-1]
        rows = sorted((band.north, band.south), key=lambda r: r.length)
        row = next(
            (r for r in rows if not r.items or r.length + w <= opts.max_run), None
        )
        if row is None:
            band = _Band()
            bands.append(band)
            row = band.north
        row.items.append((idx, inst, row.length))
        row.length += w
    return bands


# --- the layout -----------------------------------------------------------------------------


def _layout_id(body: Mapping[str, Any]) -> str:
    """``lo-`` + 12 hex of the plans and the whole layout (options and every placed
    footprint's geometry), so a new capture or dump that moves a box gives a new id."""
    text = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return "lo-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def plan_layout(
    conn: sqlite3.Connection,
    plans: Sequence[Mapping[str, Any]],
    options: Options | None = None,
) -> dict[str, Any]:
    """The layout of ``plans`` (in order) as its JSON. Reads the DB; writes nothing."""
    if not plans:
        raise LayoutError("a layout needs at least one plan")
    opts = options or Options()
    ids = [str(p.get("id", "?")) for p in plans]
    if len(set(ids)) != len(ids):
        raise LayoutError("a plan is named twice")
    footprints = _Footprints(conn)
    items, unplaced = instances(plans, footprints)
    c, ct, W, H = (
        opts.clearance,
        opts.clearance_top,
        opts.corridor_width,
        opts.corridor_height,
    )
    kx: dict[str, int] = {}
    kz: dict[str, int] = {}
    for inst in items:
        sx, _, sz = inst.footprint.size  # north and south facings keep x and z
        kx[inst.footprint.key] = sx + 2 * c
        kz[inst.footprint.key] = sz + 2 * c
    bands = _pack(items, kx, opts)

    corridors: list[dict[str, Any]] = []
    chases: list[dict[str, Any]] = []
    keepouts: list[dict[str, Any]] = []
    placements: list[dict[str, Any]] = []
    boxes: dict[str, Box] = {}  # placement id -> its machine's box
    spines: list[tuple[int, str, str]] = []  # (spine z0, corridor id, chase id)
    slots: dict[int, tuple[Instance, fp.Footprint, Box, Box, str, str]] = {}
    z = 0
    for band in bands:
        z0 = z + band.north.depth(kz)
        length = max(band.north.length, band.south.length)
        cid, hid = f"c{len(corridors) + 2}", f"h{len(chases) + 2}"
        if length:
            spine = Box((W, 0, z0), (W + length - 1, H - 1, z0 + W - 1))
            corridors.append({"id": cid, "kind": "spine", **spine.to_json()})
            chases.append(_chase(hid, cid, spine, opts))
        spines.append((z0, cid, hid))
        for row in (band.north, band.south):
            for idx, inst, xo in row.items:
                facing: fp.Facing = "south" if row.side == "north" else "north"
                turned = inst.footprint.rotated(facing)
                sx, sy, sz = turned.size
                x0 = W + xo
                kz_lo = z0 - (sz + 2 * c) if row.side == "north" else z0 + W
                keep = Box(
                    (x0, 0, kz_lo),
                    (x0 + sx + 2 * c - 1, sy - 1 + ct, kz_lo + sz + 2 * c - 1),
                )
                body = Box(
                    (x0 + c, 0, kz_lo + c),
                    (x0 + c + sx - 1, sy - 1, kz_lo + c + sz - 1),
                )
                slots[idx] = (inst, turned, keep, body, cid, hid)
        z = z0 + W + band.south.depth(kz)
    for idx in sorted(slots):  # ids in layout order: m1 is the most upstream machine
        inst, turned, keep, body, cid, hid = slots[idx]
        pid, kid = f"m{len(placements) + 1}", f"k{len(keepouts) + 1}"
        keepouts.append({"id": kid, "of": pid, **keep.to_json()})
        boxes[pid] = body
        placements.append(_placement(pid, kid, cid, hid, inst, turned, body))

    first, last = spines[0][0], spines[-1][0]
    hall = Box((0, 0, first), (W - 1, H - 1, last + W - 1))
    corridors.insert(0, {"id": "c1", "kind": "hall", **hall.to_json()})
    chases.insert(0, _chase("h1", "c1", hall, opts))
    entrance = Box((0, 0, first), (0, H - 1, first + W - 1))
    entrances = [{"id": "e1", "side": "west", "corridor": "c1", **entrance.to_json()}]

    every = [box_of(v) for v in (*corridors, *chases, *keepouts)]
    hi = tuple(max(b.hi[i] for b in every) for i in range(3))
    bounds = Box((0, 0, 0), (hi[0], hi[1], hi[2]))

    reach_all(placements, keepouts, corridors, entrances, boxes)
    warnings = [
        f"{p['id']} {p['name'] or 'mte:' + str(p['mte'])}: {why}"
        for p in placements
        for why in _reach_problems(p["reach"])
    ]
    warnings += [
        f"{p['id']} {p['name'] or 'mte:' + str(p['mte'])}: no hatch data, "
        "hatches not checked"
        for p in placements
        if p["kind"] == "multiblock" and not p["hatches"]
    ]
    warnings += _variant_warnings(placements, footprints.variants)
    warnings += [
        f"{u['plan']} {u['node']}: {u['count']}x mte:{u['mte']} not placed: no footprint"
        for u in unplaced
    ]
    flows, flow_warnings = services(plans, placements)
    warnings += flow_warnings
    body = {
        "plans": [
            {"id": pid, "name": p.get("name"), "kind": p.get("kind")}
            for pid, p in zip(ids, plans, strict=True)
        ],
        "gt": next((p.get("gt") for p in plans if p.get("gt")), None),
        "run": next((p.get("run") for p in plans if p.get("run") is not None), None),
        "axes": AXES,
        "options": asdict(opts),
        "bounds": {**bounds.to_json(), "size": list(bounds.size)},
        "placements": placements,
        "keepouts": keepouts,
        "corridors": corridors,
        "chases": chases,
        "entrances": entrances,
        "services": flows,
        "unplaced": unplaced,
        "warnings": warnings,
    }
    lid = _layout_id({"plans": list(plans), "layout": body})
    return {"schema": SCHEMA, "v": VERSION, "id": lid, **body}


def _variant_warnings(
    placements: Sequence[Mapping[str, Any]],
    variants: Mapping[str, Sequence[tuple[str, fp.Offset]]],
) -> list[str]:
    """One warning per machine type placed at its largest of several variants (D312)."""
    out: list[str] = []
    seen: set[str] = set()
    for p in placements:
        key = p["footprint"].split("|", 1)[0]
        if key not in variants or key in seen:
            continue
        seen.add(key)
        sizes = ", ".join(
            f"{name or '(default)'} {x}x{y}x{z}" for name, (x, y, z) in variants[key]
        )
        ids = [q["id"] for q in placements if q["footprint"].split("|", 1)[0] == key]
        out.append(
            f"{p['name'] or 'mte:' + str(p['mte'])} ({', '.join(ids)}): placed at its "
            f"largest variant {p['variant'] or '(default)'} of {len(variants[key])} ({sizes}); "
            "the variant its recipe needs isn't in the DB, so check the size before building"
        )
    return out


def _chase(hid: str, cid: str, under: Box, opts: Options) -> dict[str, Any]:
    y0 = opts.corridor_height
    box = Box(
        (under.lo[0], y0, under.lo[2]),
        (under.hi[0], y0 + opts.chase_height - 1, under.hi[2]),
    )
    return {"id": hid, "over": cid, **box.to_json()}


def _placement(
    pid: str,
    kid: str,
    cid: str,
    hid: str,
    inst: Instance,
    turned: fp.Footprint,
    body: Box,
) -> dict[str, Any]:
    # the controller's cell: the box's corner less the footprint's lowest offset
    ox, oy, oz = (body.lo[i] - turned.lo[i] for i in range(3))
    front = fp.FRONT[turned.facing]
    return {
        "id": pid,
        "plan": inst.plan,
        "node": inst.node,
        "role": inst.role,
        "mte": inst.mte,
        "name": inst.name or turned.name,
        "kind": inst.kind,
        "tier": inst.tier,
        "footprint": turned.key,
        "variant": turned.variant,
        "source": turned.source,
        "facing": turned.facing,
        "controller": [ox, oy, oz],
        "front": [ox + front[0], oy + front[1], oz + front[2]],
        **body.to_json(),
        "keepout": kid,
        "corridor": cid,
        "chase": hid,
        "hatches": [
            {
                "p": [ox + h.d[0], oy + h.d[1], oz + h.d[2]],
                "kinds": list(h.hatches),
            }
            for h in turned.hatch_cells
        ],
        "reach": {},
    }


# --- reach ----------------------------------------------------------------------------------

_STEPS: tuple[Cell, ...] = (
    (1, 0, 0),
    (-1, 0, 0),
    (0, 1, 0),
    (0, -1, 0),
    (0, 0, 1),
    (0, 0, -1),
)


def _add(a: Cell, b: Cell) -> Cell:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def service_cells(
    keepouts: Iterable[Mapping[str, Any]],
    corridors: Iterable[Mapping[str, Any]],
    boxes: Mapping[str, Box],
) -> set[Cell]:
    """Corridors plus every keep-out minus its machine's box: the open space to move in."""
    out: set[Cell] = set()
    for c in corridors:
        out.update(box_of(c).cells())
    for k in keepouts:
        body = boxes[k["of"]]
        out.update(cell for cell in box_of(k).cells() if not body.contains(cell))
    return out


def flood(open_cells: set[Cell], starts: Iterable[Cell]) -> set[Cell]:
    """The cells of ``open_cells`` 6-connected to one of ``starts``."""
    seen = {s for s in starts if s in open_cells}
    todo = deque(sorted(seen))
    while todo:
        cur = todo.popleft()
        for step in _STEPS:
            nxt = _add(cur, step)
            if nxt in open_cells and nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def reach_all(
    placements: list[dict[str, Any]],
    keepouts: Sequence[Mapping[str, Any]],
    corridors: Sequence[Mapping[str, Any]],
    entrances: Sequence[Mapping[str, Any]],
    boxes: Mapping[str, Box],
) -> None:
    """Fill each placement's ``reach`` (and each hatch's ``reached``) by flood fill."""
    reached = flood(
        service_cells(keepouts, corridors, boxes),
        (cell for e in entrances for cell in box_of(e).cells()),
    )
    for p in placements:
        body = boxes[p["id"]]
        front = tuple(p["front"])
        kinds: set[str] = set()
        got: set[str] = set()
        enclosed = done = 0
        for h in p["hatches"]:
            cell: Cell = (h["p"][0], h["p"][1], h["p"][2])
            faces = [
                n
                for n in (_add(cell, s) for s in _STEPS)
                if not body.contains(n) and n[1] >= 0
            ]
            h["reached"] = any(n in reached for n in faces)
            kinds.update(h["kinds"])
            if not faces:
                enclosed += 1
            if h["reached"]:
                done += 1
                got.update(h["kinds"])
        front_ok = front in reached
        missing = sorted(kinds - got)
        p["reach"] = {
            "ok": front_ok and not missing,
            "front": front_ok,
            "hatch_cells": len(p["hatches"]),
            "reached": done,
            "enclosed": enclosed,
            "kinds": sorted(kinds),
            "unreached_kinds": missing,
            "maintenance": (MAINTENANCE in got) if MAINTENANCE in kinds else None,
        }


def _reach_problems(r: Mapping[str, Any]) -> list[str]:
    out = []
    if not r["front"]:
        out.append(
            "the controller's front can't be reached (its own structure is in the way)"
        )
    if r["unreached_kinds"]:
        out.append(
            "no reachable cell for hatch kind(s) " + ", ".join(r["unreached_kinds"])
        )
    return out


# --- services: logistics and power ----------------------------------------------------------


def _ids_by_node(
    placements: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {}
    for p in placements:
        out.setdefault((p["plan"], p["node"]), []).append(p["id"])
    return out


def _kind(item: str) -> str:
    return "fluid" if item.startswith("fluid:") else "item"


def services(
    plans: Sequence[Mapping[str, Any]], placements: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """What runs in the chases: each plan's flows between placed nodes, its boundary at the
    entrance, and EU per tier. Rates are the plan's. Also the warnings: a flow to or from a
    node that isn't placed (not routed), and a tier fed from a generator of another tier."""
    by_node = _ids_by_node(placements)
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    generators: dict[str, list[str]] = {}  # tier -> generator placement ids
    for p in plans:
        pid = str(p.get("id", "?"))
        gen = (
            p.get("power", {}).get("generator")
            if isinstance(p.get("power"), Mapping)
            else None
        )
        if isinstance(gen, Mapping) and gen.get("tier"):
            ids = [
                x["id"]
                for x in placements
                if x["plan"] == pid and x["role"] == "generator"
            ]
            if ids:
                generators.setdefault(str(gen["tier"]).upper(), []).extend(ids)
    for p in plans:
        pid = str(p.get("id", "?"))
        ref = f"plan:{pid}"
        for e in p.get("edges", []):
            a = by_node.get((pid, str(e.get("from"))), [])
            b = by_node.get((pid, str(e.get("to"))), [])
            if not a or not b:
                gone = [str(e.get(k)) for k, ids in (("from", a), ("to", b)) if not ids]
                warnings.append(
                    f"{pid} {e.get('from')} -> {e.get('to')} {e.get('item')}: not routed, "
                    f"{' and '.join(gone)} has no placement"
                )
                continue
            item = str(e.get("item"))
            out.append(
                {
                    "kind": _kind(item),
                    "item": item,
                    "rate_s": e.get("rate_s"),
                    "from": a,
                    "to": b,
                    "ref": ref,
                }
            )
        boundary = p.get("boundary") if isinstance(p.get("boundary"), Mapping) else {}
        for side, key in (("in", "from"), ("out", "to")):
            for port in boundary.get(side, []):
                item = str(port.get("id"))
                out.append(
                    {
                        "kind": _kind(item),
                        "item": item,
                        "rate_s": port.get("rate_s"),
                        "from": ["e1"] if key == "from" else [],
                        "to": ["e1"] if key == "to" else [],
                        "ref": ref,
                    }
                )
        tiers: dict[str, list[str]] = {}
        for x in placements:
            if x["plan"] == pid and x["role"] == "machine" and x["tier"]:
                node = next(
                    (n for n in p.get("nodes", []) if str(n.get("n")) == x["node"]), {}
                )
                if (node.get("eut") or 0) > 0 or (node.get("eut_avg") or 0) > 0:
                    tiers.setdefault(str(x["tier"]).upper(), []).append(x["id"])
        for tier in sorted(tiers):
            # the base grid (e1) when the layout holds no generator; one of another tier
            # feeds this one through a transformer step (WO-4.5's, not placed here)
            other = bool(generators) and tier not in generators
            source = (
                [i for t in sorted(generators) for i in generators[t]]
                if other
                else generators.get(tier, ["e1"])
            )
            out.append(
                {
                    "kind": "eu",
                    "item": f"eu:{tier}",
                    "rate_s": None,
                    "from": source,
                    "to": tiers[tier],
                    "via_transformer": other,
                    "ref": ref,
                }
            )
            if other:
                at = ", ".join(
                    f"{t} generator(s) {' '.join(generators[t])}"
                    for t in sorted(generators)
                )
                # the amps the plan's own EU metrics say this tier draws (a planner number)
                eu = (p.get("metrics") or {}).get("eu") or ()
                amps = next(
                    (
                        int(e.get("amps") or 0)
                        for e in eu
                        if isinstance(e, Mapping)
                        and str(e.get("tier", "")).upper() == tier
                    ),
                    0,
                )
                amp = f"--amps {amps}" if amps > 0 else "--amps <A>"
                cmds = "; ".join(
                    f"gos dist transformer --from {t} --to {tier} {amp}"
                    for t in sorted(generators)
                )
                if amps <= 0:
                    cmds += f"; the plan has no EU metrics for {tier}: its amps from gos dist ledger"
                warnings.append(
                    f"{pid} eu:{tier}: no {tier} generator in the layout, fed from {at}: "
                    f"a transformer step ({cmds})"
                )
    return out, warnings


# --- the dashboard's preview card -----------------------------------------------------------

MAX_RECTS = 1500  # the layout card's limit (core/gregtec/cards/schema.json)
MAX_NOTES = 20


def clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters, an ellipsis marking the cut."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tier_name(text: str | None) -> str | None:
    names = {t.upper(): t for t in card_schema()["$defs"]["Tier"]["enum"]}
    return names.get((text or "").upper())


def _rect(
    rid: str,
    kind: str,
    b: Box,
    label: str | None = None,
    tier: str | None = None,
    ref: str | None = None,
    facing: str | None = None,
    ok: bool | None = None,
) -> dict[str, Any]:
    w, _, length = b.size
    return {
        "id": rid,
        "kind": kind,
        "x": b.lo[0],
        "z": b.lo[2],
        "w": w,
        "l": length,
        "label": clip(label, 300) if label else None,
        "tier": _tier_name(tier),
        "ref": ref,
        "facing": facing,
        "ok": ok,
    }


def card(lay: Mapping[str, Any]) -> dict[str, Any]:
    """The layout's top-down preview as a catalog ``layout`` card (D313), checked."""
    rects = [_rect(c["id"], "corridor", box_of(c)) for c in lay["corridors"]]
    rects += [_rect(h["id"], "chase", box_of(h)) for h in lay["chases"]]
    rects += [_rect(e["id"], "entrance", box_of(e)) for e in lay["entrances"]]
    rects += [
        _rect(
            p["id"],
            "machine",
            box_of(p),
            p["name"] or f"mte:{p['mte']}",
            p["tier"],
            f"mte:{p['mte']}",
            p["facing"],
            p["reach"]["ok"],
        )
        for p in lay["placements"]
    ]
    # keep-outs last: past the card's limit they are what is left out
    rects += [_rect(k["id"], "keepout", box_of(k)) for k in lay["keepouts"]]
    notes: list[str] = []
    if len(rects) > MAX_RECTS:
        notes.append(
            f"{len(rects) - MAX_RECTS} boxes past the card's {MAX_RECTS} not drawn"
        )
        rects = rects[:MAX_RECTS]
    placed = lay["placements"]
    ok = sum(1 for p in placed if p["reach"]["ok"])
    sx, sy, sz = lay["bounds"]["size"]
    notes.append(
        f"{len(placed)} machines placed, {ok} with the front and every hatch kind reached "
        f"from the entrance; bounding volume {sx} x {sz} blocks, {sy} high"
    )
    if lay["unplaced"]:
        missing = ", ".join(f"{u['count']}x mte:{u['mte']}" for u in lay["unplaced"])
        notes.append(f"not placed (no footprint, needs a Lab capture): {missing}")
    notes += [w for w in lay["warnings"] if "not placed" not in w]
    title = " + ".join(str(p.get("name") or p["id"]) for p in lay["plans"])
    return checked(
        {
            "type": "layout",
            "title": clip(f"Floor plan · {title}", 300),
            "ref": f"plan:{lay['plans'][0]['id']}",
            "layout": lay["id"],
            "size": {"x": sx, "y": sy, "z": sz},
            "rects": rects,
            "notes": [clip(n, 2000) for n in notes[:MAX_NOTES]],
        }
    )
