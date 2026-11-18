"""The shell designer (WO-10.7; plan 7.10 "Shells", "Shell designer (img2schem merged, D74)" and
"Placement"; D33-D36, D73, D74, D316-D318).

A **shell** is the decorative building around a line: walls, roof, windows, lights. It holds
plain blocks only; machinery is built by Clayton from the 3D guide and **GOS never pastes
machines** (D35).

**Input** (:class:`Spec`, from a WO-10.5 layout JSON with :func:`spec_from_layout`): the
bounding volume (the layout's bounds grown by :data:`MARGIN` blocks on every horizontal side for
the walls, and ``headroom`` blocks above the layout's top for the roof); the volumes the shell
must leave empty (every keep-out, which holds its machine's footprint and service ring, every
corridor, every pipe/cable chase, and a doorway through the wall ring in front of each
entrance); the theme (WO-10.6) and its palette, plus any extra swatches (palette constraints:
prompt-only builds bring their own); Clayton's prompt; the line type; and ``max_blocks``.

**The building vocabulary** (D316). The ``architect`` model never writes blocks or code: it sends
a short list of operations (:data:`OPS`) in the layout's coordinates (x east, y up, z south; y 0
the first layer above the floor), each naming a palette *role* (or one ``block`` state), which
code turns into cells (:func:`compile_ops`): ``walls`` (the outer ring between two heights),
``pillars``, ``windows`` (openings of another role along the ring), ``roof`` (flat or gable, with
an edge and a parapet), ``lights`` (a grid set into the roof), ``fill``, ``frame`` and ``clear``.

**Validation** (:func:`inspect`, :func:`design`, D317): every design, the model's or the
procedural generator's, goes through the same gate. A design is refused (nothing written) when
an operation is malformed or names a role the palette doesn't have, a cell uses a state outside
the palette (a tile-entity block, ``gregtech:gt.blockmachines``, a meta above 15 or a state not in
the block catalog is never in it: the palette passed :func:`gregtec.build.palette.check` first),
it has more than ``max_blocks`` blocks, or it has none. Cells outside the bounding volume or
inside a keep-out, corridor, chase or doorway are **repaired** by removing them (they are
decoration lost, never a block placed where it mustn't be), unless they are more than
:data:`REPAIR_SHARE` of the design: then it is refused as too far off to repair. A repaired design
is inspected again and must be clean. The palette rule runs once more on the states the shell
finally uses, and again against the Lab world's registry when the ``.schematic`` is written
(:func:`gregtec.build.schematic.write_shell`).

**The procedural generator** (:func:`procedural`) writes the vocabulary from the theme's rules:
walls with corner and bay pillars, a plinth and an eaves band, windows (a high strip, tall bays
or a mid band, from the theme's window rules), a flat or gable roof with its edge, parapet and a
grid of lights. It is the fallback when the model's design is refused or no model is available
(No-AI mode, the CLI and the dashboard's button), and the test baseline: it is always clean.

**Output** (:func:`outputs`): the cells as a :class:`~gregtec.build.schematic.Schematic` (shell
min corner at index 0; WEOffset is that corner relative to the layout's origin, where Clayton
stands to paste), the palette manifest (role, block, name, count), the preview mesh
(:func:`mesh`: the exposed faces, merged into rectangles, for the dashboard) and the notes,
including the paste steps (Lab only: :data:`PASTE_STEPS`).

Deterministic: the same layout, palette and operations give the same cells, id and files.
Nothing here writes a world; :mod:`gregtec.build.shell_store` writes the build's files.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from gregtec.build import layout as layout_lib
from gregtec.build import palette, schematic
from gregtec.build import themes as theme_lib
from gregtec.build.palette import Entry

SCHEMA = "gos-shell"
VERSION = 1
MARGIN = 1  # the wall ring sits one block outside the layout's bounds (D316)
REPAIR_SHARE = (
    0.25  # a design breaching more than this share of its cells is refused (D317)
)
MAX_OPS = 200
MAX_BLOCKS = 200_000
LIMITS: dict[str, tuple[int, int, int]] = {  # (default, lowest, highest)
    "max_blocks": (20_000, 1, MAX_BLOCKS),
    "headroom": (4, 1, 16),
}
OPS = ("walls", "pillars", "windows", "roof", "lights", "fill", "frame", "clear")
SIDES = ("north", "south", "east", "west")
STATUSES = ("accepted", "repaired")
SOURCES = ("architect", "procedural")
BUILD_ID_PREFIX = "sh-"
PASTE_STEPS = (
    (
        "Lab only: never paste a shell in Play. Machines are built by you from the 3D guide; GOS "
        "never pastes machines."
    ),
    "Copy the .schematic file(s) into the Lab instance's config/worldedit/schematics/.",
    (
        "In the Lab world, stand on the layout's origin: the floor-level block at the north-west "
        "corner of the hall corridor (layout x 0, y 0, z 0). The layout's north is the world's "
        "north: the shell is pre-rotated, so never //rotate it."
    ),
    (
        "Run //schem load <name> then //paste (with several parts, load and paste each one from "
        "the same block)."
    ),
    (
        "Check: //copy the pasted shell from the same block and `gos schematic compare` it with the "
        "file; then walk the doorway and every corridor."
    ),
)

type Cell = tuple[int, int, int]
Box = layout_lib.Box


class ShellError(ValueError):
    """A spec or design that can't be used (shown as is; nothing was written)."""


