"""The self-sustaining validator and the bootstrap kit (WO-4.2, plan §7.2 ``self_sustaining``,
D13, D234).

**The validator** (:func:`validate`) proves that a solved power line imports nothing outside the
free set in steady state, and that its net EU/t stays above 0 after its own use. It is an
independent check over the plan, not the solver's constraint: it reads every node's recipe again
from the DB (its own SQL, :func:`db_ports`, not the graph or the solver's network), multiplies by
the node's crafts per second and sums each item over the whole line:

- a GT recipe (``r:``) uses up its consumed inputs and gives its outputs x chance, as the DB has
  them; its EU is the node's sized machine draw (``eut_avg``, GT's math: overclocks included),
  not the recipe's base EU/t;
- a farm (``farm:``) is read again from its source (the DB's farm map or ``farms.tsv``,
  :mod:`.farms`);
- an oredict or wildcard slot (``member:<slot>|<member>``) turns one member into one of the slot,
  and the member must be in the slot (the DB's ``ore_member``, or the wildcard's item);
- a generator (``gen:``) uses up and gives what its node lists (the fuel catalog's numbers, WO-4.1
  and :mod:`.gtmath.boiler`).

Any item whose balance is below zero (beyond float noise, :data:`TOL` of the largest flow) and
is not in the free set is an import: the line is not self-sustaining, and the proof names each
one with its rate. The plan's own ``boundary.in`` is checked against the free set too, so a line
that declares an import fails even when the sums would hide it.

**The bootstrap kit** (:func:`bootstrap`): what the line needs once to start and never uses up:
the seeds and saplings its farms keep (and the tools they use), the inputs its recipes keep
(programmed circuits, moulds, lenses: per machine), its turbine rotors, and starter fuel, the
fuel its generators burn while the first product makes its way through the line: the generators'
fuel rate x the sum of every node's cycle time (a bound on the longest path, D234).
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from gregtec.planner import graph
from gregtec.planner.farms import Farm
from gregtec.planner.graph import EU_KEY

TOL = 1e-6  # relative to the line's largest flow per second
FREE_KEYS = ("sunlight",)  # free set entries with no item: what a solar generator takes


@dataclass(frozen=True)
class Ports:
    """One recipe per craft: used up, given and kept (catalysts)."""

    used: dict[str, float]
    given: dict[str, float]
    kept: dict[str, float] = field(default_factory=dict)
    cycle_s: float | None = (
        None  # seconds one craft takes (base, for the bootstrap kit)
    )


def run_of(conn: sqlite3.Connection, plan: Mapping[str, Any]) -> int | None:
    """The export run a plan was solved on (its ``run`` key), or None."""
    row = conn.execute(
        "SELECT id FROM export_run WHERE run_key = ?", (str(plan.get("run")),)
    ).fetchone()
    return int(row[0]) if row else None


def db_ports(
    conn: sqlite3.Connection, ref: str, run_id: int | None = None
) -> Ports | None:
    """A recipe's ports by its citation key, read straight from the DB (None: no such recipe);
    of ``run_id``, else the newest run that has it."""
    row = conn.execute(
        "SELECT id, eut, duration_ticks FROM recipe WHERE ref = ?"
        " AND (? IS NULL OR run_id = ?) ORDER BY run_id DESC LIMIT 1",
        (ref, run_id, run_id),
    ).fetchone()
    if row is None:
        return None
    rid = int(row[0])
    used: dict[str, float] = {}
    kept: dict[str, float] = {}
    for key, amount, consumed in conn.execute(
        "SELECT CASE WHEN ri.ore_id IS NOT NULL THEN 'ore:' || o.name"
        f" WHEN ri.fluid_id IS NOT NULL THEN 'fluid:' || f.name ELSE {graph._ITEM_KEY_SQL} END,"
        " ri.amount, ri.consumed FROM recipe_input AS ri LEFT JOIN ore AS o ON o.id = ri.ore_id"
        " LEFT JOIN fluid AS f ON f.id = ri.fluid_id LEFT JOIN item AS i ON i.id = ri.item_id"
        " WHERE ri.recipe_id = ?",
        (rid,),
    ):
        if key is None:
            continue
        if consumed:
            used[str(key)] = used.get(str(key), 0.0) + float(amount or 0)
        else:  # a kept input (a programmed circuit is listed with amount 0): one a machine
            kept[str(key)] = max(kept.get(str(key), 0.0), float(amount or 0), 1.0)
    given: dict[str, float] = {}
    for key, amount, chance in conn.execute(
        f"SELECT CASE WHEN ro.fluid_id IS NOT NULL THEN 'fluid:' || f.name"
        f" ELSE {graph._ITEM_KEY_SQL} END, ro.amount, ro.chance_bp FROM recipe_output AS ro"
        " LEFT JOIN fluid AS f ON f.id = ro.fluid_id LEFT JOIN item AS i ON i.id = ro.item_id"
        " WHERE ro.recipe_id = ?",
        (rid,),
    ):
        if key is None:
            continue
        given[str(key)] = (
            given.get(str(key), 0.0)
            + float(amount)
            * float(chance if chance is not None else graph.FULL)
            / graph.FULL
        )
    kept = {k: v for k, v in kept.items() if v > 0 and k not in used}
    cycle = float(row[2]) / 20 if row[2] else None
    return Ports(used, given, kept, cycle)


def is_member(conn: sqlite3.Connection, slot: str, member: str) -> bool:
    """``member`` (a stable item key) belongs to ``slot`` (``ore:<name>`` or
    ``registry@32767``), by the DB."""
    if slot.endswith(f"@{graph.WILDCARD}"):
        return member.rsplit("@", 1)[0] == slot.rsplit("@", 1)[0]
    if not slot.startswith("ore:"):
        return False
    row = conn.execute(
        f"SELECT 1 FROM ore AS o JOIN ore_member AS om ON om.ore_id = o.id"
        f" JOIN item AS i ON i.id = om.item_id WHERE o.name = ? AND {graph._ITEM_KEY_SQL} = ?"
        " LIMIT 1",
        (slot[4:], member),
    ).fetchone()
    return row is not None


def _declared(node: Mapping[str, Any]) -> Ports:
    p = node.get("ports") or {}
    return Ports(
        {k: float(v) for k, v in (p.get("in") or {}).items()},
        {k: float(v) for k, v in (p.get("out") or {}).items()},
        {k: float(v) for k, v in (p.get("kept") or {}).items()},
        float(node["cycle_s"]) if node.get("cycle_s") else None,
    )


def node_ports(
    conn: sqlite3.Connection,
    node: Mapping[str, Any],
    farms: Mapping[str, Farm],
    run_id: int | None = None,
) -> tuple[Ports | None, str | None]:
    """A node's ports read again from their source, or (None, why not)."""
    ref = str(node.get("recipe"))
    if ref.startswith("r:"):
        ports = db_ports(conn, ref, run_id)
        if ports is None:
            return None, f"{ref} is not in the DB"
        ports.used.pop(EU_KEY, None)
        draw = float(node.get("eut_avg") or 0.0)
        x = float(node.get("crafts_s") or 0.0)
        if draw > 0 and x > 0:
            ports.used[EU_KEY] = draw * 20 / x
        return ports, None
    if ref.startswith("farm:"):
        farm = farms.get(ref)
        if farm is None:
            return None, f"{ref}: no such farm in the DB or farms.tsv"
        used, given = farm.ports()
        return Ports(used, given, dict(farm.catalysts), farm.seconds), None
    if ref.startswith("member:"):
        slot, _, member = ref[7:].partition("|")
        if not is_member(conn, slot, member):
            return None, f"{member} is not a member of {slot}"
        return Ports({member: 1.0}, {slot: 1.0}), None
    if ref.startswith("gen:"):
        return _declared(node), None
    return None, f"{ref}: unknown node kind"


