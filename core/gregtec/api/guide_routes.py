"""The 3D build guide over HTTP (WO-10.1 spike, WO-10.8; plan 7.10, D304, D319, D320).

| route                              | who                   | what                                         |
|------------------------------------|-----------------------|----------------------------------------------|
| GET /api/guide                     | admin, client, device | the guide export's scene pages and size, and |
|                                    |                       | the frame's base address with a path token   |
| GET /guide/{token}/{path}          | the path token, on    | one file of the export, for the Build view's |
|                                    | the guide host only   | frame                                        |
| GET /api/build/guides              | admin, client, device | the controllers with a footprint             |
| GET /api/build/guide               | admin, client, device | one footprint as the guide draws it          |
|                                    |                       | (``controller``, ``variant``, ``facing``,    |
|                                    |                       | ``tier=<channel>=<value>``...)               |
| POST /api/build/guide/checklist    | admin, device         | tick or untick a layer; ``reset`` unticks all|

**The export** is ``GOS_HOME/data/guide-site`` (:func:`gregtec.build.guide.site_root`): Clayton's
copy of GuideNH's ``/guidenhc exportsite`` output, served as it is and never written by GOS. Its
files are served **only on the guide host** (``guide.<dashboard_host>:<port>``, D319), its own
origin: the framed page can't reach the dashboard's window, DOM, ``localStorage`` or (with its
Origin) ``/api``, and the dashboard's host-only cookie never goes there. The credential is the
path token :class:`gregtec.build.guide.Tokens` issues to a paired dashboard in ``GET /api/guide``,
never the cookie (D304). The frame is offered on the PC only: the phone's tailnet name has no guide
host name (Tailscale Serve gives one name), so the phone gets GOS's own viewer only.

**The guide data** comes from the footprint library and the block catalog
(:mod:`gregtec.build.guide_data`); the checklist is kept in the core's ``kv`` table (D320).

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from gregtec.api.dashboard import _file
from gregtec.api.security import Principal
from gregtec.build import footprints as fp
from gregtec.build import guide, guide_data
from gregtec.config import LOOPBACK
from gregtec.store import Store

if TYPE_CHECKING:
    from gregtec.api.app import Core

JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def _connect(home: Path) -> sqlite3.Connection:
    from gregtec.db import access

    db = access.db_path(home)
    if not db.is_file():
        raise HTTPException(503, "no recipe DB: run gos db build")
    return access.connect(db, readonly=True)


def guides(home: Path) -> dict[str, Any]:
    conn = _connect(home)
    try:
        return {"guides": guide_data.listing(fp.library(conn))}
    except sqlite3.Error as exc:
        raise HTTPException(
            503, f"the DB can't be used now ({type(exc).__name__})"
        ) from None
    finally:
        conn.close()


def _footprint(
    lib: fp.Library,
    controller: object,
    variant: object = None,
    facing: object = None,
    tiers: object = None,
) -> fp.Footprint:
    if not isinstance(controller, str) or len(controller) > 200:
        raise HTTPException(400, "send controller as <registry name>@<meta>")
    if variant is not None and (not isinstance(variant, str) or len(variant) > 200):
        raise HTTPException(400, "variant must be a footprint variant")
    if facing is not None and not isinstance(facing, str):
        raise HTTPException(400, "facing must be north, east, south or west")
    if tiers is not None and (
        not isinstance(tiers, list)
        or len(tiers) > 8
        or not all(isinstance(t, str) and len(t) <= 64 for t in tiers)
    ):
        raise HTTPException(400, "tiers must be <channel>=<value> items")
    try:
        reg, meta = fp.parse_controller(controller)
        return lib.get(
            reg,
            meta,
            variant,
            fp.parse_facing(facing) if facing else fp.BUILT_FACING,
            fp.parse_tiers(tiers or []),
        )
    except fp.FootprintError as exc:
        raise HTTPException(404, str(exc)) from None


def shown(
    home: Path,
    store: Store,
    controller: str,
    variant: str | None,
    facing: str | None,
    tiers: list[str],
) -> dict[str, Any]:
    conn = _connect(home)
    try:
        lib = fp.library(conn)
        f = _footprint(lib, controller, variant, facing, tiers)
        variants = [
            lib.get(f.registry_name, f.meta, v.variant)
            for v in lib.for_controller(f.registry_name, f.meta)
        ]
        looks = guide_data.looks(conn, f.materials())
        done = guide_data.checked(store, guide_data.checklist_key(f))
        return guide_data.guide(f, looks, variants, done)
    except sqlite3.Error as exc:
        raise HTTPException(
            503, f"the DB can't be used now ({type(exc).__name__})"
        ) from None
    finally:
        conn.close()


def ticked(home: Path, store: Store, body: dict[str, Any]) -> dict[str, Any]:
    conn = _connect(home)
    try:
        f = _footprint(
            fp.library(conn),
            body.get("controller"),
            body.get("variant"),
            body.get("facing"),
            body.get("tiers"),
        )
    except sqlite3.Error as exc:
        raise HTTPException(
            503, f"the DB can't be used now ({type(exc).__name__})"
        ) from None
    finally:
        conn.close()
    if body.get("reset") is True:
        guide_data.reset(store, f)
        return {"done": []}
    item, done = body.get("item"), body.get("done")
    if not isinstance(item, str) or not isinstance(done, bool):
        raise HTTPException(
            400, "send item (layer:<y>) and done (true or false), or reset"
        )
    try:
        return {"done": guide_data.check(store, f, item, done)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    trusted: Callable[..., Principal],
    json_body: JsonBody,
) -> None:
    """Add the guide's routes to the core's app (``create_app`` calls this once)."""
    Anyone = Annotated[Principal, Depends(principal)]
    Trusted = Annotated[Principal, Depends(trusted)]
    cfg = core.cfg
    scans = guide.ScanCache()
    tokens = guide.Tokens()
    guide_origin = f"http://{cfg.guide_host}"
    dashboard_origin = f"http://{cfg.dashboard_host}:{cfg.core_port}"
    local = {
        f"{LOOPBACK}:{cfg.core_port}",
        f"localhost:{cfg.core_port}",
        f"{cfg.dashboard_host}:{cfg.core_port}",
    }

    @app.get("/api/guide")
    async def guide_site(request: Request, _: Anyone) -> dict[str, Any]:
        site = await asyncio.to_thread(scans.scan, guide.site_root(cfg))
        out = site.as_dict()
        if (request.headers.get("host") or "").lower() in local:
            token, expires = tokens.issue()
            out["frame"] = {
                "base": f"{guide_origin}/guide/{token}/",
                "expires": expires,
            }
        else:
            out["frame"] = None  # the tailnet name: no guide host there (D319)
        return out

    @app.get("/guide/{token}/{rel:path}", include_in_schema=False)
    def guide_file(token: str, rel: str, request: Request) -> Response:
        if (request.headers.get("host") or "").lower() != cfg.guide_host.lower():
            raise HTTPException(404, "the guide is served on its own host")
        if not tokens.valid(token):
            raise HTTPException(
                403, "the guide link has run out: open the Build view again"
            )
        found = _file(guide.site_root(cfg), rel)
        kind = guide.media_type(found) if found is not None else None
        if found is None or kind is None:
            raise HTTPException(404, "no such guide file")
        return FileResponse(
            found,
            media_type=kind,
            headers=guide.frame_headers(guide_origin, dashboard_origin),
        )

    @app.get("/api/build/guides")
    async def build_guides(_: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(guides, cfg.gos_home)

    @app.get("/api/build/guide")
    async def build_guide(
        _: Anyone,
        controller: str,
        variant: str | None = None,
        facing: str | None = None,
        tier: Annotated[list[str] | None, Query()] = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            shown, cfg.gos_home, core.store, controller, variant, facing, tier or []
        )

    @app.post("/api/build/guide/checklist")
    async def build_checklist(request: Request, _: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        return await asyncio.to_thread(ticked, cfg.gos_home, core.store, body)