@dataclass(frozen=True)
class Swatch:
    role: str
    entry: Entry
    note: str = ""


@dataclass(frozen=True)
class Volume:
    """A volume the shell must leave empty: a keep-out, corridor, chase or doorway."""

    id: str
    kind: str  # keepout, corridor, chase or doorway
    box: Box


@dataclass(frozen=True)
class Spec:
    layout: str  # the layout's id (lo-...)
    plans: tuple[str, ...]
    bounds: Box  # the shell's bounding volume, in layout coordinates
    interior: Box  # the layout's own bounds
    clear: tuple[Volume, ...]
    swatches: tuple[Swatch, ...]  # the palette, every entry checked against the catalog
    theme: str | None = None  # th-<name>@v<N>
    theme_name: str | None = None
    prompt: str = ""
    line_type: str = theme_lib.GENERAL
    max_blocks: int = LIMITS["max_blocks"][0]
    headroom: int = LIMITS["headroom"][0]
    theme_rules: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def wall_top(self) -> int:
        """The highest wall course: the layout's top."""
        return self.interior.hi[1]

    @property
    def roof_y(self) -> int:
        return self.interior.hi[1] + 1

    def roles(self) -> dict[str, list[Entry]]:
        out: dict[str, list[Entry]] = {}
        for s in self.swatches:
            out.setdefault(s.role, [])
            if s.entry not in out[s.role]:
                out[s.role].append(s.entry)
        return out

    def allowed(self) -> set[Entry]:
        return {s.entry for s in self.swatches}

    def doorways(self) -> list[Volume]:
        return [v for v in self.clear if v.kind == "doorway"]


def _int(v: object, what: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ShellError(f"{what} must be a whole number from {lo} to {hi}")
    return v


def options(
    max_blocks: int | None = None, headroom: int | None = None
) -> tuple[int, int]:
    mb = LIMITS["max_blocks"][0] if max_blocks is None else max_blocks
    hr = LIMITS["headroom"][0] if headroom is None else headroom
    _, lo, hi = LIMITS["max_blocks"]
    _int(mb, "max_blocks", lo, hi)
    _, lo, hi = LIMITS["headroom"]
    _int(hr, "headroom", lo, hi)
    return mb, hr


# --- the palette ---------------------------------------------------------------------------


def swatches_of(
    theme: theme_lib.Theme | None,
    extra: Iterable[tuple[str, str]] = (),
    exclude: Iterable[str] = (),
) -> list[Swatch]:
    """The build's palette: the theme's swatches, then ``extra`` (role, ``registry@meta``), less
    every state in ``exclude``. Roles are the theme roles (:data:`gregtec.build.themes.ROLES`)."""
    rows: list[Swatch] = []
    if theme is not None:
        rows += [
            Swatch(s.role, palette.parse_entry(s.block), s.note) for s in theme.palette
        ]
    for role, block in extra:
        if role not in theme_lib.ROLES:
            raise ShellError(
                f"role {role!r} is not one of {', '.join(theme_lib.ROLES)}"
            )
        try:
            rows.append(Swatch(role, palette.parse_entry(block)))
        except palette.PaletteError as exc:
            raise ShellError(str(exc)) from None
    out_keys: set[str] = set()
    for text in exclude:
        try:
            out_keys.add(palette.parse_entry(text).key)
        except palette.PaletteError as exc:
            raise ShellError(str(exc)) from None
    seen: set[tuple[str, Entry]] = set()
    kept = []
    for s in rows:
        if s.entry.key in out_keys or (s.role, s.entry) in seen:
            continue
        seen.add((s.role, s.entry))
        kept.append(s)
    return kept


def check_palette(
    conn: sqlite3.Connection, swatches: Sequence[Swatch]
) -> palette.Report:
    """Plan 7.10's palette rule on the catalog alone (the world is checked at export). Raises
    :class:`ShellError` naming every refused entry, or when a required role is missing."""
    if not swatches:
        raise ShellError("the palette is empty: name a theme, or palette entries")
    report = palette.check(conn, [s.entry for s in swatches], None, max_types=None)
    bad = [
        f"{v.entry.key}: {'; '.join(v.refused)}" for v in report.verdicts if v.refused
    ]
    if bad:
        raise ShellError(
            "palette refused (plan 7.10's palette rule): " + " | ".join(bad)
        )
    roles = {s.role for s in swatches}
    missing = [r for r in ("wall", "roof") if r not in roles]
    if missing:
        raise ShellError(f"the palette has no {' or '.join(missing)} entry")
    return report


# --- the spec from a layout ----------------------------------------------------------------


def _box(d: Mapping[str, Any]) -> Box:
    try:
        return layout_lib.box_of(d)
    except (KeyError, IndexError, TypeError) as exc:
        raise ShellError(f"a layout box is malformed: {exc}") from None


def doorway(entrance: Mapping[str, Any], bounds: Box) -> Box:
    """The cells of the wall ring in front of an entrance: from its face out to the bounds."""
    e = _box(entrance)
    side = entrance.get("side")
    lo, hi = list(e.lo), list(e.hi)
    if side == "west":
        lo[0], hi[0] = bounds.lo[0], e.lo[0] - 1
    elif side == "east":
        lo[0], hi[0] = e.hi[0] + 1, bounds.hi[0]
    elif side == "north":
        lo[2], hi[2] = bounds.lo[2], e.lo[2] - 1
    elif side == "south":
        lo[2], hi[2] = e.hi[2] + 1, bounds.hi[2]
    else:
        raise ShellError(
            f"an entrance's side {side!r} is not one of {', '.join(SIDES)}"
        )
    if any(lo[i] > hi[i] for i in range(3)):
        raise ShellError(f"entrance {entrance.get('id')} is not on the layout's edge")
    return Box((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]))


