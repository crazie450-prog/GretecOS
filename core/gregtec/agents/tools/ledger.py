"""``ledger_get``: the EU ledger for the agents (WO-4.4, plan 7.0, §7.2, D238).

Read-only, both lanes: per scope (``all``: every accepted, building or running plan;
``running``: running ones only) and tier, generation, average and peak use, net and amps, then the
warnings, each cited by its plans (``plan:<id>``), and OC's measured power when it is fresh (the
telemetry hook, ``tel:`` refs). Every number is a plan's, summed by
:mod:`gregtec.planner.ledger`; nothing here computes one. Changing the ledger is Clayton's
(accepting a plan, moving it along its lifecycle), never a tool's.
"""

from __future__ import annotations

from typing import Any

from gregtec.agents.tools.registry import Call
from gregtec.agents.tools.tsv import Result

LEDGER_GET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def ledger_get(call: Call, args: dict[str, Any], offset: int) -> Result:
    from gregtec.planner import ledger

    led = ledger.read(call.conn)
    rows = [
        (
            scope,
            t.tier,
            t.gen_eut,
            t.use_eut_avg,
            t.use_eut_peak,
            t.net_eut,
            t.amps_in,
            t.amps_out,
            ", ".join(f"plan:{p}" for p in t.plans),
        )
        for scope in ledger.SCOPES
        for t in led.tiers[scope]
    ]
    head: list[tuple[str, object]] = [
        (
            "plans",
            ", ".join(f"plan:{ln.entry.plan} ({ln.status})" for ln in led.lines) or "-",
        ),
    ]
    for scope in ledger.SCOPES:
        head.append(
            (
                f"{scope}_net_eut",
                led.total(scope, "gen_eut") - led.total(scope, "use_eut_avg"),
            )
        )
    tel = led.telemetry
    if tel is not None:
        head += [
            ("tel_eu_in", tel.eu_in),
            ("tel_eu_out", tel.eu_out),
            ("tel", ", ".join(tel.refs)),
        ]
    notes = [
        f"warning {w.kind} ({w.scope}{', ' + w.tier if w.tier else ''}): {w.detail}"
        + (f" [{', '.join(f'plan:{p}' for p in w.plans)}]" if w.plans else "")
        for w in led.warnings
    ]
    if not led.lines:
        notes.append("no accepted plans: the ledger is empty")
    if tel is None:
        notes.append(
            "no fresh OC power telemetry: the ledger is the plans' numbers only"
        )
    notes.append("EU/t; cite each row by its plan: ids")
    page = rows[offset : offset + call.page]
    return Result(
        (
            "scope",
            "tier",
            "gen_eut",
            "use_eut_avg",
            "use_eut_peak",
            "net_eut",
            "amps_in",
            "amps_out",
            "plans",
        ),
        list(page),
        head=head,
        notes=notes,
        total=len(rows),
        offset=offset,
        more=offset + call.page < len(rows),
    )
