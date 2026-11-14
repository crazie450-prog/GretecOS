"""The staged MILP (WO-3.4, plan §7.1 steps 3, 4 and 6, [E§3.4]) on PlanNH's gtnh-flow fixtures
(tests/fixtures/plannh, MIT, D192) and small hand-made networks.

Acceptance (§8 row 3.4): the platline solves with every machine running and at most 2
externals; the light-fuel loop closes with 0 gates; 12 re-solves give the same result.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from gregtec.planner import solve
from gregtec.planner.solve import Network, Proc

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "plannh"


def _number(text: str) -> float:
    return float(text.strip().strip("'\""))


def load_flow(path: Path) -> Network:
    """A gtnh-flow chart (the YAML subset PlanNH's fixtures use) as a solver network: one
    recipe per machine entry, ``dur`` seconds per craft, ``number`` a fixed machine count,
    ``target`` an output rate per second. Every machine is part of the chart (no pruning)."""
    entries: list[dict[str, object]] = []
    section: dict[str, float] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- "):
            entries.append({})
            line = "  " + line[2:]
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        key = key.strip().strip("'\"")
        value = value.strip()
        entry = entries[-1]
        if indent == 2:
            if value in ("", "{}"):
                section = {}
                entry[key] = section
            else:
                entry[key] = value.strip("'\"")
                section = None
        elif indent == 4 and section is not None:
            section[key] = _number(value)
    procs: list[Proc] = []
    fixed: list[tuple[str, float]] = []
    targets: list[tuple[str, float]] = []
    for n, e in enumerate(entries):
        pid = f"{n:02d}:{e['m']}"
        ins = e.get("I") or {}
        outs = e.get("O") or {}
        assert isinstance(ins, dict) and isinstance(outs, dict)
        procs.append(
            Proc(
                pid,
                tuple(sorted(ins.items())),
                tuple(sorted(outs.items())),
                _number(str(e["dur"])),
            )
        )
        if "number" in e:
            fixed.append((pid, _number(str(e["number"]))))
        tgt = e.get("target")
        if isinstance(tgt, dict):
            targets.extend(sorted(tgt.items()))
    return Network(
        procs=tuple(procs), targets=tuple(targets), fixed=tuple(fixed), prune=False
    )


def test_light_fuel_loop_closes_with_no_gates() -> None:
    net = load_flow(FIXTURES / "light_fuel_hydrogen_loop.yaml")
    flow = solve.solve_route(net)
    assert flow.gates == ()
    assert flow.all_run
    machines = {
        pid.split(":", 1)[1]: rate * net.proc(pid).seconds for pid, rate in flow.crafts
    }
    # [E§3.3]: CR 0.0167, electrolyser 0.015, distillery 1 (fixed).
    assert machines["distillery"] == pytest.approx(1.0)
    assert machines["chemical reactor"] == pytest.approx(25 / 12000 * 8)
    assert machines["electrolyzer"] == pytest.approx(25 / 12000 * 7.2)
    exports = dict(flow.exports)
    assert exports["light fuel"] == pytest.approx(25.0)
    assert (
        "sulfuric light fuel" not in exports
    )  # the loop isn't short-cut by exporting it


def test_platline_all_machines_run_with_at_most_two_externals() -> None:
    net = load_flow(FIXTURES / "230_platline.yaml")
    flow = solve.solve_route(net)
    assert flow.all_run, [p for p, v in flow.crafts if v <= 0]
    assert len(flow.gates) <= 2, flow.gates
    assert dict(flow.exports)["platinum dust"] == pytest.approx(1.0)


def test_platline_without_the_replay_would_idle_machines() -> None:
    """Why stage 4 exists ([E§3.3]): stages 1-3 alone leave machines idle on the platline."""
    net = load_flow(FIXTURES / "230_platline.yaml")
    flow = solve.solve_route(net)
    assert flow.floor is not None and flow.floor > 0


def test_twelve_identical_resolves() -> None:
    net = load_flow(FIXTURES / "230_platline.yaml")
    first = solve.solve_route(net)
    for _ in range(11):
        assert solve.solve_route(net) == first


# --- small networks ---------------------------------------------------------------------------


def chain(*, choices: bool = True) -> Network:
    """plate <- ingot <- dust (two ways to make the ingot: a primary choice)."""
    return Network(
        procs=(
            Proc("bend", (("ingot", 1.0), ("eu", 100.0)), (("plate", 1.0),), 1.0),
            Proc("ebf", (("dust", 1.0), ("eu", 1000.0)), (("ingot", 1.0),), 2.0),
            Proc("smelt", (("dust", 1.0), ("eu", 50.0)), (("ingot", 1.0),), 4.0),
            Proc("grind", (("ore", 1.0),), (("dust", 2.0), ("stone", 1.0)), 1.0),
        ),
        targets=(("plate", 2.0),),
        choices=(("ingot", ("ebf", "smelt")),) if choices else (),
    )


def test_rates_follow_the_target() -> None:
    flow = solve.solve_route(chain(), {"ingot": "ebf"})
    assert flow.rate("bend") == pytest.approx(2.0)
    assert flow.rate("ebf") == pytest.approx(2.0)
    assert flow.rate("grind") == pytest.approx(1.0)
    assert "smelt" not in dict(flow.crafts)
    assert dict(flow.imports)["ore"] == pytest.approx(1.0)
    assert dict(flow.imports)["eu"] == pytest.approx(2.0 * 100 + 2.0 * 1000)
    assert dict(flow.exports)["stone"] == pytest.approx(1.0)  # a byproduct: free
    assert flow.gates == ()
    assert flow.objectives[2] == pytest.approx(2 * 1 + 2 * 2 + 1 * 1)


def test_routes_differ_in_a_primary_choice() -> None:
    found = solve.routes(chain(), 3)
    assert [dict(f.choice)["ingot"] for f in found] == [
        "ebf",
        "smelt",
    ]  # fewest machines first
    assert len({f.choice for f in found}) == len(found)


def test_a_pin_is_a_fixed_choice() -> None:
    net = chain()
    pinned = Network(
        procs=net.procs,
        targets=net.targets,
        choices=net.choices,
        pinned=(("ingot", "smelt"),),
    )
    found = solve.routes(pinned, 3)
    assert [dict(f.choice)["ingot"] for f in found] == ["smelt"]


def test_choice_needs_a_recipe() -> None:
    with pytest.raises(solve.SolveError, match="no recipe chosen"):
        solve.solve_route(chain())


def test_connected_item_crossing_is_a_gate() -> None:
    """A loop item that can't close needs a gate; a boundary item doesn't."""
    net = Network(
        procs=(
            Proc("a", (("x", 2.0),), (("t", 1.0), ("y", 1.0)), 1.0),
            Proc("b", (("y", 1.0),), (("x", 1.0),), 1.0),
        ),
        targets=(("t", 1.0),),
    )
    flow = solve.solve_route(net)
    assert flow.gates == ("x",)
    assert dict(flow.imports)["x"] == pytest.approx(1.0)
    free = Network(
        net.procs, net.targets, free_in=frozenset({"x"}), free_out=frozenset({"x"})
    )
    flow = solve.solve_route(free)
    assert flow.gates == ()


def test_exact_counts_are_whole_and_cover_the_rate() -> None:
    net = chain()
    flow = solve.solve_route(net, {"ingot": "ebf"})
    cps = {"bend": 0.75, "ebf": 0.3, "grind": 4.0}
    counts = solve.exact_counts(net, flow, cps)
    assert counts == {"bend": 3, "ebf": 7, "grind": 1}
    for pid, n in counts.items():
        assert n * cps[pid] >= flow.rate(pid) - 1e-9
        assert n == math.ceil(flow.rate(pid) / cps[pid] - 1e-9)


def a_or_b() -> Network:
    """The target t from A (which takes x, made by X1 or X2) or from B (cheapest, no x)."""
    return Network(
        procs=(
            Proc("A", (("x", 1.0),), (("t", 1.0),), 2.0),
            Proc("B", (("ore", 1.0),), (("t", 1.0),), 1.0),
            Proc("X1", (("ore", 1.0),), (("x", 1.0),), 1.0),
            Proc("X2", (("ore", 2.0),), (("x", 1.0),), 1.0),
        ),
        targets=(("t", 1.0),),
        choices=(("t", ("A", "B")), ("x", ("X1", "X2"))),
    )


def test_routes_are_distinct_and_name_only_the_choices_they_run() -> None:
    """Review HIGH-1: picking B leaves x's choice unreached; it isn't a second route."""
    found = solve.routes(a_or_b(), 3)
    recipes = [frozenset(pid for pid, _ in f.crafts) for f in found]
    assert len(set(recipes)) == len(found) == 3
    assert [dict(f.choice) for f in found] == [
        {"t": "B"},
        {"t": "A", "x": "X1"},
        {"t": "A", "x": "X2"},
    ]
    for f in found:
        assert set(dict(f.choice).values()) <= {pid for pid, _ in f.crafts}


def test_effective_choice_drops_unreached_entries() -> None:
    net = a_or_b()
    assert solve.effective(net, {"t": "B", "x": "X2"}) == {"t": "B"}
    assert solve.effective(net, {"t": "A", "x": "X2"}) == {"t": "A", "x": "X2"}
    flow = solve.solve_route(net, {"t": "B", "x": "X1"})
    assert flow.choice == (("t", "B"),)


def test_solver_output_never_reaches_stdout(capfd: pytest.CaptureFixture[str]) -> None:
    """Review HIGH-2: HiGHS can print to the C stdout past disp=False."""
    with solve._quiet_stdout():
        os.write(1, b"HighsMipSolverData::debug\n")
    print("after")
    solve.routes(a_or_b(), 3)
    assert capfd.readouterr().out == "after\n"


def test_an_unreachable_target_is_refused() -> None:
    net = Network(
        procs=(Proc("a", (("x", 1.0),), (("y", 1.0),), 1.0),), targets=(("t", 1.0),)
    )
    with pytest.raises(solve.SolveError):
        solve.solve_route(net)


# --- power networks: the EU stage (WO-4.7, D323) ---------------------------------------------


def power_net(make_f_eu: float, make_h_eu: float = 140.0) -> Network:
    """A generator burning 1 fuel F a second for 100 EU/s (5 EU/t); F's chain: F from H
    (``make_f_eu`` EU a craft), H from a raw R (``make_h_eu`` EU). EU is never imported and an
    import of F stands for its 100 EU (a power line, ``power.network``)."""
    return Network(
        procs=(
            Proc("gen", (("F", 1.0),), (("eu", 100.0), ("unit", 1.0)), 1.0, ("unit",)),
            Proc("make_f", (("H", 1.0), ("eu", make_f_eu)), (("F", 1.0),), 1.0),
            Proc("make_h", (("R", 1.0), ("eu", make_h_eu)), (("H", 1.0),), 1.0),
        ),
        targets=(("unit", 1.0),),
        free_out=frozenset({"eu", "unit"}),
        banned=frozenset({"eu"}),
        eu_value=(("F", 100.0),),
    )


def test_a_power_chain_spends_eu_only_where_it_saves_more_than_it_costs() -> None:
    """G4 finding 1: the gate-free chain needs 150 EU per F (more than F's 100), so one gate is
    needed. Without the EU stage the gated quantity was minimised, and the chain made as much H
    from R as the generator's EU allowed (net 0). With it, the gate is H, all of it imported,
    and the chain draws only F's 10 EU: 90 EU/s are left."""
    net = power_net(10.0)
    assert net.costs_eu
    flow = solve.solve_route(net)
    assert flow.gates == ("H",)
    assert dict(flow.imports) == pytest.approx({"H": 1.0})
    assert dict(flow.crafts) == pytest.approx({"gen": 1.0, "make_f": 1.0})
    assert dict(flow.exports)["eu"] == pytest.approx(90.0)
    assert flow.eu == pytest.approx(10.0) and flow.all_run


def test_the_fuel_is_imported_when_making_it_costs_more_eu_than_it_gives() -> None:
    """Making F from H costs 120 EU, more than F's own 100: the line imports F through one
    gate, and the chain's recipes, idle at the optimum, are not part of it (a power line has no
    every-machine-runs floor, D323)."""
    flow = solve.solve_route(power_net(120.0))
    assert flow.gates == ("F",)
    assert dict(flow.crafts) == {"gen": 1.0}
    assert dict(flow.imports) == pytest.approx({"F": 1.0})
    assert dict(flow.exports)["eu"] == pytest.approx(100.0)
    assert flow.eu == pytest.approx(100.0) and flow.all_run


def test_a_gate_free_power_chain_is_kept_when_its_eu_fits() -> None:
    """Gates come first: when the gate-free chain fits in the generator's EU (90 of 100 EU/s),
    it is the line, with the 10 EU/s left as its net."""
    flow = solve.solve_route(power_net(10.0, 80.0))
    assert flow.gates == ()
    assert dict(flow.crafts) == pytest.approx(
        {"gen": 1.0, "make_f": 1.0, "make_h": 1.0}
    )
    assert dict(flow.exports)["eu"] == pytest.approx(10.0)


def test_no_zero_sum_loop_survives_in_a_power_line() -> None:
    """G4 finding 5: rods burn at 0.00065536/s; a saw cuts a long rod into 2 rods and a hammer
    makes the long rod from 2 rods. The line imports long rods and saws them; the hammer is
    idle at the optimum (the replay used to run it with the saw, a rod -> long rod -> rod loop
    above the generator's own rate)."""
    rate = 0.00065536
    eu = 163840.0
    net = Network(
        procs=(
            Proc("gen", (("rod", rate),), (("eu", eu), ("unit", 1.0)), 1.0, ("unit",)),
            Proc("saw", (("eu", 5.0), ("long", 1.0)), (("rod", 2.0),), 1.0),
            Proc("hammer", (("eu", 5.0), ("rod", 2.0)), (("long", 1.0),), 1.0),
        ),
        targets=(("unit", 1.0),),
        free_out=frozenset({"eu", "unit"}),
        banned=frozenset({"eu"}),
        eu_value=(("rod", eu / rate),),
    )
    flow = solve.solve_route(net)
    assert flow.gates == ("long",)
    assert dict(flow.crafts) == pytest.approx({"gen": 1.0, "saw": rate / 2})
    assert "hammer" not in dict(flow.crafts) and flow.all_run


def test_a_production_network_has_no_eu_stage() -> None:
    """A production line imports its EU (WO-4's generators supply it): no EU stage and the
    every-machine-runs replay as before (D192-D195)."""
    net = load_flow(FIXTURES / "light_fuel_hydrogen_loop.yaml")
    assert not net.costs_eu
    assert solve.solve_route(net).eu is None
    # The same chain making F as a production line: its EU is imported freely, so the
    # gate-free chain is the line whatever it draws.
    prod = Network(procs=power_net(10.0).procs[1:], targets=(("F", 1.0),))
    assert not prod.costs_eu
    flow = solve.solve_route(prod)
    assert flow.eu is None and flow.gates == ()
    assert dict(flow.imports)["eu"] == pytest.approx(150.0)
