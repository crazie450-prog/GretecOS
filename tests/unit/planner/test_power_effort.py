"""The effort model applied to power (WO-4.3, plan §7.2 "Effort model", §8 row 4.3, D17, D62,
D196, D236, D237) on the WO-4.2 fixture DB (tests/unit/planner/test_power.py) plus, for this file
only, the recipes that make its machines:

- ``assembler/0``: 4 Plate A + 1 Dust B -> the electrolyzer (``gregtech:gt.blockmachines@103``);
- ``assembler/1``: 2 Plate A -> the test combustion generator (``@110``);
- ``assembler/2``: 1 Plate A -> an LV dynamo hatch (``@31``, GT's dynamo hatch class);
- an LV assembler (mte 107) to run them;
- crafting-table recipes (NEI's shaped handler): 8 Plate A -> the chemical reactor (``@102``),
  8 Plate A -> the assembler (``@107``), 8 Dust A -> the mixer (``@101``).

Plate A comes from ``mixer/2`` (1 Dust A, 16 EU/t, 60 ticks), Dust A from ``chemicalreactor/1``
(1 Ore A + 100 L water -> 2 Dust A, 30 EU/t, 100 ticks), Dust B from ``mixer/1`` (1 log, 16 EU/t,
60 ticks); ore and logs are raw. Every machine here is LV except the MV electrolyzer, and no
recipe the components use can overclock on them (GT: one overclock needs 4 x the recipe's EU/t
within the machine's voltage, 32 V at LV), so a craft takes the recipe's own ticks. Every item,
recipe, machine and number is made up; no pack data.

The §8 acceptance ("hand-checked on 2 lines") is the two worked examples below: for each line
the expected crafts and hours are derived step by step from the DB rows the test reads by their
``r:`` ids, then compared with the designer's breakdown; the second line has machines My base
doesn't own, so ``unlock_h`` is exercised.
"""

from __future__ import annotations

import importlib.util
import json
import math
import re
import shutil
import sqlite3
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.cards import validate
from gregtec.db import access
from gregtec.db import power as power_db
from gregtec.ingest import power as power_ingest
from gregtec.planner import chains, effort, generators, power, rank
from gregtec.planner.outputs import cards
from gregtec.planner.request import Request


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_wo43_{name}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tpw = _load("test_power")
tg = tpw.tg
MACHINE = "gregtech:gt.blockmachines"
MIXER, REACTOR, ELECTROLYZER, ASSEMBLER = 101, 102, 103, 107
DYNAMO_LV = 31
GENERATOR = tpw.GENERATOR
PLATE, DUST_A, DUST_B = tg.PLATE, tg.DUST_A, tg.DUST_B
SHAPED = "codechicken.nei.recipe.ShapedRecipeHandler"
LV = 1
TURBINE = 1131
TURBINE_CLASS = "gregtech.common.tileentities.machines.multi.MTELargeTurbineGas"
ROTOR_META = generators.SIZES["large"]
ROTOR_NBT = {
    "GT.ToolStats": {"PrimaryMaterial": "Testium", "SecondaryMaterial": "Testium"}
}
POWER_JSON = {
    "sizes": [
        {"size": ROTOR_META, "name": "large", "speed_multiplier": 1.0, "base_damage": 1.0,
         "durability_multiplier": 1.0},
    ],
    "materials": [
        {"name": "Testium", "tool_speed": 1.0, "tool_quality": 1, "durability": 1000,
         "steam_multiplier": 1.0, "gas_multiplier": 1.0, "plasma_multiplier": 1.0,
         "blade": {"item": "test:item", "meta": 2},
         "rotors": [{"size": ROTOR_META,
                     "rotor": {"item": power.ROTOR_ITEM, "meta": ROTOR_META},
                     **{k: 1.0 for k in power_ingest.ROTOR_VALUES}}]},
    ],
    "generators": [
        {"mte_id": GENERATOR, "tier": 1, "efficiency": 100, "eu_out": 32, "amps_out": 1,
         "class": "gregtech.common.tileentities.generators.MTEDieselGenerator",
         "recipe_map": "gt.recipe.dieselgeneratorfuel", "fuels": []},
    ],
    "containers": [],
}  # fmt: skip


def _stack(meta: int, item: str = tg.T) -> dict[str, object]:
    return {"Count": 1, "Damage": meta, "strId": item, "name": f"{item}@{meta}"}


