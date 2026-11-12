"""Routes as canonical plan JSON (WO-3.4, plan §7.1 steps 3-6 and its plan sketch, D11, D12, D14,
D61, D142, D192-D195).

:func:`design` runs the designer from a request: WO-3.3's graph (pins included: a pin is a fixed
choice and the whole design re-runs, D61), WO-3.2's machine for each recipe (:mod:`.sizing`),
the staged MILP (:mod:`.solve`) for up to ``request.routes`` routes differing in a primary
choice, and with ``machines: base+addons`` the best route on addon machines for the
side-by-side column (D12), which lists the ``machine_fact`` confidence of each addon machine.

Every route becomes one **plan**, a dict shaped as §7.1's sketch: ``id``, ``name``, ``v``, ``gt``,
``run`` (the export run key it was solved on, which WO-1.9's affected-plan check reads), ``kind``,
``target``, ``tier_cap``, ``machines``, ``pins``, ``nodes``, ``edges``, ``boundary``, ``metrics``
and ``rank`` (WO-3.5's :mod:`.metrics` and :mod:`.rank`: the base routes come back in rank
order, each named for its place, the addon route last), ``alternatives`` (the other routes' ids)
and ``layout``
(empty: WO-3.6's), plus ``request`` (to re-solve it), ``route`` (the solver's view: choices,
gates, the replay floor) and ``warnings`` (D194). ``plan_schema.json`` is its JSON Schema. Every
number in it is the DB's or GT's math on it: a node cites its ``r:`` recipe and ``mte:`` machine
and lists the machine facts it was sized with. :func:`dumps` is the canonical text (sorted
keys, no spaces, floats to 9 significant digits): the same request on the same DB gives the
same bytes.

**Batch mode** (D14): the rate solve runs at ``qty`` per ``within`` (default one hour); the plan
then gives each node's crafts for the batch (``crafts``), the batch's inputs and byproducts
(``qty``) and the ETA with the machine counts found (``eta_s``: the slowest node's crafts over
its machines' speed).
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec.config import GT_PIN
from gregtec.planner import chains, effort, graph, metrics, rank, sizing, solve
from gregtec.planner.graph import EU_KEY, Graph
from gregtec.planner.gtmath.tiers import VN
from gregtec.planner.machines import MachineOption
from gregtec.planner.request import BaseInputs, Request

VERSION = 1
KIND = "production"
SCHEMA = Path(__file__).with_name("plan_schema.json")
BATCH_WINDOW_S = 3600.0  # D14: a batch with no within: is sized at qty per hour
# Graph boundaries the player supplies or takes away freely (D63); a byproduct or catalyst
# is free by structure (nothing uses it up), the target is the plan's export.
FREE = frozenset({"raw", "stock", "line", "request", "locked", "depth", "eu"})


@dataclass(frozen=True)
class Design:
    graph: Graph
    plans: tuple[
        dict[str, Any], ...
    ]  # the base routes in rank order (WO-3.5), the addon route last


def rate_and_window(request: Request) -> tuple[float, float | None]:
    """(target rate per second, the batch window in seconds or None in rate mode)."""
    if request.mode == "rate":
        rate = request.rate_per_s
        assert rate is not None
        return rate, None
    window = request.within_s or BATCH_WINDOW_S
    return request.qty / window, window


def network(
    g: Graph,
    request: Request,
    sized: Mapping[str, sizing.Sized],
    *,
    base_only: bool,
) -> solve.Network:
    """The solver's network for ``g``: every recipe (base machines only with ``base_only``),
    the primary choices with two or more recipes, the request's pins."""
    rate, _ = rate_and_window(request)
    recipes = [r for r in g.recipes if not base_only or r.base]
    ids = {r.id for r in recipes}
    makes: dict[str, list[str]] = {}
    for item in g.items:
        for ref in item.chosen:
            makes.setdefault(ref, []).append(item.id)
    procs = []
    for r in recipes:
        ins: dict[str, float] = {}
        for p in r.inputs:
            if p.consumed and p.amount > 0:
                ins[p.key] = ins.get(p.key, 0.0) + float(p.amount)
        outs: dict[str, float] = {}
        for p in r.outputs:
            if p.expected > 0:
                outs[p.key] = outs.get(p.key, 0.0) + p.expected
        s = sized.get(r.id)
        procs.append(
            solve.Proc(
                r.id,
                tuple(sorted(ins.items())),
                tuple(sorted(outs.items())),
                s.seconds if s is not None else 0.0,
                tuple(sorted(makes.get(r.id, ()))),
            )
        )
    free = frozenset(i.id for i in g.items if i.boundary in FREE)
    choices = []
    for key in g.primary:
        options = tuple(ref for ref in g.item(key).chosen if ref in ids)
        if len(options) > 1:
            choices.append((key, options))
    pins = tuple((p.node, p.recipe) for p in request.pins if p.recipe in ids)
    return solve.Network(
        procs=tuple(procs),
        targets=((g.target, rate),),
        free_in=free,
        free_out=free,
        choices=tuple(choices),
        pinned=pins,
    )


