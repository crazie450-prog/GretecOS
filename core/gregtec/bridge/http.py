"""The bridge's HTTP routes in the core's app (WO-7.1; plan 7.9, 8 WO-7.1; D214, D220, D221).

| route                           | who                              | what                          |
|---------------------------------|----------------------------------|-------------------------------|
| POST /oc/telemetry              | an OC computer's token (D221)    | M2: one telemetry message in, |
|                                 |                                  | the reply with its commands   |
|                                 |                                  | and displays (WO-7.4)         |
| GET  /api/base/live             | admin, client, device            | the live base (WO-7.4)        |
| GET  /api/actions             | admin, client, device            | the pending action, the newest|
| GET  /api/actions/{id}          | admin, client, device            | one action                    |
| POST /api/actions/{id}/confirm  | a paired browser, voice, discord | confirm it by id (D59)        |
| POST /api/actions/{id}/cancel   | those and admin                  | cancel it by id               |

**/oc/telemetry always answers HTTP 200** with one reply document of at most 2048 bytes (OC can't
read a non-2xx body and reads 2048 bytes a call, [C§1.4]); a refusal is ``{"v": 1, "ok": false,
"cmds": [], "err": ...}`` and nothing in the request is processed:

- ``unauthorized``: no ``Authorization: Bearer``, or a token that isn't a live OC token. The
  token is checked in constant time (:meth:`gregtec.api.security.Auth.oc_binding`) and never
  logged; the token's instance (``play`` or ``lab``) is the binding (D217).
- ``bad_payload``: not ``application/json``, a body over 256 KB (refused unread past the
  limit), not JSON, or not valid by the contract's schema (:func:`gregtec.bridge.ingest.parse`).
- ``internal``: more messages than :data:`RATE` allows for one token (the agent backs off on an
  ``ok: false`` reply and retries), or the core couldn't record it.

The confirm routes take the action's id, so a button confirms only the action its card shows: a
newer proposal has another id, and an expired one can't be confirmed (:class:`~gregtec.bridge.
actions.ActionDesk`). ``confirmed_via`` comes from who called, never from the request: a
paired browser on the dashboard's own name is ``dashboard``, on any other allowed name (the
tailnet's, the phone) ``phone``; the ``voice`` and ``discord`` clients are themselves. Admin
(``gos`` commands) can cancel but not confirm: confirming is Clayton's, by one of D59's ways.
"""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response

from gregtec.api.security import Principal
from gregtec.bridge.actions import VIAS, ActionDesk
from gregtec.bridge.ingest import MAX_BODY, Outcome, encode

if TYPE_CHECKING:
    from gregtec.api.app import Core

log = logging.getLogger("gregtec.bridge")

RATE = (10, 1.0)  # a burst of 10 messages, then one a second, per OC token
FAILED_RATE = (20, 1.0)  # refused requests (no or a wrong token), all callers together