def spec_from_layout(
    lay: Mapping[str, Any],
    swatches: Sequence[Swatch],
    *,
    theme: theme_lib.Theme | None = None,
    prompt: str = "",
    line_type: str = theme_lib.GENERAL,
    max_blocks: int | None = None,
    headroom: int | None = None,
) -> Spec:
    """The shell's input from a WO-10.5 layout JSON (gos-layout v1). The palette must already
    have passed :func:`check_palette`."""
    if lay.get("schema") != layout_lib.SCHEMA or lay.get("v") != layout_lib.VERSION:
        raise ShellError(
            f"not a {layout_lib.SCHEMA} v{layout_lib.VERSION} layout: "
            f"{lay.get('schema')!r} v{lay.get('v')!r}"
        )
    mb, hr = options(max_blocks, headroom)
    if line_type not in theme_lib.LINE_TYPES:
        raise ShellError(
            f"line type {line_type!r} is not one of {', '.join(theme_lib.LINE_TYPES)}"
        )
    if len(prompt) > theme_lib.MAX_TEXT * 5:
        raise ShellError(
            f"the prompt is longer than {theme_lib.MAX_TEXT * 5} characters"
        )
    inner = _box(lay["bounds"])
    bounds = Box(
        (inner.lo[0] - MARGIN, inner.lo[1], inner.lo[2] - MARGIN),
        (inner.hi[0] + MARGIN, inner.hi[1] + hr, inner.hi[2] + MARGIN),
    )
    clear: list[Volume] = []
    for key, kind in (
        ("keepouts", "keepout"),
        ("corridors", "corridor"),
        ("chases", "chase"),
    ):
        for v in lay.get(key, []):
            clear.append(Volume(str(v["id"]), kind, _box(v)))
    entrances = lay.get("entrances") or []
    if not entrances:
        raise ShellError("the layout has no entrance: a shell needs a doorway")
    for e in entrances:
        clear.append(Volume(f"door-{e['id']}", "doorway", doorway(e, bounds)))
    rules: dict[str, tuple[str, ...]] = {}
    if theme is not None:
        rules = {k: tuple(getattr(theme, k)) for k in theme_lib.RULE_KEYS}
        if (a := theme.adaptation(line_type)) is not None:
            rules["adapt"] = (a,)
    return Spec(
        layout=str(lay["id"]),
        plans=tuple(str(p["id"]) for p in lay.get("plans", [])),
        bounds=bounds,
        interior=inner,
        clear=tuple(clear),
        swatches=tuple(swatches),
        theme=None if theme is None else f"{theme.id}@v{theme.v}",
        theme_name=None if theme is None else theme.name,
        prompt=prompt,
        line_type=line_type,
        max_blocks=mb,
        headroom=hr,
        theme_rules=rules,
    )


# --- the vocabulary ------------------------------------------------------------------------


@dataclass
class Compiled:
    cells: dict[Cell, Entry]
    roles: dict[Entry, str]  # the role each state was placed as (first seen)
    outside: int  # cells the operations put outside the bounding volume (dropped)


def _cell(v: object, what: str) -> Cell:
    if (
        not isinstance(v, list | tuple)
        or len(v) != 3
        or any(isinstance(c, bool) or not isinstance(c, int) for c in v)
    ):
        raise ShellError(f"{what} must be [x, y, z], whole numbers")
    if any(abs(c) > 100_000 for c in v):
        raise ShellError(f"{what} is too far from the layout")
    return (v[0], v[1], v[2])


def _clamp(lo: Cell, hi: Cell, b: Box) -> tuple[Cell, Cell] | None:
    a = tuple(max(lo[i], b.lo[i]) for i in range(3))
    z = tuple(min(hi[i], b.hi[i]) for i in range(3))
    if any(a[i] > z[i] for i in range(3)):
        return None
    return (a[0], a[1], a[2]), (z[0], z[1], z[2])


