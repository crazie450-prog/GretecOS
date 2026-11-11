"""No-AI mode and the credits flow in the core (WO-2.8, plan 7.16, D48, D164-D166).

| route                       | who                              | what                              |
|-----------------------------|----------------------------------|-----------------------------------|
| GET  /api/ai                | admin, client, device            | the AI's state and the credits'   |
| POST /api/ai                | admin, device                    | ``{"online": bool}``: his switch  |
| POST /api/credits/decision  | admin, device, the Discord bot   | ``{"choice": ...}``: D48's answer |

``/api/credits/decision`` is the decision API the dashboard's credit banner calls now and WO-9.2's
Discord buttons will call (as the client token named ``discord``): ``billing`` (the reply carries
the Console billing URL to open), ``no_ai``, ``stop`` (GOS stops as ``gos stop`` does: the core
exits with :data:`gregtec.supervisor.EXIT_STOPPED`, which the supervisor leaves stopped) and
``resume``. Both states are also in ``/api/state`` (``ai``, ``credits``) and pushed with it.

The core's watcher calls :func:`refresh` once a second: the credit cycle against the thresholds
(:meth:`CreditWatch.check`), and, at most once a minute while an API problem keeps the AI off, a
free probe of the API with the stored key. When the AI goes off for the credits or by Clayton's
switch, the deep lane's unfinished tasks are stopped, so nothing spends while it is off.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from gregtec import supervisor
from gregtec.agents.deep import FINAL
from gregtec.agents.no_ai import (
    CHOICE,
    CHOICES,
    CREDITS,
    AiMode,
    CreditWatch,
    Problem,
    probe_api,
)
from gregtec.api import dashboard
from gregtec.api.security import Principal
from gregtec.llm import keys

if TYPE_CHECKING:
    from gregtec.api.app import Core

log = logging.getLogger("gregtec.api")

DISCORD_CLIENT = (
    "discord"  # the bot's client token name (gos token new discord, WO-9.1)
)
JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def probe_stored_key() -> Problem | None:
    """The API probe with the key in the keyring (raises when there is none)."""
    return probe_api(keys.get_key(keys.ANTHROPIC))


def wire(core: "Core", ai: AiMode, credits: CreditWatch) -> None:
    """Give the core its AI mode and credit watch, and connect their callbacks."""
    core.ai, core.credits = ai, credits
    credits.stop = lambda: stop_gos(core)
    ai.on_change = lambda: changed(core)


def changed(core: "Core") -> None:
    """The AI went on or off (any thread): stop spending deep-lane work when it went off for
    the credits or by his switch, and push the state to the dashboards."""
    ai = core.ai
    if (
        ai is not None
        and core.deep is not None
        and {CREDITS, CHOICE} & set(ai.reasons())
    ):
        for task in core.deep.tasks():
            if task.status not in FINAL:
                log.info(
                    "deep-lane task stopped: the AI is off", extra={"task": task.id}
                )
                core.deep.stop_task(task.id)
    if core.loop is not None:
        core.loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(dashboard.push_state(core))
        )


def stop_gos(core: "Core") -> None:
    """D48's Stop: shut GOS down as ``gos stop`` does. The core exits with EXIT_STOPPED, which
    the supervisor (and so the Discord bot it will also run, WO-9.1) leaves stopped until
    ``gos serve`` or the phone starts it again (plan 7.16)."""
    log.warning("stopping GOS: Stop on the credit alert")
    core.exit_code = supervisor.EXIT_STOPPED
    if core.shutdown is not None:
        core.shutdown()


def refresh(core: "Core") -> None:
    """Once a second from the watcher (blocking: in a thread)."""
    if core.credits is not None:
        try:
            core.credits.check()
        except (
            sqlite3.Error
        ) as exc:  # shown on the health strip; tried again next second
            log.info("credit check skipped", extra={"why": str(exc)[:200]})
    if core.ai is not None and core.ai.probe_due():
        core.ai.probe(core.probe or probe_stored_key)


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    trusted: Callable[..., Principal],
    json_body: JsonBody,
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]
    Trusted = Annotated[Principal, Depends(trusted)]

    def push() -> None:
        asyncio.ensure_future(dashboard.push_state(core))

    @app.get("/api/ai")
    def get_ai(_: Anyone) -> dict[str, Any]:
        return {
            "ai": dashboard.ai_state(core),
            "credits": dashboard.credits_state(core),
        }

    @app.post("/api/ai")
    async def set_ai(request: Request, who: Trusted) -> dict[str, Any]:
        """``{"online": false}`` turns the AI off until ``{"online": true}`` (plan 7.16)."""
        if core.ai is None:
            raise HTTPException(503, "no-AI mode isn't set up in this core")
        body = await json_body(request)
        online = body.get("online")
        if not isinstance(online, bool):
            raise HTTPException(400, "online must be true or false")
        await asyncio.to_thread(core.ai.set_choice, online)
        log.info("AI switch", extra={"online": online, "by": who.name})
        push()
        return {
            "ai": dashboard.ai_state(core),
            "credits": dashboard.credits_state(core),
        }

    @app.post("/api/credits/decision")
    async def decide(request: Request, who: Anyone) -> dict[str, Any]:
        """D48's answer: ``{"choice": "billing" | "no_ai" | "stop" | "resume"}``."""
        if who.role not in ("admin", "device") and (who.role, who.name) != (
            "client",
            DISCORD_CLIENT,
        ):
            raise HTTPException(403, "only a paired device, gos or the Discord bot")
        if core.credits is None:
            raise HTTPException(503, "the credit watch isn't set up in this core")
        body = await json_body(request)
        choice = body.get("choice")
        if choice not in CHOICES:
            raise HTTPException(400, f"choice must be one of {', '.join(CHOICES)}")
        log.info("credit decision", extra={"choice": choice, "by": who.name})
        credits = await asyncio.to_thread(core.credits.decide, choice)
        push()
        return {
            "choice": choice,
            "open": credits["billing_url"] if choice == "billing" else None,
            "stopping": choice == "stop",
            "ai": dashboard.ai_state(core),
            "credits": credits,
        }
