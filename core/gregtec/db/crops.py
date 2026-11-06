"""The crop tables (WO-1.10, D134): one set of CropsNH crop data per export run.

``gos db build`` writes a run's crop data (:mod:`gregtec.ingest.crops`) in its own transaction,
next to the run's recipes, and deletes it with the run. A crop is cited ``crop:<id>`` (its
``ref``); parent-combination recipes are keyed ``<child><<parent>+<parent>...`` (parents sorted)
and cite their child's ``crop:<id>``. Items are registry name + meta (+ NBT hash), with
``item_id`` when the item is in the catalog; soils and sub-soils are blocks by registry name and
meta (NULL: any meta). :func:`read` gives a run's data back as :class:`~gregtec.ingest.crops.
CropData`, so :func:`gregtec.ingest.crops.diff` compares two runs of one DB, or of two.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence

from gregtec.ingest.crops import (
    Crop,
    CropData,
    Drop,
    ParentRecipe,
    Pool,
    Requirement,
    Seed,
    Soil,
    SoilBlock,
)
from gregtec.ingest.normalize import ITEM, Ref, canonical

TABLES = (
    "crop",
    "crop_drop",
    "crop_seed",
    "crop_parent_recipe",
    "crop_parent",
    "crop_mutation",
    "crop_soil",
)


def _bit(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _reqs(reqs: Sequence[Requirement]) -> str:
    return canonical([[r.kind, r.value, r.text] for r in reqs])


def _unreqs(text: str) -> tuple[Requirement, ...]:
    return tuple(Requirement(k, v, t) for k, v, t in json.loads(text))


def delete(conn: sqlite3.Connection, run_ids: Sequence[int]) -> None:
    for run_id in run_ids:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))


def write(
    conn: sqlite3.Connection, run_id: int, data: CropData, items: Mapping[Ref, int]
) -> None:
    """Replace ``run_id``'s crop data with ``data`` (inside the caller's transaction)."""
    delete(conn, [run_id])
    crops, drops = [], []
    for c in data.crops.values():
        crops.append(
            (
                run_id,
                c.id,
                c.ref,
                c.name,
                c.unlocalized,
                c.flavour,
                c.creator,
                c.soil,
                canonical(list(c.soil_lists)),
                c.tier,
                c.min_seed_bed_tier,
                canonical(list(c.liked_biomes)),
                c.growth_duration,
                c.breeding_threshold,
                c.crossing_threshold,
                c.weeds_above,
                c.drop_multiplier,
                _reqs(c.requirements),
                canonical(list(c.alternate_seeds)),
                canonical([[r.name, r.meta, r.nbt_hash] for r in c.sub_soils]),
                _bit(c.in_nei),
            )
        )
        for slot, d in enumerate(c.drops):
            ref = d.item
            drops.append(
                (
                    run_id,
                    c.id,
                    slot,
                    d.amount,
                    d.chance_bp,
                    d.name,
                    d.nbt,
                    ref.name if ref else None,
                    ref.meta if ref else None,
                    ref.nbt_hash if ref else None,
                    items.get(ref) if ref else None,
                )
            )
    conn.executemany(
        "INSERT INTO crop(run_id, id, ref, name, unlocalized, flavour, creator, soil, soil_lists,"
        " tier, min_seed_bed_tier, liked_biomes, growth_duration, breeding_threshold,"
        " crossing_threshold, weeds_above, drop_multiplier, requirements, alternate_seeds,"
        " sub_soils, in_nei) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        crops,
    )
    conn.executemany(
        "INSERT INTO crop_drop(run_id, crop_id, slot, amount, chance_bp, name, nbt,"
        " registry_name, meta, nbt_hash, item_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        drops,
    )
    conn.executemany(
        "INSERT INTO crop_seed(run_id, slot, registry_name, meta, crop_id, item_id)"
        " VALUES (?,?,?,?,?,?)",
        [
            (
                run_id,
                n,
                s.registry_name,
                s.meta,
                s.crop,
                None
                if s.meta is None
                else items.get(Ref(ITEM, s.registry_name, s.meta)),
            )
            for n, s in enumerate(data.seeds)
        ],
    )
    conn.executemany(
        "INSERT INTO crop_parent_recipe(run_id, key, child, requirements, machine_only,"
        " world_only, in_nei) VALUES (?,?,?,?,?,?,?)",
        [
            (
                run_id,
                r.key,
                r.child,
                _reqs(r.requirements),
                int(r.machine_only),
                int(r.world_only),
                _bit(r.in_nei),
            )
            for r in data.recipes
        ],
    )
    conn.executemany(
        "INSERT INTO crop_parent(run_id, recipe_key, slot, crop_id) VALUES (?,?,?,?)",
        [
            (run_id, r.key, slot, p)
            for r in data.recipes
            for slot, p in enumerate(r.parents)
        ],
    )
    conn.executemany(
        "INSERT INTO crop_mutation(run_id, pool, crop_id) VALUES (?,?,?)",
        [(run_id, p.name, c) for p in data.pools for c in p.members],
    )
    soils = []
    for s in data.soils:
        if not s.blocks:  # registered, no block: kept so the list still exists
            soils.append((run_id, s.name, 0, None, None))
        soils.extend(
            (run_id, s.name, slot, b.registry_name, b.meta)
            for slot, b in enumerate(s.blocks)
        )
    conn.executemany(
        "INSERT INTO crop_soil(run_id, soil, slot, registry_name, meta) VALUES (?,?,?,?,?)",
        soils,
    )


def read(conn: sqlite3.Connection, run_id: int) -> CropData:
    """``run_id``'s crop data as :func:`write` stored it (empty if it has none)."""
    drops: dict[str, list[Drop]] = {}
    for row in conn.execute(
        "SELECT crop_id, amount, chance_bp, name, nbt, registry_name, meta, nbt_hash"
        " FROM crop_drop WHERE run_id = ? ORDER BY crop_id, slot",
        (run_id,),
    ):
        item = None if row[5] is None else Ref(ITEM, row[5], row[6], row[7] or "")
        drops.setdefault(row[0], []).append(Drop(row[1], row[2], row[3], row[4], item))
    crops: dict[str, Crop] = {}
    for row in conn.execute(
        "SELECT id, name, unlocalized, flavour, creator, soil, soil_lists, tier,"
        " min_seed_bed_tier, liked_biomes, growth_duration, breeding_threshold,"
        " crossing_threshold, weeds_above, drop_multiplier, requirements, alternate_seeds,"
        " sub_soils, in_nei FROM crop WHERE run_id = ? ORDER BY id",
        (run_id,),
    ):
        crops[row[0]] = Crop(
            id=row[0],
            name=row[1],
            unlocalized=row[2],
            flavour=row[3],
            creator=row[4],
            soil=row[5],
            soil_lists=tuple(json.loads(row[6])),
            tier=row[7],
            min_seed_bed_tier=row[8],
            liked_biomes=tuple(json.loads(row[9])),
            growth_duration=row[10],
            breeding_threshold=row[11],
            crossing_threshold=row[12],
            weeds_above=row[13],
            drop_multiplier=row[14],
            requirements=_unreqs(row[15]),
            drops=tuple(drops.get(row[0], ())),
            alternate_seeds=tuple(json.loads(row[16])),
            sub_soils=tuple(Ref(ITEM, n, m, h) for n, m, h in json.loads(row[17])),
            in_nei=None if row[18] is None else bool(row[18]),
        )
    parents: dict[str, list[str]] = {}
    for key, crop in conn.execute(
        "SELECT recipe_key, crop_id FROM crop_parent WHERE run_id = ?"
        " ORDER BY recipe_key, slot",
        (run_id,),
    ):
        parents.setdefault(key, []).append(crop)
    recipes = [
        ParentRecipe(
            child,
            tuple(parents.get(key, ())),
            _unreqs(reqs),
            None if in_nei is None else bool(in_nei),
        )
        for key, child, reqs, in_nei in conn.execute(
            "SELECT key, child, requirements, in_nei FROM crop_parent_recipe"
            " WHERE run_id = ? ORDER BY key",
            (run_id,),
        )
    ]
    members: dict[str, list[str]] = {}
    for pool, crop in conn.execute(
        "SELECT pool, crop_id FROM crop_mutation WHERE run_id = ? ORDER BY pool, crop_id",
        (run_id,),
    ):
        members.setdefault(pool, []).append(crop)
    blocks: dict[str, list[SoilBlock]] = {}
    for soil, reg, meta in conn.execute(
        "SELECT soil, registry_name, meta FROM crop_soil WHERE run_id = ?"
        " ORDER BY soil, slot",
        (run_id,),
    ):
        listed = blocks.setdefault(soil, [])
        if reg is not None:
            listed.append(SoilBlock(reg, meta))
    seeds = [
        Seed(reg, meta, crop)
        for reg, meta, crop in conn.execute(
            "SELECT registry_name, meta, crop_id FROM crop_seed WHERE run_id = ?"
            " ORDER BY slot",
            (run_id,),
        )
    ]
    return CropData(
        crops=crops,
        recipes=recipes,
        pools=[Pool(p, tuple(m)) for p, m in sorted(members.items())],
        soils=[Soil(s, tuple(b)) for s, b in sorted(blocks.items())],
        seeds=seeds,
    )


def runs_with_crops(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """(export_run id, run key) of the runs with crop data, newest first."""
    return [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT e.id, e.run_key FROM export_run AS e WHERE EXISTS"
            " (SELECT 1 FROM crop AS c WHERE c.run_id = e.id)"
            " ORDER BY e.created_at DESC, e.run_key DESC"
        )
    ]