def _volume(lo: Cell, hi: Cell) -> int:
    return (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)


def _ring(b: Box, inset: int, y0: int, y1: int) -> Iterator[tuple[Cell, str, int, int]]:
    """The ring of columns ``inset`` in from ``b``'s sides, from y0 to y1: (cell, side, index
    along the side from its west or north end, the side's length). Corners belong to the
    north and south sides."""
    x0, x1 = b.lo[0] + inset, b.hi[0] - inset
    z0, z1 = b.lo[2] + inset, b.hi[2] - inset
    if x0 > x1 or z0 > z1:
        return
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            yield (x, y, z0), "north", x - x0, x1 - x0 + 1
            if z1 != z0:
                yield (x, y, z1), "south", x - x0, x1 - x0 + 1
        for z in range(z0 + 1, z1):
            yield (x0, y, z), "west", z - z0, z1 - z0 + 1
            if x1 != x0:
                yield (x1, y, z), "east", z - z0, z1 - z0 + 1


class _Builder:
    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.roles = spec.roles()
        self.cells: dict[Cell, Entry] = {}
        self.placed_as: dict[Entry, str] = {}
        self.outside = 0
        self.cap = 4 * spec.max_blocks

    def state(
        self, op: Mapping[str, Any], n: int, key: str = "role", pick_key: str = "n"
    ) -> Entry:
        """The state an operation names: ``block`` (one ``registry@meta``), or the palette
        role in ``key`` and its entry number in ``pick_key`` (default the role's first)."""
        if key == "role" and "block" in op:
            if "role" in op:
                raise ShellError(f"op {n}: name a role or a block, not both")
            try:
                e = palette.parse_entry(str(op["block"]))
            except palette.PaletteError as exc:
                raise ShellError(f"op {n}: {exc}") from None
            self.placed_as.setdefault(e, "block")
            return e
        role = op.get(key)
        if not isinstance(role, str):
            raise ShellError(f"op {n} ({op.get('op')}) needs a {key} (a palette role)")
        got = self.roles.get(role)
        if not got:
            raise ShellError(
                f"op {n}: the palette has no {role!r} entry (roles: {', '.join(sorted(self.roles))})"
            )
        pick = _int(op.get(pick_key, 0), f"op {n}: {pick_key}", 0, len(got) - 1)
        e = got[pick]
        self.placed_as.setdefault(e, role)
        return e

    def put(self, c: Cell, e: Entry | None) -> None:
        if not self.spec.bounds.contains(c):
            self.outside += 1
            return
        if e is None:
            self.cells.pop(c, None)
        else:
            self.cells[c] = e
            if len(self.cells) > self.cap:
                raise ShellError(
                    f"the design places more than {self.cap} blocks, far more than max_blocks "
                    f"({self.spec.max_blocks}) allows"
                )

    def box(self, lo: Cell, hi: Cell, e: Entry | None, n: int) -> None:
        if any(lo[i] > hi[i] for i in range(3)):
            raise ShellError(f"op {n}: lo must not be above hi")
        size = _volume(lo, hi)
        got = _clamp(lo, hi, self.spec.bounds)
        inside = 0 if got is None else _volume(*got)
        self.outside += size - inside
        if got is None:
            return
        if e is not None and inside > self.cap:
            raise ShellError(
                f"op {n} fills {inside} cells, more than max_blocks allows ({self.spec.max_blocks})"
            )
        a, z = got
        for x in range(a[0], z[0] + 1):
            for y in range(a[1], z[1] + 1):
                for zz in range(a[2], z[2] + 1):
                    self.put((x, y, zz), e)


def _heights(
    op: Mapping[str, Any], n: int, spec: Spec, y0: int, y1: int
) -> tuple[int, int]:
    got = op.get("y", [y0, y1])
    if isinstance(got, int) and not isinstance(got, bool):
        got = [got, got]
    if not isinstance(got, list | tuple) or len(got) != 2:
        raise ShellError(f"op {n}: y must be [y0, y1] or one height")
    a = _int(got[0], f"op {n}: y0", -64, 512)
    b = _int(got[1], f"op {n}: y1", -64, 512)
    if a > b:
        raise ShellError(f"op {n}: y0 must not be above y1")
    return a, b


def _sides(op: Mapping[str, Any], n: int) -> set[str]:
    got = op.get("sides", list(SIDES))
    if not isinstance(got, list) or not got or any(s not in SIDES for s in got):
        raise ShellError(f"op {n}: sides must be a list of {', '.join(SIDES)}")
    return set(got)


_KEYS: dict[str, frozenset[str]] = {
    "walls": frozenset({"op", "role", "block", "n", "y", "inset"}),
    "pillars": frozenset({"op", "role", "block", "n", "y", "inset", "every"}),
    "windows": frozenset(
        {"op", "role", "block", "n", "y", "inset", "every", "width", "sides"}
    ),
    "roof": frozenset(
        {
            "op",
            "role",
            "block",
            "n",
            "y",
            "style",
            "edge",
            "edge_n",
            "parapet",
            "parapet_role",
            "parapet_n",
        }
    ),
    "lights": frozenset({"op", "role", "block", "n", "y", "every"}),
    "fill": frozenset({"op", "role", "block", "n", "lo", "hi"}),
    "frame": frozenset({"op", "role", "block", "n", "lo", "hi"}),
    "clear": frozenset({"op", "lo", "hi"}),
}


