"""The staged MILP and plan JSON (WO-3.4) on a real harness DB: the dev runtime's recipes and machines.

As test_graph_harness.py, it runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the
server harness; locally: mod/README.md), and writes nothing outside tmp_path. §8 row 3.4:

- the platline (platinum dust at IV): every route runs every machine with at most 2 externals
  (gates);
- the light-fuel line at MV closes with 0 gates on its best route;
- 12 identical re-solves give byte-identical plan JSON;
- pinning a non-primary node changes only its branch;
- every other G3 line the dev runtime has solves with every machine running, and WO-1.9's
  affected-plan check reads the plans and finds nothing changed against their own run.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import jsonschema
import pytest

from gregtec.db import access
from gregtec.ingest import collect
from gregtec.packupdate import plans as pack_plans
from gregtec.planner import alternatives, plan
from gregtec.planner.request import DefaultBaseInputs, Request

REPO = Path(__file__).resolve().parents[2]
PLATINUM = "gregtech:gt.metaitem.01@2085"
LIGHT_FUEL = "fluid:liquid_light_fuel"
IV, MV = 5, 2
LIMIT_S = (
    10.0  # a design of three routes; the platline takes about 1 s on the dev container
)
G3 = {
    "gregtech:gt.metaitem.01@11305": MV,  # steel (EBF)
    "gregtech:gt.metaitem.01@2067": 6,  # neodymium (rare earths)
    "gregtech:gt.metaitem.01@2324": 7,  # naquadah
    "fluid:molten.plastic": MV,  # polyethylene
    "fluid:sulfuricacid": MV,
    "gregtech:gt.metaitem.03@32030": 3,  # monocrystalline silicon boule
    "gregtech:gt.metaitem.01@17880": MV,  # rubber sheet
    "fluid:molten.epoxid": 4,
    "gregtech:gt.metaitem.01@11306": 3,  # stainless steel ingot
    "fluid:nitricacid": 3,
    "fluid:molten.polybenzimidazole": IV,
}
SCHEMA = json.loads(plan.SCHEMA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def conn(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Iterator[sqlite3.Connection]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("plan-harness")
    built_home(game, home)
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


def design(conn: sqlite3.Connection, request: Request) -> plan.Design:
    return plan.design(conn, request, DefaultBaseInputs(0))


def run_key(conn: sqlite3.Connection) -> str:
    return str(
        conn.execute("SELECT run_key FROM export_run ORDER BY id DESC").fetchone()[0]
    )


def test_platline_all_machines_run_with_at_most_two_externals(
    conn: sqlite3.Connection,
) -> None:
    started = time.perf_counter()
    d = design(conn, Request(PLATINUM, tier_cap=IV))
    took = time.perf_counter() - started
    print(f"platline design: {took:.2f} s, {[len(p['nodes']) for p in d.plans]} nodes")
    assert took < LIMIT_S
    assert len(d.plans) >= 2
    for p in d.plans:
        jsonschema.Draft202012Validator(SCHEMA).validate(p)
        assert p["route"]["all_run"], p["warnings"]
        assert len(p["route"]["gates"]) <= 2, p["route"]["gates"]
        assert p["run"] == run_key(conn) and p["target"]["rate_s"] == pytest.approx(1.0)
        assert all(n["count"] >= 1 for n in p["nodes"] if n["machine"])
    choices = [json.dumps(p["route"]["choices"]) for p in d.plans]
    assert len(set(choices)) == len(choices)
    assert_distinct_routes(d)


def assert_distinct_routes(d: plan.Design) -> None:
    """Review HIGH-1: each route runs its own recipes and names only choices it runs."""
    recipes = [frozenset(n["recipe"] for n in p["nodes"]) for p in d.plans]
    assert len(set(recipes)) == len(recipes), [p["route"]["choices"] for p in d.plans]
    for p, ids in zip(d.plans, recipes, strict=True):
        assert {c["recipe"] for c in p["route"]["choices"]} <= ids


def test_light_fuel_closes_with_no_gates(conn: sqlite3.Connection) -> None:
    d = design(conn, Request(LIGHT_FUEL, tier_cap=MV, qty=1000))
    best = d.plans[0]
    assert best["route"]["gates"] == [] and best["route"]["all_run"]


def test_twelve_identical_resolves(conn: sqlite3.Connection) -> None:
    first = [plan.dumps(p) for p in design(conn, Request(PLATINUM, tier_cap=IV)).plans]
    for _ in range(11):
        again = [
            plan.dumps(p) for p in design(conn, Request(PLATINUM, tier_cap=IV)).plans
        ]
        assert again == first


def test_pinning_a_non_primary_node_changes_only_its_branch(
    conn: sqlite3.Connection,
) -> None:
    request = Request(PLATINUM, tier_cap=IV, routes=1)
    before = design(conn, request)
    g = before.graph
    route = before.plans[0]
    used = sorted({k for n in route["nodes"] for k in n["for"]} - set(g.primary))
    tried = 0
    for key in used:
        others = [
            a["recipe"]
            for a in alternatives.node_alternatives(g, key)
            if a["status"] == "alternative"
        ]
        for other in others[:1]:
            tried += 1
            after = design(conn, alternatives.pin(request, key, other))
            branch = alternatives.branch(g, key) | alternatives.branch(after.graph, key)
            old = {n["recipe"]: n for n in route["nodes"]}
            new = {n["recipe"]: n for n in after.plans[0]["nodes"]}
            for ref in set(old) ^ set(new):
                assert set((old.get(ref) or new[ref])["for"]) <= branch, (key, ref)
            for ref in set(old) & set(new):
                if not set(old[ref]["for"]) & branch:
                    assert old[ref]["machine"] == new[ref]["machine"]
            assert new[other]["pinned"]
            pinned_choice = {"node": key, "recipe": other}
            kept = [c for c in after.plans[0]["route"]["choices"] if c != pinned_choice]
            assert kept == route["route"]["choices"]
        if tried >= 3:
            break
    assert tried >= 3


@pytest.mark.parametrize(("target", "tier"), sorted(G3.items()))
def test_g3_lines_run_every_machine(
    conn: sqlite3.Connection, target: str, tier: int, tmp_path: Path
) -> None:
    d = design(conn, Request(target, tier_cap=tier))
    folder = tmp_path / "plans"
    folder.mkdir()
    assert_distinct_routes(d)
    for p in d.plans:
        assert p["route"]["all_run"], (target, p["warnings"])
        (folder / f"{p['id']}.json").write_text(plan.dumps(p), encoding="utf-8")
    read, problems = pack_plans.read_folder(folder)
    assert problems == [] and len(read) == len(d.plans)
    key = run_key(conn)
    view = pack_plans.view(conn, key)
    assert view is not None
    assert pack_plans.check(read, lambda k: pack_plans.view(conn, k), view) == []


def test_solver_prints_nothing_to_stdout(
    conn: sqlite3.Connection, capfd: pytest.CaptureFixture[str]
) -> None:
    """Review HIGH-2: the epoxid line made HiGHS print hundreds of debug lines to fd 1."""
    capfd.readouterr()
    design(conn, Request("fluid:molten.epoxid", tier_cap=4))
    assert capfd.readouterr().out == ""


def test_batch_totals(conn: sqlite3.Connection) -> None:
    steel = "gregtech:gt.metaitem.01@11305"
    p = design(
        conn, Request(steel, tier_cap=MV, mode="batch", qty=64, within_s=1800)
    ).plans[0]
    assert p["target"]["qty"] == 64 and p["target"]["window_s"] == 1800
    assert p["target"]["rate_s"] == pytest.approx(64 / 1800)
    for n in p["nodes"]:
        assert n["crafts"] == pytest.approx(n["crafts_s"] * 1800)
    for b in p["boundary"]["in"]:
        assert b["qty"] == pytest.approx(b["rate_s"] * 1800)
    assert 0 < p["target"]["eta_s"] <= 1800
