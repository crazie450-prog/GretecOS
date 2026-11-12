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
from gregtec.planner import chains, effort, power, rank
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
        return [*out, assembler, dynamo]

    shaped = [
        {"handler": SHAPED, "ingredients": [_stack(2)] * 8,
         "results": [_stack(REACTOR, MACHINE)]},
        {"handler": SHAPED, "ingredients": [_stack(2)] * 8,
         "results": [_stack(ASSEMBLER, MACHINE)]},
        {"handler": SHAPED, "ingredients": [_stack(3)] * 8,
         "results": [_stack(MIXER, MACHINE)]},
    ]  # fmt: skip
    nei = {"nei/Shaped Crafting.jsonl": tg.jsonl(shaped)}
    tg.recipes, tg.machines, tg.MAPS = more_recipes, more_machines, (*maps, "assembler")
    tg.make_home = lambda h: make(h, nei)
    try:
        return Path(tpw.make_home(home))
    finally:
        tg.recipes, tg.machines, tg.MAPS, tg.make_home = recipes, machines, maps, make


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


def line(d: power.PowerDesign, fuel: str) -> dict[str, Any]:
    return next(p for p in d.options if p["power"]["fuel"]["id"] == fuel)


def check_row(
    got: Mapping[str, Any],
    crafts: int,
    seconds: float,
    machines: int,
    refs: set[str],
    mte: int | None,
) -> None:
    assert got["crafts"] == crafts
    assert got["machines"] == machines
    assert got["h"] == pytest.approx(seconds / machines / 3600, rel=1e-4)
    assert set(got["recipes"]) == refs
    assert got["mte"] == mte


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
        build["gt.recipe.assembler"], 2, s_gen + s_el, 1, {gen.ref, el.ref}, ASSEMBLER
    )
    check_row(
        build["gt.recipe.mixer"],
        plates + dust_b_crafts,  # 15
        plates * s_plate + dust_b_crafts * s_dust_b,  # 45 s over 3 mixers
        OWNED_ALL[MIXER],
        {plate.ref, dust_b.ref},
        MIXER,
    )
    check_row(
        build["gt.recipe.chemicalreactor"],
        dust_a_crafts,
        dust_a_crafts * s_dust_a,  # 35 s over 2 reactors
        OWNED_ALL[REACTOR],
        {dust_a.ref},
        REACTOR,
    )
    check_row(build[effort.HAND], 1, effort.CRAFT_S, 1, {re.ref}, None)  # 2 s
    m = p["metrics"]
    # 25 s + 45/3 s + 35/2 s + 2 s = 59.5 s
    assert m["build_h"] == pytest.approx(59.5 / 3600, rel=1e-4)
    assert m["unlock_h"] == 0 and m["effort"]["unlocks"] == []
    assert m["eff_h"] == pytest.approx(m["build_h"], rel=1e-4)


# --- worked example 2: the test-acid line, an assembler to unlock ------------------------------


OWNED_FEW = {REACTOR: 1, GENERATOR: 1}


