"""``gos db build`` (WO-1.3) on a small self-authored export run.

The run follows docs/contracts/gregteclink-export-v1.md in shape only: made-up recipes, items and
NEI lines that exercise each loader rule (circuits, chances, fluids and their alternatives,
oredict slots, NBT with numeric IDs, wildcard metas, metadata, special items, duplicates,
collisions, NEI merges, crafting and furnace recipes). Never pack data.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gregtec import cli, config
from gregtec.db import access, build, report
from gregtec.ingest import collect, gtdump, merge
from gregtec.ingest.normalize import (
    CIRCUIT,
    ITEM,
    WILDCARD,
    Ref,
    nbt_hash,
    normalize_nbt,
)

RUN = "20261106T120000Z"
WATER = {"fluid": "water", "mb": 1000}
FUEL_AND_FACADE = (
    "appeng.integration.modules.NEIHelpers.NEIFacadeRecipeHandler",
    "codechicken.nei.recipe.FuelRecipeHandler",
)


def ref(item: str, meta: int = 0, n: int = 1, **kw: object) -> dict[str, object]:
    return {"item": item, "meta": meta, "n": n, **kw}


def gt(map_: str, idx: int, eut: int, duration: int, **kw: object) -> dict[str, object]:
    line: dict[str, object] = {
        "map": map_,
        "idx": idx,
        "category": kw.pop("category", map_),
        "eut": eut,
        "duration": duration,
        "special": kw.pop("special", 0),
        "in": [],
        "out": [],
        "fin": [],
        "fout": [],
        "enabled": True,
        "hidden": False,
        "fake": False,
        "can_be_buffered": True,
        "needs_empty_output": False,
        "nbt_sensitive": False,
    }
    line.update(kw)
    return line


DATA_STICK_NBT = {
    "output": {"Count": "1B", "Damage": "0S", "__item": "test:plate", "id": "628S"},
    "title": "Plate Construction Data",
}
NEI_DATA_STICK_TAG = {
    "output": {"Count": "1B", "Damage": "0S", "id": "628S"},
    "title": "Plate Construction Data",
}


def gt_recipes() -> list[dict[str, object]]:
    return [
        # 0: circuit, fluid input with alternatives, a chanced output, a null slot.
        gt(
            "gt.recipe.mixer",
            0,
            30,
            100,
            **{
                "in": [ref("test:dust", 1, 2), None, ref(CIRCUIT, 3, 0)],
                "fin": [
                    {
                        **WATER,
                        "alt": [WATER, {"fluid": "ic2distilledwater", "mb": 1000}],
                    }
                ],
                "out": [ref("test:dust", 2, 3)],
                "out_chance": [7500],
                "owners": ["gregtech"],
            },
        ),
        # 1: an oredict slot (GTRecipe_WithAlt).
        gt(
            "gt.recipe.mixer",
            1,
            7,
            200,
            **{
                "in": [
                    ref(
                        "test:dye",
                        0,
                        1,
                        ore="dyeBlack",
                        alt=[ref("test:dye", 0, 1), ref("test:gtdye", 14, 1)],
                    )
                ],
                "out": [ref("test:glass", 15, 8)],
                "owners": ["gregtech"],
            },
        ),
        # 2 and 3: same items in two categories of one map: two recipes, no collision.
        gt(
            "gt.recipe.mixer",
            2,
            16,
            24,
            category="gt.recipe.category.mixer_recycling",
            **{
                "in": [ref("test:block")],
                "fout": [{"fluid": "molten.test", "mb": 1296}],
            },
        ),
        gt(
            "gt.recipe.mixer",
            3,
            480,
            100,
            **{
                "in": [ref("test:block")],
                "fout": [{"fluid": "molten.test", "mb": 1296}],
            },
        ),
        # 4: metadata, cleanroom special value, special items, NEI description.
        gt(
            "gt.recipe.blastfurnace",
            0,
            120,
            400,
            special=-200,
            **{
                "in": [ref("test:dust", 1, 1)],
                "out": [ref("test:ingot", 1, 1)],
                "metadata": {
                    "cleanroom": True,
                    "coil_heat": 1800,
                    "qft_catalyst": ref("test:catalyst", 0, 0),
                },
                "special_items": ref("test:module"),
                "nei_desc": ["Needs a cleanroom"],
                "owners": ["gregtech"],
            },
        ),
        # 5: an exact copy of 4 (GT registered it twice): kept once.
        gt(
            "gt.recipe.blastfurnace",
            1,
            120,
            400,
            special=-200,
            **{
                "in": [ref("test:dust", 1, 1)],
                "out": [ref("test:ingot", 1, 1)],
                "metadata": {
                    "cleanroom": True,
                    "coil_heat": 1800,
                    "qft_catalyst": ref("test:catalyst", 0, 0),
                },
                "special_items": ref("test:module"),
                "nei_desc": ["Needs a cleanroom"],
                "owners": ["gregtech"],
            },
        ),
        # 6 and 7: a genuine collision: same items, 100 and 20 ticks.
        gt(
            "gt.recipe.mixer",
            4,
            4,
            100,
            **{"in": [ref("test:dust", 5, 1)], "out": [ref("test:tiny", 5, 4)]},
        ),
        gt(
            "gt.recipe.mixer",
            5,
            4,
            20,
            **{"in": [ref("test:dust", 5, 1)], "out": [ref("test:tiny", 5, 4)]},
        ),
        # 8: NBT with a numeric item ID (a data stick), not consumed.
        gt(
            "gt.recipe.mixer",
            6,
            30,
            50,
            **{
                "in": [
                    ref("test:datastick", 0, 0, nbt=DATA_STICK_NBT),
                    ref("test:plate", 0, 2),
                ],
                "out": [ref("test:circuit", 0, 1)],
            },
        ),
        # 9: a wildcard input.
        gt(
            "gt.recipe.mixer",
            7,
            4,
            100,
            **{"in": [ref("test:log", 32767, 1)], "out": [ref("test:planks", 0, 4)]},
        ),
        # 10: a map with 2 A, owned by an addon.
        gt(
            "tst.recipe.test",
            0,
            64,
            20,
            **{
                "in": [ref("test:ingot", 1, 1)],
                "out": [ref("test:plate", 0, 1)],
                "owners": ["TwistSpaceTechnology"],
            },
        ),
        # 11: GT shows a fuel cell; NEI shows it as a fluid (dropped there).
        gt(
            "gt.recipe.mixer",
            8,
            0,
            0,
            **{"in": [ref("test:fuelcell")], "metadata": {"fuel_value": 100}},
        ),
        # 12: a recipe the mod couldn't write.
        {"map": "gt.recipe.mixer", "idx": 9, "error": "java.lang.NullPointerException"},
    ]


def stack(
    item: str, damage: int = 0, count: object = 1, **kw: object
) -> dict[str, object]:
    return {
        "Count": count,
        "Damage": damage,
        "strId": item,
        "name": item.split(":")[1].title(),
        **kw,
    }


def fluid(name: str, mb: int, **kw: object) -> dict[str, object]:
    return {"Count": f"{mb}L", "neiFluidName": name, "name": name.title(), **kw}


def nei_line(
    handler: str, ins: list[object], outs: list[object], gt_: tuple[int, int] | None
) -> dict[str, object]:
    line: dict[str, object] = {"handler": handler, "ingredients": ins, "results": outs}
    if gt_ is not None:
        line["gt"] = {
            "eut": gt_[0],
            "durationTicks": gt_[1],
            "totalEU": gt_[0] * gt_[1],
        }
    return line


def nei_files() -> dict[str, list[dict[str, object]]]:
    mixer = [
        # GT 0, merged by identity hash; NEI adds an alternative water.
        nei_line(
            "gt.recipe.mixer",
            [
                stack("test:dust", 1, 2),
                stack(CIRCUIT, 3, 0),
                fluid(
                    "water",
                    1000,
                    anyOf=[
                        {"neiFluidName": "water"},
                        {"neiFluidName": "ic2distilledwater"},
                        {"neiFluidName": "test.water"},
                    ],
                ),
            ],
            [stack("test:dust", 2, 3, chance=7500)],
            (30, 100),
        ),
        # The same line again: a duplicate.
        nei_line(
            "gt.recipe.mixer",
            [stack("test:dust", 1, 2), stack(CIRCUIT, 3, 0), fluid("water", 1000)],
            [stack("test:dust", 2, 3, chance=7500)],
            (30, 100),
        ),
        # GT 1, with one more accepted dye (unification).
        nei_line(
            "gt.recipe.mixer",
            [
                stack(
                    "test:dye",
                    0,
                    1,
                    anyOf=[
                        {"strId": "test:dye", "Damage": 0},
                        {"strId": "test:gtdye", "Damage": 14},
                        {"strId": "other:dye", "Damage": 3},
                    ],
                )
            ],
            [stack("test:glass", 15, 8)],
            (7, 200),
        ),
        # GT 9: the wildcard log shown as one of its metas, merged by its slots.
        nei_line(
            "gt.recipe.mixer",
            [
                stack(
                    "test:log",
                    0,
                    1,
                    anyOf=[
                        {"strId": "test:log", "Damage": 0},
                        {"strId": "test:log", "Damage": 1},
                    ],
                )
            ],
            [stack("test:planks", 0, 4)],
            (4, 100),
        ),
        # GT 8 with NEI's own numeric-ID NBT (mapped through the run's ID table).
        nei_line(
            "gt.recipe.mixer",
            [
                stack("test:datastick", 0, 0, tag=NEI_DATA_STICK_TAG),
                stack("test:plate", 0, 2),
            ],
            [stack("test:circuit", 0, 1)],
            (30, 50),
        ),
        # GT 11 in another form: dropped.
        nei_line("gt.recipe.mixer", [fluid("test.fuel", 1)], [], (0, 0)),
    ]
    blast = [
        # GT 4 with its special item shown as an ingredient: merged by its slots.
        nei_line(
            "gt.recipe.blastfurnace",
            [stack("test:dust", 1, 1), stack("test:module", 0, 1)],
            [stack("test:ingot", 1, 1)],
            (120, 400),
        ),
    ]
    empty = [  # a GT category with no recipe in GT's dump: only NEI has them
        nei_line(
            "gt.recipe.empty",
            [stack("test:dust", 1, 1)],
            [fluid("molten.test", 144)],
            (30, 20),
        ),
    ]
    shaped = [
        nei_line(
            "codechicken.nei.recipe.ShapedRecipeHandler",
            [stack("minecraft:planks")] * 4,
            [stack("test:table")],
            None,
        ),
        nei_line(
            "codechicken.nei.recipe.ShapedRecipeHandler",
            [
                stack(
                    "minecraft:planks",
                    anyOf=[
                        {"strId": "minecraft:planks", "Damage": 0},
                        {"strId": "minecraft:planks", "Damage": 1},
                    ],
                )
            ]
            * 4,
            [stack("test:table")],
            None,
        ),
        # The same recipe as the one before, with NEI showing another plank: a copy.
        nei_line(
            "codechicken.nei.recipe.ShapedRecipeHandler",
            [
                stack(
                    "minecraft:planks",
                    1,
                    anyOf=[
                        {"strId": "minecraft:planks", "Damage": 0},
                        {"strId": "minecraft:planks", "Damage": 1},
                    ],
                )
            ]
            * 4,
            [stack("test:table")],
            None,
        ),
        nei_line(
            "codechicken.nei.recipe.ShapedRecipeHandler",
            [stack("test:datastick", 0, 1, tag=NEI_DATA_STICK_TAG)],
            [stack("test:copy")],
            None,
        ),
    ]
    shapeless = [
        nei_line(
            "codechicken.nei.recipe.ShapelessRecipeHandler",
            [
                stack("minecraft:milk_bucket", returns=stack("minecraft:bucket")),
                stack("minecraft:sugar"),
            ],
            [stack("test:cake")],
            None,
        ),
    ]
    furnace = [
        nei_line(
            "codechicken.nei.recipe.FurnaceRecipeHandler",
            [stack("test:dust", 1)],
            [stack("test:ingot", 1)],
            None,
        ),
    ]
    # Alternatives that no oredict in items.jsonl has: keyed by the set they make.
    ores = [{"strId": "test:ore", "Damage": 0}, {"strId": "test:ore", "Damage": 1}]
    other = [
        nei_line(
            "other.blast",
            [stack("test:ore", anyOf=ores)],
            [stack("test:ingot", 1, 2)],
            None,
        )
    ]
    # Handlers that aren't recipes (merge.NOT_RECIPES): left out.
    fuel = [
        nei_line(
            "codechicken.nei.recipe.FuelRecipeHandler",
            [stack("test:dust", 1)],
            [stack("test:ingot", 1)],
            None,
        )
    ]
    facade = [
        nei_line(
            "appeng.integration.modules.NEIHelpers.NEIFacadeRecipeHandler",
            [stack("minecraft:stone")],
            [
                stack(
                    "appliedenergistics2:item.ItemFacade",
                    count=4,
                    tag={"itemname": "stone", "modid": "minecraft", "x": [1, 0]},
                )
            ],
            None,
        )
    ]
    return {
        "nei/Mixer.jsonl": mixer,
        "nei/Blast Furnace.jsonl": blast,
        "nei/Blast Furnace_2.jsonl": other,
        "nei/Empty.jsonl": empty,
        "nei/Shaped Crafting.jsonl": shaped,
        "nei/Shapeless Crafting.jsonl": shapeless,
        "nei/Smelting.jsonl": furnace,
        "nei/Fuel.jsonl": fuel,
        "nei/Facade Crafting.jsonl": facade,
    }


def recipe_maps() -> list[dict[str, object]]:
    def m(
        name: str, owner: str, amps: int = 1, cats: tuple[str, ...] = (), count: int = 0
    ) -> dict[str, object]:
        return {
            "name": name,
            "amperage": amps,
            "backend": "gregtech.api.recipe.RecipeMapBackend",
            "frontend": "gregtech.api.recipe.RecipeMapFrontend",
            "ui": {
                "max_item_in": 6,
                "max_item_out": 6,
                "max_fluid_in": 2,
                "max_fluid_out": 2,
            },
            "nei_register": True,
            "owner_mod": owner,
            "categories": [{"name": c, "owner_mod": owner} for c in (name, *cats)],
            "count": count,
        }

    return [
        m("gt.recipe.blastfurnace", "gregtech", count=2),
        m("gt.recipe.empty", "tectech"),
        m(
            "gt.recipe.mixer",
            "gregtech",
            cats=("gt.recipe.category.mixer_recycling",),
            count=11,  # one more than gt_recipes.jsonl has: the report says so
        ),
        m("tst.recipe.test", "TwistSpaceTechnology", amps=2, count=1),
    ]


def items() -> list[dict[str, object]]:
    out = [
        {"item": i, "meta": m, "unlocalized": f"{i}.{m}", "name": name, "ore": ore}
        for i, m, name, ore in [
            ("test:dust", 1, "Test Dust", ["dustTest"]),
            ("test:dust", 2, "Other Dust", []),
            ("test:dust", 5, "Fine Dust", []),
            ("test:dye", 0, "Ink Sac", ["dyeBlack"]),
            ("test:gtdye", 14, "Black Dye", ["dyeBlack"]),
            ("test:glass", 15, "Black Glass", []),
            ("test:block", 0, "Test Block", []),
            ("test:ingot", 1, "Test Ingot", ["ingotTest"]),
            ("test:tiny", 5, "Tiny Dust", []),
            ("test:plate", 0, "Test Plate", []),
            ("test:circuit", 0, "Test Circuit", []),
            ("test:log", 32767, "Log", []),
            ("test:planks", 0, "Planks", []),
            ("test:catalyst", 0, "Catalyst", []),
            ("test:module", 0, "Module", []),
            ("test:fuelcell", 0, "Fuel Cell", []),
            (CIRCUIT, 3, "Programmed Circuit 3", []),
            ("test:datastick", 0, "Data Stick", []),
            ("gregtech:gt.blockmachines", 1000, "Test Mixer", []),
            ("minecraft:planks", 32767, "Wooden Planks", ["plankWood"]),
            ("minecraft:planks", 0, "Oak Wood Planks", ["plankWood"]),
        ]
    ]
    out[0]["gt"] = {"prefix": "dust", "material": "Test", "amount": 3628800}
    out.append(
        {
            "item": "test:datastick",
            "meta": 0,
            "nbt": DATA_STICK_NBT,
            "unlocalized": "x",
            "name": "Plate Construction Data",
            "ore": [],
        }
    )
    return out


def machines() -> list[dict[str, object]]:
    return [
        {
            "mte_id": 1000,
            "item": "gregtech:gt.blockmachines",
            "meta": 1000,
            "class": "test.MTEMixer",
            "mod": "gregtech",
            "meta_name": "multimachine.testmixer",
            "name": "Test Mixer",
            "multiblock": True,
            "recipe_maps": ["gt.recipe.mixer", "gt.recipe.blastfurnace"],
            "flags": {
                "batch_mode": True,
                "input_separation": False,
                "void_protection": True,
                "recipe_locking": True,
            },
            "tooltip": ["§7Mixes things"],
            "structure_tooltip": ["Controller: front"],
        },
    ]


def fluids() -> list[dict[str, object]]:
    return [
        {
            "fluid": f,
            "unlocalized": f"fluid.{f}",
            "name": name,
            "temperature": 300,
            "density": 1000,
            "viscosity": 1000,
            "gaseous": False,
        }
        for f, name in [
            ("water", "Water"),
            ("ic2distilledwater", "Distilled Water"),
            ("molten.test", "Molten Test"),
        ]
    ]


def jsonl(lines: list[dict[str, object]], end: str = "\n") -> bytes:
    return "".join(json.dumps(x) + end for x in lines).encode()


def default_files(nei: bool = True) -> dict[str, bytes]:
    files = {
        "gt_recipe_maps.json": json.dumps(recipe_maps()).encode() + b"\n",
        "gt_recipes.jsonl": jsonl(gt_recipes()),
        "gt_machines.jsonl": jsonl(machines()),
        "items.jsonl": jsonl(items()),
        "fluids.jsonl": jsonl(fluids()),
    }
    if nei:
        files.update({rel: jsonl(lines, "\r\n") for rel, lines in nei_files().items()})
    return files


MODS = [
    {
        "modid": "gregtech",
        "name": "GregTech",
        "version": "5.09.54.265",
        "file": "mods/gt.jar",
        "sha256": "0" * 64,
    },
    {
        "modid": "TwistSpaceTechnology",
        "name": "Twist Space Technology",
        "version": "0.8.0",
        "file": "mods/tst.jar",
        "sha256": "1" * 64,
    },
    {
        "modid": "programmablehatches",
        "name": "ProgrammableHatches",
        "version": "0.2.1p0",
        "file": "mods/ph.jar",
        "sha256": "2" * 64,
    },
]


def make_run(
    home: Path,
    run_id: str = RUN,
    created: str = "2026-11-06T12:00:00Z",
    files: dict[str, bytes] | None = None,
    **manifest: object,
) -> Path:
    """A collected run in ``home``/data/exports, as ``gos export collect`` leaves it."""
    files = default_files() if files is None else files
    run = home / collect.DATA_EXPORTS / run_id
    for rel, data in files.items():
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_bytes(data)
    m: dict[str, object] = {
        "format": "gregteclink-export/1",
        "run_id": run_id,
        "created_utc": created,
        "complete": True,
        "mode": "autoexport",
        "side": "client",
        "pack_version": "2.9.0",
        "gt_version": "5.09.54.265",
        "nei_version": "2.8.172-GTNH",
        "gregteclink": {"version": "abcdef123456"},
        "mods": MODS,
        "files": {
            rel: {
                "sha256": hashlib.sha256(d).hexdigest(),
                "bytes": len(d),
                "lines": d.count(b"\n"),
            }
            for rel, d in files.items()
        },
        "counts": {
            "recipe_maps": 4,
            "recipes": 13,
            "recipe_errors": 1,
            "machines": 1,
            "items": 20,
            "fluids": 3,
            "recipe_map_errors": 0,
            "machine_errors": 0,
            "item_errors": 0,
            "unregistered": 0,
        },
        "parts": {
            "gt_dump": "ok",
            "machines": "ok",
            "refs": "ok",
            "nei": "ok" if any(r.startswith("nei/") for r in files) else "absent",
            "autoexport": "ok",
        },
    }
    m.update(manifest)
    (run / collect.MANIFEST).write_text(json.dumps(m), encoding="utf-8")
    return run


@pytest.fixture
def allow_list(tmp_path: Path) -> Path:
    """The fixture's one genuine collision (recipes 6 and 7) allow-listed."""
    path = tmp_path / "collisions.tsv"
    sig, content = collision_row(tmp_path / "probe")
    write_allow_list(path, sig, content)
    return path


