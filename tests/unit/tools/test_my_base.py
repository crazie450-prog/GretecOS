"""WO-3.8: My base's state (gregtec.base.state) and the base_get tool, over the tool tests' DB.

Defaults (D63, D64): the quests' tier, nothing owned, nothing stocked, no lines. Edits keyed on
stable keys (GT mte ids, item and fluid keys, D210); which source's machine count is used (D211);
the tier override; and that all of it outlives ``gos db build`` and a restart. WO-3.3's graph
builder isn't on main yet, so the boundary is checked through what the designer reads
(``BaseInputs``) and through the machine model's ``require_owned``; WO-3.7 adds the
end-to-end plan test.
"""

from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest

from gregtec import store
from gregtec.agents.tools import LATER, TOOLS, build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.base import state
from gregtec.db import access, build
from gregtec.db import quests as quest_db
from gregtec.planner import machines as m

from .conftest import GT, JET_FUEL, KEY, STEEL, Runner

EBF, MACERATOR, BBF = 1000, 31, 140
EBF_RECIPE = "r:gt.recipe.blastfurnace/9c40869458cb"


@runtime_checkable
class BaseInputs(Protocol):
    """WO-3.3's interface (core/gregtec/planner/request.py), as the work orders give it."""

    def tier_cap(self) -> int: ...
    def owned_machines(self) -> Mapping[int, int]: ...
    def stock(self) -> Mapping[str, float]: ...
    def line_outputs(self) -> Mapping[str, float]: ...


@pytest.fixture
def home(fresh_db: Path) -> Path:
    return fresh_db.parents[1]


@pytest.fixture
def conn(fresh_db: Path) -> Iterator[sqlite3.Connection]:
    c = store.connect(
        fresh_db
    )  # the core's connection: My base's tables made if missing
    try:
        yield c
    finally:
        c.close()


# --- defaults ----------------------------------------------------------------------------------


def test_nothing_owned_or_stocked_by_default(conn: sqlite3.Connection) -> None:
    """D63 (none stocked by default) and D64 (tier from the quests)."""
    base = state.MyBase.from_db(conn)
    assert isinstance(base, BaseInputs)
    assert base.tier_cap() == 0  # the book's stone stage: ULV
    assert base.owned_machines() == {}
    assert base.stock() == {}
    assert base.line_outputs() == {}
    snap = state.snapshot(conn)
    assert snap["tier"]["stage"] == "stone" and snap["tier"]["how"] == "quests"
    assert snap["machines"] == [] and snap["stock"] == [] and snap["lines"] == []
    assert snap["has_db"] is True


def test_my_base_implements_the_interface_exactly() -> None:
    """The four methods with exactly WO-3.3's signatures (structural typing)."""
    for name in ("tier_cap", "owned_machines", "stock", "line_outputs"):
        want = inspect.signature(getattr(BaseInputs, name))
        got = inspect.signature(getattr(state.MyBase, name))
        assert list(got.parameters) == list(want.parameters) == ["self"]
        assert str(got.return_annotation) == str(want.return_annotation)


def test_a_db_without_my_base_tables_reads_as_empty(tool_db: Path) -> None:
    """The game DB opened read-only before the core ever ran: nothing owned, nothing stocked."""
    c = access.connect(tool_db, readonly=True)
    try:
        if state.has_table(c, state.MACHINE_TABLE):
            pytest.skip("this DB already has My base's tables")
        assert state.machines(c) == [] and state.stock(c) == []
        assert m.owned_machines(c) == frozenset()
        with pytest.raises(state.BaseError, match="gregtec.store.connect"):
            state.clear_machine(c, EBF)
    finally:
        c.close()


# --- owned machines ----------------------------------------------------------------------------


def test_a_manual_count_is_kept_by_mte_id(conn: sqlite3.Connection) -> None:
    got = state.set_machine(conn, EBF, 2, now=100)
    assert (got.mte_id, got.count, got.source, got.upd) == (EBF, 2, "manual", 100)
    assert got.name == "Electric Blast Furnace" and got.multiblock is True
    assert state.owned_machines(conn) == {EBF: 2}
    rows = conn.execute("SELECT mte_id, source, count FROM my_base_machine").fetchall()
    assert [tuple(r) for r in rows] == [
        (EBF, "manual", 2)
    ]  # the GT id, never machine.id
    state.set_machine(conn, EBF, 3)
    assert state.owned_machines(conn) == {EBF: 3}


