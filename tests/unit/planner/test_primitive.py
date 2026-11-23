"""GT's primitive machines in the graph (WO-3.10 F1, D389 (1), D390), on the WO-3.3 fixture DB
(tests/unit/planner/test_graph.py) plus one made-up Bricked Blast Furnace and its EU-less recipe
for Widget. Every item, recipe and machine is made up; no pack data.

- A recipe a primitive machine runs is flagged ``primitive``, not ``eu0``, and its machine
  check passes with the furnace.
- It doesn't take one of the :data:`graph.TOP_N` slots: the first primitive recipe is kept
  beside the first four others.
- An EU-less recipe on a map no primitive machine runs is still ``eu0``.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.db import access
from gregtec.planner import chains, graph
from gregtec.planner.request import Pin, Request


def _graph_fixture() -> ModuleType:
    path = Path(__file__).with_name("test_graph.py")
    spec = importlib.util.spec_from_file_location("_f1_graph_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tg = _graph_fixture()
WIDGET = tg.WIDGET
BBF_MTE = 140
PRIMITIVE_MAP = "primitiveblastfurnace"
# Dust B -> Widget in a Bricked Blast Furnace: no EU, 2400 ticks.
SMELT = tg.gt(PRIMITIVE_MAP, 0, 0, 2400, ins=[tg.ref(4, 3)], outs=[tg.ref(1)])
BBF = {
    "mte_id": BBF_MTE,
    "item": "gregtech:gt.blockmachines",
    "meta": BBF_MTE,
    "class": "gregtech.common.tileentities.machines.multi.MTEBrickedBlastFurnace",
    "mod": "gregtech",
    "meta_name": "multimachine.brickedblastfurnace",
    "name": "Bricked Blast Furnace",
    "tier": None,
    "multiblock": False,
    "recipe_maps": [f"gt.recipe.{PRIMITIVE_MAP}"],
    "tooltip": [],
}


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    maps, machines, recipes = tg.MAPS, tg.machines, tg.recipes
    tg.MAPS = (*maps, PRIMITIVE_MAP)
    tg.machines = lambda: [*machines(), BBF]
    tg.recipes = lambda: [*recipes(), SMELT]
    try:
        return tg.make_home(tmp_path_factory.mktemp("primitive-home"))
    finally:
        tg.MAPS, tg.machines, tg.recipes = maps, machines, recipes


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def test_a_primitive_recipe_is_kept_beside_the_top_four(
    conn: sqlite3.Connection,
) -> None:
    g = graph.build(conn, Request(WIDGET), tg.Base(), templates=chains.EMPTY)
    recipes = {r.id: r for r in g.recipes}
    smelt = next(r for r in g.recipes if r.map == f"gt.recipe.{PRIMITIVE_MAP}")
    assert "primitive" in smelt.flags and "eu0" not in smelt.flags
    assert smelt.machines == (BBF_MTE,)
    node = g.item(WIDGET)
    assert smelt.id in node.chosen
    # The first TOP_N of the other valid recipes are still top (chosen or alternative): the
    # primitive one is a fifth.
    top = {c.recipe for c in node.candidates if c.status in ("chosen", "alternative")}
    plain = [
        c.recipe
        for c in node.candidates
        if c.status != "rejected"
        and c.status != "unchecked"
        and "primitive" not in recipes[c.recipe].flags
    ]
    assert len(plain) >= graph.TOP_N  # enough others to fill every slot
    assert top == {*plain[: graph.TOP_N], smelt.id}
    # An EU-less recipe on a map no primitive machine runs is still eu0 (mixer/4, pinned).
    mixer4 = tg.ref_of(conn, "mixer", 4)
    pinned = Request(WIDGET, pins=(Pin(WIDGET, mixer4),))
    g = graph.build(conn, pinned, tg.Base(), templates=chains.EMPTY)
    flags = g.recipe(mixer4).flags
    assert "eu0" in flags and "primitive" not in flags
