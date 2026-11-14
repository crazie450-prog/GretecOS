"""The footprint library (WO-10.2, plan 7.10 "Footprint library", D33, D305, D306).

One footprint per (controller, tier channels, facing), built from the ``structure`` table
(WO-1.8, D123): gtnh-extractor's rows for the base pack (``source = 'gtnh-extractor'``) and Lab
captures of addon multis (``source = 'lab-capture'``, :mod:`gregtec.build.capture`). Nothing here
is stored: a footprint is a pure function of its structure row, a facing and the tier choices,
so the structure table is the library and this module reads it (read-only).

- **Cells.** Every cell's ``registry@meta`` (never a numeric id), as offsets from the controller
  (``d = [dx, dy, dz]``, y up). A cell that takes a hatch carries the hatch kinds it accepts (GT
  ``HatchElement`` names, e.g. ``InputBus``, from the row's ``hatches_json``); a hatch-only cell
  (GT's ``newAny``) has no block, because it stays air until a hatch goes in.
- **Bounding box** of the blocks and hatch cells: ``lo``/``hi`` offsets and ``size``. For the
  facing the extractor built in (north) it equals the row's ``size_x/y/z``.
- **Tier channels.** The row's variant key (``t<trigger stack>`` plus the StructureLib channels
  the extractor set, e.g. a height) is the shape. Its ``subs_json`` lists the tier swaps that
  keep the shape (coil, glass, pipe, casing tiers): a footprint has one value per such channel.
  The value the extractor built with is the one whose block is in the cells; choosing another
  swaps those cells for that value's block (:func:`build`).
- **Facing.** The extractor builds with the controller's front facing NORTH (-Z). The four
  facings (the way the controller's front faces) rotate the offsets about the vertical axis
  through the controller, in code (:func:`rotate`). Block metas are kept as they are (D305).
- **Lineage.** 2.9 ships legacy and new turbines, large combustion engines and boilers as
  separate controllers (``MTELargeTurbineSteamLegacy`` and ``MTELargeTurbineSteam``, ...). Each
  is its own controller, so its own footprints; :func:`lineage` (a class name ending in
  ``Legacy``) and :func:`family` label them so a report keeps them apart (D305).

Footprints are machinery: Clayton builds them legitimately from the 3D guide (D35). GOS never
pastes one, and nothing here writes a schematic.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from gregtec.build.palette import GT_MACHINES, PaletteError, parse_entry

type Offset = tuple[int, int, int]
type Facing = Literal["north", "east", "south", "west"]

FACINGS: tuple[Facing, ...] = ("north", "east", "south", "west")
BUILT_FACING: Facing = (
    "north"  # the extractor's facing_convention: front faces NORTH (-Z)
)
# The unit vector the controller's front points along, per facing (x east, z south).
FRONT: dict[Facing, Offset] = {
    "north": (0, 0, -1),
    "east": (1, 0, 0),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
}
EXTRACTOR = "gtnh-extractor"
CAPTURE = "lab-capture"
SOURCES = (
    EXTRACTOR,
    CAPTURE,
)  # the order a duplicate is resolved in: the extractor wins
LEGACY = "legacy"
CURRENT = "current"
FAMILIES = (
    "turbine",
    "lce",
    "boiler",
)  # the 2.9 families with legacy and new controllers

_COLUMNS = (
    "registry_name",
    "meta",
    "variant",
    "size_x",
    "size_y",
    "size_z",
    "blocks_json",
    "hatches_json",
    "subs_json",
    "channels_json",
    "failures_json",
    "name_en",
    "class_name",
    "source",
    "machine_id",
)


class FootprintError(ValueError):
    """A footprint that can't be built as asked (shown as is)."""


def rotate(d: Offset, facing: Facing) -> Offset:
    """``d`` (an offset with the front facing north) as it stands with the front facing ``facing``.

    A quarter turn clockwise seen from above per step north -> east -> south -> west: (x, z)
    becomes (-z, x), which takes the north front (0, 0, -1) to the east one (1, 0, 0).
    """
    x, y, z = d
    match facing:
        case "north":
            return (x, y, z)
        case "east":
            return (-z, y, x)
        case "south":
            return (-x, y, -z)
        case "west":
            return (z, y, -x)
    raise FootprintError(f"facing {facing!r} is not one of {', '.join(FACINGS)}")


