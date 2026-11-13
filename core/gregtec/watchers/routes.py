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

Play's machine events and census (``machine_built``, ``machine_removed``, ``machine_census``,
WO-6.2) also update My base's automatic machine counts (:mod:`gregtec.base.seen`, D261); Lab's are
only kept in the feed. Every machine event of the post is applied, not only the ones the feed
hadn't seen (each is keyed by position, so applying it again changes nothing); a failed DB write
answers 503 and the watchers send the batch again, so no event or census is lost to it.
Every well-formed event of a post also goes into the event store (WO-6.3,
:mod:`gregtec.events.store`), keyed by its id, so a batch sent again adds nothing.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from gregtec import store
from gregtec.api import dashboard
from gregtec.api.security import Principal
from gregtec.base import seen
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


def _apply_machines(core: "Core", events: list[dict[str, Any]]) -> None:
    conn = store.connect(core.store.path)
    try:
        applied = seen.apply(conn, events)
    finally:
        conn.close()
    if applied.changed or applied.ignored:
        log.info(
            "My base machines",
            extra={
                "events": applied.events,
                "census_chunks": applied.census_chunks,
                "census_machines": applied.census_machines,
                "ignored": applied.ignored,
            },
        )


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
        changed, _new, kept = feed.receive_new(body)
        machine = [e for e in kept if e["kind"] in seen.KINDS]
        applied = True
        if core.events is not None and kept:  # the event store and sessions (WO-6.3)
            instance = body.get("instance")
            try:
                await asyncio.to_thread(
                    core.events.record,
                    instance if isinstance(instance, str) else None,
                    kept,
                )
            except Exception:
                log.exception("game events not stored")
                applied = False
        if body.get("instance") == "play" and machine:
            try:
                await asyncio.to_thread(_apply_machines, core, machine)
            except Exception:
                log.exception("machine events not applied to My base")
                applied = False
        if changed:
            log.info("paused state", extra={"paused": feed.paused, "why": feed.why})
            await dashboard.push_state(core)
        if not applied:  # the watchers keep the batch and send it again
            raise HTTPException(
                503, "events not stored or My base not updated; send the batch again"
            )
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
