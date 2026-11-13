"""A saved plan's outputs over HTTP (WO-3.6; plan 7.1 step 9, 7.12, 7.14).

| route                               | who                         | what                          |
|-------------------------------------|-----------------------------|-------------------------------|
| GET /api/plans/{id}/cards           | admin, client, device       | ``{"flow": card, "bom": card}`` (catalog cards, D160) |
| GET /api/plans/{id}/export/{format} | admin, client, device       | the export file: ``bom`` (TSV), ``mermaid``, ``png``, ``gtnhplanner`` (JSON), ``gtnhflow`` (YAML) |
| GET /api/plans                      | admin, client, device       | saved plans, newest first (id, name, status, kind) |
| GET /api/designer                   | admin, client, device       | the Designer's latest design: ``{"ids": [...]}`` |
| GET /api/plans/{id}/designer        | admin, client, device       | the Designer's view of the plan's routes (WO-3.7) |
| GET /api/plans/{id}/alternatives    | admin, client, device       | ``?node=<key>[&all=1]``: a node's candidate recipes (D61) |
| POST /api/designer/design           | admin, device               | a spec (``designer.request_from_spec``): solve, save, view; ``{"clarify": card}`` when the target is unclear |
| POST /api/plans/{id}/pin            | admin, device               | ``{"node", "recipe"}`` (``null`` frees it): re-solve with the pin |
| POST /api/plans/{id}/resolve        | admin, device               | solve the plan's request again |
| POST /api/plans/{id}/accept         | admin, device               | proposed -> accepted: notes, its EU ledger line |
| POST /api/plans/{id}/name           | admin, device               | ``{"name"}``: a human name ([QA:D13]) |
| POST /api/plans/{id}/status         | admin, device               | ``{"status"}``: building, running or retired; the EU ledger follows (WO-4.4) |
| GET /api/ledger                     | admin, client, device       | the EU ledger (WO-4.4): data, its ``power_ledger`` card and the running scope's |

The dashboard draws the flow card (React Flow + elkjs) and the BOM card; Discord (WO-9.x, the
client token ``discord``) attaches the PNG and the BOM (D53). A plan is one ``gos plan --save``
(or WO-3.7's designer) saved under ``GOS_HOME/data/plans``. The Designer's routes
(:mod:`gregtec.planner.designer`, D250) solve and write: only Clayton's paired dashboards and his
``gos`` commands (admin) may, never the client tokens (voice, Discord, OC) and never a model
(accepting is his, D251). Every number in their answers is the planner's.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from gregtec.api.security import Principal
from gregtec.packupdate.plans import PLAN_ID
from gregtec.planner.outputs import FILES  # a plain dict: imports no planner code

if TYPE_CHECKING:
    from gregtec.api.app import Core
    from gregtec.planner.designer import Designed

MEDIA = {
    "bom": "text/tab-separated-values; charset=utf-8",
    "mermaid": "text/plain; charset=utf-8",
    "png": "image/png",
    "gtnhplanner": "application/json",
    "gtnhflow": "application/yaml",
}  # one per FILES format


def _pid(text: str) -> str:
    if not PLAN_ID.fullmatch(text):
        raise HTTPException(400, "not a plan id")
    return text


def cards(home: Path, pid: str) -> dict[str, Any]:
    """The plan's flow and BOM cards, from its saved JSON and the DB it was solved on."""
    from gregtec.db import access
    from gregtec.planner.outputs import book, store
    from gregtec.planner.outputs import cards as plan_cards

    try:
        plan = store.load(home, pid)
    except (OSError, ValueError) as exc:
        raise HTTPException(404, f"plan {pid} isn't saved") from exc
    db = access.db_path(home)
    if not db.is_file():
        raise HTTPException(503, "no recipe DB: run gos db build")
    conn = access.connect(db, readonly=True)
    try:
        b = book.read(conn, plan)
    except (LookupError, sqlite3.Error) as exc:
        raise HTTPException(409, f"plan {pid}: {exc}") from exc
    finally:
        conn.close()
    out = {"flow": plan_cards.flow_card(plan, b), "bom": plan_cards.bom_card(plan)}
    if "effort" in plan.get("metrics", {}):
        out["effort"] = plan_cards.effort_card(plan)  # WO-4.3, D237
    return out


JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def _run(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """A Designer call, its refusal as a 400 (404 for a plan that isn't saved). The planner is
    imported on first use, so the core starts without it."""
    from gregtec.planner.designer import DesignerError

    try:
        return fn()
    except DesignerError as exc:
        raise HTTPException(
            404 if "isn't saved" in str(exc) else 400, str(exc)
        ) from None
    except sqlite3.Error as exc:
        raise HTTPException(
            503, f"the DB can't be used now ({type(exc).__name__})"
        ) from None


def _changed(home: Path, d: "Designed") -> dict[str, Any]:
    from gregtec.planner import designer

    return {"ids": d.ids, "problems": d.problems, "view": designer.view(home, d.ids[0])}


def designed(home: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """POST /api/designer/design's answer: the view of the new design, or the question."""
    from gregtec.planner import designer

    req = designer.request_from_spec(home, spec)
    if not isinstance(req, designer.Request):
        return {"clarify": req.card()}
    return _changed(home, designer.design(home, req))


def accepted(home: Path, pid: str) -> dict[str, Any]:
    from gregtec.planner import designer

    done = designer.accept(home, pid)
    return {
        "id": done.id,
        "status": done.status,
        "line": done.line,
        "note": done.note,
        "ledger": done.ledger,
        "problems": done.problems,
        "view": designer.view(home, pid) if pid.startswith("pl-") else None,
    }


def moved(home: Path, pid: str, status: str) -> dict[str, Any]:
    """POST /api/plans/{id}/status's answer (WO-4.4): the move and the ledger after it."""
    from gregtec.planner import designer

    done = designer.set_status(home, pid, status)
    return {
        "id": done.id,
        "status": done.status,
        "line": done.line,
        "note": done.note,
        "ledger": done.ledger,
        "problems": done.problems,
    }


def ledger_view(home: Path) -> dict[str, Any]:
    """GET /api/ledger: the EU ledger (WO-4.4) as data and its two catalog cards."""
    from gregtec.db import access
    from gregtec.planner import ledger

    db = access.db_path(home)
    if not db.is_file():
        raise HTTPException(503, "no DB: run gos db build")
    conn = access.connect(db, readonly=True)
    try:
        led = ledger.read(conn)
    except sqlite3.Error as exc:
        raise HTTPException(
            503, f"the DB can't be used now ({type(exc).__name__})"
        ) from None
    finally:
        conn.close()
    return {
        "ledger": led.as_dict(),
        "card": ledger.card(led),
        "running": ledger.card(led, "running"),
    }


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    trusted: Callable[..., Principal],
    json_body: JsonBody,
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]
    Trusted = Annotated[Principal, Depends(trusted)]

    def home() -> Path:
        return core.cfg.gos_home

    async def call(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        return await asyncio.to_thread(_run, fn)

    def dsg() -> ModuleType:
        from gregtec.planner import designer

        return designer

    @app.get("/api/plans")
    async def plan_list(who: Anyone) -> dict[str, Any]:
        from gregtec.agents import saved_plans
        from gregtec.db import access

        found, problems = await asyncio.to_thread(
            saved_plans.load, access.db_path(home()), home()
        )
        return {
            "plans": [
                {"id": p.id, "name": p.name, "status": p.status, "kind": p.kind}
                for p in found
            ],
            "problems": problems,
        }

    @app.get("/api/designer")
    async def designer_latest(who: Anyone) -> dict[str, Any]:
        return {"ids": await asyncio.to_thread(lambda: dsg().latest(home()))}

    @app.get("/api/plans/{plan_id}/designer")
    async def designer_view(plan_id: str, who: Anyone) -> dict[str, Any]:
        pid = _pid(plan_id)
        return await call(lambda: dsg().view(home(), pid))

    @app.get("/api/plans/{plan_id}/alternatives")
    async def plan_alternatives(
        plan_id: str, who: Anyone, node: str = "", all: int = 0
    ) -> dict[str, Any]:
        pid = _pid(plan_id)
        if not node or len(node) > 300:
            raise HTTPException(400, "node is an item or fluid key")
        return await call(lambda: dsg().alternatives(home(), pid, node, bool(all)))

    @app.post("/api/designer/design")
    async def designer_design(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        return await call(lambda: designed(home(), body))

    @app.post("/api/plans/{plan_id}/pin")
    async def plan_pin(plan_id: str, request: Request, who: Trusted) -> dict[str, Any]:
        pid = _pid(plan_id)
        body = await json_body(request)
        node, recipe = body.get("node"), body.get("recipe")
        if not isinstance(node, str) or not (recipe is None or isinstance(recipe, str)):
            raise HTTPException(400, "send node (a key) and recipe (an r: id, or null)")
        return await call(
            lambda: _changed(home(), dsg().pin(home(), pid, node, recipe))
        )

    @app.post("/api/plans/{plan_id}/resolve")
    async def plan_resolve(plan_id: str, who: Trusted) -> dict[str, Any]:
        pid = _pid(plan_id)
        return await call(lambda: _changed(home(), dsg().resolve(home(), pid)))

    @app.post("/api/plans/{plan_id}/accept")
    async def plan_accept(plan_id: str, who: Trusted) -> dict[str, Any]:
        pid = _pid(plan_id)
        return await call(lambda: accepted(home(), pid))

    @app.post("/api/plans/{plan_id}/status")
    async def plan_status(
        plan_id: str, request: Request, who: Trusted
    ) -> dict[str, Any]:
        pid = _pid(plan_id)
        body = await json_body(request)
        status = body.get("status")
        if not isinstance(status, str):
            raise HTTPException(400, "send status: building, running or retired")
        return await call(lambda: moved(home(), pid, status))

    @app.get("/api/ledger")
    async def ledger_get(who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(ledger_view, home())

    @app.post("/api/plans/{plan_id}/name")
    async def plan_name(plan_id: str, request: Request, who: Trusted) -> dict[str, Any]:
        pid = _pid(plan_id)
        body = await json_body(request)

        def go() -> dict[str, Any]:
            dsg().rename(home(), pid, body.get("name"))
            return {"view": dsg().view(home(), pid)}

        return await call(go)

    @app.get("/api/plans/{plan_id}/cards")
    async def plan_cards(plan_id: str, who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(cards, core.cfg.gos_home, _pid(plan_id))

    @app.get("/api/plans/{plan_id}/export/{fmt}")
    def plan_file(plan_id: str, fmt: str, who: Anyone) -> FileResponse:
        if fmt not in FILES:
            raise HTTPException(404, f"format must be one of {', '.join(FILES)}")
        name, media = FILES[fmt], MEDIA[fmt]
        pid = _pid(plan_id)
        path = core.cfg.gos_home / "data" / "plans" / pid / name
        if not path.is_file():
            raise HTTPException(404, f"plan {pid} has no saved {fmt}")
        return FileResponse(path, media_type=media, filename=f"{pid}-{name}")