def design(
    conn: sqlite3.Connection,
    request: Request,
    base: BaseInputs,
    *,
    templates: chains.Templates | None = None,
    run_id: int | None = None,
    exact: bool = False,
    prefs: rank.Prefs = rank.BALANCED,
) -> Design:
    """The plans for ``request`` (see the module docstring), measured and ranked (WO-3.5:
    :mod:`.metrics`, :mod:`.rank` with ``prefs``' weights). ``exact``: whole machine counts
    from the integer MILP instead of ceil (step 6)."""
    g = graph.build(conn, request, base, templates=templates, run_id=run_id)
    run_key = _run_key(conn, g.run_id)
    player = graph.player(request, base)
    excluded = request.constraints.excluded_mtes()
    base_sized = sizing.size(conn, g, player, excluded_mtes=excluded)
    built: list[dict[str, Any]] = []
    net = network(g, request, base_sized, base_only=request.addons)
    for n, flow in enumerate(solve.routes(net, request.routes), 1):
        built.append(
            _plan(g, request, run_key, net, flow, base_sized, "base", n, exact)
        )
    if request.addons:
        addon_sized = sizing.size(conn, g, player, addon=True, excluded_mtes=excluded)
        addon_net = network(g, request, addon_sized, base_only=False)
        try:
            best = solve.routes(addon_net, 1)[0]
        except solve.SolveError:
            best = None
        if best is not None and any(
            (o := addon_sized[pid].option) is not None and o.addon
            for pid, _ in best.crafts
        ):
            built.append(
                _plan(
                    g, request, run_key, addon_net, best, addon_sized, "addon", 1, exact
                )
            )
    ids = [p["id"] for p in built]
    for p in built:
        p["alternatives"] = [i for i in ids if i != p["id"]]
    measure = effort.Effort(
        conn,
        request,
        base,
        g.tier_cap,
        craft_s=prefs.craft_s,
        templates=templates,
        run_id=g.run_id,
    )
    table = metrics.load_scarcity()
    for p in built:
        p["metrics"] = metrics.route(
            conn, g, p, measure.on_map, table, measure.route(p)
        )
    ranked = rank.order(built, prefs)
    for p in ranked:
        r = p["rank"]
        p["name"] = _name(g, request, r["column"], r["position"])
        p["metrics"] = _rounded(p["metrics"])
        p["rank"] = _rounded(r)
    return Design(g, tuple(ranked))