class Bucket:
    """A token bucket: ``burst`` at once, refilled at ``per_s`` a second."""

    def __init__(
        self, burst: int, per_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.burst, self.per_s, self.clock = burst, per_s, clock
        self._level: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> bool:
        with self._lock:
            now = self.clock()
            level, at = self._level.get(key, (float(self.burst), now))
            level = min(float(self.burst), level + (now - at) * self.per_s)
            if level < 1.0:
                self._level[key] = (level, now)
                return False
            self._level[key] = (level - 1.0, now)
            return True


def confirm_via(who: Principal, host: str | None, dashboard_host: str) -> str | None:
    """How a caller confirms (D59), or None if it can't: see the module docstring."""
    if who.role == "device":
        name = (host or "").lower().split(":")[0]
        return "dashboard" if name == dashboard_host.lower() else "phone"
    if who.role == "client" and who.name in ("voice", "discord"):
        return who.name
    return None


def _refused(err: str) -> Response:
    return _reply({"v": 1, "ok": False, "cmds": [], "err": err})


def _reply(doc: dict[str, Any]) -> Response:
    return Response(encode(doc), status_code=200, media_type="application/json")


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[..., Principal],
) -> None:
    """Add the bridge's routes to the core's app (one call from :func:`gregtec.api.app.create_app`)."""
    rate = Bucket(*RATE)
    failed = Bucket(*FAILED_RATE)

    @app.post("/oc/telemetry")
    async def oc_telemetry(request: Request) -> Response:
        ops = core.ops
        if ops is None:
            return _refused("internal")
        auth = request.headers.get("authorization") or ""
        scheme, _, token = auth.partition(" ")
        bound = None
        if scheme.lower() == "bearer" and token.strip():
            bound = await asyncio.to_thread(core.auth.oc_binding, token.strip())
        if bound is None:
            if failed.take("all"):
                log.warning("OC message refused", extra={"err": "unauthorized"})
            return _refused("unauthorized")
        binding, name = bound
        if not rate.take(name):
            log.warning("OC message refused", extra={"err": "rate", "client": name})
            return _refused("internal")
        source = f"http:{name}"
        if (
            request.headers.get("content-type", "").split(";")[0].strip()
            != "application/json"
        ):
            ops.bridge.refuse("bad_payload", "not application/json", source=source)
            return _refused("bad_payload")
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY):
            ops.bridge.refuse(
                "bad_payload", "Content-Length over the limit", source=source
            )
            return _refused("bad_payload")
        raw = bytearray()
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > MAX_BODY:  # a chunked body: stop reading at the limit
                ops.bridge.refuse("bad_payload", "body over the limit", source=source)
                return _refused("bad_payload")
        outcome: Outcome = await asyncio.to_thread(
            ops.bridge.receive,
            bytes(raw),
            transport="http",
            source=source,
            binding=binding,
        )
        ack = (
            ops.bridge.seqs.highest(outcome.agent)
            if outcome.ok and outcome.agent
            else None
        )
        doc, _ = await asyncio.to_thread(ops.bridge.reply, outcome, ack=ack)
        return _reply(doc)

    def desk() -> ActionDesk:
        if core.ops is None:
            raise HTTPException(503, "the OC bridge isn't set up in this core")
        return core.ops.desk

    Anyone = Depends(principal)

    @app.get("/api/base/live")
    def base_live(_: Principal = Anyone) -> dict[str, Any]:
        """The Base view's live telemetry (WO-7.4, :class:`gregtec.bridge.live.Live`)."""
        if core.ops is None or core.ops.live is None:
            raise HTTPException(503, "the OC bridge isn't set up in this core")
        return core.ops.live.snapshot()

    @app.get("/api/actions")
    def actions(_: Principal = Anyone) -> dict[str, Any]:
        d = desk()
        pending = d.pending()
        return {
            "pending": pending.as_dict() if pending else None,
            "recent": [a.as_dict() for a in d.recent(20)],
        }

    @app.get("/api/actions/{action_id}")
    def action(action_id: int, _: Principal = Anyone) -> dict[str, Any]:
        found = desk().get(action_id)
        if found is None:
            raise HTTPException(404, "no such action")
        return found.as_dict()

    @app.post("/api/actions/{action_id}/confirm")
    async def confirm(
        action_id: int, request: Request, who: Principal = Anyone
    ) -> dict[str, Any]:
        via = confirm_via(who, request.headers.get("host"), core.cfg.dashboard_host)
        if via is None or via not in VIAS:
            raise HTTPException(
                403, "confirm on the dashboard, the phone, Discord or by voice"
            )
        out = await asyncio.to_thread(desk().confirm, via, action_id)
        if out.action is None:
            raise HTTPException(409, out.text)
        return {"ok": out.ok, "text": out.text, "action": out.action.as_dict()}

    @app.post("/api/actions/{action_id}/cancel")
    async def cancel(
        action_id: int, request: Request, who: Principal = Anyone
    ) -> dict[str, Any]:
        via = confirm_via(who, request.headers.get("host"), core.cfg.dashboard_host)
        if via is None and who.role != "admin":
            raise HTTPException(
                403, "cancel on the dashboard, the phone, Discord or by voice"
            )
        out = await asyncio.to_thread(desk().cancel, via or "admin", action_id)
        if out.action is None:
            raise HTTPException(409, out.text)
        return {"ok": out.ok, "text": out.text, "action": out.action.as_dict()}
