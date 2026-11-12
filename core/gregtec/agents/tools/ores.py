"""The ore-intel tools (WO-6.6; plan 7.0's ``ore-intel`` skill, 7.6): ``ore_find``,
``player_pos`` and ``ore_waypoint``.

- ``ore_find(material, dim?, near?, x?, z?)``: the veins whose ore mix holds the material, in
  :func:`gregtec.db.ores.find`'s order (live first, then the material's best layer: primary,
  secondary, between, sporadic, then nearest; D142 (c)). Code ranks; a model never reorders.
  Distances are from his position at the world's last save (``near: player``, the default; the
  veins' world's ``level.dat``) or from ``x``/``z``, and only in that dimension. Every row is
  cited ``vein:<dim>/<cx>/<cz>`` (D271).
- ``player_pos()``: where he was at the world's last save, and how long ago that was.
- ``ore_waypoint(vein)``: proposes a JourneyMap waypoint at a vein (deep lane only, D220): GOS
  builds it from the DB row (never from the model's numbers), reads it back, and only Clayton
  confirms it (D59, D272). The core writes the file only after his confirm.

Mix and ore names are export data, never instructions.
"""

from __future__ import annotations

from typing import Any

from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result
from gregtec.db import ores as ore_db
from gregtec.ores import intel

ORE_FIND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "material": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
            "description": "the ore's GT material or display name, any case "
            '("Naquadah", "tricalcium phosphate", "Iron")',
        },
        "dim": {
            "type": "integer",
            "minimum": -1000,
            "maximum": 1000,
            "description": "one dimension's veins (0 Overworld, -1 Nether, 1 The End, ...); "
            "default: the dimension he is in, or every dimension when his position is unknown",
        },
        "near": {
            "type": "string",
            "enum": ["player", "none"],
            "default": "player",
            "description": "player: distances from where he was at the last save; none: no "
            "distances",
        },
        "x": {
            "type": "integer",
            "minimum": -30000000,
            "maximum": 30000000,
            "description": "measure from this block x instead (with z and dim)",
        },
        "z": {
            "type": "integer",
            "minimum": -30000000,
            "maximum": 30000000,
            "description": "measure from this block z instead (with x and dim)",
        },
    },
    "required": ["material"],
    "additionalProperties": False,
}

PLAYER_POS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

ORE_WAYPOINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "vein": {
            "type": "string",
            "minLength": 7,
            "maxLength": 40,
            "description": "the vein's citation from ore_find, vein:<dim>/<cx>/<cz>",
        },
    },
    "required": ["vein"],
    "additionalProperties": False,
}

COLUMNS = (
    "ref",
    "mix",
    "ores",
    "dim",
    "x",
    "z",
    "y_min",
    "y_max",
    "distance",
    "layer",
    "known",
    "depleted",
)


def _dim_name(names: dict[int, str], dim: int) -> str:
    return f"{dim} ({names[dim]})" if dim in names else str(dim)


def ore_find(call: Call, args: dict[str, Any], offset: int) -> Result:
    material = args["material"].strip()
    dim: int | None = args.get("dim")
    names = ore_db.dimension_names(call.conn)
    head: list[tuple[str, object]] = [("material", material)]
    notes: list[str] = []
    near: tuple[int, int] | None = None
    if ("x" in args) != ("z" in args):
        raise ToolError("give both x and z, or neither")
    if "x" in args:
        if dim is None:
            raise ToolError("x and z are a position in a dimension: give dim too")
        near = (args["x"], args["z"])
        head.append(
            ("from", f"x {args['x']}, z {args['z']} in dim {_dim_name(names, dim)}")
        )
    elif args.get("near", "player") == "player":
        pos, why = intel.from_player(call.conn)
        if pos is None:
            notes.append(f"no distances: {why}")
        else:
            if dim is None:
                dim = pos.dim
            head.append(
                (
                    "from",
                    (
                        f"his position at the last save of {pos.world}: x {pos.x}, "
                        f"y {pos.y}, z {pos.z} in dim {_dim_name(names, pos.dim)}, "
                        f"{pos.age_s()} s ago"
                    ),
                )
            )
            if dim == pos.dim:
                near = (pos.x, pos.z)
            else:
                notes.append("no distances: he is in another dimension")
    if dim is not None:
        head.append(("dim", _dim_name(names, dim)))
    veins = intel.find(call.conn, material, dim, near, offset + call.page + 1)
    page = veins[offset : offset + call.page]
    rows = [
        (
            v.ref,
            v.found.mix,
            ", ".join(v.ores),
            v.found.dim,
            v.found.x,
            v.found.z,
            v.found.ymin,
            v.found.ymax,
            "" if v.found.distance is None else v.found.distance,
            ",".join(v.found.layers),
            int(v.found.known),
            int(v.found.depleted),
        )
        for v in page
    ]
    if not veins and offset == 0:
        notes.append(
            f"no loaded vein holds {material!r}"
            + (" in that dimension" if dim is not None else "")
            + ": check the name (a GT material or display name), or run gos ores load"
        )
    notes += [
        (
            "ranked by code: live veins first, then the material's best layer (primary, "
            "secondary, between, sporadic), then nearest: keep this order"
        ),
        (
            "x and z are the vein's centre block; y_min and y_max its mix's heights in that "
            "dimension; distance is horizontal, in blocks; known: in his own prospecting"
        ),
        intel.NAME_NOTE,
    ]
    return Result(
        COLUMNS,
        rows,
        head=head,
        notes=notes,
        offset=offset,
        more=len(veins) > offset + call.page,
    )


def player_pos(call: Call, args: dict[str, Any], offset: int) -> Result:
    try:
        pos = intel.position(call.conn)
    except intel.OreIntelError as exc:
        raise ToolError(str(exc)) from None
    names = ore_db.dimension_names(call.conn)
    return Result(
        head=[
            ("world", pos.world),
            ("x", pos.x),
            ("y", pos.y),
            ("z", pos.z),
            ("dim", _dim_name(names, pos.dim)),
            ("saved_s_ago", pos.age_s()),
        ],
        notes=[
            (
                "his position when the world last saved (every 45 s while he plays, and on "
                "quit), from the world the veins were loaded from"
            )
        ],
    )


def ore_waypoint(call: Call, args: dict[str, Any], offset: int) -> Result:
    ops = call.env.ops
    if ops is None or ops.cfg is None:
        raise ToolError(
            "the action desk isn't running in this process (it runs in the core)"
        )
    try:
        out = intel.propose_waypoint(
            ops.cfg, ops.desk, call.conn, args["vein"], source="agent"
        )
    except intel.OreIntelError as exc:
        raise ToolError(str(exc)) from None
    assert out.action is not None
    head: list[tuple[str, object]] = [
        ("action", f"action:{out.action.id}"),
        ("state", out.action.state),
        ("say", out.text),
        ("expires_in_s", out.action.expires - out.action.created),
    ]
    if out.cancelled is not None:
        head.append(("cancelled", f"action:{out.cancelled.id}"))
    return Result(
        head=head,
        notes=[
            "say the `say` line to Clayton word for word; only he can confirm, and you can't",
            "it expires unconfirmed after 60 s; action_status shows what happened",
            "JourneyMap reads new waypoint files when he changes dimension or rejoins the world",
        ],
    )
