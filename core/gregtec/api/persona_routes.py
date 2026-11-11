"""Persona switching in the core (WO-5.6; plan 7.11, 7.12, D206).

| route              | who                                  | what                                   |
|--------------------|--------------------------------------|----------------------------------------|
| GET  /api/persona  | admin, client, device                | the active pack, every pack, problems (admin, device only) |
| POST /api/persona  | admin, device, the Discord bot       | ``{"id": "<name>" or "random"}``       |

``POST /api/persona`` is what the dashboard's Settings dropdown calls now and WO-9.x's Discord
``/persona`` command will call (as the client token named ``discord``, as for the credit
decision). It takes a pack's name or display name; an unknown one is a 404 whose message names
the packs there are. A switch is pushed to the dashboards with the state (its ``persona``). The
spoken "persona <name>" goes through ``/api/ask`` and the router instead (no model call).

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from gregtec.agents.personas import Pack, UnknownPersona
from gregtec.api import dashboard
from gregtec.api.ai_routes import DISCORD_CLIENT
from gregtec.api.security import Principal

if TYPE_CHECKING:
    from gregtec.api.app import Core

log = logging.getLogger("gregtec.api")

NAME_MAX = 60
JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def wire(core: "Core") -> None:
    """Push the state to the dashboards whenever the persona changes, by any path."""
    if core.personas is None:
        return

    def changed(_pack: Pack, _by: str) -> None:
        if core.loop is not None:
            core.loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(dashboard.push_state(core))
            )

    core.personas.on_change.append(changed)


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    json_body: JsonBody,
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]

    def personas() -> Any:  # noqa: ANN401 - gregtec.agents.personas.Personas
        if core.personas is None:
            raise HTTPException(503, "personas aren't set up in this core")
        return core.personas

    @app.get("/api/persona")
    def get_persona(who: Anyone) -> dict[str, Any]:
        return personas().listing(problems=who.role in ("admin", "device"))

    @app.post("/api/persona")
    async def set_persona(request: Request, who: Anyone) -> dict[str, Any]:
        """``{"id": "ship-ai"}`` (a name or display name, or ``random``)."""
        if who.role not in ("admin", "device") and (who.role, who.name) != (
            "client",
            DISCORD_CLIENT,
        ):
            raise HTTPException(403, "only a paired device, gos or the Discord bot")
        found = personas()
        body = await json_body(request)
        name = body.get("id")
        if not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX:
            raise HTTPException(
                400, f"id must be a persona's name (1 to {NAME_MAX} characters)"
            )
        try:
            pack = await asyncio.to_thread(found.set, name, who.name)
        except UnknownPersona as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "persona": pack.info(),
            **found.listing(problems=who.role in ("admin", "device")),
        }
