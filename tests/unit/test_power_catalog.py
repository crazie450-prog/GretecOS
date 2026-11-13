"""The power catalog (WO-4.1, D230-D232) on a small hand-made DB: the fuel maps' decoding, the
fuel rows ``gos db build`` writes, gt_power.json's reader, the generator catalog and ``gos
power``. The numbers here are test inputs shaped like GT's, not game data; the harness tests
(tests/integration/test_power_harness.py) check the same code against GT's own values."""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec import cli
from gregtec.db import access
from gregtec.db import power as power_db
from gregtec.ingest import power as power_ingest
from gregtec.planner import fuels, generators, powercli
from gregtec.planner.gtmath import turbine as gt_turbine
from gregtec.planner.gtmath.turbine import Kind


def test_every_fuel_map_of_the_plan_is_catalogued_once() -> None:
    names = [m.gt_name for m in fuels.MAPS]
    assert len(names) == len(set(names)) == 16
    assert {
        "dieselFuels",
        "extremeDieselFuels",
        "gasTurbineFuels",
        "hotFuels",
        "denseLiquidFuels",
        "plasmaFuels",
        "magicFuels",
        "rocketFuels",
        "rtgFuels",
        "largeBoilerFakeFuels",
    } <= set(names)
    assert sum("NaquadahReactor" in n for n in names) == 5


@pytest.mark.parametrize("fuel_map", fuels.MAPS, ids=lambda m: m.gt_name)
def test_each_map_decodes_by_its_rule(fuel_map: fuels.FuelMap) -> None:
    fluid = fuels.decode(fuel_map, special=7, duration=40, fluid=True)
    item = fuels.decode(fuel_map, special=7, duration=40, fluid=False)
    if fuel_map.rule == "eu":
        assert fluid == fuels.Decoded(fuels.Unit.EU_PER_L, 7.0)
        assert item == fuels.Decoded(fuels.Unit.EU_PER_ITEM, 7000.0)
    elif fuel_map.rule == "rtg":
        # MTERTGenerator: 7 x 1 728 000 ticks at the recipe's EU/t (0 here).
        assert item == fuels.Decoded(fuels.Unit.EU_PER_ITEM, 0.0)
        rtg = fuels.decode(fuel_map, special=2, duration=0, fluid=False, eut=7)
        assert rtg == fuels.Decoded(fuels.Unit.EU_PER_ITEM, 24192000.0)
    else:
        assert fluid == fuels.Decoded(fuels.Unit.BOILER_TICKS, 40.0)
        assert item == fuels.Decoded(fuels.Unit.BURN_RATIO, 7.0)


def test_an_rtg_pellet_is_capped_like_gt() -> None:
    assert fuels.rtg_pellet_eu(special=1, eut=480) == 829440000
    # 87 x 1 728 000 x 60 is above Integer.MAX_VALUE: GT caps the pellet's EU there.
    assert fuels.rtg_pellet_eu(special=87, eut=60) == 2**31 - 1
    assert fuels.rtg_pellet_eu(special=10**9, eut=1) == 2**31 - 1


def test_single_block_eu_is_gts_long_arithmetic() -> None:
    assert fuels.single_block_eu(special=480, efficiency=95, fluid_input=True) == 456
    assert (
        fuels.single_block_eu(special=1, efficiency=7, fluid_input=True, consumed=7)
        == 0
    )
    assert fuels.single_block_eu(special=10, efficiency=95, fluid_input=False) == 9500
    # No int cap here: GT's int accessor turns these into 0, the planner keeps the long.
    assert (
        fuels.single_block_eu(special=10**6, efficiency=250, fluid_input=False) == 2.5e9
    )


POWER_JSON = {
    "sizes": [
        {
            "size": 174,
            "name": "large",
            "speed_multiplier": 3.0,
            "base_damage": 5.0,
            "durability_multiplier": 3.0,
        }
    ],
    "materials": [
        {
            "name": "Testium",
            "tool_speed": 7.0,
            "tool_quality": 3,
            "durability": 1600,
            "steam_multiplier": 1.0,
            "gas_multiplier": 1.0,
            "plasma_multiplier": 1.0,
            "blade": {"item": "gregtech:gt.metaitem.02", "meta": 1},
            "rotors": [
                {
                    "size": 174,
                    "rotor": {"item": "gregtech:gt.metatool.01", "meta": 174},
                    **{k: 1.0 for k in power_ingest.ROTOR_VALUES},
                },
                {"size": 176, "error": "boom"},
            ],
        }
    ],
    "generators": [
        {
            "mte_id": 1110,
            "class": "gregtech.common.tileentities.generators.MTEDieselGenerator",
            "tier": 1,
            "efficiency": 95,
            "eu_out": 32,
            "amps_out": 1,
            "recipe_map": "gt.recipe.dieselgeneratorfuel",
            "fuels": [
                {
                    "special": 20,
                    "input": {"item": "test:cell", "meta": 1},
                    "item_eu": 19000,
                    "item_eu_long": 19000,
                    "fluid": "testfuel",
                    "eu": 19,
                    "eu_long": 19,
                    "consumed": 1,
                }
            ],
        },
        {
            "mte_id": 869,
            "class": "gtPlusPlus.xmod.gregtech.common.tileentities.generators.MTERTGenerator",
            "tier": 1,
            "efficiency": 100,
            "eu_out": 32,
            "amps_out": 1,
            "recipe_map": "gt.recipe.RTGgenerators",
            "fuels": [],
        },
        {
            "mte_id": 1120,
            "class": "gregtech.common.tileentities.generators.MTESteamTurbine",
            "tier": 1,
            "efficiency": 7,
            "eu_out": 32,
            "amps_out": 1,
            "fuels": [],
        },
    ],
    "containers": [
        {"input": {"item": "test:cell", "meta": 1}, "fluid": "testfuel", "mb": 1000}
    ],
}


