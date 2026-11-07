"""The dashboard's side of the core (WO-2.6, D159): the built UI at ``/`` and what its views read.

| route                    | who                   | what                                           |
|--------------------------|-----------------------|------------------------------------------------|
| GET  /                   | anyone (Host-checked) | the built dashboard (``dashboard/dist``)       |
| GET  /assets/..., /fonts/... | anyone            | its files: scripts, styles, fonts, licences    |
| GET  /api/state          | admin, client, device | game, data, AI, tier, alerts, approvals, jobs  |
| GET  /api/answers        | admin, device         | the last 50 answers, oldest first (Chat)       |
| GET  /api/recipe?ref=    | admin, client, device | the recipe card of one r: id (citation chips)  |
| POST /api/tools/{name}   | admin, client, device | a read-only lookup tool's result as a card     |
| GET  /api/verify         | admin, client, device | the VERIFY list with doctor's last statuses    |
| POST /api/doctor         | admin, device         | run ``gos doctor`` again                       |

The UI is public code, so ``/`` and its files need no credential: an unpaired browser has to load
the pairing screen. Everything under ``/api`` does, and every request still passes the Host and
Origin checks (QA:A8). The page is sent with a Content-Security-Policy that allows scripts, fonts
and connections to this origin only, so no font is ever fetched from Google at runtime, and no
other page may frame it. ``/`` on bare ``127.0.0.1`` or ``localhost`` redirects to the dashboard
name, the only one pairing works on (D117).

Pushed on ``/ws``: ``state`` (when it changes, at least every 15 s, and when a question starts or
is answered) and, to trusted sockets (admin, devices), ``ask.reply``: every answer of
``/api/ask`` as ``{"id", "by", "request", "asked_ts", "ts", ...the reply}``, so a spoken question's
card shows on monitor 2 too. Pairing codes also go only to trusted sockets. A card that fails
the catalog check (D160) is logged and left out.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import json
import logging
import mimetypes
import secrets
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from gregtec import cards, desktop, doctor, locks, prism
from gregtec.api.security import Principal
from gregtec.cards import build as cards_build
from gregtec.config import LOOPBACK, Config
from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.ingest import fingerprint
from gregtec.ingest import quests as bq

if TYPE_CHECKING:
    from gregtec.api.app import Core

log = logging.getLogger("gregtec.api")

REPO_ROOT = Path(__file__).resolve().parents[3]
DIST = REPO_ROOT / "dashboard" / "dist"
ANSWERS_KEEP = 50
FOCUS_EVERY_S = 1.0  # the foreground window: three Win32 calls
GAME_EVERY_S = 30.0  # running GTNH clients: PowerShell on Windows, about a second
DATA_EVERY_S = 5.0  # the stale flag, the last export, the instance lock, the tier
HEARTBEAT_S = 15.0
# The read-only lookups the views run (Search, the Ctrl+K palette, quest nudges). Never
# handoff (it starts paid deep-lane work) or the note tools (deep lane only).
TOOLS = (
    "item_resolve",
    "recipe_find",
    "recipe_uses",
    "machine_info",
    "quest_find",
    "quest_get",
    "quest_frontier",
    "quest_for_item",
)
FILE_ROOTS = ("assets", "fonts")  # the folders of dist/ served as they are
TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".js": "text/javascript"}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
PAGE_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}
NOT_BUILT = (
    "<!doctype html><meta charset=utf-8><title>Gregtec OS</title>"
    "<body style='font:18px system-ui;background:#050605;color:#e4eae6;padding:48px'>"
    "<h1>The dashboard isn't built</h1><p>Run <code>pnpm -C dashboard install</code> and "
    "<code>pnpm -C dashboard build</code>, or deploy a release (<code>gos deploy</code> builds "
    "it).</p></body>"
)
JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


@dataclass
class Board:
    """What the core keeps for the dashboard besides the health strip."""

    cfg: Config
    dist: Path = DIST
    focused: bool | None = None
    running: list[str] | None = None  # roles with a GTNH client; None until checked
    game_error: str | None = None
    export: dict[str, Any] | None = None  # who holds the instance lock (WO-1.4)
    data: dict[str, Any] = field(default_factory=dict)
    tier: dict[str, Any] | None = None
    # None: the real ones (desktop.foreground_program, prism.running_clients), looked up per call
    foreground: Callable[[], str | None] | None = None
    clients: Callable[[Config], list[str]] | None = None
    next_focus: float = 0.0
    next_game: float = 0.0
    next_data: float = 0.0
    last_push: float = 0.0
    signature: str = ""
    doctor_lock: threading.Lock = field(default_factory=threading.Lock)
    _answers: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=ANSWERS_KEEP)
    )
    _asking: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- answers (Chat, and Now's "last answer right after a question", D55)

    def asked(self, text: str) -> dict[str, Any]:
        entry = {"id": secrets.token_hex(6), "text": text, "ts": int(time.time())}
        with self._lock:
            self._asking[entry["id"]] = entry
        return entry

    def done(self, entry: dict[str, Any]) -> None:
        """The question is no longer being worked on (answered or failed)."""
        with self._lock:
            self._asking.pop(entry["id"], None)

    def answered(
        self, entry: dict[str, Any], reply: dict[str, Any], by: str
    ) -> dict[str, Any]:
        """Keep ``reply`` (an ``/api/ask`` answer) with who asked what, and when."""
        answer = {
            **reply,
            "id": entry["id"],
            "by": by,
            "request": entry["text"],
            "asked_ts": entry["ts"],
            "ts": int(time.time()),
        }
        with self._lock:
            self._asking.pop(entry["id"], None)
            self._answers.append(answer)
        return answer

    def answers(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._answers)

    def asking(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._asking.values())

    # --- what the views show (each refresh blocks: run it in a thread)

    def refresh_focus(self) -> None:
        try:
            program = (self.foreground or desktop.foreground_program)()
            self.focused = desktop.game_focused(program)
        except OSError:
            self.focused = None

    def refresh_game(self) -> None:
        try:
            found = (self.clients or prism.running_clients)(self.cfg)
            self.running, self.game_error = found, None
        except OSError as exc:
            self.running, self.game_error = None, str(exc)[:200]

    def refresh_data(self) -> None:
        """The stale flag and last export (WO-1.4), the instance lock, the DB's run, the tier."""
        home = self.cfg.gos_home
        state = fingerprint.load_state(home)
        last = fingerprint.last_export(home)
        stale = state.get("stale") if isinstance(state.get("stale"), bool) else None
        line = None
        if stale:
            line = (
                f"Play changed since export {last['run_id']}; export again to refresh it."
                if last
                else "No export is recorded yet, so recipe data may not match Play."
            )
        data: dict[str, Any] = {
            "stale": stale,
            "line": line,
            "last_export": (
                {"run_id": last["run_id"], "recorded_utc": last.get("recorded_utc")}
                if last
                else None
            ),
            "checked_utc": (state.get("play") or {}).get("checked_utc"),
            "db_run": None,
        }
        holder = locks.held(home)
        self.export = (
            {"holder": holder.holder, "since": holder.since, "reason": holder.reason}
            if holder
            else None
        )
        tier = None
        db = access.db_path(home)
        if db.is_file():
            try:
                conn = access.connect(db, readonly=True)
                try:
                    run = access.query(conn, "current_run")
                    if run:
                        data["db_run"] = {
                            "run_key": run[0]["run_key"],
                            "created_at": run[0]["created_at"],
                        }
                    stage, how, detected = quest_db.effective_tier(conn)
                    tier = {
                        "stage": stage,
                        "tier": bq.STAGES.get(stage),
                        "how": how,
                        "detected": detected,
                    }
                finally:
                    conn.close()
            except (sqlite3.Error, ValueError) as exc:
                log.info(
                    "dashboard data refresh skipped", extra={"why": str(exc)[:200]}
                )
        self.data, self.tier = data, tier


