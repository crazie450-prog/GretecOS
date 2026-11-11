"""The recipe graph builder (WO-3.3) on a real harness DB: the dev runtime's recipes and machines.

As test_machines_harness.py, it runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the
server harness; locally: mod/README.md), and writes nothing outside tmp_path:

- every shipped chain template holds against the run (no stale keys);
- the platline graph (platinum dust at IV) builds in under 2 s (§8 row 3.3) on its template, with
  its loops (metallic powder from its own refined salt, calcium from its calcium chloride) and
  its branch points as primary choices, the same on every build;
- three more G3 lines (D65): steel in the EBF at MV, neodymium (rare earths) at LuV and naquadah
  at ZPM, each on its template; and every other G3 target the dev runtime has builds in time
  (not nanocircuits or wetware circuits: their recipes come from GTNH's core mod, which the dev
  runtime doesn't load).
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import chains, graph, machines
from gregtec.planner.request import DefaultBaseInputs, Request

REPO = Path(__file__).resolve().parents[2]
LIMIT_S = 2.0  # §8 row 3.3; it builds in about 0.3 s on the dev container
PLATINUM = "gregtech:gt.metaitem.01@2085"
PMP = "bartworks:gt.bwMetaGenerateddust@47"  # platinum metallic powder
REFINED_SALT = "bartworks:gt.bwMetaGenerateddust@46"
CALCIUM = "gregtech:gt.metaitem.01@2026"
CACL2 = "bartworks:gt.bwMetaGenerateddust@63"  # calcium chloride dust
STEEL = "gregtech:gt.metaitem.01@11305"
NEODYMIUM = "gregtech:gt.metaitem.01@2067"
NAQUADAH = "gregtech:gt.metaitem.01@2324"
EBF = 1000
IV, LUV, ZPM, MV = 5, 6, 7, 2
# Other G3 targets the dev runtime has (D65), with a tier their lines run at.
OTHER_G3 = {
    "fluid:molten.plastic": MV,  # polyethylene
    "fluid:sulfuricacid": MV,
    "gregtech:gt.metaitem.03@32030": 3,  # monocrystalline silicon boule
    "gregtech:gt.metaitem.01@17880": MV,  # rubber sheet
    "fluid:molten.epoxid": 4,
    "gregtech:gt.metaitem.01@11306": 3,  # stainless steel ingot
    "fluid:nitricacid": 3,
    "fluid:molten.polybenzimidazole": 5,
}
# Not in the dev runtime: nanocircuits and wetware circuits. GTNH's circuit recipes come from
# its core mod, which the harness doesn't load (the circuit assembler map is empty there).


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("graph-harness")
    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


def timed(
    conn: sqlite3.Connection, target: str, tier: int
) -> tuple[graph.Graph, float]:
    started = time.perf_counter()
    g = graph.build(conn, Request(target, tier_cap=tier), DefaultBaseInputs(0))
    return g, time.perf_counter() - started


def test_templates_hold(conn: sqlite3.Connection) -> None:
    templates = chains.load()
    assert chains.validate(templates, conn, machines.current_run(conn)) == []


def test_platline(conn: sqlite3.Connection) -> None:
    g, took = timed(conn, PLATINUM, IV)
    print(
        f"platline graph: {took:.3f} s, {len(g.items)} items, {len(g.recipes)} recipes"
    )
    assert took < LIMIT_S
    assert "platline" in g.templates
    target = g.item(PLATINUM)
    assert target.template == "platline" and target.branch == "pt-reduction"
    assert set(target.chosen) == set(chains.load().choices[PLATINUM].recipes)
    loops = [set(c) for c in g.cycles]
    assert any({PMP, REFINED_SALT} <= c for c in loops)  # the refined salt loop
    assert any({CALCIUM, CACL2} <= c for c in loops)  # the calcium loop
    assert {"fluid:platinum concentrate", "fluid:aqua regia"} <= set(g.primary)
    assert not [w for w in g.warnings if w.startswith("chain")]
    again, _ = timed(conn, PLATINUM, IV)
    assert again.digest() == g.digest()


def test_steel_in_the_ebf(conn: sqlite3.Connection) -> None:
    g, took = timed(conn, STEEL, MV)
    assert took < LIMIT_S
    ebf = [
        g.recipe(ref)
        for ref in g.item(STEEL).chosen
        if g.recipe(ref).map == "gt.recipe.blastfurnace"
    ]
    assert ebf and all(EBF in r.machines for r in ebf)
    assert all(r.tier <= MV for r in g.recipes)


@pytest.mark.parametrize(
    ("target", "tier", "chain"),
    [(NEODYMIUM, LUV, "rare-earths"), (NAQUADAH, ZPM, "naquadah")],
)
def test_template_lines(
    conn: sqlite3.Connection, target: str, tier: int, chain: str
) -> None:
    g, took = timed(conn, target, tier)
    assert took < LIMIT_S
    assert chain in g.templates and g.item(target).template == chain
    assert not [w for w in g.warnings if w.startswith(f"chain {chain}")]
    templated = {k for k, c in chains.load().choices.items() if c.chain == chain}
    assert len(templated & {i.id for i in g.items if i.template == chain}) >= 5


def test_naquadah_keeps_the_neutron_activator(conn: sqlite3.Connection) -> None:
    g, _ = timed(conn, NAQUADAH, ZPM)
    na = g.recipe("r:gg.recipe.neutron_activator/8b84d3c2bc80")
    assert "eu0" in na.flags and "custom" in na.badges  # D142 (a): used, with a warning
    assert any(w.startswith(na.id) for w in g.warnings)


@pytest.mark.parametrize(("target", "tier"), sorted(OTHER_G3.items()))
def test_other_g3_targets_build(
    conn: sqlite3.Connection, target: str, tier: int
) -> None:
    g, took = timed(conn, target, tier)
    assert took < LIMIT_S
    assert g.item(target).chosen, g.item(target).candidates[:5]
    assert all(r.tier <= tier for r in g.recipes)


# The platline's own items: a nitric or sulfuric acid graph must not reach them through a
# byproduct route (review M2).
PLATLINE_ITEMS = {PMP, REFINED_SALT, "fluid:aqua regia", "fluid:platinum concentrate"}


@pytest.mark.parametrize(
    ("target", "tier"), [("fluid:sulfuricacid", MV), ("fluid:nitricacid", 3)]
)
def test_acids_stay_out_of_the_platline(
    conn: sqlite3.Connection, target: str, tier: int
) -> None:
    g, _ = timed(conn, target, tier)
    assert not PLATLINE_ITEMS & {i.id for i in g.items}
    assert len(g.items) < 60, len(g.items)  # 19 and 34 on the dev runtime's run
    assert not [p for p in g.primary if g.item(p).template == "platline"]


def test_epoxid_stays_out_of_other_chains(conn: sqlite3.Connection) -> None:
    # Final check: salt and water in the mixer is a way to make salt water, not the reverse
    # of one of its uses (salt has routes of its own), so saltwater no longer distils
    # naquadah's waste liquid; sodium isn't electrolysed from the sodium oxide only
    # naquadah's line gives off; and calcium for calcium acetate doesn't enter the
    # platline's calcium loop from outside (D189).
    g, _ = timed(conn, "fluid:molten.epoxid", 4)
    ids = {i.id for i in g.items}
    other = {
        k
        for k, c in chains.load().choices.items()
        if c.chain in ("naquadah", "platline", "rare-earths")
    }
    assert not other & (ids - {CALCIUM}), sorted(other & ids)
    assert g.item(CALCIUM).template is None
    assert set(g.templates) <= {"basic-chemicals"}
    assert g.item("fluid:saltwater").chosen[0] == "r:gt.recipe.mixer/41bbc2fe4611"
    sodium = g.item("gregtech:gt.metaitem.01@2017")
    assert sodium.chosen == ("r:gt.recipe.electrolyzer/800e278da1d1",)  # from salt
    assert len(g.items) < 150, len(g.items)  # 129 on the dev runtime's run (166 before)


def test_sulfuric_acid_first_choice_takes_no_cells(conn: sqlite3.Connection) -> None:
    # Review M1: water given off by one of the acid's uses doesn't make every recipe that
    # takes water a "reversal"; the first choice is the LCR's, no IC2 cells.
    g, _ = timed(conn, "fluid:sulfuricacid", MV)
    first = g.recipe(g.item("fluid:sulfuricacid").chosen[0])
    assert "cells" not in first.flags
    assert first.id == "r:gt.recipe.largechemicalreactor/7e955d56eb0f"


def test_assembly_line_recipes_need_their_research(conn: sqlite3.Connection) -> None:
    # GT lists its assembly-line recipes only in a fake NEI map; the graph admits them when
    # their research recipe is at the tier cap (D189).
    arm = "gregtech:gt.metaitem.01@32657"  # Robot Arm (UV)
    al = "r:gt.recipe.fakeAssemblylineProcess/0657ced2c028"
    g, _ = timed(conn, arm, 7)  # a ZPM recipe in the run
    rec = g.recipe(al)
    assert "research" in rec.flags and rec.research is not None
    assert rec.research.startswith(
        ("r:gt.recipe.scanner/", "r:gt.recipe.researchStation/")
    )
    low, _ = timed(conn, arm, 6)
    c = {c.recipe: c for c in low.item(arm).candidates}[al]
    assert c.status == "rejected" and "above the cap" in c.reason
