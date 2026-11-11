"""The staged MILP (WO-3.4, plan §7.1 steps 3, 4 and 6, [E§3.4]) on PlanNH's gtnh-flow fixtures
(tests/fixtures/plannh, MIT, D192) and small hand-made networks.

Acceptance (§8 row 3.4): the platline solves with every machine running and at most 2
externals; the light-fuel loop closes with 0 gates; 12 re-solves give the same result.
"""

from __future__ import annotations

import math
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


def test_an_unreachable_target_is_refused() -> None:
    net = Network(
        procs=(Proc("a", (("x", 1.0),), (("y", 1.0),), 1.0),), targets=(("t", 1.0),)
    )
    with pytest.raises(solve.SolveError):
        solve.solve_route(net)
