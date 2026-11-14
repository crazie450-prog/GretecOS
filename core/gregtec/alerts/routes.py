"""The alert router in the core: its sources, its senders and its routes (WO-9.2, D298-D300).

| route                                 | who                    | what                                  |
|---------------------------------------|------------------------|---------------------------------------|
| GET  /api/alerts/outbox               | admin, the Discord bot | undelivered DMs and Discord's ``/alerts`` state |
| POST /api/alerts/outbox/{id}/delivered| admin, the Discord bot | that DM was posted                    |
| GET  /api/alerts/routed               | admin, a paired device | the moment (away, quiet, ...), the active alerts and where each went |
| POST /api/alerts/test                 | admin (gos alerts test)| one test DM, whatever his presence    |

:func:`wire` connects the router to the core once its loop runs: the credit watch's banner state
(D165), the watchers' paused state (D258), Discord's ``/alerts`` (D297), whether a voice client
and a game are connected, and the two senders: a voice line pushed only to the voice client's
sockets, and a GregtecLink chat line (D76).

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from gregtec.alerts.router import AlertRouter
from gregtec.api.security import Principal
from gregtec.remote.routes import alerts_state

if TYPE_CHECKING:
    from gregtec.api.app import Core

DISCORD = "discord"
VOICE = "voice"  # gregtec.api.app.VOICE_CLIENT


def _is(who: Principal, name: str) -> bool:
    return who.role == "client" and who.name == name


def wire(core: "Core", router: AlertRouter) -> None:
    """Give the router the core's sources and senders (call from the core's loop)."""
    from gregtec.api import dashboard

    def credits() -> dict[str, Any] | None:
        return core.credits.state() if core.credits is not None else None

    def paused() -> tuple[str, float | None] | None:
        game = core.game
        if game is None or game.instance is None:
            return None
        if game.paused_now(dashboard.board_of(core).running) is not True:
            return None
        return game.instance, game.since

    def voice_up() -> bool:
        return any(_is(s.who, VOICE) for s in list(core.sockets))

    def game_up() -> bool:
        link = core.gamelink
        return link is not None and bool(link.state().get("links"))

    def say(text: str) -> bool:
        """The line, as an ``alert`` push the voice client speaks (WO-7.5's shape), to the
        voice client's sockets only: the dashboards already have the alert."""
        socks = [s for s in list(core.sockets) if _is(s.who, VOICE)]
        if not socks or core.loop is None:
            return False
        message = json.dumps(
            {"type": "alert", "data": {"event": "fired", "say": text, "routed": True}}
        )

        def push() -> None:
            for sock in socks:
                sock.put(message)

        core.loop.call_soon_threadsafe(push)
        return True

    def chat(text: str) -> bool:
        if not game_up() or core.gamelink is None:
            return False
        core.gamelink.notice(text, "notice")
        return True

    router.credits = credits
    router.paused = paused
    router.voice_up = voice_up
    router.game_up = game_up
    router.say = say
    router.chat = chat


def add_routes(
    app: FastAPI, core: "Core", principal: Callable[[Request], Principal]
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]

    def router() -> AlertRouter:
        if core.router is None:
            raise HTTPException(503, "alert routing isn't set up in this core")
        return core.router

    def bot_or_admin(who: Principal) -> None:
        if who.role != "admin" and not _is(who, DISCORD):
            raise HTTPException(403, "only gos and the Discord bot")

    @app.get("/api/alerts/outbox")
    async def outbox(who: Anyone) -> dict[str, Any]:
        bot_or_admin(who)
        dms = await asyncio.to_thread(router().outbox)
        state = await asyncio.to_thread(alerts_state, core.store)
        return {"dms": dms, "alerts": state}

    @app.post("/api/alerts/outbox/{dm_id}/delivered")
    async def delivered(dm_id: int, who: Anyone) -> dict[str, Any]:
        bot_or_admin(who)
        done = await asyncio.to_thread(router().delivered, dm_id)
        if not done:
            raise HTTPException(409, "no such undelivered DM")
        return {"delivered": dm_id}

    @app.get("/api/alerts/routed")
    def routed(who: Anyone) -> dict[str, Any]:
        if who.role not in ("admin", "device"):
            raise HTTPException(403, "only gos and a paired device")
        return router().view()

    @app.post("/api/alerts/test")
    def test(who: Anyone) -> dict[str, Any]:
        if who.role != "admin":
            raise HTTPException(403, "only gos")
        router().test()
        return {"queued": True}
