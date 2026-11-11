"""Metrics, ranking and explanations (WO-3.5) on a real harness DB: the dev runtime's recipes.

As test_plan_harness.py, it runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the
server harness; locally: mod/README.md) and writes nothing outside tmp_path. §8 row 3.5:

- the same request gives the same order (and bytes) every run;
- each benchmark returns 2-3 routes differing in at least one primary choice, unless the
  dev runtime offers no second recipe for any primary choice (checked, not assumed);
- for 3 targets, batch totals equal rate x time within 0.1%;
- the templated explanation passes the numeric validator, and a wrong number injected into it
  is caught.

The addon column criterion is tested on the fixture DB (tests/unit/planner/test_metrics.py):
the dev runtime has no addon machines. The server harness exports no NEI crafting recipes and
no structures, so machine items can't be made here and ``eff_h`` is 0 with notes saying so;
the effort model is tested on the fixture and, with NEI's crafting-table recipes, on the
client harness run.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import effort, explain, graph, plan, rank, sizing, validate
from gregtec.planner.request import DefaultBaseInputs, Request

REPO = Path(__file__).resolve().parents[2]
BENCHMARKS = {
    "gregtech:gt.metaitem.01@2085": 5,  # platinum dust (platline)
    "fluid:liquid_light_fuel": 2,
    "gregtech:gt.metaitem.01@11305": 2,  # steel
    "gregtech:gt.metaitem.01@2067": 6,  # neodymium (rare earths)
    "gregtech:gt.metaitem.01@2324": 7,  # naquadah
    "fluid:molten.plastic": 2,
    "fluid:sulfuricacid": 2,
    "gregtech:gt.metaitem.03@32030": 3,  # monocrystalline silicon boule
    "gregtech:gt.metaitem.01@17880": 2,  # rubber sheet
    "gregtech:gt.metaitem.01@11306": 3,  # stainless steel ingot
    "fluid:nitricacid": 3,
    "fluid:molten.polybenzimidazole": 5,
}
BATCH = {
    "gregtech:gt.metaitem.01@11305": (2, 64, 1800.0),  # 64 steel in 30 min
    "fluid:nitricacid": (3, 10000, 3600.0),  # 10 000 L in an hour
    "gregtech:gt.metaitem.01@2085": (5, 128, 7200.0),  # 128 platinum dust in 2 h
}
TOL = 1e-3  # §8: within 0.1%


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("rank-harness")
    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


def design(conn: sqlite3.Connection, request: Request) -> plan.Design:
    return plan.design(conn, request, DefaultBaseInputs(0))


@pytest.mark.parametrize(
    "target",
    ["gregtech:gt.metaitem.01@2085", "fluid:molten.plastic", "fluid:nitricacid"],
)
def test_same_request_same_order(conn: sqlite3.Connection, target: str) -> None:
    request = Request(target, tier_cap=BENCHMARKS[target])
    first = design(conn, request).plans
    order = [p["id"] for p in first]
    for _ in range(2):
        again = design(conn, request).plans
        assert [p["id"] for p in again] == order
        assert [plan.dumps(p) for p in again] == [plan.dumps(p) for p in first]
    scores = [p["rank"]["score"] for p in first]
    assert scores == sorted(scores, reverse=True)


def _primary_options(conn: sqlite3.Connection, request: Request) -> int:
    """How many primary choices offer a second base recipe (the routes' search space)."""
    base = DefaultBaseInputs(0)
    g = graph.build(conn, request, base)
    sized = sizing.size(conn, g, graph.player(request, base))
    return len(plan.network(g, request, sized, base_only=False).choices)


@pytest.mark.parametrize(("target", "tier"), sorted(BENCHMARKS.items()))
def test_benchmarks_return_two_or_three_ranked_routes(
    conn: sqlite3.Connection, target: str, tier: int
) -> None:
    request = Request(target, tier_cap=tier)
    d = design(conn, request)
    base = [p for p in d.plans if p["rank"]["column"] == "base"]
    if len(base) == 1:
        assert _primary_options(conn, request) == 0, target
        pytest.skip(f"{target}: the dev runtime offers one recipe per primary choice")
    assert 2 <= len(base) <= 3
    choices = [{(c["node"], c["recipe"]) for c in p["route"]["choices"]} for p in base]
    for i, a in enumerate(choices):
        for b in choices[i + 1 :]:
            assert a != b
    assert [p["rank"]["position"] for p in base] == list(range(1, len(base) + 1))
    for p in base:
        assert p["rank"]["w"] == rank.BALANCED.as_dict()
        m = p["metrics"]
        assert m["machines"] >= 1 and m["eut_avg"] >= 0 and m["scarcity"] >= 0
        assert m["eff_h"] == pytest.approx(
            m["build_h"] + m["unlock_h"], rel=1e-8, abs=1e-12
        )
    doc = explain.facts(d.plans, d.graph.item(d.graph.target).name, rank.BALANCED)
    text = explain.template(doc)
    names = explain.names(doc)
    assert validate.check(text, doc, names).ok
    # A number far above anything in the JSON (and its sums) replaces the first machine count.
    count = explain.fmt(doc["routes"][0]["metrics"]["machines"])
    wrong = text.replace(f"{count} machines (", f"{_absent(doc)} machines (", 1)
    assert wrong != text
    assert validate.check(wrong, doc, names).offending == (_absent(doc),)


def _absent(doc: object) -> str:
    """A whole number 1000 times larger than every number in ``doc``."""
    found: list[float] = [0.0]

    def walk(x: object) -> None:
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, int | float) and not isinstance(x, bool):
            found.append(abs(float(x)))

    walk(doc)
    return str(int(max(found) * 1000) + 7)


