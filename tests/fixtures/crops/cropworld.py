"""A small made-up CropsNH world for the crop guide's tests (WO-6.7): never pack data.

Crops (ids in CropsNH's form, names made up):

- ``cropsnh:wheat`` and ``cropsnh:carrot``: tier 1, planted from alternate seeds.
- ``cropsnh:reed``: tier 2, recipe wheat + carrot.
- ``cropsnh:stoneLily``: tier 1, recipe reed + carrot, needs a stone block under it.
- ``cropsnh:ironFern``: tier 6, Crop Breeder only (stoneLily + reed), with a Crop Breeder recipe
  (MV, 120 EU/t, 400 ticks, catalyst 1 Iron Ore) and an iron block under it.
- ``cropsnh:weedy``: tier 3, only in the pool "Green" (with wheat and carrot).
- ``cropsnh:lonely``: tier 2, no recipe and no pool.

Machines: Crop Managers (LV 28001, MV 28002), Crop Breeders (LV 28025, MV 28026, HV 28027) and
Seed Generators (LV 28013, MV 28014), as CropsNH's mte ids.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from gregtec.crops import facts as crop_facts
from gregtec.db import access
from gregtec.db import crops as crop_db
from gregtec.ingest.crops import (
    Crop,
    CropData,
    ParentRecipe,
    Pool,
    Requirement,
    Seed,
    Soil,
    SoilBlock,
)
from gregtec.ingest.normalize import ITEM, Ref

RUN_KEY = "20261111T000000Z"
BREEDER_REF = "r:cropsnh.recipes.cropBreeder/0123456789ab"


def crop(
    cid: str,
    name: str,
    tier: int,
    *,
    soil: str = "farmland",
    reqs: tuple[Requirement, ...] = (),
    subs: tuple[Ref, ...] = (),
    duration: int = 600,
) -> Crop:
    return Crop(
        id=cid,
        name=name,
        unlocalized=f"crop.{cid}",
        flavour="",
        creator="test",
        soil=soil,
        soil_lists=(soil,),
        tier=tier,
        min_seed_bed_tier=-1,
        liked_biomes=(),
        growth_duration=duration,
        breeding_threshold=0.8,
        crossing_threshold=0.8,
        weeds_above=24,
        drop_multiplier=1.0,
        requirements=reqs,
        drops=(),
        alternate_seeds=(),
        sub_soils=subs,
        in_nei=True,
    )


STONE = Ref(ITEM, "minecraft:stone", 0, "")
IRON = Ref(ITEM, "minecraft:iron_block", 0, "")


def data() -> CropData:
    crops = {
        c.id: c
        for c in (
            crop("cropsnh:wheat", "Wheat", 1),
            crop("cropsnh:carrot", "Carrot", 1),
            crop("cropsnh:reed", "Reed", 2),
            crop(
                "cropsnh:stoneLily",
                "Stone Lily",
                1,
                reqs=(
                    Requirement(
                        "sub_soil", None, "Needs a block of stone under it to grow"
                    ),
                ),
                subs=(STONE,),
            ),
            crop(
                "cropsnh:ironFern",
                "Iron Fern",
                6,
                reqs=(
                    Requirement("light_max", 10, "Needs a light level of 10 or lower"),
                    Requirement(
                        "sub_soil", None, "Needs a block of iron under it to grow"
                    ),
                ),
                subs=(IRON,),
                duration=1200,
            ),
            crop("cropsnh:weedy", "Weedy", 3),
            crop("cropsnh:lonely", "Lonely", 2),
        )
    }
    recipes = [
        ParentRecipe("cropsnh:reed", ("cropsnh:wheat", "cropsnh:carrot"), ()),
        ParentRecipe("cropsnh:stoneLily", ("cropsnh:reed", "cropsnh:carrot"), ()),
        ParentRecipe(
            "cropsnh:ironFern",
            ("cropsnh:stoneLily", "cropsnh:reed"),
            (Requirement("machine_only", None, "Requires the crop breeder"),),
        ),
    ]
    return CropData(
        crops=dict(sorted(crops.items())),
        recipes=sorted(recipes, key=lambda r: r.key),
        pools=[Pool("Green", ("cropsnh:carrot", "cropsnh:weedy", "cropsnh:wheat"))],
        soils=[Soil("farmland", (SoilBlock("minecraft:farmland", None),))],
        seeds=[
            Seed("minecraft:carrot", 0, "cropsnh:carrot"),
            Seed("minecraft:wheat_seeds", 0, "cropsnh:wheat"),
        ],
    )


MACHINES = {
    "crop_manager": [
        crop_facts.Machine(28001, "Basic Crop Manager", 1),
        crop_facts.Machine(28002, "Advanced Crop Manager", 2),
    ],
    "crop_breeder": [
        crop_facts.Machine(28025, "Basic Crop Breeder", 1),
        crop_facts.Machine(28026, "Advanced Crop Breeder", 2),
        crop_facts.Machine(28027, "Advanced Crop Breeder II", 3),
    ],
    "seed_generator": [
        crop_facts.Machine(28013, "Basic Seed Generator", 1),
        crop_facts.Machine(28014, "Advanced Seed Generator", 2),
    ],
}
NAMES = {
    "minecraft:stone@0": "Stone",
    "minecraft:iron_block@0": "Block of Iron",
    "minecraft:carrot@0": "Carrot",
    "minecraft:wheat_seeds@0": "Seeds",
}
BREEDER = crop_facts.BreederRecipe(
    BREEDER_REF,
    "cropsnh:ironFern",
    frozenset({"cropsnh:stoneLily", "cropsnh:reed"}),
    120,
    400,
    2,
    (("Iron Ore", 1),),
)


def facts() -> crop_facts.Facts:
    return crop_facts.from_parts(data(), [BREEDER], MACHINES, NAMES, RUN_KEY)


def _item(
    conn: sqlite3.Connection, reg: str, meta: int, name: str, nbt: dict | None = None
) -> int:
    nbt_json = None if nbt is None else json.dumps(nbt)
    h = "" if nbt_json is None else hashlib.sha256(nbt_json.encode()).hexdigest()[:16]
    conn.execute(  # another fixture's DB may hold the item already (the e2e core's)
        "INSERT OR IGNORE INTO item(registry_name, meta, nbt_hash, nbt_json, mod_id, name_en)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (reg, meta, h, nbt_json, reg.split(":")[0], name),
    )
    row = conn.execute(
        "SELECT id FROM item WHERE registry_name = ? AND meta = ? AND nbt_hash = ?",
        (reg, meta, h),
    ).fetchone()
    return int(row[0])


def write_db(path: Path, *, into_latest: bool = False) -> Path:
    """The made-up world as a game DB at ``path``: an export run, its crops, CropsNH's machines
    and the Crop Breeder recipe, the way ``gos db build`` stores them.

    ``into_latest`` (the e2e core's DB): add them to the DB's newest export run instead of a new
    one, so the other tools (which read only the newest run) still see that run's recipes."""
    conn = access.connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if into_latest:
            row = conn.execute(
                "SELECT id FROM export_run ORDER BY created_at DESC, run_key DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise ValueError(f"{path} has no export run to add the crop world to")
            run_id = int(row[0])
        else:
            cur = conn.execute(
                "INSERT INTO export_run(created_at, fingerprint, run_key) VALUES (?, ?, ?)",
                ("2026-11-11T00:00:00Z", "test", RUN_KEY),
            )
            run_id = int(cur.lastrowid or 0)
        crop_db.write(conn, run_id, data(), {})
        for job, ms in MACHINES.items():
            for m in ms:
                conn.execute(
                    "INSERT INTO machine(mte_id, class_name, mod_id, name_en, is_multiblock, tier)"
                    " VALUES (?, ?, 'cropsnh', ?, 0, ?)",
                    (m.mte_id, crop_facts.MACHINE_CLASSES[job], m.name, m.tier),
                )
        for key, name in NAMES.items():
            reg, meta = key.rsplit("@", 1)
            _item(conn, reg, int(meta), name)
        map_id = int(
            conn.execute(
                "INSERT INTO recipe_map(unloc_name, name_en, owner_mod) VALUES (?, ?, ?)",
                (crop_facts.BREEDER_MAP, "Crop Breeder", "cropsnh"),
            ).lastrowid
            or 0
        )
        rid = int(
            conn.execute(
                "INSERT INTO recipe(run_id, source, recipe_map_id, eut, duration_ticks,"
                " voltage_tier, signature, content_hash, ref)"
                " VALUES (?, 'gt', ?, 120, 400, 2, 'sig', 'hash', ?)",
                (run_id, map_id, BREEDER_REF),
            ).lastrowid
            or 0
        )
        seed = crop_facts.SEED_ITEM
        a = _item(
            conn,
            seed,
            0,
            "Stone Lily Seeds",
            {"crop": "cropsnh:stoneLily", "scan": "1B"},
        )
        b = _item(conn, seed, 0, "Reed Seeds", {"crop": "cropsnh:reed", "scan": "1B"})
        ore = _item(conn, "minecraft:iron_ore", 0, "Iron Ore")
        out = _item(conn, seed, 0, "Iron Fern Seeds", {"crop": "cropsnh:ironFern"})
        for slot, item in enumerate((a, b, ore)):
            conn.execute(
                "INSERT INTO recipe_input(recipe_id, slot, item_id, amount) VALUES (?, ?, ?, 1)",
                (rid, slot, item),
            )
        conn.execute(
            "INSERT INTO recipe_input(recipe_id, slot, fluid_id, amount) VALUES (?, 3, 1, 1000)",
            (rid,),
        )
        conn.execute(
            "INSERT INTO recipe_output(recipe_id, slot, item_id, amount) VALUES (?, 0, ?, 1)",
            (rid, out),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    return path
