"""The 3D build guide's export over HTTP (WO-10.1 spike, plan 7.10, D304).

| route              | who                   | what                                                    |
|--------------------|-----------------------|---------------------------------------------------------|
| GET /api/guide     | admin, client, device | whether a guide export is there, its scene pages, size  |
| GET /guide/{path}  | admin, client, device | one file of it, for the Build view's frame              |

The folder is ``GOS_HOME/data/guide-site`` (:func:`gregtec.build.guide.site_root`): Clayton's
copy of GuideNH's ``/guidenhc exportsite`` output, served as it is and never written by GOS. The
files need a credential like ``/api`` (a paired browser's cookie goes with the frame's requests),
and each one is sent with :func:`gregtec.build.guide.frame_headers`: a CSP that limits which files
the framed page loads to ``/guide/``. That CSP is not isolation (the frame is same-origin, so its
scripts can use ``window.parent.fetch``): the folder's contents are the trust boundary until
WO-10.8 serves ``/guide/`` from its own origin (D304).

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from gregtec.api.dashboard import _file
from gregtec.api.security import Principal
from gregtec.build import guide

if TYPE_CHECKING:
    from gregtec.api.app import Core


def add_routes(
    app: FastAPI, core: "Core", principal: Callable[[Request], Principal]
) -> None:
    """Add the guide's routes to the core's app (``create_app`` calls this once)."""
    Anyone = Annotated[Principal, Depends(principal)]

    @app.get("/api/guide")
    async def guide_site(_: Anyone) -> dict[str, Any]:
        site = await asyncio.to_thread(guide.scan, guide.site_root(core.cfg))
        return site.as_dict()

    @app.get("/guide/{rel:path}", include_in_schema=False)
    def guide_file(rel: str, request: Request, _: Anyone) -> Response:
        found = _file(guide.site_root(core.cfg), rel)
        kind = guide.media_type(found) if found is not None else None
        if found is None or kind is None:
            raise HTTPException(404, "no such guide file")
        # The Host was checked against [core] allowed_hosts before any route ran.
        origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        return FileResponse(found, media_type=kind, headers=guide.frame_headers(origin))
