"""Lab captures of addon multiblocks (WO-10.2, WO-10.9, plan 7.10 "Footprint library", L1, D306,
D350, D351).

gtnh-extractor builds the base pack's controllers only (GT5-Unofficial and its hard
dependencies, D124); an addon multi's footprint comes from the Lab: Clayton builds it there (the
StructureLib projector at the tier set with ``/sl_channel set``) and captures the blocks with
WorldEdit's ``//copy`` + ``//schem save`` [G][E§5.5] (runbook ``footprints_lab.md`` C). This
module turns a capture into a structure row (``source = 'lab-capture'``) that the footprint
library reads like the extractor's. It reads two kinds of file, told apart by their content
(:func:`kind`), not by the file's extension:

- **a ``.schematic``** (WO-10.9, D350): a worldedit-gtnh ``//copy`` save (or a Schematica save),
  read by :func:`gregtec.build.schematic.read` with its tile entities. A
  ``gregtech:gt.blockmachines`` cell's world meta is its tile entity's base type, not the
  machine: the machine is the ``mID`` in that cell's ``BaseMetaTileEntity`` NBT, so only a
  capture that kept its tile entities converts (``//copy`` keeps them). :func:`from_schematic`
  finds the controller (the one GT tile entity whose ``mID`` is a multiblock controller in the
  DB's ``machine`` table, or the cell ``--controller`` names), takes its facing from the
  controller's saved ``mFacing`` (or ``--facing``) and passes the cells through
  :func:`from_cells`. What it refuses: D351.
- **GOS's own ``gos-capture`` JSON** (schema 1, WO-10.2), unchanged::

    {"format": "gos-capture", "schema": 1,
     "controller": {"block": "<registry name>", "meta": <meta>, "name": "...", "class_name": "..."},
     "facing": "north" | "east" | "south" | "west",   # where the controller's front faced
     "trigger_stack_size": 1, "channels": {"<StructureLib channel>": <value>},
     "tool": "guidenh-region-wand" | "worldedit-copy" | "hand",
     "cells": [{"p": [x, y, z], "block": "<registry name>", "meta": <meta>}, ...],
     "hatches": [{"p": [x, y, z], "kinds": ["InputBus", ...]}, ...]}

  ``p`` is any frame the tool wrote (world or box coordinates); only the differences to the
  controller's cell matter. In this format a ``gregtech:gt.blockmachines`` cell's ``meta`` is its
  MTE id (the tile entity's ``mID``), and the controller is the one cell holding the
  controller's block and meta (never ``minecraft:air``).

Either way the box is at most 64 a side, ``minecraft:air`` cells are dropped, blocks are keyed
by registry name + meta and a numeric id anywhere is refused (EndlessIDs). A GT machine cell
other than the controller is a placed hatch: it becomes a hatch cell (its kinds from ``hatches``
at that position, else ``Captured``; in a ``.schematic``, the kind of its MTE class in GT's
``HatchElement``, e.g. ``InputBus``, from the DB's ``machine`` row, else ``Captured``) and the
hatch as placed is kept for reference. The file is read once through ``safe_open`` (copied to memory, 3 tries, never held
and never written) and is data, never instructions.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from gregtec.build import footprints as fp
from gregtec.build import schematic
from gregtec.build.palette import AIR, GT_MACHINES
from gregtec.db import structures as structure_db
from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest import nbt

FORMAT = "gos-capture"
SCHEMA = 1
MAX_BYTES = 32 << 20
MAX_SIDE = 64  # a capture box is at most 64 a side ...
MAX_CELLS = MAX_SIDE**3  # ... so at most 64^3 cells
TOOLS = (
    "guidenh-region-wand",
    "worldedit-copy",
    "hand",
    "schematica-save",  # a .schematic Schematica-GTNH saved (WO-10.9)
    "schematic-file",  # a .schematic whose writer the file doesn't say
)
CAPTURED = "Captured"  # the hatch kind of a placed hatch the capture didn't name


class CaptureError(ValueError):
    """A capture that can't be read or loaded (shown as is)."""