def unrotate(d: Offset, facing: Facing) -> Offset:
    """The inverse of :func:`rotate`: an offset seen with the front facing ``facing``, as north."""
    back: dict[Facing, Facing] = {
        "north": "north",
        "east": "west",
        "south": "south",
        "west": "east",
    }
    if facing not in back:
        raise FootprintError(f"facing {facing!r} is not one of {', '.join(FACINGS)}")
    return rotate(d, back[facing])


def parse_facing(text: str) -> Facing:
    f = text.strip().lower()
    for facing in FACINGS:
        if f == facing:
            return facing
    raise FootprintError(f"facing {text!r} is not one of {', '.join(FACINGS)}")


def lineage(class_name: str | None) -> str | None:
    """``legacy`` for a controller class whose name ends in ``Legacy`` (2.9's kept old
    controllers), ``current`` for any other class, None when the class isn't known."""
    if not class_name:
        return None
    return LEGACY if class_name.rsplit(".", 1)[-1].endswith("Legacy") else CURRENT


def family(class_name: str | None) -> str | None:
    """``turbine``, ``lce`` (a large combustion or diesel engine) or ``boiler`` from the class
    name, else None: the families 2.9 ships in legacy and new controllers."""
    simple = (class_name or "").rsplit(".", 1)[-1]
    if "Turbine" in simple:
        return "turbine"
    if "CombustionEngine" in simple or "DieselEngine" in simple:
        return "lce"
    if "Boiler" in simple:
        return "boiler"
    return None


@dataclass(frozen=True, slots=True)
class Cell:
    d: Offset
    block: (
        str | None
    )  # registry name; None for a hatch-only cell (air until a hatch goes in)
    meta: int | None
    hatches: tuple[
        str, ...
    ] = ()  # the hatch kinds the cell accepts; () when it takes none
    placed: str | None = None  # a capture's hatch as placed in the Lab (registry@meta)

    @property
    def key(self) -> str | None:
        return None if self.block is None else f"{self.block}@{self.meta}"


@dataclass(frozen=True, slots=True)
class Tier:
    """One value of a tier channel: the block its cells hold at that value."""

    channel: str
    value: int
    block: str
    meta: int

    @property
    def key(self) -> str:
        return f"{self.block}@{self.meta}"


@dataclass(frozen=True)
class Footprint:
    registry_name: str  # the controller's block, e.g. gregtech:gt.blockmachines
    meta: int  # its meta (a GT controller's MTE id)
    name: str | None
    class_name: str | None
    source: str  # gtnh-extractor or lab-capture
    variant: str  # the structure row's variant key: the shape
    tiers: tuple[
        tuple[str, int], ...
    ]  # (channel, value) per tier channel in effect, sorted
    facing: Facing
    cells: tuple[Cell, ...]  # sorted by offset (y, z, x)
    choices: Mapping[str, tuple[Tier, ...]] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()  # the extractor's notes on this controller
    machine_id: int | None = None

    @property
    def controller(self) -> str:
        return f"{self.registry_name}@{self.meta}"

    @property
    def mte(self) -> int | None:
        """The GT MTE id for a GT controller (its meta), else None."""
        return self.meta if self.registry_name == GT_MACHINES else None

    @property
    def key(self) -> str:
        """``<controller>|<variant>[;<tier channel>=<value>...]|<facing>``: one per footprint."""
        tiers = "".join(f";{c}={v}" for c, v in self.tiers)
        return f"{self.controller}|{self.variant}{tiers}|{self.facing}"

    @property
    def lo(self) -> Offset:
        return _corner(self.cells, min)

    @property
    def hi(self) -> Offset:
        return _corner(self.cells, max)

    @property
    def size(self) -> Offset:
        lo, hi = self.lo, self.hi
        return (hi[0] - lo[0] + 1, hi[1] - lo[1] + 1, hi[2] - lo[2] + 1)

    @property
    def blocks(self) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if c.block is not None)

    @property
    def hatch_cells(self) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if c.hatches)

    @property
    def lineage(self) -> str | None:
        return lineage(self.class_name)

    @property
    def family(self) -> str | None:
        return family(self.class_name)

    def rotated(self, facing: Facing) -> Footprint:
        """This footprint with the controller's front facing ``facing``."""
        if facing == self.facing:
            return self
        back = [replace(c, d=unrotate(c.d, self.facing)) for c in self.cells]
        return replace(
            self,
            facing=facing,
            cells=_sorted(replace(c, d=rotate(c.d, facing)) for c in back),
        )

    def materials(self) -> dict[str, int]:
        """``registry@meta`` -> how many cells hold it, controller included."""
        out: dict[str, int] = {}
        for c in self.blocks:
            assert c.key is not None
            out[c.key] = out.get(c.key, 0) + 1
        return dict(sorted(out.items()))

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "controller": self.controller,
            "name": self.name,
            "class_name": self.class_name,
            "source": self.source,
            "variant": self.variant,
            "tiers": dict(self.tiers),
            "facing": self.facing,
            "front": list(FRONT[self.facing]),
            "lineage": self.lineage,
            "family": self.family,
            "bbox": {"lo": list(self.lo), "hi": list(self.hi), "size": list(self.size)},
            "cells": [
                {"d": list(c.d)}
                | ({} if c.block is None else {"block": c.block, "meta": c.meta})
                | ({"hatches": list(c.hatches)} if c.hatches else {})
                | ({"placed": c.placed} if c.placed else {})
                for c in self.cells
            ],
            "materials": self.materials(),
            "hatch_cells": len(self.hatch_cells),
            "choices": {
                ch: [{"value": t.value, "block": t.key} for t in tiers]
                for ch, tiers in sorted(self.choices.items())
            },
            "caveats": list(self.caveats),
        }


