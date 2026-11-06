"""``recipe_find`` and ``recipe_uses``: recipe cards from the newest export run (plan 7.3).

Each row is one recipe, cited by its ``r:<map>/<sig12>`` key, with the DB's own numbers: EU/t
(amperage included, as GT stores it), amperage, duration in ticks, the GT voltage tier, every
input and output with its amount (items, or L = mB for fluids) and chance, and the recipe's
metadata (coil heat, cleanroom, ...). The only numbers code derives are exact unit changes: the
duration in seconds (ticks / 20) and chances in percent (basis points / 100), never rounded.

Planner defaults (plan 5.2) hide disabled and fake recipes unless ``show_fake``; a recipe whose
handler doesn't say whether it is fake (NEI-only handlers, D105) is shown and flagged ``fake?``.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from gregtec.agents.tools import items
from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result, number

TIERS = (
    "ULV",
    "LV",
    "MV",
    "HV",
    "EV",
    "IV",
    "LuV",
    "ZPM",
    "UV",
    "UHV",
    "UEV",
    "UIV",
    "UMV",
    "UXV",
    "MAX",
)
COLUMNS = (
    "ref",
    "machine",
    "tier",
    "eut",
    "amps",
    "dur_t",
    "dur_s",
    "in",
    "out",
    "cond",
    "flags",
)
NOTE = (
    "cite each recipe by its r: id; eut is EU/t, dur_t ticks (dur_s = dur_t / 20), fluids in L; "
    "fake? = the NEI handler doesn't say whether the recipe is real"
)


def tier_name(tier: int | None) -> str:
    if tier is None:
        return ""
    return TIERS[tier] if 0 <= tier < len(TIERS) else str(tier)


def percent(bp: int) -> str:
    """Basis points as an exact percent: 7500 -> 75%, 125 -> 1.25%."""
    whole, frac = divmod(bp, 100)
    return f"{whole}.{frac:02d}".rstrip("0").rstrip(".") + "%"


def seconds(ticks: int | None) -> str:
    """Ticks as exact seconds (20 ticks a second): 100 -> 5, 7 -> 0.35."""
    if ticks is None:
        return ""
    whole, rest = divmod(ticks, 20)
    return f"{whole}.{rest * 5:02d}".rstrip("0").rstrip(".")


def current_run(call: Call) -> sqlite3.Row:
    rows = call.query("current_run")
    if not rows:
        raise ToolError("the DB has no export run yet: run `gos db build`")
    return rows[0]


def slot_text(s: sqlite3.Row) -> str:
    name = items.clean(s["name"])
    label = f"{name} [{s['key']}]" if name else f"[{s['key']}]"
    amount = s["amount"]
    text = label if amount == 0 else f"{amount}{'L' if s['is_fluid'] else 'x'} {label}"
    if s["circuit"] is not None:
        text += f" circuit {s['circuit']}"
    if s["part"] == "in" and not s["consumed"]:
        text += " (not consumed)"
    if s["ore"]:
        text += f" (any ore:{s['ore']})"
    elif s["alts"]:
        text += f" (or {s['alts']} other{'s' if s['alts'] > 1 else ''})"
    if s["chance_bp"] != 10000:
        text += f" {percent(s['chance_bp'])} chance"
    return text


def meta_text(m: sqlite3.Row) -> str:
    value = m["value_text"] if m["value_num"] is None else number(m["value_num"])
    return f"{m['key']}={value}"


def cards(
    call: Call, rows: list[sqlite3.Row], offset: int, head: list[tuple[str, object]]
) -> Result:
    """One page of recipe rows (fetched one past the page) as recipe cards."""
    page = rows[: call.page]
    ids = json.dumps([r["id"] for r in page])
    slots: dict[tuple[int, str], list[str]] = defaultdict(list)
    for s in call.query("recipe_slots", {"ids": ids}):
        slots[(s["recipe_id"], s["part"])].append(slot_text(s))
    conds: dict[int, list[str]] = defaultdict(list)
    for m in call.query("recipe_conditions", {"ids": ids}):
        conds[m["recipe_id"]].append(meta_text(m))
    out = []
    for r in page:
        cond = conds[r["id"]]
        if not cond and r["special_value"]:
            cond = [
                f"special={r['special_value']}"
            ]  # GT's raw mSpecialValue (plan 5.2)
        flags = [
            flag
            for flag, on in (
                ("disabled", r["enabled"] == 0),
                ("fake", r["fake"] == 1),
                ("fake?", r["fake"] is None),
                ("hidden", r["hidden"] == 1),
            )
            if on
        ]
        out.append(
            (
                r["ref"],
                r["machine"],
                tier_name(r["voltage_tier"]),
                r["eut"],
                r["amperage"],
                r["duration_ticks"],
                seconds(r["duration_ticks"]),
                "; ".join(slots[(r["id"], "in")]),
                "; ".join(slots[(r["id"], "out")]),
                "; ".join(cond),
                " ".join(flags),
            )
        )
    total = rows[0]["total"] if rows else None
    return Result(
        COLUMNS,
        out,
        head=head,
        notes=[NOTE],
        total=total,
        offset=offset,
        more=len(rows) > call.page,
    )


def machine_filter(call: Call, text: str | None) -> tuple[str | None, str | None]:
    """The recipe map and NEI handler ids ``text`` names, as JSON arrays (None: any machine)."""
    if not text:
        return None, None
    text = " ".join(text.split())
    rows = call.query("recipe_maps_named", {"name": text})
    if not rows and (found := items.words(text)):
        rows = call.query("recipe_maps_matching", {"q": items.fts_query(found)})
    if not rows:
        raise ToolError(f"no machine, recipe map or NEI handler matches {text!r}")
    maps = [r["id"] for r in rows if r["kind"] == "map"]
    handlers = [r["id"] for r in rows if r["kind"] == "handler"]
    return json.dumps(maps), json.dumps(handlers)


def _head(
    target: items.Target, run: sqlite3.Row, **filters: object
) -> list[tuple[str, object]]:
    head: list[tuple[str, object]] = [
        ("item", target.label()),
        ("run", f"{run['run_key']} (GT {run['gt_version']})"),
    ]
    shown = " ".join(f"{k}={v}" for k, v in filters.items() if v not in (None, False))
    if shown:
        head.append(("filters", shown))
    return head


ITEM_ARG = {
    "type": "string",
    "description": "the item or fluid: words, or a key from item_resolve (registry@meta, fluid:<name>)",
    "minLength": 1,
    "maxLength": 200,
}
SHOW_FAKE = {
    "type": "boolean",
    "description": "also show disabled and fake recipes (default false)",
    "default": False,
}
RECIPE_FIND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item": ITEM_ARG,
        "tier_max": {
            "type": "string",
            "description": "only recipes up to this GT voltage tier (crafting has none and stays)",
            "enum": list(TIERS),
        },
        "machine": {
            "type": "string",
            "description": "only recipes of this machine or recipe map (name, mte:<id> or gt.recipe.*)",
            "maxLength": 200,
        },
        "show_fake": SHOW_FAKE,
    },
    "required": ["item"],
    "additionalProperties": False,
}
RECIPE_USES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"item": ITEM_ARG, "show_fake": SHOW_FAKE},
    "required": ["item"],
    "additionalProperties": False,
}


def recipe_find(call: Call, args: dict[str, Any], offset: int) -> Result:
    target = items.target(call, args["item"])
    if isinstance(target, Result):
        return target
    run = current_run(call)
    maps, handlers = machine_filter(call, args.get("machine"))
    tier = args.get("tier_max")
    rows = call.query(
        "recipes_for_item",
        {
            "run_id": run["id"],
            "item_id": target.ref_id if target.kind == "item" else None,
            "fluid_id": target.ref_id if target.kind == "fluid" else None,
            "all": int(args["show_fake"]),
            "tier_max": TIERS.index(tier) if tier else None,
            "maps": maps,
            "handlers": handlers,
            "limit": call.page + 1,
            "offset": offset,
        },
    )
    head = _head(
        target,
        run,
        tier_max=tier,
        machine=args.get("machine"),
        show_fake=args["show_fake"],
    )
    return cards(call, rows, offset, head)


def recipe_uses(call: Call, args: dict[str, Any], offset: int) -> Result:
    target = items.target(call, args["item"])
    if isinstance(target, Result):
        return target
    run = current_run(call)
    rows = call.query(
        "recipes_using_item",
        {
            "run_id": run["id"],
            "items": json.dumps(target.item_ids(call)),
            "fluid_id": target.ref_id if target.kind == "fluid" else None,
            "ores": json.dumps([o["id"] for o in target.ores(call)]),
            "all": int(args["show_fake"]),
            "limit": call.page + 1,
            "offset": offset,
        },
    )
    return cards(call, rows, offset, _head(target, run, show_fake=args["show_fake"]))
