"""Steel at MV on a real harness DB: the plan GOS should make, and today doesn't (WO-3.10).

Runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the server harness; locally:
mod/README.md), and writes nothing outside tmp_path. docs/reviews/WO-3.10_steel_route.md has
the diagnosis: on this DB the top steel plan is Steeleaf nuggets (a Twilight Forest material
no recipe makes, so a free "raw" import) -> Steeleaf ingot -> dust -> centrifuge -> steel dust
-> Alloy Blast Smelter -> molten steel -> solidifier, and no iron or coal anywhere, because the
Bricked Blast Furnace's iron + coal recipes have no machine model and nothing gates where a raw
input comes from.

``test_steel_at_mv_is_iron_and_carbon`` is the acceptance test of the fixes Clayton approved
(D389), on a batch of 64 (what a request with no amount gets, F6), with Clayton's bounds (D404
(1)): iron from any GT iron ore, at most 25 machines. It passes since D404's planner work. ``test_a_locked_import_is_gated_and_warned`` checks F4 (D395).
The fixture loads
the run's GT ore mixes as `gos ores load` does (no world, so no veins), so the raw-source gate
(F2, D391) sees vein sources: ``test_twilight_raws_are_gated`` checks it, and
``test_steel_starts_with_iron_and_coal`` checks F3's ranking on them (D393).
``test_simplest_steel_route_is_an_extra_column`` checks F5's extra column (D389 (4)) and that
the flag can't reorder the ranked routes (D404 (8)).
``GOS_SERVER_RUN`` names another server run folder.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.db import access, ores
from gregtec.db import quests as quest_db
from gregtec.ingest import collect
from gregtec.ingest import quests as bq
from gregtec.planner import graph, plan
from gregtec.planner.request import DEFAULT_BATCH, DefaultBaseInputs, Request

REPO = Path(__file__).resolve().parents[2]
STEEL = "gregtech:gt.metaitem.01@11305"
STEELEAF_NUGGET = "gregtech:gt.metaitem.01@9339"
STEELEAF_INGOT = "gregtech:gt.metaitem.01@11339"
STEELEAF_DUST = "gregtech:gt.metaitem.01@2339"
STEELEAF_ALLOY = "r:gt.recipe.alloysmelter/1d5e67877281"  # nuggets -> Steeleaf ingot
IRON_ORE = "gregtech:gt.blockores2@32"
NETHER_IRON_ORE = "gregtech:gt.blockores2@1032"
PLATINUM = "gregtech:gt.metaitem.01@2085"
MV, IV = 2, 5
# Every GT iron-bearing ore material (D404 (1)): iron itself, and the ores GT smelts into iron
# (on this run, the Bricked Blast Furnace recipes whose output is an Iron ingot take dust of
# exactly these: SELECT ... FROM recipe JOIN recipe_output ... WHERE gt_material = 'Iron' AND
# gt_prefix = 'ingot' on gt.recipe.primitiveblastfurnace). Ores that only give iron as a
# byproduct (chrome, garnets, vanadium magnetite's magnetite) are not iron ores.
IRON = {
    "Iron", "WroughtIron", "PigIron", "Magnetite", "BrownLimonite", "YellowLimonite",
    "BandedIron", "Pyrite", "RoastedIron", "BasalticMineralSand", "GraniticMineralSand",
}  # fmt: skip
CARBON = {"Coal", "Charcoal", "Coke", "Carbon", "Lignite"}
TWILIGHT = {"Steeleaf", "Knightmetal", "FierySteel", "IronWood"}


@pytest.fixture(scope="module")
def conn(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Iterator[sqlite3.Connection]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = Path(os.environ.get("GOS_SERVER_RUN") or REPO / "mod" / "run" / "server")
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("steel-route-harness")
    built_home(game, home)
    # The run's GT ore mixes, as `gos ores load` writes them (no world here, so no veins):
    # the graph then gates raw ores by dimension (WO-3.10 F2).
    run_id, mixes = ores.read_mixes(home, allow_harness=True)
    w = access.connect(access.db_path(home))
    try:
        ores.write_mixes(w, run_id, mixes)
    finally:
        w.close()
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="module")
def plans(conn: sqlite3.Connection) -> tuple[dict[str, Any], ...]:
    # No amount named: a batch of 64, as the entry points make it (D389 (5), F6).
    req = Request(STEEL, tier_cap=MV, mode="batch", qty=DEFAULT_BATCH)
    d = plan.design(conn, req, DefaultBaseInputs(MV))
    assert d.plans
    return d.plans


@pytest.fixture(scope="module")
def best(plans: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    return plans[0]


def materials(conn: sqlite3.Connection, keys: set[str]) -> set[str]:
    """The GT materials of the item keys (registry@meta) among ``keys``."""
    out: set[str] = set()
    for k in keys:
        reg, _, meta = k.rpartition("@")
        if not reg or not meta.isdigit():
            continue
        row = conn.execute(
            "SELECT gt_material FROM item WHERE registry_name = ? AND meta = ?",
            (reg, int(meta)),
        ).fetchone()
        if row and row[0]:
            out.add(str(row[0]))
    return out


def test_twilight_raws_are_gated(conn: sqlite3.Connection) -> None:
    """D389 (2): with no quests, only the Overworld and the Nether are reachable, so the
    Steeleaf ingot's alloy smelter recipe is rejected for its nugget (a Twilight Forest
    material, data/curated/raw_inputs.tsv), and iron ore is a raw input from its veins."""
    g = graph.build(conn, Request(STEELEAF_INGOT, tier_cap=MV), DefaultBaseInputs(MV))
    why = {c.recipe: c.reason for c in g.item(STEELEAF_INGOT).candidates}
    assert (
        why[STEELEAF_ALLOY] == "Steeleaf Nugget: Twilight Forest, not reachable (D389)"
    )
    vein = graph.sources(conn).vein
    assert {"Overworld", "Nether"} <= set(vein[IRON_ORE])
    assert vein[NETHER_IRON_ORE] == ("Nether",)  # a Netherrack ore: the Nether's only
    g = graph.build(conn, Request(IRON_ORE, tier_cap=MV), DefaultBaseInputs(MV))
    ore = g.item(IRON_ORE)
    assert ore.boundary == "raw" and (ore.source or "").startswith("vein:")


def test_every_ore_mix_dimension_is_known(conn: sqlite3.Connection) -> None:
    """Every dimension the run's ore mixes name is always reachable or has an unlock quest
    in data/curated/dimension_unlocks.tsv (catches a typo or a new dimension)."""
    named: set[str] = set()
    for (dims,) in conn.execute("SELECT dims FROM ore_mix"):
        named.update(json.loads(dims))
    assert named, "no ore mixes loaded"
    rows = bq.read_dimension_unlocks(quest_db.DIMENSION_UNLOCKS)
    unlocks = {u.dimension for u in rows}
    assert named - bq.ALWAYS_DIMENSIONS - unlocks == set()


def test_a_locked_import_is_gated_and_warned(
    conn: sqlite3.Connection, best: dict[str, Any]
) -> None:
    """F4 (D389 (6), D395): a locked import is a gate, so steel's best plan makes its steel
    from reachable raws instead of importing the locked Steeleaf ingot (the best plan before
    F4). It stays legal: Steeleaf dust, which only the ingot's macerator recipe makes, still
    gets a plan that imports the ingot through a gate, with its warning."""
    imports = {b["id"] for b in best["boundary"]["in"]}
    assert STEELEAF_INGOT not in imports, imports
    assert not [w for w in best["warnings"] if "(locked" in w], best["warnings"]
    p = plan.design(
        conn, Request(STEELEAF_DUST, tier_cap=MV), DefaultBaseInputs(MV)
    ).plans[0]
    imports = {b["id"] for b in p["boundary"]["in"]}
    assert STEELEAF_INGOT in imports, imports
    assert STEELEAF_INGOT in p["route"]["gates"]
    # D404 (6): rank.py reads the locked import from route.unsourced; steel's best has none
    assert STEELEAF_INGOT in p["route"]["unsourced"]
    assert "unsourced" not in best["route"]
    assert f"imports {STEELEAF_INGOT} (locked" in "\n".join(p["warnings"])
    for w in p["warnings"]:
        if w.startswith("imports "):
            assert w.split()[1] in imports
        if "(locked:" in w or "(depth:" in w:  # every unsourced import is a gate
            assert w.split()[1] in p["route"]["gates"], w


def test_steel_starts_with_iron_and_coal(conn: sqlite3.Connection) -> None:
    """F3 (D389 (3), D393): with the run's ore mixes loaded, iron dust is one step from its
    veins' ores, so steel's first candidate is the Bricked Blast Furnace's iron dust + coal,
    and the EBF is still offered beside it (D389 (1)). The platline still builds in time."""
    g = graph.build(conn, Request(STEEL, tier_cap=MV), DefaultBaseInputs(MV))
    steel = g.item(STEEL)
    first = g.recipe(steel.candidates[0].recipe)
    assert first.map == "gt.recipe.primitiveblastfurnace", first.id
    keys = {p.key for p in first.inputs if p.consumed}
    assert materials(conn, keys) & IRON, keys
    assert "minecraft:coal@0" in keys or materials(conn, keys) & CARBON, keys
    assert any(g.recipe(r).map == "gt.recipe.blastfurnace" for r in steel.chosen)
    started = time.perf_counter()
    graph.build(conn, Request(PLATINUM, tier_cap=IV), DefaultBaseInputs(0))
    assert time.perf_counter() - started < 2.0  # §8 row 3.3's LIMIT_S


def test_simplest_steel_route_is_an_extra_column(
    conn: sqlite3.Connection, plans: tuple[dict[str, Any], ...]
) -> None:
    """F5 (D389 (4)): asking for the simplest route adds it beside the ranked routes, made of
    each node's first candidate (steel's: the Bricked Blast Furnace, F3), and leaves the
    ranked routes as they were, in the same order (ties break on the route, D404 (8))."""
    req = Request(STEEL, tier_cap=MV, mode="batch", qty=DEFAULT_BATCH, simplest=True)
    d = plan.design(conn, req, DefaultBaseInputs(MV))
    base = [p for p in d.plans if p["rank"]["column"] == "base"]
    before = [p for p in plans if p["rank"]["column"] == "base"]
    assert [(p["route"]["choices"], p["rank"]["score"]) for p in base] == [
        (p["route"]["choices"], p["rank"]["score"]) for p in before
    ]
    simplest = [p for p in d.plans if p["rank"]["column"] == "simplest"]
    simplest += [p for p in base if p["route"].get("simplest")]
    assert len(simplest) == 1, [p["rank"]["column"] for p in d.plans]
    first = graph.build(conn, req, DefaultBaseInputs(MV)).item(STEEL).chosen[0]
    assert first in {n["recipe"] for n in simplest[0]["nodes"]}
    if simplest[0]["rank"]["column"] == "simplest":
        assert d.plans.index(simplest[0]) == len(base)  # right after the ranked routes


def test_steel_at_mv_is_iron_and_carbon(
    conn: sqlite3.Connection, best: dict[str, Any]
) -> None:
    imported = materials(conn, {b["id"] for b in best["boundary"]["in"]})
    assert imported & IRON, imported
    assert imported & CARBON, imported
    assert not imported & TWILIGHT, imported
    assert best["metrics"]["machines"] <= 25  # D404 (1)
    # D404 (4): a Bricked Blast Furnace is fed by hand, said once, and paid for in effort.
    hand = [n for n in best["nodes"] if (n["machine"] or {}).get("hand_fed")]
    said = [w for w in best["warnings"] if "fed and emptied by hand" in w]
    assert len(said) == len({n["machine"]["mte"] for n in hand}), best["warnings"]
    assert (best["metrics"]["hand_h"] > 0) == bool(hand)
    # D404 (5): the ore mixes are loaded here, so no "no ore mixes" warning.
    assert not [w for w in best["warnings"] if w.startswith("no ore mixes")]