def write_allow_list(path: Path, sig: str, content: list[str]) -> None:
    path.write_text(
        "# test allow-list\n" + "\t".join(merge.ALLOW_HEADER) + "\n"
        f"gt.recipe.mixer\t{sig}\t{','.join(content)}\tregistered twice, 100 and 20 ticks\n",
        encoding="utf-8",
    )


def collision_row(home: Path) -> tuple[str, list[str]]:
    """(sig12, content12 list) of the fixture's collision, from the curation task."""
    make_run(home)
    with pytest.raises(build.BuildError, match="1 identity collision"):
        build.build(home, allow_list=home / "none.tsv")
    task = (home / build.CURATION / f"collisions-{RUN}.tsv").read_text(encoding="utf-8")
    rows = [
        line.split("\t")
        for line in task.splitlines()
        if line.startswith("gt.recipe.mixer\t")
    ]
    assert len(rows) == 1
    return rows[0][1], rows[0][2].split(",")


def built(home: Path, allow_list: Path, **kw: object) -> build.Result:
    return build.build(home, allow_list=allow_list, **kw)


def rows(db: Path, sql: str, *args: object) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def recipe_id(db: Path, where: str, *args: object) -> int:
    found = rows(
        db,
        f"SELECT r.id FROM recipe r LEFT JOIN recipe_map m ON m.id = r.recipe_map_id WHERE {where}",
        *args,
    )
    assert len(found) == 1, found
    return found[0][0]


