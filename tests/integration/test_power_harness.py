"""The power catalog (WO-4.1, D230-D232) against the real thing: GT's jar and a server harness DB.

As test_gtmath_harness.py, these run only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after
the harnesses; locally: mod/README.md):

- **The turbine vectors are GT's.** ``make_turbine_vectors.py`` runs GT's own turbine classes
  again and must write exactly ``tests/unit/gtmath/vectors/{rotor,turbine}.tsv`` (D231).
- **Every rotor of the game.** The port's ``TurbineStatCalculator`` on each rotor material and
  size the export lists (``rotor_material``, ``rotor_size``) equals what GT's own calculator gave
  in the game (``rotor_gt``), to the bit.
- **Every fuel map, decoded.** Each map of plan §7.2 has fuel rows; ``FUEL_VALUE`` is the recipe's
  special value; and for every single-block generator and every fuel of its map, the catalog's
  reading (``fuels.single_block_eu`` on the decoded row at the exported efficiency) equals what
  GT's own ``getFuelValue`` gave in the game (``generator_gt``), fluid and item alike.
- **The generator catalog.** New and legacy turbine, engine and boiler controllers are separate
  entries; three rotor set-ups per turbine kind size from the DB.
- **The Large Naquadah Reactor** (WO-4.8, D326): ``make_naquadah_vectors.py`` reruns GT's own
  class and must write exactly ``vectors/naquadah.tsv``; the export's reactor lists and fuel map
  are the oracle's, and the catalog's steady run on each fuel equals the oracle's run.

Nothing is written outside tmp_path.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import fuels, generators
from gregtec.planner.gtmath import naquadah as gt_naquadah
from gregtec.planner.gtmath.turbine import Kind

REPO = Path(__file__).resolve().parents[2]
GTMATH_TESTS = REPO / "tests" / "unit" / "gtmath"
SETUPS = (("Titanium", "large"), ("TungstenSteel", "huge"), ("Neutronium", "normal"))


def _require_harness() -> None:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")


def _load(name: str) -> ModuleType:
    path = GTMATH_TESTS / "oracle" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"gtmath_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_turbine_vectors_are_what_gts_own_classes_compute(
    tmp_path: Path,
) -> None:
    _require_harness()
    assert _load("make_turbine_vectors").main(["--out", str(tmp_path)]) == 0
    for name in ("rotor", "turbine"):
        fresh = (tmp_path / f"{name}.tsv").read_text(encoding="utf-8").splitlines()
        kept = (GTMATH_TESTS / "vectors" / f"{name}.tsv").read_text(encoding="utf-8")
        assert fresh == kept.splitlines(), (
            f"{name}.tsv: run make_turbine_vectors.py and commit"
        )


def test_the_committed_naquadah_vectors_are_what_gts_own_class_computes(
    tmp_path: Path,
) -> None:
    """WO-4.8 (D326): GT's own Large Naquadah Reactor, rerun on the mod's GT jar."""
    _require_harness()
    assert _load("make_naquadah_vectors").main(["--out", str(tmp_path)]) == 0
    fresh = (tmp_path / "naquadah.tsv").read_text(encoding="utf-8").splitlines()
    kept = (GTMATH_TESTS / "vectors" / "naquadah.tsv").read_text(encoding="utf-8")
    assert fresh == kept.splitlines(), (
        "naquadah.tsv: run make_naquadah_vectors.py and commit"
    )


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    _require_harness()
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("power-harness")
    collect.collect(game, home, 1, allow_harness=True)
    result = build.build(home, allow_harness=True)
    assert result.loaded.power_status == "ok", result.loaded.power_error
    c = sqlite3.connect(access.db_path(home))
    yield c
    c.close()


def _run(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT MAX(id) FROM export_run").fetchone()[0])


def test_every_rotor_matches_gts_calculator_in_the_game(
    conn: sqlite3.Connection,
) -> None:
    run = _run(conn)
    sizes = {
        int(r[0]): str(r[1]) for r in conn.execute("SELECT size, name FROM rotor_size")
    }
    assert sorted(sizes) == [170, 172, 174, 176]
    gt: dict[tuple[str, int], dict[str, float]] = {}
    for material, size, key, value in conn.execute(
        "SELECT material, size, key, value FROM rotor_gt WHERE run_id = ?", (run,)
    ):
        gt.setdefault((material, size), {})[key] = value
    materials = [r[0] for r in conn.execute("SELECT name FROM rotor_material")]
    assert len(materials) >= 100 and "Titanium" in materials
    bad = []
    for material in materials:
        for size_id, size in sizes.items():
            rotor = generators.rotor(conn, material, size, run)
            want = gt[(material, size_id)]
            got = {
                "max_durability": rotor.max_durability,
                "base_efficiency": rotor.base_efficiency,
                "loose_efficiency": rotor.loose_efficiency,
                "optimal_flow": rotor.optimal_flow,
                "optimal_steam_flow": rotor.optimal_steam_flow,
                "optimal_gas_flow": rotor.optimal_gas_flow,
                "optimal_plasma_flow": rotor.optimal_plasma_flow,
                "optimal_loose_steam_flow": rotor.optimal_loose_steam_flow,
                "optimal_loose_gas_flow": rotor.optimal_loose_gas_flow,
                "optimal_loose_plasma_flow": rotor.optimal_loose_plasma_flow,
                "overflow": rotor.overflow_tier,
            }
            for key, value in got.items():
                if float(value) != want[key]:
                    bad.append((material, size, key, value, want[key]))
    assert not bad, bad[:10]


def test_every_fuel_map_is_decoded(conn: sqlite3.Connection) -> None:
    run = _run(conn)
    counts = dict(
        conn.execute(
            "SELECT gt_name, COUNT(*) FROM fuel WHERE run_id = ? GROUP BY 1", (run,)
        )
    )
    assert set(counts) == {m.gt_name for m in fuels.MAPS}, set(fuels.BY_NAME) - set(
        counts
    )
    # FUEL_VALUE is GT's special value (an alias of it, GTRecipeConstants).
    mismatched = conn.execute(
        "SELECT COUNT(*) FROM fuel AS f JOIN recipe AS r ON r.id = f.recipe_id"
        " WHERE f.run_id = ? AND f.fuel_value != r.special_value",
        (run,),
    ).fetchone()[0]
    assert mismatched == 0
    # Every fuel of the diesel-like maps is named by its fluid (a cell is its fluid).
    for gt_name in ("dieselFuels", "gasTurbineFuels", "semiFluidFuels", "plasmaFuels"):
        unnamed = conn.execute(
            "SELECT COUNT(*) FROM fuel WHERE run_id = ? AND gt_name = ? AND fluid IS NULL",
            (run, gt_name),
        ).fetchone()[0]
        assert unnamed == 0, gt_name
    # Extreme diesel fuels are GT's copies of the diesel fuels of 1500 EU/L or more.
    diesel = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT fluid, fuel_value FROM fuel WHERE run_id = ? AND gt_name = 'dieselFuels'",
            (run,),
        )
    }
    for fluid, value in conn.execute(
        "SELECT fluid, fuel_value FROM fuel WHERE run_id = ? AND gt_name = 'extremeDieselFuels'",
        (run,),
    ):
        assert value >= 1500 and diesel.get(fluid) == value, fluid
    units = {
        (r[0], r[1])
        for r in conn.execute("SELECT gt_name, unit FROM fuel WHERE run_id = ?", (run,))
    }
    assert ("rtgFuels", fuels.Unit.EU_PER_ITEM.value) in units
    assert ("largeBoilerFakeFuels", fuels.Unit.BOILER_TICKS.value) in units
    assert ("smallNaquadahReactorFuels", fuels.Unit.EU_PER_ITEM.value) in units


def test_single_blocks_match_gts_fuel_values_in_the_game(
    conn: sqlite3.Connection,
) -> None:
    """Every map a single block burns, decoded by the catalog, against GT's getFuelValue: the
    ``fuel`` row's ``value`` in its ``unit`` (EU/L, EU/item with GT's x 1000, EU per RTG pellet)
    at the generator's exported efficiency gives what GT's own generator gave in the game."""
    run = _run(conn)
    gens = {
        int(r[0]): (int(r[1]), str(r[2]), r[3])
        for r in conn.execute(
            "SELECT mte_id, efficiency, class_name, recipe_map FROM generator WHERE run_id = ?",
            (run,),
        )
    }
    Row = tuple[int, str, float]  # FUEL_VALUE, unit, decoded value
    by_fluid: dict[tuple[str, str], Row] = {}
    by_item: dict[tuple[str, str], Row] = {}
    for map_name, fluid, label, special, unit, value, hidden in conn.execute(
        "SELECT map, fluid, input, fuel_value, unit, value, hidden FROM fuel WHERE run_id = ?",
        (run,),
    ):
        if fluid is not None and not hidden:
            by_fluid[(map_name, fluid)] = (special, unit, value)
        by_item[(map_name, label)] = (special, unit, value)
    eu_per_l, eu_per_item = fuels.Unit.EU_PER_L.value, fuels.Unit.EU_PER_ITEM.value
    checked: dict[str, int] = {}
    bad = []
    refused = 0
    for (
        mte_id,
        special,
        fluid,
        eu_long,
        consumed,
        reg,
        meta,
        item_eu,
        item_eu_long,
    ) in conn.execute(
        "SELECT mte_id, special, fluid, eu_long, consumed, registry_name, meta, item_eu,"
        " item_eu_long FROM generator_gt WHERE run_id = ?",
        (run,),
    ):
        efficiency, class_name, map_name = gens[mte_id]
        rule = fuels.BY_MAP[map_name].rule if map_name in fuels.BY_MAP else None
        if rule == "rtg" and reg is not None and item_eu is not None:
            # GT++'s RTG: its getFuelValue (an int) is the pellet's EU at its efficiency.
            row = by_item.get((map_name, f"{reg}@{meta}"))
            if (
                row is None
                or row[1] != eu_per_item
                or int(row[2]) * efficiency // 100 != item_eu
            ):
                bad.append((mte_id, class_name, f"{reg}@{meta}", special, row, item_eu))
            checked[map_name] = checked.get(map_name, 0) + 1
            continue
        if rule != "eu":
            continue
        if fluid is not None and eu_long is not None:
            row = by_fluid.get((map_name, fluid))
            ours = None
            if row is not None and row[1] == eu_per_l:
                ours = int(row[2]) * efficiency * consumed // 100
                assert ours == fuels.single_block_eu(
                    special=row[0],
                    efficiency=efficiency,
                    fluid_input=True,
                    consumed=consumed,
                )
            if ours != eu_long:
                bad.append((mte_id, class_name, fluid, special, row, ours, eu_long))
        if reg is not None and item_eu_long is not None:
            row = by_item.get((map_name, f"{reg}@{meta}"))
            ours = None
            if row is not None and row[1] == eu_per_item:
                ours = int(row[2]) * efficiency // 100  # EU/item is FUEL_VALUE x 1000
            elif row is not None and row[1] == eu_per_l:
                # A container GT indexes by its fluid: one item at GT's cell reading.
                ours = int(row[2]) * 10 * efficiency
            if ours is not None:
                assert ours == fuels.single_block_eu(
                    special=row[0] if row else -1,
                    efficiency=efficiency,
                    fluid_input=False,
                )
            if item_eu_long == 0 and row is not None and fluid is not None:
                # GT's item lookup took no recipe for this container (the dev runtime's IC2
                # lava cell in the geothermal generators); the generator burns its fluid,
                # checked above. Counted, not a catalog error.
                refused += 1
            elif ours != item_eu_long:
                bad.append(
                    (
                        mte_id,
                        class_name,
                        f"{reg}@{meta}",
                        special,
                        row,
                        ours,
                        item_eu_long,
                    )
                )
        checked[map_name] = checked.get(map_name, 0) + 1
    assert not bad, f"{len(bad)}: {bad[:10]}"
    assert refused <= 6
    burned = {
        "dieselFuels",
        "gasTurbineFuels",
        "hotFuels",
        "semiFluidFuels",
        "plasmaFuels",
        "magicFuels",
        "smallNaquadahReactorFuels",
        "largeNaquadahReactorFuels",
        "hugeNaquadahReactorFuels",
        "extremeNaquadahReactorFuels",
        "ultraHugeNaquadahReactorFuels",
        "rocketFuels",
        "rtgFuels",
    }
    assert {fuels.BY_MAP[m].gt_name for m in checked} == burned


def test_the_catalog_tracks_new_and_legacy_controllers(
    conn: sqlite3.Connection,
) -> None:
    entries = generators.catalog(conn)
    kinds: dict[str, set[bool]] = {}
    for e in entries:
        kinds.setdefault(e.kind, set()).add(e.legacy)
    for kind in (
        "large_turbine_steam",
        "large_turbine_hp_steam",
        "large_turbine_sc_steam",
        "large_turbine_gas",
        "large_turbine_plasma",
        "lce",
        "ece",
        "large_boiler_bronze",
        "large_boiler_steel",
        "large_boiler_titanium",
        "large_boiler_tungstensteel",
    ):
        assert kinds.get(kind) == {False, True}, kind
    singles = [e for e in entries if e.sizing == "single_block"]
    assert len(singles) >= 30 and all(e.efficiency is not None for e in singles)
    # A basic generator is sized only on a map whose FUEL_VALUE reads as EU (D232): the steam
    # turbine, the magical energy absorber, the acid generator and the RTG are not.
    unsized = {
        e.class_name.rsplit(".", 1)[-1]
        for e in entries
        if e.kind == "single_block" and e.sizing == "not_modelled"
    }
    assert {"MTESteamTurbine", "MTERTGenerator"} <= unsized
    for e in entries:
        if e.kind == "single_block":
            eu_map = any(
                m in fuels.BY_MAP and fuels.BY_MAP[m].rule == "eu" for m in e.fuel_maps
            )
            assert (e.sizing == "single_block") == eu_map, e.class_name
    turbines = [e for e in entries if e.sizing == "turbine"]
    assert len(turbines) == 10


@pytest.mark.parametrize("kind", list(Kind))
def test_three_rotor_setups_per_turbine_kind_size_from_the_db(
    conn: sqlite3.Connection, kind: Kind
) -> None:
    run = _run(conn)
    fuel = None
    if kind in (Kind.GAS, Kind.PLASMA):
        gt_name = generators.TURBINE_FUEL_MAP[kind]
        fuel = conn.execute(
            "SELECT fluid FROM fuel WHERE run_id = ? AND gt_name = ? AND fluid IS NOT NULL"
            " ORDER BY fuel_value, fluid LIMIT 1",
            (run, gt_name),
        ).fetchone()[0]
    for material, size in SETUPS:
        for legacy in (False, True):
            report = generators.turbine(
                conn,
                kind=kind,
                material=material,
                size=size,
                dynamo_tier=5,
                fuel=fuel,
                legacy=legacy,
            )
            assert [r.label for r in report.runs] == [
                "half optimal",
                "optimal",
                "maximum",
            ]
            assert all(0 < r.eut <= report.cap for r in report.runs)
            assert report.refs[0] == f"rotor:{material}/{size}"
            if fuel is not None:
                assert report.refs[1].startswith("r:")


# The oracle runs GT's Materials outside the game, where three fluids keep their default
# names; in the game they are registered under these (the dev runtime's fluid table).
ORACLE_FLUID = {
    "molten.time": "temporalfluid",
    "molten.space": "spatialfluid",
    "molten.atomicseparationcatalyst": "molten.atomic separation catalyst",
}


def _naquadah_vectors() -> dict[str, dict[str, str]]:
    """The reactor oracle's rows (GT's own class at the pin, make_naquadah_vectors.py), by id."""
    mnv = _load("make_naquadah_vectors")
    lines = (GTMATH_TESTS / "vectors" / "naquadah.tsv").read_text(encoding="utf-8")
    out = {}
    for line in lines.splitlines():
        if line.startswith(("#", "id\t")):
            continue
        case_id, _inputs, outputs = line.split("\t")
        out[case_id] = mnv.parse_fields(outputs)
    return out


def test_the_large_naquadah_reactor_reads_as_gts_own_class(
    conn: sqlite3.Connection,
) -> None:
    """WO-4.8 (D326): the export's reactor lists and fuel map are what GT's own class has at
    the pin (the dev runtime runs GoodGenerator's default config, as the oracle does), and the
    catalog's steady run on each fuel, unboosted and with each coolant and excited fluid,
    equals the oracle's run of the real class (its third run: the first starts from 0 EU/t)."""
    run = _run(conn)
    cat = generators.catalog(conn, run)
    [entry] = [e for e in cat if e.sizing == "naquadah_reactor"]
    assert entry.class_name.endswith(".MTELargeNaquadahReactor") and not entry.legacy
    assert {e.legacy for e in cat if e.kind == "naquadah_reactor_multi"} == {
        False,
        True,
    }
    reactor = generators.reactor(conn, entry.mte_id, run)
    assert reactor is not None
    vec = _naquadah_vectors()
    lists = vec["lists"]
    assert reactor.air == int(lists["liquid_air_per_second"])
    for prefix, boosts in (("coolant", reactor.coolants), ("excited", reactor.excited)):
        oracle = []
        for i in range(len(boosts)):
            name, amount, value = lists[f"{prefix}_{i}"].rsplit(":", 2)
            oracle.append((ORACLE_FLUID.get(name, name), int(amount), int(value)))
        # Names too (review L1): a renamed or reordered fluid with the same numbers fails.
        assert [(b.fluid, b.amount, b.value) for b in boosts] == oracle
        assert f"{prefix}_{len(boosts)}" not in lists
    # The oracle's map holds GoodGenerator's nine (basic output, burn time) pairs first.
    oracle_fuels = {
        tuple(int(x) for x in lists[f"fuel_{i}"].split(":")[:2]): i for i in range(9)
    }
    db_fuels = generators.reactor_fuels(conn, run)
    assert {(f.basic, f.duration) for f in db_fuels} == set(oracle_fuels)
    for f in db_fuels:
        i = oracle_fuels[(f.basic, f.duration)]
        cases: list[tuple[str, gt_naquadah.Boost | None, gt_naquadah.Boost | None]]
        cases = [("none", None, None)]
        cases += [(f"c{c}", b, None) for c, b in enumerate(reactor.coolants)]
        cases += [(f"e{e}", None, b) for e, b in enumerate(reactor.excited)]
        for name, coolant, excited in cases:
            s = gt_naquadah.steady(
                reactor,
                basic=f.basic,
                duration=f.duration,
                coolant=coolant,
                excited=excited,
            )
            out = vec[f"steady:f{i}:{name}"]
            assert (s.eut, s.eu, s.fuel, s.depleted, s.air, s.coolant, s.excited) == (
                int(out["r2_eut"]),
                int(out["r2_eu"]),
                int(out["r2_fuel"]),
                int(out["r2_depleted"]),
                int(out["r2_air"]),
                int(out["r2_coolant"]),
                int(out["r2_excited"]),
            ), (f.fluid, name)
    # MkI: 975 000 EU/t for 60 ticks a litre, 7200 L of liquid air a run (2400 L/s).
    mk1 = next(f for f in db_fuels if f.fluid == "naquadah based liquid fuel mki")
    s = gt_naquadah.steady(reactor, basic=mk1.basic, duration=mk1.duration)
    assert (s.eut, s.per_second(s.air)) == (975000, 2400)
    assert s.per_second(s.fuel) == pytest.approx(1 / 3)
    # The IV dynamo that holds it: TecTech's 256 A laser source hatch.
    d = generators.dynamo(
        conn, run, eut=s.eut, tier_cap=5, takes=generators.exotic(conn, entry.mte_id)
    )
    assert d is not None and d.class_name.endswith(".MTEHatchDynamoTunnel")
    assert (d.tier, d.amps_out) == (5, 256)
