"""The off-PC routes (WO-9.1, plan 7.12, D295, D297).

| route                          | who                      | what                                      |
|--------------------------------|--------------------------|-------------------------------------------|
| POST /api/jobs                 | admin, the Discord bot   | ``{"kind", "spec", "key"}``: queue a design job (idempotent by key) |
| GET  /api/jobs[?undelivered=1] | admin, the Discord bot   | the caller's jobs; finished, undelivered ones |
| GET  /api/jobs/{id}            | admin, the Discord bot   | one of the caller's jobs                  |
| POST /api/jobs/{id}/delivered  | admin, the Discord bot   | the result was posted                     |
| GET/POST /api/discord/alerts   | admin, device, the bot   | Discord alerts: ``on``, ``off``, ``quiet`` for N hours |
| POST /api/discord/journal      | the Discord bot          | one line of a Discord answer in the day's journal |

**Who may queue.** A design job writes proposed plans (D250, D243), which D251 keeps from the
client tokens; the one exception is the client named ``discord`` (as for the credit decision,
D165, and the persona, D206), because the bot relays only Clayton's own commands (his user id,
checked on every command and interaction, [QA:V9]). Pinning, re-solving, accepting and moving a
plan stay his dashboards' and ``gos`` commands' alone. A job runs no model (plan 0.1 principle
1), so ``/plan`` and ``/power`` work in no-AI mode (plan 7.16).

**Specs.** ``plan``: ``target`` (words or a key), one of ``rate`` ("2/s") or ``batch`` (with an
optional ``within``), optional ``tier``. ``power``: ``mode`` (one of the four, plan 7.2) and an
optional ``tier``. Anything else is refused before it is queued.

**Alerts** (``kv`` ``discord.alerts``): ``{"mode": "on" | "off" | "quiet", "until": epoch s}``;
quiet lasts 1 to 24 hours, then alerts are on again. WO-9.2's alert routing reads
:func:`alerts_state`.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from gregtec.api.security import Principal
from gregtec.planner.request import RequestError, parse_duration, parse_qty, tier_index
from gregtec.remote.jobs import Job, JobError, JobQueue
from gregtec.store import Store

if TYPE_CHECKING:
    from gregtec.api.app import Core

log = logging.getLogger("gregtec.remote")

DISCORD = "discord"  # the bot's client token name (gos token new discord)
PLAN_KEYS = frozenset({"target", "rate", "batch", "within", "tier"})
POWER_KEYS = frozenset({"mode", "tier"})
ALERTS_KEY = "discord.alerts"
ALERT_MODES = ("on", "off", "quiet")
QUIET_MAX_H = 24
JOURNAL_KINDS = ("ask", "deep", "status", "persona", "alerts", "action")
JOURNAL_MAX = 240

JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def check_spec(kind: str, spec: object) -> dict[str, Any]:
    """The spec as the designer takes it, or :class:`JobError` (a 400) saying why not."""
    if not isinstance(spec, Mapping):
        raise JobError("spec is an object")
    allowed = PLAN_KEYS if kind == "plan" else POWER_KEYS
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise JobError(f"unknown fields: {', '.join(unknown)}")
    out: dict[str, Any] = {}
    for key, value in spec.items():
        if value in (None, ""):
            continue
        if not isinstance(value, str) or len(value) > 200:
            raise JobError(f"{key} is text of at most 200 characters")
        out[key] = " ".join(value.split())
    if kind == "plan":
        if not out.get("target"):
            raise JobError("name a target item or fluid")
        if "rate" in out and "batch" in out:
            raise JobError("give a rate or a batch, not both")
        _check_amounts(out)
    elif not out.get("mode"):
        raise JobError("name a mode")
    return out


def _check_amounts(spec: Mapping[str, str]) -> None:
    """The rate, batch, within and tier parsed by the designer's own parsers
    (:func:`gregtec.planner.designer.request_from_spec`'s rules), so a typo is a 400 at once
    instead of a failed job later."""
    try:
        if "batch" in spec:
            if parse_qty(spec["batch"])[1] is not None:
                raise JobError("a batch is a quantity, without /s, /min or /h")
            if "within" in spec:
                parse_duration(spec["within"])
        else:
            if "within" in spec:
                raise JobError("within is for a batch only")
            if "rate" in spec and parse_qty(spec["rate"])[1] is None:
                raise JobError("a rate needs /s, /min or /h")
        if "tier" in spec:
            tier_index(spec["tier"])
    except RequestError as exc:
        raise JobError(str(exc)) from None


def run_job(home: Path) -> Callable[[str, Mapping[str, Any]], dict[str, Any]]:
    """The queue's runner: the Designer's or the Power view's own design call, the result
    kept small (ids, names and the planner's numbers for the reply)."""

    def run(kind: str, spec: Mapping[str, Any]) -> dict[str, Any]:
        if kind == "plan":
            from gregtec.api import plan_routes

            out = plan_routes.designed(home, dict(spec))
            if "clarify" in out:
                return {"clarify": out["clarify"]}
            return {
                "ids": out["ids"],
                "problems": [str(p)[:300] for p in out.get("problems") or ()][:5],
                "routes": [_route(r) for r in out["view"].get("routes") or ()][:4],
            }
        from gregtec.api import power_routes

        out = power_routes.designed(home, dict(spec))
        view = out["view"]
        return {
            "ids": out["ids"],
            "mode": view.get("mode"),
            "mode_label": view.get("mode_label"),
            "tier_cap": view.get("tier_cap"),
            "options": [_option(o) for o in view.get("options") or ()][:3],
        }

    return run


def _route(r: Mapping[str, Any]) -> dict[str, Any]:
    target = r.get("target") or {}
    metrics = r.get("metrics") or {}
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "column": r.get("column"),
        "position": r.get("position"),
        "of": r.get("of"),
        "target": target.get("name") or target.get("id"),
        "machines": metrics.get("machines"),
        "eut_avg": metrics.get("eut_avg"),
        "eff_h": metrics.get("eff_h"),
        "warnings": [str(w)[:200] for w in r.get("warnings") or ()][:3],
    }


def _option(o: Mapping[str, Any]) -> dict[str, Any]:
    gen = o.get("generator") or {}
    fuel = o.get("fuel") or {}
    return {
        "id": o.get("id"),
        "name": o.get("name"),
        "position": o.get("position"),
        "of": o.get("of"),
        "net_eut": o.get("net_eut"),
        "gross_eut": o.get("gross_eut"),
        "generator": gen.get("label"),
        "fuel": fuel.get("name"),
    }


def alerts_state(store: Store, now: float | None = None) -> dict[str, Any]:
    """Discord alerts as Clayton set them: ``mode`` (``quiet`` turns back to ``on`` when its
    hours are over) and ``until`` (epoch seconds, quiet only)."""
    now = time.time() if now is None else now
    rows = store.rows("SELECT value FROM kv WHERE key = ?", (ALERTS_KEY,))
    state: dict[str, Any] = {"mode": "on", "until": None}
    if rows:
        try:
            saved = json.loads(rows[0]["value"])
            if saved.get("mode") in ALERT_MODES:
                state = {"mode": saved["mode"], "until": saved.get("until")}
        except (ValueError, AttributeError):
            pass
    if state["mode"] == "quiet" and (
        not isinstance(state["until"], int | float) or state["until"] <= now
    ):
        state = {"mode": "on", "until": None}
    return state


def set_alerts(
    store: Store, mode: object, hours: object, now: float | None = None
) -> dict[str, Any]:
    if mode not in ALERT_MODES:
        raise HTTPException(400, f"mode is one of {', '.join(ALERT_MODES)}")
    now = time.time() if now is None else now
    until = None
    if mode == "quiet":
        if (
            isinstance(hours, bool)
            or not isinstance(hours, int | float)
            or not 1 <= hours <= QUIET_MAX_H
        ):
            raise HTTPException(400, f"quiet takes 1 to {QUIET_MAX_H} hours")
        until = int(now + float(hours) * 3600)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE"
            " SET value = excluded.value, upd = excluded.upd",
            (ALERTS_KEY, json.dumps({"mode": mode, "until": until}), int(now)),
        )
    return alerts_state(store, now)