def _corner(cells: Sequence[Cell], pick: Callable[[Iterable[int]], int]) -> Offset:
    if not cells:
        raise FootprintError("a footprint with no cells")
    return (
        pick(c.d[0] for c in cells),
        pick(c.d[1] for c in cells),
        pick(c.d[2] for c in cells),
    )


def _sorted(cells: Iterable[Cell]) -> tuple[Cell, ...]:
    return tuple(sorted(cells, key=lambda c: (c.d[1], c.d[2], c.d[0])))


@dataclass(frozen=True)
class Row:
    """One ``structure`` row, its JSON columns parsed."""

    registry_name: str
    meta: int
    variant: str
    size: Offset  # size_x, size_y, size_z as stored
    blocks: tuple[tuple[Offset, str, int], ...]
    hatches: tuple[tuple[Offset, tuple[str, ...], str | None], ...]
    subs: Mapping[str, tuple[Tier, ...]]
    caveats: tuple[str, ...]
    name: str | None
    class_name: str | None
    source: str
    machine_id: int | None

    @property
    def controller(self) -> str:
        return f"{self.registry_name}@{self.meta}"


def build(
    row: Row, facing: Facing = BUILT_FACING, tiers: Mapping[str, int] | None = None
) -> Footprint:
    """The footprint of ``row`` with the front facing ``facing`` and ``tiers`` chosen (a value
    per tier channel; the others keep the value the row was built with)."""
    tiers = dict(tiers or {})
    unknown = sorted(set(tiers) - set(row.subs))
    if unknown:
        known = ", ".join(sorted(row.subs)) or "none"
        raise FootprintError(
            f"{row.controller} has no tier channel {', '.join(unknown)} (its channels: {known})"
        )
    blocks = {d: (b, m) for d, b, m in row.blocks}
    present = {(b, m) for b, m in blocks.values()}
    effective: dict[str, int] = {}
    swap: dict[tuple[str, int], tuple[str, int]] = {}
    for channel, options in sorted(row.subs.items()):
        built = [t for t in options if (t.block, t.meta) in present]
        if not built:
            if channel in tiers:
                raise FootprintError(
                    f"{row.controller} {row.variant}: no cell holds a {channel} block, so its "
                    "tier can't be chosen"
                )
            continue  # the channel doesn't touch this shape
        if len(built) > 1:
            if channel in tiers:
                found = ", ".join(t.key for t in built)
                raise FootprintError(
                    f"{row.controller} {row.variant}: cells hold more than one {channel} "
                    f"block ({found}), so which cells are the {channel} is unknown"
                )
            continue  # ambiguous: leave the cells as built, and the channel out of the key
        now = built[0]
        want = tiers.get(channel, now.value)
        chosen = next((t for t in options if t.value == want), None)
        if chosen is None:
            values = ", ".join(str(t.value) for t in options)
            raise FootprintError(
                f"{row.controller} has no {channel} tier {want} (its values: {values})"
            )
        effective[channel] = chosen.value
        if chosen != now:
            swap[(now.block, now.meta)] = (chosen.block, chosen.meta)
    slots = {d: (kinds, placed) for d, kinds, placed in row.hatches}
    cells: list[Cell] = []
    for d in blocks.keys() | slots.keys():
        block, meta = swap.get(blocks[d], blocks[d]) if d in blocks else (None, None)
        kinds, placed = slots.get(d, ((), None))
        cells.append(Cell(d, block, meta, kinds, placed))
    fp = Footprint(
        registry_name=row.registry_name,
        meta=row.meta,
        name=row.name,
        class_name=row.class_name,
        source=row.source,
        variant=row.variant,
        tiers=tuple(sorted(effective.items())),
        facing=BUILT_FACING,
        cells=_sorted(cells),
        choices=dict(row.subs),
        caveats=row.caveats,
        machine_id=row.machine_id,
    )
    return fp.rotated(facing)


