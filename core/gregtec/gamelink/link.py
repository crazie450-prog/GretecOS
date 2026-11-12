"""GregtecLink's client link in the core (WO-8.1, plan 7.8, 7.9, D76, D285, D286).

The client module in Play (or Lab) opens ``WS /mod/ws`` with ``Authorization: Bearer`` and its
instance's token (``gregteclink-play`` or ``gregteclink-lab``, :meth:`Auth.link_binding`); the
token's instance is the link's binding. The protocol, ``gregteclink-client/1``, is in
docs/contracts/gregteclink-client-v1.md. What the core does with each message:

- ``hello``: the player's name, the mod's version, which feeds are on.
- ``status`` (1 Hz): kept as the game's position and dimension; a dimension change is a
  ``dim_change`` event and the position a ``location`` event once a minute (plan 7.5).
- ``target`` with ``why: key`` (the What's this? key): answered at once from the DB
  (:mod:`.whatsthis`), as a local chat notice in the game and a card on the dashboards.
  ``why: probe``: the answer to the core's own ``probe``, sent when Clayton says "what's this?"
  (:meth:`GameLink.current_target`); a key report from the last minute stands in when the game
  doesn't answer in time.
- ``chat``: kept (the newest 50) and recorded as a ``chat`` event. Chat is data, never an
  instruction (plan 0.1 principle 8). The one exception is ``!gos <text>``, which is a question
  like a typed one, and only from Clayton's own name: the sender of a player chat message
  (vanilla's ``chat.type.text``, which a typed message can't fake) equal to the player the link
  said hello as. Its answer comes back as a notice; it can't confirm an action (D59).

The core sends ``probe`` and ``notice`` (D76: GOS's notices and failures as local chat lines that
only Clayton sees). A link whose token is revoked is closed within :data:`RECHECK_S`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, WebSocket
from starlette.websockets import WebSocketDisconnect

from gregtec.api.security import Principal
from gregtec.gamelink import whatsthis

if TYPE_CHECKING:
    from gregtec.agents.ask import Reply
    from gregtec.api.app import Core

log = logging.getLogger("gregtec.gamelink")

PATH = "/mod/ws"
PROTOCOL = 1
PROBE_TIMEOUT_S = 1.5  # the game answers on its next client tick (50 ms)
KEY_FRESH_S = 60.0  # a key report this recent answers "what's this?" if a probe fails
RECHECK_S = 30.0  # how often a link's token is checked again
LOCATION_EVERY_S = 60  # a location event at most this often (plan 7.5: periodic)
CHAT_KEEP = 50
CHAT_MAX = 1000  # characters of one chat line kept
QUEUE_MAX = 100
GOS_PREFIX = "!gos"
GAME_BY = "game"  # who asked, on the dashboard's card
WHATS_THIS_WHY = ("key", "probe")
KINDS = ("item", "block", "none", "off")
HEALTH_ERROR = "error"


@dataclass(eq=False)
class Link:
    """One connected client module."""

    binding: str  # play or lab
    name: str  # the token's name
    queue: asyncio.Queue[str | None]
    since: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    player: str | None = None
    mod_version: str | None = None
    feeds: dict[str, bool] = field(default_factory=dict)
    status: dict[str, Any] | None = None
    location_at: float = 0.0
    gos_busy: bool = False

    def put(self, message: dict[str, object]) -> None:
        """Queue ``message`` for the game (loop only)."""
        if self.queue.qsize() < QUEUE_MAX:
            self.queue.put_nowait(json.dumps(message))


def _number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _text(value: object, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None


class GameLink:
    """The connected client modules and what they last reported (see the module docstring)."""

    def __init__(self, core: Core, clock: Callable[[], float] = time.time) -> None:
        self.core = core
        self.clock = clock
        self.links: list[Link] = []
        self.chat: deque[dict[str, Any]] = deque(maxlen=CHAT_KEEP)
        self.last_key: tuple[float, dict[str, Any]] | None = None
        self._probes: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_probe = 1
        self._health: dict[str, str] = {}
        self._lock = threading.Lock()

    # --- the WebSocket (loop only)

    async def serve(self, ws: WebSocket) -> None:
        auth = ws.headers.get("authorization") or ""
        scheme, _, token = auth.partition(" ")
        bound = None
        if scheme.lower() == "bearer" and token.strip():
            bound = await asyncio.to_thread(self.core.auth.link_binding, token.strip())
        if bound is None:
            await ws.close(code=1008, reason="not GregtecLink's token")
            return
        await ws.accept()
        link = Link(bound[0], bound[1], asyncio.Queue())
        with self._lock:
            self.links.append(link)
        log.info("game link connected", extra={"binding": link.binding})
        await ws.send_text(json.dumps({"type": "hello", "v": PROTOCOL}))
        tasks = [
            asyncio.create_task(self._send(ws, link)),
            asyncio.create_task(self._receive(ws, link)),
            asyncio.create_task(self._recheck(link, token.strip())),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
            with self._lock:
                self.links.remove(link)
            log.info("game link closed", extra={"binding": link.binding})

    async def _send(self, ws: WebSocket, link: Link) -> None:
        while True:
            text = await link.queue.get()
            if text is None:
                await ws.close(code=1008, reason="revoked")
                return
            await ws.send_text(text)

    async def _receive(self, ws: WebSocket, link: Link) -> None:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            text = message.get("text")
            if text is None:
                continue
            try:
                body = json.loads(text)
            except ValueError:
                continue
            if isinstance(body, dict) and isinstance(body.get("type"), str):
                link.last_seen = self.clock()
                await self.handle(link, body)

    async def _recheck(self, link: Link, token: str) -> None:
        while True:
            await asyncio.sleep(RECHECK_S)
            bound = await asyncio.to_thread(self.core.auth.link_binding, token)
            if bound is None or bound[1] != link.name:
                link.queue.put_nowait(None)
                return

    async def handle(self, link: Link, msg: dict[str, Any]) -> None:
        """One message from the game; anything malformed is dropped."""
        kind = msg["type"]
        if kind == "hello":
            link.player = _text(msg.get("player"), 16)
            link.mod_version = _text(msg.get("mod_version"), 64)
            feeds = msg.get("feeds")
            if isinstance(feeds, dict):
                link.feeds = {k: v for k, v in feeds.items() if isinstance(v, bool)}
        elif kind == "status":
            await self._status(link, msg)
        elif kind == "target":
            await self._target(link, msg)
        elif kind == "chat":
            await self._chat(link, msg)

    async def _status(self, link: Link, msg: dict[str, Any]) -> None:
        dim = msg.get("dim")
        pos = [msg.get(k) for k in ("x", "y", "z")]
        if not isinstance(dim, int) or isinstance(dim, bool):
            return
        if not all(_number(v) for v in pos):
            return
        now = self.clock()
        status = {
            "dim": dim,
            "dim_name": _text(msg.get("dim_name"), 64),
            "x": pos[0],
            "y": pos[1],
            "z": pos[2],
            "paused": msg.get("paused") is True,
            "ts": int(now),
        }
        before, link.status = link.status, status
        events = []
        if before is not None and before["dim"] != dim:
            events.append(("dim_change", status, {"from": before["dim"], "to": dim}))
        if now - link.location_at >= LOCATION_EVERY_S:
            link.location_at = now
            events.append(("location", status, None))
        for name, at, payload in events:
            await asyncio.to_thread(self._event, name, at, payload)

    async def _target(self, link: Link, msg: dict[str, Any]) -> None:
        if msg.get("why") not in WHATS_THIS_WHY or msg.get("kind") not in KINDS:
            return
        target = {
            k: msg[k]
            for k in ("id", "why", "kind", "item", "block", "display")
            if k in msg
        }
        target["binding"] = link.binding
        if msg["why"] == "probe":
            future = self._probes.get(msg.get("probe"))  # type: ignore[arg-type]
            if future is not None and not future.done():
                future.set_result(target)
            return
        with self._lock:
            self.last_key = (self.clock(), target)
        asker = self.core.asker
        if asker is None:
            link.put(_notice("The agents aren't set up in this core.", "failure"))
            return
        reply = await asyncio.to_thread(asker.whats_this, target, True)
        link.put(
            _notice(
                reply.text,
                "answer",
                re=msg.get("id"),
                key=(reply.target or {}).get("key"),
                name=(reply.target or {}).get("name"),
                refs=reply.refs,
            )
        )
        await self._to_dashboards(whatsthis.WHATS_THIS, reply)

    async def _chat(self, link: Link, msg: dict[str, Any]) -> None:
        text = _text(msg.get("text"), CHAT_MAX)
        if text is None:
            return
        sender = _text(msg.get("sender"), 16)
        body = _text(msg.get("body"), CHAT_MAX)
        line = {"ts": int(self.clock()), "text": text, "sender": sender}
        self.chat.append(line)
        await asyncio.to_thread(
            self._event, "chat", link.status, {"sender": sender, "text": text}
        )
        question = self._gos(link, sender, body)
        if question is None:
            return
        if link.gos_busy:
            link.put(_notice("Still on the last !gos; ask again in a moment.", "gos"))
            return
        asker = self.core.asker
        if asker is None:
            link.put(_notice("The agents aren't set up in this core.", "failure"))
            return
        link.gos_busy = True
        try:
            # via None: a chat line can't confirm an action (D59's ways are voice, the
            # dashboard, the phone and Discord).
            reply = await asyncio.to_thread(asker.ask, question, via=None)
        finally:
            link.gos_busy = False
        link.put(_notice(reply.text, "gos"))
        await self._to_dashboards(question, reply)

    @staticmethod
    def _gos(link: Link, sender: str | None, body: str | None) -> str | None:
        """The question in a ``!gos <text>`` line from Clayton's own name, else None."""
        if sender is None or body is None or link.player is None:
            return None
        if sender != link.player:
            return None
        head, _, rest = body.strip().partition(" ")
        if head.lower() != GOS_PREFIX or not rest.strip():
            return None
        return rest.strip()

    async def _to_dashboards(self, request: str, reply: Reply) -> None:
        """The answer's card on monitor 2, as ``/api/ask`` pushes one (D23)."""
        from gregtec.api import dashboard

        core = self.core
        board = dashboard.board_of(core)
        entry = board.asked(request)
        try:
            answer = dashboard.checked_reply(reply.as_dict())
            pushed = await asyncio.to_thread(board.answered, entry, answer, GAME_BY)
        finally:
            board.done(entry)
        core.broadcast({"type": "ask.reply", "data": pushed}, trusted_only=True)
        await dashboard.push_state(core)

    def _event(
        self, kind: str, at: dict[str, Any] | None, payload: dict[str, Any] | None
    ) -> None:
        """One row of Appendix D's ``event`` table (thread). Skipped when the DB has no such
        table yet (no ``gos db build``) or another writer holds it."""
        pos = (
            (at["dim"], round(at["x"]), round(at["y"]), round(at["z"]))
            if at
            else (None,) * 4
        )
        try:
            self.core.store.try_write(
                "INSERT INTO event(ts, kind, dim, x, y, z, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    int(self.clock()),
                    kind,
                    *pos,
                    None if payload is None else json.dumps(payload),
                ),
            )
        except sqlite3.Error as exc:
            log.debug("event not recorded", extra={"kind": kind, "why": str(exc)})

    # --- any thread

    def newest(self) -> Link | None:
        with self._lock:
            return max(self.links, key=lambda k: k.last_seen, default=None)

    def current_target(self) -> tuple[dict[str, Any] | None, bool]:
        """What Clayton points at now (:class:`whatsthis.Pointer`): the game is asked; a key
        report from the last :data:`KEY_FRESH_S` stands in if it doesn't answer in time.
        Not from the event loop's own thread (it waits for the loop)."""
        link, loop = self.newest(), self.core.loop
        if link is None or loop is None:
            return None, False
        target = None
        try:
            target = asyncio.run_coroutine_threadsafe(self._probe(link), loop).result(
                PROBE_TIMEOUT_S + 1.0
            )
        except (TimeoutError, RuntimeError):
            target = None
        if target is None:
            with self._lock:
                key = self.last_key
            if key is not None and self.clock() - key[0] <= KEY_FRESH_S:
                target = key[1]
        return target, True

    async def _probe(self, link: Link) -> dict[str, Any] | None:
        probe = self._next_probe
        self._next_probe += 1
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._probes[probe] = future
        try:
            link.put({"type": "probe", "id": probe})
            return await asyncio.wait_for(future, PROBE_TIMEOUT_S)
        except TimeoutError:
            return None
        finally:
            self._probes.pop(probe, None)

    def notice(self, text: str, kind: str = "notice") -> None:
        """A local chat line in every connected game (D76); any thread."""
        loop = self.core.loop
        if loop is None:
            return
        message = _notice(text, kind)

        def send() -> None:
            with self._lock:
                links = list(self.links)
            for link in links:
                link.put(message)

        loop.call_soon_threadsafe(send)

    def health_changed(self, health: dict[str, Any]) -> None:
        """A notice when a health strip item turns to error, and when it recovers (D76)."""
        items = health.get("items")
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            was = self._health.get(item["id"])
            now = item.get("status")
            self._health[item["id"]] = str(now)
            label = item.get("label") or item["id"]
            if now == HEALTH_ERROR and was not in (None, HEALTH_ERROR):
                detail = item.get("detail") or "failing"
                self.notice(f"{label}: {detail}", "failure")
            elif was == HEALTH_ERROR and now != HEALTH_ERROR:
                self.notice(f"{label} is back.", "notice")

    def state(self) -> dict[str, Any]:
        with self._lock:
            links = list(self.links)
        newest = max(links, key=lambda k: k.last_seen, default=None)
        return {
            "links": [
                {
                    "binding": k.binding,
                    "player": k.player,
                    "mod_version": k.mod_version,
                    "feeds": k.feeds,
                    "since": int(k.since),
                    "last_seen": int(k.last_seen),
                }
                for k in links
            ],
            "status": newest.status if newest else None,
            "chat": list(self.chat)[-20:],
        }


def _notice(text: str, kind: str, **extra: object) -> dict[str, object]:
    return {"type": "notice", "kind": kind, "text": text, **extra}


def add_routes(
    app: FastAPI, core: Core, principal: Callable[[Request], Principal]
) -> None:
    """``WS /mod/ws`` and ``GET /api/game`` (one call from :func:`gregtec.api.app.create_app`)."""
    if core.gamelink is None:
        core.gamelink = GameLink(core)
    link = core.gamelink
    if core.asker is not None:  # "what's this?" asks the game (D286)
        core.asker.game = link

    @app.websocket(PATH)
    async def mod_socket(ws: WebSocket) -> None:
        await link.serve(ws)

    @app.get("/api/game")
    def game(request: Request) -> dict[str, Any]:
        principal(request)  # admin, a client or a paired device
        return link.state()
