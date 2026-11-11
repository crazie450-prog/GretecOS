"""Machine selection (WO-3.2) on a real harness DB: the dev runtime's recipes and machines.

As test_gtmath_harness.py, it runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the
server harness; locally: mod/README.md), and writes nothing outside tmp_path:

- every Electric Blast Furnace recipe of GT's, sized on the real EBF (mte:1000, its curated
  facts) with two energy hatches of the recipe's tier, equals GT's math set up by hand (the
  EBF's heat rule, D110's row);
- a sample of every GT recipe at UXV: selection is the same on every run; a single block never
  draws more than its tier and map allow; flagged machines carry their badge; and nearly every
  recipe gets at least one option (the rest name a reason).
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import machines as m
from gregtec.planner.gtmath.processing import (
    EnergyHatch,
    Logic,
    Recipe,
    multiblock_power,
    process,
)
from gregtec.planner.gtmath.tiers import V, ebf_heat, get_tier

REPO = Path(__file__).resolve().parents[2]
EBF_MTE = 1000
GT_RECIPES = (
    "SELECT r.ref, rm.unloc_name FROM recipe AS r JOIN recipe_map AS rm"
    " ON rm.id = r.recipe_map_id WHERE r.run_id = ? AND r.source IN ('gt', 'gt+nei')"
    " AND r.eut > 0 AND r.duration_ticks > 0 AND r.enabled = 1 AND coalesce(r.fake, 0) = 0"
    " ORDER BY r.ref"
)


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("machines-harness")
    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


def test_the_ebf_is_sized_by_gts_heat_rule(conn: sqlite3.Connection) -> None:
    run = m.current_run(conn)
    machines = m.load_machines(conn, "gt.recipe.blastfurnace", run)
    ebf = next(x for x in machines if x.mte_id == EBF_MTE)
    assert ebf.facts["oc"].value == "heat" and ebf.facts["oc"].source == "curated"
    refs = [
        r[0]
        for r in conn.execute(GT_RECIPES, (run,))
        if r[1] == "gt.recipe.blastfurnace"
    ]
    assert len(refs) > 300
    checked = 0
    for ref in refs:
        recipe = m.load_recipe(conn, ref, run)
        tier = max(1, get_tier(recipe.eut))
        if tier > 13:
            continue
        hs = (EnergyHatch(tier),) * 2
        options, why = m.options_for(
            recipe, ebf, m.Player(tier=tier, coil_max=13, hatches=(hs,))
        )
        heat = ebf_heat(13, 2 * V[tier])
        want = process(
            Recipe(recipe.eut, recipe.duration, heat=recipe.special_value or 0),
            Logic(
                power=multiblock_power(hs),
                heat_oc=True,
                heat_discount=True,
                machine_heat=heat,
                check_heat=True,
            ),
        )
        if not want.ok:
            assert why is not None, ref
            continue
        assert why is None, (ref, why)
        s = options[0].sizing
        assert (s.eut.value, s.duration.value, s.parallel.value) == (
            want.eut,
            want.duration,
            want.parallel,
        ), ref
        assert s.eut.refs == (ref, f"mte:{EBF_MTE}")
        checked += 1
    assert checked > 300


def test_a_sample_of_every_gt_recipe(conn: sqlite3.Connection) -> None:
    run = m.current_run(conn)
    rows = conn.execute(GT_RECIPES, (run,)).fetchall()[::40]
    assert len(rows) > 1500
    player = m.Player(tier=13, coil_max=13)
    cache: dict[str, list[m.MachineInfo]] = {}
    with_options = 0
    for ref, recipe_map in rows:
        recipe = m.load_recipe(conn, ref, run)
        if recipe_map not in cache:
            cache[recipe_map] = m.load_machines(conn, recipe_map, run)
        sel = m.choose(recipe, cache[recipe_map], player)
        assert m.choose(recipe, reversed(cache[recipe_map]), player) == sel
        if sel.options:
            with_options += 1
        else:
            assert all(r.reason for r in sel.rejected), ref
        for o in sel.options:
            s = o.sizing
            assert s.duration.value >= 1 and s.parallel.value >= 1, (ref, o.mte_id)
            if o.kind == "singleblock":
                assert s.eut.value <= V[o.tier] * recipe.map_amperage, (ref, o.mte_id)
                assert s.duration.value <= recipe.duration, (ref, o.mte_id)
            flagged = any(f.key == "flag" for f in o.facts)
            assert ("flagged" in o.badges) is flagged
    # The rest need a build input, a fixed-power machine (D176) or a tier above UXV.
    assert with_options / len(rows) > 0.9, (with_options, len(rows))
