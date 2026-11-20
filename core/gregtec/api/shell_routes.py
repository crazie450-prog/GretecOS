"""Decorative shells over HTTP (WO-10.7, plan 7.10, 7.14, D316-D318).

| route               | who                   | what                                                     |
|---------------------|-----------------------|----------------------------------------------------------|
| GET /api/shells     | admin, client, device | the saved shell builds, newest first (summaries)          |
| GET /api/shells/{id}| admin, client, device | ``{"card": shell card}``: preview, manifest, paste steps |
| POST /api/shells    | admin, device         | ``{"plans": [ids], "theme"?, "line_type"?}``: the procedural shell (No-AI mode), saved unless refused; ``{"card"}`` |

The workbench (WO-10.10, D367-D370):

| route                                   | who                   | what                                     |
|-----------------------------------------|-----------------------|------------------------------------------|
| GET  /api/shells/designs                | admin, client, device | ``{"designs": [...], "current": ds}``: every design, the one touched last first |
| GET  /api/shells/designs/{ds}           | admin, client, device | ``{"design"}``: its versions (parent, asked, by, time, cost, blocks) and the current one |
| POST /api/shells/designs/{ds}/current   | admin, device         | ``{"build"}``: make that version current (undo is an older one); nothing is deleted |
| POST /api/shells/jobs                   | admin, device         | ``{"design": {plans, theme?, line_type?, prompt?, max_blocks?, headroom?}}`` or ``{"revise": {design?, words, theme?, max_blocks?, headroom?}}``: a shell job (stages, Cancel; one at a time), GOS's own generator or reviser, no model |
| GET  /api/shells/compare?a=&b=          | admin, client, device | two versions' materials and block counts side by side |
| GET  /api/shells/{id}/view              | admin, client, device | the 3D viewer's data: cells, palette with the catalog's colours, layers, the floor plan's machines and keep-outs as ghosts |

The shell card is data (plan 7.14 rule 1); the Build view draws it. Nothing here writes a world or
a ``.schematic``: ``gos shell export`` is Clayton's (it reads the Lab world's registry, D318).

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from gregtec.api.security import Principal

if TYPE_CHECKING:
    from gregtec.api.app import Core

JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def listed(home: Path) -> dict[str, Any]:
    from gregtec.build import shell_store

    return {"shells": shell_store.builds(home)}


def shown(home: Path, build: str) -> dict[str, Any]:
    from gregtec.build import shell, shell_store

    try:
        return {"card": shell_store.card(home, build)}
    except shell.ShellError as exc:
        raise HTTPException(404, str(exc)) from None


def designed(home: Path, body: dict[str, Any]) -> dict[str, Any]:
    """The procedural shell for the plans (no model): saved, or refused with the reasons."""
    from gregtec.build import palette, shell, shell_store
    from gregtec.build import themes as theme_lib
    from gregtec.db import access
    from gregtec.vault.io import VaultIO

    plans = body.get("plans")
    theme = body.get("theme")
    line_type = body.get("line_type", theme_lib.GENERAL)
    if (
        not isinstance(plans, list)
        or not plans
        or not all(isinstance(p, str) and len(p) <= 64 for p in plans)
        or not (theme is None or isinstance(theme, str))
        or not isinstance(line_type, str)
    ):
        raise HTTPException(400, "send plans (a list of plan ids), theme and line_type")
    db = access.db_path(home)
    if not db.is_file():
        raise HTTPException(503, "no recipe DB: run gos db build")
    conn = access.connect(db, readonly=True)
    try:
        made = shell_store.make(
            conn,
            home,
            VaultIO(home / "vault"),
            plans,
            theme_id=theme,
            line_type=line_type,
        )
    except (shell.ShellError, palette.CatalogError) as exc:
        raise HTTPException(400, str(exc)) from None
    except sqlite3.Error as exc:
        raise HTTPException(
            503, f"the DB can't be used now ({type(exc).__name__})"
        ) from None
    finally:
        conn.close()
    if not made.outcome.ok:
        raise HTTPException(400, "refused: " + " | ".join(made.outcome.reasons))
    from gregtec.build import shell_versions as versions

    versions.start(home, made.build, asked="the Designer's button", by="procedural")
    return {"card": shell_store.card(home, made.build)}


def _int_or_none(body: dict[str, Any], key: str) -> int | None:
    v = body.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise HTTPException(400, f"{key} is a whole number")
    return v


def shell_job(home: Path, body: dict[str, Any]) -> tuple[str, Any]:
    """(title, work) for a workbench job, its request checked now (a 400 when it is wrong)."""
    from gregtec.build import palette, shell, shell_store
    from gregtec.build import shell_versions as versions
    from gregtec.build import themes as theme_lib
    from gregtec.db import access
    from gregtec.vault.io import VaultIO

    if set(body) not in ({"design"}, {"revise"}):
        raise HTTPException(400, "send design or revise")
    db = access.db_path(home)
    if not db.is_file():
        raise HTTPException(503, "no recipe DB: run gos db build")
    if "design" in body:
        req = body["design"]
        if not isinstance(req, dict):
            raise HTTPException(400, "design is an object")
        plans, theme = req.get("plans"), req.get("theme")
        line_type = req.get("line_type", theme_lib.GENERAL)
        prompt = req.get("prompt", "")
        if (
            not isinstance(plans, list)
            or not 1 <= len(plans) <= shell.MAX_PLANS
            or not all(isinstance(p, str) and len(p) <= 64 for p in plans)
            or not (theme is None or isinstance(theme, str))
            or line_type not in theme_lib.LINE_TYPES
            or not isinstance(prompt, str)
            or len(prompt) > theme_lib.MAX_TEXT * 5
        ):
            raise HTTPException(
                400, "send plans (1-3 plan ids), theme, line_type and prompt"
            )
        try:
            mb, hr = shell.options(
                _int_or_none(req, "max_blocks"), _int_or_none(req, "headroom")
            )
        except shell.ShellError as exc:
            raise HTTPException(400, str(exc)) from None

        def design() -> dict[str, Any]:
            conn = access.connect(db, readonly=True)
            try:
                made = shell_store.make(
                    conn,
                    home,
                    VaultIO(home / "vault"),
                    plans,
                    theme_id=theme,
                    prompt=prompt,
                    line_type=line_type,
                    max_blocks=mb,
                    headroom=hr,
                )
            except (shell.ShellError, palette.CatalogError) as exc:
                raise HTTPException(400, str(exc)) from None
            finally:
                conn.close()
            if not made.outcome.ok:
                return {"refused": list(made.outcome.reasons)}
            d = versions.start(
                home, made.build, asked=prompt or "designed", by="procedural"
            )
            return {"design": d["id"], "build": made.build}

        return f"Shell: design around {', '.join(plans)}", design
    req = body["revise"]
    if not isinstance(req, dict):
        raise HTTPException(400, "revise is an object")
    ds, words, theme = req.get("design"), req.get("words", ""), req.get("theme")
    if (
        not (ds is None or (isinstance(ds, str) and versions.DESIGN_ID.fullmatch(ds)))
        or not isinstance(words, str)
        or len(words) > 400
        or not (theme is None or isinstance(theme, str))
    ):
        raise HTTPException(400, "send design (ds-<12 hex>), words and theme")
    mb, hr = _int_or_none(req, "max_blocks"), _int_or_none(req, "headroom")
    try:
        shell.options(mb, hr)
        target = ds or versions.current_id(home)
        if target is None:
            raise HTTPException(400, "there is no shell yet: design one first")
        versions.get(home, target)
    except shell.ShellError as exc:
        raise HTTPException(
            404 if "no shell design" in str(exc) else 400, str(exc)
        ) from None
    if not words.strip() and mb is None and hr is None and theme is None:
        raise HTTPException(400, "say what to change, or change the theme or a size")

    def revise() -> dict[str, Any]:
        conn = access.connect(db, readonly=True)
        try:
            got = versions.revise(
                conn,
                home,
                VaultIO(home / "vault"),
                target,
                words,
                theme_id=theme,
                max_blocks=mb,
                headroom=hr,
            )
        except (shell.ShellError, palette.CatalogError) as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            conn.close()
        if not got.saved:
            return {"refused": list(got.reasons), "design": target, "build": got.parent}
        return {"design": target, "build": got.build, "changes": list(got.changes)}

    return f"Shell: revise {target}: {words[:80]}", revise


def viewed(home: Path, build: str) -> dict[str, Any]:
    from gregtec.build import palette, shell
    from gregtec.build import shell_versions as versions
    from gregtec.db import access

    db = access.db_path(home)
    if not db.is_file():
        raise HTTPException(503, "no recipe DB: run gos db build")
    conn = access.connect(db, readonly=True)
    try:
        return versions.viewer(conn, home, build)
    except (shell.ShellError, palette.CatalogError) as exc:
        raise HTTPException(
            404 if "no shell build" in str(exc) else 409, str(exc)
        ) from None
    finally:
        conn.close()


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

    from gregtec.api import job_routes
    from gregtec.build import shell
    from gregtec.build import shell_versions as versions

    def chain(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return fn()
        except shell.ShellError as exc:
            raise HTTPException(
                404 if "no shell" in str(exc) else 400, str(exc)
            ) from None

    # Before /api/shells/{build}: "designs" and "compare" aren't build ids.
    @app.get("/api/shells/designs")
    async def shell_designs(who: Anyone) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            return {
                "designs": versions.designs(home()),
                "current": versions.current_id(home()),
            }

        return await asyncio.to_thread(chain, run)

    @app.get("/api/shells/themes")
    async def shell_themes(who: Anyone) -> dict[str, Any]:
        """The themes the workbench's picker offers (WO-10.6's, from the vault)."""

        def run() -> dict[str, Any]:
            from gregtec.build import themes as theme_lib
            from gregtec.vault.io import VaultIO

            found, problems = theme_lib.Themes(VaultIO(home() / "vault")).load()
            rows = [
                {"id": t.id, "name": t.name, "v": t.v, "summary": t.summary}
                for t in found
            ]
            return {
                "themes": rows,
                "line_types": list(theme_lib.LINE_TYPES),
                "limits": {k: list(v) for k, v in shell.LIMITS.items()},
                "problems": [str(p) for p in problems][:20],
            }

        return await asyncio.to_thread(run)

    @app.get("/api/shells/designs/{ds}")
    async def shell_design_get(ds: str, who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(
            chain, lambda: {"design": versions.get(home(), ds)}
        )

    @app.post("/api/shells/designs/{ds}/current")
    async def shell_make_current(
        ds: str, request: Request, who: Trusted
    ) -> dict[str, Any]:
        body = await json_body(request)
        build = body.get("build")
        if set(body) != {"build"} or not isinstance(build, str):
            raise HTTPException(400, "send build (sh-<12 hex>)")
        return await asyncio.to_thread(
            chain, lambda: {"design": versions.make_current(home(), ds, build)}
        )

    @app.post("/api/shells/jobs")
    async def shell_job_start(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        title, work = await asyncio.to_thread(chain, lambda: shell_job(home(), body))
        try:
            job = job_routes.registry(core).start("shell", title, work)
        except job_routes.Busy as exc:
            raise HTTPException(409, str(exc)) from None
        return {"job": job.as_dict()}

    @app.get("/api/shells/compare")
    async def shell_compare(a: str, b: str, who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(chain, lambda: versions.compare(home(), a, b))

    @app.get("/api/shells/{build}/view")
    async def shell_view(build: str, who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(viewed, home(), build)

    @app.get("/api/shells")
    async def shell_list(who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(listed, home())

    @app.get("/api/shells/{build}")
    async def shell_get(build: str, who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(shown, home(), build)

    @app.post("/api/shells")
    async def shell_design(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        return await asyncio.to_thread(designed, home(), body)
