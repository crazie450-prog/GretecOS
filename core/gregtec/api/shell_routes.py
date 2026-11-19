"""Decorative shells over HTTP (WO-10.7, plan 7.10, 7.14, D316-D318).

| route               | who                   | what                                                     |
|---------------------|-----------------------|----------------------------------------------------------|
| GET /api/shells     | admin, client, device | the saved shell builds, newest first (summaries)          |
| GET /api/shells/{id}| admin, client, device | ``{"card": shell card}``: preview, manifest, paste steps |
| POST /api/shells    | admin, client         | ``{"plans": [ids], "theme"?, "line_type"?}``: the procedural shell (No-AI mode), saved unless refused; ``{"card"}`` |

The shell card is data (plan 7.14 rule 1); the Build view draws it. Nothing here writes a world or
a ``.schematic``: ``gos shell export`` is Clayton's (it reads the Lab world's registry, D318).

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from gregtec.api.security import Principal

if TYPE_CHECKING:
    from gregtec.api.app import Core

JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def listed(home: Path) -> dict[str, Any]:
    from gregtec.build import shell_store

    return {"shells": shell_store.builds(home)}


def shown(home: Path, build: str) -> dict[str, Any]:
    from gregtec.build import shell, shell_store

    try:
        return {"card": shell_store.card(home, build)}
    except shell.ShellError as exc:
        raise HTTPException(404, str(exc)) from None


def designed(home: Path, body: dict[str, Any]) -> dict[str, Any]:
    """The procedural shell for the plans (no model): saved, or refused with the reasons."""
    from gregtec.build import palette, shell, shell_store
    from gregtec.build import themes as theme_lib
    from gregtec.db import access
    from gregtec.vault.io import VaultIO

    plans = body.get("plans")
    theme = body.get("theme")
    line_type = body.get("line_type", theme_lib.GENERAL)
    if (
        not isinstance(plans, list)
        or not plans
        or not all(isinstance(p, str) and len(p) <= 64 for p in plans)
        or not (theme is None or isinstance(theme, str))
        or not isinstance(line_type, str)
    ):
        raise HTTPException(400, "send plans (a list of plan ids), theme and line_type")
    db = access.db_path(home)
    if not db.is_file():
        raise HTTPException(503, "no recipe DB: run gos db build")
    conn = access.connect(db, readonly=True)
    try:
        made = shell_store.make(
            conn,
            home,
            VaultIO(home / "vault"),
            plans,
            theme_id=theme,
            line_type=line_type,
        )
    except (shell.ShellError, palette.CatalogError) as exc:
        raise HTTPException(400, str(exc)) from None
    except sqlite3.Error as exc:
        raise HTTPException(
            503, f"the DB can't be used now ({type(exc).__name__})"
        ) from None
    finally:
        conn.close()
    if not made.outcome.ok:
        raise HTTPException(400, "refused: " + " | ".join(made.outcome.reasons))
    return {"card": shell_store.card(home, made.build)}


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    trusted: Callable[..., Principal],
    json_body: JsonBody,
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]
    Trusted = Annotated[Principal, Depends(trusted)]

    def home() -> Path:
        return core.cfg.gos_home

    @app.get("/api/shells")
    async def shell_list(who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(listed, home())

    @app.get("/api/shells/{build}")
    async def shell_get(build: str, who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(shown, home(), build)

    @app.post("/api/shells")
    async def shell_design(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        return await asyncio.to_thread(designed, home(), body)