def compile_ops(spec: Spec, ops: Sequence[Mapping[str, Any]]) -> Compiled:
    """Turn the vocabulary into cells (later operations overwrite earlier ones). A malformed
    operation refuses the whole design (:class:`ShellError`)."""
    if not isinstance(ops, list | tuple) or not ops:
        raise ShellError("a design is a non-empty list of operations")
    if len(ops) > MAX_OPS:
        raise ShellError(f"{len(ops)} operations: at most {MAX_OPS}")
    b = _Builder(spec)
    bx = spec.bounds
    for n, op in enumerate(ops, 1):
        if not isinstance(op, Mapping):
            raise ShellError(f"op {n} is not an object")
        kind = op.get("op")
        if kind not in _KEYS:
            raise ShellError(f"op {n}: {kind!r} is not one of {', '.join(OPS)}")
        extra = set(op) - _KEYS[str(kind)]
        if extra:
            raise ShellError(
                f"op {n} ({kind}): unknown key(s) {', '.join(sorted(extra))}"
            )
        if kind in ("fill", "frame", "clear"):
            lo, hi = (
                _cell(op.get("lo"), f"op {n}: lo"),
                _cell(op.get("hi"), f"op {n}: hi"),
            )
            e = None if kind == "clear" else b.state(op, n)
            if kind != "frame":
                b.box(lo, hi, e, n)
                continue
            if any(lo[i] > hi[i] for i in range(3)):
                raise ShellError(f"op {n}: lo must not be above hi")
            for c in _edges(lo, hi):
                b.put(c, e)
            continue
        e = b.state(op, n)
        if kind in ("walls", "pillars", "windows"):
            y0, y1 = _heights(op, n, spec, bx.lo[1], spec.wall_top)
            inset = _int(op.get("inset", 0), f"op {n}: inset", 0, 8)
            if kind == "walls":
                for c, *_ in _ring(bx, inset, y0, y1):
                    b.put(c, e)
            elif kind == "pillars":
                every = _int(op.get("every", 8), f"op {n}: every", 2, 64)
                for c, _, k, length in _ring(bx, inset, y0, y1):
                    if k == 0 or k == length - 1 or k % every == 0:
                        b.put(c, e)
            else:
                every = _int(op.get("every", 4), f"op {n}: every", 2, 64)
                width = _int(op.get("width", 2), f"op {n}: width", 1, every - 1)
                sides = _sides(op, n)
                start = (every - width + 1) // 2
                for c, side, k, length in _ring(bx, inset, y0, y1):
                    if side not in sides or k in (0, length - 1):
                        continue
                    if start <= k % every < start + width:
                        b.put(c, e)
        elif kind == "roof":
            y = _int(op.get("y", spec.roof_y), f"op {n}: y", -64, 512)
            style = op.get("style", "flat")
            if style not in ("flat", "gable"):
                raise ShellError(f"op {n}: style must be flat or gable")
            _roof(b, op, n, e, y, str(style))
        else:  # lights
            y = _int(op.get("y", spec.roof_y), f"op {n}: y", -64, 512)
            every = _int(op.get("every", 6), f"op {n}: every", 2, 32)
            for x in range(bx.lo[0] + 1, bx.hi[0]):
                for z in range(bx.lo[2] + 1, bx.hi[2]):
                    if (x - bx.lo[0]) % every == every // 2 and (
                        z - bx.lo[2]
                    ) % every == every // 2:
                        b.put((x, y, z), e)
    return Compiled(b.cells, b.placed_as, b.outside)


def _edges(lo: Cell, hi: Cell) -> Iterator[Cell]:
    """The cells on a box's 12 edges, each once."""
    seen: set[Cell] = set()
    for axis in range(3):
        a, b = [i for i in range(3) if i != axis]
        for pa in sorted({lo[a], hi[a]}):
            for pb in sorted({lo[b], hi[b]}):
                for t in range(lo[axis], hi[axis] + 1):
                    c = [0, 0, 0]
                    c[axis], c[a], c[b] = t, pa, pb
                    cell = (c[0], c[1], c[2])
                    if cell not in seen:
                        seen.add(cell)
                        yield cell