def test_worked_example_2_acid_line_with_an_unlock(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    """§8 row 4.3, line 2: the generator on test acid, its chain Dust B (logs) -> gas -> acid.
    My base owns a chemical reactor and the generator only: the generator's item needs an
    assembler, a machine type he doesn't own and the line doesn't build, so its components
    are unlock_h (depth 1; its own steps run on maps he owns or the line builds)."""
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
    hand = n[MIXER] + n[REACTOR]  # 3 shaped crafts
    s_gen = no_overclock(conn, gen, ASSEMBLER)
    s_plate = no_overclock(conn, plate, MIXER)
    s_dust_a = no_overclock(conn, dust_a, REACTOR)
    build = rows_of(p, "build")
    # Spread: 1 reactor owned; no mixer and no assembler owned: at least 1 (plan 7.2).
    check_row(build["gt.recipe.assembler"], 1, s_gen, 1, {gen.ref}, ASSEMBLER)  # 15 s
    check_row(build["gt.recipe.mixer"], plates, plates * s_plate, 1, {plate.ref}, MIXER)
    check_row(
        build["gt.recipe.chemicalreactor"],
        dust_a_crafts,
        dust_a_crafts * s_dust_a,  # 65 s
        OWNED_FEW[REACTOR],
        {dust_a.ref},
        REACTOR,
    )
    check_row(
        build[effort.HAND], hand, hand * effort.CRAFT_S, 1, {mixer.ref, re.ref}, None
    )
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
    check_row(unlock[effort.HAND], 1, effort.CRAFT_S, 1, {assembler.ref}, None)
    check_row(
        unlock["gt.recipe.mixer"], u_plates, u_plates * s_plate, 1, {plate.ref}, MIXER
    )
    check_row(
        unlock["gt.recipe.chemicalreactor"],
        u_dust_a_crafts,
        u_dust_a_crafts * s_dust_a,
        1,
        {dust_a.ref},
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
    """The mode's order is planner/rank.py's: net EU/t / eff_h, highest first; the ratio is
    printed with each option."""
    d = design(conn, no_farms, OWNED_FEW)
    assert len(d.options) == 2 and d.weights.startswith("mode output_per_effort")
    ratios = []
    for p in d.options:
        ratio = p["power"]["net_eut"] / p["metrics"]["eff_h"]
        assert p["rank"]["eut_per_effort_h"] == pytest.approx(ratio, rel=1e-3)
        ratios.append(ratio)
    assert ratios == sorted(ratios, reverse=True)
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


# --- a power line's own parts (D236) -----------------------------------------------------------


def _turbine_node(spec: dict[str, Any], count: int = 2) -> dict[str, Any]:
    return {
        "n": "n9",
        "recipe": "gen:gas_turbine/test",
        "kind": "generator",
        "count": count,
        "machine": {"mte": 1131, "kind": "multiblock"},
        "spec": spec,
    }


def test_parts_count_the_dynamo_hatch_and_list_what_isnt_counted(
    conn: sqlite3.Connection,
) -> None:
    node = _turbine_node(
        {
            "kind": "gas_turbine",
            "mte": 1131,
            "rotor": ["Steel", "large"],
            "dynamo_tier": LV,
        }
    )
    out, notes = power.parts(conn, {"nodes": [node]}, 1)
    # The dynamo hatch at the line's tier: GT's dynamo-hatch class at LV, its item, one per
    # generator block built.
    assert out == [effort.Component(f"{MACHINE}@{DYNAMO_LV}", 2, 1131, "hatch")]
    assert any(n.startswith("rotor Steel large: no such item") for n in notes)
    assert any("maintenance, input, output, muffler" in n for n in notes)
    assert any("cables" in n and "WO-4.5" in n for n in notes)
    # A dynamo tier with no hatch in the DB is listed, not guessed.
    node["spec"]["dynamo_tier"] = 5
    out, notes = power.parts(conn, {"nodes": [node]}, 1)
    assert out == [] and any(n.startswith("dynamo hatch IV: no such") for n in notes)
    # A single block has no parts.
    single = {"kind": "single_block", "mte": GENERATOR, "fuel_ref": "r:x"}
    out, notes = power.parts(conn, {"nodes": [_turbine_node(single, 1)]}, 1)
    assert out == [] and not any("maintenance" in n for n in notes)


def test_parts_are_made_like_any_component(conn: sqlite3.Connection) -> None:
    """The dynamo hatch's assembler recipe (1 Plate A, 100 ticks) and its plate are counted in
    the build rows, and the part is listed with the line's components."""
    node = _turbine_node({"kind": "gas_turbine", "mte": 1131, "dynamo_tier": LV}, 3)
    extra, notes = power.parts(conn, {"nodes": [node]}, 1)
    measure = effort.Effort(
        conn, Request("fluid:water", tier_cap=2), tg.Base(), 2, templates=chains.EMPTY
    )
    eff = measure.route({"nodes": []}, extra, notes)
    hatch = making(conn, f"{MACHINE}@{DYNAMO_LV}")
    build = {r["map"]: r for r in eff["rows"]}
    check_row(
        build["gt.recipe.assembler"],
        3,
        3 * no_overclock(conn, hatch, ASSEMBLER),
        1,
        {hatch.ref},
        ASSEMBLER,
    )
    assert build["gt.recipe.mixer"]["crafts"] == 3 * hatch.ins[PLATE]
    assert {"id": f"{MACHINE}@{DYNAMO_LV}", "qty": 3, "for": "mte:1131",
            "part": "hatch"} in eff["components"]  # fmt: skip
    assert any("maintenance" in n for n in eff["notes"])


def test_rotor_key_reads_the_material_from_the_items_nbt(
    db: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "rotor.sqlite"
    shutil.copyfile(db, copy)
    c = sqlite3.connect(copy)
    try:
        c.execute("INSERT INTO rotor_size VALUES (1, 174, 'large', 3.0, 5.0, 3.0)")
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


def test_breakdown_card_is_a_catalog_table_card(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    p = line(design(conn, no_farms, OWNED_FEW), tg.ACID)
    card = cards.effort_card(p)
    assert validate(card) == []
    assert card["type"] == "table" and card["tool"] == "effort_estimate"
    assert card["columns"] == list(effort.COLUMNS)
    assert card["rows"] == effort.table(p["metrics"]["effort"])
    assert card["total"] == len(p["metrics"]["effort"]["rows"]) and card["span"] is None
    head = {h["key"]: h["value"] for h in card["head"]}
    assert head["plan"] == f"plan:{p['id']}" and head["unlocks"] == f"mte:{ASSEMBLER}"
    assert head["effort"] == f"{p['metrics']['eff_h']:.4g} h"
    # Each row cites its machine (mte:) and recipes (r:), drawn as chips.
    for row in card["rows"]:
        assert row[2] == "-" or row[2].startswith("mte:")
        assert all(ref.startswith("r:") for ref in row[6].split())
    assert card["notes"] == p["metrics"]["effort"]["notes"]


def test_breakdown_card_clips_rows_and_notes() -> None:
    row = {"part": "build", "map": "m", "mte": 1, "crafts": 1, "machines": 1, "h": 0.5,
           "recipes": ["r:gt.recipe.m/0"]}  # fmt: skip
    p = {
        "id": "pw-0",
        "name": "Big",
        "metrics": {
            "eff_h": 30.0,
            "build_h": 30.0,
            "unlock_h": 0.0,
            "effort": {
                "craft_s": 2.0,
                "rows": [row] * 60,
                "components": [],
                "unlocks": [],
                "notes": [f"note {i}" for i in range(25)],
            },
        },
    }
    card = cards.effort_card(p)
    assert validate(card) == []
    assert (
        len(card["rows"]) == 50 and card["total"] == 60 and card["span"] == "1-50 of 60"
    )
    assert len(card["notes"]) == 20 and card["notes"][0] == "the first 50 of 60 rows"
    assert card["notes"][-1] == "7 more notes"


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
                    "recipes": ["r:gt.recipe.mixer/a"],
                },
                {
                    "part": "unlock",
                    "map": effort.HAND,
                    "mte": None,
                    "crafts": 1,
                    "machines": 1,
                    "h": 0.25,
                    "recipes": [],
                },
            ],
            "components": [],
            "unlocks": ["mte:107"],
            "notes": ["a note"],
        },
    }
    lines = effort.text(m)
    assert lines[0] == (
        "  effort 0.5 h = build 0.25 h + unlock 0.25 h (crafting-table step 2 s)"
    )
    assert lines[1].split() == list(effort.COLUMNS)
    assert lines[2].split() == ["build", "gt.recipe.mixer", "mte:101", "10", "2", "0.25",
                                "r:gt.recipe.mixer/a"]  # fmt: skip
    assert lines[3].split() == ["unlock", "crafting", "-", "1", "1", "0.25"]
    assert lines[4:] == ["  unlocks: mte:107", "  effort note: a note"]


def test_cli_prints_the_breakdown_and_json_carries_it(db: Path) -> None:
    home = db.parent.parent
    code, text = tpw.run_cli(home, "design", "output_per_effort", "--tier", "MV",
                             "--free", "fluid:water")  # fmt: skip
    assert code == 0, text
    assert text.startswith("mode output_per_effort")
    assert "   effort " in text and " = build " in text
    assert "   part" in text and "gt.recipe.assembler" in text
    code, text = tpw.run_cli(home, "design", "output_per_effort", "--tier", "MV",
                             "--free", "fluid:water", "--json")  # fmt: skip
    assert code == 0
    for raw in text.splitlines():
        p = json.loads(raw)
        rows = p["metrics"]["effort"]["rows"]
        assert rows and all({"part", "map", "mte", "crafts", "machines", "h",
                             "recipes"} <= set(r) for r in rows)  # fmt: skip
        assert p["metrics"]["eff_h"] > 0
