"""The 3D build guide's data: one multiblock footprint laid out for building (WO-10.8, plan 7.10
"Viewer features", D35, D304, D319, D320).

Everything the Build view's guide shows comes from here, by position, from the footprint library
(WO-10.2, :mod:`gregtec.build.footprints`) and the block catalog (WO-10.3,
:mod:`gregtec.db.blocks`): never from GuideNH's export, whose hover gives a position and a
tooltip, not ``registry@meta`` (D304). The dashboard only draws it.

- **Cells** as ``[x, y, z, block, slot]`` from the footprint's low corner (``y`` 0 the bottom
  layer, x east, z south, the facing chosen): ``block`` an index into ``palette`` (-1 for a
  hatch-only cell, air until a hatch goes in) and ``slot`` an index into ``slots`` (the hatch
  kinds the cell accepts) or -1. Blocks are keyed by registry name + meta, never a numeric id.
- **Palette = the BOM per structure**: each ``registry@meta`` with its catalog name, the top and
  side faces' average colours (the catalog's ``block_texture.rgb``; no texture file is copied or
  committed) and how many cells hold it, controller included. The controller is a
  ``gregtech:gt.blockmachines`` block: it is built from its item, never pasted (D35).
- **Layers**: per ``y`` the materials it takes (``registry@meta`` -> count), its block and
  hatch-cell counts. The per-layer counts add up to the footprint's own materials (tested).
- **Checklist** (D320): one item per layer, kept in the core's ``kv`` table per footprint shape
  (controller, variant and tiers; the facing doesn't change what is built), so it survives a
  reload and is the same on monitor 2 and the phone.

Read-only on the game DB; the checklist writes only its own ``kv`` row through the core's store.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from gregtec.build import footprints as fp
from gregtec.db import blocks as block_db
from gregtec.store import Store

KV_PREFIX = "build.checklist|"
MAX_KEY = 400  # a checklist key's length (a footprint key is far shorter)
_ITEM = re.compile(r"layer:(0|[1-9][0-9]{0,2})")
SIDES = ("down", "up", "north", "south", "west", "east")  # the catalog's face order


@dataclass(frozen=True)
class Look:
    """What the catalog says about one ``registry@meta``: its name and two faces' colours."""

    name: str | None
    top: str | None  # "#rrggbb", the up face's average colour
    side: str | None  # the north face's


def looks(conn: sqlite3.Connection, keys: Iterable[str]) -> dict[str, Look]:
    """``registry@meta`` -> :class:`Look` from the newest block catalog, for the keys it has.
    Empty when there is no catalog yet (``gos db build`` after a client export run)."""
    run = block_db.catalog_run(conn)
    if run is None:
        return {}
    out: dict[str, Look] = {}
    for key in sorted(set(keys)):
        block, _, meta = key.rpartition("@")
        row = conn.execute(
            "SELECT name, faces FROM block_state WHERE run_id = ? AND registry_name = ?"
            " AND meta = ? ORDER BY item_name, item_meta LIMIT 1",
            (run[0], block, int(meta)),
        ).fetchone()
        if row is None:
            continue
        try:
            faces = json.loads(row[1])
        except (TypeError, ValueError):
            faces = []
        colours: list[str | None] = []
        for side in (1, 2):  # up, north
            icon = (
                faces[side] if isinstance(faces, list) and len(faces) > side else None
            )
            rgb = None
            if isinstance(icon, str):
                t = conn.execute(
                    "SELECT rgb FROM block_texture WHERE run_id = ? AND icon = ?",
                    (run[0], icon),
                ).fetchone()
                rgb = t[0] if t is not None else None
            colours.append(rgb)
        out[key] = Look(row[0], colours[0], colours[1])
    return out


def checklist_key(f: fp.Footprint) -> str:
    """The checklist's key: the footprint's shape (controller, variant, tiers), not its facing."""
    tiers = "".join(f";{c}={v}" for c, v in f.tiers)
    return f"{f.controller}|{f.variant}{tiers}"


def items(f: fp.Footprint) -> list[dict[str, Any]]:
    """The checklist: one item per layer, bottom up, with what it takes."""
    lo = f.lo
    out = []
    for y, layer in sorted(_by_layer(f).items()):
        blocks = sum(layer.values())
        hatch = sum(1 for c in f.cells if c.d[1] - lo[1] == y and c.block is None)
        parts = [f"{blocks} {'block' if blocks == 1 else 'blocks'}"]
        if hatch:
            parts.append(f"{hatch} hatch-only {'cell' if hatch == 1 else 'cells'}")
        out.append({"id": f"layer:{y}", "label": f"Layer {y + 1}: {', '.join(parts)}"})
    return out


def _by_layer(f: fp.Footprint) -> dict[int, dict[str, int]]:
    """y (from the bottom, 0) -> ``registry@meta`` -> cells; every layer with a cell is there."""
    lo = f.lo
    out: dict[int, dict[str, int]] = {}
    for c in f.cells:
        layer = out.setdefault(c.d[1] - lo[1], {})
        if c.key is not None:
            layer[c.key] = layer.get(c.key, 0) + 1
    return {y: dict(sorted(m.items())) for y, m in out.items()}


