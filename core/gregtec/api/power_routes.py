"""The Power view over HTTP (WO-4.6; plan 7.2, 7.14 "Power", D243).

| route                         | who                   | what                                     |
|-------------------------------|-----------------------|------------------------------------------|
| GET /api/power                | admin, client, device | the latest power design's ids and the EU ledger as it is (``?window=S``: buffers for an S-second peak) |
| GET /api/power/{id}           | admin, client, device | the Power view of the design plan ``id`` belongs to (``?window=S``) |
| POST /api/power/design        | admin, device         | a spec (``powerview.request_from_spec``): solve, save each option as a proposed plan, view |
| POST /api/power/{id}/accept   | admin, device         | Clayton accepts the option: its line note and its source in the EU ledger; the view again |

Lifecycle moves after accepting are ``POST /api/plans/{id}/status`` (WO-4.4). Designing and
accepting are Clayton's paired dashboards' and his ``gos`` commands' (admin) only, never the
client tokens (voice, Discord, OC) and never a model (D251). Every number in the answers is the
power designer's, the ledger's or the distribution checks'.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import math
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from gregtec.api.security import Principal
from gregtec.packupdate.plans import PLAN_ID

if TYPE_CHECKING:
    from gregtec.api.app import Core

JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def _pid(text: str) -> str:
    if not PLAN_ID.fullmatch(text) or not text.startswith("pw-"):
        raise HTTPException(400, "not a power plan id")
    return text


def _window(text: str) -> float:
    try:
        v = float(text or 0)
    except ValueError:
        raise HTTPException(400, "window is a number of seconds") from None
    if not math.isfinite(v):
        raise HTTPException(400, "window is a number of seconds")
    return v


def _run(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """A Power view call, its refusal as a 400 (404 for a plan that isn't saved). The planner is
    imported on first use, so the core starts without it."""
    from gregtec.planner.designer import DesignerError

    try:
        return fn()
    except DesignerError as exc:
        raise HTTPException(
            404 if "isn't saved" in str(exc) else 400, str(exc)
        ) from None
    except sqlite3.Error as exc:
        raise HTTPException(
            503, f"the DB can't be used now ({type(exc).__name__})"
        ) from None


def latest(home: Path, window_s: float) -> dict[str, Any]:
    from gregtec.planner import designer, powerview

    try:
        ledger = powerview.ledger_view(home, window_s)
    except designer.DesignerError as exc:
        if "doesn't exist" not in str(exc):
            raise
        ledger = None  # no DB yet: the view's empty state says so
    return {"ids": powerview.latest(home), "ledger": ledger}


def designed(home: Path, spec: dict[str, Any]) -> dict[str, Any]:
    from gregtec.planner import powerview

    d = powerview.design(home, powerview.request_from_spec(spec))
    return {"ids": d.ids, "view": powerview.view(home, d.ids[0])}


def accepted(home: Path, pid: str) -> dict[str, Any]:
    from gregtec.planner import powerview

    done = powerview.accept(home, pid)
    return {
        "id": done.id,
        "status": done.status,
        "line": done.line,
        "ledger": done.ledger,
        "problems": done.problems,
        "view": powerview.view(home, pid),
    }


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

    async def call(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        return await asyncio.to_thread(_run, fn)

    @app.get("/api/power")
    async def power_latest(who: Anyone, window: str = "0") -> dict[str, Any]:
        w = _window(window)
        return await call(lambda: latest(home(), w))

    @app.post("/api/power/design")
    async def power_design(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        return await call(lambda: designed(home(), body))

    @app.get("/api/power/{plan_id}")
    async def power_view(
        plan_id: str, who: Anyone, window: str = "0"
    ) -> dict[str, Any]:
        from gregtec.planner import powerview

        pid, w = _pid(plan_id), _window(window)
        return await call(lambda: powerview.view(home(), pid, w))

    @app.post("/api/power/{plan_id}/accept")
    async def power_accept(plan_id: str, who: Trusted) -> dict[str, Any]:
        pid = _pid(plan_id)
        return await call(lambda: accepted(home(), pid))
