"""The bot's message texts (WO-9.1). Every number comes from the core's answer as it is, beside
its citation (``plan:``, ``r:``, ``tel:``; plan 0.1 principles 1 and 2); nothing here computes a
game number. Text from the core (answers, item and plan names, OC readings) is data: it is sent
with mentions off (the adapter's ``allowed_mentions``), so it can't ping anyone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from gregtec.discordbot.settings import BotSettings

MSG_MAX = 1900  # Discord's limit is 2000 characters
GONE = "That button isn't active any more."
OFFLINE = "GOS's core isn't answering right now; try again in a minute."
STILL_WORKING = "Still working on it; the answer follows here."
NO_DB = "There's no recipe DB yet (run gos db build on the PC), so I can't design that."
DEEP_PAUSED = (
    "The deep-lane task stopped at its spending cap. Approve it on the dashboard "
    "(on the phone too)."
)


def clip(text: str, limit: int = MSG_MAX) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def num(value: object) -> str:
    """A number from the core, as it is: whole numbers without ``.0``."""
    if isinstance(value, bool) or value is None:
        return "?"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:,.6g}" if abs(value) >= 1 else f"{value:.6g}"
    return str(value)


def core_error(detail: str, status: int | None) -> str:
    if status is None:
        return OFFLINE
    if status == 503 and "gos db build" in detail:
        return NO_DB
    return clip(f"GOS couldn't do that: {detail}")


def ask_reply(reply: Mapping[str, Any]) -> str:
    """``/api/ask``'s reply: its checked text and its citations (D152)."""
    text = str(reply.get("text") or "").strip() or "(no answer)"
    refs = [str(r) for r in reply.get("refs") or () if isinstance(r, str)]
    if refs:
        text += "\n" + "refs: " + ", ".join(refs[:12])
    return clip(text)


def deep_result(task: Mapping[str, Any]) -> str:
    status = task.get("status")
    if status == "done":
        return clip(str(task.get("text") or "Done.").strip())
    if status == "failed":
        return clip(
            f"The deep-lane task failed: {task.get('error') or 'no reason given'}"
        )
    if status == "stopped":
        return "The deep-lane task was stopped."
    return "The deep lane is offline, so that wasn't done; I can do lookups."


def _link(settings: BotSettings, plan_id: str) -> str:
    link = settings.plan_link(plan_id)
    if link is None:
        return "Phone link: set [discord] dashboard_url (or the tailnet name in [core] allowed_hosts)."
    return f"On the phone: {link}"


def clarify(card: Mapping[str, Any], what: str) -> str:
    lines = [str(card.get("question") or "Which one did you mean?")]
    for c in (card.get("candidates") or ())[:5]:
        lines.append(f"{c.get('n')}. {c.get('name')} ({c.get('key')})")
    lines.append(f"Ask {what} again with the exact name or key.")
    return clip("\n".join(lines))


def plan_result(job: Mapping[str, Any], settings: BotSettings) -> str:
    """A finished ``/plan`` job: the routes in rank.py's order (never re-ordered, D250)."""
    spec = job.get("spec") or {}
    head = f"Plan job {job.get('id')} ({spec.get('target')}"
    amount = spec.get("rate") or spec.get("batch")
    head += f", {amount})" if amount else ")"
    if job.get("state") == "failed":
        return clip(f"{head} failed: {job.get('error') or 'no reason given'}")
    res = job.get("result") or {}
    if res.get("clarify"):
        return clip(head + "\n" + clarify(res["clarify"], "/plan"))
    lines = [head + ":"]
    for r in res.get("routes") or ():
        where = "addon" if r.get("column") == "addon" else f"route {r.get('position')}"
        lines.append(
            f"{where} of {r.get('of')}: {r.get('name')} (plan:{r.get('id')}): "
            f"{num(r.get('machines'))} machines, {num(r.get('eut_avg'))} EU/t average, "
            f"effort {num(r.get('eff_h'))} h"
        )
        for w in r.get("warnings") or ():
            lines.append(f"  warning: {w}")
    ids = [i for i in res.get("ids") or () if isinstance(i, str)]
    for p in res.get("problems") or ():
        lines.append(f"note: {p}")
    if ids:
        lines.append(
            "The diagram and BOM are of route 1. Accept a route on the dashboard."
        )
        lines.append(_link(settings, ids[0]))
    return clip("\n".join(lines))