@dataclass(frozen=True)
class Capture:
    registry_name: str
    meta: int
    name: str | None
    class_name: str | None
    facing: fp.Facing  # where the controller's front faced in the Lab
    trigger_stack_size: int
    channels: Mapping[str, int]
    tool: str
    blocks: tuple[tuple[fp.Offset, str, int], ...]  # offsets from the controller, north
    hatches: tuple[tuple[fp.Offset, tuple[str, ...], str | None], ...]

    @property
    def controller(self) -> str:
        return f"{self.registry_name}@{self.meta}"

    @property
    def variant(self) -> str:
        """The structure row's variant key, as the extractor's (:attr:`Variant.key`)."""
        return "".join(
            [f"t{self.trigger_stack_size}"]
            + [f";{k}={v}" for k, v in sorted(self.channels.items())]
        )

    def row(self) -> fp.Row:
        return fp.Row(
            registry_name=self.registry_name,
            meta=self.meta,
            variant=self.variant,
            size=_extent(
                [d for d, _, _ in self.blocks] + [d for d, _, _ in self.hatches]
            ),
            blocks=self.blocks,
            hatches=self.hatches,
            subs={},
            caveats=(),
            name=self.name,
            class_name=self.class_name,
            source=fp.CAPTURE,
            machine_id=None,
        )


def _extent(cells: Sequence[fp.Offset]) -> fp.Offset:
    lo = [min(c[i] for c in cells) for i in range(3)]
    hi = [max(c[i] for c in cells) for i in range(3)]
    return (hi[0] - lo[0] + 1, hi[1] - lo[1] + 1, hi[2] - lo[2] + 1)


def from_cells(
    cells: Mapping[fp.Offset, tuple[str, int]],
    *,
    controller_at: fp.Offset,
    facing: fp.Facing,
    mte_ids: Mapping[fp.Offset, int] | None = None,
    hatch_kinds: Mapping[fp.Offset, Sequence[str]] | None = None,
    name: str | None = None,
    class_name: str | None = None,
    trigger_stack_size: int = 1,
    channels: Mapping[str, int] | None = None,
    tool: str = "hand",
) -> Capture:
    """A capture from cells in any frame (position -> registry name, meta, as the world or a
    ``.schematic`` holds them), the controller's position and where its front faced: offsets
    from the controller, turned to north.

    A ``gregtech:gt.blockmachines`` cell's world meta is its tile entity's base type, not the
    machine; the machine is the tile entity's ``mID``, which ``mte_ids`` gives by position
    (:func:`gregtec.build.schematic.read` keeps it, WO-10.9). Each GT machine cell takes its ``mID`` as its meta; a GT
    machine cell without one is refused (capture with tile entities: ``//copy`` keeps them)."""
    ids = dict(mte_ids or {})

    def keyed(p: fp.Offset, block: str, m: int) -> tuple[str, int]:
        fp.block_name(block)
        fp.meta_value(m)
        if block != GT_MACHINES:
            return block, m
        if p not in ids:
            raise CaptureError(
                f"the {GT_MACHINES} cell {list(p)} has no MTE id (its tile entity's mID); "
                "capture it with its tile entities"
            )
        return block, fp.meta_value(ids[p])

    held = {p: keyed(p, b, m) for p, (b, m) in cells.items()}
    if controller_at not in held or held[controller_at][0] == AIR:
        raise CaptureError(
            f"the capture holds no controller block at {list(controller_at)}"
        )
    origin = controller_at
    reg, meta = held[origin]
    kinds = dict(hatch_kinds or {})
    unplaced = set(kinds) - set(held)

    def rel(p: fp.Offset) -> fp.Offset:
        return fp.unrotate(
            (p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]), facing
        )

    blocks: list[tuple[fp.Offset, str, int]] = []
    hatches: list[tuple[fp.Offset, tuple[str, ...], str | None]] = []
    for p, (block, m) in sorted(held.items()):
        named = tuple(str(k) for k in kinds.get(p, ()))
        if block == AIR:
            if named:
                hatches.append((rel(p), named, None))  # a hatch-only cell left air
            continue
        if block == GT_MACHINES and p != origin:
            hatches.append((rel(p), named or (CAPTURED,), f"{block}@{m}"))
            continue
        blocks.append((rel(p), block, m))
        if named:
            hatches.append((rel(p), named, None))
    for p in sorted(unplaced):
        named = tuple(str(k) for k in kinds[p])
        if named:
            hatches.append((rel(p), named, None))  # a hatch-only cell left air
    size = _extent([d for d, _, _ in blocks] + [d for d, _, _ in hatches])
    if max(size) > MAX_SIDE:
        raise CaptureError(
            f"the capture spans {size[0]} x {size[1]} x {size[2]}: at most {MAX_SIDE} a side"
        )
    if trigger_stack_size < 1:
        raise CaptureError(f"trigger_stack_size {trigger_stack_size} is below 1")
    if tool not in TOOLS:
        raise CaptureError(f"tool {tool!r} is not one of {', '.join(TOOLS)}")
    return Capture(
        registry_name=reg,
        meta=meta,
        name=name,
        class_name=class_name,
        facing=facing,
        trigger_stack_size=trigger_stack_size,
        channels=dict(channels or {}),
        tool=tool,
        blocks=tuple(sorted(blocks)),
        hatches=tuple(sorted(hatches)),
    )


