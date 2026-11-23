"""Steel at MV on a real harness DB: the plan GOS should make, and today doesn't (WO-3.10).

Runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the server harness; locally:
mod/README.md), and writes nothing outside tmp_path. docs/reviews/WO-3.10_steel_route.md has
the diagnosis: on this DB the top steel plan is Steeleaf nuggets (a Twilight Forest material
no recipe makes, so a free "raw" import) -> Steeleaf ingot -> dust -> centrifuge -> steel dust
-> Alloy Blast Smelter -> molten steel -> solidifier, and no iron or coal anywhere, because the
Bricked Blast Furnace's iron + coal recipes have no machine model and nothing gates where a raw
input comes from.

``test_steel_at_mv_is_iron_and_carbon`` is the acceptance test of the fixes Clayton approved
(D389): a strict xfail until then, so it fails CI the day the planner starts passing it
(remove the mark in that PR). ``test_a_locked_import_is_warned`` holds today. The fixture loads
the run's GT ore mixes as `gos ores load` does (no world, so no veins), so the raw-source gate
(F2, D391) sees vein sources: ``test_twilight_raws_are_gated`` checks it, and
``test_steel_starts_with_iron_and_coal`` checks F3's ranking on them (D393).
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
from gregtec.planner.request import Constraints, DefaultBaseInputs, Request

REPO = Path(__file__).resolve().parents[2]
STEEL = "gregtech:gt.metaitem.01@11305"
STEELEAF_NUGGET = "gregtech:gt.metaitem.01@9339"
STEELEAF_INGOT = "gregtech:gt.metaitem.01@11339"
STEELEAF_ALLOY = "r:gt.recipe.alloysmelter/1d5e67877281"  # nuggets -> Steeleaf ingot
IRON_ORE = "gregtech:gt.blockores2@32"
NETHER_IRON_ORE = "gregtech:gt.blockores2@1032"
PLATINUM = "gregtech:gt.metaitem.01@2085"
MV, IV = 2, 5
IRON = {"Iron", "WroughtIron", "PigIron"}
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
def best(conn: sqlite3.Connection) -> dict[str, Any]:
    d = plan.design(conn, Request(STEEL, tier_cap=MV), DefaultBaseInputs(MV))
    assert d.plans
    return d.plans[0]


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


def test_a_locked_import_is_warned(conn: sqlite3.Connection) -> None:
    """Holds today: with the Steeleaf nugget left out, the best plan imports the Steeleaf
    ingot, which this player can't make (locked), and its warnings say so."""
    req = Request(
        STEEL, tier_cap=MV, constraints=Constraints(exclude=(STEELEAF_NUGGET,))
    )
    p = plan.design(conn, req, DefaultBaseInputs(MV)).plans[0]
    imports = {b["id"] for b in p["boundary"]["in"]}
    assert STEELEAF_INGOT in imports, imports
    assert f"imports {STEELEAF_INGOT} (locked" in "\n".join(p["warnings"])
    for w in p["warnings"]:
        if w.startswith("imports "):
            assert w.split()[1] in imports


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


@pytest.mark.xfail(
    strict=True,
    reason="WO-3.10: F1, F2 and F3 are built; F4 is not yet",
)
def test_steel_at_mv_is_iron_and_carbon(
    conn: sqlite3.Connection, best: dict[str, Any]
) -> None:
    imported = materials(conn, {b["id"] for b in best["boundary"]["in"]})
    assert imported & IRON, imported
    assert imported & CARBON, imported
    assert not imported & TWILIGHT, imported
    assert best["metrics"]["machines"] <= 10
