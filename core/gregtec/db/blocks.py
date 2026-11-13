"""The block catalog's tables (WO-10.3, D307), written by ``gos db build``, and its reads.

From a run's ``palette.jsonl`` and ``textures.jsonl`` (:mod:`gregtec.ingest.palette`):
``block_state`` (every placeable block state, keyed by registry name + meta, with the item that
places it and Forge's ``hasTileEntity(meta)``) and ``block_texture`` (each face icon's PNG and
average colour). Like recipes, the tables hold one set per export run; pruning a run deletes its
rows. The catalog GOS reads is the newest run that has one (:func:`catalog_run`).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from gregtec.ingest import palette as palette_ingest

TABLES = ("block_state", "block_texture")


def delete(conn: sqlite3.Connection, run_ids: Sequence[int]) -> None:
    for run_id in run_ids:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))


def _bit(v: bool | None) -> int | None:
    return None if v is None else int(v)


def write(
    conn: sqlite3.Connection, run_id: int, data: palette_ingest.Palette | None
) -> int:
    """Replace ``run_id``'s catalog with ``data`` (none for a run without one). Returns the
    number of block states written."""
    delete(conn, [run_id])
    if data is None:
        return 0
    conn.executemany(
        "INSERT INTO block_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                run_id,
                s.block,
                s.meta,
                s.item or "",
                s.item_meta,
                s.name,
                s.unlocalized,
                s.mod,
                int(s.has_te),
                _bit(s.opaque),
                _bit(s.full_cube),
                _bit(s.normal_cube),
                _bit(s.standard_render),
                _bit(s.liquid),
                int(s.falls),
                s.light,
                s.light_opacity,
                s.tab,
                int(s.hidden),
                json.dumps(list(s.faces)),
                json.dumps(list(s.errors)) if s.errors else None,
            )
            for s in data.states
        ],
    )
    conn.executemany(
        "INSERT INTO block_texture VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (run_id, t.icon, t.path, t.sha256, t.width, t.height, t.rgb, t.alpha, t.error)
            for t in data.textures
        ],
    )
    return len(data.states)


def catalog_run(conn: sqlite3.Connection) -> tuple[int, str] | None:
    """(export_run.id, run key) of the newest run with a block catalog, or None."""
    row = conn.execute(
        "SELECT r.id, r.run_key FROM export_run r WHERE EXISTS"
        " (SELECT 1 FROM block_state b WHERE b.run_id = r.id) ORDER BY r.id DESC LIMIT 1"
    ).fetchone()
    return None if row is None else (int(row[0]), str(row[1]))


@dataclass(frozen=True)
class State:
    """One block state of the catalog, every item variant that places it merged: ``has_te``
    when any of them says so (Forge asks by meta, so they agree)."""

    block: str
    meta: int
    name: str | None
    mod: str
    has_te: bool
    opaque: bool | None
    full_cube: bool | None
    falls: bool
    liquid: bool | None
    light: int | None
    hidden: bool  # every variant is out of the creative tabs

    @property
    def key(self) -> str:
        return f"{self.block}@{self.meta}"


_STATE = (
    "SELECT registry_name, meta, MIN(name), MIN(mod_id), MAX(has_te), MIN(opaque),"
    " MIN(full_cube), MAX(falls), MAX(liquid), MAX(light), MIN(hidden)"
    " FROM block_state WHERE run_id = ? AND registry_name = ? AND meta = ?"
    " GROUP BY registry_name, meta"
)


def _opt(v: object) -> bool | None:
    return None if v is None else bool(v)


def state(conn: sqlite3.Connection, run_id: int, block: str, meta: int) -> State | None:
    """``block``@``meta`` in run ``run_id``'s catalog, or None."""
    row = conn.execute(_STATE, (run_id, block, meta)).fetchone()
    if row is None:
        return None
    return State(
        block=row[0],
        meta=int(row[1]),
        name=row[2],
        mod=row[3],
        has_te=bool(row[4]),
        opaque=_opt(row[5]),
        full_cube=_opt(row[6]),
        falls=bool(row[7]),
        liquid=_opt(row[8]),
        light=row[9],
        hidden=bool(row[10]),
    )


def metas(conn: sqlite3.Connection, run_id: int, block: str) -> list[int]:
    """The metas run ``run_id``'s catalog has for ``block`` (empty: the block isn't in it)."""
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT DISTINCT meta FROM block_state WHERE run_id = ? AND registry_name = ?"
            " ORDER BY meta",
            (run_id, block),
        )
    ]


def counts(conn: sqlite3.Connection, run_id: int) -> dict[str, int]:
    """Block states (distinct registry name + meta), blocks, tile-entity states and mods."""
    row = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT registry_name), COALESCE(SUM(te), 0),"
        " COUNT(DISTINCT mod_id) FROM (SELECT registry_name, meta, MAX(has_te) AS te,"
        " MIN(mod_id) AS mod_id FROM block_state WHERE run_id = ?"
        " GROUP BY registry_name, meta)",
        (run_id,),
    ).fetchone()
    return {
        "states": int(row[0]),
        "blocks": int(row[1]),
        "tile_entity_states": int(row[2]),
        "mods": int(row[3]),
    }