def inputs(db: Path, rid: int) -> list[tuple[Any, ...]]:
    return rows(
        db,
        "SELECT ri.slot, coalesce(i.registry_name || '@' || i.meta, 'fluid:' || f.name), ri.amount,"
        " ri.consumed, ri.chance_bp, ri.circuit, o.name FROM recipe_input ri"
        " LEFT JOIN item i ON i.id = ri.item_id LEFT JOIN fluid f ON f.id = ri.fluid_id"
        " LEFT JOIN ore o ON o.id = ri.ore_id WHERE ri.recipe_id = ? ORDER BY ri.slot",
        rid,
    )


def alts(db: Path, rid: int, slot: int) -> set[str]:
    return {
        r[0]
        for r in rows(
            db,
            "SELECT coalesce(i.registry_name || '@' || i.meta, 'fluid:' || f.name) FROM recipe_input_alt a"
            " LEFT JOIN item i ON i.id = a.item_id LEFT JOIN fluid f ON f.id = a.fluid_id"
            " WHERE a.recipe_id = ? AND a.slot = ?",
            rid,
            slot,
        )
    }


# --- the tests -----------------------------------------------------------------------------


def test_a_run_loads_with_every_rule(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    result = built(tmp_path, allow_list)
    db = result.db
    assert db == tmp_path / "data" / "gos.sqlite"
    stats = result.loaded.stats
    assert stats is not None
    assert (
        stats.exact,
        stats.matched,
        sum(stats.duplicates.values()),
        sum(stats.dropped.values()),
    ) == (3, 2, 1, 1)
    assert stats.nei_only == 8
    assert dict(stats.not_recipes) == {h: 1 for h in FUEL_AND_FACADE}
    keys = result.loaded.keys
    assert keys is not None and dict(keys.duplicates) == {"gt": 1, "nei": 1}
    by_source = dict(rows(db, "SELECT source, count(*) FROM recipe GROUP BY source"))
    # GT: 12 recipe lines, 1 exact copy dropped; NEI: 4 shaped (2 kept apart by oredict, 1 copy
    # dropped, 1 NBT), 1 shapeless, 1 furnace, 1 other handler, 1 in the empty GT category; the
    # fuel and facade lines are left out.
    assert by_source == {"gt+nei": 5, "gt": 6, "crafting": 4, "furnace": 1, "nei": 2}
    refs = [r[0] for r in rows(db, "SELECT ref FROM recipe ORDER BY ref")]
    assert len(set(refs)) == len(refs) == 18
    assert all(r.startswith("r:") and "/" in r for r in refs)
    run = rows(
        db, "SELECT run_key, gt_version, nei_version, fingerprint FROM export_run"
    )
    assert run == [(RUN, "5.09.54.265", "2.8.172-GTNH", "")]
    assert rows(db, "SELECT count(*) FROM mod") == [(3,)]
    assert result.loaded.recipe_errors[0].error == "java.lang.NullPointerException"


def test_circuits_chances_and_fluids(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    db = built(tmp_path, allow_list).db
    rid = recipe_id(db, "r.eut = 30 AND r.duration_ticks = 100")
    assert inputs(db, rid) == [
        (0, "test:dust@1", 2, 1, 10000, None, None),
        (1, f"{CIRCUIT}@3", 0, 0, 10000, 3, None),
        (2, "fluid:water", 1000, 1, 10000, None, None),
    ]
    # GT's water alternatives, plus the one NEI also shows.
    assert alts(db, rid, 2) == {
        "fluid:water",
        "fluid:ic2distilledwater",
        "fluid:test.water",
    }
    assert rows(
        db, "SELECT amount, chance_bp FROM recipe_output WHERE recipe_id = ?", rid
    ) == [(3, 7500)]
    assert rows(
        db, "SELECT source, voltage_tier, amperage FROM recipe WHERE id = ?", rid
    ) == [("gt+nei", 1, 1)]
    # 64 EU/t at 2 A is 32 EU/t per amp: LV.
    tst = recipe_id(db, "m.unloc_name = 'tst.recipe.test'")
    assert rows(
        db, "SELECT voltage_tier, amperage, owners FROM recipe WHERE id = ?", tst
    ) == [(1, 2, '["TwistSpaceTechnology"]')]


def test_oredict_slots_keep_their_name_and_alternatives(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path)
    db = built(tmp_path, allow_list).db
    rid = recipe_id(db, "r.eut = 7")
    assert inputs(db, rid) == [(0, "test:dye@0", 1, 1, 10000, None, "dyeBlack")]
    assert alts(db, rid, 0) == {"test:dye@0", "test:gtdye@14", "other:dye@3"}
    members = rows(
        db,
        "SELECT i.registry_name FROM ore_member m JOIN ore o ON o.id = m.ore_id"
        " JOIN item i ON i.id = m.item_id WHERE o.name = 'dyeBlack' ORDER BY 1",
    )
    assert members == [("test:dye",), ("test:gtdye",)]


def test_nbt_keys_never_hold_numeric_ids(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    db = built(tmp_path, allow_list).db
    sticks = rows(
        db,
        "SELECT nbt_hash, nbt_json FROM item WHERE registry_name = 'test:datastick' AND nbt_hash != ''",
    )
    # GT's data stick and NEI's (whose NBT has only the numeric ID) are one item.
    assert len(sticks) == 1
    nbt = json.loads(sticks[0][1])
    assert nbt["output"] == {"Count": "1B", "Damage": "0S", "__item": "test:plate"}
    assert rows(db, "SELECT name_en FROM item WHERE nbt_hash = ?", sticks[0][0]) == [
        ("Plate Construction Data",)
    ]
    merged = recipe_id(db, "r.eut = 30 AND r.duration_ticks = 50")
    assert rows(db, "SELECT source FROM recipe WHERE id = ?", merged) == [("gt+nei",)]
    # The shaped recipe that takes the stick uses the same item row.
    copy_ = rows(
        db,
        "SELECT ri.item_id FROM recipe_input ri JOIN recipe r ON r.id = ri.recipe_id"
        " JOIN recipe_output o ON o.recipe_id = r.id JOIN item i ON i.id = o.item_id"
        " WHERE i.registry_name = 'test:copy'",
    )
    assert copy_ == rows(db, "SELECT id FROM item WHERE nbt_hash = ?", sticks[0][0])


def test_metadata_special_value_and_flags(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    db = built(tmp_path, allow_list).db
    rid = recipe_id(db, "m.unloc_name = 'gt.recipe.blastfurnace'")
    meta = {
        k: (num, text)
        for k, num, text in rows(
            db,
            "SELECT key, value_num, value_text FROM recipe_meta WHERE recipe_id = ?",
            rid,
        )
    }
    assert meta["coil_heat"] == (1800.0, None)
    assert meta["cleanroom"] == (1.0, None)
    assert meta["can_be_buffered"] == (1.0, None)
    assert meta["nei_desc"] == (None, '["Needs a cleanroom"]')
    assert json.loads(meta["special_items"][1]) == {
        "item": "test:module",
        "meta": 0,
        "n": 1,
    }
    assert json.loads(meta["qft_catalyst"][1]) == {
        "item": "test:catalyst",
        "meta": 0,
        "n": 0,
    }
    assert rows(
        db,
        "SELECT special_value, enabled, hidden, fake, source FROM recipe WHERE id = ?",
        rid,
    ) == [(-200, 1, 0, 0, "gt+nei")]


def test_nei_only_recipes(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    db = built(tmp_path, allow_list).db
    tables = rows(
        db,
        "SELECT r.ref FROM recipe r JOIN recipe_output o ON o.recipe_id = r.id JOIN item i ON i.id = o.item_id"
        " WHERE i.registry_name = 'test:table'",
    )
    # Four oak planks, and four of any plank: two recipes (D104).
    assert len(tables) == 2
    assert all(
        t[0].startswith("r:codechicken.nei.recipe.ShapedRecipeHandler/") for t in tables
    )
    cake = recipe_id(
        db,
        "r.source = 'crafting' AND r.nei_handler_id = (SELECT id FROM nei_handler WHERE handler_name LIKE '%Shapeless%')",
    )
    assert rows(
        db,
        "SELECT value_text FROM recipe_meta WHERE recipe_id = ? AND key = 'returns'",
        cake,
    ) == [('[[0,"minecraft:bucket@0"]]',)]
    assert rows(db, "SELECT fake, enabled, eut FROM recipe WHERE id = ?", cake) == [
        (0, 1, None)
    ]
    furnace = rows(db, "SELECT count(*) FROM recipe WHERE source = 'furnace'")
    assert furnace == [(1,)]
    # A GT category GT's dump has no recipe in: NEI's line is the recipe, in that map.
    empty = recipe_id(db, "r.source = 'nei' AND m.unloc_name = 'gt.recipe.empty'")
    assert rows(db, "SELECT category, eut, fake FROM recipe WHERE id = ?", empty) == [
        ("gt.recipe.empty", 30, None)
    ]
    handlers = dict(rows(db, "SELECT handler_name, name_en FROM nei_handler"))
    assert handlers["other.blast"] == "Blast Furnace"  # NEI's _2 suffix dropped
    assert handlers["gt.recipe.mixer"] == "Mixer"
    assert rows(
        db, "SELECT name_en FROM recipe_map WHERE unloc_name = 'gt.recipe.mixer'"
    ) == [("Mixer",)]


def test_catalog_machines_items_fluids(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    db = built(tmp_path, allow_list).db
    machine = rows(
        db, "SELECT mte_id, mod_id, name_en, is_multiblock, tooltip FROM machine"
    )
    assert machine == [(1000, "gregtech", "Test Mixer", 1, "§7Mixes things")]
    maps = rows(
        db,
        "SELECT m.unloc_name, mm.priority FROM machine_map mm JOIN recipe_map m ON m.id = mm.recipe_map_id ORDER BY 2",
    )
    assert maps == [("gt.recipe.mixer", 0), ("gt.recipe.blastfurnace", 1)]
    facts = dict(rows(db, "SELECT key, value FROM machine_fact WHERE source = 'code'"))
    assert facts == {
        "batch_mode": "true",
        "input_separation": "false",
        "void_protection": "true",
        "recipe_locking": "true",
    }
    dust = rows(
        db,
        "SELECT name_en, mod_id, gt_prefix, gt_material, gt_material_amount FROM item"
        " WHERE registry_name = 'test:dust' AND meta = 1",
    )
    assert dust == [("Test Dust", "test", "dust", "Test", 3628800)]
    # An item only NEI shows is named from NEI's stack.
    assert rows(db, "SELECT name_en FROM item WHERE registry_name = 'test:cake'") == [
        ("Cake",)
    ]
    assert rows(db, "SELECT name_en FROM fluid WHERE name = 'test.water'") == [(None,)]
    assert rows(db, "SELECT name_en, temperature FROM fluid WHERE name = 'water'") == [
        ("Water", 300)
    ]


def test_without_the_allow_list_a_collision_fails_and_writes_nothing(
    tmp_path: Path,
) -> None:
    make_run(tmp_path)
    with pytest.raises(build.BuildError, match="1 identity collision") as err:
        build.build(tmp_path, allow_list=tmp_path / "none.tsv")
    assert "curation" in str(err.value)
    assert not (tmp_path / "data" / "gos.sqlite").exists()
    task = (tmp_path / build.CURATION / f"collisions-{RUN}.tsv").read_text(
        encoding="utf-8"
    )
    row = next(
        line for line in task.splitlines() if line.startswith("gt.recipe.mixer\t")
    )
    content = row.split("\t")[2].split(",")
    assert len(content) == 2 and all(len(c) == 12 for c in content)
    assert content == sorted(content)  # a new group: content-hash order
    assert row.split("\t")[3] == "TODO: duration 100 | 20"
    assert "#   -1 content" in task and "#   -2 content" in task
    assert not list((tmp_path / build.CURATION).glob("*.tmp"))


def test_an_allow_listed_collision_gets_suffixes_by_content_hash(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path)
    result = built(tmp_path, allow_list)
    sig = allow_list.read_text().splitlines()[-1].split("\t")[1]
    found = rows(
        result.db,
        "SELECT ref, duration_ticks, content_hash FROM recipe WHERE ref LIKE ? ORDER BY ref",
        f"%/{sig}-%",
    )
    assert [r[0] for r in found] == [
        f"r:gt.recipe.mixer/{sig}-1",
        f"r:gt.recipe.mixer/{sig}-2",
    ]
    assert found[0][2] < found[1][2]
    assert sorted(r[1] for r in found) == [20, 100]


def test_an_allow_list_entry_must_have_the_groups_size(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path)
    sig, content = collision_row(tmp_path / "probe2")
    write_allow_list(allow_list, sig, [*content, "0" * 12])
    with pytest.raises(build.BuildError, match="1 identity collision"):
        built(tmp_path, allow_list)
    for bad in (
        "map\tsig12\n",
        f"map\tsig12\tcontent12\twhy\ngt.recipe.mixer\t{sig}\t2\twhy\n",
        f"map\tsig12\tcontent12\twhy\ngt.recipe.mixer\t{sig}\t{content[0]}\twhy\n",
        f"map\tsig12\tcontent12\twhy\ngt.recipe.mixer\t{sig}\t{content[0]},XYZ\twhy\n",
    ):
        allow_list.write_text(bad)
        with pytest.raises(build.BuildError, match="allow-list"):
            built(tmp_path, allow_list)


def test_an_allow_listed_groups_order_is_kept(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    sig, content = collision_row(tmp_path / "probe2")
    write_allow_list(allow_list, sig, list(reversed(content)))
    result = built(tmp_path, allow_list)
    found = rows(
        result.db,
        "SELECT ref, substr(content_hash, 1, 12) FROM recipe WHERE ref LIKE ? ORDER BY ref",
        f"%/{sig}-%",
    )
    # The listed order, not content-hash order: a citation keeps its recipe.
    assert [c for _ref, c in found] == list(reversed(content))


def test_the_repos_allow_list_parses() -> None:
    allowed = merge.read_allow_list()
    assert allowed and all(
        len(sig) == 12 and len(content) >= 2 and all(len(c) == 12 for c in content)
        for (_m, sig), content in allowed.items()
    )


def shuffled_export(
    files: dict[str, bytes], change: Callable[[list[dict[str, Any]]], None]
) -> dict[str, bytes]:
    """The same pack exported again: GT's lines re-sorted with new idx, outputs reversed."""
    lines = [json.loads(x) for x in files["gt_recipes.jsonl"].splitlines()]
    for line in lines:
        if "out" in line:
            line["out"] = list(reversed(line["out"]))
            if "out_chance" in line:
                line["out_chance"] = list(reversed(line["out_chance"]))
        line["idx"] = 100 - line["idx"]
    lines.reverse()
    change(lines)
    return {**files, "gt_recipes.jsonl": jsonl(lines)}


def test_keys_survive_a_new_export_and_a_rebalance(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path)
    first = built(tmp_path, allow_list)

    def rebalance(lines: list[dict[str, Any]]) -> None:
        for line in lines:
            if line.get("map") == "gt.recipe.mixer" and line.get("eut") == 7:
                line["duration"] = 150  # NEI's line for it then no longer merges

    files = shuffled_export(default_files(), rebalance)
    make_run(tmp_path, "20261107T120000Z", "2026-11-07T12:00:00Z", files)
    second = built(tmp_path, allow_list)
    assert second.run_id != first.run_id
    q = "SELECT r.ref, r.signature, r.content_hash FROM recipe r JOIN export_run e ON e.id = r.run_id WHERE e.run_key = ? AND r.source LIKE 'gt%' ORDER BY 1"
    old = rows(second.db, q, RUN)
    new = rows(second.db, q, "20261107T120000Z")
    assert [r[:2] for r in old] == [
        r[:2] for r in new
    ]  # every key and identity survives
    changed = [o[0] for o, n in zip(old, new, strict=True) if o[2] != n[2]]
    assert changed == [
        recipe_ref(second.db, "r.eut = 7 AND r.run_id = ?", second.run_id)
    ]


def recipe_ref(db: Path, where: str, *args: object) -> str:
    return rows(db, f"SELECT ref FROM recipe r WHERE {where}", *args)[0][0]


def test_runs_past_keep_are_pruned_unless_a_plan_cites_them(
    tmp_path: Path, allow_list: Path
) -> None:
    for day in (6, 7, 8):
        make_run(tmp_path, f"202611{day:02}T120000Z", f"2026-11-{day:02}T12:00:00Z")
    first = built(tmp_path, allow_list, run_key="20261106T120000Z")
    conn = sqlite3.connect(first.db)
    conn.execute(
        "INSERT INTO plan(id, name, run_id) VALUES ('plan-1', 'steel line', ?)",
        (first.run_id,),
    )
    conn.commit()
    conn.close()
    built(tmp_path, allow_list, run_key="20261107T120000Z", keep=1)
    third = built(tmp_path, allow_list, run_key="20261108T120000Z", keep=1)
    assert third.pruned == ["20261107T120000Z"]
    keys = rows(third.db, "SELECT run_key FROM export_run ORDER BY run_key")
    assert keys == [("20261106T120000Z",), ("20261108T120000Z",)]
    orphans = rows(
        third.db,
        "SELECT count(*) FROM recipe_input WHERE recipe_id NOT IN (SELECT id FROM recipe)",
    )
    assert orphans == [(0,)]


def test_the_same_input_gives_byte_identical_keys_and_hashes(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path)
    a = built(tmp_path, allow_list, db=tmp_path / "a.sqlite")
    b = built(tmp_path, allow_list, db=tmp_path / "b.sqlite")
    recipes = "SELECT ref, signature, content_hash, source FROM recipe ORDER BY ref"
    items = "SELECT registry_name, meta, nbt_hash, nbt_json FROM item ORDER BY 1, 2, 3"
    assert rows(a.db, recipes) == rows(b.db, recipes)
    assert rows(a.db, items) == rows(b.db, items)
    # Loading the same run again replaces it rather than adding a copy.
    again = built(tmp_path, allow_list, db=tmp_path / "a.sqlite")
    assert again.replaced and again.run_id == a.run_id
    assert rows(a.db, recipes) == rows(b.db, recipes)
    assert rows(a.db, "SELECT count(*) FROM export_run") == [(1,)]


def test_without_neis_files_only_gt_is_loaded(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path, files=default_files(nei=False))
    result = built(tmp_path, allow_list)
    assert dict(rows(result.db, "SELECT source, count(*) FROM recipe GROUP BY 1")) == {
        "gt": 11
    }
    # A failed NEI step leaves the files unused even when they are there.
    home = tmp_path / "failed"
    make_run(home, parts={"gt_dump": "ok", "nei": "failed"})
    result = built(home, allow_list)
    assert dict(rows(result.db, "SELECT source, count(*) FROM recipe GROUP BY 1")) == {
        "gt": 11
    }
    assert any(
        "parts.nei is 'failed'" in line for line in report.lines(result, full=False)
    )


def test_refusals_leave_the_db_alone(tmp_path: Path, allow_list: Path) -> None:
    with pytest.raises(build.BuildError, match="gos export collect"):
        built(tmp_path, allow_list)
    make_run(tmp_path, mode="harness")
    with pytest.raises(build.BuildError, match="no collected run"):
        built(tmp_path, allow_list)
    assert built(tmp_path, allow_list, allow_harness=True).run_id == 1
    # A file damaged since collect: refused before anything is read.
    (tmp_path / collect.DATA_EXPORTS / RUN / "items.jsonl").write_bytes(b"{}\n")
    with pytest.raises(build.BuildError, match="collect it again"):
        built(tmp_path, allow_list, allow_harness=True)
    with pytest.raises(build.BuildError, match="no such collected run"):
        built(tmp_path, allow_list, run_key="nope")


def test_a_collect_in_progress_blocks_the_build(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path)
    with (
        collect.lock(tmp_path),
        pytest.raises(
            build.BuildError, match="another gos export collect or gos db build"
        ),
    ):
        built(tmp_path, allow_list)
    assert built(tmp_path, allow_list).run_id == 1


@pytest.mark.parametrize(
    ("bad", "where"),
    [
        (b"not json\n", "gt_recipes.jsonl:1: not JSON"),
        (
            json.dumps(gt("gt.recipe.nope", 0, 1, 1)).encode() + b"\n",
            "gt_recipes.jsonl:1: map 'gt.recipe.nope'",
        ),
        (
            json.dumps({**gt("gt.recipe.mixer", 0, 1, 1), "eut": "30"}).encode()
            + b"\n",
            "eut is not an integer",
        ),
        (
            json.dumps({**gt("gt.recipe.mixer", 0, 1, 1), "fake": None}).encode()
            + b"\n",
            "fake is not true or false",
        ),
    ],
)
def test_a_bad_line_fails_the_build_with_its_place(
    tmp_path: Path, allow_list: Path, bad: bytes, where: str
) -> None:
    make_run(tmp_path)
    good = built(tmp_path, allow_list)
    before = rows(good.db, "SELECT ref, content_hash FROM recipe ORDER BY ref")
    make_run(
        tmp_path,
        "20261107T120000Z",
        "2026-11-07T12:00:00Z",
        {**default_files(), "gt_recipes.jsonl": bad},
    )
    with pytest.raises(build.BuildError, match=where):
        built(tmp_path, allow_list)
    assert rows(good.db, "SELECT ref, content_hash FROM recipe ORDER BY ref") == before


def test_a_failed_write_rolls_back(
    tmp_path: Path, allow_list: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_run(tmp_path)
    good = built(tmp_path, allow_list)
    before = rows(good.db, "SELECT count(*) FROM recipe")

    def boom(*_args: object) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(build, "_names", boom)
    make_run(tmp_path, "20261107T120000Z", "2026-11-07T12:00:00Z")
    with pytest.raises(sqlite3.OperationalError):
        built(tmp_path, allow_list)
    assert rows(good.db, "SELECT count(*) FROM recipe") == before
    assert rows(good.db, "SELECT run_key FROM export_run") == [(RUN,)]


def test_a_full_disk_is_reported_as_such(
    tmp_path: Path, allow_list: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA W4: SQLite rolls back by itself on SQLITE_FULL; the build reports the full disk, not
    'cannot rollback - no transaction is active'."""
    make_run(tmp_path)
    good = built(tmp_path, allow_list)
    before = rows(good.db, "SELECT count(*) FROM recipe")

    def fill(conn: sqlite3.Connection) -> None:
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        conn.execute(f"PRAGMA max_page_count = {pages + 1}")
        conn.execute("CREATE TABLE junk(x BLOB)")
        conn.execute("INSERT INTO junk VALUES (zeroblob(1000000))")

    monkeypatch.setattr(build, "_names", fill)
    make_run(tmp_path, "20261107T120000Z", "2026-11-07T12:00:00Z")
    with pytest.raises(sqlite3.OperationalError, match="full"):
        built(tmp_path, allow_list)
    assert rows(good.db, "SELECT count(*) FROM recipe") == before
    assert rows(good.db, "SELECT run_key FROM export_run") == [(RUN,)]


def test_name_search_and_named_queries(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    result = built(tmp_path, allow_list)
    conn = access.connect(result.db, readonly=True)
    try:
        names = access.query(conn, "find_names", {"q": "dust", "limit": 10})
        assert {(r["kind"], r["name_en"]) for r in names} >= {
            ("item", "Test Dust"),
            ("item", "Fine Dust"),
        }
        assert [
            r["kind"]
            for r in access.query(conn, "find_names", {"q": "mixer", "limit": 10})
        ].count("machine") == 1
        making = access.query(
            conn,
            "recipes_making",
            {"q": '"test ingot"', "run_id": result.run_id, "limit": 10},
        )
        # The planner defaults (enabled, not fake) leave out "other.blast", whose fake is unknown.
        assert {r["source"] for r in making} == {"gt+nei", "furnace"}
        ref = making[0]["ref"]
        one = access.query(conn, "recipe_by_ref", {"run_id": result.run_id, "ref": ref})
        assert len(one) == 1 and one[0]["ref"] == ref
        with pytest.raises(ValueError, match="not a query name"):
            access.query(conn, "../x")
    finally:
        conn.close()


def test_a_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "gos.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {access.SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(access.SchemaError, match="schema version"):
        access.connect(path)
    conn = access.connect(tmp_path / "new.sqlite")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == access.SCHEMA_VERSION
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"recipe", "item", "plan", "llm_usage", "kv", "name_fts"} <= tables
    conn.close()


def test_report_counts_per_mod_and_the_addon_check(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path)
    result = built(tmp_path, allow_list)
    text = "\n".join(report.lines(result, full=True))
    assert (
        "ok          Twist Space Technology (TwistSpaceTechnology 0.8.0): 1 recipe maps, 1 GT "
        "recipes in them, 1 recipes added" in text
    )
    assert "not loaded  GT-Not-Leisure" in text
    # GT's own copies and NEI-only copies are counted apart.
    assert "1 rows dropped as copies of another GT recipe" in text
    assert "(1 of them dropped as copies of another NEI-only recipe)" in text
    assert "2 lines of 2 handlers that aren't recipes, left out" in text
    assert "NEI-only oredict slots: 8 keyed by their oredict name, 1 by the set" in text
    # NEI's recipes in a GT category GT's dump has none in: a warning, and the map is listed.
    assert (
        "WARNING: GT categories with recipes only in NEI (the planner doesn't use them "
        "yet): gt.recipe.empty (1)" in text
    )
    assert "Recipe maps with no GT recipe (1):\n  gt.recipe.empty\n" in text
    assert "ProgrammableHatches" not in text or "none expected" in text
    assert "(it adds to GT's own maps, none expected)" in text
    assert (
        "map gt.recipe.mixer: 10 recipe lines, but gt_recipe_maps.json says 11" in text
    )
    per_mod = {r[0]: r[1:] for r in report.per_mod(result)}
    assert per_mod["gregtech"][:2] == (2, 10)
    assert per_mod["TwistSpaceTechnology"][:3] == (1, 1, 1)
    assert result.report_path is not None
    assert result.report_path.read_text(encoding="utf-8") == text + "\n"
    # An addon that loads but owns no map is listed as missing.
    mods = [*MODS, {"modid": "othtech", "name": "123Technology", "version": "2.2.1.1"}]
    home = tmp_path / "missing"
    make_run(home, mods=mods)
    text = "\n".join(report.lines(built(home, allow_list), full=False))
    assert "MISSING     123Technology (othtech 2.2.1.1): 0 recipe maps" in text
    # An addon whose maps have no GT recipe (added after GT's dump, say) is missing too.
    home = tmp_path / "empty"
    lines = [x for x in gt_recipes() if x.get("map") != "tst.recipe.test"]
    make_run(home, files={**default_files(), "gt_recipes.jsonl": jsonl(lines)})
    text = "\n".join(report.lines(built(home, allow_list), full=False))
    assert (
        "MISSING     Twist Space Technology (TwistSpaceTechnology 0.8.0): 1 recipe maps, 0 GT "
        "recipes in them, 0 recipes added: it loads but its recipe maps have no GT recipe"
        in text
    )
    assert "    no GT recipe in tst.recipe.test" in text


def test_gos_db_build(
    tmp_path: Path,
    allow_list: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    toml = tmp_path / "gos.toml"
    toml.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n[db]\nkeep_runs = 3\n')
    assert config.load(toml).keep_runs == 3
    assert cli.main(["db", "build", "--config", str(toml)]) == 1
    assert "Not built: " in capsys.readouterr().out
    make_run(home)
    # The repo's allow-list doesn't know the fixture's collision.
    assert cli.main(["db", "build", "--config", str(toml)]) == 1
    assert "curation task" in capsys.readouterr().out
    monkeypatch.setattr(merge, "ALLOW_LIST", allow_list)
    assert cli.main(["db", "build", RUN, "--config", str(toml)]) == 0
    out = capsys.readouterr().out
    assert "Collision test: passed" in out and "Per mod:" in out
    assert "NEI handlers:" not in out  # the full list is in the saved report only
    assert (home / build.REPORTS / f"db-{RUN}.txt").is_file()


def test_bad_config_is_exit_2(tmp_path: Path) -> None:
    toml = tmp_path / "gos.toml"
    toml.write_text("[db]\nkeep_runs = 0\n")
    out = io.StringIO()
    assert build.main(toml, None, out) == 2
    assert "keep_runs" in out.getvalue()
    toml.write_text("[db]\nkeep_runs = true\n")
    with pytest.raises(config.ConfigError):
        config.load(toml)


def test_a_reload_keeps_the_runs_fingerprint(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    first = built(tmp_path, allow_list, fingerprint="f" * 64)
    assert rows(first.db, "SELECT fingerprint FROM export_run") == [("f" * 64,)]
    built(tmp_path, allow_list)  # gos db build by hand: no fingerprint to give
    assert rows(first.db, "SELECT fingerprint FROM export_run") == [("f" * 64,)]
    built(tmp_path, allow_list, fingerprint="e" * 64)
    assert rows(first.db, "SELECT fingerprint FROM export_run") == [("e" * 64,)]


def replanked(files: dict[str, bytes], show: int, extra: bool) -> dict[str, bytes]:
    """The same export with NEI showing plank ``show`` in its plank slots and, with ``extra``,
    test:planks@0 added to plankWood (in items.jsonl and in NEI's anyOf)."""
    out = dict(files)
    lines = [json.loads(x) for x in files["nei/Shaped Crafting.jsonl"].splitlines()]
    for line in lines:
        for st in line["ingredients"]:
            if "anyOf" in st:
                st["Damage"] = show
                if extra:
                    st["anyOf"].append({"strId": "test:planks", "Damage": 0})
    out["nei/Shaped Crafting.jsonl"] = jsonl(lines, "\r\n")
    if extra:
        found = [json.loads(x) for x in files["items.jsonl"].splitlines()]
        for item in found:
            if item["item"] == "test:planks":
                item["ore"] = ["plankWood"]
        out["items.jsonl"] = jsonl(found)
    return out


def test_nei_only_oredict_slots_are_keyed_by_their_oredict_name(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path)
    first = built(tmp_path, allow_list)
    db = first.db
    q = (
        "SELECT r.ref, r.content_hash FROM recipe r JOIN recipe_output o ON o.recipe_id = r.id"
        " JOIN item i ON i.id = o.item_id JOIN export_run e ON e.id = r.run_id"
        " WHERE i.registry_name = 'test:table' AND e.run_key = ? ORDER BY 1"
    )
    any_plank = recipe_id(
        db,
        "r.id IN (SELECT recipe_id FROM recipe_input WHERE ore_id ="
        " (SELECT id FROM ore WHERE name = 'plankWood'))",
    )
    # Every plank slot names its oredict (planks@32767 in plankWood covers planks@1).
    assert {r[-1] for r in inputs(db, any_plank)} == {"plankWood"}
    assert alts(db, any_plank, 0) == {"minecraft:planks@0", "minecraft:planks@1"}
    tables = rows(db, q, RUN)
    assert len(tables) == 2  # any plank, and only oak planks

    # NEI shows another plank: nothing changes (NEI picks the stack it shows anew each run).
    files = replanked(default_files(), show=1, extra=False)
    make_run(tmp_path, "20261107T120000Z", "2026-11-07T12:00:00Z", files)
    built(tmp_path, allow_list)
    every = (
        "SELECT r.ref, r.signature, r.content_hash FROM recipe r JOIN export_run e"
        " ON e.id = r.run_id WHERE e.run_key = ? ORDER BY 1"
    )
    assert rows(db, every, "20261107T120000Z") == rows(db, every, RUN)

    # plankWood gains a member: the recipe keeps its key; only its content hash changes.
    files = replanked(default_files(), show=0, extra=True)
    make_run(tmp_path, "20261108T120000Z", "2026-11-08T12:00:00Z", files)
    built(tmp_path, allow_list, keep=3)
    grown = rows(db, q, "20261108T120000Z")
    assert [r[0] for r in grown] == [r[0] for r in tables]
    assert sum(a[1] != b[1] for a, b in zip(tables, grown, strict=True)) == 1
    assert len(rows(db, every, "20261108T120000Z")) == len(rows(db, every, RUN))


def test_handlers_that_are_not_recipes_are_left_out(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path)
    result = built(tmp_path, allow_list)
    db = result.db
    for handler in FUEL_AND_FACADE:
        assert handler in merge.NOT_RECIPES
        assert rows(
            db,
            "SELECT count(*) FROM recipe r JOIN nei_handler h ON h.id = r.nei_handler_id"
            " WHERE h.handler_name = ?",
            handler,
        ) == [(0,)]
    # Still listed as handlers; the facade, keyed by NBT with a numeric block ID, is no item.
    names = {r[0] for r in rows(db, "SELECT handler_name FROM nei_handler")}
    assert set(FUEL_AND_FACADE) <= names
    assert rows(
        db, "SELECT count(*) FROM item WHERE registry_name LIKE 'appliedenergistics2:%'"
    ) == [(0,)]
    text = "\n".join(report.lines(result, full=True))
    assert "Handlers left out (not recipes, D105):" in text
    assert "  codechicken.nei.recipe.FuelRecipeHandler: furnace fuels" in text


def test_a_locked_curation_task_is_written_under_another_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_run(tmp_path)
    real = build.os.replace
    locked = {f"collisions-{RUN}.tsv"}

    def replace(src: Path, dst: Path) -> None:
        if Path(dst).name in locked:  # open in Excel, which locks it on Windows
            raise PermissionError(32, "The process cannot access the file")
        real(src, dst)

    monkeypatch.setattr(build.os, "replace", replace)
    with pytest.raises(build.BuildError, match="1 identity collision") as err:
        build.build(tmp_path, allow_list=tmp_path / "none.tsv")
    second = tmp_path / build.CURATION / f"collisions-{RUN}-2.tsv"
    assert f"The curation task is {second}" in str(err.value)
    assert second.is_file()
    assert not list((tmp_path / build.CURATION).glob("*.tmp"))
    # No name can be written: the collisions are in the message itself.
    locked.update(f"collisions-{RUN}-{n}.tsv" for n in range(2, 10))
    with pytest.raises(build.BuildError, match="1 identity collision") as err:
        build.build(tmp_path, allow_list=tmp_path / "none.tsv")
    assert "couldn't be written" in str(err.value)
    assert "the collisions are gt.recipe.mixer " in str(err.value)
    assert not list((tmp_path / build.CURATION).glob("*.tmp"))


def test_a_report_that_cant_be_saved_still_builds(
    tmp_path: Path, allow_list: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_run(tmp_path)

    def locked(path: Path, text: str) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(build, "_write_text", locked)
    result = built(tmp_path, allow_list)
    assert result.report_path is None and result.report_error is not None
    assert rows(result.db, "SELECT count(*) FROM export_run") == [(1,)]
    text = "\n".join(report.lines(result, full=False))
    assert f"Report not saved: {tmp_path / build.REPORTS}" in text


def test_name_search_matches_names_not_kinds(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    result = built(tmp_path, allow_list)
    conn = access.connect(result.db, readonly=True)
    try:
        for word in ("fluid", "item", "machine", "nei_handler"):
            assert access.query(conn, "find_names", {"q": word, "limit": 50}) == []
        water = access.query(conn, "find_names", {"q": "water", "limit": 50})
        assert ("fluid", "Water") in {(r["kind"], r["name_en"]) for r in water}
    finally:
        conn.close()


def test_opening_the_db_writes_nothing(tmp_path: Path, allow_list: Path) -> None:
    make_run(tmp_path)
    db = built(tmp_path, allow_list).db
    writer = access.connect(db)
    try:
        writer.execute(
            "BEGIN IMMEDIATE"
        )  # a long write, such as a build, holds the lock
        writer.execute("INSERT INTO kv(key, value) VALUES ('stale', '1')")
        reader = access.connect(db)  # would wait 30 s and fail if opening wrote
        try:
            assert reader.execute("SELECT count(*) FROM recipe").fetchone()[0] > 0
        finally:
            reader.close()
        writer.execute("ROLLBACK")
    finally:
        writer.close()


# --- machine facts (WO-1.7) ----------------------------------------------------------------

FACTS_HEADER = "machine\tname\tprocessing\tparallel\tspeed\teu_modifier\toc\textra\tconfidence\tcite\tnote\n"
MIXER_ROW = (
    "test.MTEMixer\tTest Mixer\tlogic\t4*vt\t\t\tperfect\ttier_skips=unlimited\t0.95\t"
    "gt@5.09.54.265:test/MTEMixer.java#createProcessingLogic\tcurated by hand\n"
)


def fact_files() -> dict[str, bytes]:
    lines = machines()
    lines[0]["tooltip"] = [
        "§7Mixes things",
        "§b8§7 Parallels per §9Voltage§7 Tier",
        "§a200%§7 Speed",
    ]
    return {**default_files(), "gt_machines.jsonl": jsonl(lines)}


def curated(path: Path, *lines: str) -> Path:
    path.write_text("# test\n" + FACTS_HEADER + "".join(lines), encoding="utf-8")
    return path


def best_facts(db: Path) -> dict[str, tuple[Any, ...]]:
    conn = access.connect(db, readonly=True)
    try:
        return {
            r["key"]: (r["value"], r["source"], r["confidence"])
            for r in access.query(conn, "machine_facts", {"mte_id": 1000})
        }
    finally:
        conn.close()


def test_machine_facts_from_the_tooltip_and_the_curated_table(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path, files=fact_files())
    table = curated(tmp_path / "facts.tsv", MIXER_ROW)
    result = built(tmp_path, allow_list, curated=table)
    best = best_facts(result.db)
    assert best["parallel"] == ("4*vt", "curated", 0.95)  # the curated row wins
    assert best["speed"] == ("2", "tooltip", 0.85)  # only the tooltip says
    assert best["oc"] == ("perfect", "curated", 0.95)
    assert best["tier_skips"] == ("unlimited", "curated", 0.95)
    assert best["batch_mode"] == ("true", "code", 1.0)  # the machine pass's flags stay
    by_source = dict(
        rows(result.db, "SELECT source, count(*) FROM machine_fact GROUP BY source")
    )
    assert by_source == {"code": 4, "tooltip": 4, "curated": 4}
    note = rows(
        result.db,
        "SELECT note, unit FROM machine_fact WHERE source = 'curated' AND key = 'parallel'",
    )
    assert note == [
        (
            "gt@5.09.54.265:test/MTEMixer.java#createProcessingLogic; curated by hand",
            "recipes",
        )
    ]
    text = "\n".join(report.lines(result, full=True))
    assert (
        "Base multiblocks (GT's jar): 1: covered 1 (0 of them with a run-state input, 0 with "
        "a recipe input, 0 with a planner's choice per run, 0 with outputs not modelled), no "
        "recipes (n/a) 0, flagged 0, missing 0" in text
    )
    assert (
        "Tooltip and curated row disagree on 1 facts, and on 0 facts of one mode"
        in text
    )
    assert "curated rows for 0 classes this run doesn't have" in text
    assert "1000 Test Mixer: parallel tooltip '8*vt'" in text


def test_a_lab_probe_in_the_db_clears_a_flag_in_the_report(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path, files=fact_files())
    flagged = MIXER_ROW.replace("tier_skips=unlimited", "flag=review")
    first = built(tmp_path, allow_list, curated=curated(tmp_path / "a.tsv", flagged))
    assert "flagged 1, missing 0" in "\n".join(report.lines(first, full=False))
    conn = access.connect(first.db)
    try:
        conn.execute(
            "INSERT INTO machine_fact(machine_id, key, value, unit, source, confidence, note)"
            " SELECT id, 'parallel', '16', 'recipes', 'probe', 0.95, 'Lab run' FROM machine"
            " UNION ALL"
            " SELECT id, 'oc', 'perfect', NULL, 'probe', 0.95, 'Lab run' FROM machine"
        )
    finally:
        conn.close()
    text = "\n".join(
        report.lines(
            built(tmp_path, allow_list, curated=curated(tmp_path / "b.tsv", flagged)),
            full=False,
        )
    )
    assert "flagged 0, missing 0" in text and "covered 1 (" in text


def test_a_rebuild_replaces_tooltip_and_curated_facts_but_keeps_probes(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path, files=fact_files())
    db = built(tmp_path, allow_list, curated=curated(tmp_path / "a.tsv", MIXER_ROW)).db
    conn = access.connect(db)
    try:
        conn.execute(
            "INSERT INTO machine_fact(machine_id, key, value, unit, source, confidence, note)"
            " SELECT id, 'parallel', '16', 'recipes', 'probe', 0.99, 'Lab run' FROM machine"
        )
    finally:
        conn.close()
    result = built(tmp_path, allow_list, curated=curated(tmp_path / "b.tsv"))
    best = best_facts(result.db)
    assert best["parallel"] == ("16", "probe", 0.99)
    assert best["oc"] == ("standard", "tooltip", 0.5)  # the curated row is gone
    assert rows(
        result.db, "SELECT count(*) FROM machine_fact WHERE source = 'curated'"
    ) == [(0,)]
    text = "\n".join(report.lines(result, full=False))
    assert "no recipes (n/a) 0, flagged 0, missing 1" in text
    assert "covered 0 (" in text
    assert "WARNING: 1 base multiblocks have neither the facts nor a flag" in text


def test_a_bad_curated_table_fails_before_the_db_is_touched(
    tmp_path: Path, allow_list: Path
) -> None:
    make_run(tmp_path)
    bad = curated(tmp_path / "bad.tsv", MIXER_ROW.replace("4*vt", "4*tier"))
    with pytest.raises(build.BuildError, match=r"curated machine facts: bad\.tsv:3: "):
        built(tmp_path, allow_list, curated=bad)
    assert not access.db_path(tmp_path).exists()


# --- the full ore dictionary (D145) ---------------------------------------------------------

PRESS = "test.press"  # an NEI-only handler


def press_line(shown: tuple[str, int]) -> dict[str, object]:
    """An NEI-only recipe: 2 of any test ingot (ours, and another mod's that GT's files never
    reference, so items.jsonl doesn't list it) into a plate; NEI shows ``shown``."""
    return nei_line(
        PRESS,
        [
            stack(
                shown[0],
                shown[1],
                2,
                anyOf=[
                    {"strId": "test:ingot", "Damage": 1},
                    {"strId": "other:ingot", "Damage": 7},
                ],
            )
        ],
        [stack("test:plate", 0, 1)],
        None,
    )


def oredict_lines() -> list[dict[str, object]]:
    return [
        {"ore": "dustTest", "members": [{"item": "test:dust", "meta": 1}]},
        {"ore": "emptyName", "members": []},
        {
            "ore": "ingotTest",
            "members": [
                {"item": "other:ingot", "meta": 7},
                {"item": "test:ingot", "meta": 1},
            ],
        },
        {"ore": "plankWood", "members": [{"item": "minecraft:planks", "meta": 32767}]},
    ]


def with_press(
    shown: tuple[str, int] = ("test:ingot", 1), oredict: bool = True
) -> dict[str, bytes]:
    files = default_files()
    files["nei/Press.jsonl"] = jsonl([press_line(shown)], "\r\n")
    if oredict:
        files["oredict.jsonl"] = jsonl(oredict_lines())
    return files


def parts(oredict: str | None) -> dict[str, str]:
    out = {
        "gt_dump": "ok",
        "machines": "ok",
        "refs": "ok",
        "nei": "ok",
        "autoexport": "ok",
    }
    if oredict is not None:
        out["oredict"] = oredict
    return out


PRESS_SLOT = (
    "SELECT r.ref, o.name FROM recipe r JOIN nei_handler h ON h.id = r.nei_handler_id"
    " JOIN recipe_input ri ON ri.recipe_id = r.id LEFT JOIN ore o ON o.id = ri.ore_id"
    " JOIN export_run e ON e.id = r.run_id WHERE h.handler_name = ? AND e.run_key = ?"
)


def test_read_oredict_keys_members_as_items_are_keyed(tmp_path: Path) -> None:
    lines = [
        {
            "ore": "plankWood",
            "members": [{"item": "minecraft:planks", "meta": WILDCARD}],
        },
        {
            "ore": "stickData",
            "members": [{"item": "test:datastick", "meta": 0, "nbt": DATA_STICK_NBT}],
        },
        {"ore": "broken", "error": "IllegalStateException: no"},
    ]
    (tmp_path / gtdump.ORE_DICT).write_bytes(jsonl(lines))
    ids: dict[int, str | None] = {}
    got = list(gtdump.read_oredict(tmp_path, ids))
    stick = nbt_hash(normalize_nbt(DATA_STICK_NBT))  # as items.jsonl's line is keyed
    assert got == [
        gtdump.OreName("plankWood", (Ref(ITEM, "minecraft:planks", WILDCARD),), None),
        gtdump.OreName("stickData", (Ref(ITEM, "test:datastick", 0, stick),), None),
        gtdump.OreName("broken", (), "IllegalStateException: no"),
    ]
    assert ids == {628: "test:plate"}  # the run's numeric ID table, for NEI's NBT
    assert gtdump.oredict_status({"parts": {"oredict": "ok"}}) == "ok"
    assert gtdump.oredict_status({"parts": {}}) == "absent"

    (tmp_path / gtdump.ORE_DICT).write_bytes(jsonl([{"ore": "x", "members": [5]}]))
    with pytest.raises(gtdump.FormatError, match=r"oredict\.jsonl:1: member"):
        list(gtdump.read_oredict(tmp_path))


def test_the_full_oredict_names_slots_items_jsonl_cannot(
    tmp_path: Path, allow_list: Path
) -> None:
    # Before D145 (no oredict.jsonl): other:ingot@7 is in no ore list, so the slot is keyed
    # by the set of what it accepts.
    make_run(tmp_path, files=with_press(oredict=False), parts=parts(None))
    first = built(tmp_path, allow_list)
    db = first.db
    [(old_ref, old_ore)] = rows(db, PRESS_SLOT, PRESS, RUN)
    assert old_ore is None
    assert first.loaded.oredict is None

    # With the full ore dictionary: keyed by ingotTest.
    run2 = "20261107T120000Z"
    make_run(tmp_path, run2, "2026-11-07T12:00:00Z", with_press(), parts=parts("ok"))
    second = built(tmp_path, allow_list)
    [(ref, ore)] = rows(db, PRESS_SLOT, PRESS, run2)
    assert ore == "ingotTest"
    assert ref != old_ref  # a new key, once: the slot is now keyed ore:ingotTest
    assert first.loaded.stats is not None and second.loaded.stats is not None
    assert second.loaded.stats.ore_named == first.loaded.stats.ore_named + 1
    # Every name is in `ore`, the empty one too; members as registered, wildcard rows kept.
    members = rows(
        db,
        "SELECT o.name, i.registry_name || '@' || i.meta FROM ore_member m"
        " JOIN ore o ON o.id = m.ore_id JOIN item i ON i.id = m.item_id"
        " WHERE o.name IN ('ingotTest', 'plankWood') ORDER BY 1, 2",
    )
    assert members == [
        ("ingotTest", "other:ingot@7"),
        ("ingotTest", "test:ingot@1"),
        ("plankWood", "minecraft:planks@0"),  # its own ore list in items.jsonl
        ("plankWood", "minecraft:planks@32767"),
    ]
    assert rows(db, "SELECT count(*) FROM ore WHERE name = 'emptyName'") == [(1,)]
    text = "\n".join(report.lines(second, full=False))
    assert "full ore dictionary (4 names)" in text

    # NEI shows the other mod's ingot next time: the key holds.
    run3 = "20261108T120000Z"
    make_run(
        tmp_path,
        run3,
        "2026-11-08T12:00:00Z",
        with_press(("other:ingot", 7)),
        parts=parts("ok"),
    )
    built(tmp_path, allow_list, keep=3)
    assert rows(db, PRESS_SLOT, PRESS, run3) == [(ref, "ingotTest")]


def test_a_failed_oredict_part_is_refused_when_nei_is_used(
    tmp_path: Path, allow_list: Path
) -> None:
    # items.jsonl's ore lists name fewer NEI-only slots, so building it would give those
    # recipes other keys for one run (D145): refused, the DB untouched.
    make_run(
        tmp_path,
        files=with_press(),
        parts=parts("failed"),
        part_errors={"oredict": "IllegalStateException: boom"},
    )
    with pytest.raises(
        build.BuildError,
        match=r"parts\.oredict is 'failed' \(IllegalStateException: boom\), so its NEI-only",
    ):
        built(tmp_path, allow_list)
    assert not access.db_path(tmp_path).exists()

    # Without NEI the oredict names no slot, so the GT-only run still builds.
    gt_only = parts("failed") | {"nei": "absent"}
    make_run(tmp_path, "20261107T120000Z", "2026-11-07T12:00:00Z", parts=gt_only)
    result = built(tmp_path, allow_list)
    assert result.loaded.oredict is None
    assert result.loaded.oredict_status == "failed"
