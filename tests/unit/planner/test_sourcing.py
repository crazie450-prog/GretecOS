"""Inputs closer to the world first (WO-3.10 F3, D427 (3), (6)) on the WO-3.3 fixture DB
(tests/unit/planner/test_graph.py) plus made-up recipes for this file. No pack data.

- ``mixer/10``: Dust A -> Gear, LV (tier 1);
- ``mixer/11``: Plate A -> Gear, 1 EU/t (tier 0);
- ``mixer/12``: Ore A -> Gear, 1 EU/t (tier 0);
- ``mixer/13``: Ore A -> Rod at 0 EU/t (EU-less: no step from the world);
- ``mixer/14``: Rod from a circuit it doesn't use up (nothing consumed: no step either);
- ``canner/1``: an empty cell filled with acid -> Acid Cell (a fluid cell holds its fluid).

Dust A is one step from Ore A (with the fixture's water, a curated raw input), Plate A two.
A recipe whose inputs are at hand, or one step from them, ranks before a lower-tier one whose
inputs aren't; an unknown raw (no recipe, no source) ranks last when GOS has source data.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.db import access
from gregtec.ingest.normalize import CIRCUIT
from gregtec.planner import cache, chains, graph, machines
from gregtec.planner.request import Request


def _graph_fixture() -> ModuleType:
    path = Path(__file__).with_name("test_graph.py")
    spec = importlib.util.spec_from_file_location("_f3_graph_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tg = _graph_fixture()
T, ORE_A, DUST_A, PLATE = tg.T, tg.ORE_A, tg.DUST_A, tg.PLATE
ACID, GAS, ACID_CELL = tg.ACID, tg.GAS, tg.ACID_CELL
GEAR, ROD = f"{T}@23", f"{T}@24"
EXTRA = [
    tg.gt("mixer", 10, 30, 50, ins=[tg.ref(3)], outs=[tg.ref(23)]),
    tg.gt("mixer", 11, 1, 51, ins=[tg.ref(2)], outs=[tg.ref(23)]),
    tg.gt("mixer", 12, 1, 52, ins=[tg.ref(5)], outs=[tg.ref(23)]),
    tg.gt("mixer", 13, 0, 53, ins=[tg.ref(5)], outs=[tg.ref(24)]),
    tg.gt("mixer", 14, 30, 54, ins=[tg.ref(1, 0, CIRCUIT)], outs=[tg.ref(24)]),
    tg.gt("canner", 1, 1, 55, ins=[tg.ref(11)], fin=[tg.fl("testacid", 1000)],
          outs=[tg.ref(10)]),
]  # fmt: skip


@pytest.fixture(scope="module")
def src(tmp_path_factory: pytest.TempPathFactory) -> Path:
    recipes, items = tg.recipes, tg.items

    def more_items() -> list[dict[str, object]]:
        return [
            *items(),
            *(
                {"item": T, "meta": m, "unlocalized": f"{T}.{m}", "name": n, "ore": []}
                for m, n in ((23, "Gear"), (24, "Rod"))
            ),
        ]

    tg.recipes = lambda: [*recipes(), *EXTRA]
    tg.items = more_items
    try:
        return tg.make_home(tmp_path_factory.mktemp("sourcing-home"))
    finally:
        tg.recipes, tg.items = recipes, items


@pytest.fixture
def conn(src: Path, tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A writable copy of the fixture DB (its own path: its own reader cache scope)."""
    dst = tmp_path / "gos.sqlite"
    a, b = access.connect(src, readonly=True), sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()
    c = access.connect(dst)
    try:
        yield c
    finally:
        c.close()


def mixer(conn: sqlite3.Connection, idx: int) -> str:
    line = next(x for x in EXTRA if x["idx"] == idx)
    row = conn.execute(
        "SELECT r.ref FROM recipe AS r JOIN recipe_map AS m ON m.id = r.recipe_map_id"
        " WHERE m.unloc_name = 'gt.recipe.mixer' AND r.eut = ? AND r.duration_ticks = ?",
        (line["eut"], line["duration"]),
    ).fetchone()
    return str(row[0])


def order(g: graph.Graph, key: str) -> list[str]:
    return [c.recipe for c in g.item(key).candidates if c.status != "rejected"]


def add_other_mix(c: sqlite3.Connection) -> None:
    """An Overworld ore mix of a material no fixture ore has: GOS has source data, and Ore A
    (no mix places it) stays an unknown raw."""
    c.execute(
        "INSERT INTO ore_mix(id, name, dims, ymin, ymax, weight, density, size, ores)"
        " VALUES ('ore.mix.other', 'Other', ?, 10, 40, 40, 3, 24, ?)",
        (
            json.dumps({"Overworld": {"ids": []}}),
            json.dumps([{"layer": "primary", "material": "Nothingium", "name": "X",
                         "item": T, "meta": 99}]),
        ),
    )  # fmt: skip


def test_sourced_inputs_rank_before_a_lower_tier(conn: sqlite3.Connection) -> None:
    m10, m11, m12 = (mixer(conn, i) for i in (10, 11, 12))
    # Ore A at hand: Ore A's and Dust A's (one step) recipes before Plate A's, tier 1 too.
    g = tg.build_graph(conn, Request(GEAR, boundary=(ORE_A,)))
    assert order(g, GEAR) == [m12, m10, m11]
    # Plate A in stock: its recipe first.
    g = tg.build_graph(conn, Request(GEAR), tg.Base(stocked={PLATE: 64}))
    assert order(g, GEAR)[0] == m11


