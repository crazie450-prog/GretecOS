"""The core's HTTP and WebSocket API (WO-2.1). Every route sits behind :class:`HostOriginGuard`.

| route                          | who                     | what                                  |
|--------------------------------|-------------------------|---------------------------------------|
| GET  /health                   | anyone (Host-checked)   | liveness for the supervisor's probe   |
| GET  /api/session              | admin, client, device   | who the caller is                     |
| GET  /api/health               | admin, client, device   | the dashboard health strip's data     |
| GET  /api/usage                | admin, client, device   | the credit cycle's llm_usage summary  |
| POST /api/ask                  | admin, client, device   | route and answer one request (WO-2.3) |
| POST /api/prewarm              | admin, client, device   | warm the fast lane's prompt cache     |
| POST /api/ui                   | admin, client, device   | a dashboard action (``next_card``)    |
| POST /api/voice/state          | admin, the voice client | the voice strip's state (WO-5.1)      |
| GET  /api/deep/tasks[/{id}]    | admin, client, device   | deep-lane tasks and their state       |
| POST /api/deep/tasks/{id}/approve | admin, device        | a paused task may spend another cap   |
| POST /api/deep/tasks/{id}/stop | admin, device           | end a queued, running or paused task  |
| POST /api/pair/start           | anyone, dashboard name  | ask to pair; the code goes elsewhere  |
| POST /api/pair/complete        | anyone, dashboard name  | the right code sets the device cookie |
| GET  /api/pair/pending         | admin, device           | pending requests with their codes     |
| POST /api/pair/{id}/deny       | admin, device           | drop a pending request                |
| GET  /api/devices              | admin, device           | paired devices                        |
| DELETE /api/devices/{id}       | admin, device           | revoke a device                       |
| GET/POST/DELETE /api/tokens... | admin                   | client bearer tokens                  |
| POST /oc/telemetry, /api/actions... | see ``gregtec.bridge.http`` | the OC bridge, confirm flow (WO-7.1)|
| POST /api/admin/shutdown       | admin                   | stop this core (``gos stop``)         |
| GET/POST /api/ai, POST /api/credits/decision | see ``ai_routes`` | no-AI mode, D48 (WO-2.8)  |
| GET/POST /api/persona          | see ``persona_routes``  | the persona and switching (WO-5.6)    |
| GET  /api/plans/{id}/...       | see ``plan_routes``     | a saved plan's cards and exports (WO-3.6)|
| GET  /api/quests?spoil=        | see ``quests.view``     | the Quests view's payload (WO-6.4)    |
| WS   /ws                       | admin, client, device   | pushes ``health``, ``pair.pending``, ``deep.task``|
|                                |                         | and, to admin and devices, ``ask.reply``, |
|                                |                         | ``ui``, ``voice.state``, ``state`` (D179) |
|                                |                         | and ``action`` (WO-7.1)               |
| GET  /, /api/state, ...        | see ``dashboard``       | the dashboard and what its views read (WO-2.6)|
| /api/setup...                  | admin, device           | the setup wizard (``setup_routes``, WO-2.9)|

Pairing works only on the dashboard's own names: ``[core] dashboard_host`` (``gos.localhost``,
which browsers resolve to loopback) and the tailnet name, never bare ``127.0.0.1`` or
``localhost``. Cookies ignore ports, so a cookie for ``localhost`` would also go to every other
web server on this PC that the browser opens, a dev server of gos-dev's included (D117).

Health strip items have ``id``, ``kind`` (process or check), ``label``, ``status`` (ok, warn,
error or off) and ``detail``; process items add ``state``, ``pid`` (the process's own),
``launcher_pid`` (the one the supervisor started: on Windows a venv's python.exe launcher),
``uptime_s``, ``restarts``, ``last_exit`` and ``last_restart_ts`` from the supervisor.

**Voice (WO-5.1, D179).** Every answer ``/api/ask`` gives is also pushed to the dashboards as
``{"type": "ask.reply", "data": {"id", "by", "request", "asked_ts", "ts", ...the reply}}`` (built
and kept by the dashboard's board, WO-2.6), so a spoken question's card shows on monitor 2 (D23);
``asked_ts`` (epoch seconds, when the question came) lets a dashboard keep the newest question's
card when an older answer finishes after it. A confirm or cancel that found nothing waiting
(``pending`` false) changed nothing and is neither kept nor pushed.
``/api/ui`` pushes ``{"type": "ui", "action", "by"}`` (the voice client's next-card hotkey).
The voice client reports its state (``idle``, ``listening``,
``thinking``, ``speaking``, ``off``; ``trigger`` ``key`` or ``wake``) to ``/api/voice/state``; the
core keeps the latest, pushes it as ``{"type": "voice.state", "data": {"state", "trigger",
"ts"}}`` and sends it to each dashboard that connects; when the voice client's WebSocket closes
the state is ``off``. ``/api/prewarm`` runs :meth:`Asker.prewarm` (the talk key went down).

No ``from __future__ import annotations`` here: FastAPI reads the route signatures at runtime,
and the ``Anyone``/``Trusted``/``Admin`` dependency aliases are local to :func:`create_app`.
"""

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import keyring.errors
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.websockets import WebSocketDisconnect