@pytest.mark.parametrize(
    ("mte_id", "count", "why"),
    [
        (99999, 1, "isn't a machine in the current DB"),
        (EBF, -1, "count must be 0 to"),
        (EBF, 1.5, "whole number"),
        (EBF, True, "whole number"),
        (EBF, state.MAX_COUNT + 1, "count must be 0 to"),
        ("1000", 1, "mte_id must be"),
        (None, 1, "mte_id must be"),
        (-5, 1, "mte_id must be"),
    ],
)
def test_bad_machine_edits_are_refused(
    conn: sqlite3.Connection, mte_id: object, count: object, why: str
) -> None:
    with pytest.raises(state.BaseError, match=why):
        state.set_machine(conn, mte_id, count)  # type: ignore[arg-type]
    assert state.machines(conn) == []


def test_unknown_source_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(state.BaseError, match="source must be"):
        state.set_machine(conn, EBF, 1, source="guess")


def test_which_source_counts(conn: sqlite3.Connection) -> None:
    """D211: manual, else census, else events; a manual 0 says "none"."""
    state.set_machine(conn, MACERATOR, 4, source="event")
    assert state.owned_machines(conn) == {MACERATOR: 4}
    state.set_machine(conn, MACERATOR, 2, source="census")
    assert state.owned_machines(conn) == {MACERATOR: 2}
    state.set_machine(conn, MACERATOR, 0)
    assert state.owned_machines(conn) == {}
    (row,) = state.machines(conn)
    assert row.source == "manual" and row.sources == {
        "manual": 0,
        "event": 4,
        "census": 2,
    }
    assert state.clear_machine(conn, MACERATOR) is True
    assert state.owned_machines(conn) == {MACERATOR: 2}  # the census applies again
    assert state.clear_machine(conn, MACERATOR) is False


# --- stock -------------------------------------------------------------------------------------


def test_stock_is_kept_by_stable_key(conn: sqlite3.Connection) -> None:
    got = state.set_stock(conn, STEEL, 64, now=5)
    assert (got.key, got.qty, got.kind, got.unit, got.name) == (
        STEEL,
        64.0,
        "item",
        "items",
        "Steel Ingot",
    )
    fluid = state.set_stock(conn, f"fluid:{JET_FUEL}", 8000)
    assert (fluid.kind, fluid.unit, fluid.name) == ("fluid", "L", "Jet Fuel A")
    assert state.MyBase.from_db(conn).stock() == {
        STEEL: 64.0,
        f"fluid:{JET_FUEL}": 8000.0,
    }
    state.set_stock(conn, STEEL, 32)
    assert state.MyBase.from_db(conn).stock()[STEEL] == 32.0
    assert state.remove_stock(conn, STEEL) is True
    assert state.remove_stock(conn, STEEL) is False
    assert state.MyBase.from_db(conn).stock() == {f"fluid:{JET_FUEL}": 8000.0}


@pytest.mark.parametrize(
    ("key", "qty", "why"),
    [
        (f"{GT}@99999", 1, "isn't an item or fluid key"),
        ("Steel Ingot", 1, "isn't an item or fluid key"),  # a name, not a key
        ("fluid:nope", 1, "isn't an item or fluid key"),
        (STEEL, 0, "above 0"),
        (STEEL, -3, "above 0"),
        (STEEL, float("nan"), "above 0"),
        (STEEL, float("inf"), "above 0"),
        (STEEL, "64", "must be a number"),
        (STEEL, True, "must be a number"),
        ("", 1, "must be an item or fluid key"),
        (None, 1, "must be an item or fluid key"),
        ("x" * (state.MAX_KEY + 1), 1, "must be an item or fluid key"),
    ],
)
def test_bad_stock_edits_are_refused(
    conn: sqlite3.Connection, key: object, qty: object, why: str
) -> None:
    with pytest.raises(state.BaseError, match=why):
        state.set_stock(conn, key, qty)  # type: ignore[arg-type]
    assert state.stock(conn) == []


def test_a_key_the_db_no_longer_has_stays_listed(conn: sqlite3.Connection) -> None:
    """A pack update can drop an item: its row stays, shown as not in the current DB."""
    conn.execute(
        "INSERT INTO my_base_stock(key, qty, upd) VALUES (?, 3, 0)", ("gone:thing@0",)
    )
    (row,) = state.stock(conn)
    assert row.name is None and row.key == "gone:thing@0"
    assert state.MyBase.from_db(conn).stock() == {"gone:thing@0": 3.0}


# --- tier --------------------------------------------------------------------------------------