def journal_line(kind: str, text: str) -> str:
    """One journal line (plan 6.2): folded to one line, links defused, cut to size. Discord
    text is data (plan 0.1 principle 8): it can't add lines or links of its own."""
    folded = " ".join(text.split())[:JOURNAL_MAX]
    return re.sub(r"\[(?=\[)", "[ ", f"discord_{kind}: {folded}")


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    json_body: JsonBody,
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]

    def caller(who: Principal) -> str:
        if who.role == "admin":
            return "admin"
        if (who.role, who.name) == ("client", DISCORD):
            return DISCORD
        raise HTTPException(403, "only gos and the Discord bot")

    def queue() -> JobQueue:
        if core.jobs is None:
            raise HTTPException(503, "the job queue isn't set up in this core")
        return core.jobs

    def mine(job_id: str, who: Principal) -> Job:
        found = queue().get(job_id, caller(who))
        if found is None:
            raise HTTPException(404, "no such job")
        return found

    @app.post("/api/jobs")
    async def job_submit(request: Request, who: Anyone) -> dict[str, Any]:
        client = caller(who)
        body = await json_body(request)
        kind, key = body.get("kind"), body.get("key")
        if kind not in ("plan", "power") or not isinstance(key, str):
            raise HTTPException(400, "send kind (plan or power), key and spec")
        try:
            spec = check_spec(kind, body.get("spec"))
            job, created = await asyncio.to_thread(
                queue().submit, client, key, kind, spec
            )
        except JobError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"job": job.as_dict(), "created": created}

    @app.get("/api/jobs")
    def job_list(who: Anyone, undelivered: int = 0) -> dict[str, Any]:
        found = queue().jobs(caller(who), undelivered=bool(undelivered))
        return {"jobs": [j.as_dict() for j in found]}

    @app.get("/api/jobs/{job_id}")
    def job_get(job_id: str, who: Anyone) -> dict[str, Any]:
        return mine(job_id, who).as_dict()

    @app.post("/api/jobs/{job_id}/delivered")
    def job_delivered(job_id: str, who: Anyone) -> dict[str, Any]:
        done = queue().delivered(job_id, caller(who))
        if done is None:
            raise HTTPException(409, "that job isn't finished, or isn't yours")
        return done.as_dict()

    def alerts_caller(who: Principal) -> None:
        if who.role not in ("admin", "device") and (who.role, who.name) != (
            "client",
            DISCORD,
        ):
            raise HTTPException(403, "only a paired device, gos or the Discord bot")

    @app.get("/api/discord/alerts")
    def alerts_get(who: Anyone) -> dict[str, Any]:
        alerts_caller(who)
        return alerts_state(core.store)

    @app.post("/api/discord/alerts")
    async def alerts_set(request: Request, who: Anyone) -> dict[str, Any]:
        alerts_caller(who)
        body = await json_body(request)
        state = await asyncio.to_thread(
            set_alerts, core.store, body.get("mode"), body.get("hours")
        )
        log.info("discord alerts", extra={"mode": state["mode"], "by": who.name})
        return state

    @app.post("/api/discord/journal")
    async def discord_journal(request: Request, who: Anyone) -> dict[str, Any]:
        if (who.role, who.name) != ("client", DISCORD):
            raise HTTPException(403, "only the Discord bot")
        body = await json_body(request)
        kind, text = body.get("kind"), body.get("text")
        if kind not in JOURNAL_KINDS or not isinstance(text, str) or not text.strip():
            raise HTTPException(400, f"send kind ({', '.join(JOURNAL_KINDS)}) and text")
        if core.journal is None:
            return {"journalled": False}
        line = journal_line(kind, text)
        await asyncio.to_thread(core.journal, [line])
        return {"journalled": True}