def test_gt_power_json_reads() -> None:
    data = power_ingest.parse(POWER_JSON)
    assert data.sizes[0].size == 174 and data.materials[0].name == "Testium"
    assert [r.size for r in data.materials[0].rotors] == [
        174
    ]  # an error row is left out
    fuel = data.generators[0].fuels[0]
    assert (
        fuel.item == ("test:cell", 1)
        and fuel.fluid == "testfuel"
        and fuel.eu_long == 19
    )
    assert data.containers[0].fluid == "testfuel"
    assert power_ingest.status({"parts": {"power": "ok"}}) == "ok"
    assert power_ingest.status({}) == "absent"
    with pytest.raises(power_ingest.FormatError):
        power_ingest.parse(
            {"sizes": [{"size": "x"}], "materials": [], "generators": []}
        )


def _db(path: Path) -> sqlite3.Connection:
    conn = access.connect(path)
    conn.execute(
        "INSERT INTO export_run(id, run_key, fingerprint) VALUES (1, 'r1', 'f')"
    )
    conn.execute(
        "INSERT INTO fluid(id, name) VALUES (1, 'testgas'), (2, 'testfuel'), (3, 'testplasma')"
    )
    conn.execute(
        "INSERT INTO item(id, registry_name, meta, nbt_hash) VALUES"
        " (1, 'test:cell', 1, ''), (2, 'test:rod', 0, ''), (3, 'gregtech:gt.blockmachines', 1, '')"
    )
    maps = {m.map: i for i, m in enumerate(fuels.MAPS, start=1)}
    conn.executemany(
        "INSERT INTO recipe_map(id, unloc_name) VALUES (?, ?)",
        [(i, n) for n, i in maps.items()],
    )
    recipes = [
        # id, map, special, duration, hidden, fake, fluid_id, item_id
        (1, "gt.recipe.gasturbinefuel", 360, 0, 0, 0, 1, None),
        (2, "gt.recipe.dieselgeneratorfuel", 20, 0, 0, 0, None, 1),
        (3, "gt.recipe.smallnaquadahreactor", 500, 0, 0, 0, None, 2),
        (4, "gt.recipe.largeboilerfakefuels", 25, 1200, 0, 0, 2, None),
        (
            5,
            "gt.recipe.largeboilerfakefuels",
            1,
            1,
            0,
            1,
            None,
            None,
        ),  # GT's fake text row
        (6, "gt.recipe.gasturbinefuel", 999, 0, 1, 0, 1, None),  # a hidden duplicate
        (7, "gt.recipe.plasmageneratorfuels", 81920, 0, 0, 0, 3, None),
    ]
    for rid, name, special, duration, hidden, fake, fluid_id, item_id in recipes:
        conn.execute(
            "INSERT INTO recipe(id, run_id, source, recipe_map_id, duration_ticks, special_value,"
            " hidden, fake, signature, content_hash, ref) VALUES (?,1,'gt',?,?,?,?,?,?,?,?)",
            (
                rid,
                maps[name],
                duration,
                special,
                hidden,
                fake,
                f"s{rid}",
                f"c{rid}",
                f"r:{name}/{rid:012d}",
            ),
        )
        if fluid_id or item_id:
            conn.execute(
                "INSERT INTO recipe_input(recipe_id, slot, item_id, fluid_id, amount)"
                " VALUES (?, 0, ?, ?, 1)",
                (rid, item_id, fluid_id),
            )
    conn.execute(
        "INSERT INTO machine(id, mte_id, class_name, name_en, tier) VALUES"
        " (1, 1110, 'gregtech.common.tileentities.generators.MTEDieselGenerator', 'Diesel', 1),"
        " (2, 15527, 'gregtech.common.tileentities.machines.multi.turbines.MTELargeTurbineGas',"
        " 'Large Gas Turbine', NULL),"
        " (3, 1151, 'gregtech.common.tileentities.machines.multi.MTELargeTurbineGasLegacy',"
        " 'Large Gas Turbine', NULL),"
        " (4, 1000, 'gregtech.common.tileentities.machines.multi.MTEElectricBlastFurnace',"
        " 'EBF', NULL),"
        " (5, 869, 'gtPlusPlus.xmod.gregtech.common.tileentities.generators.MTERTGenerator',"
        " 'RTG', 1),"
        " (6, 1120, 'gregtech.common.tileentities.generators.MTESteamTurbine', 'Steam', 1)"
    )
    return conn


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = _db(tmp_path / "gos.sqlite")
    power_db.write(c, 1, power_ingest.parse(POWER_JSON))
    yield c
    c.close()