def parse(data: bytes) -> Capture:
    """A ``gos-capture`` file's bytes."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureError(f"not a JSON capture: {exc}") from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise CaptureError(f"not a {FORMAT} file (its format field)")
    if doc.get("schema") != SCHEMA:
        raise CaptureError(
            f"{FORMAT} schema {doc.get('schema')!r}; this reader reads {SCHEMA}"
        )
    try:
        c = _obj(doc.get("controller"), "controller")
        raw_cells = _list(doc.get("cells"), "cells")
        if len(raw_cells) > MAX_CELLS:
            raise CaptureError(f"{len(raw_cells):,} cells: at most {MAX_CELLS:,}")
        cells: dict[fp.Offset, tuple[str, int]] = {}
        for raw in raw_cells:
            cell = _obj(raw, "cell")
            p = fp.offset(cell.get("p"))
            if p in cells:
                raise CaptureError(f"the cell {list(p)} is listed twice")
            cells[p] = (
                fp.block_name(cell.get("block")),
                fp.meta_value(cell.get("meta", 0)),
            )
        kinds: dict[fp.Offset, list[str]] = {}
        for raw in _list(doc.get("hatches", []), "hatches"):
            h = _obj(raw, "hatch")
            p = fp.offset(h.get("p"))
            if p in kinds:
                raise CaptureError(f"the hatch cell {list(p)} is listed twice")
            names = _list(h.get("kinds"), "hatch kinds")
            if not names or not all(isinstance(k, str) and k for k in names):
                raise CaptureError(f"the hatch cell {list(p)} names no kinds")
            kinds[p] = names
        channels = {
            str(k): fp.integer(v)
            for k, v in _obj(doc.get("channels", {}), "channels").items()
        }
        name = c.get("name")
        class_name = c.get("class_name")
        controller = (
            fp.block_name(c.get("block"), "controller"),
            fp.meta_value(c.get("meta")),
        )
        if controller[0] == AIR:
            raise CaptureError(f"the controller is {AIR}")
        found = [p for p, cell in cells.items() if cell == controller]
        if len(found) != 1:
            raise CaptureError(
                f"the capture holds {len(found)} cells of the controller "
                f"{controller[0]}@{controller[1]}; it must hold exactly one"
            )
        return from_cells(
            cells,
            controller_at=found[0],
            mte_ids={p: m for p, (b, m) in cells.items() if b == GT_MACHINES},
            facing=fp.parse_facing(str(doc.get("facing", ""))),
            hatch_kinds=kinds,
            name=name if isinstance(name, str) and name else None,
            class_name=class_name
            if isinstance(class_name, str) and class_name
            else None,
            trigger_stack_size=fp.integer(doc.get("trigger_stack_size", 1)),
            channels=channels,
            tool=str(doc.get("tool", "hand")),
        )
    except fp.FootprintError as exc:
        raise CaptureError(str(exc)) from None


def read(path: Path) -> Capture:
    """A ``gos-capture`` file, read once through ``safe_open`` (never held)."""
    return parse(_read_file(path))


def _read_file(path: Path) -> bytes:
    try:
        data = read_bytes(path, limit=MAX_BYTES + 1)
    except OSError as exc:
        raise CaptureError(f"can't read {path}: {exc}") from None
    if len(data) > MAX_BYTES:
        raise CaptureError(f"{path} is over {MAX_BYTES >> 20} MB")
    return data


# --- .schematic captures (WO-10.9, D350, D351) -------------------------------------------------

type Kind = Literal["json", "schematic"]

# ForgeDirection's ordinals, as BaseMetaTileEntity saves mFacing (a short); 0 down and 1 up
FACING_ORDINALS: dict[int, fp.Facing] = {2: "north", 3: "south", 4: "west", 5: "east"}
# GT's HatchElement at the pin (5.09.54.265) has 15 elements; these are the 8 with a single base
# MTE class in ``implementations``, each with its kind. A placed hatch of exactly one of these
# classes takes that kind; any other class (a subclass such as an ME bus, a TecTech or GT++ hatch,
# or an element such as ExoticEnergy or LaserSource) stays ``Captured``: the DB doesn't hold the
# class tree (D350).
_IMPL = "gregtech.api.metatileentity.implementations."
HATCH_CLASSES: dict[str, str] = {
    _IMPL + "MTEHatchMuffler": "Muffler",
    _IMPL + "MTEHatchMaintenance": "Maintenance",
    _IMPL + "MTEHatchInput": "InputHatch",
    _IMPL + "MTEHatchInputBus": "InputBus",
    _IMPL + "MTEHatchOutput": "OutputHatch",
    _IMPL + "MTEHatchOutputBus": "OutputBus",
    _IMPL + "MTEHatchEnergy": "Energy",
    _IMPL + "MTEHatchDynamo": "Dynamo",
}
_WRITER_TOOL = {
    "worldedit": "worldedit-copy",
    "schematica": "schematica-save",
    "unknown": "schematic-file",
}


def kind(data: bytes) -> Kind:
    """What a capture file is, by its content: a JSON object (``gos-capture``) or NBT (a
    ``.schematic``: gzipped, as WorldEdit and Schematica save it, or plain)."""
    if data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{"):
        return "json"
    if data.startswith((nbt.GZIP_MAGIC, bytes((nbt.COMPOUND, 0)))):
        return "schematic"
    raise CaptureError(
        "neither a gos-capture JSON file nor a .schematic (gzipped NBT): nothing to read"
    )


@dataclass(frozen=True)
class Machine:
    """A ``machine`` row (the DB's MTE catalog), by MTE id."""

    name: str | None
    class_name: str | None
    multiblock: bool


def machines(conn: sqlite3.Connection, mte_ids: Iterable[int]) -> dict[int, Machine]:
    """The ``machine`` rows of ``mte_ids`` (empty when the DB has no machine table)."""
    ids = sorted(set(mte_ids))
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not ids or "machine" not in tables:
        return {}
    out: dict[int, Machine] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        for mte, name, cls, multi in conn.execute(
            "SELECT mte_id, name_en, class_name, is_multiblock FROM machine"
            f" WHERE mte_id IN ({','.join('?' * len(chunk))})",
            chunk,
        ):
            out[int(mte)] = Machine(name, cls, bool(multi))
    return out


def gt_tiles(sc: schematic.Capture) -> dict[fp.Offset, schematic.TileEntity]:
    """The GT machine cells' tile entities by cell, each checked against its cell's block (D351):
    a ``gregtech:gt.blockmachines`` cell's must be GT's ``BaseMetaTileEntity`` with an ``mID`` in
    1..32767 (a GT pipe or cable, ``BaseMetaPipeEntity``, is refused); a tile entity outside the
    box, two at one cell, or a GT tile entity on another block is refused. Other blocks' tile
    entities (a chest, a tank) are left alone: the cell is a block like any other."""
    s = sc.schematic
    seen: set[fp.Offset] = set()
    out: dict[fp.Offset, schematic.TileEntity] = {}
    for t in sc.tile_entities:
        if t.pos is None:
            if t.id in (schematic.GT_TILE, schematic.GT_PIPE_TILE):
                raise CaptureError(
                    f"a {t.id} tile entity (mID {t.mte_id}) has no integer x, y and z: "
                    "the file doesn't say which cell it belongs to"
                )
            continue  # another block's tile entity: its cell is a block like any other
        x, y, z = t.pos
        if not (0 <= x < s.width and 0 <= y < s.height and 0 <= z < s.length):
            raise CaptureError(
                f"a tile entity at {list(t.pos)} is outside the {s.size} box"
            )
        if t.pos in seen:
            raise CaptureError(f"two tile entities at {list(t.pos)}")
        seen.add(t.pos)
        block = s.at(x, y, z).block
        if block != GT_MACHINES:
            if t.id in (schematic.GT_TILE, schematic.GT_PIPE_TILE):
                raise CaptureError(
                    f"a {t.id} tile entity at {list(t.pos)} stands on {block}, not "
                    f"{GT_MACHINES}: the file's blocks and tile entities disagree"
                )
            continue
        if t.id == schematic.GT_PIPE_TILE:
            raise CaptureError(
                f"the {GT_MACHINES} cell {list(t.pos)} is a GT pipe or cable "
                f"({schematic.GT_PIPE_TILE}, mID {t.mte_id}): a capture holds a multi's "
                "controller, hatches and blocks; capture it without the pipe"
            )
        if t.id != schematic.GT_TILE:
            raise CaptureError(
                f"the {GT_MACHINES} cell {list(t.pos)} has a {t.id or 'nameless'} tile "
                f"entity, not GT's {schematic.GT_TILE}"
            )
        if t.mte_id is None or not 0 < t.mte_id <= fp.MAX_META:
            raise CaptureError(
                f"the {GT_MACHINES} cell {list(t.pos)} has no MTE id (mID "
                f"{t.mte_id!r} in its {schematic.GT_TILE})"
            )
        out[t.pos] = t
    return out


def _in_box(s: schematic.Schematic, p: fp.Offset) -> bool:
    return 0 <= p[0] < s.width and 0 <= p[1] < s.height and 0 <= p[2] < s.length


def from_schematic(
    sc: schematic.Capture,
    catalog: Mapping[int, Machine],
    *,
    controller_at: fp.Offset | None = None,
    facing: fp.Facing | None = None,
    trigger_stack_size: int = 1,
    channels: Mapping[str, int] | None = None,
) -> Capture:
    """A capture from a read ``.schematic`` (D350). The controller is the one GT tile entity
    whose ``mID`` ``catalog`` (the DB's ``machine`` rows) lists as a multiblock controller, or
    the cell ``controller_at`` names (box coordinates, as ``gos schematic read`` lists the tile
    entities). Its front is the controller's saved ``mFacing``, or ``facing`` when the NBT has
    no horizontal one (a ``facing`` that disagrees with the saved one is refused). The
    controller's cell must hold a GT machine the catalog doesn't call a non-multiblock, and must
    not be rotated or flipped (StructureLib's ``eRotation``/``eFlip``). The cells then go through
    :func:`from_cells`, which refuses what the JSON path refuses (D351)."""
    s = sc.schematic
    tiles = gt_tiles(sc)
    cells: dict[fp.Offset, tuple[str, int]] = {}
    for y in range(s.height):
        for z in range(s.length):
            for x in range(s.width):
                st = s.at(x, y, z)
                if st.block != AIR:
                    cells[(x, y, z)] = (st.block, st.meta)
    if not cells:
        raise CaptureError("the capture holds only air")
    mte = {p: int(t.mte_id or 0) for p, t in tiles.items()}
    if controller_at is None:
        found = sorted(
            p for p, m in mte.items() if m in catalog and catalog[m].multiblock
        )
        if len(found) != 1:
            listed = "; ".join(
                f"{list(p)} mID {mte[p]} {catalog[mte[p]].name or '?'}" for p in found
            )
            raise CaptureError(
                (
                    f"{len(found)} tile entities are multiblock controllers in the DB's "
                    f"machine table ({listed})"
                    if found
                    else "no tile entity is a multiblock controller in the DB's machine table"
                )
                + ": name the controller's cell with --controller x,y,z"
            )
        controller_at = found[0]
    if controller_at not in cells:
        where = "air" if _in_box(s, controller_at) else f"outside the {s.size} box"
        raise CaptureError(f"the controller's cell {list(controller_at)} is {where}")
    tile = tiles.get(controller_at)
    if tile is None:
        raise CaptureError(
            f"the controller's cell {list(controller_at)} is {s.at(*controller_at).key}, not "
            f"a GT machine ({GT_MACHINES} with a {schematic.GT_TILE} and its mID): check "
            "--controller against the tile entities `gos schematic read` lists"
        )
    picked = catalog.get(int(tile.mte_id or 0))
    if picked is not None and not picked.multiblock:
        raise CaptureError(
            f"the controller's cell {list(controller_at)} holds mID {tile.mte_id} "
            f"{picked.name or '?'}, which the DB's machine table says is not a multiblock "
            "controller: check --controller"
        )
    if tile.rotation or tile.flip:
        raise CaptureError(
            f"the controller at {list(controller_at)} is rotated or flipped (eRotation "
            f"{tile.rotation}, eFlip {tile.flip}): rotate it back to normal with the wrench, "
            "build the projection again and capture again"
        )
    raw = tile.facing
    saved = None if raw is None else FACING_ORDINALS.get(raw)
    if saved is not None and facing is not None and saved != facing:
        raise CaptureError(
            f"the controller's saved facing is {saved} (mFacing {raw}), not {facing}: "
            "drop --facing, or check you named the right controller"
        )
    front = saved or facing
    if front is None:
        why = (
            f"its mFacing is {raw} (up or down)"
            if raw is not None
            else "the file saved no facing for it"
        )
        raise CaptureError(
            f"which way does the controller's front face? {why}: give --facing "
            + "|".join(fp.FACINGS)
        )
    info = catalog.get(mte.get(controller_at, 0))
    kinds: dict[fp.Offset, tuple[str, ...]] = {}
    for p, m in mte.items():
        hatch = catalog.get(m)
        found_kind = HATCH_CLASSES.get(hatch.class_name or "") if hatch else None
        if p != controller_at and found_kind is not None:
            kinds[p] = (found_kind,)
    return from_cells(
        cells,
        controller_at=controller_at,
        facing=front,
        mte_ids=mte,
        hatch_kinds=kinds,
        name=info.name if info else None,
        class_name=info.class_name if info else None,
        trigger_stack_size=trigger_stack_size,
        channels=channels,
        tool=_WRITER_TOOL[sc.writer],
    )


@dataclass(frozen=True)
class SchematicOptions:
    """What ``gos footprints capture|compare`` passes for a ``.schematic`` (refused with a
    ``gos-capture`` file, which says them itself)."""

    controller_at: fp.Offset | None = None
    facing: fp.Facing | None = None
    trigger_stack_size: int | None = None
    channels: Mapping[str, int] | None = None
    source_ids: Mapping[int, str] | None = None

    def given(self) -> bool:
        return any(
            v is not None
            for v in (
                self.controller_at,
                self.facing,
                self.trigger_stack_size,
                self.channels,
                self.source_ids,
            )
        )


def read_any(
    path: Path,
    conn: sqlite3.Connection | None,
    options: SchematicOptions | None = None,
) -> Capture:
    """A capture file of either kind (:func:`kind`), read once through ``safe_open``. A
    ``.schematic`` needs ``conn`` (the DB's ``machine`` table) to find and name its controller,
    unless ``options.controller_at`` names the cell."""
    opts = options or SchematicOptions()
    data = _read_file(path)
    if kind(data) == "json":
        if opts.given():
            raise CaptureError(
                "--controller, --facing, --trigger, --channel and --source-world are for a "
                ".schematic; a gos-capture file says them itself"
            )
        return parse(data)
    try:
        sc = schematic.read(data, opts.source_ids)
    except schematic.CaptureError as exc:
        raise CaptureError(f"not a readable .schematic: {exc}") from None
    if conn is None and opts.controller_at is None:
        raise CaptureError(
            "finding the controller needs the DB's machine table: run `gos db build`, or "
            "name the controller's cell with --controller x,y,z"
        )
    gt = gt_tiles(sc)
    catalog = (
        machines(conn, (int(t.mte_id or 0) for t in gt.values()))
        if conn is not None
        else {}
    )
    try:
        return from_schematic(
            sc,
            catalog,
            controller_at=opts.controller_at,
            facing=opts.facing,
            trigger_stack_size=1
            if opts.trigger_stack_size is None
            else opts.trigger_stack_size,
            channels=opts.channels,
        )
    except fp.FootprintError as exc:
        raise CaptureError(str(exc)) from None


def _obj(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CaptureError(f"{what} is not an object")
    return value


def _list(value: object, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise CaptureError(f"{what} is not a list")
    return value


@dataclass(frozen=True)
class Loaded:
    run_id: int
    footprint: fp.Footprint  # facing north
    replaced: bool  # an earlier capture of this controller and variant was replaced


def load(
    conn: sqlite3.Connection,
    capture: Capture,
    path: Path | str,
    *,
    now: datetime | None = None,
) -> Loaded:
    """Write ``capture`` as a ``lab-capture`` structure row (replacing an earlier capture of the
    same controller and variant) and its provenance as a ``structure_run`` row, in one
    transaction. Refused when the extractor's rows already hold the controller: captures are
    for the multis the extractor can't reach (D306)."""
    footprint = fp.build(capture.row())  # checks the capture makes a footprint first
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    conn.execute("BEGIN IMMEDIATE")
    try:
        structure_db.ensure_schema(conn)
        have = conn.execute(
            "SELECT count(*) FROM structure WHERE registry_name = ? AND meta = ?"
            " AND source = ?",
            (capture.registry_name, capture.meta, fp.EXTRACTOR),
        ).fetchone()[0]
        if have:
            raise CaptureError(
                f"the extractor's dump already holds {capture.controller}; its footprint comes "
                "from there, so a capture isn't loaded"
            )
        cur = conn.execute(
            "INSERT INTO structure_run(loaded_at, source, dataset, controllers, failures_json,"
            " rejected_json) VALUES (?,?,?,?,?,?)",
            (stamp, fp.CAPTURE, f"{capture.tool}: {path}", 1, "[]", "[]"),
        )
        run_id = int(cur.lastrowid or 0)
        old = conn.execute(
            "DELETE FROM structure WHERE registry_name = ? AND meta = ? AND variant = ?"
            " AND source = ?",
            (capture.registry_name, capture.meta, capture.variant, fp.CAPTURE),
        ).rowcount
        size = capture.row().size
        conn.execute(
            "INSERT INTO structure(machine_id, variant, size_x, size_y, size_z, blocks_json,"
            " schematic_path, source, run_id, registry_name, meta, name_en, class_name,"
            " trigger_stack_size, channels_json, hatches_json, subs_json, failures_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _machine_id(conn, capture),
                capture.variant,
                *size,
                _json(
                    [
                        {"d": list(d), "block": b, "meta": m}
                        for d, b, m in capture.blocks
                    ]
                ),
                None,
                fp.CAPTURE,
                run_id,
                capture.registry_name,
                capture.meta,
                capture.name,
                capture.class_name,
                capture.trigger_stack_size,
                _json(dict(sorted(capture.channels.items()))),
                _json(
                    [
                        {"d": list(d), "kinds": list(k)} | ({"placed": p} if p else {})
                        for d, k, p in capture.hatches
                    ]
                ),
                "{}",
                "[]",
            ),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return Loaded(run_id, footprint, bool(old))


def _machine_id(conn: sqlite3.Connection, capture: Capture) -> int | None:
    """``machine.id`` of a GT controller (by its MTE id), when the DB has a machine table."""
    if capture.registry_name != GT_MACHINES:
        return None
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "machine" not in tables:
        return None
    row = conn.execute(
        "SELECT id FROM machine WHERE mte_id = ?", (capture.meta,)
    ).fetchone()
    return None if row is None else int(row[0])


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Compared:
    footprint: fp.Footprint  # the library's, at the tiers the capture holds
    differences: tuple[str, ...]  # one line per cell that differs


def compare(capture: Capture, lib: fp.Library) -> Compared:
    """A capture of a multi the library has (L1: build it with the projector in the Lab, then
    capture it) against its footprint: the cells that differ. A tier channel is taken at the
    value whose block the capture holds. A hatch cell matches a placed hatch, its fallback
    block, or nothing (a hatch-only cell); any other cell must hold the footprint's block."""
    found = lib.for_controller(capture.registry_name, capture.meta)
    if not found:
        raise CaptureError(f"the library has no footprint for {capture.controller}")
    if capture.variant not in {f.variant for f in found}:
        variants = ", ".join(f.variant for f in found)
        raise CaptureError(
            f"{capture.controller} has no variant {capture.variant!r} (its variants: "
            f"{variants}); set trigger_stack_size and channels as built"
        )
    base = next(f for f in found if f.variant == capture.variant)
    held = {f"{b}@{m}" for _, b, m in capture.blocks}
    tiers = {}
    for channel, options in base.choices.items():
        values = [t.value for t in options if t.key in held]
        if len(values) == 1:
            tiers[channel] = values[0]
    try:
        f = lib.get(capture.registry_name, capture.meta, capture.variant, tiers=tiers)
    except fp.FootprintError:
        f = base
    got = {d: f"{b}@{m}" for d, b, m in capture.blocks}
    placed = {d for d, _, p in capture.hatches if p}
    diffs: list[str] = []
    for c in f.cells:
        have = got.pop(c.d, None)
        if c.hatches and (c.d in placed or have in (None, c.key)):
            continue
        if have != c.key:
            want = c.key or "nothing (a hatch cell)"
            diffs.append(f"{list(c.d)}: footprint {want}, capture {have or 'air'}")
    for d in sorted(placed - {c.d for c in f.cells}):
        diffs.append(f"{list(d)}: footprint air, capture a hatch")
    for d, key in sorted(got.items()):
        diffs.append(f"{list(d)}: footprint air, capture {key}")
    return Compared(f, tuple(sorted(diffs)))