def _rounded(value: object) -> object:
    """Every float in ``value`` to 9 significant digits (stable text, :func:`_num`)."""
    if isinstance(value, float):
        return _num(value)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def _run_key(conn: sqlite3.Connection, run_id: int) -> str:
    row = conn.execute(
        "SELECT run_key FROM export_run WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"export run {run_id} is not in the DB")
    return str(row[0])


# --- one plan --------------------------------------------------------------------------------


def _num(v: float) -> float | int:
    """A float to 9 significant digits (stable text); whole ints stay ints."""
    if isinstance(v, int):
        return v
    if not math.isfinite(v):
        raise ValueError(f"not a finite number: {v!r}")
    out = float(f"{v:.9g}")
    return 0.0 if out == 0 else out


def plan_id(
    request: Request, run_key: str, column: str, choice: Sequence[tuple[str, str]]
) -> str:
    text = json.dumps([request.digest(), run_key, column, list(choice)])
    return "pl-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def _name(g: Graph, request: Request, column: str, n: int) -> str:
    target = g.item(g.target).name
    if request.mode == "rate":
        amount = f"{request.qty:g}{request.unit}"
    else:
        amount = f"{request.qty:g} batch"
    parts = [f"{target} line", amount, VN[g.tier_cap]]
    if column == "addon":
        parts.append("addons")
    elif n > 1:
        parts.append(f"route {n}")
    return " · ".join(parts)


def _facts(option: MachineOption) -> list[dict[str, Any]]:
    return [
        {
            "key": f.key,
            "value": f.value,
            "source": f.source,
            "confidence": _num(f.confidence),
        }
        for f in sorted(option.facts, key=lambda f: f.key)
    ]


def _oc_mode(option: MachineOption) -> str:
    for f in option.facts:
        if f.key == "oc" or f.key.startswith("oc["):
            return str(f.value)
    return "standard" if option.kind == "singleblock" else "none"


def _sizes(size: sizing.NodeSize) -> dict[str, Any]:
    out: dict[str, Any] = {
        "count": size.count,
        "util": _num(size.util),
        "eut": size.eut,
        "eut_avg": _num(size.eut_avg),
        "eut_peak": size.eut_peak,
        "amps": size.amps,
        "ticks_eff": size.ticks_eff,
        "parallel": size.parallel,
        "oc": size.oc,
    }
    if size.steam_per_tick is not None:
        out["steam_lt"] = _num(size.steam_per_tick)
    return out


def _plan(
    g: Graph,
    request: Request,
    run_key: str,
    net: solve.Network,
    flow: solve.Flow,
    sized: Mapping[str, sizing.Sized],
    column: str,
    n: int,
    exact: bool,
) -> dict[str, Any]:
    rate, window = rate_and_window(request)
    depth = {i.id: i.depth for i in g.items}
    procs = {p.id: p for p in net.procs}
    running = [(pid, x) for pid, x in flow.crafts]
    order = sorted(
        running,
        key=lambda t: (
            min((depth.get(k, 99) for k in procs[t[0]].made()), default=99),
            t[0],
        ),
    )
    node_of = {pid: f"n{i}" for i, (pid, _) in enumerate(order, 1)}
    counts: dict[str, int] = {}
    if exact:
        counts = solve.exact_counts(
            net, flow, {pid: sized[pid].crafts_per_s for pid, _ in running}
        )
    pinned = {p.node: p.recipe for p in request.pins}
    choice = dict(flow.choice)
    nodes: list[dict[str, Any]] = []
    sizes: dict[str, sizing.NodeSize] = {}
    warnings: list[str] = list(flow.warnings)
    for pid, x in order:
        rec = g.recipe(pid)
        made = [k for k in procs[pid].made() if k in {i.id for i in g.items}]
        node: dict[str, Any] = {
            "n": node_of[pid],
            "recipe": pid,
            "for": made,
            "map": rec.map,
            "crafts_s": _num(x),
            "flags": list(rec.flags),
            "pinned": any(pinned.get(k) == pid for k in made),
            "primary": any(choice.get(k) == pid for k in made),
        }
        if window is not None:
            node["crafts"] = _num(x * window)
        option = sized[pid].option
        if option is None:
            node.update(machine=None, count=None, util=None, eut=None, eut_avg=None,
                        eut_peak=None, amps=None, ticks_eff=None, steady=None,
                        badges=[], warnings=[], facts=[])  # fmt: skip
            nodes.append(node)
            continue
        size = sizing.node_size(option.sizing, x, counts.get(pid))
        sizes[pid] = size
        node["machine"] = {
            "mte": option.mte_id,
            "name": option.name,
            "kind": option.kind,
            "tier": VN[option.tier],
            "hatches": [{"tier": VN[h.tier], "amps": h.amps} for h in option.hatches],
            "coil_tier": option.coil_tier,
            "oc": size.oc,
            "oc_mode": _oc_mode(option),
            "parallel": size.parallel,
            "perfect": _oc_mode(option) == "perfect",
            "batch_mode": option.batch_mode,
            "addon": option.addon,
            "mod": option.mod,
            "confidence": _num(min((f.confidence for f in option.facts), default=1.0)),
            "requires": list(option.requires),
        }
        node.update(_sizes(size))
        steady = None
        if option.alternative is not None:
            steady = _sizes(sizing.node_size(option.alternative, x))
            steady["state"] = option.alternative.state
        node["state"] = option.sizing.state
        node["steady"] = steady
        node["badges"] = list(option.badges)
        node["warnings"] = list(option.notes)
        node["facts"] = _facts(option)
        for b in option.badges:
            warnings.append(f"{pid} on mte:{option.mte_id} ({option.name}) is {b}")
        nodes.append(node)
    edges = _edges(net, flow, node_of)
    target_out = dict(flow.exports).get(g.target, rate)
    target: dict[str, Any] = {"id": g.target, "rate_s": _num(target_out)}
    gates = set(flow.gates)

    def port(k: str, v: float) -> dict[str, Any]:
        out: dict[str, Any] = {"id": k, "rate_s": _num(v), "gated": k in gates}
        if window is not None:
            out["qty"] = _num(v * window)
        return out

    boundary = {
        "in": [port(k, v) for k, v in flow.imports if k != EU_KEY],
        "out": [port(k, v) for k, v in flow.exports if k != g.target],
    }
    if window is not None:
        eta = max(
            (
                x * window / (s.count * s.crafts_per_s)
                for pid, x in running
                if (s := sizes.get(pid)) is not None and s.count and s.crafts_per_s > 0
            ),
            default=0.0,
        )
        target.update(qty=_num(request.qty), window_s=_num(window), eta_s=_num(eta))
    available = {p.id for p in net.procs}
    for p in request.pins:
        if p.recipe not in available:
            # The base column's network has no addon recipe: the pin can't hold there.
            how = "imported" if p.node in dict(flow.imports) else "made another way"
            warnings.append(
                f"pin {p.node}={p.recipe} not available on base machines; {p.node} {how}"
            )
    for w in g.warnings:
        if w not in warnings:
            warnings.append(w)
    return {
        "id": plan_id(request, run_key, column, flow.choice),
        "name": _name(g, request, column, n),
        "v": VERSION,
        "gt": GT_PIN,
        "run": run_key,
        "kind": KIND,
        "target": target,
        "tier_cap": VN[g.tier_cap],
        "machines": "base+addons" if column == "addon" else "base",
        "pins": [{"node": p.node, "recipe": p.recipe} for p in request.pins],
        "nodes": nodes,
        "edges": edges,
        "boundary": boundary,
        "metrics": {},
        "rank": {},
        "alternatives": [],
        "layout": {},
        "request": request.as_dict(),
        "route": {
            "n": n,
            "column": column,
            "mode": request.mode,
            "exact": exact,
            "choices": [{"node": k, "recipe": r} for k, r in flow.choice],
            "gates": list(flow.gates),
            "all_run": flow.all_run,
            "floor": _num(flow.floor) if flow.floor is not None else None,
            "objectives": {
                "gates": flow.objectives[0],
                "gated": _num(flow.objectives[1]),
                "machines": _num(flow.objectives[2]),
            },
            "big_m": _num(flow.big_m),
        },
        "warnings": warnings,
    }


def _edges(
    net: solve.Network, flow: solve.Flow, node_of: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Item flows between nodes: each consumer takes from each maker in proportion to the
    maker's share of the item's supply (makers plus imports)."""
    made: dict[str, list[tuple[str, float]]] = {}
    used: dict[str, list[tuple[str, float]]] = {}
    for pid, x in flow.crafts:
        if x <= 0:
            continue
        p = net.proc(pid)
        for k, a in p.outputs:
            made.setdefault(k, []).append((pid, a * x))
        for k, a in p.inputs:
            used.setdefault(k, []).append((pid, a * x))
    imports = dict(flow.imports)
    out: list[dict[str, Any]] = []
    for k in sorted(made):
        if k == EU_KEY or k not in used:
            continue
        supply = sum(v for _, v in made[k]) + imports.get(k, 0.0)
        for src, sv in made[k]:
            for dst, dv in used[k]:
                if src == dst:
                    continue
                rate = sv * dv / supply
                if rate > 0:
                    out.append(
                        {
                            "from": node_of[src],
                            "to": node_of[dst],
                            "item": k,
                            "rate_s": _num(rate),
                        }
                    )
    out.sort(key=lambda e: (int(e["from"][1:]), int(e["to"][1:]), e["item"]))
    return out


def dumps(plan: Mapping[str, Any]) -> str:
    """The canonical text of a plan: sorted keys, no spaces, UTF-8."""
    return json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cites(plan: Mapping[str, Any]) -> tuple[str, ...]:
    """The ``r:`` and ``mte:`` keys a plan's numbers come from, in order, each once."""
    out: list[str] = []
    for node in plan.get("nodes") or ():
        if isinstance(node, dict):
            if isinstance(node.get("recipe"), str):
                out.append(node["recipe"])
            machine = node.get("machine")
            if isinstance(machine, dict) and isinstance(machine.get("mte"), int):
                out.append(f"mte:{machine['mte']}")
    for pin in plan.get("pins") or ():
        if isinstance(pin, dict) and isinstance(pin.get("recipe"), str):
            out.append(pin["recipe"])
    return tuple(dict.fromkeys(out))