def make_home(home: Path) -> Path:
    recipes, machines, maps, make = tg.recipes, tg.machines, tg.MAPS, tg.make_home

    def more_recipes() -> list[dict[str, object]]:
        return [
            *recipes(),
            tg.gt("assembler", 0, 30, 200, ins=[tg.ref(2, 4), tg.ref(4)],
                  outs=[tg.ref(ELECTROLYZER, 1, MACHINE)]),
            tg.gt("assembler", 1, 30, 300, ins=[tg.ref(2, 2)],
                  outs=[tg.ref(GENERATOR, 1, MACHINE)]),
            tg.gt("assembler", 2, 30, 100, ins=[tg.ref(2)],
                  outs=[tg.ref(DYNAMO_LV, 1, MACHINE)]),
            tg.gt("assembler", 3, 30, 400, ins=[tg.ref(2, 4)],
                  outs=[tg.ref(ROTOR_META, 1, power.ROTOR_ITEM, nbt=ROTOR_NBT)]),
            tg.gt("assembler", 4, 30, 500, ins=[tg.ref(2, 6)],
                  outs=[tg.ref(TURBINE, 1, MACHINE)]),
            tg.gt("gasturbinefuel", 0, 0, 0, fin=[tg.fl("testgas", 1)], special=30),
        ]  # fmt: skip

    def more_machines() -> list[dict[str, object]]:
        out = machines()
        assembler = dict(out[0])
        assembler.update(
            mte_id=ASSEMBLER, meta=ASSEMBLER, name="Test assembler 1",
            meta_name="basicmachine.assembler.tier.01", recipe_maps=["gt.recipe.assembler"],
        )  # fmt: skip
        dynamo = dict(out[0])
        dynamo.update(
            mte_id=DYNAMO_LV, meta=DYNAMO_LV, name="LV Dynamo Hatch", recipe_maps=[],
            meta_name="hatch.dynamo.tier.01", **{"class": power.DYNAMO_HATCH},
        )  # fmt: skip
        turbine = dict(out[0])
        turbine.update(
            mte_id=TURBINE, meta=TURBINE, tier=None, multiblock=True,
            name="Test Large Gas Turbine", meta_name="multimachine.largegasturbine",
            recipe_maps=[], **{"class": TURBINE_CLASS},
        )  # fmt: skip
        return [*out, assembler, dynamo, turbine]

    shaped = [
        {"handler": SHAPED, "ingredients": [_stack(2)] * 8,
         "results": [_stack(REACTOR, MACHINE)]},
        {"handler": SHAPED, "ingredients": [_stack(2)] * 8,
         "results": [_stack(ASSEMBLER, MACHINE)]},
        {"handler": SHAPED, "ingredients": [_stack(3)] * 8,
         "results": [_stack(MIXER, MACHINE)]},
    ]  # fmt: skip
    nei = {"nei/Shaped Crafting.jsonl": tg.jsonl(shaped)}
    tg.recipes, tg.machines = more_recipes, more_machines
    tg.MAPS = (*maps, "assembler", "gasturbinefuel")
    tg.make_home = lambda h: make(h, nei)
    try:
        db = Path(tpw.make_home(home))
    finally:
        tg.recipes, tg.machines, tg.MAPS, tg.make_home = recipes, machines, maps, make
    # The turbine's rotor (gt_power.json's tables, written by `gos db build`'s own writer;
    # it rewrites the run's generator rows, so the WO-4.2 fixture's one is in POWER_JSON).
    c = sqlite3.connect(db)
    try:
        run = int(c.execute("SELECT MAX(id) FROM export_run").fetchone()[0])
        power_db.write(c, run, power_ingest.parse(POWER_JSON))
        c.commit()
    finally:
        c.close()
    return db


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_home(tmp_path_factory.mktemp("power-effort-home"))


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def no_farms(tmp_path: Path) -> Path:
    path = tmp_path / "farms.tsv"
    path.write_text("\t".join(tpw.farms.HEADER) + "\n", encoding="utf-8")
    return path


def design(
    conn: sqlite3.Connection,
    farms_path: Path,
    owned: Mapping[int, int],
    prefs: rank.Prefs = rank.BALANCED,
) -> power.PowerDesign:
    return power.design(
        conn,
        power.PowerRequest(
            mode="output_per_effort",
            tier_cap=2,
            free=("fluid:water",),
            rotor=("Testium", "large"),
        ),
        tg.Base(owned=dict(owned)),
        prefs=prefs,
        templates=chains.EMPTY,
        farms_path=farms_path,
    )


# --- reading the DB rows the worked examples are derived from ---------------------------------


@dataclass(frozen=True)
class Row:
    """One recipe row, read back from the DB by what it makes."""

    ref: str  # its r: id
    category: str
    ticks: int
    eut: int
    ins: dict[str, int]  # consumed items by stable key, summed over slots
    out: int  # how many of the item one craft makes


def making(conn: sqlite3.Connection, key: str) -> Row:
    """The one enabled, not fake recipe that makes ``key`` (the fixture has one per item;
    GT's recycling recipes take things apart and are never a way to make, D142 (2))."""
    reg, meta = key.rsplit("@", 1)
    rows = conn.execute(
        "SELECT r.id, r.ref, r.category, r.duration_ticks, r.eut, o.amount FROM recipe AS r"
        " JOIN recipe_output AS o ON o.recipe_id = r.id JOIN item AS i ON i.id = o.item_id"
        " WHERE i.registry_name = ? AND i.meta = ? AND r.enabled = 1 AND r.fake = 0"
        " AND COALESCE(r.category, '') NOT LIKE '%recycling%'",
        (reg, int(meta)),
    ).fetchall()
    assert len(rows) == 1, (key, rows)
    rid, ref, category, ticks, eut, amount = rows[0]
    ins: dict[str, int] = {}
    for k, n in conn.execute(
        "SELECT i.registry_name || '@' || i.meta, x.amount FROM recipe_input AS x"
        " JOIN item AS i ON i.id = x.item_id WHERE x.recipe_id = ? AND x.consumed = 1",
        (rid,),
    ):
        ins[k] = ins.get(k, 0) + int(n)
    return Row(
        str(ref), str(category or ""), int(ticks or 0), int(eut or 0), ins, int(amount)
    )


def no_overclock(conn: sqlite3.Connection, row: Row, mte: int) -> float:
    """Machine-seconds per craft of ``row`` on ``mte``: GT overclocks once the recipe's EU/t
    x 4 fits the machine's voltage (8 x 4^tier); none of these recipes can, so a craft takes
    the recipe's ticks / 20."""
    tier = int(
        conn.execute("SELECT tier FROM machine WHERE mte_id = ?", (mte,)).fetchone()[0]
    )
    assert row.eut * 4 > 8 * 4**tier, (row.ref, tier)
    return row.ticks / 20


def counts(p: Mapping[str, Any]) -> dict[int, int]:
    """Machines the line builds, per mte (its nodes' counts)."""
    out: dict[int, int] = {}
    for n in p["nodes"]:
        m = n.get("machine")
        if m and n.get("count"):
            out[m["mte"]] = out.get(m["mte"], 0) + int(n["count"])
    return out