def test_fuel_rows_are_decoded_per_map(conn: sqlite3.Connection) -> None:
    rows = {
        r[0]: tuple(r[1:])
        for r in conn.execute(
            "SELECT recipe_id, gt_name, input, fluid, hidden, fuel_value, unit, value FROM fuel"
        )
    }
    assert 5 not in rows  # GT's fake description row
    assert rows[1] == ("gasTurbineFuels", "testgas", "testgas", 0, 360, "EU/L", 360.0)
    # A cell is the fluid in it (fuel_container), per litre.
    assert rows[2] == ("dieselFuels", "test:cell@1", "testfuel", 0, 20, "EU/L", 20.0)
    assert rows[3] == (
        "smallNaquadahReactorFuels",
        "test:rod@0",
        None,
        0,
        500,
        "EU/item",
        5e5,
    )
    assert rows[4][5:] == (fuels.Unit.BOILER_TICKS.value, 1200.0)
    assert rows[6][3] == 1
    got = fuels.fuels(conn, gt_name="gasTurbineFuels")
    assert [f.fuel_value for f in got] == [360]  # the hidden duplicate is not GT's fuel


def test_pruning_a_run_deletes_its_power_rows(conn: sqlite3.Connection) -> None:
    power_db.delete(conn, [1])
    for table in power_db.TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_the_catalog_lists_generators_new_and_legacy(conn: sqlite3.Connection) -> None:
    entries = {e.mte_id: e for e in generators.catalog(conn)}
    assert set(entries) == {1110, 15527, 1151, 869, 1120}  # the EBF is no generator
    # Only a basic generator on a map read as EU is sized (D232): not the RTG (EU per pellet),
    # not the steam turbine (no fuel map).
    assert [(entries[i].kind, entries[i].sizing) for i in (869, 1120)] == [
        ("single_block", "not_modelled"),
        ("single_block", "not_modelled"),
    ]
    diesel = entries[1110]
    assert (diesel.sizing, diesel.efficiency, diesel.fuel_maps) == (
        "single_block",
        95,
        ("gt.recipe.dieselgeneratorfuel",),
    )
    assert (entries[15527].kind, entries[15527].legacy) == ("large_turbine_gas", False)
    assert (entries[1151].kind, entries[1151].legacy) == ("large_turbine_gas", True)
    assert entries[1151].turbine is Kind.GAS and entries[1151].ref == "mte:1151"


def test_a_turbine_sizes_from_the_db(conn: sqlite3.Connection) -> None:
    report = generators.turbine(
        conn,
        kind=Kind.GAS,
        material="testium",
        size="large",
        dynamo_tier=5,
        fuel="testgas",
    )
    assert report.fuel is not None and report.fuel.fuel_value == 360
    assert report.refs == (
        "rotor:Testium/large",
        "r:gt.recipe.gasturbinefuel/000000000001",
    )
    optimal = report.runs[1]
    # 1050 L/t of gas EU / 360 EU/L -> 2 L/t at 135 %.
    assert (optimal.flow, optimal.eut) == (2, 972)
    with pytest.raises(generators.CatalogError, match="needs a fuel"):
        generators.turbine(
            conn, kind=Kind.GAS, material="Testium", size="large", dynamo_tier=5
        )
    with pytest.raises(generators.CatalogError, match="no turbine rotor material"):
        generators.turbine(
            conn, kind=Kind.STEAM, material="Nope", size="large", dynamo_tier=5
        )
    with pytest.raises(generators.CatalogError, match="not a fuel"):
        generators.turbine(
            conn,
            kind=Kind.PLASMA,
            material="Testium",
            size="large",
            dynamo_tier=5,
            fuel="x",
        )