from gregtec import control
from gregtec.agents.ask import TEXT_MAX, Asker
from gregtec.agents.deep import DeepLaneService, DeepTask
from gregtec.agents.no_ai import AiMode, CreditWatch, Problem
from gregtec.agents.personas import Personas
from gregtec.api import ai_routes, dashboard, persona_routes, plan_routes, setup_routes
from gregtec.api.security import (
    COOKIE,
    COOKIE_MAX_AGE,
    PAIR_TTL_S,
    Auth,
    HostOriginGuard,
    Pairing,
    PairingError,
    Principal,
)
from gregtec.base import routes as base_routes
from gregtec.quests import view as quest_view
from gregtec.bridge import http as bridge_http
from gregtec.bridge.actions import Action
from gregtec.bridge.ingest import Bridge
from gregtec.bridge.ops import Ops
from gregtec.config import LOOPBACK, Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.setup.service import SetupService
from gregtec.store import Store
from gregtec.vault.scheduler import STOP_DEADLINE_S, TICK_S, Maintenance

log = logging.getLogger("gregtec.api")

SERVICE = control.CORE_SERVICE
VERSION = "0.0.0"
POLL_S = 1.0  # how often the core refreshes the supervisor's process table
KEY_CHECK_S = 30.0  # how often it asks the keyring whether the Anthropic key is stored
HEARTBEAT_S = 15.0  # a health push at least this often, changed or not
MAX_MESSAGE = 4096  # request bodies and WebSocket messages
# Process states from the supervisor and the strip status each one shows as.
STATE_STATUS = {
    "running": "ok",
    "starting": "warn",
    "unresponsive": "error",
    "backoff": "error",
    "stopped": "off",
}


QUEUE_MAX = (
    100  # a client that stopped reading drops messages instead of growing the queue
)
CLOSE = (
    None  # queued instead of a message: close the socket (its credential was revoked)
)
UI_ACTIONS = ("next_card",)  # what /api/ui pushes to the dashboards (WO-5.1 hotkeys)
VOICE_CLIENT = "voice"  # the voice client's token name (gos token new voice)
VOICE_STATES = ("idle", "listening", "thinking", "speaking", "off")
VOICE_TRIGGERS = ("key", "wake")


@dataclass(eq=False)
class Socket:
    """One WebSocket client: who it authenticated as, and its outgoing queue."""

    who: Principal
    queue: asyncio.Queue[str | None]

    @property
    def trusted(self) -> bool:
        """Admin or a paired device: may see pairing codes."""
        return self.who.role in ("admin", "device")

    def put(self, text: str) -> None:
        if self.queue.qsize() < QUEUE_MAX:
            self.queue.put_nowait(text)


