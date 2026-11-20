"""The shell tool (WO-10.7; plan 7.0 ``build_request(spec)``, 7.10 "Shell designer", D316-D318).

``build_request(plans, theme?, prompt?, line_type?, palette?, exclude?, max_blocks?, headroom?,
design?, save?, site?)``, deep lane only (the architect's and the orchestrator's): designs a decorative
shell around the saved plans' layout. Without ``design`` GOS's procedural generator draws it from
the theme; with ``design`` (a list of operations in the building vocabulary,
:data:`gregtec.build.shell.OPS`) the model's own design is compiled to blocks by code. Either way
code validates it (:func:`gregtec.build.shell.design`): a small breach of a keep-out, corridor,
chase, doorway or the bounding volume is removed and said; a tile-entity block, GT's machine
block, a state outside the palette, a malformed operation or more than ``max_blocks`` blocks is
refused and **nothing is written**. With ``save`` false nothing is written either (the
architect's first look at the procedural ops; review L3). A saved build is files under
``GOS_HOME/data/build/shells/`` (never the vault, never a world). The ``.schematic`` is written
only by Clayton's ``gos shell export``, which reads the Lab world's registry; GOS never pastes.

The model gets no free-form code execution: the vocabulary is data, and every number in a
design is a coordinate the code checks.
"""

from __future__ import annotations

import json
from typing import Any

from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result
from gregtec.build import palette, shell
from gregtec.build import themes as theme_lib
from gregtec.packupdate.plans import PLAN_ID

GATE = (
    "the shell gate (gregtec.build.shell.design: the palette rule on the catalog, nothing in a "
    "keep-out, corridor, chase, doorway or the layout's interior, inside the bounding volume, at "
    "most max_blocks; "
    "refused designs write nothing) and the build folder (GOS_HOME/data/build/shells/ only)"
)
COLUMNS = ("role", "block", "name", "count")
MAX_DESIGN_TEXT = 60_000  # characters of the operations as JSON
# The value limit of build_request's connection (Tool.max_value_bytes): the layout reads each of
# the plans' multis' structure rows whole, and a real dump's largest blocks_json is over the
# tools' 1 MB (Forge of the Gods t3: 1,239,746 bytes on 2.9.0). Only the plans' rows are read
# (footprints.library's mtes), so this bounds those, with room for an addon's Lab capture.
MAX_STRUCTURE_BYTES = 4_000_000

VOCABULARY = (
    "a JSON list of operations, in the layout's coordinates (x east, y up, z south; y 0 the "
    "first layer above the floor). Each is an object with op: walls (the outer ring; y [y0, y1], "
    "inset), pillars (corners and every N along the ring; every, y, inset), windows (openings "
    "along the ring; y, every, width < every, sides [north, south, east, west]), roof (style "
    "flat or gable, y, edge + edge_n, parapet 0-3 + parapet_role), lights (a grid set in the "
    "roof; every, y), fill and frame (a solid box, or its edges; lo [x,y,z], hi [x,y,z]), clear "
    "(air; lo, hi). Each but clear names a palette role (role, and n: that role's entry "
    "number) or one block (<registry name>@<meta> from the palette). Later operations overwrite "
    "earlier ones. Leave design out for GOS's procedural shell: its operations come back in the "
    "result's ops note to start from (with save false, nothing is saved)."
)
BUILD_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plans": {
            "type": "string",
            "maxLength": 200,
            "description": "the saved plan ids the shell houses, comma-separated (a line and "
            f"the power plan that feeds it; at most {shell.MAX_PLANS}): their layout is "
            "the shell's input",
        },
        "theme": {
            "type": "string",
            "maxLength": theme_lib.MAX_ID,
            "description": "th-<name> (theme_list); leave out with no prompt for blank mode "
            "(the theme used nearby, else one that fits the line type)",
        },
        "prompt": {
            "type": "string",
            "maxLength": theme_lib.MAX_TEXT * 5,
            "description": "Clayton's words for this build, as he said them",
        },
        "line_type": {
            "type": "string",
            "enum": list(theme_lib.LINE_TYPES),
            "description": "what the line does (blank mode's concept and the theme's adaptation)",
        },
        "palette": {
            "type": "string",
            "maxLength": 4000,
            "description": "extra palette entries, one <role> <registry name>@<meta> a line, "
            "from block_find (a prompt-only build needs at least a wall and a roof)",
        },
        "exclude": {
            "type": "string",
            "maxLength": 4000,
            "description": "palette states to leave out, one <registry name>@<meta> a line",
        },
        "max_blocks": {
            "type": "integer",
            "minimum": shell.LIMITS["max_blocks"][1],
            "maximum": shell.LIMITS["max_blocks"][2],
            "description": "the most blocks the shell may have (default "
            f"{shell.LIMITS['max_blocks'][0]})",
        },
        "headroom": {
            "type": "integer",
            "minimum": shell.LIMITS["headroom"][1],
            "maximum": shell.LIMITS["headroom"][2],
            "description": "blocks above the layout's top for the roof (default "
            f"{shell.LIMITS['headroom'][0]})",
        },
        "design": {
            "type": "string",
            "maxLength": MAX_DESIGN_TEXT,
            "description": "your design: " + VOCABULARY,
        },
        "save": {
            "type": "boolean",
            "description": "false: design and check it but save nothing (a look at the "
            "procedural shell's ops to start your own design from); default true",
        },
        "site": {
            "type": "object",
            "additionalProperties": {"type": ["integer"]},
            "maxProperties": 3,
            "description": "where the build stands: {dim, x, z} (dimension, block x and z), "
            "for blank mode's themes used nearby",
        },
    },
    "required": ["plans"],
    "additionalProperties": False,
}