@dataclass(frozen=True)
class Proof:
    """The validator's verdict on one line, with its working."""

    ok: bool
    imports: tuple[tuple[str, float], ...]  # non-free items imported, per second
    net_eut: float  # EU/t left after the line's own use
    balance: tuple[
        tuple[str, float], ...
    ]  # every item's net per second (+ made, - used)
    free_used: tuple[tuple[str, float], ...]  # free-set items taken, per second
    problems: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "imports": [{"id": k, "rate_s": v} for k, v in self.imports],
            "net_eut": self.net_eut,
            "free_used": [{"id": k, "rate_s": v} for k, v in self.free_used],
            "balance": [{"id": k, "rate_s": v} for k, v in self.balance],
            "problems": list(self.problems),
        }


def validate(
    conn: sqlite3.Connection,
    plan: Mapping[str, Any],
    free: Iterable[str],
    farms: Mapping[str, Farm] | None = None,
    ports: Callable[[Mapping[str, Any]], tuple[Ports | None, str | None]] | None = None,
) -> Proof:
    """Prove (or refute) that ``plan`` takes nothing from outside ``free`` (see the module
    docstring). ``ports`` replaces :func:`node_ports` (tests)."""
    free_set = frozenset(free)
    farm_map = farms or {}
    run_id = run_of(conn, plan)
    read = ports or (lambda n: node_ports(conn, n, farm_map, run_id))
    balance: dict[str, float] = {}
    problems: list[str] = []
    flows: list[float] = []
    for node in plan.get("nodes") or ():
        x = float(node.get("crafts_s") or 0.0)
        if x <= 0:
            continue
        p, why = read(node)
        if p is None:
            problems.append(why or f"{node.get('recipe')}: unreadable")
            continue
        for k, a in p.used.items():
            balance[k] = balance.get(k, 0.0) - a * x
            flows.append(a * x)
        for k, a in p.given.items():
            balance[k] = balance.get(k, 0.0) + a * x
            flows.append(a * x)
    tol = TOL * max([1.0, *flows])
    imports = tuple(
        (k, -v)
        for k, v in sorted(balance.items())
        if v < -tol and k != EU_KEY and k not in free_set
    )
    free_used = tuple(
        (k, -v) for k, v in sorted(balance.items()) if v < -tol and k in free_set
    )
    declared = sorted(
        str(p["id"])
        for p in (plan.get("boundary") or {}).get("in") or ()
        if p.get("id") not in free_set and p.get("id") != EU_KEY
    )
    imported = {k for k, _ in imports}
    for k in declared:
        if k not in imported:
            problems.append(f"the plan declares {k} as an import")

    net_eut = balance.get(EU_KEY, 0.0) / 20
    if net_eut <= tol / 20:
        problems.append(
            f"net EU/t {net_eut:.6g} is not above 0 after the line's own use"
        )
    ok = not imports and not problems
    return Proof(
        ok,
        imports,
        net_eut,
        tuple(sorted((k, 0.0 if abs(v) <= tol else v) for k, v in balance.items())),
        free_used,
        tuple(problems),
    )


