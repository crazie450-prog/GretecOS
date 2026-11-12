"""A saved plan's outputs over HTTP (WO-3.6; plan 7.1 step 9, 7.12, 7.14).

| route                               | who                         | what                          |
|-------------------------------------|-----------------------------|-------------------------------|
| GET /api/plans/{id}/cards           | admin, client, device       | ``{"flow": card, "bom": card}`` (catalog cards, D160) |
| GET /api/plans/{id}/export/{format} | admin, client, device       | the export file: ``bom`` (TSV), ``mermaid``, ``png``, ``gtnhplanner`` (JSON), ``gtnhflow`` (YAML) |

The dashboard draws the flow card (React Flow + elkjs) and the BOM card; Discord (WO-9.x, the
client token ``discord``) attaches the PNG and the BOM (D53). A plan is one ``gos plan --save``
(or WO-3.7's designer) saved under ``GOS_HOME/data/plans``; nothing here solves or writes.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from gregtec.api.security import Principal
from gregtec.packupdate.plans import PLAN_ID
from gregtec.planner.outputs import FILES  # a plain dict: imports no planner code

if TYPE_CHECKING:
    from gregtec.api.app import Core

MEDIA = {
    "bom": "text/tab-separated-values; charset=utf-8",
    "mermaid": "text/plain; charset=utf-8",
    "png": "image/png",
    "gtnhplanner": "application/json",
    "gtnhflow": "application/yaml",
}  # one per FILES format


def _pid(text: str) -> str:
    if not PLAN_ID.fullmatch(text):
        raise HTTPException(400, "not a plan id")
    return text


def cards(home: Path, pid: str) -> dict[str, Any]:
    """The plan's flow and BOM cards, from its saved JSON and the DB it was solved on."""
    from gregtec.db import access
    from gregtec.planner.outputs import book, store
    from gregtec.planner.outputs import cards as plan_cards

    try:
        plan = store.load(home, pid)
    except (OSError, ValueError) as exc:
        raise HTTPException(404, f"plan {pid} isn't saved") from exc
    db = access.db_path(home)
    if not db.is_file():
        raise HTTPException(503, "no recipe DB: run gos db build")
    conn = access.connect(db, readonly=True)
    try:
        b = book.read(conn, plan)
    except (LookupError, sqlite3.Error) as exc:
        raise HTTPException(409, f"plan {pid}: {exc}") from exc
    finally:
        conn.close()
    out = {"flow": plan_cards.flow_card(plan, b), "bom": plan_cards.bom_card(plan)}
    if "effort" in plan.get("metrics", {}):
        out["effort"] = plan_cards.effort_card(plan)  # WO-4.3, D237
    return out


def add_routes(
    app: FastAPI, core: "Core", principal: Callable[[Request], Principal]
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]

    @app.get("/api/plans/{plan_id}/cards")
    async def plan_cards(plan_id: str, who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(cards, core.cfg.gos_home, _pid(plan_id))

    @app.get("/api/plans/{plan_id}/export/{fmt}")
    def plan_file(plan_id: str, fmt: str, who: Anyone) -> FileResponse:
        if fmt not in FILES:
            raise HTTPException(404, f"format must be one of {', '.join(FILES)}")
        name, media = FILES[fmt], MEDIA[fmt]
        pid = _pid(plan_id)
        path = core.cfg.gos_home / "data" / "plans" / pid / name
        if not path.is_file():
            raise HTTPException(404, f"plan {pid} has no saved {fmt}")
        return FileResponse(path, media_type=media, filename=f"{pid}-{name}")