@dataclass
class Core:
    """Everything the routes share. ``shutdown`` stops the server (set by the runner)."""

    cfg: Config
    store: Store
    auth: Auth
    meter: UsageMeter
    pairing: Pairing = field(default_factory=Pairing)
    supervisor_token: str | None = None
    shutdown: Callable[[], None] | None = None
    started: float = field(default_factory=time.time)
    supervisor_status: dict[str, Any] | None = None
    supervisor_error: str | None = None
    key_status: tuple[str, str] = ("warn", "not checked yet")
    sockets: set[Socket] = field(default_factory=set)
    loop: asyncio.AbstractEventLoop | None = None
    pushed_pending: list[str] = field(default_factory=list)  # request ids last pushed
    maintenance: Maintenance | None = (
        None  # idle maintenance and vault commits (WO-2.5)
    )
    # time.monotonic() when the runner was asked to stop (the session-end deadline, D143)
    stop_asked: float | None = None
    asker: Asker | None = None  # the router and the lanes (WO-2.3)
    deep: DeepLaneService | None = None  # the deep lane (WO-2.4)
    board: dashboard.Board | None = None  # the dashboard's own state (WO-2.6)
    voice: dict[str, Any] | None = None  # the voice client's latest state (WO-5.1)
    ai: AiMode | None = None  # no-AI mode (WO-2.8)
    credits: CreditWatch | None = None  # the D48 credit flow (WO-2.8)
    personas: Personas | None = None  # the persona packs (WO-5.6)
    probe: Callable[[], Problem | None] | None = (
        None  # None: the API with the stored key
    )
    exit_code: int = 0  # what server.run returns (D48 Stop: supervisor.EXIT_STOPPED)
    config_path: Path | None = (
        None  # the gos.toml it read (None: config.default_path())
    )
    setup: SetupService | None = None  # the setup wizard (WO-2.9), made on first use
    # The OC bridge's ingest, shared by M1 (WO-7.3) and /oc/telemetry (WO-7.1).
    oc: Bridge | None = None
    ops: Ops | None = None  # telemetry tables and the action desk (WO-7.1)

    # --- pushing to WebSocket clients

    def broadcast(self, message: dict[str, Any], *, trusted_only: bool = False) -> None:
        """Queue ``message`` for every socket (only admin/device sockets if ``trusted_only``)."""
        text = json.dumps(message)
        for sock in list(self.sockets):
            if trusted_only and not sock.trusted:
                continue
            sock.put(text)

    def _threadsafe(self, fn: Callable[[], None]) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(fn)

    def deep_changed(self, task: DeepTask) -> None:
        """The deep lane's ``on_change`` (its own thread): push the task to every socket."""
        data = task.as_dict()
        self._threadsafe(lambda: self.broadcast({"type": "deep.task", "data": data}))

    def action_changed(self, action: Action, say: str) -> None:
        """The action desk's ``on_change`` (any thread): push the action and the words GOS says
        about it (the read-back, a cancellation, a result) to every socket, and refresh the
        state so the approval card comes and goes."""
        data = {**action.as_dict(), "say": say}

        def push() -> None:
            self.broadcast({"type": "action", "data": data})
            if self.loop is not None:
                self.loop.create_task(dashboard.push_state(self))

        self._threadsafe(push)

    def set_voice(self, state: str, trigger: str | None) -> dict[str, Any]:
        """Keep the voice client's state and push it to the dashboards (loop only)."""
        self.voice = {"state": state, "trigger": trigger, "ts": int(time.time())}
        self.broadcast({"type": "voice.state", "data": self.voice}, trusted_only=True)
        return self.voice

    def pairing_changed(self) -> None:
        """Pairing's ``on_change``; may run on a worker thread."""
        self._threadsafe(self.push_pending)

    def push_pending(self) -> None:
        """Push the pending pairing requests to trusted sockets if they changed (loop only)."""
        pending = self.pairing.pending()
        ids = [p["request_id"] for p in pending]
        if ids != self.pushed_pending:
            self.pushed_pending = ids
            self.broadcast({"type": "pair.pending", "data": pending}, trusted_only=True)

    def disconnect(self, revoked: Callable[[Principal], bool]) -> None:
        """Close the open sockets of a revoked device or client (any thread)."""

        def close() -> None:
            for sock in list(self.sockets):
                if revoked(sock.who):
                    self.sockets.discard(sock)
                    sock.queue.put_nowait(CLOSE)

        self._threadsafe(close)

    # --- health

    def refresh_supervisor(self) -> None:
        """Fetch the supervisor's process table (blocking; run it in a thread)."""
        if self.supervisor_token is None:
            return
        try:
            self.supervisor_status = control.call(
                self.cfg.supervisor_port,
                "GET",
                "/status",
                token=self.supervisor_token,
                timeout=2.0,
            )
            self.supervisor_error = None
        except control.ControlError as exc:
            self.supervisor_status = None
            self.supervisor_error = str(exc)

    def refresh_key(self) -> None:
        try:
            stored = keys.has_key(keys.ANTHROPIC)
        except keyring.errors.KeyringError as exc:
            self.key_status = ("warn", f"keyring unavailable ({type(exc).__name__})")
            return
        if self.ai is not None:
            self.ai.key_seen(stored)
        self.key_status = (
            ("ok", "stored")
            if stored
            else ("warn", "not stored: gos key set anthropic")
        )

    def health(self) -> dict[str, Any]:
        now = time.time()
        items: list[dict[str, Any]] = []
        if self.supervisor_token is None:
            items.append(
                _item("supervisor", "process", "Supervisor", "off", "not supervised")
            )
            items.append(self._own_item(now))
        elif self.supervisor_status is None:
            items.append(
                _item(
                    "supervisor",
                    "process",
                    "Supervisor",
                    "error",
                    self.supervisor_error or "no answer yet",
                )
            )
            items.append(self._own_item(now))
        else:
            sup = self.supervisor_status
            items.append(
                _item(
                    "supervisor",
                    "process",
                    "Supervisor",
                    "ok",
                    "running",
                    pid=sup.get("pid"),
                    uptime_s=sup.get("uptime_s"),
                )
            )
            for proc in sup.get("processes", []):
                items.append(_process_item(proc))
        items.append(self._db_item())
        items.append(_item("anthropic_key", "check", "Anthropic key", *self.key_status))
        items.append(self._credits_item())
        if self.maintenance is not None:
            items.append(self._maintenance_item())
        if self.deep is not None:
            items.append(_item("deep_lane", "check", "Deep lane", *self.deep.health()))
        statuses = {i["status"] for i in items}
        overall = (
            "error" if "error" in statuses else "warn" if "warn" in statuses else "ok"
        )
        return {"ts": int(now), "overall": overall, "items": items}

    def _own_item(self, now: float) -> dict[str, Any]:
        return _item(
            "core",
            "process",
            "Core",
            "ok",
            "running",
            state="running",
            pid=os.getpid(),
            uptime_s=int(now - self.started),
            restarts=0,
            last_exit=None,
            last_restart_ts=None,
        )

    def _db_item(self) -> dict[str, Any]:
        try:
            self.store.rows("SELECT 1")
        except sqlite3.Error as exc:  # shown on the strip, not raised
            return _item("db", "check", "Database", "error", type(exc).__name__)
        return _item("db", "check", "Database", "ok", self.store.path.name)

    def _maintenance_item(self) -> dict[str, Any]:
        assert self.maintenance is not None
        try:
            status, detail = self.maintenance.health()
        except sqlite3.Error as exc:  # shown on the strip, not raised
            return _item(
                "maintenance", "check", "Maintenance", "error", type(exc).__name__
            )
        return _item("maintenance", "check", "Maintenance", status, detail)

    def _credits_item(self) -> dict[str, Any]:
        try:
            cycle = self.meter.cycle()
        except sqlite3.Error as exc:  # shown on the strip, not raised
            return _item("credits", "check", "API credits", "error", type(exc).__name__)
        crossed = cycle["crossed_pct"]
        top = max(self.meter.alerts_pct)
        status = "error" if top in crossed else "warn" if crossed else "ok"
        detail = (
            f"${cycle['spent_usd']:.2f} of ${cycle['budget_usd']:.2f} "
            f"this cycle ({cycle['pct']:.0f}%)"
        )
        return _item(
            "credits", "check", "API credits", status, detail, pct=cycle["pct"]
        )


