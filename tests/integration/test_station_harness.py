"""The breeding station's plans (WO-7.6) on a real export's DB: GregtecLink's client harness (the
dev runtime's GT 5.09.54.265 with CropsNH 2.0.140, mode "harness").

- The Seed Generator's duplication recipes (``cropsnh.recipes.seedGenerator``) load for the
  crops it can copy and none it can't (a crossing threshold below 0: synthesizer-only), and every
  Crop Breeder recipe's catalysts load by registry name and damage.
- Seed labels (V31): every analysed seed item the export names is "<its crop's name> Seeds",
  the label the station identifies seeds by.
- From the crops with no Crop Breeder recipe of their own (1/1/1), the station plans 3-step
  targets at MV: each plan's command fits the contract and one reply, every run fits the MV
  tank, and every id it cites is in the DB.

As the other harness tests, this runs with GOS_REQUIRE_HARNESS_RUN=1 after the harnesses (CI's
mod job); ``GOS_CROPS_HOME`` names a GOS_HOME whose DB a client run was already built into.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from gregtec.crops import facts as crop_facts
from gregtec.crops import rules, station
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
        home = tmp_path_factory.mktemp("station")
        collect.collect(game, home, 1, allow_harness=True)
        build.build(home, allow_harness=True)
    c = access.connect(access.db_path(home), readonly=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def facts(conn: sqlite3.Connection) -> crop_facts.Facts:
    return crop_facts.load(conn)


def test_the_seed_generator_copies_what_it_can(facts: crop_facts.Facts) -> None:
    crops = facts.data.crops
    copied = set(facts.generator)
    assert len(copied) > 100, len(copied)
    assert copied <= set(crops), copied - set(crops)
    synth_only = {c.id for c in crops.values() if c.crossing_threshold < 0}
    assert not (copied & synth_only), copied & synth_only
    for recipes in facts.generator.values():
        for g in recipes:
            assert g.ref.startswith("r:cropsnh.recipes.seedGenerator/")
            for reg, meta, n, _ in g.catalysts:
                assert ":" in reg and meta >= 0 and n >= 1


def test_breeder_catalysts_load_by_key(facts: crop_facts.Facts) -> None:
    assert len(facts.breeder) >= 100
    with_cat = [r for r in facts.breeder if r.catalysts]
    assert with_cat, "some Crop Breeder recipes take a catalyst"
    for r in facts.breeder:
        assert len(r.catalyst_keys) == len(r.catalysts), r.ref
        for reg, _meta, _n in r.catalyst_keys:
            assert ":" in reg, r.ref


def test_seed_labels_are_crop_name_seeds(
    conn: sqlite3.Connection, facts: crop_facts.Facts
) -> None:
    """V31: the station tells seeds apart by label, "<crop name> Seeds" (CropsNH's
    cropsnh_genericSeeds.format), never by NBT or a numeric id."""
    checked = 0
    for name_en, nbt in conn.execute(
        "SELECT name_en, nbt_json FROM item WHERE registry_name = ? AND nbt_json IS NOT NULL",
        (crop_facts.SEED_ITEM,),
    ):
        try:
            tag = json.loads(nbt)
            crop = tag.get("crop")
        except (ValueError, AttributeError):
            continue
        if isinstance(tag.get("display"), dict) and "Name" in tag["display"]:
            # a renamed stack (the export has one: "Scanned Seed"): its label is its custom
            # name, so the station never takes it for a crop's seed
            continue
        if crop not in facts.data.crops or name_en in (
            None,
            "Unknown Seeds",
            "Invalid Seeds",
            "Any Crops NH Seeds",
        ):
            continue
        assert name_en == station.SEEDS_FORMAT.format(facts.crop_name(crop)), (
            crop,
            name_en,
        )
        checked += 1
    assert checked >= 50, checked


def test_three_step_plans_at_mv(facts: crop_facts.Facts) -> None:
    schema = json.loads(
        (REPO / "docs" / "contracts" / "oc_bridge.schema.json").read_text(
            encoding="utf-8"
        )
    )
    args_ok = Draft202012Validator(
        {"$ref": "#/$defs/crop_start_args", "$defs": schema["$defs"]}
    )
    bred = {r.child for r in facts.breeder}
    have = {c: (1, 1, 1) for c in facts.data.crops if c not in bred}
    known = (
        facts.recipe_refs
        | {f"crop:{c}" for c in facts.data.crops}
        | {m.ref for ms in facts.machines.values() for m in ms}
    )
    three = []
    for target in sorted(bred):
        try:
            plan = station.build(facts, have, target, tier=rules.MV)
        except station.StationError:
            continue
        assert not list(args_ok.iter_errors(plan.args())), target
        size = len(json.dumps(plan.args(), separators=(",", ":")))
        assert size <= station.ARGS_MAX, (target, size)
        for s in plan.steps:
            assert s.fert_mb <= rules.breeder_tank(rules.MV), (target, s.n)
            assert s.chance_pct == 50 and s.copies == 5
        assert set(plan.refs) <= known, set(plan.refs) - known
        if len(plan.steps) == 3:
            three.append(target)
    assert three, "no 3-step target at MV from the base crops"
