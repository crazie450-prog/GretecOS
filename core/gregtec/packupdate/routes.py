"""The addon watcher's side of the core (WO-11.1, plan 5.2 step 4 and 7.13, D69, D329).

| route                                        | who                               | what         |
|----------------------------------------------|-----------------------------------|--------------|
| GET  /api/export/question                    | admin, client, device             | the open export question, or ``null`` |
| POST /api/export/question/{id}/confirm       | a paired browser, voice, discord  | yes: export now |
| POST /api/export/question/{id}/cancel        | those and admin                   | no: not now  |

The question itself is the post-exit hook's (:func:`gregtec.packupdate.addons.ask`, a file in
GOS_HOME's ``run`` folder); the core shows it as an approval card (kind ``export``) and answers it
for the dashboard, the phone and the voice (whose plain "confirm" and "cancel" answer it when no
world action waits, :mod:`gregtec.agents.ask`). As for world actions (D59), the answer's ``via``
comes from who called, and admin (``gos`` commands) can say no but not yes.

:func:`tick` (from the dashboard's tick, every few seconds) gives each due notice once: the stale
episode at pre-launch and the open question (:func:`gregtec.packupdate.addons.due_notices`),
pushed as a ``notice`` the voice client says and the dashboards count.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from gregtec.api.security import Principal
from gregtec.bridge.http import confirm_via
from gregtec.packupdate import addons

if TYPE_CHECKING:
    from gregtec.api.app import Core

log = logging.getLogger("gregtec.api")


def cards(core: "Core") -> list[dict[str, Any]]:
    """The open export question's approval card, if one waits."""
    q = addons.current(core.cfg.gos_home)
    return [] if q is None else [addons.approval_card(q)]


def tick(core: "Core") -> list[dict[str, Any]]:
    """Push each due notice once (its thread: the dashboard's tick runs it in one). Returns
    the notices pushed."""
    try:
        with core.store.transaction() as conn:
            announced = addons.load_announced(conn)
            due = addons.due_notices(core.cfg.gos_home, announced)
            if due:
                addons.save_announced(conn, [*announced, *(k for k, _ in due)])
    except sqlite3.Error as exc:
        log.info("addon notices skipped", extra={"why": str(exc)[:200]})
        return []
    for _, notice in due:
        core.broadcast({"type": "notice", "data": notice})
    return [n for _, n in due]


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]

    @app.get("/api/export/question")
    def question(who: Anyone) -> dict[str, Any]:
        q = addons.current(core.cfg.gos_home)
        if q is None:
            return {"question": None}
        return {
            "question": {
                "id": q.id,
                "title": q.title,
                "text": q.text,
                "expires": int(q.expires),
            }
        }

    async def decide(qid: str, yes: bool, via: str) -> dict[str, Any]:
        done = await asyncio.to_thread(addons.answer, core.cfg.gos_home, qid, yes, via)
        if done is None:
            raise HTTPException(409, "that question isn't waiting any more")
        log.info("export question answered", extra={"answer": done.answer, "via": via})
        return {"ok": True, "answer": done.answer, "via": via}

    @app.post("/api/export/question/{qid}/confirm")
    async def confirm(qid: str, request: Request, who: Anyone) -> dict[str, Any]:
        via = confirm_via(who, request.headers.get("host"), core.cfg.dashboard_host)
        if via is None:
            raise HTTPException(
                403, "answer on the dashboard, the phone, Discord or by voice"
            )
        return await decide(qid, True, via)

    @app.post("/api/export/question/{qid}/cancel")
    async def cancel(qid: str, request: Request, who: Anyone) -> dict[str, Any]:
        via = confirm_via(who, request.headers.get("host"), core.cfg.dashboard_host)
        if via is None and who.role != "admin":
            raise HTTPException(
                403, "answer on the dashboard, the phone, Discord or by voice"
            )
        return await decide(qid, False, via or "admin")