@pytest.mark.parametrize(("target", "batch"), sorted(BATCH.items()))
def test_batch_totals_equal_rate_times_time(
    conn: sqlite3.Connection, target: str, batch: tuple[int, int, float]
) -> None:
    tier, qty, within = batch
    request = Request(target, tier_cap=tier, mode="batch", qty=qty, within_s=within)
    for p in design(conn, request).plans:
        rate = p["target"]["rate_s"]
        b = p["metrics"]["batch"]
        assert b["window_s"] == within and b["qty"] == qty
        assert rate * within == pytest.approx(qty, rel=TOL)
        rates = {x["id"]: x["rate_s"] for x in p["boundary"]["in"]}
        for x in b["inputs"]:
            assert x["qty"] == pytest.approx(rates[x["id"]] * within, rel=TOL)
        outs = {x["id"]: x["rate_s"] for x in p["boundary"]["out"]}
        for x in b["byproducts"]:
            assert x["qty"] == pytest.approx(outs[x["id"]] * within, rel=TOL)
        assert b["crafts"] == pytest.approx(
            sum(n["crafts_s"] for n in p["nodes"]) * within, rel=TOL
        )
        assert b["eu"] == pytest.approx(
            sum(n["eut_avg"] or 0 for n in p["nodes"]) * 20 * within, rel=TOL
        )
        assert 0 < b["eta_s"] <= within * (1 + TOL)


# --- the effort model on the client run (NEI's crafting-table recipes) ------------------------


@dataclass(frozen=True)
class Owned(DefaultBaseInputs):
    """No stock or lines, the default tier, and these machines owned."""

    owned: tuple[tuple[int, int], ...] = ()

    def owned_machines(self) -> Mapping[int, int]:
        return dict(self.owned)


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("rank-client")
    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


def test_effort_on_a_run_with_crafting_recipes(client: sqlite3.Connection) -> None:
    """The client run has NEI's crafting-table recipes, so machine items can be made: each
    route's build effort is positive, crafting-table steps cost craft_s each, and owning only
    some machines unlocks the rest (each map once, depth <= 3)."""
    steel = Request("gregtech:gt.metaitem.01@11305", tier_cap=2)
    d = plan.design(client, steel, DefaultBaseInputs(0))
    for p in d.plans:
        m = p["metrics"]
        assert m["build_h"] > 0 and m["unlock_h"] == 0
        assert m["eff_h"] == pytest.approx(
            m["build_h"] + m["unlock_h"], rel=1e-8, abs=1e-12
        )
        hand = [r for r in m["effort"]["rows"] if r["map"] == effort.HAND]
        assert hand and hand[0]["h"] == pytest.approx(
            hand[0]["crafts"] * effort.CRAFT_S / 3600, rel=1e-6
        )
    best = d.plans[0]
    owned = ((best["metrics"]["by_type"][0]["mte"], 1),)
    again = plan.design(client, steel, Owned(0, owned=owned))
    m = next(p for p in again.plans if p["id"] == best["id"])["metrics"]
    assert m["unlock_h"] > 0 and m["effort"]["unlocks"]
    assert len(set(m["effort"]["unlocks"])) == len(m["effort"]["unlocks"])
    assert m["eff_h"] == pytest.approx(
        m["build_h"] + m["unlock_h"], rel=1e-8, abs=1e-12
    )