def test_unknown_raws_rank_last_only_with_source_data(
    conn: sqlite3.Connection,
) -> None:
    m10, m11, m12 = (mixer(conn, i) for i in (10, 11, 12))
    # No ore mixes: Ore A (no recipe, no source) ranks with the other unsourced inputs, so
    # tier decides (the two tier-0 recipes by their key), and the graph says so.
    g = tg.build_graph(conn, Request(GEAR))
    assert order(g, GEAR) == [*sorted([m11, m12]), m10]
    assert any(w.startswith("no ore mixes loaded") for w in g.warnings)
    # With source data, the unknown raw ranks last, after a higher tier.
    add_other_mix(conn)
    g = tg.build_graph(conn, Request(GEAR))
    assert order(g, GEAR) == [m11, m10, m12]
    assert not any(w.startswith("no ore mixes loaded") for w in g.warnings)


def test_no_ore_mixes_is_always_said_once(conn: sqlite3.Connection) -> None:
    """D404 (5): with no vein data the graph says so (pointing at ``gos ores load``) even
    when no unknown raw meets a candidate: Ore A at hand, so no recipe has one."""
    g = tg.build_graph(conn, Request(GEAR, boundary=(ORE_A,)))
    said = [w for w in g.warnings if w.startswith("no ore mixes loaded")]
    assert len(said) == 1 and "gos ores load" in said[0], g.warnings


def builder(conn: sqlite3.Connection, request: Request) -> graph._Builder:
    return graph._Builder(
        conn, request, tg.Base(), chains.EMPTY, machines.current_run(conn)
    )


def test_a_step_from_the_world_uses_something_up_with_eu(
    conn: sqlite3.Connection,
) -> None:
    """The Eye of Harmony case: an EU-less recipe, or one that uses nothing up, making an
    input is no step from the world."""
    b = builder(conn, Request(GEAR, boundary=(ORE_A,)))
    b._sourcing([ROD, DUST_A, PLATE, ORE_A])
    assert b.src == {ORE_A: 0, DUST_A: 1, PLATE: 2, ROD: 2}
    # EU-less from Ore A; with EU, but from nothing used up.
    assert sorted((c, sorted(ins)) for _, c, ins in b.reader.feeds[ROD]) == [
        (False, [ORE_A]),
        (True, []),
    ]
    b = builder(conn, Request(GEAR))
    b._sourcing([ORE_A, DUST_A])
    assert b.src == {ORE_A: 3, DUST_A: 2}


def test_an_oredict_slot_takes_its_best_member(conn: sqlite3.Connection) -> None:
    """An oredict slot is at hand when one member is (the birch log here), and an unknown
    raw when none is and no recipe makes any."""
    b = builder(conn, Request(GEAR, boundary=(tg.LOG_B,)))
    b._sourcing(["ore:logTest", tg.LOG_A])
    assert b.src == {"ore:logTest": 0, tg.LOG_A: 3}
    b = builder(conn, Request(GEAR))
    b._sourcing(["ore:logTest"])
    assert b.src == {"ore:logTest": 3}


def test_a_fluid_cell_counts_as_its_fluid(conn: sqlite3.Connection) -> None:
    """The acid is one step from the gas and water at hand, so its cell is too (not a
    further step through the canner): a recipe with a cell of A and fluid B ties with one
    with fluid A and a cell of B."""
    b = builder(conn, Request(GEAR, boundary=(GAS,)))
    b._sourcing([ACID, ACID_CELL])
    assert b.reader.holds[ACID_CELL] == frozenset({ACID})
    assert b.src == {ACID: 1, ACID_CELL: 1}
    # A recipe that uses a cell up is never in the first group: its empty cells are made.
    rec = graph.RecipeNode(
        "r:a/000000000001", "gt.recipe.x", "gt", 30, 20, 1,
        (graph.Port(ACID_CELL, 1),), (graph.Port(GEAR, 1),), (1,), True, (), ("cells",),
    )  # fmt: skip
    b.src[ACID_CELL] = 0
    assert b._pair(rec) == (1, 0)


def test_a_shared_reader_carries_no_request_world(conn: sqlite3.Connection) -> None:
    cache.clear()
    tg.build_graph(conn, Request(GEAR, boundary=(ORE_A,)))
    shared = tg.build_graph(conn, Request(GEAR))
    assert cache.stats.reader_hits >= 1
    cache.clear()
    assert shared.digest() == tg.build_graph(conn, Request(GEAR)).digest()


def test_heuristic_keys() -> None:
    def node(ref: str, tier: int) -> graph.RecipeNode:
        return graph.RecipeNode(
            ref, "gt.recipe.x", "gt", 30, 20, tier, (graph.Port("y@1", 1),),
            (graph.Port("x@1", 1),), (1,), True, (), (),
        )  # fmt: skip

    a, b, c = node("r:a/000000000001", 1), node("r:a/000000000002", 2), node("r:a/3", 1)
    srcs = {a.id: (1, 2), b.id: (0, 0), c.id: (1, 1)}
    ranked = sorted([a, b, c], key=lambda n: graph.heuristic("x@1", n, src=srcs[n.id]))
    # Sourced before tier; then the unsourced count before the key.
    assert [n.id for n in ranked] == [b.id, c.id, a.id]
    # Equal pairs: the key decides (the last determinism key).
    assert max([a, c], key=lambda n: graph.heuristic("x@1", n, src=(2, 1))) is c
    assert graph.heuristic("x@1", a, src=(2, 0)) > graph.heuristic("x@1", b, src=(1, 9))
