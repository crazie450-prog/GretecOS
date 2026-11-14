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
- a generator (``gen:``) is derived again from the DB by its node's ``spec`` (:func:`gen_ports`),
  never taken from the ports the designer wrote: the fuel row it cites (its fluid or item and
  FUEL_VALUE), and for a single block GT's own EU per unit (``generator_gt``, else
  ``fuels.single_block_eu``) at the block's voltage x amps; for a turbine GT's math at its rotor and
  dynamo (``generators.turbine``); for boilers ``gtmath.boiler`` at their throttle circuit feeding
  the steam turbine; for the Large Naquadah Reactor ``gtmath.naquadah``'s steady run on the fuel
  recipe it cites, its liquid air and depleted fuel included, with the output checked against
  its dynamo hatch's voltage x amps (``dynamo_hatch``, D327). Ports the node declares must match (each item within :data:`TOL` of its
  flow), or the proof names the difference.

A GT recipe that uses up nothing but gives something outside the free set (an export quirk: an
input it couldn't key) is never counted on: the proof names it (D234).

Any item whose balance is below zero (beyond float noise, :data:`TOL`) and
is not in the free set is an import (noise is relative to that item's own flow, so a trickle of
a dense fuel still counts): the line is not self-sustaining, and the proof names each
one with its rate. Any item outside the free set left over (balance above zero) is listed as
``surplus``: in the game it needs a sink (a void or storage), or the machine making it stalls when
its output is full. The plan's own ``boundary.in`` is checked against the free set too, so a line
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
from fractions import Fraction
from typing import Any

from gregtec.planner import fuels, generators, graph
from gregtec.planner.farms import Farm
from gregtec.planner.graph import EU_KEY
from gregtec.planner.gtmath import boiler as gt_boiler
from gregtec.planner.gtmath import naquadah as gt_naquadah
from gregtec.planner.gtmath.turbine import Kind

TOL = 1e-6  # float noise, relative to the item's own flow (made or used, the larger)
ABS_TOL = 1e-12  # per second
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


_TURBINES = {"gas_turbine": Kind.GAS, "plasma_turbine": Kind.PLASMA}
_TURBINE_CLASS = {
    "gas_turbine": "MTELargeTurbineGas",
    "plasma_turbine": "MTELargeTurbinePlasma",
}


def _class(conn: sqlite3.Connection, mte: int) -> str | None:
    row = conn.execute(
        "SELECT class_name FROM machine WHERE mte_id = ?", (mte,)
    ).fetchone()
    return str(row[0]).rsplit(".", 1)[-1] if row else None


def _gen_maps(conn: sqlite3.Connection, mte: int, recipe_map: str | None) -> set[str]:
    """The recipe maps a generator burns: its own and those the DB lists for its mte."""
    out = {
        str(r[0])
        for r in conn.execute(
            "SELECT rm.unloc_name FROM machine_map AS mm JOIN machine AS m"
            " ON m.id = mm.machine_id JOIN recipe_map AS rm ON rm.id = mm.recipe_map_id"
            " WHERE m.mte_id = ?",
            (mte,),
        )
    }
    if recipe_map:
        out.add(str(recipe_map))
    return out


def _single_eu(
    conn: sqlite3.Connection,
    run_id: int,
    mte: int,
    fuel_value: int,
    efficiency: int,
    fluid: str | None,
    item: str,
) -> Fraction:
    """EU per unit (litre or item) GT's single block gives: GT's own ``getFuelValue`` from the
    export (``generator_gt``) when it has the fuel, else WO-4.1's reading of it."""
    if fluid is not None:
        row = conn.execute(
            "SELECT eu_long, consumed FROM generator_gt WHERE run_id = ? AND mte_id = ?"
            " AND fluid = ? AND special = ? AND eu_long IS NOT NULL LIMIT 1",
            (run_id, mte, fluid, fuel_value),
        ).fetchone()
        if row is not None and row[1]:
            return Fraction(int(row[0]), int(row[1]))
    else:
        reg, _, meta = item.rpartition("@")
        row = conn.execute(
            "SELECT item_eu_long FROM generator_gt WHERE run_id = ? AND mte_id = ?"
            " AND registry_name = ? AND meta = ? AND item_eu_long IS NOT NULL LIMIT 1",
            (run_id, mte, reg, int(meta) if meta.isdigit() else -1),
        ).fetchone()
        if row is not None:
            return Fraction(int(row[0]))
    return Fraction(
        fuels.single_block_eu(
            special=fuel_value, efficiency=efficiency, fluid_input=fluid is not None
        )
    )


def _turbine_run(
    conn: sqlite3.Connection,
    kind: Kind,
    spec: Mapping[str, Any],
    fluid: str | None,
    run: int,
) -> generators.TurbineRun:
    rotor = tuple(spec["rotor"])
    return generators.turbine(
        conn,
        kind=kind,
        material=str(rotor[0]),
        size=str(rotor[1]),
        dynamo_tier=int(spec["dynamo_tier"]),
        fuel=fluid,
        run_id=run,
    ).runs[1]


def gen_ports(
    conn: sqlite3.Connection, node: Mapping[str, Any], run_id: int | None = None
) -> tuple[Ports | None, str | None]:
    """A generator node's ports per build unit, derived again from the DB and GT's math by its
    ``spec`` (see the module docstring), or (None, why not)."""
    ref = str(node.get("recipe"))
    spec = node.get("spec")
    if not isinstance(spec, Mapping) or "kind" not in spec or "fuel_ref" not in spec:
        return None, f"{ref}: no spec to derive its ports from"
    row = conn.execute(
        "SELECT run_id, input, fluid, fuel_value, value, unit, map FROM fuel WHERE ref = ?"
        " AND (? IS NULL OR run_id = ?) ORDER BY run_id DESC LIMIT 1",
        (str(spec["fuel_ref"]), run_id, run_id),
    ).fetchone()
    if row is None:
        return None, f"{ref}: fuel {spec['fuel_ref']} is not in the DB"
    run = int(row[0])
    item, fluid, fuel_value = str(row[1]), row[2], int(row[3])
    key = f"fluid:{fluid}" if fluid else item
    kind = str(spec["kind"])
    try:
        if kind == "single_block":
            mte = int(spec["mte"])
            g = conn.execute(
                "SELECT efficiency, eu_out, amps_out, recipe_map FROM generator"
                " WHERE run_id = ? AND mte_id = ?",
                (run, mte),
            ).fetchone()
            if g is None or not g[0] or not g[1] or not g[2]:
                return None, f"{ref}: mte:{mte} is not a single-block generator"
            # GT burns only its own maps' fuels in a block: the fuel row's map must be one of
            # them (as generators.catalog builds fuel_maps), or any fuel would be "proven".
            if str(row[6]) not in _gen_maps(conn, mte, g[3]):
                return None, f"{ref}: {key} ({row[6]}) is not a fuel of mte:{mte}"
            per = _single_eu(conn, run, mte, fuel_value, int(g[0]), fluid, item)
            if per <= 0:
                return None, f"{ref}: GT gives 0 EU per unit of {key}"
            eu_s = Fraction(int(g[1]) * int(g[2]) * 20)
            return Ports({key: float(eu_s / per)}, {EU_KEY: float(eu_s)}), None
        if kind in _TURBINES:
            mte = int(spec["mte"])
            if _class(conn, mte) != _TURBINE_CLASS[kind] or fluid is None:
                return None, f"{ref}: mte:{mte} on {key} is not a {kind}"
            tr = _turbine_run(conn, _TURBINES[kind], spec, fluid, run)
            return Ports(
                {key: tr.flow * 20 / tr.ticks}, {EU_KEY: float(tr.eut * 20)}
            ), None
        if kind == "boiler_steam":
            b = gt_boiler.BOILERS.get(_class(conn, int(spec["boiler_mte"])) or "")
            if (
                b is None
                or fluid is None
                or str(row[5]) != fuels.Unit.BOILER_TICKS.value
            ):
                return None, f"{ref}: not a 2.9 large boiler on a fluid fuel"
            steam = (
                "MTELargeTurbineHPSteam" if b.superheated else "MTELargeTurbineSteam"
            )
            if _class(conn, int(spec["turbine_mte"])) != steam:
                return (
                    None,
                    f"{ref}: mte:{spec['turbine_mte']} doesn't take {b.steam_fluid}",
                )
            st = gt_boiler.steady(
                b, fluid=fluid, duration=int(row[4]), circuit=int(spec["circuit"])
            )
            tr = _turbine_run(
                conn, Kind.HP_STEAM if b.superheated else Kind.STEAM, spec, None, run
            )
            count = int(spec["boilers"])
            if st.steam * count < tr.flow:
                return None, (
                    f"{ref}: {count} boilers at circuit {spec['circuit']} give"
                    f" {float(st.steam * count):.6g} L/t, the turbine takes {tr.flow}"
                )
            ins = {key: float(count * st.fuel * 20)}
            ins["fluid:water"] = ins.get("fluid:water", 0.0) + float(
                count * st.water * 20
            )
            return Ports(ins, {EU_KEY: float(tr.eut * 20)}), None
        if kind == "naquadah_reactor":
            return _reactor_ports(conn, ref, spec, run, key)
    except (
        generators.CatalogError,
        gt_boiler.BoilerError,
        ValueError,
        KeyError,
    ) as exc:
        return None, f"{ref}: {exc}"
    return None, f"{ref}: unknown generator kind {kind!r}"


def _reactor_ports(
    conn: sqlite3.Connection,
    ref: str,
    spec: Mapping[str, Any],
    run: int,
    key: str,
) -> tuple[Ports | None, str | None]:
    """The Large Naquadah Reactor's ports per build unit, from the DB (D327)."""
    mte = int(spec["mte"])
    if _class(conn, mte) != "MTELargeNaquadahReactor":
        return None, f"{ref}: mte:{mte} is not a Large Naquadah Reactor"
    reactor = generators.reactor(conn, mte, run)
    if reactor is None:
        return None, f"{ref}: the DB has no lists for mte:{mte}"
    fuel = next(
        (f for f in generators.reactor_fuels(conn, run) if f.ref == spec["fuel_ref"]),
        None,
    )
    if fuel is None or f"fluid:{fuel.fluid}" != key:
        return None, f"{ref}: {spec['fuel_ref']} is not a fuel of mte:{mte}'s map"
    s = gt_naquadah.steady(reactor, basic=fuel.basic, duration=fuel.duration)
    hatch = conn.execute(
        "SELECT eu_out, amps_out FROM dynamo_hatch WHERE run_id = ? AND mte_id = ?",
        (run, int(spec["dynamo_mte"])),
    ).fetchone()
    if hatch is None:
        return None, f"{ref}: mte:{spec['dynamo_mte']} is not a dynamo hatch"
    cap = int(hatch[0]) * int(hatch[1])
    if cap < s.eut:
        return None, (
            f"{ref}: {s.eut} EU/t is more than mte:{spec['dynamo_mte']} takes ({cap}):"
            " GT stops the reactor"
        )
    used = {key: s.per_second(s.fuel)}
    if s.air:
        air = f"fluid:{reactor.air_fluid}"
        used[air] = used.get(air, 0.0) + s.per_second(s.air)
    given = {
        EU_KEY: float(s.eut * 20),
        f"fluid:{fuel.depleted}": s.per_second(s.depleted),
    }
    return Ports(used, given), None


def _mismatch(declared: Ports, derived: Ports) -> list[str]:
    """Where a node's declared ports differ from the derived ones (relative :data:`TOL`)."""
    out = []
    for side, a, b in (
        ("uses", declared.used, derived.used),
        ("gives", declared.given, derived.given),
    ):
        for k in sorted(set(a) | set(b)):
            x, y = a.get(k, 0.0), b.get(k, 0.0)
            if abs(x - y) > max(ABS_TOL, TOL * max(abs(x), abs(y))):
                out.append(f"{side} {k} {x:.6g}/unit, GT gives {y:.6g}")
    return out


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
        derived, why = gen_ports(conn, node, run_id)
        if derived is None:
            return None, why
        if node.get("ports"):
            bad = _mismatch(_declared(node), derived)
            if bad:
                return None, f"{ref} declares other ports than GT: " + "; ".join(bad)
        return derived, None
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
    surplus: tuple[tuple[str, float], ...] = ()  # non-free items left over: need a sink

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "imports": [{"id": k, "rate_s": v} for k, v in self.imports],
            "net_eut": self.net_eut,
            "free_used": [{"id": k, "rate_s": v} for k, v in self.free_used],
            "balance": [{"id": k, "rate_s": v} for k, v in self.balance],
            "problems": list(self.problems),
            "surplus": [
                {"id": k, "rate_s": v, "note": "needs a sink (void or storage)"}
                for k, v in self.surplus
            ],
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
    gross: dict[str, float] = {}  # per item, the larger of what is made and used
    made: dict[str, float] = {}
    used: dict[str, float] = {}
    for node in plan.get("nodes") or ():
        x = float(node.get("crafts_s") or 0.0)
        if x <= 0:
            continue
        p, why = read(node)
        if p is None:
            problems.append(why or f"{node.get('recipe')}: unreadable")
            continue
        ref = str(node.get("recipe"))
        nonfree = [k for k in p.given if k != EU_KEY and k not in free_set]
        if ref.startswith("r:") and nonfree and not any(k != EU_KEY for k in p.used):
            # The export lists no input it uses up: never counted on (D234).
            problems.append(
                f"{ref} uses up nothing but gives {', '.join(sorted(nonfree))}"
            )
        for k, a in p.used.items():
            balance[k] = balance.get(k, 0.0) - a * x
            used[k] = used.get(k, 0.0) + a * x
        for k, a in p.given.items():
            balance[k] = balance.get(k, 0.0) + a * x
            made[k] = made.get(k, 0.0) + a * x
    for k in balance:
        gross[k] = max(made.get(k, 0.0), used.get(k, 0.0))

    def short(k: str) -> bool:
        # Float noise is relative to the item's own flow, never to another item's.
        return balance[k] < -max(ABS_TOL, TOL * gross[k])

    imports = tuple(
        (k, -v)
        for k, v in sorted(balance.items())
        if short(k) and k != EU_KEY and k not in free_set
    )
    free_used = tuple(
        (k, -v) for k, v in sorted(balance.items()) if short(k) and k in free_set
    )
    surplus = tuple(
        (k, v)
        for k, v in sorted(balance.items())
        if v > max(ABS_TOL, TOL * gross[k]) and k != EU_KEY and k not in free_set
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
    if balance.get(EU_KEY, 0.0) <= max(ABS_TOL, TOL * gross.get(EU_KEY, 0.0)):
        problems.append(
            f"net EU/t {net_eut:.6g} is not above 0 after the line's own use"
        )
    ok = not imports and not problems
    return Proof(
        ok,
        imports,
        net_eut,
        tuple(
            sorted(
                (k, 0.0 if abs(v) <= max(ABS_TOL, TOL * gross[k]) else v)
                for k, v in balance.items()
            )
        ),
        free_used,
        tuple(problems),
        surplus,
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
            {
                "id": k,
                "qty": math.ceil(v * startup_s),
                "startup_s": startup_s,
                "note": "an upper bound: every node's cycle, end to end",
            }
            for k, v in sorted(fuel.items())
        ],
    }