def _has_columns(conn: sqlite3.Connection) -> bool:
    have = {str(r[1]) for r in conn.execute("PRAGMA table_info(structure)")}
    return set(_COLUMNS) <= have


@dataclass(frozen=True)
class Skipped:
    controller: str
    variant: str
    source: str
    reason: str


@dataclass(frozen=True)
class Library:
    """Every structure row as a footprint facing north with the tiers it was built with."""

    footprints: tuple[Footprint, ...]  # sorted by (controller, variant)
    skipped: tuple[Skipped, ...] = ()  # rows that don't make a footprint, and why
    rows: Mapping[tuple[str, int, str], Row] = field(default_factory=dict)

    def for_controller(self, registry_name: str, meta: int) -> list[Footprint]:
        return [
            f
            for f in self.footprints
            if f.registry_name == registry_name and f.meta == meta
        ]

    def for_mte(self, mte: int) -> list[Footprint]:
        return self.for_controller(GT_MACHINES, mte)

    def get(
        self,
        registry_name: str,
        meta: int,
        variant: str | None = None,
        facing: Facing = BUILT_FACING,
        tiers: Mapping[str, int] | None = None,
    ) -> Footprint:
        """One footprint; ``variant`` None takes the controller's smallest by volume (D200's
        choice), then the first by key."""
        found = self.for_controller(registry_name, meta)
        controller = f"{registry_name}@{meta}"
        if not found:
            raise FootprintError(f"no footprint for {controller}")
        if variant is None:
            pick = min(found, key=lambda f: (_volume(f.size), f.variant))
        else:
            pick = next((f for f in found if f.variant == variant), None)  # type: ignore[assignment]
            if pick is None:
                variants = ", ".join(f.variant for f in found)
                raise FootprintError(
                    f"{controller} has no variant {variant!r} (its variants: {variants})"
                )
        return build(self.rows[(registry_name, meta, pick.variant)], facing, tiers)

    def every_facing(self) -> Iterable[Footprint]:
        for f in self.footprints:
            for facing in FACINGS:
                yield f.rotated(facing)


def _volume(size: Offset) -> int:
    return size[0] * size[1] * size[2]


def library(conn: sqlite3.Connection) -> Library:
    """Every structure row's footprint (north, built tiers); an empty library before WO-1.8's
    columns exist. A controller in both sources keeps the extractor's rows (D306)."""
    if not _has_columns(conn):
        return Library(())
    rows: dict[tuple[str, int, str], Row] = {}
    skipped: list[Skipped] = []
    by_source: dict[str, set[tuple[str, int]]] = {s: set() for s in SOURCES}
    raw = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM structure WHERE registry_name IS NOT NULL"
        " ORDER BY registry_name, meta, variant, source"
    ).fetchall()
    parsed: list[Row] = []
    for r in raw:
        try:
            parsed.append(_row(tuple(r)))
        except (FootprintError, ValueError, TypeError) as exc:
            skipped.append(
                Skipped(f"{r[0]}@{r[1]}", str(r[2]), str(r[13]), f"unreadable: {exc}")
            )
    for row in parsed:
        by_source.setdefault(row.source, set()).add((row.registry_name, row.meta))
    for row in parsed:
        key = (row.registry_name, row.meta)
        if row.source != EXTRACTOR and key in by_source[EXTRACTOR]:
            skipped.append(
                Skipped(
                    row.controller,
                    row.variant,
                    row.source,
                    "the extractor has this controller; its rows are the footprint",
                )
            )
            continue
        if row.source not in SOURCES:
            skipped.append(
                Skipped(
                    row.controller, row.variant, row.source, "not a footprint source"
                )
            )
            continue
        rows[(row.registry_name, row.meta, row.variant)] = row
    footprints = []
    for row in rows.values():
        try:
            footprints.append(build(row))
        except FootprintError as exc:
            skipped.append(Skipped(row.controller, row.variant, row.source, str(exc)))
    footprints.sort(key=lambda f: (f.registry_name, f.meta, f.variant))
    skipped.sort(key=lambda s: (s.controller, s.variant, s.source))
    return Library(tuple(footprints), tuple(skipped), rows)


