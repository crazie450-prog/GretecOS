"""GT's primitive machines in the graph (WO-3.10 F1, D389 (1), D390), on the WO-3.3 fixture DB
(tests/unit/planner/test_graph.py) plus one made-up Bricked Blast Furnace and its EU-less recipe
for Widget. Every item, recipe and machine is made up; no pack data.

- A recipe a primitive machine runs is flagged ``primitive``, not ``eu0``, and its machine
  check passes with the furnace.
- It doesn't take one of the :data:`graph.TOP_N` slots: the first primitive recipe is kept
  beside the first four others.
- An EU-less recipe on a map no primitive machine runs is still ``eu0``.
- A plan running the furnace is warned that it is fed and emptied by hand, and its effort
  pays a fixed 5 minutes per furnace it builds, whatever the batch or the rate (D411 (1)), so
  it ranks below an otherwise equal automatable route (D404 (4)).
"""

from __future__ import annotations

import copy
import importlib.util
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.db import access
from gregtec.planner import chains, effort, explain, graph, plan, rank
from gregtec.planner.outputs import cards
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


def test_a_primitive_recipe_ranked_below_the_top_four_is_still_checked(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extra slot holds even when the furnace's recipe sorts after four valid others (its
    inputs further from the world than theirs, say): the check goes on past the top 4 until
    a primitive recipe passes, instead of filing it "below the top 4" (D390 (c))."""
    cheap = graph._cheap

    def last(
        item: str, rec: graph.RecipeNode, *args: object, **kw: object
    ) -> tuple[object, ...]:
        key = cheap(item, rec, *args, **kw)  # type: ignore[arg-type]
        return (*key[:2], True, 9, *key[4:]) if "primitive" in rec.flags else key

    monkeypatch.setattr(graph, "_cheap", last)
    g = graph.build(conn, Request(WIDGET), tg.Base(), templates=chains.EMPTY)
    smelt = next(r for r in g.recipes if r.map == f"gt.recipe.{PRIMITIVE_MAP}")
    status = {c.recipe: c.status for c in g.item(WIDGET).candidates}
    assert status[smelt.id] in ("chosen", "alternative")
    assert "unchecked" in status.values()  # a plain recipe below the top 4 still waits


def test_a_hand_fed_route_is_warned_and_pays_for_it(conn: sqlite3.Connection) -> None:
    """D404 (4): the Bricked Blast Furnace takes no pipe or hopper, so a plan running it says
    so once, and its effort counts a fixed 5 minutes per furnace the route builds (D411 (1)),
    the same for any batch and in rate mode: an otherwise equal route without that hand work
    ranks above it."""
    (smelt,) = conn.execute(
        "SELECT r.ref FROM recipe AS r JOIN recipe_map AS m ON m.id = r.recipe_map_id"
        " WHERE m.unloc_name = ?",
        (f"gt.recipe.{PRIMITIVE_MAP}",),
    ).fetchone()
    req = Request(WIDGET, mode="batch", qty=64, routes=1, pins=(Pin(WIDGET, smelt),))
    p = plan.design(conn, req, tg.Base(), templates=chains.EMPTY).plans[0]
    node = next(n for n in p["nodes"] if n["recipe"] == smelt)
    assert node["machine"]["hand_fed"] is True
    said = [w for w in p["warnings"] if "fed and emptied by hand" in w]
    assert len(said) == 1 and f"mte:{BBF_MTE}" in said[0] and smelt in said[0]
    m = p["metrics"]
    assert effort.HAND_FED_S == 300.0
    count = node["count"]
    assert count >= 1
    assert m["hand_h"] == pytest.approx(count * 5 / 60, rel=1e-6)
    hand = [r for r in m["effort"]["rows"] if r["part"] == "hand"]
    assert [(r["mte"], r["machines"]) for r in hand] == [(BBF_MTE, count)]
    assert m["eff_h"] == pytest.approx(
        m["build_h"] + m["unlock_h"] + m["hand_h"], rel=1e-6
    )
    assert "+ by hand" in effort.summary(m)
    assert any("5.0 min per machine" in w for w in said)
    head = {h["key"]: h["value"] for h in cards.effort_card(p)["head"]}
    assert head["by hand"].endswith(f"({count} x 5.0 min)")
    doc = explain.facts([p], "Widget", rank.Prefs())
    assert f"for {count} hand-fed machines" in explain.template(doc)
    # D411 (1): neither the batch size nor rate mode moves it, only the furnace count.
    for other in (
        Request(WIDGET, mode="batch", qty=640, routes=1, pins=(Pin(WIDGET, smelt),)),
        Request(WIDGET, mode="rate", qty=0.05, routes=1, pins=(Pin(WIDGET, smelt),)),
    ):
        q = plan.design(conn, other, tg.Base(), templates=chains.EMPTY).plans[0]
        n = next(n for n in q["nodes"] if n["recipe"] == smelt)
        assert n["count"] >= 1
        assert q["metrics"]["hand_h"] == pytest.approx(n["count"] * 5 / 60, rel=1e-6)
    # The same route, automatable: no hand work, so a lower effort and the better rank.
    piped = copy.deepcopy(p)
    piped["id"] = "pl-piped"
    piped["metrics"]["eff_h"] -= m["hand_h"]
    out = rank.order([copy.deepcopy(p), piped])
    assert [x["id"] for x in out] == ["pl-piped", p["id"]]
    assert out[0]["rank"]["score"] > out[1]["rank"]["score"]