def test_the_plasma_turbine_burns_per_second(conn: sqlite3.Connection) -> None:
    """Both plasma controllers run fluidIntoPower once every 20 ticks: the flow is L/s, the
    EU/t holds for the 20 ticks, and a litre makes FUEL_VALUE x efficiency EU (D230)."""
    assert (gt_turbine.cycle_ticks(Kind.PLASMA), gt_turbine.flow_unit(Kind.PLASMA)) == (
        20,
        "L/s",
    )
    for kind in Kind:
        if kind is not Kind.PLASMA:
            assert (gt_turbine.cycle_ticks(kind), gt_turbine.flow_unit(kind)) == (
                1,
                "L/t",
            )
    for legacy in (False, True):
        report = generators.turbine(
            conn,
            kind=Kind.PLASMA,
            material="Testium",
            size="large",
            dynamo_tier=14,
            fuel="testplasma",
            legacy=legacy,
        )
        optimal = report.runs[1]
        assert (optimal.ticks, optimal.unit) == (20, "L/s")
        per_litre = optimal.eut * optimal.ticks / optimal.flow
        want = 81920 * report.rotor.base_efficiency
        assert abs(per_litre - want) / want < 1e-3
    gas = generators.turbine(
        conn,
        kind=Kind.GAS,
        material="Testium",
        size="large",
        dynamo_tier=5,
        fuel="testgas",
    ).runs[1]
    assert (gas.ticks, gas.unit) == (1, "L/t")
    assert gas.eut / gas.flow == 486  # 360 EU/L x 1.35, per tick


def test_gos_power_prints_from_the_db(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    c = _db(home / "data" / "gos.sqlite")
    power_db.write(c, 1, power_ingest.parse(POWER_JSON))
    c.close()
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f"[paths]\ngos_home = {json.dumps(str(home))}\n", encoding="utf-8")
    parser = argparse.ArgumentParser(prog="gos")
    powercli.add_parsers(parser.add_subparsers(dest="command", required=True))

    def run(*argv: str) -> tuple[int, str]:
        out = io.StringIO()
        code = powercli.run(
            parser.parse_args(["power", "--config", str(cfg), *argv]), out
        )
        return code, out.getvalue()

    code, text = run(
        "turbine",
        "gas",
        "--rotor",
        "Testium",
        "--size",
        "large",
        "--dynamo",
        "IV",
        "--fuel",
        "testgas",
    )
    assert (
        code == 0
        and "optimal\t2\t972\t2" in text
        and "r:gt.recipe.gasturbinefuel/" in text
    )
    code, text = run("fuels", "--map", "dieselFuels")
    assert code == 0 and "testfuel\t20\t20\tEU/L" in text
    code, text = run("generators")
    assert (
        code == 0
        and "mte:1151\tLarge Gas Turbine\tlarge_turbine_gas\tlegacy\tturbine" in text
    )
    assert run("fuels", "--map", "nope")[0] == 2
    code, text = run(
        "turbine",
        "plasma",
        "--rotor",
        "Testium",
        "--size",
        "large",
        "--dynamo",
        "UHV",
        "--fuel",
        "testplasma",
    )
    assert code == 0 and "flow\tL/s\tEU/t\toptimal flow shown (L/s)" in text
    assert "regulate the input per second" in text
    # Bad dynamo arguments are argparse errors, not tracebacks or negative EU/t.
    for bad in (
        ("--dynamo", "99"),
        ("--dynamo", "XYZ"),
        ("--amps", "-4"),
        ("--amps", "0"),
    ):
        args = ["--dynamo", "IV", *bad] if bad[0] == "--amps" else list(bad)
        with pytest.raises(SystemExit):
            run("turbine", "steam", "--rotor", "Testium", "--size", "large", *args)
    assert (
        run(
            "turbine",
            "plasma",
            "--rotor",
            "Testium",
            "--size",
            "large",
            "--dynamo",
            "IV",
        )[0]
        == 2
    )


def test_gos_power_is_wired_into_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / "gos.toml"
    cfg.write_text(
        f"[paths]\ngos_home = {json.dumps(str(tmp_path))}\n", encoding="utf-8"
    )
    assert cli.main(["power", "--config", str(cfg), "generators"]) == 2
    assert "gos db build" in capsys.readouterr().out


SCHEMA_V3 = Path(__file__).resolve().parents[1] / "fixtures" / "gos-schema-v3.sql"


def test_a_version_3_db_gets_the_power_tables(tmp_path: Path) -> None:
    # A real version-3 DB: WO-1.10's schema.sql as it was, with data in it.
    path = tmp_path / "gos.sqlite"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA_V3.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO item(registry_name, meta) VALUES ('minecraft:stone', 0)"
        )
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone() == (3,)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert not tables & set(power_db.TABLES)
    finally:
        conn.close()
    conn = access.connect(path)
    try:
        assert (
            conn.execute("PRAGMA user_version").fetchone()[0] == access.SCHEMA_VERSION
        )
        for table in power_db.TABLES:
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM item").fetchone()[0] == 1
    finally:
        conn.close()
