"""The Activity view's routes (WO-6.3, plan 7.14 "Activity: what happened this session?").

| route                     | who                   | what                                           |
|---------------------------|-----------------------|------------------------------------------------|
| GET /api/activity         | admin, client, device | sessions, one session's timeline and counts,   |
|                           |                       | its journal lines, the finished ages' recaps    |
|                           |                       | (``?session=<id>&limit=``)                     |
| GET /api/activity/recap   | admin, client, device | the newest finished age's recap, for the       |
|                           |                       | tier-up moment (D162)                          |

Event text is the game's: data, never instructions; the dashboard shows it as plain text.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from gregtec.api.security import Principal
from gregtec.events import ages

if TYPE_CHECKING:
    from gregtec.api.app import Core
    from gregtec.events.service import EventService


def stage_of(core: "Core") -> tuple[str | None, bool]:
    """The stage the dashboard is themed by (the board's ``effective_tier``), and whether it is
    provisional (his override, above the tier detected from his quests, D265). None until it is
    known: his override, or a tier detected from quests; before ``gos quests load`` the board's
    first stage is a default, not an age GOS has seen."""
    tier = core.board.tier if core.board is not None else None
    if not isinstance(tier, dict):
        return None, False
    stage, how, detected = tier.get("stage"), tier.get("how"), tier.get("detected")
    if not isinstance(stage, str):
        return None, False
    if how == "override":
        found = detected if isinstance(detected, str) else None
        return stage, found is None or ages.age_of(found) < ages.age_of(stage)
    if not isinstance(detected, str):
        return None, False
    return stage, False


def add_routes(
    app: FastAPI, core: "Core", principal: Callable[[Request], Principal]
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]

    def service() -> "EventService":
        if core.events is None:
            raise HTTPException(503, "the event store isn't set up in this core")
        return core.events

    @app.get("/api/activity")
    async def activity(
        _: Anyone,
        session: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 300,
    ) -> dict[str, Any]:
        svc = service()
        try:
            found: dict[str, Any] = await asyncio.to_thread(
                svc.activity, session, limit
            )
        except KeyError:
            raise HTTPException(404, f"no session {session}") from None
        return found

    @app.get("/api/activity/recap")
    async def recap(_: Anyone) -> dict[str, Any]:
        svc = service()
        found: dict[str, Any] = await asyncio.to_thread(svc.recap, *stage_of(core))
        return found
