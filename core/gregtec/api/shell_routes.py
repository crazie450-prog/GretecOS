"""Decorative shells over HTTP (WO-10.7, plan 7.10, 7.14, D316-D318).

| route               | who                   | what                                                     |
|---------------------|-----------------------|----------------------------------------------------------|
| GET /api/shells     | admin, client, device | the saved shell builds, newest first (summaries)          |
| GET /api/shells/{id}| admin, client, device | ``{"card": shell card}``: preview, manifest, paste steps |
| POST /api/shells    | admin, device         | ``{"plans": [ids], "theme"?, "line_type"?, "mode"?, "site_factor"?}`` or ``{"mode": "free", "area": N, ...}`` (a plan-free building, D424): the procedural shell (No-AI mode), saved unless refused; ``{"card"}`` |

The workbench (WO-10.10, D367-D370):

| route                                   | who                   | what                                     |
|-----------------------------------------|-----------------------|------------------------------------------|
| GET  /api/shells/designs                | admin, client, device | ``{"designs": [...], "current": ds}``: every design, the one touched last first |
| GET  /api/shells/designs/{ds}           | admin, client, device | ``{"design"}``: its versions (parent, asked, by, time, cost, blocks) and the current one |
| POST /api/shells/designs/{ds}/current   | admin, device         | ``{"build"}``: make that version current (undo is an older one); nothing is deleted |
| POST /api/shells/jobs                   | admin, device         | ``{"design": {plans, theme?, line_type?, prompt?, max_blocks?, headroom?, mode?, site_factor?, ai?}}`` (``mode`` layout by default, or site; ``{mode: "free", area, ...}`` with no plans is a plan-free building, D424) or ``{"revise": {design?, words, theme?, max_blocks?, headroom?, ai?}}``: a shell job (stages, Cancel; one at a time), GOS's own generator or reviser, no model; with ``ai: true`` the live shell designer draws it (D423: ``[shell] designer``; a design's mode is then site by default; a revise takes words only), a 409 with the why when none is set or it isn't ready |
| GET  /api/shells/designer               | admin, client, device | the live shell designer (D423): ``{"configured": false}``, or its label, provider, ready and why not, this request's cap and what today leaves (never a model id) |
| GET  /api/shells/plot?area=&factor=&headroom= | admin, client, device | a plan-free building's plot (D424): ``{"area", "factor", "size": [x, y, z]}`` from :func:`gregtec.build.shell.site_for_area`, or a 400 with the sentence (the workbench's note; it never computes the plot itself) |
| GET  /api/shells/compare?a=&b=          | admin, client, device | two versions' materials and block counts side by side |
| GET  /api/shells/{id}/view              | admin, client, device | the 3D viewer's data: cells, palette with the catalog's colours, layers, the floor plan's machines and keep-outs as ghosts |

The bake-off (WO-10.11, D374-D377): runs are started only by ``gos shell bakeoff run`` (spending
is Clayton's, at the CLI); these routes show a run **blind** and take his ratings. Before the
report is written nothing here names a model, a provider, a cost, a token count or a time.

| route                                        | who                   | what                                |
|----------------------------------------------|-----------------------|-------------------------------------|
| GET  /api/shells/bakeoff                     | admin, client, device | ``{"runs": [...]}``: the runs, newest first (blind summaries) |
| GET  /api/shells/bakeoff/{run}               | admin, client, device | ``{"run": the blind view, "report": the report's table card or null}`` |
| GET  /api/shells/bakeoff/{run}/view?case=&letter=&which= | admin, client, device | one blind entry's 3D viewer data (``which``: design or revise) |
| GET  /api/shells/bakeoff/{run}/compare?case=&a=&b=&which= | admin, client, device | two blind entries of a case side by side |
| POST /api/shells/bakeoff/{run}/rate          | admin, device         | ``{"case", "pick"}``: a letter, ``tie`` or ``bad``; refused once the models are named |
| POST /api/shells/bakeoff/{run}/report        | admin, device         | ``{}``: write the report and name the models; refused while cases are unrated (giving up blind rating is ``gos shell bakeoff report RUN --reveal``, at the CLI only, D377: ``{"reveal": true}`` is refused) |

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
    from gregtec.build import shell_brief, shell_designer
    from gregtec.planner.design_jobs import Lock

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


def _mode_of(
    req: dict[str, Any], headroom: int | None = None
) -> tuple[str, list[str], int | None, float | None]:
    """A design request's mode, plans, area and site factor (D424), checked: a plan-free
    building (mode free) takes an area and no plans, the others 1 to 3 plans and no area; a
    site factor is for site or free. A 400 with the sentence when wrong."""
    from gregtec.build import shell, shell_store

    mode, plans = req.get("mode", "layout"), req.get("plans")
    area, factor = req.get("area"), req.get("site_factor")
    if mode not in shell_store.MODES:
        raise HTTPException(400, f"mode is one of {', '.join(shell_store.MODES)}")
    try:
        if factor is not None:
            if mode == "layout":
                raise shell.ShellError("a site factor is for a site or plan-free build")
            factor = shell.check_factor(factor)
        if mode == "free":
            if plans or area is None:
                raise shell.ShellError(
                    "a plan-free building takes an area and no plans"
                )
            if req.get("zones"):
                raise shell.ShellError(
                    "a plan-free building has no zones (zones steer where machines go)"
                )
            shell.site_for_area(
                shell.check_area(area),
                None,
                headroom,
                shell.SITE_FACTOR if factor is None else factor,
            )
            return mode, [], area, factor
        if area is not None:
            raise shell.ShellError(
                "an area is only for a plan-free building (mode free)"
            )
    except shell.ShellError as exc:
        raise HTTPException(400, str(exc)) from None
    if (
        not isinstance(plans, list)
        or not 1 <= len(plans) <= shell.MAX_PLANS
        or not all(isinstance(p, str) and len(p) <= 64 for p in plans)
    ):
        raise HTTPException(400, "name 1 to 3 saved plans (plan ids)")
    return mode, plans, None, factor


def designed(home: Path, body: dict[str, Any]) -> dict[str, Any]:
    """The procedural shell for the plans (no model), or a plan-free building (D424): saved,
    or refused with the reasons."""
    from gregtec.build import palette, shell, shell_store
    from gregtec.build import themes as theme_lib
    from gregtec.db import access
    from gregtec.vault.io import VaultIO

    plans = body.get("plans")
    theme = body.get("theme")
    line_type = body.get("line_type", theme_lib.GENERAL)
    if (
        not (body.get("mode") == "free" or isinstance(plans, list))
        or not (theme is None or isinstance(theme, str))
        or not isinstance(line_type, str)
    ):
        raise HTTPException(400, "send plans (a list of plan ids), theme and line_type")
    mode, plans, area, factor = _mode_of(body)
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
            mode=mode,
            area=area,
            site_factor=factor,
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


def plotted(area: int, factor: float | None, headroom: int | None) -> dict[str, Any]:
    """A plan-free building's plot for ``area`` (D424), as the design job will size it."""
    from gregtec.build import shell

    try:
        f = shell.SITE_FACTOR if factor is None else shell.check_factor(factor)
        box = shell.site_for_area(shell.check_area(area), None, headroom, f)
    except shell.ShellError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"area": area, "factor": f, "size": list(box.size)}


