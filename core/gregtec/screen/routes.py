"""``POST /api/screen`` (plan 7.7, D288): the voice client's one frame per screen-key press.

The body is the frame itself (``Content-Type: image/png`` or ``image/jpeg``, at most
:data:`~gregtec.screen.cache.MAX_BYTES`); the query says how it was captured
(``source=dxcam|screenshot``) and may carry a question (``q``). Only the voice client's token
and the admin token may post one: a browser's cross-site request can't send an image body
without a CORS preflight, which the core refuses, and paired devices have no screen to send.
The answer is an ``/api/ask``-shaped reply, also pushed to the dashboards as ``ask.reply``, so
the frame's card shows on monitor 2 and the voice client says its ``text``.

This route is the only way a frame reaches GOS: nothing in the core captures the screen.
No ``from __future__ import annotations``: FastAPI reads the route's local ``Anyone``.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from gregtec.api import dashboard
from gregtec.api.security import Principal
from gregtec.screen import reader
from gregtec.screen.cache import MAX_BYTES, FrameError

if TYPE_CHECKING:
    from gregtec.api.app import Core

log = logging.getLogger(__name__)

PATH = "/api/screen"
CONTENT_TYPES = ("image/png", "image/jpeg")
VOICE_CLIENT = "voice"  # the voice client's token name (gos token new voice)


def wire(core: "Core") -> reader.ScreenReader | None:
    """The core's screen reader, from its agents (None: no agents in this core)."""
    asker = core.asker
    if asker is None:
        return None
    personas = core.personas
    return reader.for_home(
        core.cfg.gos_home,
        asker.registry,
        model=core.cfg.model_vision,
        meter=core.meter,
        ai=core.ai,
        state=core.gamelink.state if core.gamelink is not None else None,
        pointer=core.gamelink,
        persona=personas.info if personas is not None else None,
        persona_block=personas.block if personas is not None else None,
    )


async def _image_body(request: Request) -> bytes:
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind not in CONTENT_TYPES:
        raise HTTPException(415, "send the frame as image/png or image/jpeg")
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_BYTES):
        raise HTTPException(413, "frame too large")
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, "frame too large")
    return bytes(raw)


def add_routes(
    app: FastAPI, core: "Core", principal: Callable[[Request], Principal]
) -> None:
    """``POST /api/screen`` (one call from :func:`gregtec.api.app.create_app`)."""
    if core.screen is None:
        core.screen = wire(core)
    Anyone = Annotated[Principal, Depends(principal)]

    @app.post(PATH)
    async def screen(request: Request, who: Anyone) -> dict[str, Any]:
        if who.role != "admin" and (who.role, who.name) != ("client", VOICE_CLIENT):
            raise HTTPException(403, "only the voice client sends screen frames")
        found = core.screen
        if found is None:
            raise HTTPException(503, "the agents aren't set up in this core")
        source = request.query_params.get("source", "dxcam")
        if source not in reader.SOURCES:
            raise HTTPException(
                400, f"source must be one of {', '.join(reader.SOURCES)}"
            )
        question = request.query_params.get("q")
        if question is not None and len(question) > reader.QUESTION_MAX:
            raise HTTPException(
                400, f"q must be at most {reader.QUESTION_MAX} characters"
            )
        data = await _image_body(request)
        board = dashboard.board_of(core)
        entry = board.asked(f"Screen: {question or reader.DEFAULT_QUESTION}")
        try:
            await dashboard.push_state(core)
            shot = reader.Shot(data, source, question)
            try:
                reply = await asyncio.to_thread(found.read, shot)
            except FrameError as exc:
                raise HTTPException(400, str(exc)) from None
        finally:
            board.done(entry)
        log.info(
            "screen answered",
            extra={
                "by": who.role,
                "source": source,
                "lane": reply.lane,
                "ttft_ms": reply.ttft_ms,
                "checked": reply.checked,
            },
        )
        answer = dashboard.checked_reply(reply.as_dict())
        pushed = await asyncio.to_thread(board.answered, entry, answer, who.name)
        core.broadcast({"type": "ask.reply", "data": pushed}, trusted_only=True)
        await dashboard.push_state(core)
        return answer
