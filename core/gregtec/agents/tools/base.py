"""``base_get`` (plan 7.0, WO-3.8): My base as the agents read it.

Read-only. My base is edited only by Clayton, from the dashboard (``base_set`` is UI-only, plan
7.0, D212): no tool writes it. The tier comes with how it was set (quests or his override, D64)
and the key quest that shows the detected stage (``q:``); machines are cited ``mte:<id>``; stock
is keyed as the other tools key items and fluids. Nothing is stocked until he enters it (D63).
"""

from __future__ import annotations

from typing import Any

from gregtec.agents.tools.registry import Call
from gregtec.agents.tools.tsv import Result
from gregtec.base import state

BASE_GET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
COLUMNS = ("kind", "ref", "name", "amount", "unit", "source")


def base_get(call: Call, args: dict[str, Any], offset: int) -> Result:
    snap = state.snapshot(call.conn)
    t = snap["tier"]
    tier = f"{t['stage']} (GT voltage tier {t['tier']}, {t['how']})"
    if t["detected"] is None:
        detected = "nothing yet (no quest progress loaded)"
    else:
        detected = f"{t['detected']}" + (
            f" from {t['quest']} ({t['quest_name']})" if t["quest"] else ""
        )
    rows: list[tuple[object, ...]] = [
        (
            "machine",
            m["ref"],
            m["name"] or "(not in the current DB)",
            m["count"],
            "",
            m["source"],
        )
        for m in snap["machines"]
    ]
    rows += [
        (
            s["kind"],
            s["key"],
            s["name"] or "(not in the current DB)",
            s["qty"],
            s["unit"],
            "manual",
        )
        for s in snap["stock"]
    ]
    notes = [
        (
            "machine count 0: Clayton said he has none (a manual 0 outranks placement events "
            "and the census)"
        ),
        "stock is manual (D63): an item not listed is assumed not stocked",
        "accepted lines: none yet (plans are accepted from WO-3.7)",
    ]
    if not snap["has_db"]:
        notes.append("no recipe DB yet: names are missing until `gos db build`")
    page = rows[offset : offset + call.page]
    return Result(
        COLUMNS,
        page,
        head=[
            ("tier", tier),
            ("detected", detected),
            ("machines", len(snap["machines"])),
            ("stocked", len(snap["stock"])),
        ],
        notes=notes,
        total=len(rows),
        offset=offset,
        more=offset + call.page < len(rows),
    )