def _roof(
    b: _Builder, op: Mapping[str, Any], n: int, e: Entry, y: int, style: str
) -> None:
    bx = b.spec.bounds
    x0, x1, z0, z1 = bx.lo[0], bx.hi[0], bx.lo[2], bx.hi[2]
    edge = b.state(op, n, "edge", "edge_n") if "edge" in op else None
    if style == "flat":
        b.box((x0, y, z0), (x1, y, z1), e, n)
        if edge is not None:
            for c, *_ in _ring(bx, 0, y, y):
                b.put(c, edge)
        top = y
    else:  # gable: slopes rise from the long sides; the ends are filled
        along_x = (x1 - x0) >= (z1 - z0)
        lo_s, hi_s = (z0, z1) if along_x else (x0, x1)
        i = 0
        while lo_s + i <= hi_s - i:
            yy = y + i
            if yy > bx.hi[1]:  # out of headroom: close the roof flat here
                yy = bx.hi[1]
                for s in range(lo_s + i, hi_s - i + 1):
                    _line(b, along_x, s, yy, x0, x1, z0, z1, e)
                break
            for s in {lo_s + i, hi_s - i}:
                _line(b, along_x, s, yy, x0, x1, z0, z1, e)
            for s in range(lo_s + i, hi_s - i + 1):  # the gable ends
                if along_x:
                    b.put((x0, yy, s), e)
                    b.put((x1, yy, s), e)
                else:
                    b.put((s, yy, z0), e)
                    b.put((s, yy, z1), e)
            if edge is not None and i == 0:
                for s in (lo_s, hi_s):
                    _line(b, along_x, s, yy, x0, x1, z0, z1, edge)
            i += 1
        top = y
    parapet = _int(op.get("parapet", 0), f"op {n}: parapet", 0, 3)
    if parapet:
        pe = (
            b.state(op, n, "parapet_role", "parapet_n")
            if "parapet_role" in op
            else edge or e
        )
        for c, *_ in _ring(bx, 0, top + 1, top + parapet):
            b.put(c, pe)


def _line(
    b: _Builder,
    along_x: bool,
    s: int,
    y: int,
    x0: int,
    x1: int,
    z0: int,
    z1: int,
    e: Entry,
) -> None:
    if along_x:
        for x in range(x0, x1 + 1):
            b.put((x, y, s), e)
    else:
        for z in range(z0, z1 + 1):
            b.put((s, y, z), e)


# --- validation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Findings:
    blocks: int  # cells inside the bounding volume
    outside: int  # cells the design put outside it
    breaches: Mapping[str, int]  # clear volume id -> cells of the design inside it
    foreign: Mapping[Entry, int]  # states outside the palette -> cells
    over: int  # blocks above max_blocks (0: within)

    @property
    def breached(self) -> int:
        return sum(self.breaches.values())

    @property
    def clean(self) -> bool:
        return (
            self.blocks > 0
            and not self.outside
            and not self.breaches
            and not self.foreign
            and not self.over
        )


def _index(spec: Spec) -> list[tuple[Volume, Box]]:
    return [(v, v.box) for v in spec.clear]


def breach_of(spec: Spec, c: Cell, vols: Sequence[tuple[Volume, Box]]) -> str | None:
    for v, box in vols:
        if box.contains(c):
            return v.id
    return None


def inspect(spec: Spec, cells: Mapping[Cell, Entry], outside: int = 0) -> Findings:
    """Every rule a shell must hold, on the cells alone: inside the bounding volume, nothing in
    a keep-out, corridor, chase or doorway, every state in the palette, at most max_blocks."""
    vols = _index(spec)
    breaches: Counter[str] = Counter()
    foreign: Counter[Entry] = Counter()
    allowed = spec.allowed()
    out = outside
    for c, e in cells.items():
        if not spec.bounds.contains(c):
            out += 1
            continue
        hit = breach_of(spec, c, vols)
        if hit is not None:
            breaches[hit] += 1
        if e not in allowed:
            foreign[e] += 1
    inside = len(cells) - (out - outside)
    return Findings(
        blocks=inside,
        outside=out,
        breaches=dict(breaches),
        foreign=dict(foreign),
        over=max(0, inside - spec.max_blocks),
    )


@dataclass(frozen=True)
class Outcome:
    """A design's verdict. ``cells`` is empty when refused; then nothing may be written."""

    status: str  # accepted, repaired or refused
    source: str
    cells: Mapping[Cell, Entry]
    roles: Mapping[Entry, str]
    reasons: tuple[str, ...] = ()  # why refused
    repairs: tuple[str, ...] = ()  # what was removed to make it valid
    findings: Findings | None = None
    report: palette.Report | None = None  # the palette rule on the final states

    @property
    def ok(self) -> bool:
        return self.status != "refused"


def _why_foreign(conn: sqlite3.Connection, states: Iterable[Entry]) -> list[str]:
    report = palette.check(conn, list(states), None, max_types=None)
    out = []
    for v in report.verdicts:
        why = "; ".join(v.refused) if v.refused else "not in this build's palette"
        out.append(f"{v.entry.key}: {why}")
    return out


