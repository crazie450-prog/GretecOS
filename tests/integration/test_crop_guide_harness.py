"""The crop guide (WO-6.7) on a real export's DB: GregtecLink's client harness (the dev runtime's
GT 5.09.54.265 with CropsNH 2.0.140, mode "harness").

- The ported rules agree with what CropsNH itself shows in that export: every Crop Breeder's
  success chance and tank, every Seed Generator's tank, every Crop Manager's range (their
  tooltips, written by the mod's own code), and the enriched fertilizer of every Crop Breeder
  recipe NEI lists (the mod's fake recipes are made at 31/31/31 for the child's tier).
- The PM's five picks (``gregtec.crops.view.PM_PICKS``, pending Clayton's own) each get a guide
  with every number cited (:func:`gregtec.crops.planner.check`), one needing a sub-soil block
  and one the Crop Breeder.

As the other harness tests, this runs with GOS_REQUIRE_HARNESS_RUN=1 after the harnesses (CI's
mod job); ``GOS_CROPS_HOME`` names a GOS_HOME whose DB a client run was already built into, for
a local run. Nothing is written outside tmp_path.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.crops import facts as crop_facts
from gregtec.crops import planner, rules, view
from gregtec.db import access, build
from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    built = os.environ.get("GOS_CROPS_HOME")
    if built:
        home = Path(built)
    else:
        if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
            pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
        game = REPO / "mod" / "run" / "client"
        if not (game / collect.EXPORTS).is_dir():
            pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
        home = tmp_path_factory.mktemp("crops")
        collect.collect(game, home, 1, allow_harness=True)
        build.build(home, allow_harness=True)
    c = access.connect(access.db_path(home), readonly=True)
    yield c
    c.close()


def _tooltips(conn: sqlite3.Connection, cls: str) -> list[tuple[int, str]]:
    return [
        (int(t), re.sub("§.", "", tip or ""))
        for t, tip in conn.execute(
            "SELECT tier, tooltip FROM machine WHERE class_name = ? ORDER BY tier",
            (cls,),
        )
    ]


def _num(text: str) -> int:
    return int(text.replace(",", ""))


def test_crop_breeder_tooltips_match_the_rules(conn: sqlite3.Connection) -> None:
    tips = _tooltips(conn, crop_facts.MACHINE_CLASSES["crop_breeder"])
    assert len(tips) >= 7
    for tier, tip in tips:
        chance = re.search(r"([0-9]+)% success chance", tip)
        tank = re.search(r"Stores ([0-9,]+)L of fluid", tip)
        assert chance and tank, tip
        assert _num(chance[1]) == rules.breeder_chance(tier), tier
        assert _num(tank[1]) == rules.breeder_tank(tier), tier
        assert f"Needs {rules.BREEDER_FERTILIZER_PER_TIER}L" in tip
        assert f"Needs {rules.BREEDER_FERTILIZER_PER_STAT}L" in tip
        assert ("Cannot breed high-stat seeds" in tip) == (tier < rules.HV)


def test_seed_generator_and_crop_manager_tooltips_match(
    conn: sqlite3.Connection,
) -> None:
    for tier, tip in _tooltips(conn, crop_facts.MACHINE_CLASSES["seed_generator"]):
        tank = re.search(r"Stores ([0-9,]+)L of fluid", tip)
        assert tank and _num(tank[1]) == rules.seed_generator_tank(tier), (tier, tip)
        assert f"Needs {rules.SEED_GENERATOR_FERTILIZER_PER_STAT}L" in tip
    for tier, tip in _tooltips(conn, crop_facts.MACHINE_CLASSES["crop_manager"]):
        rng = re.search(r"Range: ([0-9]+)x([0-9]+)x([0-9]+) blocks", tip)
        assert rng, tip
        side = 2 * rules.crop_manager_radius(tier) + 1
        assert (int(rng[1]), int(rng[2]), int(rng[3])) == (
            side,
            2 * rules.CROP_MANAGER_VERTICAL_RADIUS + 1,
            side,
        ), tier


def test_every_crop_breeder_recipe_is_a_parent_recipe_at_the_rules_fertilizer(
    conn: sqlite3.Connection,
) -> None:
    f = crop_facts.load(conn)
    recipes = {(r.child, frozenset(r.parents)) for r in f.data.recipes}
    assert len(f.breeder) == len(f.data.recipes) > 0
    for b in f.breeder:
        assert (b.child, b.parents) in recipes, b.ref
    fluids = dict(
        conn.execute(
            "SELECT r.ref, i.amount FROM recipe AS r JOIN recipe_map AS m"
            " ON m.id = r.recipe_map_id JOIN recipe_input AS i ON i.recipe_id = r.id"
            " WHERE m.unloc_name = ? AND i.fluid_id IS NOT NULL",
            (crop_facts.BREEDER_MAP,),
        ).fetchall()
    )
    for b in f.breeder:
        tier = f.data.crops[b.child].tier
        assert fluids[b.ref] == rules.breeder_fertilizer(tier, 31, 31, 31), b.ref


@pytest.mark.parametrize("crop", view.PM_PICKS)
def test_the_five_picks_every_number_cited(conn: sqlite3.Connection, crop: str) -> None:
    f = crop_facts.load(conn)
    for tier in (0, 1, 3):
        g = planner.guide(f, crop, {}, tier)
        assert g.steps, (crop, tier)
        assert planner.check(g, f) == [], (crop, tier)
        nums = [o for o in planner._walk(g) if isinstance(o, planner.Num)]
        assert nums and all(n.refs for n in nums)


def test_the_picks_cover_a_sub_soil_block_and_the_crop_breeder(
    conn: sqlite3.Connection,
) -> None:
    f = crop_facts.load(conn)
    guides = [planner.guide(f, c, {}, 3) for c in view.PM_PICKS]
    assert any(
        r.what == "sub_soil" and s.child == g.target
        for g in guides
        for s in g.steps
        for r in s.requirements
    )
    assert any(
        s.kind == "breeder" and s.child == g.target for g in guides for s in g.steps
    )
