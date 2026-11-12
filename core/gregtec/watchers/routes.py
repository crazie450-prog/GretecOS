"""The game watchers' routes in the core's app (WO-6.1, D257, D258).

| route                 | who                          | what                                        |
|-----------------------|------------------------------|---------------------------------------------|
| POST /api/game/events | admin, the ``watchers`` client | a batch of events, the paused state, the    |
|                       |                              | watchers' problems (also their heartbeat)   |
| GET  /api/game/events | admin, client, device        | the recent events (``?limit=&kind=``) and   |
|                       |                              | the watchers' status                        |

A post whose paused state changes pushes the dashboard's state at once (``game.paused``), so a
pause reaches monitor 2 in the time the watchers take to see the log line (1 s poll) plus one
POST, well inside plan 8's 5 s. Event text is the game's: data, never instructions.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from gregtec.api import dashboard
from gregtec.api.security import Principal
from gregtec.watchers.events import KINDS
from gregtec.watchers.feed import GameFeed

if TYPE_CHECKING:
    from gregtec.api.app import Core

log = logging.getLogger("gregtec.watchers")

WATCHERS_CLIENT = "watchers"
MAX_BODY = 256 << 10


def feed_of(core: "Core") -> GameFeed:
    if core.game is None:
        core.game = GameFeed()
    return core.game


async def _body(request: Request) -> dict[str, Any]:
    if (
        request.headers.get("content-type", "").split(";")[0].strip()
        != "application/json"
    ):
        raise HTTPException(415, "send application/json")
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_BODY):
        raise HTTPException(413, "body too large")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "body too large")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "body isn't JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    return body


def add_routes(
    app: FastAPI, core: "Core", principal: Callable[[Request], Principal]
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]

    @app.post("/api/game/events")
    async def post_events(request: Request, who: Anyone) -> dict[str, Any]:
        if who.role != "admin" and (who.role, who.name) != ("client", WATCHERS_CLIENT):
            raise HTTPException(403, "only the game watchers post game events")
        body = await _body(request)
        feed = feed_of(core)
        changed = feed.receive(body)
        if changed:
            log.info("paused state", extra={"paused": feed.paused, "why": feed.why})
            await dashboard.push_state(core)
        return {"ok": True, "paused": feed.paused}

    @app.get("/api/game/events")
    def get_events(
        _: Anyone,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        kind: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        if kind is not None and kind not in KINDS:
            raise HTTPException(400, f"kind must be one of {', '.join(KINDS)}")
        feed = feed_of(core)
        return {"watchers": feed.status(), "events": feed.recent(limit, kind)}