def design(
    conn: sqlite3.Connection,
    spec: Spec,
    ops: Sequence[Mapping[str, Any]],
    source: str = "architect",
) -> Outcome:
    """Compile, inspect, repair or refuse (D317). An accepted or repaired outcome's cells are
    clean by :func:`inspect` and pass the palette rule; a refused one has none."""
    if source not in SOURCES:
        raise ValueError(source)

    def refuse(*why: str, f: Findings | None = None) -> Outcome:
        return Outcome("refused", source, {}, {}, tuple(why), findings=f)

    try:
        got = compile_ops(spec, ops)
    except ShellError as exc:
        return refuse(str(exc))
    f = inspect(spec, got.cells, got.outside)
    if f.foreign:
        return refuse(
            "states outside the palette (shells hold the palette's plain blocks only): "
            + " | ".join(_why_foreign(conn, sorted(f.foreign, key=lambda e: e.key))),
            f=f,
        )
    total = f.blocks + f.outside
    bad = f.outside + f.breached
    if total and bad > REPAIR_SHARE * total:
        return refuse(
            f"{bad} of {total} cells are outside the bounding volume or inside a keep-out, "
            f"corridor, chase or doorway: more than {round(REPAIR_SHARE * 100)}% is too far off "
            "to repair; redesign",
            f=f,
        )
    vols = _index(spec)
    cells = {
        c: e
        for c, e in got.cells.items()
        if spec.bounds.contains(c) and breach_of(spec, c, vols) is None
    }
    repairs: list[str] = []
    if f.outside:
        repairs.append(f"removed {f.outside} cells outside the bounding volume")
    kinds = {v.id: v.kind for v in spec.clear}
    for vid, count in sorted(f.breaches.items()):
        repairs.append(f"removed {count} cells inside {kinds[vid]} {vid}")
    final = inspect(spec, cells)
    if final.blocks == 0:
        return refuse("the design has no blocks", f=final)
    if final.over:
        return refuse(
            f"{final.blocks} blocks: more than max_blocks ({spec.max_blocks})", f=final
        )
    if not final.clean:  # can't happen: the repair removed every breach
        raise AssertionError(final)
    report = palette.check(
        conn, sorted(set(cells.values()), key=lambda e: e.key), None, None
    )
    if not report.ok:  # the palette was checked; the catalog changed under us
        return refuse(*palette.render(report), f=final)
    roles = {e: got.roles.get(e, "block") for e in set(cells.values())}
    status = "repaired" if repairs else "accepted"
    return Outcome(status, source, cells, roles, (), tuple(repairs), final, report)


# --- the procedural generator --------------------------------------------------------------


def _words(spec: Spec, key: str) -> str:
    return " ".join(spec.theme_rules.get(key, ())).lower()


def procedural(spec: Spec) -> list[dict[str, Any]]:
    """The fallback and test baseline: walls, pillars, plinth, eaves band, windows, roof and
    lights from the theme's rules and the palette's roles. Always clean by :func:`inspect`."""
    roles = spec.roles()
    top = spec.wall_top
    ops: list[dict[str, Any]] = [{"op": "walls", "role": "wall", "y": [0, top]}]
    if "trim" in roles:
        ops.append({"op": "walls", "role": "trim", "y": [0, 0]})  # a plinth
    if "accent" in roles and top >= 3:
        ops.append({"op": "walls", "role": "accent", "y": [top, top]})  # the eaves band
    win = _words(spec, "window")
    if "window" in roles:
        if any(w in win for w in ("strip", "high", "clerestory")):
            hi = top - 1 if "accent" in roles and top >= 3 else top
            lo = max(1, hi - 1)
            ops.append(
                {
                    "op": "windows",
                    "role": "window",
                    "y": [lo, hi],
                    "every": 4,
                    "width": 3,
                }
            )
        elif any(w in win for w in ("tall", "lots", "big", "arched", "many")):
            hi = max(1, top - 1)
            ops.append(
                {
                    "op": "windows",
                    "role": "window",
                    "y": [1, hi],
                    "every": 4,
                    "width": 2,
                }
            )
        else:
            hi = min(top, 2) if top >= 2 else top
            ops.append(
                {
                    "op": "windows",
                    "role": "window",
                    "y": [1, hi],
                    "every": 5,
                    "width": 2,
                }
            )
    if "pillar" in roles:
        ops.append({"op": "pillars", "role": "pillar", "y": [0, top], "every": 8})
    roof_words = _words(spec, "roof") + " " + _words(spec, "massing")
    b = spec.bounds
    short = min(b.hi[0] - b.lo[0], b.hi[2] - b.lo[2]) + 1
    gable = (
        any(w in roof_words for w in ("steep", "gable", "pitched", "slope"))
        and short <= 33
    )
    roof: dict[str, Any] = {
        "op": "roof",
        "role": "roof",
        "style": "gable" if gable else "flat",
    }
    if len(roles["roof"]) > 1:  # the roof's first entry, its last at the edge
        roof["edge"] = "roof"
        roof["edge_n"] = len(roles["roof"]) - 1
    if "parapet" in roof_words and not gable and spec.headroom >= 2:
        roof["parapet"] = 1
        roof["parapet_role"] = "wall"
    ops.append(roof)
    if "light" in roles:
        ops.append({"op": "lights", "role": "light", "every": 6})
    for v in spec.doorways():
        ops.append({"op": "clear", "lo": list(v.box.lo), "hi": list(v.box.hi)})
    return ops


