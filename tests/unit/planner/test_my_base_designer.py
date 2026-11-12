"""WO-3.8's acceptance (plan §8): a plan's boundary changes when My base's stock or owned
machines change, end to end through WO-3.3's graph builder and the `gos graph` and `gos plan`
CLIs, which build with :class:`gregtec.base.state.MyBase` (D211).

On test_graph's fixture DB, written by the real ``gos db build``; My base is edited through
:mod:`gregtec.base.state` on the core's connection, as the dashboard edits it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec import store
from gregtec.base import state
from gregtec.base.state import MyBase
from gregtec.db import access
from gregtec.planner import graph
from gregtec.planner.request import Constraints, Request

from .test_graph import MV, PLATE, WIDGET, make_home, ref_of
from .test_graph import run_cli as graph_cli
from .test_plan import run_cli as plan_cli

CHEMICAL_REACTOR = 102  # the fixture's LV chemical reactor (mte id)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    make_home(tmp_path)
    return tmp_path


@pytest.fixture
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = store.connect(access.db_path(home))  # the core's: My base's tables made
    try:
        yield c
    finally:
        c.close()


def widget(conn: sqlite3.Connection, **kw: object) -> graph.Graph:
    return graph.build(conn, Request(WIDGET, tier_cap=MV, **kw), MyBase.from_db(conn))  # type: ignore[arg-type]


def test_stock_makes_a_boundary(conn: sqlite3.Connection) -> None:
    assert widget(conn).item(PLATE).boundary is None  # made in the line
    state.set_stock(conn, PLATE, 64)
    plate = widget(conn).item(PLATE)
    assert plate.boundary == "stock" and not plate.chosen
    state.remove_stock(conn, PLATE)
    assert widget(conn).item(PLATE).boundary is None


def test_owned_machines_change_the_chosen_recipes(conn: sqlite3.Connection) -> None:
    owned_only = Constraints(require_owned=True)

    def chosen() -> list[str]:
        g = widget(conn, constraints=owned_only)
        return [c.recipe for c in g.item(WIDGET).candidates if c.status == "chosen"]

    assert chosen() == []  # nothing owned (D63)
    state.set_machine(conn, CHEMICAL_REACTOR, 1)
    assert chosen() == [
        ref_of(conn, "chemicalreactor", 5),
        ref_of(conn, "chemicalreactor", 0),
    ]
    state.set_machine(conn, CHEMICAL_REACTOR, 0)  # a manual 0 says "none" (D211)
    assert chosen() == []


def test_the_tier_override_sets_the_cap(conn: sqlite3.Connection) -> None:
    def cap() -> int:
        return graph.build(conn, Request(WIDGET), MyBase.from_db(conn)).tier_cap

    assert cap() == 0  # the quests' stone stage
    state.set_tier(conn, "MV")
    assert cap() == MV


def _boundary_in(text: str) -> list[set[str]]:
    return [
        {b["id"] for b in json.loads(line)["boundary"]["in"]}
        for line in text.splitlines()
    ]


def test_gos_graph_and_gos_plan_read_my_base(
    home: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLIs open the DB read-only and see what the dashboard wrote."""
    code, text = plan_cli(home, monkeypatch, WIDGET, "--tier", "MV", "--json")
    assert code == 0 and not any(PLATE in b for b in _boundary_in(text))
    state.set_stock(conn, PLATE, 64)
    code, text = plan_cli(home, monkeypatch, WIDGET, "--tier", "MV", "--json")
    assert code == 0
    assert PLATE in _boundary_in(text)[0]  # the best route takes the stock
    code, text = graph_cli(home, monkeypatch, WIDGET, "--tier", "2", "--json")
    items = {i["id"]: i for i in json.loads(text)["items"]}
    assert code == 0 and items[PLATE]["boundary"] == "stock"