def _row(r: tuple[Any, ...]) -> Row:
    (
        registry,
        meta,
        variant,
        sx,
        sy,
        sz,
        blocks_j,
        hatches_j,
        subs_j,
        _channels_j,
        failures_j,
        name,
        class_name,
        source,
        machine_id,
    ) = r
    block_name(registry, "controller")
    blocks = []
    for b in json.loads(blocks_j or "[]"):
        blocks.append(
            (offset(b["d"]), block_name(b["block"]), meta_value(b.get("meta", 0)))
        )
    if not blocks:
        raise FootprintError("no blocks")
    if len({d for d, _, _ in blocks}) != len(blocks):
        raise FootprintError("a cell is listed twice")
    hatches = []
    for h in json.loads(hatches_j or "[]"):
        kinds = tuple(str(k) for k in h["kinds"])
        if not kinds:
            raise FootprintError("a hatch cell with no kinds")
        placed = h.get("placed")
        hatches.append((offset(h["d"]), kinds, None if placed is None else str(placed)))
    if len({d for d, _, _ in hatches}) != len(hatches):
        raise FootprintError("a hatch cell is listed twice")
    subs = {
        str(ch): tuple(
            Tier(
                str(ch),
                int(s["channel_value"]),
                block_name(s["block"]),
                meta_value(s.get("meta", 0)),
            )
            for s in entries
        )
        for ch, entries in json.loads(subs_j or "{}").items()
    }
    return Row(
        registry_name=str(registry),
        meta=meta_value(meta),
        variant=str(variant or ""),
        size=(int(sx or 0), int(sy or 0), int(sz or 0)),
        blocks=tuple(blocks),
        hatches=tuple(hatches),
        subs=subs,
        caveats=tuple(str(f) for f in json.loads(failures_j or "[]")),
        name=None if name is None else str(name),
        class_name=None if class_name is None else str(class_name),
        source=str(source),
        machine_id=None if machine_id is None else int(machine_id),
    )


def offset(value: object) -> Offset:
    if not isinstance(value, list) or len(value) != 3:
        raise FootprintError(f"not an offset [x, y, z]: {value!r}")
    x, y, z = (integer(v) for v in value)
    return (x, y, z)


def integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FootprintError(f"not an integer: {value!r}")
    return value


def meta_value(value: object) -> int:
    m = integer(value)
    if not 0 <= m <= 0xFFFF:
        raise FootprintError(f"meta {m} is outside 0..65535")
    return m


def block_name(value: object, what: str = "block") -> str:
    """A registry name (``mod:name``); a numeric id is refused (EndlessIDs)."""
    if not isinstance(value, str):
        raise FootprintError(f"{what} is not a registry name: {value!r}")
    try:
        parse_entry(f"{value}@0")
    except PaletteError:
        raise FootprintError(f"{what} is not a registry name: {value!r}") from None
    if value.split(":", 1)[0].isdigit():
        raise FootprintError(f"{what} is a numeric id, not a registry name: {value!r}")
    return value


def parse_controller(text: str) -> tuple[str, int]:
    """``<registry>@<meta>`` or ``mte:<id>`` (a GT controller) -> (registry name, meta)."""
    t = text.strip()
    if t.startswith("mte:"):
        try:
            return GT_MACHINES, meta_value(int(t[4:]))
        except ValueError:
            raise FootprintError(f"{text!r} is not mte:<id>") from None
    try:
        e = parse_entry(t)
    except PaletteError as exc:
        raise FootprintError(f"{text!r}: {exc}") from None
    return e.block, e.meta


def parse_tiers(items: Sequence[str]) -> dict[str, int]:
    """``channel=value`` items -> {channel: value}."""
    out: dict[str, int] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name.strip() or not value.strip().lstrip("-").isdigit():
            raise FootprintError(f"{item!r} is not <channel>=<value>")
        out[name.strip()] = int(value)
    return out
