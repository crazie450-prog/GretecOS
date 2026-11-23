"""Route sanity checks from the steel-at-MV diagnosis (WO-3.10) on the WO-3.3 fixture DB
(tests/unit/planner/test_graph.py), plus one made-up recipe for this file: ``mixer/8``, the
target Widget taken apart into Dust B at the lowest tier, so the heuristic's tier key alone
would rank it first. Every item, recipe and machine is made up; no pack data.

- A recipe that uses the request's target up is a loop back to the target, never a source of
  one of its inputs: it ranks as a reversal, and a primary node doesn't expand it.
- A plan that imports what it can't make (a ``locked`` boundary) says so in its warnings.
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
from gregtec.planner import chains, graph, plan
from gregtec.planner.request import Constraints, Pin, Request


def _graph_fixture() -> ModuleType:
    path = Path(__file__).with_name("test_graph.py")
    spec = importlib.util.spec_from_file_location("_wo310_graph_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tg = _graph_fixture()
WIDGET, DUST_B = tg.WIDGET, tg.DUST_B
# Widget -> 2 Dust B at 1 EU/t: the lowest tier of Dust B's recipes.
TEAR_DOWN = tg.gt("mixer", 8, 1, 10, ins=[tg.ref(1)], outs=[tg.ref(4, 2)])


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    recipes = tg.recipes
    tg.recipes = lambda: [*recipes(), TEAR_DOWN]
    try:
        return tg.make_home(tmp_path_factory.mktemp("sanity-home"))
    finally:
        tg.recipes = recipes


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def mixer_ref(conn: sqlite3.Connection, eut: int, ticks: int) -> str:
    """The ref of the fixture's one mixer recipe that makes Dust B at ``eut`` x ``ticks``."""
    rows = conn.execute(
        "SELECT DISTINCT r.ref FROM recipe AS r"
        " JOIN recipe_map AS m ON m.id = r.recipe_map_id"
        " JOIN recipe_output AS o ON o.recipe_id = r.id JOIN item AS i ON i.id = o.item_id"
        " WHERE m.unloc_name = 'gt.recipe.mixer' AND r.eut = ? AND r.duration_ticks = ?"
        " AND i.registry_name || '@' || i.meta = ?",
        (eut, ticks, DUST_B),
    ).fetchall()
    assert len(rows) == 1, rows
    return str(rows[0][0])


def tear_down_ref(conn: sqlite3.Connection) -> str:
    return mixer_ref(conn, 1, 10)


def test_a_recipe_using_the_target_up_is_a_loop_not_a_source(
    conn: sqlite3.Connection,
) -> None:
    loop = tear_down_ref(conn)
    g = graph.build(conn, Request(WIDGET), tg.Base(), templates=chains.EMPTY)
    dust_b = g.item(DUST_B)
    assert (
        DUST_B in g.primary
    )  # a direct input of the target: it expands its top recipes
    assert loop not in dust_b.chosen and dust_b.chosen
    cand = next(c for c in dust_b.candidates if c.recipe == loop)
    assert cand.status == "alternative"
    assert "the reverse of one of its uses" in cand.reason
    assert cand is not dust_b.candidates[0]  # ranked after the real way to make it
    # For a request whose target isn't made from Dust B, the same recipe is a plain way to
    # make Dust B again (the loop guard is about the request's own target only).
    g2 = graph.build(conn, Request(DUST_B), tg.Base(), templates=chains.EMPTY)
    assert loop in [c.recipe for c in g2.item(DUST_B).candidates]
    assert g2.item(DUST_B).candidates[0].recipe == loop


def test_a_plan_importing_what_it_cannot_make_warns(conn: sqlite3.Connection) -> None:
    logs = mixer_ref(conn, 16, 60)  # mixer/1: Dust B from logs
    loop = tear_down_ref(conn)
    # mixer/0 (Dust A + Dust B) pinned, with both of Dust B's recipes this player can run
    # left out: Dust B is locked, so the route imports it.
    pin = Pin(WIDGET, tg.ref_of(conn, "mixer", 0))
    req = Request(
        WIDGET, constraints=Constraints(exclude=(logs, loop)), pins=(pin,), routes=1
    )
    d = plan.design(conn, req, tg.Base(), templates=chains.EMPTY)
    assert d.plans
    for p in d.plans:
        imported = {b["id"] for b in p["boundary"]["in"]}
        warned = [w for w in p["warnings"] if w.startswith(f"imports {DUST_B} (locked")]
        assert bool(warned) == (DUST_B in imported), (imported, p["warnings"])
    assert DUST_B in {b["id"] for b in d.plans[0]["boundary"]["in"]}
