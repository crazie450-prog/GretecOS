"""Design jobs over HTTP (WO-3.9; plan 7.1, 7.14 "the right rail shows running jobs", D353, D354).

| route                       | who                   | what                                     |
|-----------------------------|-----------------------|------------------------------------------|
| POST /api/designer/jobs     | admin, device         | ``{"spec": {...}}``, ``{"resolve": id}`` or ``{"pin": {"plan", "node", "recipe"}}``: the job at once (``{"job"}``), or ``{"clarify": card}`` when the target is unclear (no job) |
| POST /api/power/jobs        | admin, device         | a power spec (``powerview.request_from_spec``): the job at once |
| GET  /api/design-jobs              | admin, client, device | ``{"running": [...], "latest": {kind: job}}`` (a view opened again finds its job) |
| GET  /api/design-jobs/{id}         | admin, client, device | the job; once ``done``, its ``result`` is what ``POST /api/designer/design`` (or ``/api/power/design``) answers |
| POST /api/design-jobs/{id}/cancel  | admin, device         | stop it at its next checkpoint; nothing is saved |

The spec, the plan id or the pin is checked before the job starts, so a bad request is a 400 and
an unclear target is the question, as before. A second job of a kind while one runs is a 409 that
names it (:class:`gregtec.planner.design_jobs.Busy`). Every change is pushed to the dashboards as
``{"type": "job", "data": job}`` and in the state's ``jobs`` (the right rail). ``POST
/api/designer/design`` and ``POST /api/power/design`` still answer when the design is done, for
the callers that wait (WO-11.3's rehearsal, scripts); the agent tools, voice, the Discord queue
and the CLI call the planner's functions directly and are unchanged. Designing is Clayton's
paired dashboards' and his ``gos`` commands' (admin) only, never the client tokens and never a
model (D251).

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from gregtec.api import plan_routes
from gregtec.api.security import Principal
from gregtec.packupdate.plans import PLAN_ID
from gregtec.planner.design_jobs import KINDS, Busy, DesignJobs, Job, Work

if TYPE_CHECKING:
    from gregtec.agents.route_review import Reviewer
    from gregtec.api.app import Core

JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]
JOB_ID = r"job-[0-9a-f]{12}"


def checked[T](fn: Callable[[], T]) -> T:
    """``fn()`` with the Designer's refusals as HTTP errors (a 400, a 404 for a plan that
    isn't saved, a 503 for a DB that can't be read), as the waiting routes answer them."""
    return plan_routes._run(lambda: {"v": fn()})["v"]  # type: ignore[no-any-return]


def registry(core: "Core") -> DesignJobs:
    """The core's design jobs, made on first use; each change is pushed to every socket."""
    if core.design_jobs is None:
        from gregtec.api import dashboard
        from gregtec.planner import designer

        def changed(job: Job) -> None:
            data = job.as_dict(result=False)

            def push() -> None:
                core.broadcast({"type": "job", "data": data})
                if core.loop is not None:
                    core.loop.create_task(dashboard.push_state(core))

            core._threadsafe(push)

        core.design_jobs = DesignJobs(core.cfg.gos_home, designer._LOCK, changed)
    return core.design_jobs


