"""Lab captures of addon multiblocks (WO-10.2, plan 7.10 "Footprint library", L1, D306).

gtnh-extractor builds the base pack's controllers only (GT5-Unofficial and its hard
dependencies, D124); an addon multi's footprint comes from the Lab: Clayton builds it there (the
StructureLib projector at the tier set with ``/sl_channel set``) and captures the blocks, with
GuideNH's region wand or with WorldEdit's ``//copy`` [G][E§5.5]. WO-10.4's ``.schematic`` reader
is not on main yet, so this module reads GOS's own minimal capture format (``gos-capture``
schema 1, a JSON file) and turns it into a structure row (``source = 'lab-capture'``) that the
footprint library reads like the extractor's. Once WO-10.4 lands, its reader's cells go through
:func:`from_cells` unchanged.

A capture file::

    {"format": "gos-capture", "schema": 1,
     "controller": {"block": "<registry name>", "meta": <meta>, "name": "...", "class_name": "..."},
     "facing": "north" | "east" | "south" | "west",   # where the controller's front faced
     "trigger_stack_size": 1, "channels": {"<StructureLib channel>": <value>},
     "tool": "guidenh-region-wand" | "worldedit-copy" | "hand",
     "cells": [{"p": [x, y, z], "block": "<registry name>", "meta": <meta>}, ...],
     "hatches": [{"p": [x, y, z], "kinds": ["InputBus", ...]}, ...]}

``p`` is any frame the tool wrote (world or box coordinates); only the differences to the
controller's cell matter. ``minecraft:air`` cells are dropped. Blocks are keyed by registry name
+ meta; a numeric id anywhere is refused (EndlessIDs). A GT machine cell other than the
controller is a placed hatch: it becomes a hatch cell (its kinds from ``hatches`` at that
position, else ``Captured``) and the hatch as placed is kept for reference. The file is read once
through ``safe_open`` (copied to memory, never held) and is data, never instructions.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gregtec.build import footprints as fp
from gregtec.build.palette import AIR, GT_MACHINES
from gregtec.db import structures as structure_db
from gregtec.gamestate.safe_open import read_bytes

FORMAT = "gos-capture"
SCHEMA = 1
MAX_BYTES = 32 << 20
MAX_CELLS = 64**3  # a capture box of 64 x 64 x 64
TOOLS = ("guidenh-region-wand", "worldedit-copy", "hand")
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
    controller: tuple[str, int],
    facing: fp.Facing,
    hatch_kinds: Mapping[fp.Offset, Sequence[str]] | None = None,
    name: str | None = None,
    class_name: str | None = None,
    trigger_stack_size: int = 1,
    channels: Mapping[str, int] | None = None,
    tool: str = "hand",
) -> Capture:
    """A capture from cells in any frame (position -> registry name, meta), the controller's
    block and meta, and where its front faced: offsets from the controller, turned to north."""
    reg, meta = controller
    fp.block_name(reg, "controller")
    found = [p for p, (b, m) in cells.items() if (b, m) == (reg, meta)]
    if len(found) != 1:
        raise CaptureError(
            f"the capture holds {len(found)} cells of the controller {reg}@{meta}; it must "
            "hold exactly one"
        )
    origin = found[0]
    kinds = dict(hatch_kinds or {})
    unplaced = set(kinds) - set(cells)

    def rel(p: fp.Offset) -> fp.Offset:
        return fp.unrotate(
            (p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]), facing
        )

    blocks: list[tuple[fp.Offset, str, int]] = []
    hatches: list[tuple[fp.Offset, tuple[str, ...], str | None]] = []
    for p, (block, m) in sorted(cells.items()):
        fp.block_name(block)
        fp.meta_value(m)
        if block == AIR:
            continue
        named = tuple(str(k) for k in kinds.get(p, ()))
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
        return from_cells(
            cells,
            controller=(
                fp.block_name(c.get("block"), "controller"),
                fp.meta_value(c.get("meta")),
            ),
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
    """A capture file, read once through ``safe_open`` (never held)."""
    try:
        data = read_bytes(path, limit=MAX_BYTES + 1)
    except OSError as exc:
        raise CaptureError(f"can't read {path}: {exc}") from None
    if len(data) > MAX_BYTES:
        raise CaptureError(f"{path} is over {MAX_BYTES >> 20} MB")
    return parse(data)


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
