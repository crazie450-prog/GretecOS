"""The plan's dashboard cards (WO-3.6, plan §7.14, D160): ``flow`` and ``bom``; and the effort
breakdown as a ``table`` card (WO-4.3, D237).

Cards are data in WO-2.6's catalog (``core/gregtec/cards/schema.json``), never HTML; the dashboard
draws the flow card with React Flow and elkjs. The flow card's boxes and links are
:mod:`.diagram`'s: a node cites its ``r:`` recipe, a link carries the plan's item and rate, the
card cites ``plan:<id>``. The BOM card is :mod:`.bom`'s table as BOM rows (one per node: the
machine and tier as the name, the count in machines, the node's ``r:`` id), so it shows the
same numbers as the TSV. Both are checked against the catalog before they leave
(:func:`gregtec.cards.checked`). Over the catalog's limits (200 boxes, 400 links), the first
ones are shown and a note says so.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gregtec.cards import checked
from gregtec.cards.build import clip
from gregtec.planner import effort
from gregtec.planner.outputs import bom
from gregtec.planner.outputs.book import Book
from gregtec.planner.outputs.diagram import flow

MAX_NODES, MAX_EDGES, MAX_ROWS = 200, 400, 500
MAX_EFFORT_ROWS, MAX_NOTES, MAX_HEAD = 50, 20, 30  # the table card's limits
EFFORT_TOOL = "effort_estimate"  # plan 5's tool name for the effort model (D237)
TIERS = {
    t.upper(): t
    for t in ("Steam", "ULV", "LV", "MV", "HV", "EV", "IV", "LuV", "ZPM", "UV", "UHV")
    + ("UEV", "UIV", "UMV", "UXV", "MAX")
}


def _tier(name: str | None) -> str | None:
    return TIERS.get((name or "").upper())


def flow_card(plan: Mapping[str, Any], book: Book) -> dict[str, Any]:
    f = flow(plan, book)
    notes: list[str] = []
    nodes = []
    for b in f.boxes[:MAX_NODES]:
        if b.kind == "node":
            machine = b.machine
        else:
            side = {"in": "input", "out": "byproduct", "target": "target"}[b.kind]
            machine = f"{side} · {b.rate_s:.6g}/s" if b.rate_s is not None else side
        nodes.append(
            {
                "id": clip(b.id, 100),
                "label": clip(b.label, 300),
                "machine": clip(machine, 300) if machine else None,
                "tier": _tier(b.tier),
                "count": b.count,
                "ref": clip(b.ref, 200) if b.ref else None,
            }
        )
    shown = {n["id"] for n in nodes}
    links = [x for x in f.links if x.src in shown and x.dst in shown][:MAX_EDGES]
    if len(f.boxes) > MAX_NODES or len(links) < len(f.links):
        notes.append(
            f"the first {len(nodes)} of {len(f.boxes)} boxes and {len(links)} of "
            f"{len(f.links)} flows; the plan's PNG and gtnhplanner.com file show them all"
        )
    notes.append(f"{plan['tier_cap']} · export run {plan['run']}")
    return checked(
        {
            "type": "flow",
            "title": clip(plan["name"], 300),
            "ref": f"plan:{plan['id']}",
            "nodes": nodes,
            "edges": [
                {
                    "from": clip(x.src, 100),
                    "to": clip(x.dst, 100),
                    "label": clip(x.label, 300),
                    "rate": x.rate_s,
                    "unit": "/s" if x.rate_s is not None else None,
                    "ref": None,
                }
                for x in links
            ],
            "notes": [clip(n, 2000) for n in notes],
        }
    )


def bom_card(plan: Mapping[str, Any]) -> dict[str, Any]:
    table = bom.rows(plan)
    rows = [
        {
            "key": clip(r["node"], 300),
            "name": clip(
                f"{r['machine']} · {r['tier']}"
                if r["machine"]
                else "no machine (crafting)",
                300,
            ),
            "amount": int(r["count"] or 0),
            "unit": "machines",
            "ref": clip(r["recipe"], 200),
        }
        for r in table[:MAX_ROWS]
    ]
    t = bom.totals(table)
    notes = [
        f"{t['count']} machines, {t['eut_avg']:g} EU/t average, {t['eut_peak']:g} EU/t peak",
        "the TSV has OC, parallel, EU/t and utilisation per row",
    ]
    if len(table) > MAX_ROWS:
        notes.append(f"the first {MAX_ROWS} of {len(table)} rows")
    return checked(
        {
            "type": "bom",
            "title": clip(f"{plan['name']} · BOM", 300),
            "ref": f"plan:{plan['id']}",
            "rows": rows,
            "notes": notes,
        }
    )


def effort_card(plan: Mapping[str, Any]) -> dict[str, Any]:
    """The plan's effort breakdown (plan 7.2, D17, WO-4.3) as WO-2.6's table card: one row per
    recipe (part, recipe map, the machine its steps are sized on as an ``mte:`` chip, the
    recipe as an ``r:`` chip, crafts, the machines they are spread over, hours); the totals,
    how many items are not counted and each unlocked machine (an ``mte:`` chip of its own) in
    the head; its notes. Power and production plans carry the same ``metrics`` shape, so both
    use it (D237)."""
    m = plan["metrics"]
    eff = m["effort"]
    rows = effort.table(eff)
    head = [
        ("plan", f"plan:{plan['id']}"),
        ("effort", effort.duration(float(m["eff_h"]))),
        ("build", effort.duration(float(m["build_h"]))),
        ("unlock", effort.duration(float(m["unlock_h"]))),
        ("not counted", f"{int(eff.get('uncounted', 0))} items (see the notes)"),
        ("crafting-table step", f"{float(eff['craft_s']):g} s"),
        ("components", str(len(eff["components"]))),
    ]
    notes = list(eff["notes"])
    unlocks = list(eff["unlocks"])
    room = MAX_HEAD - len(head)
    if len(unlocks) > room:
        room -= 1
        notes.insert(0, "more unlocked machines: " + ", ".join(unlocks[room:]))
        unlocks = unlocks[:room]
    # one pair each, so each is a chip; keys stay distinct (the renderer keys on them)
    head.extend((f"unlocks {i}", u) for i, u in enumerate(unlocks, 1))
    if len(rows) > MAX_EFFORT_ROWS:
        notes.insert(0, f"the first {MAX_EFFORT_ROWS} of {len(rows)} rows")
    if len(notes) > MAX_NOTES:
        notes = [*notes[: MAX_NOTES - 1], f"{len(notes) - MAX_NOTES + 1} more notes"]
    return checked(
        {
            "type": "table",
            "tool": EFFORT_TOOL,
            "error": None,
            "title": clip(f"{plan['name']} · effort", 300),
            "head": [{"key": k, "value": clip(v, 2000)} for k, v in head],
            "columns": list(effort.COLUMNS),
            "rows": [[clip(c, 2000) for c in r] for r in rows[:MAX_EFFORT_ROWS]],
            "span": (
                f"1-{MAX_EFFORT_ROWS} of {len(rows)}"
                if len(rows) > MAX_EFFORT_ROWS
                else None
            ),
            "total": len(rows),
            "notes": [clip(n, 2000) for n in notes],
        }
    )