def designer_ctx(core: "Core") -> "Callable[[], shell_designer.Ctx]":
    """The live shell designer's context on the core's gos.toml, meter, store and AI mode, made
    when a job with ``ai: true`` is asked for (D423), as ``plan_routes.reviewer_for`` makes the
    route reviewer's."""

    def make() -> "shell_designer.Ctx":
        import secrets

        from gregtec.api import job_routes
        from gregtec.build import shell_designer

        return shell_designer.Ctx(
            core.cfg,
            core.cfg.gos_home,
            store=core.store,
            meter=core.meter,
            ai=core.ai,
            lock=job_routes.registry(core).planner_lock,
            path=core.config_path,
            task=f"shell-{secrets.token_hex(4)}",
        )

    return make


def _ready(ctx: "shell_designer.Ctx") -> str:
    """The configured designer's label, or a 409 with why there is none ready (no job)."""
    from gregtec.build import shell_designer

    ready = shell_designer.resolve(ctx.cfg, ctx.path)
    if ready is None:
        raise HTTPException(409, shell_designer.UNSET_WHY)
    if isinstance(ready, shell_designer.NotReady):
        raise HTTPException(409, ready.why)
    return ready.label


def shell_job(
    home: Path,
    body: dict[str, Any],
    lock: "Lock | None" = None,
    designer: "Callable[[], shell_designer.Ctx] | None" = None,
) -> tuple[str, Any]:
    """(title, work) for a workbench job, its request checked now (a 400 when it is wrong).
    ``lock``: the planner's (default :data:`gregtec.planner.designer._LOCK`), which GOS's own
    generator and reviser hold for their whole work; ``designer``: the live shell designer's
    context, for ``ai: true`` (D423; None: refused)."""
    from gregtec.build import palette, shell, shell_designer, shell_store
    from gregtec.build import shell_versions as versions
    from gregtec.build import themes as theme_lib
    from gregtec.db import access
    from gregtec.planner import design_jobs, progress
    from gregtec.vault.io import VaultIO

    if lock is None:
        from gregtec.planner.designer import _LOCK as lock
    if set(body) not in ({"design"}, {"revise"}):
        raise HTTPException(400, "send design or revise")
    db = access.db_path(home)
    if not db.is_file():
        raise HTTPException(503, "no recipe DB: run gos db build")
    which = body.get("design", body.get("revise"))
    ai = which.get("ai", False) if isinstance(which, dict) else False
    if not isinstance(ai, bool):
        raise HTTPException(400, "ai is true or false")
    ctx = None
    if ai:
        if designer is None:
            raise HTTPException(409, "the shell designer runs in the core (gos serve)")
        ctx = designer()
        label = _ready(ctx)
    if "design" in body:
        req = body["design"]
        if not isinstance(req, dict):
            raise HTTPException(400, "design is an object")
        if ai:  # D422: a new AI design is a site design unless it says otherwise
            req = {"mode": "site", **req}
        plans, theme = req.get("plans"), req.get("theme")
        line_type = req.get("line_type", theme_lib.GENERAL)
        prompt = req.get("prompt", "")
        if (
            not (
                req.get("mode") == "free"
                or (
                    isinstance(plans, list)
                    and 1 <= len(plans) <= shell.MAX_PLANS
                    and all(isinstance(p, str) and len(p) <= 64 for p in plans)
                )
            )
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
        mode, plans, area, factor = _mode_of(req, hr)
        if ctx is not None:
            ask = shell_brief_request(
                plans, prompt, mode, theme, line_type, mb, hr, area, factor
            )

            def drawn() -> dict[str, Any]:
                return shell_designer.design(ctx, ask, progress.stage).result

            if area is not None:
                return (
                    f"Shell: {label} designs a plan-free building (≥{area:,} floor)",
                    drawn,
                )
            return f"Shell: {label} designs around {', '.join(plans)}", drawn

        def design() -> dict[str, Any]:
            with design_jobs.planner(lock):
                return _designed()

        def _designed() -> dict[str, Any]:
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
                    mode=mode,
                    area=area,
                    site_factor=factor,
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

        if area is not None:
            return f"Shell: a plan-free building (≥{area:,} floor)", design
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
    if ctx is not None:
        if mb is not None or hr is not None or theme is not None:
            raise HTTPException(
                400,
                "the architect revises from your words alone: change the theme or a size "
                "with Revise (GOS's reviser)",
            )
        if not words.strip():
            raise HTTPException(400, "say what to change")

        def redrawn() -> dict[str, Any]:
            return shell_designer.revise(ctx, target, words, progress.stage).result

        return f"Shell: {label} revises {target}: {words[:80]}", redrawn
    if not words.strip() and mb is None and hr is None and theme is None:
        raise HTTPException(400, "say what to change, or change the theme or a size")

    def revise() -> dict[str, Any]:
        with design_jobs.planner(lock):
            return _revised()

    def _revised() -> dict[str, Any]:
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


def shell_brief_request(
    plans: list[str],
    prompt: str,
    mode: str,
    theme: str | None,
    line_type: str,
    max_blocks: int | None,
    headroom: int | None,
    area: int | None,
    factor: float | None,
) -> "shell_brief.DesignRequest":
    """A workbench design job's request as the live designer's brief takes it."""
    from gregtec.build import shell_brief

    return shell_brief.DesignRequest(
        tuple(plans),
        prompt,
        mode=mode,
        theme=theme,
        line_type=line_type,
        max_blocks=max_blocks,
        headroom=headroom,
        area=area,
        site_factor=factor,
    )


def start_job(core: "Core", body: dict[str, Any]) -> dict[str, Any]:
    """Start a shell job for the agents (the architect's ``ask_designer``, a chat revise):
    checked and started as ``POST /api/shells/jobs`` starts it; refused as
    :class:`gregtec.agents.tools.registry.ShellJobError` with the sentence."""
    from gregtec.agents.tools.registry import ShellJobError
    from gregtec.api import job_routes
    from gregtec.build import shell

    jobs = job_routes.registry(core)
    try:
        title, work = shell_job(
            core.cfg.gos_home, body, jobs.planner_lock, designer_ctx(core)
        )
        return jobs.start("shell", title, work).as_dict(result=False)
    except HTTPException as exc:
        raise ShellJobError(str(exc.detail)) from None
    except (shell.ShellError, job_routes.Busy) as exc:
        raise ShellJobError(str(exc)) from None


def viewed(home: Path, build: str) -> dict[str, Any]:
    from gregtec.build import palette, shell
    from gregtec.build import shell_versions as versions
    from gregtec.db import access

    db = access.db_path(home)  # the catalog only names and colours the blocks
    conn = access.connect(db, readonly=True) if db.is_file() else None
    try:
        return versions.viewer(conn, home, build)
    except (shell.ShellError, palette.CatalogError) as exc:
        raise HTTPException(
            404 if "no shell build" in str(exc) else 409, str(exc)
        ) from None
    finally:
        if conn is not None:
            conn.close()


def _bakeoff(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    from gregtec.build import palette, shell
    from gregtec.build import shell_bakeoff as bo

    try:
        return fn()
    except (shell.ShellError, palette.CatalogError) as exc:
        text = str(exc)
        status = (
            404
            if text.startswith(("no bake-off run", "no shell build", "no such"))
            or "is not a bake-off run id" in text
            else 409
            if "ratings are closed" in text or "still running" in text
            else 503  # a file held open by another program: said, never a 500
            if bo.BUSY in text
            else 400
        )
        raise HTTPException(status, text) from None


def bakeoff_run(home: Path, run_id: str) -> dict[str, Any]:
    from gregtec.build import shell_bakeoff as bo

    run = bo.load(home, run_id)
    return {"run": bo.blind(run), "report": bo.card_of(home, run)}


def bakeoff_scene(
    home: Path, run_id: str, case: str, letter: str, which: str
) -> dict[str, Any]:
    from gregtec.build import shell_bakeoff as bo
    from gregtec.db import access

    db = access.db_path(home)  # the catalog only names and colours the blocks
    conn = access.connect(db, readonly=True) if db.is_file() else None
    try:
        return bo.scene(conn, home, run_id, case, letter, which)
    finally:
        if conn is not None:
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
                # a plan-free building's floor and the plot factor (D424): lowest, highest
                "plot": {
                    "area": list(shell.AREA),
                    "site_factor": [shell.SITE_FACTOR, *shell.SITE_FACTORS],
                },
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
        jobs = job_routes.registry(core)
        title, work = await asyncio.to_thread(
            chain,
            lambda: shell_job(home(), body, jobs.planner_lock, designer_ctx(core)),
        )
        try:
            job = jobs.start("shell", title, work)
        except job_routes.Busy as exc:
            raise HTTPException(409, str(exc)) from None
        return {"job": job.as_dict()}

    @app.get("/api/shells/designer")
    async def shell_designer_status(who: Anyone) -> dict[str, Any]:
        from gregtec.build import shell_designer

        return await asyncio.to_thread(
            shell_designer.status, core.cfg, core.store, core.ai, core.config_path
        )

    @app.get("/api/shells/plot")
    async def shell_plot(
        area: int,
        who: Anyone,
        factor: float | None = None,
        headroom: int | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(plotted, area, factor, headroom)

    @app.get("/api/shells/compare")
    async def shell_compare(a: str, b: str, who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(chain, lambda: versions.compare(home(), a, b))

    # The bake-off (WO-10.11): before /api/shells/{build}, "bakeoff" isn't a build id.
    @app.get("/api/shells/bakeoff")
    async def shell_bakeoff_runs(who: Anyone) -> dict[str, Any]:
        from gregtec.build import shell_bakeoff as bo

        return await asyncio.to_thread(_bakeoff, lambda: {"runs": bo.runs(home())})

    @app.get("/api/shells/bakeoff/{run}")
    async def shell_bakeoff_run(run: str, who: Anyone) -> dict[str, Any]:
        return await asyncio.to_thread(_bakeoff, lambda: bakeoff_run(home(), run))

    @app.get("/api/shells/bakeoff/{run}/view")
    async def shell_bakeoff_view(
        run: str, case: str, letter: str, who: Anyone, which: str = "design"
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            _bakeoff, lambda: bakeoff_scene(home(), run, case, letter, which)
        )

    @app.get("/api/shells/bakeoff/{run}/compare")
    async def shell_bakeoff_compare(
        run: str, case: str, a: str, b: str, who: Anyone, which: str = "design"
    ) -> dict[str, Any]:
        from gregtec.build import shell_bakeoff as bo

        return await asyncio.to_thread(
            _bakeoff, lambda: bo.compare(home(), run, case, a, b, which)
        )

    @app.post("/api/shells/bakeoff/{run}/rate")
    async def shell_bakeoff_rate(
        run: str, request: Request, who: Trusted
    ) -> dict[str, Any]:
        from gregtec.build import shell_bakeoff as bo

        body = await json_body(request)
        case, pick = body.get("case"), body.get("pick")
        if set(body) != {"case", "pick"} or not (
            isinstance(case, str) and isinstance(pick, str)
        ):
            raise HTTPException(400, "send case and pick (a letter, tie or bad)")
        return await asyncio.to_thread(
            _bakeoff, lambda: {"run": bo.blind(bo.rate(home(), run, case, pick))}
        )

    @app.post("/api/shells/bakeoff/{run}/report")
    async def shell_bakeoff_report(
        run: str, request: Request, who: Trusted
    ) -> dict[str, Any]:
        from gregtec.build import shell_bakeoff as bo

        body = await json_body(request)
        reveal = body.get("reveal", False)
        if set(body) - {"reveal"} or not isinstance(reveal, bool):
            raise HTTPException(400, "send nothing (or reveal: false)")
        if (
            reveal
        ):  # D377: giving up blind rating is his own typed command, never a request
            raise HTTPException(
                400,
                "naming the models before every case is rated is done at the command line "
                f"only: gos shell bakeoff report {run} --reveal",
            )

        def write() -> dict[str, Any]:
            bo.report(home(), run)
            return bakeoff_run(home(), run)

        return await asyncio.to_thread(_bakeoff, write)

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
