"""Bounding-box footprints (WO-3.6, plan §7.1 step 9 "Footprint", D10, D200).

Per machine type in the plan: its box, from the ``structure`` table (WO-1.8: ``size_x`` wide,
``size_y`` high, ``size_z`` deep, hatch slots included) for a multiblock, or one block for a single
block (a GT single block is one block by construction), then the clearance around it
(:data:`CLEARANCE_XZ` blocks on each side, :data:`CLEARANCE_Y` above). A multiblock with several
structure variants (a tier channel, a height channel) takes its smallest by volume, as the effort
model takes its smallest by blocks (D196). A multiblock with no structure row (an addon multi the
extractor doesn't reach, or a DB without a structure dump) is ``footprint: unknown`` and counts
in neither sum.

:func:`layout` sums the boxes times their counts into ``area`` (blocks of floor) and ``volume``
(blocks), with the highest box as ``h``; that is the plan's ``layout`` and the summary in its
``metrics.footprint``. It is the Phase 3 estimate the plan asks for: machines side by side, each
with its own walkway. WO-10.5's corridor-aware layout replaces it. The controller is looked up
by its MTE id (``gregtech:gt.blockmachines`` + meta, or the ``machine`` row it is linked to),
never by a numeric block ID.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

CLEARANCE_XZ = 1  # blocks of walkway on each horizontal side of a machine (D200)
CLEARANCE_Y = 1  # blocks above it
CONTROLLER = "gregtech:gt.blockmachines"
UNKNOWN = "unknown"


def structure_size(
    conn: sqlite3.Connection, mte: int
) -> tuple[int, int, int, str] | None:
    """(x, y, z, variant) of the multiblock's smallest structure variant by volume, or None."""
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(structure)")}
    # D123's registry_name and meta columns come with the first structure load (WO-1.8); a
    # DB without them finds a controller through its machine row only.
    by_key = " OR (s.registry_name = ? AND s.meta = ?)" if "meta" in cols else ""
    rows = conn.execute(
        "SELECT s.size_x, s.size_y, s.size_z, s.variant FROM structure AS s"
        " LEFT JOIN machine AS m ON m.id = s.machine_id"
        f" WHERE (m.mte_id = ?{by_key})"
        " AND s.size_x > 0 AND s.size_y > 0 AND s.size_z > 0",
        (mte, CONTROLLER, mte) if by_key else (mte,),
    ).fetchall()
    best = min(
        ((int(x), int(y), int(z), str(v or "")) for x, y, z, v in rows),
        key=lambda t: (t[0] * t[1] * t[2], t[3]),
        default=None,
    )
    return best


def machine(conn: sqlite3.Connection, mte: int, kind: str) -> dict[str, Any]:
    """One machine type's footprint: ``size`` (the structure's box, or one block) and ``box``
    (with clearance), or ``footprint: unknown`` and why."""
    if kind == "multiblock":
        found = structure_size(conn, mte)
        if found is None:
            return {
                "mte": mte,
                "footprint": UNKNOWN,
                "why": f"mte:{mte} has no structure data",
            }
        x, y, z, variant = found
        source = "structure"
    else:
        x, y, z, variant = 1, 1, 1, ""
        source = "single block"
    box = {"x": x + 2 * CLEARANCE_XZ, "y": y + CLEARANCE_Y, "z": z + 2 * CLEARANCE_XZ}
    return {
        "mte": mte,
        "footprint": "box",
        "source": source,
        "variant": variant,
        "size": {"x": x, "y": y, "z": z},
        "box": box,
        "area": box["x"] * box["z"],
        "volume": box["x"] * box["y"] * box["z"],
    }


def layout(conn: sqlite3.Connection, plan: Mapping[str, Any]) -> dict[str, Any]:
    """The plan's ``layout``: per machine type its footprint and count, and the sums."""
    counts: dict[int, int] = {}
    kinds: dict[int, str] = {}
    names: dict[int, str] = {}
    for n in plan["nodes"]:
        m = n.get("machine")
        if m and n.get("count"):
            counts[m["mte"]] = counts.get(m["mte"], 0) + int(n["count"])
            kinds[m["mte"]] = m["kind"]
            names[m["mte"]] = m["name"]
    machines: list[dict[str, Any]] = []
    area = volume = h = 0
    unknown: list[str] = []
    for mte in sorted(counts):
        fp = machine(conn, mte, kinds[mte])
        fp.update(name=names[mte], count=counts[mte])
        if fp["footprint"] == UNKNOWN:
            unknown.append(f"mte:{mte}")
        else:
            area += fp["area"] * counts[mte]
            volume += fp["volume"] * counts[mte]
            h = max(h, fp["box"]["y"])
        machines.append(fp)
    return {
        "method": "bbox",
        "clearance": {"xz": CLEARANCE_XZ, "y": CLEARANCE_Y},
        "machines": machines,
        "area": area,
        "volume": volume,
        "h": h,
        "unknown": unknown,
    }


def summary(lay: Mapping[str, Any]) -> dict[str, Any]:
    """``metrics.footprint``: the sums, and how many machine types are unknown."""
    return {
        "area": lay["area"],
        "volume": lay["volume"],
        "h": lay["h"],
        "unknown": len(lay["unknown"]),
    }