def rows_of(p: Mapping[str, Any], part: str) -> dict[str, dict[str, Any]]:
    return {r["map"]: r for r in p["metrics"]["effort"]["rows"] if r["part"] == part}


def line(d: power.PowerDesign, fuel: str, kind: str = "single_block") -> dict[str, Any]:
    return next(
        p
        for p in d.options
        if p["power"]["fuel"]["id"] == fuel and p["power"]["generator"]["kind"] == kind
    )


def check_row(
    got: Mapping[str, Any],
    recipes: Mapping[str, tuple[int, float]],
    machines: int,
    mte: int | None,
) -> None:
    """One breakdown row (a map's steps) against its recipes' expected (crafts, machine-
    seconds): the row is their sum, each spread over ``machines``, and each recipe is its own
    entry with its own crafts and hours (the card's one row per recipe, D237)."""
    assert got["machines"] == machines and got["mte"] == mte
    assert got["crafts"] == sum(n for n, _ in recipes.values())
    seconds = math.fsum(s for _, s in recipes.values())
    assert got["h"] == pytest.approx(seconds / machines / 3600, rel=1e-4)
    each = {r["id"]: (r["crafts"], r["h"]) for r in got["recipes"]}
    assert set(each) == set(recipes)
    for ref, (n, s) in recipes.items():
        assert each[ref][0] == n
        assert each[ref][1] == pytest.approx(s / machines / 3600, rel=1e-4)


# --- worked example 1: the test-gas line, every map owned --------------------------------------


OWNED_ALL = {MIXER: 3, REACTOR: 2, ELECTROLYZER: 1, ASSEMBLER: 1, GENERATOR: 1}