def power_result(job: Mapping[str, Any], settings: BotSettings) -> str:
    spec = job.get("spec") or {}
    head = f"Power job {job.get('id')} ({spec.get('mode')}"
    head += f", up to {spec['tier']})" if spec.get("tier") else ")"
    if job.get("state") == "failed":
        return clip(f"{head} failed: {job.get('error') or 'no reason given'}")
    res = job.get("result") or {}
    lines = [f"{head}: {res.get('mode_label') or res.get('mode')}"]
    for o in res.get("options") or ():
        lines.append(
            f"option {o.get('position')} of {o.get('of')}: {o.get('name')} (plan:{o.get('id')}): "
            f"net {num(o.get('net_eut'))} EU/t of {num(o.get('gross_eut'))} gross; "
            f"{o.get('generator')} on {o.get('fuel')}"
        )
    ids = [i for i in res.get("ids") or () if isinstance(i, str)]
    if ids:
        lines.append("Accept an option on the dashboard.")
        lines.append(_link(settings, ids[0]))
    return clip("\n".join(lines))


def job_queued(job: Mapping[str, Any], created: bool) -> str:
    what = "Plan" if job.get("kind") == "plan" else "Power"
    if not created:
        return f"{what} job {job.get('id')} is already queued ({job.get('state')})."
    return (
        f"{what} job {job.get('id')} queued. The result comes here (or by DM) with the "
        "diagram and the BOM."
        if job.get("kind") == "plan"
        else f"{what} job {job.get('id')} queued. The options come here (or by DM)."
    )


def status(
    live: Mapping[str, Any] | None,
    ledger: Mapping[str, Any] | None,
    ai: Mapping[str, Any] | None,
    errors: Sequence[str],
) -> str:
    """``/status``: the AI's state, the live base (telemetry, WO-7.4) and the EU ledger (WO-4.4)."""
    lines: list[str] = []
    if ai is not None:
        state = ai.get("ai") or {}
        lines.append(
            "AI: online"
            if state.get("online")
            else f"AI: offline ({state.get('reason') or state.get('detail') or 'no reason'});"
            " lookups, plans and status still work"
        )
    if live is not None:
        bindings = live.get("bindings") or []
        if not bindings:
            lines.append("Base: no OC telemetry yet")
        for b in bindings:
            agents = b.get("agents") or []
            stale = sum(1 for a in agents if a.get("stale"))
            lines.append(
                f"Base ({b.get('binding')}): {len(agents)} OC computer(s)"
                + (f", {stale} silent" if stale else "")
            )
            for p in (b.get("problems") or ())[:6]:
                lines.append(f"  {p.get('level')}: {p.get('text')} [{p.get('cite')}]")
            for e in (b.get("power") or ())[:4]:
                if e.get("pct") is not None:
                    lines.append(
                        f"  {e.get('name')}: {num(e.get('pct'))}% [{e.get('cite')}]"
                    )
    if ledger is not None:
        data = ledger.get("ledger") or {}
        tiers = (data.get("tiers") or {}).get("all") or []
        if not tiers:
            lines.append("EU ledger: no accepted plans yet")
        else:
            lines.append("EU ledger (EU/t: made, used, net):")
            for t in tiers[:8]:
                refs = ", ".join(t.get("plans") or ())
                lines.append(
                    f"  {t.get('tier')}: {num(t.get('gen_eut'))}, {num(t.get('use_eut_avg'))}, "
                    f"{num(t.get('net_eut'))}" + (f" [{refs}]" if refs else "")
                )
        for w in (data.get("warnings") or ())[:4]:
            lines.append(
                f"  warning: {w.get('kind')} {w.get('tier') or ''}: {w.get('detail')}"
            )
    lines.extend(f"({e})" for e in errors)
    return clip("\n".join(lines) or "Nothing to report.")


def personas(listing: Mapping[str, Any]) -> str:
    active = listing.get("active")
    lines = ["Personas:"]
    for p in listing.get("packs") or ():
        mark = "*" if p.get("id") == active else "-"
        lines.append(f"{mark} {p.get('id')}: {p.get('name')}")
    lines.append("Switch with /persona name:<name> (or random).")
    return clip("\n".join(lines))


def alerts(state: Mapping[str, Any]) -> str:
    mode = state.get("mode")
    if mode == "quiet":
        return f"Alerts are quiet until <t:{int(state.get('until') or 0)}:t>."
    return f"Alerts are {mode}."


def readback(action: Mapping[str, Any]) -> str:
    """The pending action's read-back (D59), with its expiry as a Discord timestamp."""
    expires = action.get("expires")
    when = f" Expires <t:{int(expires)}:R>." if isinstance(expires, int | float) else ""
    return clip(
        f"Action {action.get('id')} waits for you: {action.get('readback')}{when}"
    )
