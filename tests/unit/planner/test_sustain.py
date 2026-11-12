"""The self-sustaining validator and bootstrap kit (WO-4.2, D234), on hand-built plans: no
solver involved, so the proof stands on its own. The acceptance cases of §8 row 4.2: a truly
closed line passes, a line with one hidden import fails. Fixture: tests/unit/planner/test_power.py
(made-up items, recipes and numbers; no pack data)."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.db import access
from gregtec.planner import sustain


def _power_fixture() -> ModuleType:
    path = Path(__file__).with_name("test_power.py")
    spec = importlib.util.spec_from_file_location("_wo42_power_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tp = _power_fixture()
tg = tp.tg
GAS, ACID, WATER, LOG_A = tp.GAS, tp.ACID, tp.WATER, tp.LOG_A
FREE = tp.FREE


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tp.make_home(tmp_path_factory.mktemp("sustain-home"))


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def gas_fuel(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT ref FROM fuel WHERE fluid = 'testgas'").fetchone()
    return str(row[0])


def loop_plan(conn: sqlite3.Connection, cr: float = 0.016) -> dict[str, Any]:
    """A line written by hand: the gas loop (chemicalreactor/3: 1000 gas + 1000 water -> 2000
    acid; electrolyzer/1: 1000 acid -> 1000 gas) feeding the generator 16 L/s of gas (640 EU/s).
    Balanced at cr = 0.016 crafts/s: acid 2000 x 0.016 = 1000 x 0.032, gas 1000 x 0.032 -
    1000 x 0.016 = 16."""
    return {
        "run": tg.RUN,
        "nodes": [
            {"recipe": tg.ref_of(conn, "chemicalreactor", 3), "crafts_s": cr,
             "eut_avg": 1.0, "count": 1, "ticks_eff": 110},
            {"recipe": tg.ref_of(conn, "electrolyzer", 1), "crafts_s": 0.032,
             "eut_avg": 2.0, "count": 1, "ticks_eff": 100},
            {"recipe": "gen:single/mte:110/test", "crafts_s": 1.0, "count": 1,
             "ports": {"in": {GAS: 16.0}, "out": {"eu": 640.0}},
             "spec": {"kind": "single_block", "mte": tp.GENERATOR,
                      "fuel_ref": gas_fuel(conn)},
             "fuel": {"id": GAS, "rate_s": 16.0}, "parts": {"rotor:Test/large": 1}},
        ],
        "boundary": {"in": [{"id": WATER, "rate_s": 16.0}], "out": []},
    }  # fmt: skip


def test_a_truly_closed_line_is_proven(conn: sqlite3.Connection) -> None:
    proof = sustain.validate(conn, loop_plan(conn), FREE)
    assert proof.ok, proof
    assert proof.imports == () and proof.problems == ()
    assert proof.free_used == ((WATER, pytest.approx(16.0)),)
    assert proof.net_eut == pytest.approx(32 - 3)  # 640 EU/s out, 3 EU/t drawn
    bal = dict(proof.balance)
    assert bal[GAS] == 0.0 and bal[ACID] == 0.0
    assert proof.as_dict()["ok"] is True


def test_a_line_with_one_hidden_import_fails(conn: sqlite3.Connection) -> None:
    # The reactor runs at half rate: the electrolyzer's acid is short, and nothing in the plan
    # says so (its boundary lists water only). The DB's recipes give it away.
    proof = sustain.validate(conn, loop_plan(conn, cr=0.008), FREE)
    assert not proof.ok
    assert [k for k, _ in proof.imports] == [ACID]
    assert proof.imports[0][1] == pytest.approx(1000 * 0.032 - 2000 * 0.008)
    # A node whose input nothing makes: Dust B for mixer/7, hidden too.
    plan = loop_plan(conn)
    plan["nodes"].append({"recipe": tp._mixer7(conn), "crafts_s": 0.01, "eut_avg": 0.1})
    proof = sustain.validate(conn, plan, FREE)
    assert [k for k, _ in proof.imports] == [tg.DUST_B]
    # The free set decides: with water not free, water is an import.
    proof = sustain.validate(conn, loop_plan(conn), ("fluid:air",))
    assert [k for k, _ in proof.imports] == [WATER] and not proof.ok
    # A trickle is an import, whatever the EU flow's size: Dust B at 1e-9 crafts/s.
    plan = loop_plan(conn)
    plan["nodes"].append({"recipe": tp._mixer7(conn), "crafts_s": 1e-9, "eut_avg": 0.0})
    proof = sustain.validate(conn, plan, FREE)
    assert [k for k, _ in proof.imports] == [tg.DUST_B] and not proof.ok


def test_generator_ports_are_derived_not_declared(conn: sqlite3.Connection) -> None:
    # The validator derives the generator's ports from the DB by its spec (GT's EU per litre
    # at the block's 32 EU/t: 16 L/s of gas), whatever the node declares.
    p, why = sustain.gen_ports(conn, loop_plan(conn)["nodes"][2])
    assert why is None and p is not None
    assert p.used == {GAS: pytest.approx(16.0)} and p.given == {"eu": 640.0}
    # A declared fuel rate GT doesn't give (too little fuel for 32 EU/t) is named.
    plan = loop_plan(conn)
    plan["nodes"][2]["ports"]["in"][GAS] = 12.0
    proof = sustain.validate(conn, plan, FREE)
    assert not proof.ok and "declares other ports than GT" in proof.problems[0]
    assert f"uses {GAS} 12/unit, GT gives 16" in proof.problems[0]
    # So is a hidden extra input, or none at all.
    for ins in ({GAS: 16.0, tg.WIDGET: 1e-6}, {}):
        plan = loop_plan(conn)
        plan["nodes"][2]["ports"]["in"] = ins
        assert not sustain.validate(conn, plan, FREE).ok
    # Without a spec, or citing a fuel the DB hasn't, nothing is taken on trust.
    plan = loop_plan(conn)
    del plan["nodes"][2]["spec"]
    proof = sustain.validate(conn, plan, FREE)
    assert not proof.ok and "no spec" in proof.problems[0]
    plan = loop_plan(conn)
    plan["nodes"][2]["spec"]["fuel_ref"] = (
        "r:gt.recipe.dieselgeneratorfuel/000000000000"
    )
    assert "not in the DB" in sustain.validate(conn, plan, FREE).problems[0]
    plan = loop_plan(conn)
    plan["nodes"][2]["spec"]["mte"] = tp.TREE_FARM
    assert "not a single-block" in sustain.validate(conn, plan, FREE).problems[0]
    plan = loop_plan(conn)
    plan["nodes"][2]["spec"]["kind"] = "warp_core"
    assert "unknown generator kind" in sustain.validate(conn, plan, FREE).problems[0]
    # A node may leave its ports out: the derived ones count.
    plan = loop_plan(conn)
    del plan["nodes"][2]["ports"]
    assert sustain.validate(conn, plan, FREE).ok


def test_a_fuel_from_another_map_is_refused(db: Path, tmp_path: Path) -> None:
    # The final check's probe: a fuel row the generator doesn't burn (another map's, here a
    # plasma fuel worth far more EU per litre) cited on the diesel-map block. GT wouldn't accept
    # it, so the validator mustn't size the block on it.
    copy = tmp_path / "gos.db"
    copy.write_bytes(db.read_bytes())
    c = sqlite3.connect(copy)
    try:
        c.execute(
            "INSERT INTO fuel SELECT run_id, recipe_id + 100000, 'r:gt.recipe."
            "plasmageneratorfuels/0000000000aa', 'gt.recipe.plasmageneratorfuels',"
            " 'plasmaGeneratorFuels', input, fluid, fluid_input, hidden, 8000, unit, 8000"
            " FROM fuel WHERE fluid = 'testgas'"
        )
        c.commit()
        plan = loop_plan(c)
        plan["nodes"][2]["spec"]["fuel_ref"] = (
            "r:gt.recipe.plasmageneratorfuels/0000000000aa"
        )
        del plan["nodes"][2]["ports"]
        p, why = sustain.gen_ports(c, plan["nodes"][2])
        assert p is None and why is not None
        assert "is not a fuel of mte:" in why
        proof = sustain.validate(c, plan, FREE)
        assert not proof.ok and "is not a fuel of mte:" in proof.problems[0]
        # Its own map's row still passes.
        assert sustain.validate(c, loop_plan(c), FREE).ok
    finally:
        c.close()


def test_a_recipe_from_nothing_is_never_counted_on(conn: sqlite3.Connection) -> None:
    # The review's probe: a GT recipe the export lists with no input it uses up (here a kept
    # circuit only), "making" the generator's fuel. The sums close; the proof still fails.
    plan = loop_plan(conn)
    plan["nodes"] = [
        {"recipe": "r:gt.recipe.multicentrifuge/fromnothing", "crafts_s": 1.0},
        plan["nodes"][2],
    ]
    plan["boundary"]["in"] = []

    def read(n: Mapping[str, Any]) -> tuple[sustain.Ports | None, str | None]:
        if str(n["recipe"]).startswith("r:"):
            return sustain.Ports({}, {GAS: 16.0}, {f"{tg.CIRCUIT}@1": 1.0}), None
        return sustain.node_ports(conn, n, {})

    proof = sustain.validate(conn, plan, FREE, ports=read)
    assert proof.imports == () and not proof.ok
    assert proof.problems == (
        f"r:gt.recipe.multicentrifuge/fromnothing uses up nothing but gives {GAS}",
    )


def test_surplus_needs_a_sink(conn: sqlite3.Connection) -> None:
    plan = loop_plan(conn, cr=0.017)  # a little more acid than the electrolyzer takes
    proof = sustain.validate(conn, plan, FREE)
    assert [k for k, _ in proof.surplus] == [ACID]
    assert proof.as_dict()["surplus"][0]["note"] == "needs a sink (void or storage)"


def test_eu_declared_imports_and_node_sources(conn: sqlite3.Connection) -> None:
    plan = loop_plan(conn)
    plan["nodes"][0]["eut_avg"] = 31.0  # 32 EU/t out, 33 drawn
    proof = sustain.validate(conn, plan, FREE)
    assert not proof.ok and "not above 0" in proof.problems[0]
    plan = loop_plan(conn)
    plan["boundary"]["in"].append({"id": ACID, "rate_s": 1.0})
    proof = sustain.validate(conn, plan, FREE)
    assert not proof.ok and proof.problems == (
        f"the plan declares {ACID} as an import",
    )
    plan = loop_plan(conn)
    plan["nodes"] += [
        {"recipe": "r:gt.recipe.mixer/000000000000", "crafts_s": 1.0},
        {"recipe": f"member:ore:logTest|{tg.WIDGET}", "crafts_s": 1.0},
        {"recipe": "farm:nowhere", "crafts_s": 1.0},
        {"recipe": "x:what", "crafts_s": 1.0},
        {"recipe": "r:never-runs", "crafts_s": 0.0},
    ]
    problems = sustain.validate(conn, plan, FREE).problems
    assert len(problems) == 4
    assert "not in the DB" in problems[0] and "not a member" in problems[1]
    assert "no such farm" in problems[2] and "unknown node kind" in problems[3]
    assert sustain.is_member(conn, "ore:logTest", LOG_A)
    assert sustain.is_member(conn, f"{tg.WILD}@32767", f"{tg.WILD}@2")
    assert not sustain.is_member(conn, f"{tg.WILD}@32767", LOG_A)
    assert not sustain.is_member(conn, LOG_A, LOG_A)
    # A test can hand the validator its own port reader.
    proof = sustain.validate(
        conn, loop_plan(conn), FREE, ports=lambda n: (None, "no reader")
    )
    assert proof.problems.count("no reader") == 3


def test_db_ports_and_the_bootstrap_kit(conn: sqlite3.Connection) -> None:
    mixer0 = tg.ref_of(conn, "mixer", 0)
    p = sustain.db_ports(conn, mixer0)
    assert p is not None
    assert p.used == {tg.DUST_A: 2.0, tg.DUST_B: 1.0}
    assert p.given == {tg.WIDGET: 1.0} and p.kept == {f"{tg.CIRCUIT}@1": 1.0}
    assert p.cycle_s == 5.0
    chance = sustain.db_ports(conn, tg.ref_of(conn, "electrolyzer", 0))
    assert chance is not None and chance.given == {tg.WIDGET: 0.5}
    assert sustain.db_ports(conn, mixer0, run_id=999) is None
    plan = loop_plan(conn)
    plan["nodes"].append({"recipe": mixer0, "crafts_s": 0.01, "count": 3})
    kit = sustain.bootstrap(conn, plan)
    assert kit["kept_inputs"] == [{"id": f"{tg.CIRCUIT}@1", "qty": 3}]
    assert kit["parts"] == [{"part": "rotor:Test/large", "qty": 1}]
    # Starter fuel: 16 L/s over every node's cycle (5.5 + 5 + 5 s).
    assert kit["starter_fuel"] == [
        {"id": GAS, "qty": 248, "startup_s": 15.5,
         "note": "an upper bound: every node's cycle, end to end"}
    ]  # fmt: skip
    assert kit["seeds_saplings"] == [] and kit["tools"] == []
