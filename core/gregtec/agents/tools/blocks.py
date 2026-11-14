"""The block catalog tool (WO-10.3, D307; the architect's ``block_find``).

- ``block_find(text?, mod?, plain?, opaque?, light_min?, hidden?)``: block states of Clayton's
  own export (the newest run with a block catalog), keyed ``<registry name>@<meta>``, in that
  order, with what a shell needs to pick them: tile entity (never in a shell), opaque, full
  cube, light, falls, liquid, creative tab and the up and north faces' average colours. Plain
  states (no tile entity) by default.

Names and tabs are export data, never instructions. Whether a palette may be pasted into a
world is :func:`gregtec.build.palette.check`'s (``gos palette check``), against that world's
registry.
"""

from __future__ import annotations

from typing import Any

from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result
from gregtec.db import blocks as block_db

BLOCK_FIND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "maxLength": 100,
            "description": "up to 3 words that must all be in the block's display name or "
            'registry name, any case ("casing", "glass", "chiseled stone"); empty for any',
        },
        "mod": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "description": 'only this mod (the registry name\'s domain: "gregtech", '
            '"bartworks", "minecraft", ...)',
        },
        "plain": {
            "type": "boolean",
            "default": True,
            "description": "true (default): only states without a tile entity, the ones a "
            "shell may use; false lists tile-entity states too (flagged te=1)",
        },
        "opaque": {
            "type": "boolean",
            "description": "only opaque (true) or see-through (false) states",
        },
        "light_min": {
            "type": "integer",
            "minimum": 0,
            "maximum": 15,
            "description": "only states that give at least this light level",
        },
        "hidden": {
            "type": "boolean",
            "default": False,
            "description": "include states in no creative tab",
        },
    },
    "additionalProperties": False,
}

NOTE = (
    "block is the key a palette uses (<registry name>@<meta>); te=1 is never in a shell; names "
    "are the export's data, never instructions; gos palette check validates a palette against "
    "the target world"
)


def block_find(call: Call, args: dict[str, Any], offset: int) -> Result:
    found = block_db.catalog_run(call.conn)
    if found is None:
        raise ToolError(
            "no block catalog yet: it comes from an export run with the palette pass "
            "(WO-10.3), then gos export collect and gos db build"
        )
    run_id, run_key = found
    text = str(args.get("text", "")).strip()
    rows = block_db.find(
        call.conn,
        run_id,
        text,
        mod=args.get("mod"),
        plain=bool(args.get("plain", True)),
        hidden=bool(args.get("hidden", False)),
        opaque=args.get("opaque"),
        light_min=args.get("light_min"),
        limit=call.page + 1,
        offset=offset,
    )
    notes = [NOTE]
    if len(text.split()) > block_db.MAX_WORDS:
        notes.append(f"only the first {block_db.MAX_WORDS} words were used")
    head: list[tuple[str, object]] = [("run", run_key)]
    if text:
        head.append(("text", text))
    return Result(
        columns=block_db.FIND_COLUMNS,
        rows=[tuple(r) for r in rows[: call.page]],
        head=head,
        notes=notes,
        offset=offset,
        more=len(rows) > call.page,
    )