def designer_work(
    home: Path,
    body: dict[str, Any],
    reviewer: Callable[[], "Reviewer"] | None = None,
) -> tuple[str, Work] | dict[str, Any]:
    """(title, work) for a Designer job, checked now; or the clarifying question's answer.
    A spec with ``review: true`` runs the route reviewer (``reviewer``) before saving."""
    from gregtec.planner import designer

    if set(body) - {"spec", "resolve", "pin"} or len(body) != 1:
        raise HTTPException(400, "send one of spec, resolve (a plan id) or pin")
    if "spec" in body:
        spec = body["spec"]
        if not isinstance(spec, dict):
            raise HTTPException(400, "spec is an object (the Designer's form)")
        req = designer.request_from_spec(home, spec)
        if not isinstance(req, designer.Request):
            return {"clarify": req.card()}
        tier = f" · {spec['tier']}" if spec.get("tier") else ""
        review = designer.wants_review(spec) and reviewer is not None
        title = f"Designer: {str(spec.get('target', '')).strip()}{tier}"
        title += " · reviewed" if review else ""
        if not (review and reviewer):
            return title, lambda: plan_routes._changed(home, designer.design(home, req))
        made = reviewer
        return title, lambda: plan_routes._changed(
            home, designer.design(home, req, reviewer=made())
        )
    if "resolve" in body:
        pid = body["resolve"]
        if not isinstance(pid, str) or not PLAN_ID.fullmatch(pid):
            raise HTTPException(400, "resolve is a plan id")
        designer.load(home, pid)  # 404 now when it isn't saved
        return (
            f"Designer: solve {pid} again",
            lambda: plan_routes._changed(home, designer.resolve(home, pid)),
        )
    pin = body["pin"]
    if not isinstance(pin, dict):
        raise HTTPException(400, "pin is {plan, node, recipe}")
    pid, node, recipe = pin.get("plan"), pin.get("node"), pin.get("recipe")
    if (
        not isinstance(pid, str)
        or not PLAN_ID.fullmatch(pid)
        or not isinstance(node, str)
        or not (recipe is None or isinstance(recipe, str))
    ):
        raise HTTPException(
            400, "send plan (an id), node (a key) and recipe (r: or null)"
        )
    designer.load(home, pid)
    what = f"pin {recipe}" if recipe else "free"
    return (
        f"Designer: {what} {node}",
        lambda: plan_routes._changed(home, designer.pin(home, pid, node, recipe)),
    )


def power_work(home: Path, spec: dict[str, Any]) -> tuple[str, Work]:
    from gregtec.planner import powerview

    req = powerview.request_from_spec(spec)
    tier = f" · {spec['tier']}" if spec.get("tier") else ""
    title = f"Power: {powerview.MODE_LABELS.get(req.mode, req.mode)}{tier}"

    def work() -> dict[str, Any]:
        d = powerview.design(home, req)
        return {"ids": d.ids, "view": powerview.view(home, d.ids[0])}

    return title, work


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

    def start(kind: str, made: tuple[str, Work] | dict[str, Any]) -> dict[str, Any]:
        if isinstance(made, dict):
            return made  # the question, no job
        title, work = made
        try:
            job = registry(core).start(kind, title, work)
        except Busy as exc:
            raise HTTPException(409, str(exc)) from None
        return {"job": job.as_dict()}

    def found(job_id: str) -> Job:
        job = registry(core).get(job_id) if re.fullmatch(JOB_ID, job_id) else None
        if job is None:
            raise HTTPException(
                404, f"no job {job_id} in this core (it may have restarted)"
            )
        return job

    @app.post("/api/designer/jobs")
    async def designer_job(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        made = await asyncio.to_thread(
            checked, lambda: designer_work(home(), body, plan_routes.reviewer_for(core))
        )
        return start("designer", made)

    @app.post("/api/power/jobs")
    async def power_job(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        made = await asyncio.to_thread(checked, lambda: power_work(home(), body))
        return start("power", made)

    @app.get("/api/design-jobs")
    def jobs_list(who: Anyone) -> dict[str, Any]:
        reg = registry(core)
        latest = {k: reg.latest(k) for k in KINDS}
        return {
            "running": [j.as_dict(result=False) for j in reg.running()],
            "latest": {
                k: j.as_dict(result=False) for k, j in latest.items() if j is not None
            },
        }

    @app.get("/api/design-jobs/{job_id}")
    def job_get(job_id: str, who: Anyone) -> dict[str, Any]:
        return {"job": found(job_id).as_dict()}

    @app.post("/api/design-jobs/{job_id}/cancel")
    def job_cancel(job_id: str, who: Trusted) -> dict[str, Any]:
        found(job_id)
        return {"job": registry(core).cancel(job_id).as_dict(result=False)}
