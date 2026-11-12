"""The Ores view's payload and routes (plan 7.14 "Ores: where's the nearest X?", 7.6; WO-6.6).

| route                     | who                    | what                                       |
|---------------------------|------------------------|--------------------------------------------|
| GET  /api/ores            | admin, client, device  | the view: where he is, the loaded veins'   |
|                           |                        | world, materials, and with ``material`` an |
|                           |                        | ore list card (``dim`` optional)           |
| POST /api/ores/waypoint   | admin, client, device  | propose a vein's waypoint by its ``vein:`` |
|                           |                        | id; Clayton confirms it on the approval    |
|                           |                        | card or by voice (D59)                     |

The list is :func:`gregtec.ores.intel.find`'s order (code ranks, D142 (c)); the view only draws
it. The waypoint is built from the DB row by the core, never from what the browser sends, and
only proposed: the action desk reads it back and writes the file after his confirm (D272).
"""

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from gregtec import config
from gregtec.api.security import Principal
from gregtec.db import access
from gregtec.db import ores as ore_db
from gregtec.ores import intel

if TYPE_CHECKING:
    from gregtec.api.app import Core
    from gregtec.bridge.actions import ActionDesk

MATERIAL_MAX = 100


def _dims(src: dict[str, Any], names: dict[int, str]) -> list[dict[str, Any]]:
    per = src.get("per_dim")
    out = []
    for key, counts in (per if isinstance(per, dict) else {}).items():
        try:
            dim = int(key)
        except ValueError:
            continue
        veins = counts.get("veins") if isinstance(counts, dict) else None
        out.append(
            {
                "dim": dim,
                "name": names.get(dim),
                "veins": veins if isinstance(veins, int) else 0,
            }
        )
    return sorted(out, key=lambda d: d["dim"])


def payload(
    conn: sqlite3.Connection,
    cfg: config.Config,
    material: str | None,
    dim: int | None,
    can_mark: bool,
    reader: Callable[[sqlite3.Connection], intel.Position] = intel.position,
) -> dict[str, Any]:
    src = intel.source(conn)
    names = ore_db.dimension_names(conn)
    pos, why = intel.from_player(conn, reader)
    target: str | None = None
    mark_note: str | None = None
    world = src.get("world")
    if isinstance(world, str) and world:
        try:
            target = intel.target_of(cfg, Path(world))
        except intel.OreIntelError as exc:
            mark_note = str(exc)
    else:
        mark_note = "no veins loaded yet: run gos ores load"
    if not can_mark and mark_note is None:
        mark_note = "the action desk isn't running in this core"
    used_dim = dim if dim is not None else (pos.dim if pos is not None else None)
    card = None
    if material:
        near = (pos.x, pos.z) if pos is not None and pos.dim == used_dim else None
        notes = [
            "ranked by code: live veins first, then the material's best layer, then nearest",
            "x and z are each vein's centre block; distance is horizontal, in blocks",
            intel.NAME_NOTE,
        ]
        if near is None:
            notes.insert(
                0,
                f"no distances: {why}"
                if pos is None
                else "no distances: he is in another dimension",
            )
        card = intel.card(material, intel.find(conn, material, used_dim, near), notes)
    return {
        "source": {
            "world": Path(world).name if isinstance(world, str) and world else None,
            "instance": target,
            "loaded_utc": src.get("loaded_utc"),
            "veins": src.get("veins") if isinstance(src.get("veins"), int) else 0,
        },
        "position": None
        if pos is None
        else {
            "x": pos.x,
            "y": pos.y,
            "z": pos.z,
            "dim": pos.dim,
            "dim_name": names.get(pos.dim),
            "world": pos.world,
            "age_s": pos.age_s(),
        },
        "position_note": why,
        "dims": _dims(src, names),
        "materials": intel.materials(conn),
        "material": material,
        "dim": used_dim,
        "card": card,
        "can_mark": can_mark and target is not None,
        "mark_note": mark_note if not (can_mark and target is not None) else None,
    }


def _open(home: Path) -> sqlite3.Connection:
    db = access.db_path(home)
    if not db.is_file():
        raise HTTPException(404, "no ore data yet: run gos ores load")
    return access.connect(db, readonly=True)


def read(
    cfg: config.Config, material: str | None, dim: int | None, can_mark: bool
) -> dict[str, Any]:
    conn = _open(cfg.gos_home)
    try:
        return payload(conn, cfg, material, dim, can_mark)
    finally:
        conn.close()


def mark(cfg: config.Config, desk: "ActionDesk", ref: str) -> dict[str, Any]:
    conn = _open(cfg.gos_home)
    try:
        try:
            out = intel.propose_waypoint(
                cfg, desk, conn, ref, source="dashboard", replace_any=False
            )
        except intel.OreIntelError as exc:
            raise HTTPException(409, str(exc)) from None
    finally:
        conn.close()
    assert out.action is not None
    return {"text": out.text, "action": out.action.as_dict()}


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    json_body: Callable[[Request], Awaitable[dict[str, Any]]],
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]

    @app.get("/api/ores")
    async def ores(
        _: Anyone,
        material: Annotated[str | None, Query(max_length=MATERIAL_MAX)] = None,
        dim: Annotated[int | None, Query(ge=-1000, le=1000)] = None,
    ) -> dict[str, Any]:
        wanted = material.strip() if material else None
        try:
            return await asyncio.to_thread(
                read, core.cfg, wanted or None, dim, core.ops is not None
            )
        except sqlite3.Error as exc:
            raise HTTPException(
                503, f"the DB can't be read now ({type(exc).__name__})"
            ) from None

    @app.post("/api/ores/waypoint")
    async def waypoint(request: Request, _: Anyone) -> dict[str, Any]:
        body = await json_body(request)
        ref = body.get("vein")
        if not isinstance(ref, str) or len(ref) > 40:
            raise HTTPException(400, "send {vein: 'vein:<dim>/<cx>/<cz>'}")
        if core.ops is None:
            raise HTTPException(503, "the action desk isn't running in this core")
        try:
            return await asyncio.to_thread(mark, core.cfg, core.ops.desk, ref)
        except sqlite3.Error as exc:
            raise HTTPException(
                503, f"the DB can't be read now ({type(exc).__name__})"
            ) from None