def test_worked_example_1_gas_line_build_only(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    """§8 row 4.3, line 1: the generator on test gas, its chain the acid/gas loop. My base owns
    a machine on every map the components need (3 mixers, 2 reactors), so unlock_h is 0 and the
    build hours are spread over them."""
    p = line(design(conn, no_farms, OWNED_ALL), tg.GAS)
    n = counts(p)
    # The line: 1 electrolyzer (acid -> gas), 1 chemical reactor (gas + water -> acid), and
    # the generator; no mixer and no assembler is built.
    assert n == {ELECTROLYZER: 1, REACTOR: 1, GENERATOR: 1}
    comps = {c["id"]: c["qty"] for c in p["metrics"]["effort"]["components"]}
    assert comps == {f"{MACHINE}@{m}": q for m, q in n.items()}

    # Step 1: each component's recipe, read from the DB.
    gen = making(conn, f"{MACHINE}@{GENERATOR}")  # assembler/1: 2 Plate A, 300 ticks
    el = making(
        conn, f"{MACHINE}@{ELECTROLYZER}"
    )  # assembler/0: 4 Plate A + Dust B, 200
    re = making(conn, f"{MACHINE}@{REACTOR}")  # NEI shaped: 8 Plate A, a hand step
    assert gen.category == el.category == "gt.recipe.assembler"
    assert re.ref.startswith("r:codechicken.nei.recipe.ShapedRecipeHandler/")
    assert (
        gen.ins == {PLATE: 2}
        and el.ins == {PLATE: 4, DUST_B: 1}
        and re.ins == {PLATE: 8}
    )
    # Step 2: what they take: Plate A from mixer/2 (1 Dust A each), Dust B from mixer/1
    # (a log, raw), Dust A from chemicalreactor/1 (2 per craft, from raw ore and water).
    plate, dust_b, dust_a = (
        making(conn, PLATE),
        making(conn, DUST_B),
        making(conn, DUST_A),
    )
    assert plate.ins == {DUST_A: 1} and plate.out == 1 and dust_b.out == 1
    assert dust_a.out == 2 and dust_a.category == "gt.recipe.chemicalreactor"
    plates = (
        gen.ins[PLATE] * n[GENERATOR]
        + el.ins[PLATE] * n[ELECTROLYZER]
        + re.ins[PLATE] * n[REACTOR]
    )  # 2 + 4 + 8 = 14
    assert plates == 14
    dust_b_crafts = el.ins[DUST_B] * n[ELECTROLYZER]  # 1
    dust_a_crafts = math.ceil(plates * plate.ins[DUST_A] / dust_a.out)  # 14 / 2 = 7
    # Step 3: machine-seconds per craft (LV machines, no overclock) and hours, each map's
    # seconds spread over the machines My base owns on it.
    s_gen, s_el = no_overclock(conn, gen, ASSEMBLER), no_overclock(conn, el, ASSEMBLER)
    s_plate, s_dust_b = (
        no_overclock(conn, plate, MIXER),
        no_overclock(conn, dust_b, MIXER),
    )
    s_dust_a = no_overclock(conn, dust_a, REACTOR)
    assert (s_gen, s_el, s_plate, s_dust_b, s_dust_a) == (15.0, 10.0, 3.0, 3.0, 5.0)
    build = rows_of(p, "build")
    assert set(build) == {
        "gt.recipe.assembler",
        "gt.recipe.mixer",
        "gt.recipe.chemicalreactor",
        effort.HAND,
    }
    check_row(
        build["gt.recipe.assembler"],
        {gen.ref: (1, s_gen), el.ref: (1, s_el)},
        1,
        ASSEMBLER,
    )
    check_row(
        build["gt.recipe.mixer"],  # 45 s over 3 mixers
        {
            plate.ref: (plates, plates * s_plate),
            dust_b.ref: (dust_b_crafts, dust_b_crafts * s_dust_b),
        },
        OWNED_ALL[MIXER],
        MIXER,
    )
    check_row(
        build["gt.recipe.chemicalreactor"],  # 35 s over 2 reactors
        {dust_a.ref: (dust_a_crafts, dust_a_crafts * s_dust_a)},
        OWNED_ALL[REACTOR],
        REACTOR,
    )
    check_row(build[effort.HAND], {re.ref: (1, effort.CRAFT_S)}, 1, None)  # 2 s
    m = p["metrics"]
    # 25 s + 45/3 s + 35/2 s + 2 s = 59.5 s
    assert m["build_h"] == pytest.approx(59.5 / 3600, rel=1e-4)
    assert m["unlock_h"] == 0 and m["effort"]["unlocks"] == []
    assert m["eff_h"] == pytest.approx(m["build_h"], rel=1e-4)
    assert m["effort"]["uncounted"] == 0


# --- worked example 2: the gas turbine line, its rotor and dynamo hatch, unlocks ---------------


OWNED_FEW = {REACTOR: 1, GENERATOR: 1}


def test_worked_example_2_turbine_line_with_rotor_hatch_and_unlocks(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    """§8 row 4.3, line 2: the large gas turbine on test gas with a Testium large rotor, its
    chain the acid/gas loop. The parts WO-4.3 adds are counted: the rotor (GT's turbine rotor
    item, its material in the NBT) and the LV dynamo hatch the line sized it on, each made by
    its assembler recipe. My base owns a chemical reactor and the combustion generator only:
    the assembler and mixer maps the steps need are unlocked (their machines made once)."""
    p = line(design(conn, no_farms, OWNED_FEW), tg.GAS, "gas_turbine")
    n = counts(p)
    # The line: 1 electrolyzer, 1 chemical reactor and the turbine, sized on an LV dynamo
    # (its output fits 1 A at LV, so the smallest hatch that doesn't cap it is LV).
    assert n == {ELECTROLYZER: 1, REACTOR: 1, TURBINE: 1}
    spec = next(x for x in p["nodes"] if x.get("kind") == "generator")["spec"]
    assert spec["rotor"] == ["Testium", "large"] and spec["dynamo_tier"] == LV
    rotor_key = power.rotor_key(conn, 1, "Testium", "large")
    assert rotor_key is not None and rotor_key.startswith(
        f"{power.ROTOR_ITEM}@{ROTOR_META}#"
    )
    hatch_key = f"{MACHINE}@{DYNAMO_LV}"
    comps = {
        (c["id"], c["part"]): c["qty"] for c in p["metrics"]["effort"]["components"]
    }
    assert comps == {
        (f"{MACHINE}@{ELECTROLYZER}", "machine"): 1,
        (f"{MACHINE}@{REACTOR}", "machine"): 1,
        (f"{MACHINE}@{TURBINE}", "machine"): 1,
        (rotor_key, "rotor"): 1,
        (hatch_key, "hatch"): 1,
    }

    # Step 1: each component's recipe, read from the DB (the rotor by its stable key).
    el = making(
        conn, f"{MACHINE}@{ELECTROLYZER}"
    )  # assembler/0: 4 Plate A + Dust B, 200
    re = making(conn, f"{MACHINE}@{REACTOR}")  # NEI shaped: 8 Plate A
    tb = making(conn, f"{MACHINE}@{TURBINE}")  # assembler/4: 6 Plate A, 500 ticks
    rotor = making(
        conn, f"{power.ROTOR_ITEM}@{ROTOR_META}"
    )  # assembler/3: 4 Plate A, 400
    hatch = making(conn, hatch_key)  # assembler/2: 1 Plate A, 100 ticks
    assert {el.category, tb.category, rotor.category, hatch.category} == {
        "gt.recipe.assembler"
    }
    assert tb.ins == {PLATE: 6} and rotor.ins == {PLATE: 4} and hatch.ins == {PLATE: 1}
    plate, dust_b, dust_a = (
        making(conn, PLATE),
        making(conn, DUST_B),
        making(conn, DUST_A),
    )
    # Step 2: Plate A for every component, then Dust A (2 per craft) for the plates.
    plates = (
        el.ins[PLATE]
        + re.ins[PLATE]
        + tb.ins[PLATE]
        + rotor.ins[PLATE]
        + hatch.ins[PLATE]
    )  # 4 + 8 + 6 + 4 + 1 = 23
    assert plates == 23
    dust_a_crafts = math.ceil(plates * plate.ins[DUST_A] / dust_a.out)  # 12
    s = {r.ref: no_overclock(conn, r, ASSEMBLER) for r in (el, tb, rotor, hatch)}
    assert sorted(s.values()) == [5.0, 10.0, 20.0, 25.0]
    s_plate, s_dust_b = (
        no_overclock(conn, plate, MIXER),
        no_overclock(conn, dust_b, MIXER),
    )
    s_dust_a = no_overclock(conn, dust_a, REACTOR)
    # Step 3: the build rows. No assembler or mixer is owned: each map counts as 1 machine;
    # the reactor's steps spread over the 1 he owns.
    build = rows_of(p, "build")
    check_row(
        build["gt.recipe.assembler"],  # 10 + 25 + 20 + 5 = 60 s
        {ref: (1, sec) for ref, sec in s.items()},
        1,
        ASSEMBLER,
    )
    check_row(
        build["gt.recipe.mixer"],  # 23 x 3 + 3 = 72 s
        {plate.ref: (plates, plates * s_plate), dust_b.ref: (1, s_dust_b)},
        1,
        MIXER,
    )
    check_row(
        build["gt.recipe.chemicalreactor"],  # 12 x 5 = 60 s
        {dust_a.ref: (dust_a_crafts, dust_a_crafts * s_dust_a)},
        OWNED_FEW[REACTOR],
        REACTOR,
    )
    check_row(build[effort.HAND], {re.ref: (1, effort.CRAFT_S)}, 1, None)  # 2 s
    build_s = 60 + 72 + 60 + 2
    assert build_s == (
        math.fsum(s.values())
        + plates * s_plate
        + s_dust_b
        + dust_a_crafts * s_dust_a
        + effort.CRAFT_S
    )
    # Step 4: unlocks. The assembler and mixer maps are needed, owned by none of his machines
    # and not built by the line, so the assembler (shaped: 8 Plate A) and the mixer (shaped:
    # 8 Dust A) are made once, on maps then covered.
    m = p["metrics"]
    assert m["effort"]["unlocks"] == [f"mte:{ASSEMBLER}", f"mte:{MIXER}"]
    asm, mix = (
        making(conn, f"{MACHINE}@{ASSEMBLER}"),
        making(conn, f"{MACHINE}@{MIXER}"),
    )
    assert asm.ins == {PLATE: 8} and mix.ins == {DUST_A: 8}
    u_plates = asm.ins[PLATE]  # 8
    u_dust_a = math.ceil((mix.ins[DUST_A] + u_plates * plate.ins[DUST_A]) / dust_a.out)
    assert u_dust_a == 8  # (8 + 8) / 2
    unlock = rows_of(p, "unlock")
    assert set(unlock) == {"gt.recipe.mixer", "gt.recipe.chemicalreactor", effort.HAND}
    check_row(
        unlock[effort.HAND],
        {asm.ref: (1, effort.CRAFT_S), mix.ref: (1, effort.CRAFT_S)},
        1,
        None,
    )
    check_row(
        unlock["gt.recipe.mixer"], {plate.ref: (u_plates, u_plates * s_plate)}, 1, MIXER
    )
    check_row(
        unlock["gt.recipe.chemicalreactor"],
        {dust_a.ref: (u_dust_a, u_dust_a * s_dust_a)},
        1,
        REACTOR,
    )
    unlock_s = 2 * effort.CRAFT_S + u_plates * s_plate + u_dust_a * s_dust_a
    assert unlock_s == 4 + 24 + 40
    assert m["build_h"] == pytest.approx(build_s / 3600, rel=1e-4)
    assert m["unlock_h"] == pytest.approx(unlock_s / 3600, rel=1e-4)
    assert m["eff_h"] == pytest.approx((build_s + unlock_s) / 3600, rel=1e-4)
    # The turbine has no structure data in this DB: its casings are not counted, and the
    # effort says so outside its notes (M2): 1 item not counted.
    assert m["effort"]["uncounted"] == 1
    assert f"mte:{TURBINE}: no structure data" in " ".join(m["effort"]["notes"])
    assert "1 items not counted" in effort.summary(m)


# --- line 3: the test-acid line, an assembler to unlock ----------------------------------------


def test_acid_line_with_an_unlock(conn: sqlite3.Connection, no_farms: Path) -> None:
    """The generator on test acid, its chain Dust B (logs) -> gas -> acid. My base owns a
    chemical reactor and the generator only: the generator's item needs an assembler, a machine
    type he doesn't own and the line doesn't build, so its components are unlock_h (depth 1;
    its own steps run on maps he owns or the line builds)."""
    p = line(design(conn, no_farms, OWNED_FEW), tg.ACID)
    n = counts(p)
    # The line: 2 mixers (logs -> Dust B, Dust B -> gas), 1 reactor (gas + water -> acid),
    # the generator.
    assert n == {MIXER: 2, REACTOR: 1, GENERATOR: 1}
    gen = making(conn, f"{MACHINE}@{GENERATOR}")  # assembler/1
    mixer = making(conn, f"{MACHINE}@{MIXER}")  # NEI shaped: 8 Dust A
    re = making(conn, f"{MACHINE}@{REACTOR}")  # NEI shaped: 8 Plate A
    assembler = making(conn, f"{MACHINE}@{ASSEMBLER}")  # NEI shaped: 8 Plate A
    plate, dust_a = making(conn, PLATE), making(conn, DUST_A)
    assert mixer.ins == {DUST_A: 8} and assembler.ins == {PLATE: 8}

    # Build: the components' steps.
    plates = gen.ins[PLATE] * n[GENERATOR] + re.ins[PLATE] * n[REACTOR]  # 2 + 8 = 10
    dust = mixer.ins[DUST_A] * n[MIXER] + plates * plate.ins[DUST_A]  # 16 + 10 = 26
    dust_a_crafts = math.ceil(dust / dust_a.out)  # 13
    s_gen = no_overclock(conn, gen, ASSEMBLER)
    s_plate = no_overclock(conn, plate, MIXER)
    s_dust_a = no_overclock(conn, dust_a, REACTOR)
    build = rows_of(p, "build")
    # Spread: 1 reactor owned; no mixer and no assembler owned: at least 1 (plan 7.2).
    check_row(build["gt.recipe.assembler"], {gen.ref: (1, s_gen)}, 1, ASSEMBLER)  # 15 s
    check_row(
        build["gt.recipe.mixer"], {plate.ref: (plates, plates * s_plate)}, 1, MIXER
    )
    check_row(
        build["gt.recipe.chemicalreactor"],  # 65 s
        {dust_a.ref: (dust_a_crafts, dust_a_crafts * s_dust_a)},
        OWNED_FEW[REACTOR],
        REACTOR,
    )
    check_row(
        build[effort.HAND],
        {mixer.ref: (n[MIXER], n[MIXER] * effort.CRAFT_S), re.ref: (1, effort.CRAFT_S)},
        1,
        None,
    )
    hand = n[MIXER] + n[REACTOR]  # 3 shaped crafts
    build_s = (
        s_gen + plates * s_plate + dust_a_crafts * s_dust_a + hand * effort.CRAFT_S
    )
    assert build_s == 15 + 30 + 65 + 6

    # Unlock: the assembler map is the only one he owns no machine on that the line doesn't
    # build (mixers and the reactor are built by the line), so the assembler is made once.
    m = p["metrics"]
    assert m["effort"]["unlocks"] == [f"mte:{ASSEMBLER}"]
    u_plates = assembler.ins[PLATE]  # 8
    u_dust_a_crafts = math.ceil(u_plates * plate.ins[DUST_A] / dust_a.out)  # 4
    unlock = rows_of(p, "unlock")
    assert set(unlock) == {"gt.recipe.mixer", "gt.recipe.chemicalreactor", effort.HAND}
    check_row(unlock[effort.HAND], {assembler.ref: (1, effort.CRAFT_S)}, 1, None)
    check_row(
        unlock["gt.recipe.mixer"], {plate.ref: (u_plates, u_plates * s_plate)}, 1, MIXER
    )
    check_row(
        unlock["gt.recipe.chemicalreactor"],
        {dust_a.ref: (u_dust_a_crafts, u_dust_a_crafts * s_dust_a)},
        1,
        REACTOR,
    )
    unlock_s = effort.CRAFT_S + u_plates * s_plate + u_dust_a_crafts * s_dust_a
    assert unlock_s == 2 + 24 + 20
    assert m["build_h"] == pytest.approx(build_s / 3600, rel=1e-4)
    assert m["unlock_h"] == pytest.approx(unlock_s / 3600, rel=1e-4)
    assert m["eff_h"] == pytest.approx((build_s + unlock_s) / 3600, rel=1e-4)


def test_output_per_effort_ranks_by_net_eut_per_effort_hour(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    """The mode's order is planner/rank.py's: complete efforts first (an effort that leaves
    items out is a lower bound, D236), each group by net EU/t / eff_h, highest first; the ratio
    is printed with each option."""
    d = design(conn, no_farms, OWNED_FEW)
    assert len(d.options) == 3 and d.weights.startswith("mode output_per_effort")
    keys = []
    for p in d.options:
        ratio = p["power"]["net_eut"] / p["metrics"]["eff_h"]
        assert p["rank"]["eut_per_effort_h"] == pytest.approx(ratio, rel=1e-3)
        keys.append((p["metrics"]["effort"]["uncounted"] > 0, -ratio))
    assert keys == sorted(keys)
    # The turbine line leaves its casings out, so it ranks after both single blocks, with a
    # note.
    last = d.options[-1]
    assert last["power"]["generator"]["kind"] == "gas_turbine"
    assert any("ranked after the lines whose effort is complete" in x
               for x in last["rank"]["notes"])  # fmt: skip
    # With a crafting-table step of 60 s instead of 2 s (me/prefs.md's craft_s), the hand
    # steps weigh more and the effort changes accordingly.
    slow = design(conn, no_farms, OWNED_FEW, rank.parse_prefs("craft_s: 60\n"))
    for p in slow.options:
        fast = next(q for q in d.options if q["id"] == p["id"])
        hand = sum(
            r["crafts"]
            for r in p["metrics"]["effort"]["rows"]
            if r["map"] == effort.HAND
        )
        assert p["metrics"]["eff_h"] == pytest.approx(
            fast["metrics"]["eff_h"] + hand * 58 / 3600, rel=1e-3
        )


def test_an_incomplete_effort_ranks_after_complete_ones() -> None:
    """D236: a line whose effort leaves items out ranks after the complete ones in
    output_per_effort, even with the best ratio; effort 0 stays last (D235)."""

    def plan(pid: str, net: float, eff: float, uncounted: int) -> dict[str, Any]:
        return {
            "id": pid,
            "metrics": {
                "net_eut": net, "eff_h": eff, "upkeep": 0.0, "scarcity": 0.0,
                "complexity": {"score": 3}, "new_types": [], "tier": "MV",
                "effort": {"uncounted": uncounted},
            },
        }  # fmt: skip

    plans = [plan("a", 100, 1, 3), plan("b", 50, 1, 0), plan("c", 10, 1, 0),
             plan("z", 500, 0, 0)]  # fmt: skip
    got = rank.order_power(plans, "output_per_effort")
    assert [p["id"] for p in got] == ["b", "c", "a", "z"]
    assert any("leaves 3 items out" in n for n in got[2]["rank"]["notes"])
    assert not any("leaves" in n for n in got[0]["rank"]["notes"])
    # Other modes don't look at it.
    assert rank.order_power(plans, "max_output")[0]["id"] == "z"


# --- a power line's own parts (D236) -----------------------------------------------------------


def _turbine_node(spec: dict[str, Any], count: int = 2) -> dict[str, Any]:
    return {
        "n": "n9",
        "recipe": "gen:gas_turbine/test",
        "kind": "generator",
        "count": count,
        "machine": {"mte": TURBINE, "kind": "multiblock"},
        "spec": spec,
    }


def test_parts_count_the_dynamo_hatch_and_list_what_isnt_counted(
    conn: sqlite3.Connection,
) -> None:
    node = _turbine_node(
        {
            "kind": "gas_turbine",
            "mte": TURBINE,
            "rotor": ["Steel", "large"],
            "dynamo_tier": LV,
        }
    )
    out, notes, missing = power.parts(conn, {"nodes": [node]}, 1)
    # The dynamo hatch at the line's tier: GT's dynamo-hatch class at LV, its item, one per
    # generator block built. This DB has no Steel rotor: listed and counted as missing.
    assert out == [effort.Component(f"{MACHINE}@{DYNAMO_LV}", 2, TURBINE, "hatch")]
    assert missing == 1
    assert any(n.startswith("rotor Steel large: no such item") for n in notes)
    assert any("maintenance, input, output, muffler" in n for n in notes)
    assert any("cables" in n and "WO-4.5" in n for n in notes)
    # The Testium rotor is there (its material from the item's NBT).
    node["spec"]["rotor"] = ["Testium", "large"]
    out, _, missing = power.parts(conn, {"nodes": [node]}, 1)
    assert [c.part for c in out] == ["rotor", "hatch"] and missing == 0
    # A dynamo tier with no hatch in the DB is listed, not guessed.
    node["spec"]["dynamo_tier"] = 5
    out, notes, missing = power.parts(conn, {"nodes": [node]}, 1)
    assert [c.part for c in out] == ["rotor"] and missing == 1
    assert any(n.startswith("dynamo hatch IV: no such") for n in notes)
    # A single block has no parts; a plan without a generator gets no cables note (L4).
    single = {"kind": "single_block", "mte": GENERATOR, "fuel_ref": "r:x"}
    out, notes, missing = power.parts(conn, {"nodes": [_turbine_node(single, 1)]}, 1)
    assert out == [] and missing == 0 and not any("maintenance" in n for n in notes)
    assert power.parts(conn, {"nodes": []}, 1) == ([], [], 0)


def test_parts_are_made_like_any_component(conn: sqlite3.Connection) -> None:
    """The dynamo hatch's and the rotor's assembler recipes and their plates are counted in
    the build rows, one per generator block, and the parts are listed with the line's
    components."""
    node = _turbine_node(
        {
            "kind": "gas_turbine",
            "mte": TURBINE,
            "rotor": ["Testium", "large"],
            "dynamo_tier": LV,
        },
        3,
    )
    extra, notes, missing = power.parts(conn, {"nodes": [node]}, 1)
    measure = effort.Effort(
        conn, Request("fluid:water", tier_cap=2), tg.Base(), 2, templates=chains.EMPTY
    )
    eff = measure.route({"nodes": []}, extra, notes, missing)
    hatch = making(conn, f"{MACHINE}@{DYNAMO_LV}")
    rotor = making(conn, f"{power.ROTOR_ITEM}@{ROTOR_META}")
    build = {r["map"]: r for r in eff["rows"]}
    check_row(
        build["gt.recipe.assembler"],
        {
            hatch.ref: (3, 3 * no_overclock(conn, hatch, ASSEMBLER)),
            rotor.ref: (3, 3 * no_overclock(conn, rotor, ASSEMBLER)),
        },
        1,
        ASSEMBLER,
    )
    assert build["gt.recipe.mixer"]["crafts"] == 3 * (
        hatch.ins[PLATE] + rotor.ins[PLATE]
    )
    assert {"id": f"{MACHINE}@{DYNAMO_LV}", "qty": 3, "for": f"mte:{TURBINE}",
            "part": "hatch"} in eff["components"]  # fmt: skip
    assert any("maintenance" in n for n in eff["notes"])
    assert eff["uncounted"] == 0


def test_rotor_key_reads_the_material_from_the_items_nbt(
    db: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "rotor.sqlite"
    shutil.copyfile(db, copy)
    c = sqlite3.connect(copy)
    try:
        for h, primary, secondary in (
            ("ff" * 32, "Steel", "Iron"),
            ("ee" * 32, "Steel", "Steel"),
            ("aa" * 32, "Titanium", "Titanium"),
        ):
            nbt = json.dumps(
                {
                    "GT.ToolStats": {
                        "PrimaryMaterial": primary,
                        "SecondaryMaterial": secondary,
                    }
                }
            )
            c.execute(
                "INSERT INTO item (registry_name, meta, nbt_hash, nbt_json, name_en)"
                " VALUES (?, 174, ?, ?, 'Large Turbine')",
                (power.ROTOR_ITEM, h, nbt),
            )
        c.execute(
            "INSERT INTO item (registry_name, meta, nbt_hash, nbt_json, name_en)"
            " VALUES (?, 174, 'bb', 'not json', 'Large Turbine')",
            (power.ROTOR_ITEM,),
        )
        c.commit()
        # Steel/Steel before Steel/Iron; a malformed NBT row is skipped, never an error.
        assert (
            power.rotor_key(c, 1, "Steel", "large")
            == f"{power.ROTOR_ITEM}@174#eeeeeeee"
        )
        assert power.rotor_key(c, 1, "Titanium", "large") == (
            f"{power.ROTOR_ITEM}@174#aaaaaaaa"
        )
        assert power.rotor_key(c, 1, "Steel", "huge") is None
        assert power.rotor_key(c, 1, "Osmium", "large") is None
    finally:
        c.close()


# --- the breakdown: table card, text, JSON ------------------------------------------------------


CITE = re.compile(
    r"^(r|q|mte|crop|plan|w):\S+$"
)  # the dashboard's chip test (renderers.tsx)


def test_breakdown_card_is_a_catalog_table_card(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    d = design(conn, no_farms, OWNED_FEW)
    p = line(d, tg.GAS, "gas_turbine")
    card = cards.effort_card(p)
    assert validate(card) == []
    assert card["type"] == "table" and card["tool"] == "effort_estimate"
    assert card["columns"] == list(effort.COLUMNS)
    rows = effort.table(p["metrics"]["effort"])
    assert card["rows"] == rows and card["total"] == len(rows) and card["span"] is None
    # One row per recipe: as many as the breakdown has recipes.
    assert len(rows) == sum(len(r["recipes"]) for r in p["metrics"]["effort"]["rows"])
    head = {h["key"]: h["value"] for h in card["head"]}
    assert head["plan"] == f"plan:{p['id']}"
    assert [head["unlocks 1"], head["unlocks 2"]] == [
        f"mte:{ASSEMBLER}",
        f"mte:{MIXER}",
    ]
    assert head["effort"] == effort.duration(p["metrics"]["eff_h"])
    assert head["not counted"] == "1 items (see the notes)"
    # Every citation cell is one id, so the dashboard draws it as a chip (M1): the machine
    # (mte:, or "-" for hand steps) and the recipe (r:); unlocks in the head too.
    for row in card["rows"]:
        assert row[2] == "-" or CITE.match(row[2])
        assert CITE.match(row[3]) and row[3].startswith("r:")
    assert all(CITE.match(head[k]) for k in head if k.startswith("unlocks"))
    assert card["notes"] == p["metrics"]["effort"]["notes"]


def _big(rows: int, notes: int, unlocks: int) -> dict[str, Any]:
    row = {"part": "build", "map": "m", "mte": 1, "crafts": 1, "machines": 1, "h": 0.5,
           "recipes": [{"id": "r:gt.recipe.m/0", "crafts": 1, "h": 0.5}]}  # fmt: skip
    return {
        "id": "pw-0",
        "name": "Big",
        "metrics": {
            "eff_h": 30.0,
            "build_h": 30.0,
            "unlock_h": 0.0,
            "effort": {
                "craft_s": 2.0,
                "rows": [row] * rows,
                "components": [],
                "unlocks": [f"mte:{i}" for i in range(unlocks)],
                "uncounted": 0,
                "notes": [f"note {i}" for i in range(notes)],
            },
        },
    }


def test_breakdown_card_clips_rows_notes_and_head() -> None:
    card = cards.effort_card(_big(60, 25, 0))
    assert validate(card) == []
    assert (
        len(card["rows"]) == 50 and card["total"] == 60 and card["span"] == "1-50 of 60"
    )
    assert len(card["notes"]) == 20 and card["notes"][0] == "the first 50 of 60 rows"
    assert card["notes"][-1] == "7 more notes"
    # Unlocks past the head's 30 pairs go to a note.
    card = cards.effort_card(_big(1, 0, 40))
    assert validate(card) == [] and len(card["head"]) == 30
    assert card["head"][-1] == {"key": "unlocks 23", "value": "mte:22"}
    assert card["notes"][0].startswith("more unlocked machines: mte:23, ")


def test_durations_read_as_seconds_minutes_or_hours() -> None:
    assert effort.duration(1 / 72000) == "0.05 s"
    assert effort.duration(59.5 / 3600) == "59.5 s"
    assert effort.duration(0.5) == "30.0 min"
    assert effort.duration(1234.5) == "1,234.50 h"


def test_text_prints_the_breakdown_table() -> None:
    m = {
        "eff_h": 0.5,
        "build_h": 0.25,
        "unlock_h": 0.25,
        "effort": {
            "craft_s": 2.0,
            "rows": [
                {
                    "part": "build",
                    "map": "gt.recipe.mixer",
                    "mte": 101,
                    "crafts": 10,
                    "machines": 2,
                    "h": 0.25,
                    "recipes": [
                        {"id": "r:gt.recipe.mixer/a", "crafts": 4, "h": 0.1},
                        {"id": "r:gt.recipe.mixer/b", "crafts": 6, "h": 0.15},
                    ],
                },
                {
                    "part": "unlock",
                    "map": effort.HAND,
                    "mte": None,
                    "crafts": 1,
                    "machines": 1,
                    "h": 0.25,
                },  # a plan saved before WO-4.3: the map's totals, no recipe
            ],
            "components": [],
            "unlocks": ["mte:107"],
            "uncounted": 2,
            "notes": ["a note"],
        },
    }
    lines = effort.text(m)
    assert lines[0] == (
        "  effort 30.0 min = build 15.0 min + unlock 15.0 min (crafting-table step 2 s;"
        " 2 items not counted, see the notes)"
    )
    assert lines[1].split() == list(effort.COLUMNS)
    assert lines[2].split() == ["build", "gt.recipe.mixer", "mte:101",
                                "r:gt.recipe.mixer/a", "4", "2", "6.0", "min"]  # fmt: skip
    assert lines[3].split() == ["build", "gt.recipe.mixer", "mte:101",
                                "r:gt.recipe.mixer/b", "6", "2", "9.0", "min"]  # fmt: skip
    assert lines[4].split() == ["unlock", "crafting", "-", "-", "1", "1", "15.0", "min"]
    assert lines[5:] == ["  unlocks: mte:107", "  effort note: a note"]
    m["effort"]["uncounted"] = 0
    assert effort.summary(m).endswith("(crafting-table step 2 s)")


def test_cli_prints_the_breakdown_and_json_carries_it(db: Path) -> None:
    home = db.parent.parent
    code, text = tpw.run_cli(home, "design", "output_per_effort", "--tier", "MV",
                             "--free", "fluid:water")  # fmt: skip
    assert code == 0, text
    assert text.startswith("mode output_per_effort")
    assert "   effort " in text and " = build " in text
    assert "   part" in text and "gt.recipe.assembler" in text
    # The effort is printed once per option, on its own line (L2).
    scores = [x for x in text.splitlines() if x.startswith("   score ")]
    assert scores and text.count(" = build ") == len(scores)
    assert not any("effort" in x for x in scores)
    code, text = tpw.run_cli(home, "design", "output_per_effort", "--tier", "MV",
                             "--free", "fluid:water", "--json")  # fmt: skip
    assert code == 0
    for raw in text.splitlines():
        p = json.loads(raw)
        rows = p["metrics"]["effort"]["rows"]
        assert rows and all({"part", "map", "mte", "crafts", "machines", "h",
                             "recipes"} <= set(r) for r in rows)  # fmt: skip
        assert all({"id", "crafts", "h"} == set(x) for r in rows for x in r["recipes"])
        assert isinstance(p["metrics"]["effort"]["uncounted"], int)
        assert p["metrics"]["eff_h"] > 0