def _valid(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for card in found:
        problems = cards.validate(card)
        if problems:
            log.warning("card left out", extra={"why": "; ".join(problems)[:300]})
        else:
            out.append(card)
    return out


def state(core: "Core", trusted: bool) -> dict[str, Any]:
    """Everything the shell and Now read, as one snapshot (its cards built here, D160)."""
    board = board_of(core)
    now = time.time()
    health = core.health()
    alerts: list[dict[str, Any]] = []
    stale = cards_build.stale_alert(board.data.get("stale"), board.data.get("line"))
    if stale is not None:
        alerts.append(stale)
    try:
        credit = cards_build.credit_alert(core.meter.cycle())
    except sqlite3.Error:
        credit = None
    if credit is not None:
        alerts.append(credit)
    alerts += cards_build.health_alerts(health)
    alerts.sort(key=lambda a: a["level"] != "err")
    tasks = [t.as_dict() for t in core.deep.tasks()] if core.deep is not None else []
    approvals = [
        cards_build.deep_approval(t) for t in tasks if t["status"] == "needs_approval"
    ]
    if trusted:
        approvals += [
            cards_build.pairing_approval(p, now) for p in core.pairing.pending()
        ]
    jobs = [
        {
            "id": t["id"],
            "kind": "deep",
            "title": " ".join(str(t["request"]).split())[:200],
            "status": t["status"],
            "detail": (t["progress"] or [None])[-1],
            "started": t["started"] or t["created"],
        }
        for t in tasks
        if t["status"] in ("queued", "running", "needs_approval")
    ]
    if board.export is not None:
        jobs.append(
            {
                "id": "export",
                "kind": "export",
                "title": "Export running",
                "status": "running",
                "detail": board.export["reason"],
                "started": None,
            }
        )
    key_status, key_detail = core.key_status
    health_card = _valid([cards_build.health_card(health)])
    return {
        "ts": int(now),
        "game": {
            "running": board.running,
            "focused": board.focused,
            "paused": None,  # V29: the pause signal arrives with WO-6.1
            "export": board.export,
            "error": board.game_error,
        },
        "data": board.data,
        "ai": {"online": key_status == "ok", "detail": key_detail},
        "oc": {"status": "not_set_up", "detail": "the OC bridge arrives with WO-7.1"},
        "tier": board.tier,
        "alerts": _valid(alerts),
        "approvals": _valid(approvals),
        "jobs": jobs,
        "asking": board.asking(),
        "health": health_card[0] if health_card else None,
    }


def board_of(core: "Core") -> Board:
    if core.board is None:
        core.board = Board(core.cfg)
    return core.board


def _variants(core: "Core", sockets: set[bool]) -> dict[bool, dict[str, Any]]:
    return {trusted: state(core, trusted) for trusted in sockets | {True}}


def _send(core: "Core", variants: dict[bool, dict[str, Any]]) -> None:
    texts = {t: json.dumps({"type": "state", "data": s}) for t, s in variants.items()}
    for sock in list(core.sockets):
        sock.put(texts[sock.trusted])


async def push_state(core: "Core") -> None:
    """Build the state in a thread and send it to every socket (pairing codes to trusted only)."""
    kinds = {s.trusted for s in core.sockets}
    if kinds:
        _send(core, await asyncio.to_thread(_variants, core, kinds))


async def tick(core: "Core") -> None:
    """Once a second from the core's watcher: refresh what is due, push the state on change."""
    board = board_of(core)
    now = time.monotonic()
    if now >= board.next_focus:
        board.next_focus = now + FOCUS_EVERY_S
        await asyncio.to_thread(board.refresh_focus)
    if now >= board.next_game:
        board.next_game = now + GAME_EVERY_S
        await asyncio.to_thread(board.refresh_game)
    if now >= board.next_data:
        board.next_data = now + DATA_EVERY_S
        await asyncio.to_thread(board.refresh_data)
    variants = await asyncio.to_thread(
        _variants, core, {s.trusted for s in core.sockets}
    )
    signature = json.dumps(
        {k: v for k, v in variants[True].items() if k != "ts"}, sort_keys=True
    )
    if signature != board.signature or now - board.last_push >= HEARTBEAT_S:
        board.signature, board.last_push = signature, now
        _send(core, variants)


def checked_reply(reply: dict[str, Any]) -> dict[str, Any]:
    """An ``/api/ask`` reply whose card is a catalog card (else it goes without one)."""
    card = reply.get("card")
    if card is not None and (problems := cards.validate(card)):
        log.warning("reply card left out", extra={"why": "; ".join(problems)[:300]})
        reply = {**reply, "card": None}
    return reply


# --- the static UI ------------------------------------------------------------------------------


def _file(root: Path, rel: str) -> Path | None:
    """``root/rel`` when it is a file inside ``root`` (no ``..``, no link out of it)."""
    if not rel or "\\" in rel or any(p in ("", ".", "..") for p in rel.split("/")):
        return None
    try:
        resolved, base = (root / rel).resolve(strict=True), root.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_relative_to(base) or not resolved.is_file():
        return None
    return resolved


def _file_route(board: Board, top: str) -> Callable[[str], Response]:
    def dist_file(rel: str) -> Response:
        found = _file(board.dist / top, rel)
        if found is None:
            raise HTTPException(404, "no such file")
        kind = TYPES.get(found.suffix.lower()) or mimetypes.guess_type(found.name)[0]
        # Vite names assets by their content hash, so one name never changes content.
        cache = "public, max-age=31536000, immutable" if top == "assets" else "no-cache"
        return FileResponse(
            found,
            media_type=kind or "application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": cache},
        )

    return dist_file


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    trusted: Callable[..., Principal],
    json_body: JsonBody,
) -> None:
    """Add the dashboard's routes to the core's app (``create_app`` calls this once)."""
    board = board_of(core)
    Anyone = Annotated[Principal, Depends(principal)]
    Trusted = Annotated[Principal, Depends(trusted)]

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        name = (request.headers.get("host") or "").lower().split(":")[0]
        if name in (
            LOOPBACK,
            "localhost",
        ):  # pairing works on the dashboard name only (D117)
            return RedirectResponse(
                f"http://{core.cfg.dashboard_host}:{core.cfg.core_port}/",
                status_code=307,
            )
        page = board.dist / "index.html"
        if not page.is_file():
            return HTMLResponse(NOT_BUILT, status_code=503, headers=PAGE_HEADERS)
        return FileResponse(page, headers={**PAGE_HEADERS, "Cache-Control": "no-cache"})

    for top in FILE_ROOTS:
        app.add_api_route(
            f"/{top}/{{rel:path}}",
            _file_route(board, top),
            methods=["GET"],
            include_in_schema=False,
        )

    @app.get("/api/state")
    def get_state(who: Anyone) -> dict[str, Any]:
        return state(core, who.role in ("admin", "device"))

    @app.get("/api/answers")
    def get_answers(_: Trusted) -> list[dict[str, Any]]:
        return board.answers()

    @app.get("/api/recipe")
    def get_recipe(
        _: Anyone, ref: Annotated[str, Query(max_length=200)]
    ) -> dict[str, Any]:
        if not ref.startswith("r:") or any(c.isspace() for c in ref):
            raise HTTPException(400, "ref must be an r: citation id")
        db = access.db_path(core.cfg.gos_home)
        if not db.is_file():
            raise HTTPException(404, "no recipe DB yet: run gos db build")
        try:
            card = cards_build.recipe_card(db, [ref], title=ref)
        except sqlite3.Error as exc:
            raise HTTPException(
                503, f"the DB can't be read now ({type(exc).__name__})"
            ) from None
        if not card["recipes"]:
            raise HTTPException(404, "no recipe with that id in the newest export run")
        card["title"] = cards_build.clip(
            f"{card['recipes'][0]['machine']} · {ref}", 300
        )
        return _one(card)

    @app.post("/api/tools/{name}")
    async def run_tool(name: str, request: Request, _: Anyone) -> dict[str, Any]:
        if name not in TOOLS:
            raise HTTPException(404, "no such lookup tool")
        if core.asker is None:
            raise HTTPException(503, "the tools aren't set up in this core")
        args = (await json_body(request)).get("args", {})
        if not isinstance(args, dict):
            raise HTTPException(400, "args must be a JSON object")
        registry = core.asker.registry
        out = await asyncio.to_thread(registry.call, name, args, lane="fast")
        card = await asyncio.to_thread(
            cards_build.from_tool, name, out.text, out.is_error, registry.env.db
        )
        return {"card": _one(card)}

    @app.get("/api/verify")
    def verify(_: Anyone) -> dict[str, Any]:
        env = _env(core.cfg.gos_home)
        statuses = env.get("verify") if isinstance(env.get("verify"), dict) else {}
        return {
            "generated": env.get("generated"),
            "items": [
                {
                    "id": v.id,
                    "item": v.item,
                    "how": v.how,
                    "wo": v.wo,
                    "status": statuses.get(v.id),
                }
                for v in doctor.load_verify_list()
            ],
        }

    @app.post("/api/doctor")
    async def run_doctor(who: Trusted) -> dict[str, Any]:
        if not board.doctor_lock.acquire(blocking=False):
            raise HTTPException(409, "doctor is already running")
        try:
            out = StringIO()
            code = await asyncio.to_thread(
                doctor.main, None, core.cfg.gos_home, True, out
            )
        finally:
            board.doctor_lock.release()
        log.info(
            "doctor re-run from the dashboard", extra={"by": who.role, "exit": code}
        )
        return {"exit": code, "output": out.getvalue()[-20000:]}


def _one(card: dict[str, Any]) -> dict[str, Any]:
    problems = cards.validate(card)
    if problems:
        log.warning("card refused", extra={"why": "; ".join(problems)[:300]})
        raise HTTPException(500, "that card failed the catalog check (logged)")
    return card


def _env(home: Path) -> dict[str, Any]:
    """``data/env.json`` from doctor's last run, or {} when there is none."""
    try:
        data = json.loads((home / "data" / "env.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