def guide(
    f: fp.Footprint,
    catalog: Mapping[str, Look],
    variants: Iterable[fp.Footprint] = (),
    done: Iterable[str] = (),
) -> dict[str, Any]:
    """The guide's JSON for footprint ``f`` (:mod:`this module's docstring <gregtec.build.guide_data>`).
    ``variants`` are the controller's other footprints (for the picker), ``done`` the checklist
    items already ticked."""
    lo = f.lo
    materials = f.materials()
    palette_keys = list(materials)
    index = {k: i for i, k in enumerate(palette_keys)}
    slot_sets: list[tuple[str, ...]] = []
    slot_index: dict[tuple[str, ...], int] = {}
    cells: list[list[int]] = []
    for c in f.cells:
        slot = -1
        if c.hatches:
            kinds = tuple(sorted(c.hatches))
            if kinds not in slot_index:
                slot_index[kinds] = len(slot_sets)
                slot_sets.append(kinds)
            slot = slot_index[kinds]
        block = index[c.key] if c.key is not None else -1
        cells.append([c.d[0] - lo[0], c.d[1] - lo[1], c.d[2] - lo[2], block, slot])
    by_layer = _by_layer(f)
    layers = []
    for y, layer in sorted(by_layer.items()):
        in_layer = [c for c in f.cells if c.d[1] - lo[1] == y]
        layers.append(
            {
                "y": y,
                "blocks": sum(layer.values()),
                "hatch_cells": sum(1 for c in in_layer if c.hatches),
                "materials": layer,
            }
        )
    slot_cells = [0] * len(slot_sets)
    for c in cells:
        if c[4] >= 0:
            slot_cells[c[4]] += 1
    looked = {k: catalog.get(k) for k in palette_keys}
    item_ids = {i["id"] for i in items(f)}
    return {
        "key": f.key,
        "checklist_key": checklist_key(f),
        "controller": f.controller,
        "name": f.name,
        "ref": f"mte:{f.mte}" if f.mte is not None else None,
        "source": f.source,
        "variant": f.variant,
        "variants": [
            {"variant": v.variant, "size": list(v.size)}
            for v in sorted(variants, key=lambda v: v.variant)
        ],
        "facing": f.facing,
        "front": list(fp.FRONT[f.facing]),
        "tiers": dict(f.tiers),
        "choices": {
            ch: [{"value": t.value, "block": t.key} for t in tiers]
            for ch, tiers in sorted(f.choices.items())
        },
        "caveats": list(f.caveats),
        "size": list(f.size),
        "controller_at": [-lo[0], -lo[1], -lo[2]],
        "palette": [
            {
                "block": k,
                "name": look.name if look else None,
                "top": look.top if look else None,
                "side": look.side if look else None,
                "count": materials[k],
            }
            for k, look in looked.items()
        ],
        "slots": [
            {"kinds": list(kinds), "cells": n}
            for kinds, n in zip(slot_sets, slot_cells, strict=True)
        ],
        "cells": cells,
        "layers": layers,
        "checklist": items(f),
        "done": sorted(set(done) & item_ids),
        "catalog": bool(catalog),
    }


def listing(lib: fp.Library) -> list[dict[str, Any]]:
    """One entry per controller with a footprint: what the Build view's picker and the "Build
    guide" links on the Designer and My base use (by ``mte`` for a GT controller)."""
    by: dict[str, list[fp.Footprint]] = {}
    for f in lib.footprints:
        by.setdefault(f.controller, []).append(f)
    out = []
    for controller, fs in sorted(by.items()):
        first = fs[0]
        out.append(
            {
                "controller": controller,
                "name": first.name,
                "mte": first.mte,
                "source": first.source,
                "variants": sorted(f.variant for f in fs),
            }
        )
    return out


# --- the checklist (D320) ------------------------------------------------------------------------


def checked(store: Store, key: str) -> list[str]:
    rows = store.rows("SELECT value FROM kv WHERE key = ?", (KV_PREFIX + key,))
    if not rows:
        return []
    try:
        value = json.loads(rows[0]["value"])
    except (TypeError, ValueError):
        return []
    return (
        sorted(i for i in value if isinstance(i, str))
        if isinstance(value, list)
        else []
    )


def check(store: Store, f: fp.Footprint, item: str, done: bool) -> list[str]:
    """Tick (or untick) ``item`` of ``f``'s checklist; the items now ticked. An item the
    footprint has no layer for is refused (``ValueError``)."""
    if not _ITEM.fullmatch(item) or item not in {i["id"] for i in items(f)}:
        raise ValueError(f"{item!r} is not an item of this guide's checklist")
    key = checklist_key(f)
    if len(key) > MAX_KEY:
        raise ValueError("the footprint's key is too long to keep a checklist for")
    with store.transaction() as conn:
        row = conn.execute(
            "SELECT value FROM kv WHERE key = ?", (KV_PREFIX + key,)
        ).fetchone()
        try:
            now = set(json.loads(row[0])) if row is not None else set()
        except (TypeError, ValueError):
            now = set()
        now = {i for i in now if isinstance(i, str)}
        if done:
            now.add(item)
        else:
            now.discard(item)
        if now:
            conn.execute(
                "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO "
                "UPDATE SET value = excluded.value, upd = excluded.upd",
                (KV_PREFIX + key, json.dumps(sorted(now)), int(time.time())),
            )
        else:
            conn.execute("DELETE FROM kv WHERE key = ?", (KV_PREFIX + key,))
    return sorted(now)


def reset(store: Store, f: fp.Footprint) -> None:
    """Untick every item of ``f``'s checklist (the next one of the same multi)."""
    with store.transaction() as conn:
        conn.execute("DELETE FROM kv WHERE key = ?", (KV_PREFIX + checklist_key(f),))