# --- outputs -------------------------------------------------------------------------------


def build_id(spec: Spec, ops: Sequence[Mapping[str, Any]]) -> str:
    """``sh-`` + 12 hex: the same layout, palette, options and operations give the same id."""
    payload = {
        "layout": spec.layout,
        "theme": spec.theme,
        "palette": [[s.role, s.entry.key] for s in spec.swatches],
        "prompt": spec.prompt,
        "line_type": spec.line_type,
        "max_blocks": spec.max_blocks,
        "headroom": spec.headroom,
        "ops": list(ops),
    }
    text = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return BUILD_ID_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def to_schematic(spec: Spec, cells: Mapping[Cell, Entry]) -> schematic.Schematic:
    """The cells as a schematic of the whole bounding volume; WEOffset is its min corner
    relative to the layout's origin (where Clayton stands to paste)."""
    lo = spec.bounds.lo
    w, h, ln = spec.bounds.size
    blocks = {(c[0] - lo[0], c[1] - lo[1], c[2] - lo[2]): e for c, e in cells.items()}
    return schematic.Schematic.from_blocks(w, h, ln, blocks, offset=lo)


@dataclass(frozen=True)
class Material:
    role: str
    entry: Entry
    name: str | None
    count: int


def manifest(outcome: Outcome) -> list[Material]:
    """The palette manifest: each state the shell uses, its role, catalog name and count;
    most used first."""
    counts = Counter(outcome.cells.values())
    names = {}
    if outcome.report is not None:
        names = {v.entry: v.name for v in outcome.report.verdicts}
    rows = [
        Material(outcome.roles.get(e, "block"), e, names.get(e), n)
        for e, n in counts.items()
    ]
    return sorted(rows, key=lambda m: (-m.count, m.entry.key))


# Faces: d is the direction the face looks: 0 +x (east), 1 -x, 2 +y (up), 3 -y, 4 +z (south),
# 5 -z. A face rectangle is [d, x, y, z, a, b, c]: (x, y, z) its lowest cell, a and b its extent
# along the plane's two axes (x faces: z then y; y faces: x then z; z faces: x then y), c the
# manifest index of its block state.
_DIRS: tuple[Cell, ...] = (
    (1, 0, 0),
    (-1, 0, 0),
    (0, 1, 0),
    (0, -1, 0),
    (0, 0, 1),
    (0, 0, -1),
)
_PLANE = {
    0: (0, 2, 1),
    1: (0, 2, 1),
    2: (1, 0, 2),
    3: (1, 0, 2),
    4: (2, 0, 1),
    5: (2, 0, 1),
}


def mesh(
    cells: Mapping[Cell, Entry], order: Sequence[Entry], origin: Cell = (0, 0, 0)
) -> list[list[int]]:
    """The exposed faces of the cells (relative to ``origin``), merged greedily into rectangles
    per plane and state. Deterministic."""
    index = {e: i for i, e in enumerate(order)}
    faces: dict[tuple[int, int], dict[tuple[int, int], int]] = {}
    for c, e in cells.items():
        for d, step in enumerate(_DIRS):
            if (c[0] + step[0], c[1] + step[1], c[2] + step[2]) in cells:
                continue
            p = [c[0] - origin[0], c[1] - origin[1], c[2] - origin[2]]
            n_ax, u_ax, v_ax = _PLANE[d]
            faces.setdefault((d, p[n_ax]), {})[(p[u_ax], p[v_ax])] = index[e]
    out: list[list[int]] = []
    for (d, depth), grid in sorted(faces.items()):
        n_ax, u_ax, v_ax = _PLANE[d]
        left = dict(grid)
        for u, v in sorted(grid, key=lambda k: (k[1], k[0])):
            colour = left.get((u, v))
            if colour is None:
                continue
            a = 1
            while left.get((u + a, v)) == colour:
                a += 1
            b = 1
            while all(left.get((u + k, v + b)) == colour for k in range(a)):
                b += 1
            for du in range(a):
                for dv in range(b):
                    del left[(u + du, v + dv)]
            p = [0, 0, 0]
            p[n_ax], p[u_ax], p[v_ax] = depth, u, v
            out.append([d, p[0], p[1], p[2], a, b, colour])
    return out


def spec_json(spec: Spec) -> dict[str, Any]:
    return {
        "layout": spec.layout,
        "plans": list(spec.plans),
        "bounds": {**spec.bounds.to_json(), "size": list(spec.bounds.size)},
        "interior": spec.interior.to_json(),
        "wall_top": spec.wall_top,
        "roof_y": spec.roof_y,
        "doorways": [{"id": v.id, **v.box.to_json()} for v in spec.doorways()],
        "clear": len(spec.clear),
        "theme": spec.theme,
        "prompt": spec.prompt,
        "line_type": spec.line_type,
        "max_blocks": spec.max_blocks,
        "headroom": spec.headroom,
        "palette": [
            {"role": s.role, "block": s.entry.key, "note": s.note}
            for s in spec.swatches
        ],
    }
