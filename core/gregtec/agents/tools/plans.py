"""``plan_export``: a saved plan's outputs (WO-3.6, plan 7.0, §7.1 step 9).

A plan saved by WO-3.6 (``gos plan --save``; WO-3.7's designer saves the same way) has its
canonical JSON at ``GOS_HOME/data/plans/<id>.json`` and its exports beside it in
``data/plans/<id>/`` (:data:`gregtec.planner.outputs.FILES`). The tool reads them, never makes
them, and computes nothing:

- ``bom``: the BOM TSV's rows (machine, tier, count, OC, parallel, EU/t, utilisation, ``r:``);
- ``footprint``: the plan's ``layout`` per machine type (structure box, clearance, area, volume);
- ``mermaid``: the diagram's lines;
- ``gtnhplanner``, ``gtnhflow``, ``png``: the file's path (Clayton opens or sends the file; its
  JSON, YAML or image isn't shown to the model).

Every row cites the plan; BOM rows also cite their ``r:`` recipe.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result
from gregtec.gamestate.safe_open import read_bytes
from gregtec.packupdate.plans import PLAN_ID

FILES = {
    "bom": "bom.tsv",
    "mermaid": "flow.mmd",
    "png": "flow.png",
    "gtnhplanner": "gtnhplanner.json",
    "gtnhflow": "gtnh-flow.yaml",
}  # gregtec.planner.outputs.FILES (not imported: the planner pulls in the solver)
FORMATS = (*FILES, "footprint")
MAX_FILE = 4 << 20

PLAN_EXPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plan": {
            "type": "string",
            "description": "the plan's id (pl-..., plan: optional)",
            "minLength": 1,
            "maxLength": 80,
        },
        "format": {
            "type": "string",
            "enum": list(FORMATS),
            "description": "bom (TSV rows), footprint (layout rows), mermaid (diagram lines), "
            "or gtnhplanner / gtnhflow / png (the file's path, for Clayton)",
        },
    },
    "required": ["plan", "format"],
    "additionalProperties": False,
}


def _home(call: Call) -> Path:
    return call.env.db.parent.parent  # GOS_HOME/data/gos.sqlite


def _plan_id(text: str) -> str:
    pid = text.strip().removeprefix("plan:")
    if not PLAN_ID.fullmatch(pid):
        raise ToolError(f"{text!r} isn't a plan id (pl-...)")
    return pid


def _read(path: Path) -> bytes:
    try:
        data = read_bytes(path)
    except OSError:
        raise ToolError(
            f"{path.name} isn't saved for this plan: save it with gos plan --save"
        ) from None
    if len(data) > MAX_FILE:
        raise ToolError(f"{path.name} is over {MAX_FILE >> 20} MiB")
    return data


def plan_export(call: Call, args: dict[str, Any], offset: int) -> Result:
    pid = _plan_id(args["plan"])
    fmt = args["format"]
    if fmt not in FORMATS:
        raise ToolError(f"format must be one of {', '.join(FORMATS)}")
    home = _home(call)
    cite = f"plan:{pid}"
    head: list[tuple[str, object]] = [("plan", cite), ("format", fmt)]
    folder = home / "data" / "plans" / pid
    if fmt == "footprint":
        doc = json.loads(_read(home / "data" / "plans" / f"{pid}.json"))
        lay = doc.get("layout") or {}
        head += [
            ("area", lay.get("area")),
            ("volume", lay.get("volume")),
            ("h", lay.get("h")),
            ("unknown", ", ".join(lay.get("unknown") or ()) or "none"),
        ]
        cols = (
            "mte",
            "name",
            "count",
            "footprint",
            "size",
            "box",
            "area",
            "volume",
            "cite",
        )
        rows = []
        for m in lay.get("machines") or ():
            size, box = m.get("size") or {}, m.get("box") or {}
            rows.append(
                (
                    f"mte:{m.get('mte')}",
                    m.get("name"),
                    m.get("count"),
                    m.get("footprint"),
                    f"{size.get('x')}x{size.get('y')}x{size.get('z')}"
                    if size
                    else m.get("why"),
                    f"{box.get('x')}x{box.get('y')}x{box.get('z')}" if box else None,
                    m.get("area"),
                    m.get("volume"),
                    cite,
                )
            )
        return _page(
            call,
            cols,
            rows,
            head,
            offset,
            "size is x (wide) x y (high) x z (deep), box adds the clearance",
        )
    path = folder / FILES[fmt]
    head.append(("path", str(path)))
    if fmt in ("gtnhplanner", "gtnhflow", "png"):
        _read(path)
        return Result(head=head, notes=[f"the file is for Clayton; cite {cite}"])
    text = _read(path).decode("utf-8")
    if fmt == "mermaid":
        lines = text.splitlines()
        return _page(
            call, ("line",), [(x,) for x in lines], head, offset, f"cite {cite}"
        )
    table = list(csv.reader(io.StringIO(text), delimiter="\t"))
    header, body = table[0], table[1:]
    rows = [(*r, r[-1] or cite) for r in body]
    return _page(
        call,
        (*header, "cite"),
        rows,
        head,
        offset,
        "the total row sums count, eut_avg and eut_peak",
    )


def _page(
    call: Call,
    cols: tuple[str, ...],
    rows: list[tuple[object, ...]],
    head: list[tuple[str, object]],
    offset: int,
    note: str,
) -> Result:
    page = rows[offset : offset + call.page]
    return Result(
        cols,
        list(page),
        head=head,
        notes=[note],
        total=len(rows),
        offset=offset,
        more=offset + call.page < len(rows),
    )
