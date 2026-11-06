"""``machine_info``: a GT machine, the recipe maps it runs and its ``machine_fact`` rows.

A machine is cited ``mte:<mte_id>`` (GT's own MetaTileEntity id, the meta of
``gregtech:gt.blockmachines``; plan 7.1 names machine types the same way). Its facts are the
ones the planner uses (named query ``machine_facts``, WO-1.7 and D109): one per key, a Lab
probe over the curated table over the machine pass over the tooltip, then the highest
confidence. Each fact row keeps its source, confidence and note (a curated row's note cites
GT's source file); facts under 0.8 or flagged are not settled (D111).
"""

from __future__ import annotations

import re
from typing import Any

from gregtec.agents.tools import items
from gregtec.agents.tools.recipes import tier_name
from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result

# The prefix is required: a bare number is more likely a numeric block ID (F3, NEI), which
# GOS never keys on, than an MTE id.
_MTE = re.compile(r"(?:mte:|gregtech:gt\.blockmachines@)([0-9]{1,9})")
_NUMBER = re.compile(r"[0-9]+")
COLUMNS = ("key", "value", "unit", "source", "confidence", "note", "cite")
NOTE = (
    "cite facts by the machine's mte: id; a value is a number or a formula over named inputs "
    "(D109); confidence under 0.8 or a flag means not settled (D111)"
)

MACHINE_INFO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {
            "type": "string",
            "description": 'the machine\'s name ("Electric Blast Furnace", "EBF") or id '
            "(mte:1000 or gregtech:gt.blockmachines@1000; a bare number is not an id)",
            "minLength": 1,
            "maxLength": 200,
        }
    },
    "required": ["machine"],
    "additionalProperties": False,
}


def machine_info(call: Call, args: dict[str, Any], offset: int) -> Result:
    text = " ".join(args["machine"].split())
    m = _MTE.fullmatch(text)
    if _NUMBER.fullmatch(text):
        raise ToolError(
            f"{text} is a bare number, which could be a numeric block ID (GOS never keys on "
            "those): name the machine, or give GT's MetaTileEntity id as mte:<id>"
        )
    if m is not None:
        mte = int(m[1])
    else:
        res = items.resolve(call, text, "machine")
        best = res.best
        if best is None:
            return items.choices(res, 10, "machine")
        mte = int(best.key.removeprefix("mte:"))
    rows = call.query("machine_by_mte", {"mte_id": mte})
    if not rows:
        raise ToolError(f"no machine mte:{mte} in the DB")
    row = rows[0]
    maps = call.query("machine_maps", {"mte_id": mte})
    head: list[tuple[str, object]] = [
        ("machine", f"mte:{mte}"),
        ("name", row["name"]),
        ("item", row["item_key"]),
        ("class", row["class_name"]),
        ("mod", row["mod"]),
        ("multiblock", row["is_multiblock"]),
    ]
    if row["tier"] is not None:
        head.append(("tier", tier_name(row["tier"])))
    head.append(
        ("maps", "; ".join(f"{r['name']} [{r['map']}]" for r in maps) or "none")
    )
    facts = call.query("machine_facts", {"mte_id": mte})
    page = facts[offset : offset + call.page]
    return Result(
        COLUMNS,
        [
            (
                f["key"],
                f["value"],
                f["unit"],
                f["source"],
                f["confidence"],
                f["note"],
                f"mte:{mte}",
            )
            for f in page
        ],
        head=head,
        notes=[NOTE],
        total=len(facts),
        offset=offset,
        more=offset + call.page < len(facts),
    )
