"""The power catalog's tables (WO-4.1, D230), written by ``gos db build``.

From a run's ``gt_power.json`` (:mod:`gregtec.ingest.power`): ``rotor_size``,
``rotor_material``, ``rotor_gt``, ``generator``, ``generator_gt`` and ``fuel_container``. From the
run's recipes, after they are written: ``fuel``, one row per fuel of GT's fuel maps, decoded by
its map's rule (:mod:`gregtec.planner.fuels`). Like recipes, every table holds one set per export
run, and pruning a run deletes its rows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from gregtec.ingest import power as power_ingest
from gregtec.planner import fuels as fuel_maps

TABLES = (
    "rotor_size",
    "rotor_material",
    "rotor_gt",
    "generator",
    "generator_gt",
    "fuel_container",
    "fuel",
)


def delete(conn: sqlite3.Connection, run_ids: Sequence[int]) -> None:
    for run_id in run_ids:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))


def write(
    conn: sqlite3.Connection, run_id: int, data: power_ingest.Power | None
) -> int:
    """Replace ``run_id``'s power tables: ``gt_power.json``'s data (none for a run without it)
    and the fuel rows decoded from the run's recipes. Returns the number of fuel rows."""
    delete(conn, [run_id])
    if data is not None:
        _write_power(conn, run_id, data)
    return _write_fuels(conn, run_id)


def _write_power(
    conn: sqlite3.Connection, run_id: int, data: power_ingest.Power
) -> None:
    conn.executemany(
        "INSERT INTO rotor_size VALUES (?,?,?,?,?,?)",
        [
            (
                run_id,
                s.size,
                s.name,
                s.speed_multiplier,
                s.base_damage,
                s.durability_multiplier,
            )
            for s in data.sizes
        ],
    )
    conn.executemany(
        "INSERT INTO rotor_material VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                run_id,
                m.name,
                m.tool_speed,
                m.tool_quality,
                m.durability,
                m.steam_multiplier,
                m.gas_multiplier,
                m.plasma_multiplier,
            )
            for m in data.materials
        ],
    )
    conn.executemany(
        "INSERT INTO rotor_gt VALUES (?,?,?,?,?)",
        [
            (run_id, m.name, r.size, key, value)
            for m in data.materials
            for r in m.rotors
            for key, value in sorted(r.values.items())
        ],
    )
    conn.executemany(
        "INSERT INTO generator VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                run_id,
                g.mte_id,
                g.class_name,
                g.tier,
                g.efficiency,
                g.eu_out,
                g.amps_out,
                g.recipe_map,
            )
            for g in data.generators
        ],
    )
    conn.executemany(
        "INSERT INTO generator_gt VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                run_id,
                g.mte_id,
                f.special,
                f.fluid,
                f.eu,
                f.eu_long,
                f.consumed,
                f.item[0] if f.item else None,
                f.item[1] if f.item else None,
                f.item_eu,
                f.item_eu_long,
            )
            for g in data.generators
            for f in g.fuels
        ],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO fuel_container VALUES (?,?,?,?,?,?)",
        [
            (run_id, c.registry_name, c.meta, c.nbt_hash, c.fluid, c.mb)
            for c in data.containers
        ],
    )


def _write_fuels(conn: sqlite3.Connection, run_id: int) -> int:
    """Decode every fuel recipe of the run's fuel maps (GT's own rows, not NEI's; GT's fake
    recipes left out, its hidden duplicates kept with their flag)."""
    names = tuple(m.map for m in fuel_maps.MAPS)
    marks = ",".join("?" * len(names))
    rows = conn.execute(
        "SELECT r.id, r.ref, m.unloc_name, r.duration_ticks, r.eut,"
        " COALESCE((SELECT CAST(value_num AS INT) FROM recipe_meta"
        "           WHERE recipe_id = r.id AND key = 'fuel_value'), r.special_value),"
        " fl.name, it.registry_name, it.meta, it.nbt_hash, r.hidden"
        " FROM recipe AS r JOIN recipe_map AS m ON m.id = r.recipe_map_id"
        " JOIN recipe_input AS i ON i.recipe_id = r.id AND i.slot = ("
        "   SELECT MIN(slot) FROM recipe_input WHERE recipe_id = r.id)"
        " LEFT JOIN fluid AS fl ON fl.id = i.fluid_id"
        " LEFT JOIN item AS it ON it.id = i.item_id"
        f" WHERE r.run_id = ? AND m.unloc_name IN ({marks})"
        " AND r.source IN ('gt', 'gt+nei') AND NOT r.fake",
        (run_id, *names),
    ).fetchall()
    containers = {
        (r[0], r[1], r[2]): r[3]
        for r in conn.execute(
            "SELECT registry_name, meta, nbt_hash, fluid FROM fuel_container WHERE run_id = ?",
            (run_id,),
        )
    }
    out = []
    for rid, ref, map_name, duration, eut, value, fluid, reg, meta, nbt, hidden in rows:
        if value is None:
            continue
        fm = fuel_maps.BY_MAP[map_name]
        fluid_input = fluid is not None
        if fluid_input:
            label, burns = fluid, fluid
        elif reg is not None:
            label = f"{reg}@{meta}"
            burns = containers.get((reg, meta, nbt or ""))
        else:
            continue
        decoded = fuel_maps.decode(
            fm,
            special=int(value),
            duration=int(duration or 0),
            fluid=burns is not None,
            eut=int(eut or 0),
        )
        out.append(
            (
                run_id,
                rid,
                ref,
                map_name,
                fm.gt_name,
                label,
                burns,
                int(fluid_input),
                int(bool(hidden)),
                int(value),
                decoded.unit.value,
                decoded.value,
            )
        )
    conn.executemany("INSERT INTO fuel VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", out)
    return len(out)