def _item(
    item_id: str, kind: str, label: str, status: str, detail: str, **extra: object
) -> dict[str, Any]:
    return {
        "id": item_id,
        "kind": kind,
        "label": label,
        "status": status,
        "detail": detail,
        **extra,
    }


def _process_item(proc: dict[str, Any]) -> dict[str, Any]:
    state = str(proc.get("state"))
    return _item(
        str(proc.get("name")),
        "process",
        str(proc.get("label") or proc.get("name")),
        STATE_STATUS.get(state, "warn"),
        str(proc.get("detail") or state),
        state=state,
        pid=proc.get("pid"),
        launcher_pid=proc.get("launcher_pid"),
        uptime_s=proc.get("uptime_s"),
        restarts=proc.get("restarts", 0),
        last_exit=proc.get("last_exit"),
        last_restart_ts=proc.get("last_restart_ts"),
    )


def _health_signature(health: dict[str, Any]) -> str:
    """What a push is about: everything but the clock and uptimes."""
    items = [
        {k: v for k, v in item.items() if k not in ("uptime_s",)}
        for item in health["items"]
    ]
    return json.dumps([health["overall"], items], sort_keys=True)


def create_app(core: Core) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        core.loop = asyncio.get_running_loop()
        core.pairing.on_change = core.pairing_changed
        tasks = [asyncio.create_task(_watch(core))]
        if core.maintenance is not None:
            tasks.append(asyncio.create_task(_maintain(core.maintenance)))
        try:
            yield
        finally:
            if core.maintenance is not None:  # a running daily run ends after its job
                core.maintenance.stop()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if core.maintenance is not None:  # plan 6.5: commit at session end
                # D142 (g): and back up an overdue DB, in the time the supervisor leaves,
                # counted from the stop request (uvicorn's graceful shutdown came first)
                asked = time.monotonic() if core.stop_asked is None else core.stop_asked
                await asyncio.to_thread(
                    core.maintenance.session_end, deadline=asked + STOP_DEADLINE_S
                )

    app = FastAPI(
        title="Gregtec OS core",
        version=VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(HostOriginGuard, allowed_hosts=core.cfg.allowed_hosts)

    def principal(request: Request) -> Principal:
        found = core.auth.from_headers(
            request.headers.get("authorization"), request.cookies.get(COOKIE)
        )
        if found is None:
            raise HTTPException(401, "not paired: pair this device first")
        return found

    def trusted(who: Annotated[Principal, Depends(principal)]) -> Principal:
        if who.role not in ("admin", "device"):
            raise HTTPException(403, "only a paired device or Clayton's gos commands")
        return who

    def admin(who: Annotated[Principal, Depends(principal)]) -> Principal:
        if who.role != "admin":
            raise HTTPException(403, "only Clayton's gos commands")
        return who

    def pairing_host(request: Request) -> str:
        """The request's host name, if pairing may happen on it (see the module docstring)."""
        name = (request.headers.get("host") or "").lower().split(":")[0]
        if name in (LOOPBACK, "localhost"):
            raise HTTPException(
                403,
                f"pair on http://{core.cfg.dashboard_host}:{core.cfg.core_port}/ instead: "
                f"a cookie for {name} would also reach every other local web server",
            )
        return name

    Anyone = Annotated[Principal, Depends(principal)]
    Trusted = Annotated[Principal, Depends(trusted)]
    Admin = Annotated[Principal, Depends(admin)]
    PairingHost = Annotated[str, Depends(pairing_host)]

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/health")
    def health_probe() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": SERVICE,
            "version": VERSION,
            "pid": os.getpid(),
        }

    @app.get("/api/session")
    def session(who: Anyone) -> dict[str, Any]:
        return {"role": who.role, "name": who.name, "device_id": who.device_id}

    @app.get("/api/health")
    def health(_: Anyone) -> dict[str, Any]:
        return core.health()

    @app.get("/api/usage")
    def usage(_: Anyone) -> dict[str, Any]:
        return core.meter.cycle()

    @app.post("/api/ask")
    async def ask(request: Request, who: Anyone) -> dict[str, Any]:
        """``{"text": "..."}`` -> the reply (D152): text, card, lane, citations, timings."""
        if core.asker is None:
            raise HTTPException(503, "the agents aren't set up in this core")
        body = await _json_body(request)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
            raise HTTPException(400, f"text must be 1 to {TEXT_MAX} characters")
        board = dashboard.board_of(core)
        entry = board.asked(text)  # the rail shows what is being worked on
        # Whatever happens next, the question stops showing as asked.
        try:
            await dashboard.push_state(core)
            # Blocking (DB reads, the model call): in a thread, so the event loop keeps serving.
            via = bridge_http.confirm_via(
                who, request.headers.get("host"), core.cfg.dashboard_host
            )
            reply = await asyncio.to_thread(core.asker.ask, text, via=via)
        finally:
            board.done(entry)
        log.info(
            "ask answered",
            extra={
                "by": who.role,
                "lane": reply.lane,
                "intent": reply.intent,
                "ttft_ms": reply.ttft_ms,
                "checked": reply.checked,
            },
        )
        answer = dashboard.checked_reply(reply.as_dict())
        # Every answer's card shows on the dashboards, a spoken question's too (D23, D179); kept
        # in the DB for Chat, so in a thread. A confirm or cancel that found nothing waiting
        # changed nothing: it isn't kept or pushed, so the cancel hotkey, pressed to silence GOS,
        # leaves the dashboards' card alone. The push says when its question came (``asked_ts``)
        # and the dashboard orders answers by it, so an older question's answer that finishes
        # later doesn't replace a newer one's card.
        if reply.pending is not False:
            pushed = await asyncio.to_thread(board.answered, entry, answer, who.name)
            core.broadcast({"type": "ask.reply", "data": pushed}, trusted_only=True)
        await dashboard.push_state(core)
        return answer

    @app.post("/api/prewarm")
    async def prewarm(_: Anyone) -> dict[str, Any]:
        """Warm the fast lane's prompt cache for the request about to come (D152, D179)."""
        if core.asker is None:
            raise HTTPException(503, "the agents aren't set up in this core")
        return {"status": await asyncio.to_thread(core.asker.prewarm)}

    @app.post("/api/ui")
    async def ui_action(request: Request, who: Anyone) -> dict[str, Any]:
        """``{"action": "next_card"}`` -> pushed to the dashboards (the WO-5.1 hotkey)."""
        body = await _json_body(request)
        action = body.get("action")
        if action not in UI_ACTIONS:
            raise HTTPException(400, f"action must be one of {', '.join(UI_ACTIONS)}")
        core.broadcast(
            {"type": "ui", "action": action, "by": who.name}, trusted_only=True
        )
        return {"action": action}

    @app.post("/api/voice/state")
    async def voice_state(request: Request, who: Anyone) -> dict[str, Any]:
        """The voice client's state for the voice strip and orb (WO-5.1, D179)."""
        if who.role != "admin" and (who.role, who.name) != ("client", VOICE_CLIENT):
            raise HTTPException(403, "only the voice client reports the voice state")
        body = await _json_body(request)
        state, trigger = body.get("state"), body.get("trigger")
        if state not in VOICE_STATES:
            raise HTTPException(400, f"state must be one of {', '.join(VOICE_STATES)}")
        if trigger is not None and trigger not in VOICE_TRIGGERS:
            raise HTTPException(400, "trigger must be key, wake or null")
        return core.set_voice(state, trigger)

    def deep_lane() -> DeepLaneService:
        if core.deep is None:
            raise HTTPException(503, "the deep lane isn't set up in this core")
        return core.deep

    @app.get("/api/deep/tasks")
    def deep_tasks(_: Anyone) -> list[dict[str, Any]]:
        return [t.as_dict() for t in deep_lane().tasks()]

    @app.get("/api/deep/tasks/{task_id}")
    def deep_task(task_id: str, _: Anyone) -> dict[str, Any]:
        task = deep_lane().get(task_id)
        if task is None:
            raise HTTPException(404, "no such deep-lane task")
        return task.as_dict()

    # Approving spends money (D156), so only Clayton's own devices and commands may.
    @app.post("/api/deep/tasks/{task_id}/approve")
    async def deep_approve(task_id: str, who: Trusted) -> dict[str, Any]:
        lane = deep_lane()
        if not await asyncio.to_thread(lane.approve, task_id):
            raise HTTPException(409, "that task isn't waiting for approval")
        log.info("deep-lane task approved", extra={"task": task_id, "by": who.role})
        task = lane.get(task_id)
        return task.as_dict() if task else {}

    @app.post("/api/deep/tasks/{task_id}/stop")
    async def deep_stop(task_id: str, who: Trusted) -> dict[str, Any]:
        lane = deep_lane()
        if not await asyncio.to_thread(lane.stop_task, task_id):
            raise HTTPException(409, "that task isn't queued, running or paused")
        log.info("deep-lane task stopped", extra={"task": task_id, "by": who.role})
        task = lane.get(task_id)
        return task.as_dict() if task else {}

    @app.post("/api/pair/start")
    async def pair_start(request: Request, _: PairingHost) -> dict[str, Any]:
        body = await _json_body(request)
        try:
            pending = core.pairing.start(body.get("name"))
        except PairingError as exc:
            raise HTTPException(exc.status, exc.reason) from None
        # Only a prefix: gos-dev reads the logs (D82), and a full id would let it use up the
        # request's tries with wrong codes.
        log.info("pairing requested", extra={"request": _id_prefix(pending.request_id)})
        # The code is shown on monitor 2 and by `gos pair`, never sent back to the asker.
        return {"request_id": pending.request_id, "expires_in": PAIR_TTL_S}

    @app.post("/api/pair/complete")
    async def pair_complete(
        request: Request, response: Response, host: PairingHost
    ) -> dict[str, Any]:
        # The DB work runs in a thread, so a busy DB never blocks the event loop.
        body = await _json_body(request)
        try:
            name = core.pairing.complete(body.get("request_id"), body.get("code"))
        except PairingError as exc:
            log.warning(
                "pairing refused",
                extra={
                    "request": _id_prefix(str(body.get("request_id"))),
                    "why": exc.reason,
                },
            )
            raise HTTPException(exc.status, exc.reason) from None
        device_id, cookie = await asyncio.to_thread(core.auth.add_device, name)
        response.set_cookie(
            COOKIE,
            cookie,  # host-only: sent back to this host name alone
            max_age=COOKIE_MAX_AGE,
            path="/",
            httponly=True,
            samesite="strict",
            # The tailnet name is always HTTPS (Tailscale Serve); *.localhost is plain http.
            secure=not host.endswith(".localhost"),
        )
        log.info("device paired", extra={"device_id": device_id})
        return {"device_id": device_id, "name": name}

    @app.get("/api/pair/pending")
    def pair_pending(_: Trusted) -> list[dict[str, Any]]:
        return core.pairing.pending()

    @app.post("/api/pair/{request_id}/deny")
    def pair_deny(request_id: str, _: Trusted) -> dict[str, Any]:
        if not core.pairing.deny(request_id):
            raise HTTPException(404, "no such pairing request")
        return {"denied": request_id}

    @app.get("/api/devices")
    def devices(_: Trusted) -> list[dict[str, Any]]:
        return core.auth.devices()

    @app.delete("/api/devices/{device_id}")
    def revoke_device(device_id: str, who: Trusted) -> dict[str, Any]:
        if not core.auth.revoke_device(device_id):
            raise HTTPException(404, "no such paired device")
        core.disconnect(lambda w: w.device_id == device_id)
        log.info("device revoked", extra={"device_id": device_id, "by": who.role})
        return {"revoked": device_id}

    @app.get("/api/tokens")
    def tokens(_: Admin) -> list[dict[str, Any]]:
        return core.auth.client_tokens()

    @app.post("/api/tokens/{name}")
    def issue_token(name: str, _: Admin) -> dict[str, Any]:
        try:
            token = core.auth.issue_client_token(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        core.disconnect(
            lambda w: w.role == "client" and w.name == name
        )  # the old token's
        log.info("client token issued", extra={"client": name})
        return {"name": name, "token": token}

    @app.delete("/api/tokens/{name}")
    def revoke_token(name: str, _: Admin) -> dict[str, Any]:
        if not core.auth.revoke_client_token(name):
            raise HTTPException(404, "no such client token")
        core.disconnect(lambda w: w.role == "client" and w.name == name)
        log.info("client token revoked", extra={"client": name})
        return {"revoked": name}

    @app.post("/api/admin/shutdown")
    def shutdown(_: Admin) -> dict[str, Any]:
        if core.shutdown is None:
            raise HTTPException(409, "this core can't be stopped from the API")
        log.info("shutdown requested")
        core.shutdown()
        return {"stopping": True}

    @app.websocket("/ws")
    async def socket(ws: WebSocket) -> None:
        who = await asyncio.to_thread(
            core.auth.from_headers,
            ws.headers.get("authorization"),
            ws.cookies.get(COOKIE),
        )
        if who is None:
            await ws.close(code=1008, reason="not paired")
            return
        await ws.accept()
        sock = Socket(who, asyncio.Queue())
        core.sockets.add(sock)
        try:
            # "build": the dashboard build served; a page that loaded another reloads (D159).
            build = await asyncio.to_thread(dashboard.board_of(core).build)
            hello = {
                "type": "hello",
                "role": who.role,
                "name": who.name,
                "build": build,
            }
            await ws.send_text(json.dumps(hello))
            health = await asyncio.to_thread(core.health)
            await ws.send_text(json.dumps({"type": "health", "data": health}))
            if sock.trusted:
                pending = {"type": "pair.pending", "data": core.pairing.pending()}
                await ws.send_text(json.dumps(pending))
                if core.voice is not None:
                    voice = {"type": "voice.state", "data": core.voice}
                    await ws.send_text(json.dumps(voice))
            snapshot = await asyncio.to_thread(dashboard.state, core, sock.trusted)
            await ws.send_text(json.dumps({"type": "state", "data": snapshot}))
            await _pump(ws, sock)
        except WebSocketDisconnect:
            pass
        finally:
            core.sockets.discard(sock)
            if _is_voice(who) and not any(_is_voice(s.who) for s in core.sockets):
                core.set_voice("off", None)  # the voice client went away (D179)

    dashboard.add_routes(app, core, principal, trusted, _json_body)
    bridge_http.add_routes(app, core, principal)
    ai_routes.add_routes(app, core, principal, trusted, _json_body)
    base_routes.add_routes(app, core, principal, trusted, _json_body)
    persona_routes.add_routes(app, core, principal, _json_body)
    plan_routes.add_routes(app, core, principal)
    quest_view.add_routes(app, core, principal)
    setup_routes.add_routes(app, core, principal, trusted, _json_body)
    return app


def _is_voice(who: Principal) -> bool:
    return who.role == "client" and who.name == VOICE_CLIENT


def _id_prefix(request_id: str) -> str:
    """Enough of a pairing request id to match log lines, too little to use it."""
    return request_id[:4] + "..."


async def _json_body(request: Request) -> dict[str, Any]:
    # JSON only: a cross-site form post can't send it without a CORS preflight, which is refused.
    if (
        request.headers.get("content-type", "").split(";")[0].strip()
        != "application/json"
    ):
        raise HTTPException(415, "send application/json")
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_MESSAGE):
        raise HTTPException(413, "body too large")
    raw = b""
    async for (
        chunk
    ) in request.stream():  # a chunked body has no length: count as it comes
        raw += chunk
        if len(raw) > MAX_MESSAGE:
            raise HTTPException(413, "body too large")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "body isn't JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    return body