def bootstrap(
    conn: sqlite3.Connection,
    plan: Mapping[str, Any],
    farms: Mapping[str, Farm] | None = None,
) -> dict[str, Any]:
    """The line's bootstrap kit (see the module docstring)."""
    farm_map = farms or {}
    seeds: dict[str, float] = {}
    kept: dict[str, float] = {}
    tools: dict[str, int] = {}
    parts: dict[str, int] = {}
    startup_s = 0.0
    fuel: dict[str, float] = {}
    run_id = run_of(conn, plan)
    for node in plan.get("nodes") or ():
        ref = str(node.get("recipe"))
        count = int(node.get("count") or 1)
        if ref.startswith("farm:") and ref in farm_map:
            farm = farm_map[ref]
            for k, a in farm.catalysts:
                seeds[k] = seeds.get(k, 0.0) + a * count
            for t in farm.tools:
                tools[t] = tools.get(t, 0) + count
            startup_s += farm.seconds
        elif ref.startswith("r:"):
            p = db_ports(conn, ref, run_id)
            if p is not None:
                for k, a in p.kept.items():
                    kept[k] = kept.get(k, 0.0) + a * count
            ticks = node.get("ticks_eff")
            if ticks:
                startup_s += float(ticks) / 20
            elif p is not None and p.cycle_s:
                startup_s += p.cycle_s
        elif ref.startswith("gen:"):
            for part, n in (node.get("parts") or {}).items():
                parts[part] = parts.get(part, 0) + int(n)
            g = node.get("fuel")
            if isinstance(g, dict):
                fuel[str(g["id"])] = fuel.get(str(g["id"]), 0.0) + float(g["rate_s"])
    return {
        "seeds_saplings": [
            {"id": k, "qty": math.ceil(v)} for k, v in sorted(seeds.items())
        ],
        "kept_inputs": [
            {"id": k, "qty": math.ceil(v)} for k, v in sorted(kept.items())
        ],
        "tools": [{"tool": k, "qty": v} for k, v in sorted(tools.items())],
        "parts": [{"part": k, "qty": v} for k, v in sorted(parts.items())],
        "starter_fuel": [
            {"id": k, "qty": math.ceil(v * startup_s), "startup_s": startup_s}
            for k, v in sorted(fuel.items())
        ],
    }