def _palette_lines(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) < 2:
            raise ToolError(
                f"a palette line is <role> <registry name>@<meta>, not {line!r}"
            )
        out.append((parts[0], parts[1]))
    return out


def build_request(call: Call, args: dict[str, Any], offset: int) -> Result:
    from gregtec.build import (
        shell_store,
    )  # imports the layout planner (and the planner)

    home = call.env.db.parent.parent  # GOS_HOME/data/gos.sqlite
    plan_ids = [p.strip() for p in args["plans"].split(",") if p.strip()]
    for pid in plan_ids:
        if not PLAN_ID.fullmatch(pid):
            raise ToolError(f"{pid!r} is not a plan id")
    design = None
    if "design" in args:
        try:
            design = json.loads(args["design"])
        except ValueError as exc:
            raise ToolError(f"design is not JSON: {exc}") from None
        if not isinstance(design, list):
            raise ToolError("design is a JSON list of operations")
    site = None
    if "site" in args:
        got = args["site"]
        if set(got) != {"dim", "x", "z"}:
            raise ToolError("site is {dim, x, z}")
        site = shell_store.Site(got["dim"], got["x"], got["z"])
    exclude = [
        line.strip() for line in args.get("exclude", "").splitlines() if line.strip()
    ]
    try:
        made = shell_store.make(
            call.conn,
            home,
            call.env.vault,
            plan_ids,
            theme_id=args.get("theme"),
            prompt=args.get("prompt", ""),
            line_type=args.get("line_type", theme_lib.GENERAL),
            extra=_palette_lines(args.get("palette", "")),
            exclude=exclude,
            max_blocks=args.get("max_blocks"),
            headroom=args.get("headroom"),
            ops=design,
            site=site,
            save_it=args.get("save", True),
        )
    except (shell.ShellError, palette.CatalogError) as exc:
        raise ToolError(f"no shell: {exc}") from None
    out = made.outcome
    if not out.ok:
        raise ToolError(
            f"shell {made.build} refused, nothing was written: "
            + " | ".join(out.reasons)
            + ". Fix the design and send it again, or leave design out for GOS's procedural shell."
        )
    rec = made.record
    sx, sy, sz = rec["bounds"]["size"]
    b = rec["bounds"]
    head: list[tuple[str, object]] = [
        ("build", made.build),
        ("status", out.status),
        ("source", out.source),
        ("theme", rec.get("theme") or "none"),
        ("mode", rec["mode"]),
        ("layout", rec["layout"]),
        ("bounds", f"lo {b['lo']} hi {b['hi']} ({sx} x {sz}, {sy} high)"),
        ("wall_top", rec["wall_top"]),
        ("roof_y", rec["roof_y"]),
        (
            "doorways",
            "; ".join(f"{d['id']} lo {d['lo']} hi {d['hi']}" for d in rec["doorways"]),
        ),
        ("blocks", f"{rec['blocks']} of max {rec['max_blocks']}"),
        (
            "saved",
            f"data/build/shells/{made.build}/"
            if made.saved is not None
            else "no (save false: a preview, nothing written)",
        ),
    ]
    head += [("repaired", r) for r in out.repairs]
    notes = [
        "not saved (save false): send your design, or call again without save for this one",
        "ops: " + json.dumps(list(made.ops), separators=(",", ":")),
    ]
    if made.saved is not None:
        notes = [
            "say: "
            + (
                f"The shell is designed: {rec['blocks']} blocks, {sx} by {sz}"
                + (
                    ", with a few blocks removed from the machines' space."
                    if out.repairs
                    else "."
                )
            ),
            (
                "it shows on the dashboard's Build view; Clayton exports it with gos shell export "
                f"{made.build} and pastes it in the Lab only"
            ),
            "ops: " + json.dumps(list(made.ops), separators=(",", ":")),
        ]
    rows = [
        (m["role"], m["block"], m["name"] or "", m["count"]) for m in rec["materials"]
    ]
    page = rows[offset : offset + call.page]
    return Result(
        COLUMNS,
        page,
        head=head,
        notes=notes,
        total=len(rows),
        offset=offset,
        more=len(rows) > offset + call.page,
    )
