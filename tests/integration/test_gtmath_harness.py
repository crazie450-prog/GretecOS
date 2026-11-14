"""The GT math port (WO-3.1) against the real thing: GT's jar and a real harness DB.

Two checks that need the mod's Gradle build, so, as test_db_harness.py, they run only with
GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the harnesses; locally: mod/README.md):

- **The vectors are GT's.** ``tests/unit/gtmath/oracle/make_vectors.py`` is run again: GT
  5.09.54.265's own ``OverclockCalculator``, ``ParallelHelper`` and ``ProcessingLogic`` (the jar
  the mod builds against) must compute exactly what ``tests/unit/gtmath/vectors/*.tsv`` hold,
  which the unit tests replay through the port (D174).
- **Every recipe of the DB.** The server harness run's GT recipes (about 100 000, the dev
  runtime's, not the pack's) go through the port's single-block and multiblock rules at every
  tier: a machine finds a recipe exactly when GT's voltage rule says so, never draws more than
  its energy input, and never gets slower with a higher tier or a hotter coil. The DB's own
  ``voltage_tier`` must be GT's tier of the recipe's voltage.

Nothing is written outside tmp_path.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.db import access
from gregtec.ingest import collect
from gregtec.planner.gtmath.jmath import INT_MAX, ceil_div
from gregtec.planner.gtmath.overclock import heat_discount_multiplier
from gregtec.planner.gtmath.parallel import Check
from gregtec.planner.gtmath.processing import (
    EnergyHatch,
    Logic,
    Recipe,
    basic_machine,
    multiblock_power,
    process,
)
from gregtec.planner.gtmath.tiers import V, ebf_heat, get_tier

REPO = Path(__file__).resolve().parents[2]
GTMATH_TESTS = REPO / "tests" / "unit" / "gtmath"
BLAST_FURNACE = "gt.recipe.blastfurnace"

# One distinct (EU/t, ticks, recipe map amperage, special value) of the DB's GT recipes.
Combo = tuple[int, int, int, int]


def _require_harness() -> None:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")


def _make_vectors() -> ModuleType:
    path = GTMATH_TESTS / "oracle" / "make_vectors.py"
    spec = importlib.util.spec_from_file_location("gtmath_make_vectors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_vectors_are_what_gts_own_classes_compute(tmp_path: Path) -> None:
    _require_harness()
    assert _make_vectors().main(["--out", str(tmp_path)]) == 0
    for name in ("overclock", "parallel", "logic"):
        fresh = (tmp_path / f"{name}.tsv").read_text(encoding="utf-8").splitlines()
        kept = (GTMATH_TESTS / "vectors" / f"{name}.tsv").read_text(encoding="utf-8")
        assert fresh == kept.splitlines(), f"{name}.tsv: run make_vectors.py and commit"


@pytest.fixture(scope="module")
def db(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    _require_harness()
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("gtmath-harness")
    built_home(game, home)
    return access.db_path(home)


def _rows(db: Path, sql: str, *args: object) -> list[tuple[int, ...]]:
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@pytest.fixture(scope="module")
def combos(db: Path) -> list[Combo]:
    rows = _rows(
        db,
        "SELECT DISTINCT r.eut, r.duration_ticks, COALESCE(m.amperage, 1),"
        " COALESCE(r.special_value, 0) FROM recipe r"
        " JOIN recipe_map m ON m.id = r.recipe_map_id"
        " WHERE r.source IN ('gt', 'gt+nei') AND r.eut > 0 AND r.duration_ticks > 0"
        " ORDER BY 1, 2, 3, 4",
    )
    assert len(rows) > 2000  # the dev runtime has thousands of distinct recipe shapes
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def test_the_dbs_voltage_tier_is_gts_tier(db: Path) -> None:
    rows = _rows(
        db,
        "SELECT eut, COALESCE(amperage, 1), voltage_tier, COUNT(*) FROM recipe"
        " WHERE source IN ('gt', 'gt+nei') AND voltage_tier IS NOT NULL GROUP BY 1, 2, 3",
    )
    assert sum(r[3] for r in rows) > 50_000
    for eut, amperage, tier, _count in rows:
        per_amp = ceil_div(eut, max(amperage, 1))
        if per_amp > V[14]:
            continue  # GT's MAX is 8 EU/t short of 8 * 4^14; above it GT says tier 15
        assert get_tier(per_amp) == tier, (eut, amperage)


def test_single_blocks_run_every_recipe_within_their_voltage(
    combos: list[Combo],
) -> None:
    ran = 0
    for eut, ticks, map_amperage, _special in combos:
        recipe = Recipe(eut=eut, duration=ticks)
        previous = None
        for tier in range(15):
            run = basic_machine(recipe, tier, map_amperage=map_amperage)
            # FindRecipeQuery: voltage x the recipe map's amperage >= the recipe's EU/t.
            assert run.ok == (V[tier] * map_amperage >= eut), (eut, ticks, tier)
            if not run.ok:
                assert run.check is Check.NO_RECIPE and previous is None
                continue
            ran += 1
            assert 1 <= run.duration <= ticks
            if eut <= V[tier]:
                assert eut <= run.eut <= V[tier]
            else:
                assert (run.eut, run.overclocks) == (eut, 0)  # more than one amp
            if previous is not None:
                assert run.duration <= previous.duration
                assert run.eut >= previous.eut
            previous = run
    assert ran > 10 * len(combos)


def test_multiblocks_run_every_recipe_within_their_energy_input(
    combos: list[Combo],
) -> None:
    ran = 0
    for eut, ticks, _map_amperage, _special in combos:
        recipe = Recipe(eut=eut, duration=ticks)
        for hatches, max_parallel, perfect in (
            (1, 1, False),
            (2, 16, False),
            (2, 1, True),
        ):
            previous = None
            for tier in range(15):
                power = multiblock_power([EnergyHatch(tier)] * hatches)
                available = power.voltage * power.amperage
                logic = Logic(
                    power=power,
                    max_parallel=max_parallel,
                    oc_time_reduction=4.0 if perfect else 2.0,
                    int_power=False,
                )
                run = process(recipe, logic)
                if eut > available:
                    assert (run.check, run.required) == (Check.INSUFFICIENT_POWER, eut)
                    assert previous is None
                    continue
                # One plain hatch runs at 1 A, two at 4 A: never past the tier-skip rule.
                assert run.ok, (eut, ticks, hatches, tier)
                ran += 1
                assert run.eut <= available
                assert run.duration >= 1 and run.parallel >= 1
                if previous is not None:
                    assert run.crafts_per_second >= previous.crafts_per_second
                previous = run
                if perfect:
                    continue
                # A plain multiblock keeps its EU/t in an int.
                plain = process(recipe, Logic(power=power, max_parallel=max_parallel))
                if run.eut > INT_MAX:
                    assert plain.check is Check.POWER_OVERFLOW
                else:
                    assert plain == run
    assert ran > 10 * len(combos)


def test_the_ebf_runs_every_blast_furnace_recipe_its_coils_reach(db: Path) -> None:
    rows = _rows(
        db,
        "SELECT DISTINCT r.eut, r.duration_ticks, r.special_value FROM recipe r"
        " JOIN recipe_map m ON m.id = r.recipe_map_id"
        " WHERE m.unloc_name = ? AND r.eut > 0 AND r.duration_ticks > 0 ORDER BY 1, 2, 3",
        BLAST_FURNACE,
    )
    assert len(rows) > 100
    ran = refused = 0
    for eut, ticks, heat in rows:
        recipe = Recipe(eut=eut, duration=ticks, heat=heat)
        for tier in range(max(get_tier(eut), 1), 15, 3):
            hatches = [EnergyHatch(tier)] * 2
            power = multiblock_power(hatches)
            available = power.voltage * power.amperage
            previous = None
            for coil in range(14):
                logic = Logic(
                    power=power,
                    heat_oc=True,
                    heat_discount=True,
                    check_heat=True,
                    machine_heat=ebf_heat(coil, 2 * V[tier]),
                    int_power=False,
                )
                run = process(recipe, logic)
                if heat > logic.machine_heat:
                    assert (run.check, run.required) == (Check.INSUFFICIENT_HEAT, heat)
                    assert previous is None
                    refused += 1
                    continue
                assert run.ok, (eut, ticks, heat, tier, coil)
                ran += 1
                # The heat discount makes the recipe power a fraction: GT's hundredths.
                discount = heat_discount_multiplier(logic.overclock(recipe))
                assert (
                    run.eut <= available * (1 + 0.5 / (100 * eut * discount - 0.5)) + 1
                )
                if previous is not None:
                    assert run.crafts_per_second >= previous.crafts_per_second
                previous = run
    assert ran > 1000 and refused > 100
