"""The power designer's objective modes (WO-4.2, D233-D235) on a real server harness DB.

As test_power_harness.py, these run only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the
harnesses; locally: mod/README.md). §8 row 4.2's acceptance, "the validator proves zero non-free
imports", on the D65 benchmark the plan names: steam boilers on a tree farm (G4), at EV:

- the self-sustaining designer finds the line (GT's Tree Growth Simulator from the export's
  ``gt.recipe.treefarm``, logs into a pyrolyse oven, its creosote into a 2.9 large boiler and a
  large steam turbine), and the validator, reading every recipe again from the DB, proves it
  takes nothing but water; its bootstrap kit names the sapling, the saw and starter creosote;
- the same line with its farm taken out (one hidden import: the logs) fails the validator;
- every mode returns 2-3 ranked options at HV, with the weights printed.

Nothing is written outside tmp_path.
"""

from __future__ import annotations

import copy
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import farms, power, sustain
from gregtec.planner.request import DefaultBaseInputs

REPO = Path(__file__).resolve().parents[2]
EV, HV = 4, 3
CREOSOTE = "fluid:creosote"


def _require_harness() -> None:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    _require_harness()
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("power-objectives")
    collect.collect(game, home, 1, allow_harness=True)
    result = build.build(home, allow_harness=True)
    assert result.loaded.power_status == "ok", result.loaded.power_error
    c = access.connect(access.db_path(home), readonly=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def tree_farm_line(conn: sqlite3.Connection) -> power.PowerDesign:
    req = power.PowerRequest(
        mode="self_sustaining", tier_cap=EV, kinds=("boiler_steam",), fuels=(CREOSOTE,)
    )
    return power.design(conn, req, DefaultBaseInputs(EV), with_effort=False)


def test_the_d65_tree_farm_boiler_line_is_proven_closed(
    conn: sqlite3.Connection, tree_farm_line: power.PowerDesign
) -> None:
    d = tree_farm_line
    assert 2 <= len(d.options) <= 3, d.rejected
    for p in d.options:
        kinds = {n["kind"] for n in p["nodes"]}
        assert {"recipe", "farm", "generator"} <= kinds
        farm = next(n for n in p["nodes"] if n["kind"] == "farm")
        assert farm["recipe"].startswith("farm:r:gt.recipe.treefarm/")
        assert farm["source"] == "db"
        assert any(n["recipe"].startswith("r:gt.recipe.pyro/") for n in p["nodes"])
        gen = next(n for n in p["nodes"] if n["kind"] == "generator")
        assert gen["fuel"]["id"] == CREOSOTE
        assert "MTELargeBoiler" not in gen["recipe"]  # cited by mte, never by class
        # The designer's proof, and the validator run again on its own.
        assert p["closed"]["ok"] and p["closed"]["imports"] == []
        fl, _ = farms.load(
            conn, int(conn.execute("SELECT MAX(id) FROM export_run").fetchone()[0])
        )
        proof = sustain.validate(conn, p, power.DEFAULT_FREE, {f.id: f for f in fl})
        assert proof.ok, proof
        assert {k for k, _ in proof.free_used} <= {"fluid:water"}
        assert proof.net_eut > 0 and p["power"]["net_eut"] > 0
        kit = p["bootstrap"]
        assert kit["seeds_saplings"] and kit["tools"] == [
            {"tool": "buzzsaw", "qty": farm["count"]}
        ]
        assert (
            kit["starter_fuel"][0]["id"] == CREOSOTE
            and kit["starter_fuel"][0]["qty"] > 0
        )
        assert any(x["part"].startswith("rotor:") for x in kit["parts"])
    assert d.weights.startswith("mode self_sustaining")


def test_the_same_line_without_its_farm_fails(
    conn: sqlite3.Connection, tree_farm_line: power.PowerDesign
) -> None:
    p = copy.deepcopy(tree_farm_line.options[0])
    p["nodes"] = [n for n in p["nodes"] if n["kind"] != "farm"]
    fl, _ = farms.load(
        conn, int(conn.execute("SELECT MAX(id) FROM export_run").fetchone()[0])
    )
    proof = sustain.validate(conn, p, power.DEFAULT_FREE, {f.id: f for f in fl})
    assert not proof.ok
    assert len(proof.imports) == 1  # the logs the farm grew
    assert "log" in proof.imports[0][0].lower()


@pytest.mark.parametrize("mode", power.MODES)
def test_every_mode_returns_two_or_three_ranked_options_at_hv(
    conn: sqlite3.Connection, mode: str
) -> None:
    req = power.PowerRequest(mode=mode, tier_cap=HV)
    d = power.design(
        conn, req, DefaultBaseInputs(HV), with_effort=mode == "output_per_effort"
    )
    assert 2 <= len(d.options) <= 3, (d.notes, d.rejected[:5])
    assert d.weights.startswith(f"mode {mode}")
    assert [p["rank"]["position"] for p in d.options] == list(
        range(1, len(d.options) + 1)
    )
    for p in d.options:
        assert p["power"]["net_eut"] > 0
        assert all(
            c.startswith(("mte:", "r:", "rotor:"))
            for c in p["power"]["generator"]["cites"]
        )
        if mode == "self_sustaining":
            assert p["closed"]["ok"]
    if mode == "max_output":
        nets = [p["power"]["net_eut"] for p in d.options]
        assert nets == sorted(nets, reverse=True)


def test_single_block_eu_per_fuel_is_gts_own(conn: sqlite3.Connection) -> None:
    # The review's HIGH-1: every single-block candidate burns GT's own getFuelValue per unit
    # (generator_gt's eu_long per consumed litre), truncated as GT truncates it, and the
    # validator derives the same ports on its own.
    run = int(conn.execute("SELECT MAX(id) FROM export_run").fetchone()[0])
    gens, _ = power.candidates(
        conn,
        run,
        power.PowerRequest(mode="self_sustaining", kinds=("single_block",)),
        EV,
    )
    checked = 0
    for g in gens:
        spec = dict(g.spec)
        fluid = g.fuel.removeprefix("fluid:")
        row = conn.execute(
            "SELECT gt.eu_long, gt.consumed FROM generator_gt AS gt JOIN fuel AS f"
            " ON f.run_id = gt.run_id AND f.ref = ? AND f.fuel_value = gt.special"
            " WHERE gt.run_id = ? AND gt.mte_id = ? AND gt.fluid = ?",
            (g.fuel_ref, run, spec["mte"], fluid),
        ).fetchone()
        if not g.fuel.startswith("fluid:") or row is None:
            continue
        assert g.eu_per_fuel == pytest.approx(row[0] / row[1], rel=1e-9), g.id
        p, why = sustain.gen_ports(conn, {"recipe": g.id, "spec": spec}, run)
        assert why is None and p is not None
        assert p.used[g.fuel] == pytest.approx(g.fuel_per_s, rel=1e-9), g.id
        checked += 1
    assert checked > 100


def test_the_validator_refuses_a_recipe_from_nothing(conn: sqlite3.Connection) -> None:
    # The review's MEDIUM-2 probe on the real DB: an enabled recipe the export lists with no
    # input it uses up, "making" a gas turbine's fuel, feeding the D65 line's generator.
    d = power.design(
        conn,
        power.PowerRequest(mode="best", tier_cap=HV, kinds=("gas_turbine",)),
        DefaultBaseInputs(HV),
        with_effort=False,
    )
    gen = next(n for n in d.options[0]["nodes"] if n["kind"] == "generator")
    fuel = gen["fuel"]["id"].removeprefix("fluid:")
    row = conn.execute(
        "SELECT r.ref FROM recipe AS r JOIN recipe_output AS ro ON ro.recipe_id = r.id"
        " JOIN fluid AS f ON f.id = ro.fluid_id WHERE r.enabled = 1 AND f.name = ?"
        " AND NOT EXISTS (SELECT 1 FROM recipe_input AS ri WHERE ri.recipe_id = r.id"
        " AND ri.consumed = 1 AND ri.amount > 0) LIMIT 1",
        (fuel,),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT r.ref FROM recipe AS r JOIN recipe_output AS ro ON ro.recipe_id = r.id"
            " WHERE r.enabled = 1 AND ro.fluid_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM"
            " recipe_input AS ri WHERE ri.recipe_id = r.id AND ri.consumed = 1"
            " AND ri.amount > 0) LIMIT 1"
        ).fetchone()
    assert row is not None, (
        "the export has no recipe from nothing (good: nothing to probe)"
    )
    plan = {
        "run": d.options[0]["run"],
        "nodes": [{"recipe": row[0], "crafts_s": 1.0}, gen],
        "boundary": {"in": [], "out": []},
    }
    proof = sustain.validate(conn, plan, power.DEFAULT_FREE)
    assert not proof.ok
    assert any(f"{row[0]} uses up nothing" in x for x in proof.problems)