async def _pump(ws: WebSocket, sock: Socket) -> None:
    """Send queued pushes while answering pings, until the client goes away or is revoked."""

    async def sender() -> None:
        while True:
            text = await sock.queue.get()
            if text is CLOSE:
                await ws.close(code=1008, reason="revoked")
                return
            await ws.send_text(text)

    async def receiver() -> None:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            text = message.get("text")
            if text is None:
                continue  # binary frames mean nothing here
            if len(text) > MAX_MESSAGE:
                await ws.close(code=1009)
                return
            try:
                kind = json.loads(text).get("type")
            except (ValueError, AttributeError):
                kind = None
            if kind == "ping":
                sock.put(json.dumps({"type": "pong"}))

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # re-raise WebSocketDisconnect
    finally:
        for task in tasks:
            task.cancel()


async def _watch(core: Core) -> None:
    """Refresh health in the background and push it when it changes.

    Also pushes ``pair.pending`` when a pairing request expires, so monitor 2 never shows a dead
    code. Everything that touches the DB or the network runs in a thread.
    """
    last_signature = ""
    last_push = 0.0
    last_key = -KEY_CHECK_S
    while True:
        now = time.monotonic()
        try:
            if now - last_key >= KEY_CHECK_S:
                await asyncio.to_thread(core.refresh_key)
                last_key = now
            await asyncio.to_thread(core.refresh_supervisor)
            await asyncio.to_thread(
                ai_routes.refresh, core
            )  # credits, API probe (WO-2.8)
            if (
                core.ops is not None
            ):  # action expiry and timeouts, stale telemetry (WO-7.1)
                await asyncio.to_thread(core.ops.tick)
            health = await asyncio.to_thread(core.health)
            signature = _health_signature(health)
            if signature != last_signature or now - last_push >= HEARTBEAT_S:
                core.broadcast({"type": "health", "data": health})
                last_signature, last_push = signature, now
            core.push_pending()  # catches requests that expired
            await dashboard.tick(core)  # the dashboard's state (WO-2.6)
        except Exception:  # the watcher must keep running
            log.exception("health refresh failed")
        await asyncio.sleep(POLL_S)


async def _maintain(maintenance: Maintenance) -> None:
    """Once a minute: the vault's 10-minute commit batch and, when due and idle, the daily
    maintenance (WO-2.5, :mod:`gregtec.vault.scheduler`), in a thread."""
    while True:
        try:
            await asyncio.to_thread(maintenance.tick)
        except Exception:  # the loop must keep running
            log.exception("maintenance tick failed")
        await asyncio.sleep(TICK_S)