def test_the_tier_override_is_set_and_cleared(conn: sqlite3.Connection) -> None:
    t = state.set_tier(conn, "HV")
    assert (t.stage, t.tier, t.how, t.detected) == ("HV", 3, "override", "stone")
    assert state.MyBase.from_db(conn).tier_cap() == 3
    assert quest_db.effective_tier(conn) == ("HV", "override", "stone")  # WO-1.5's own
    t = state.set_tier(conn, None)
    assert (t.stage, t.tier, t.how) == ("stone", 0, "quests")
    with pytest.raises(state.BaseError, match="unknown stage"):
        state.set_tier(conn, "hv")


# --- outliving a rebuild and a restart -----------------------------------------------------------


def test_my_base_outlives_gos_db_build_and_a_restart(
    home: Path, fresh_db: Path
) -> None:
    c = store.connect(fresh_db)
    try:
        state.set_tier(c, "EV")
        state.set_machine(c, EBF, 2)
        state.set_stock(c, STEEL, 10)
        before = state.snapshot(c)
    finally:
        c.close()
    build.build(home)  # the same run, written again
    c = store.connect(fresh_db)  # a restarted core
    try:
        after = state.snapshot(c)
        base = state.MyBase.from_db(c)
    finally:
        c.close()
    assert after == before
    assert (base.tier_cap(), base.owned_machines(), base.stock()) == (
        4,
        {EBF: 2},
        {STEEL: 10.0},
    )


# --- the designer's inputs -----------------------------------------------------------------------


def test_owned_machines_change_what_the_machine_model_allows(
    conn: sqlite3.Connection,
) -> None:
    """The planner's own reader (machines.owned_machines) follows My base, so a request with
    require_owned gets the EBF only once Clayton ticks it."""

    def ebf_options() -> set[int]:
        player = m.Player(
            tier=state.MyBase.from_db(conn).tier_cap(),
            owned=m.owned_machines(conn),
            require_owned=True,
        )
        return {o.mte_id for o in m.select(conn, EBF_RECIPE, player).options}

    state.set_tier(conn, "HV")
    assert ebf_options() == set()
    state.set_machine(conn, EBF, 1)
    assert ebf_options() == {EBF}  # one option per energy-hatch layout
    state.set_machine(conn, EBF, 0)
    assert ebf_options() == set()


def test_the_designer_sees_every_edit(conn: sqlite3.Connection) -> None:
    """What a plan's boundary is built from (plan 7.1 Request): stock and owned machines move
    with each edit, and one snapshot doesn't change under the designer."""
    first = state.MyBase.from_db(conn)
    state.set_stock(conn, STEEL, 16)
    state.set_machine(conn, BBF, 1)
    second = state.MyBase.from_db(conn)
    assert first.stock() == {} and first.owned_machines() == {}
    assert second.stock() == {STEEL: 16.0} and second.owned_machines() == {BBF: 1}
    second.stock()[STEEL] = 0  # type: ignore[index]  # a copy: the snapshot stays as it was
    assert second.stock() == {STEEL: 16.0}


# --- base_get ----------------------------------------------------------------------------------


def test_base_get_lists_tier_machines_and_stock(
    fresh_db: Path, make_runner: Callable[..., Runner], glossary: Glossary
) -> None:
    c = store.connect(fresh_db)
    try:
        empty = make_runner(build_registry(fresh_db, glossary, key=KEY))("base_get")
        assert empty.head["tier"] == "stone (GT voltage tier 0, quests)"
        assert empty.head["machines"] == "0" and empty.rows == []
        state.set_tier(c, "MV")
        state.set_machine(c, EBF, 2)
        state.set_stock(c, f"fluid:{JET_FUEL}", 1000)
    finally:
        c.close()
    run: Runner = make_runner(build_registry(fresh_db, glossary, key=KEY))
    p = run("base_get")
    assert p.head["tier"] == "MV (GT voltage tier 2, override)"
    assert p.head["detected"] == "stone"
    assert p.col("ref") == [f"mte:{EBF}", f"fluid:{JET_FUEL}"]
    assert p.col("amount") == ["2", "1000"]
    assert p.col("unit") == ["", "L"]


def test_base_get_is_read_only_and_in_both_lanes(reg: Registry) -> None:
    assert any(t.name == "base_get" for t in reg.tools("fast"))
    (tool,) = [t for t in TOOLS if t.name == "base_get"]
    assert tool.read_only and tool.lanes == frozenset({"fast", "deep"})
    assert "base_get" not in LATER
    assert not any(t.name == "base_set" for t in TOOLS)  # UI-only (plan 7.0)
