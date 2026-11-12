"""The power designer's objective modes (WO-4.2, plan §7.2, D11, D13, D62, D65, D67, D233-D235).

A **power line** is a generator block, its fuel chain and its distribution. The fuel chain is
designed by the §7.1 engine (WO-3.3's graph, WO-3.2's machines, WO-3.4's staged MILP and no-good
cuts) with EU as an output: the generator is one more recipe of the network, fixed at the build
unit, that burns the fuel and gives EU; every machine of the chain draws its EU from it (its
sized draw, overclocks included), EU is never imported, and what is left is exported: the
line's **net EU/t**.

**Generators** (:func:`candidates`), the 2.9 controllers only (a legacy controller is never
offered for a new build, D232), each burning one fuel of its maps (WO-4.1's catalog):

- ``single_block``: the highest-tier single-block generator at the tier cap per fuel map, its
  voltage x amps out, the fuel at its efficiency (``fuels.single_block_eu``'s reading);
- ``gas_turbine`` and ``plasma_turbine``: the large turbine with the request's rotor at its
  optimal flow (``gtmath.turbine``), on the smallest dynamo hatch (1 A, up to the tier cap) that
  doesn't cap it;
- ``boiler_steam``: a 2.9 large boiler on a fluid fuel (``gtmath.boiler``) feeding a large steam
  turbine (an HP steam turbine for the superheated boilers) at its optimal flow: as many boilers
  as the flow needs, throttled by GT's circuit so their fuel follows the steam taken.

For each generator the fuels are pruned to the top :data:`TOP_FUELS` by EU per unit of fuel
(the request's ``candidates``; ``self_sustaining`` keeps only fuels a forward closure over the
DB's recipes reaches from the free set and the farms first, :func:`reachable`).

**Options** (D11): every candidate's line is solved on its own network; within it the chain's
primary choices give up to two routes through the same no-good cuts (``solve.routes``), so any
two options differ in the generator, the fuel or a primary choice of the chain. The pool is
ranked by ``planner/rank.py`` (:func:`rank.order_power`) for the mode and the best
``options`` (2-3) are returned with the weights printed.

**Modes** (plan §7.2's table, D13): ``best`` (the weighted score, ``me/prefs.md``'s weights, read
from the vault or else the template's defaults, D67), ``max_output`` (net EU/t at the tier cap
within a **scale bound**: one build unit, a generator count, a net EU/t cap or a raw-input budget
per hour), ``output_per_effort`` (net EU/t per effort hour, WO-3.5's effort model) and
``self_sustaining``: inputs only from the free set (:data:`DEFAULT_FREE`, configurable) or from
inside the line, farms (:mod:`.farms`) as virtual recipes powered by the plant, every other
import banned in the MILP; then the independent validator (:mod:`.sustain`) must prove zero
imports outside the free set and net EU/t above 0, and the line gets its bootstrap kit.

Every number is the DB's or GT's math on it, cited (``r:``, ``mte:``, ``rotor:``, ``farm:``).
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Any

from gregtec.config import GT_PIN
from gregtec.planner import (
    chains,
    effort,
    farms,
    fuels,
    generators,
    graph,
    machines,
    metrics,
    plan,
    rank,
    sizing,
    solve,
    sustain,
)
from gregtec.planner.graph import EU_KEY, Graph
from gregtec.planner.gtmath import boiler as gt_boiler
from gregtec.planner.gtmath import turbine as gt_turbine
from gregtec.planner.gtmath.tiers import VN, V
from gregtec.planner.gtmath.turbine import Kind
from gregtec.planner.machines import MachineOption
from gregtec.planner.request import (
    BaseInputs,
    Pin,
    Request,
    RequestError,
    is_stable_key,
    tier_index,
)

KIND = "power"
VERSION = 1
UNIT = "power:unit"  # the build-unit pseudo-item each generator gives one of per second
MODES = rank.POWER_MODES
SCALES = ("unit", "generators", "eut_cap", "raw_budget")
KINDS = ("single_block", "gas_turbine", "plasma_turbine", "boiler_steam")
TOP_FUELS = 4
CHAIN_ROUTES = 2  # routes per candidate (its chain's primary choices, no-good cuts)
# The free set (plan 7.2): water, air, cobble and stone generators, sunlight. Configurable.
DEFAULT_FREE = (
    "fluid:water",
    "fluid:air",
    "minecraft:cobblestone@0",
    "minecraft:stone@0",
    "sunlight",
)
DEFAULT_ROTOR = ("Steel", "large")  # D233: a question for Clayton
TEMPLATE_PREFS = chains.REPO_ROOT / "vault-template" / rank.PREFS
_TURBINE_CLASS = {
    "MTELargeTurbineGas": Kind.GAS,
    "MTELargeTurbinePlasma": Kind.PLASMA,
}
_STEAM_TURBINE = {False: "MTELargeTurbineSteam", True: "MTELargeTurbineHPSteam"}
# A power line's own parts (WO-4.3, D236): GT's turbine rotor item (MetaGeneratedTool01, the
# size by meta, the material in NBT) and GT's dynamo hatch class.
ROTOR_ITEM = "gregtech:gt.metatool.01"
DYNAMO_HATCH = "gregtech.api.metatileentity.implementations.MTEHatchDynamo"


class PowerError(RuntimeError):
    """No power line can be designed for the request; the message says why."""


def _simple(class_name: str) -> str:
    return class_name.rsplit(".", 1)[-1]


# --- the request -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PowerRequest:
    """What the power designer is asked for (see the module docstring)."""

    mode: str = "best"
    tier_cap: int | None = None  # None: My base's (D64)
    scale: str = "unit"
    bound: float | None = None  # generators: count; eut_cap: EU/t; raw_budget: per hour
    free: tuple[str, ...] = DEFAULT_FREE
    kinds: tuple[str, ...] = ()  # generator kinds to consider (all by default)
    fuels: tuple[str, ...] = ()  # fuels (stable keys) to consider (all by default)
    rotor: tuple[str, str] = DEFAULT_ROTOR  # turbine rotor material and size
    candidates: int = TOP_FUELS
    options: int = 3

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise RequestError(f"mode {self.mode!r}: one of {', '.join(MODES)}")
        if self.scale not in SCALES:
            raise RequestError(f"scale {self.scale!r}: one of {', '.join(SCALES)}")
        if self.scale == "unit":
            if self.bound is not None:
                raise RequestError("scale unit takes no bound")
        elif (
            self.bound is None
            or isinstance(self.bound, bool)
            or not math.isfinite(float(self.bound))
            or float(self.bound) <= 0
        ):
            raise RequestError(f"scale {self.scale} needs a bound above 0")
        if self.scale == "generators" and float(self.bound or 0) != int(
            self.bound or 0
        ):
            raise RequestError("a generator count is a whole number")
        if self.tier_cap is not None:
            object.__setattr__(self, "tier_cap", tier_index(self.tier_cap))
        free = tuple(sorted(set(self.free)))
        for k in free:
            if not (is_stable_key(k) or k in sustain.FREE_KEYS):
                raise RequestError(f"free: {k!r} is not an item or fluid key")
        object.__setattr__(self, "free", free)
        bad = sorted(set(self.kinds) - set(KINDS))
        if bad:
            raise RequestError(f"kinds: {', '.join(bad)}; one of {', '.join(KINDS)}")
        object.__setattr__(self, "kinds", tuple(sorted(set(self.kinds))))
        for k in self.fuels:
            if not is_stable_key(k):
                raise RequestError(f"fuels: {k!r} is not an item or fluid key")
        object.__setattr__(self, "fuels", tuple(sorted(set(self.fuels))))
        if self.rotor[1] not in generators.SIZES:
            raise RequestError(
                f"rotor size {self.rotor[1]!r}: one of {', '.join(generators.SIZES)}"
            )
        if not 1 <= self.candidates <= 16:
            raise RequestError("candidates: 1 to 16 fuels per generator")
        if not 1 <= self.options <= 3:
            raise RequestError("options: 1 to 3 (D11)")

    def as_dict(self) -> dict[str, Any]:
        return {
            "v": VERSION,
            "mode": self.mode,
            "tier_cap": self.tier_cap,
            "scale": self.scale,
            "bound": self.bound,
            "free": list(self.free),
            "kinds": list(self.kinds),
            "fuels": list(self.fuels),
            "rotor": list(self.rotor),
            "candidates": self.candidates,
            "options": self.options,
        }

    def digest(self) -> str:
        text = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- generators ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Machine:
    mte: int
    name: str | None
    kind: str  # singleblock | multiblock
    count: float  # per build unit
    tier: int | None = None


@dataclass(frozen=True)
class Gen:
    """One generator block on one fuel, per build unit, in steady state."""

    id: str
    kind: str
    label: str
    slot: str  # the generator choice this fuel is pruned within
    fuel: str  # the stable key burned
    fuel_ref: str  # r: of the fuel row
    fuel_per_s: float  # per build unit (L or items)
    water_per_s: float
    eut: int  # EU/t out, gross
    tier: int  # its output voltage tier
    machines: tuple[Machine, ...]
    cites: tuple[str, ...]
    parts: tuple[tuple[str, int], ...] = ()  # kept per unit: rotors, throttle circuits
    notes: tuple[str, ...] = ()
    # What the validator re-derives the ports from (sustain.gen_ports): the kind, the mte ids,
    # the fuel row, the rotor, the dynamo tier, the boilers and their throttle circuit.
    spec: tuple[tuple[str, Any], ...] = ()

    @property
    def eu_per_fuel(self) -> float:
        return self.eut * 20 / self.fuel_per_s if self.fuel_per_s > 0 else 0.0

    def ports(self) -> tuple[dict[str, float], dict[str, float]]:
        ins = {self.fuel: self.fuel_per_s}
        if self.water_per_s > 0:
            ins["fluid:water"] = self.water_per_s
        return ins, {EU_KEY: float(self.eut * 20)}

    def proc(self) -> solve.Proc:
        ins, outs = self.ports()
        outs = {**outs, UNIT: 1.0}
        return solve.Proc(
            self.id,
            tuple(sorted(ins.items())),
            tuple(sorted(outs.items())),
            1.0,
            (UNIT,),
        )


def _fuel_key(f: fuels.Fuel) -> str | None:
    key = f"fluid:{f.fluid}" if f.fluid else f.input
    return key if is_stable_key(key) else None


def _turbine_report(
    conn: sqlite3.Connection,
    kind: Kind,
    rotor: tuple[str, str],
    tier: int,
    fuel: str | None,
    run_id: int,
) -> generators.TurbineReport:
    """The turbine at its optimal flow on the smallest 1 A dynamo (up to ``tier``) that
    doesn't cap it."""
    top = generators.turbine(
        conn,
        kind=kind,
        material=rotor[0],
        size=rotor[1],
        dynamo_tier=tier,
        fuel=fuel,
        run_id=run_id,
    )
    need = top.runs[1].eut
    for d in range(1, tier + 1):
        if gt_turbine.dynamo_cap(d) >= need:
            if d == tier:
                return top
            return generators.turbine(
                conn,
                kind=kind,
                material=rotor[0],
                size=rotor[1],
                dynamo_tier=d,
                fuel=fuel,
                run_id=run_id,
            )
    return top


def _rotor_run(conn: sqlite3.Connection, run_id: int) -> int | None:
    row = conn.execute(
        "SELECT MAX(run_id) FROM rotor_material WHERE run_id = ?", (run_id,)
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def candidates(
    conn: sqlite3.Connection, run_id: int, req: PowerRequest, tier: int
) -> tuple[list[Gen], list[str]]:
    """Every generator x fuel the request allows at ``tier``, unpruned. Returns (gens, notes)."""
    cat = [e for e in generators.catalog(conn, run_id) if not e.legacy]
    rows = fuels.fuels(conn, run_id=run_id)
    by_map: dict[str, list[fuels.Fuel]] = {}
    for f in rows:
        by_map.setdefault(f.map, []).append(f)
    gens: list[Gen] = []
    notes: list[str] = []
    want = set(req.kinds) or set(KINDS)
    # Single blocks: per fuel map, the highest tier at the cap; for self_sustaining and
    # output_per_effort also the most efficient one at or below it (a lower tier burns less
    # fuel per EU, D233).
    if "single_block" in want:
        best: dict[str, generators.Entry] = {}
        thrifty: dict[str, generators.Entry] = {}
        for e in cat:
            if e.sizing != "single_block" or e.tier is None or e.tier > tier:
                continue
            if not e.efficiency or not e.eu_out or not e.amps_out:
                continue
            fmap = next((m for m in e.fuel_maps if m in fuels.BY_MAP), None)
            if fmap is None:
                continue
            cur = best.get(fmap)
            if cur is None or (e.tier, -e.mte_id) > (cur.tier or 0, -cur.mte_id):
                best[fmap] = e
            cur = thrifty.get(fmap)
            if cur is None or (e.efficiency, e.tier, -e.mte_id) > (
                cur.efficiency or 0,
                cur.tier or 0,
                -cur.mte_id,
            ):
                thrifty[fmap] = e
        picks = [best[m] for m in sorted(best)]
        if req.mode in ("self_sustaining", "output_per_effort"):
            picks += [thrifty[m] for m in sorted(thrifty) if thrifty[m] is not best[m]]
        for e in picks:
            fmap = next(m for m in e.fuel_maps if m in fuels.BY_MAP)
            assert e.efficiency and e.eu_out and e.amps_out and e.tier is not None
            eut = e.eu_out * e.amps_out
            for f in by_map.get(fmap, ()):
                key = _fuel_key(f)
                if key is None or f.fuel_value <= 0:
                    continue
                # GT's getFuelValue: EU per operation (1 L of a fluid, or one item), truncated
                # (HIGH-1 of the WO-4.2 review: never the exact fraction).
                per = Fraction(
                    fuels.single_block_eu(
                        special=f.fuel_value,
                        efficiency=e.efficiency,
                        fluid_input=f.fluid is not None,
                    )
                )
                if per <= 0:
                    notes.append(f"{e.ref} on {f.ref}: GT gives 0 EU per unit")
                    continue
                gens.append(
                    Gen(
                        id=f"gen:single/{e.ref}/{f.ref}",
                        kind="single_block",
                        label=f"{e.name} on {f.fluid or f.input}",
                        slot=e.ref,
                        fuel=key,
                        fuel_ref=f.ref,
                        fuel_per_s=float(Fraction(eut * 20) / per),
                        water_per_s=0.0,
                        eut=eut,
                        tier=e.tier,
                        machines=(
                            Machine(e.mte_id, e.name, "singleblock", 1.0, e.tier),
                        ),
                        cites=(e.ref, f.ref),
                        spec=(
                            ("kind", "single_block"),
                            ("mte", e.mte_id),
                            ("fuel_ref", f.ref),
                        ),
                    )
                )
    rotor_run = _rotor_run(conn, run_id)
    turbines = {_simple(e.class_name): e for e in cat if e.sizing == "turbine"}
    if rotor_run is None and want & {"gas_turbine", "plasma_turbine", "boiler_steam"}:
        notes.append("no turbine rotors in the DB (gt_power.json): turbines left out")
    for cls, kind in sorted(_TURBINE_CLASS.items()):
        name = "gas_turbine" if kind is Kind.GAS else "plasma_turbine"
        e = turbines.get(cls)
        if name not in want or e is None or rotor_run is None or tier < 1:
            continue
        for f in by_map.get(fuels.BY_NAME[generators.TURBINE_FUEL_MAP[kind]].map, ()):
            key = _fuel_key(f)
            if key is None or not f.fluid:
                continue
            try:
                r = _turbine_report(conn, kind, req.rotor, tier, f.fluid, rotor_run)
            except (generators.CatalogError, ValueError) as exc:
                notes.append(f"{e.ref} on {f.fluid}: {exc}")
                continue
            run = r.runs[1]
            if run.eut <= 0 or run.flow <= 0:
                continue
            gens.append(
                Gen(
                    id=f"gen:{name}/{e.ref}/{f.ref}/{r.refs[0]}/d{r.dynamo_tier}",
                    kind=name,
                    label=f"{e.name} on {f.fluid}, {r.material} {r.size} rotor",
                    slot=e.ref,
                    fuel=key,
                    fuel_ref=f.ref,
                    fuel_per_s=run.flow * 20 / run.ticks,
                    water_per_s=0.0,
                    eut=run.eut,
                    tier=r.dynamo_tier,
                    machines=(Machine(e.mte_id, e.name, "multiblock", 1.0),),
                    cites=(e.ref, *r.refs),
                    parts=(
                        (f"rotor:{r.material}/{r.size}", 1),
                        (f"dynamo hatch {VN[r.dynamo_tier]} 1A", 1),
                    ),
                    spec=(
                        ("kind", name),
                        ("mte", e.mte_id),
                        ("fuel_ref", f.ref),
                        ("rotor", (r.material, r.size)),
                        ("dynamo_tier", r.dynamo_tier),
                    ),
                )
            )
    if "boiler_steam" in want and rotor_run is not None and tier >= 1:
        boilers = sorted(
            (e for e in cat if _simple(e.class_name) in gt_boiler.BOILERS),
            key=lambda e: e.mte_id,
        )
        fake = [
            f
            for f in by_map.get(fuels.BY_NAME["largeBoilerFakeFuels"].map, ())
            if f.fluid and f.unit == fuels.Unit.BOILER_TICKS.value
        ]
        for b in boilers:
            spec = gt_boiler.BOILERS[_simple(b.class_name)]
            t = turbines.get(_STEAM_TURBINE[spec.superheated])
            if t is None:
                continue
            kind = Kind.HP_STEAM if spec.superheated else Kind.STEAM
            try:
                r = _turbine_report(conn, kind, req.rotor, tier, None, rotor_run)
            except (generators.CatalogError, ValueError) as exc:
                notes.append(f"{t.ref}: {exc}")
                continue
            run = r.runs[1]
            if run.eut <= 0:
                continue
            for f in fake:
                assert f.fluid is not None
                try:
                    st = gt_boiler.steady(spec, fluid=f.fluid, duration=int(f.value))
                except gt_boiler.BoilerError:
                    continue
                need = Fraction(run.flow) / st.steam  # boilers per turbine, unthrottled
                count = math.ceil(need)
                per_boiler = Fraction(run.flow) / count
                # The most throttle that still gives the turbine its flow: the boilers burn
                # (and boil water) at that throttle, the little steam above the flow included.
                throttle = max(
                    c
                    for c in range(gt_boiler.MAX_CIRCUIT + 1)
                    if gt_boiler.steady(
                        spec, fluid=f.fluid, duration=int(f.value), circuit=c
                    ).steam
                    >= per_boiler
                )
                st = gt_boiler.steady(
                    spec, fluid=f.fluid, duration=int(f.value), circuit=throttle
                )
                surplus = st.steam * count - run.flow
                gens.append(
                    Gen(
                        id=f"gen:boiler_steam/{b.ref}/{f.ref}/{t.ref}/{r.refs[0]}/d{r.dynamo_tier}",
                        kind="boiler_steam",
                        label=(
                            f"{count} x {b.name} on {f.fluid} -> {t.name}, "
                            f"{r.material} {r.size} rotor"
                        ),
                        slot=f"{b.ref}+{t.ref}",
                        fuel=f"fluid:{f.fluid}",
                        fuel_ref=f.ref,
                        fuel_per_s=float(count * st.fuel * 20),
                        water_per_s=float(count * st.water * 20),
                        eut=run.eut,
                        tier=r.dynamo_tier,
                        machines=(
                            Machine(t.mte_id, t.name, "multiblock", 1.0),
                            Machine(b.mte_id, b.name, "multiblock", float(count)),
                        ),
                        cites=(t.ref, b.ref, f.ref, *r.refs),
                        parts=(
                            (f"rotor:{r.material}/{r.size}", 1),
                            (f"dynamo hatch {VN[r.dynamo_tier]} 1A", 1),
                            *(
                                (
                                    (
                                        f"programmed circuit {throttle} (boiler throttle)",
                                        count,
                                    ),
                                )
                                if throttle
                                else ()
                            ),
                        ),
                        notes=(
                            (
                                f"{run.flow} L/t of {spec.steam_fluid} from {count} boilers"
                                f" ({float(need):.4g} at full burn), throttled with circuit"
                                f" {throttle}: {float(surplus):.4g} L/t over the turbine's"
                                " flow (its fuel and water are counted)"
                            ),
                        ),
                        spec=(
                            ("kind", "boiler_steam"),
                            ("boiler_mte", b.mte_id),
                            ("turbine_mte", t.mte_id),
                            ("fuel_ref", f.ref),
                            ("rotor", (r.material, r.size)),
                            ("dynamo_tier", r.dynamo_tier),
                            ("boilers", count),
                            ("circuit", throttle),
                        ),
                    )
                )
    out = [
        g
        for g in gens
        if (not req.fuels or g.fuel in req.fuels) and g.tier <= max(tier, 1)
    ]
    return out, notes


def prune(
    gens: Iterable[Gen], keep: int, levels: Mapping[str, int] | None = None
) -> list[Gen]:
    """Per generator, the ``keep`` fuels with the most EU per unit of fuel. With ``levels``
    (``self_sustaining``): only fuels the closure reaches, the fewest steps from the free set
    first (all of those at the lowest level, at least), then the most EU per unit of fuel
    (D233)."""
    by_slot: dict[tuple[str, str], list[Gen]] = {}
    for g in gens:
        if levels is not None and g.fuel not in levels:
            continue
        by_slot.setdefault((g.kind, g.slot), []).append(g)
    out: list[Gen] = []
    for slot in sorted(by_slot):
        ranked = sorted(
            by_slot[slot],
            key=lambda g: (
                levels[g.fuel] if levels is not None else 0,
                -g.eu_per_fuel,
                g.fuel,
                g.id,
            ),
        )
        n = keep
        if levels is not None and ranked:
            # Every fuel the fewest steps from the free set, however many (D233).
            low = levels[ranked[0].fuel]
            n = max(keep, sum(1 for g in ranked if levels[g.fuel] == low))
        out.extend(ranked[:n])
    return out


# --- reachability (self_sustaining's fuel pre-filter) -----------------------------------------


def reachable(
    conn: sqlite3.Connection, run_id: int, tier: int, start: Iterable[str]
) -> dict[str, int]:
    """Every key a forward closure over the run's enabled recipes at ``tier`` reaches from
    ``start``, with its level (recipe steps from ``start``, breadth first): a recipe fires once
    each input it uses up is reached (an oredict slot by any member, a wildcard by any meta).
    Machines are not checked: a pre-filter only."""
    item = graph._ITEM_KEY_SQL
    needs: dict[int, set[str]] = {}
    for rid, key in conn.execute(
        "SELECT r.id, CASE WHEN ri.ore_id IS NOT NULL THEN 'ore:' || o.name"
        f" WHEN ri.fluid_id IS NOT NULL THEN 'fluid:' || f.name ELSE {item} END"
        " FROM recipe AS r JOIN recipe_input AS ri ON ri.recipe_id = r.id"
        " LEFT JOIN ore AS o ON o.id = ri.ore_id LEFT JOIN fluid AS f ON f.id = ri.fluid_id"
        " LEFT JOIN item AS i ON i.id = ri.item_id"
        " WHERE r.run_id = ? AND r.enabled = 1 AND NOT coalesce(r.fake, 0)"
        " AND coalesce(r.voltage_tier, 0) <= ? AND ri.consumed = 1 AND ri.amount > 0",
        (run_id, tier),
    ):
        if key is not None:
            needs.setdefault(int(rid), set()).add(str(key))
    gives: dict[int, set[str]] = {}
    for rid, key in conn.execute(
        "SELECT r.id, CASE WHEN ro.fluid_id IS NOT NULL THEN 'fluid:' || f.name"
        f" ELSE {item} END FROM recipe AS r JOIN recipe_output AS ro ON ro.recipe_id = r.id"
        " LEFT JOIN fluid AS f ON f.id = ro.fluid_id LEFT JOIN item AS i ON i.id = ro.item_id"
        " WHERE r.run_id = ? AND r.enabled = 1 AND NOT coalesce(r.fake, 0)"
        " AND coalesce(r.voltage_tier, 0) <= ?",
        (run_id, tier),
    ):
        if key is not None and int(rid) in needs:
            gives.setdefault(int(rid), set()).add(str(key))
    ores: dict[str, set[str]] = {}
    for name, key in conn.execute(
        f"SELECT o.name, {item} FROM ore AS o JOIN ore_member AS om ON om.ore_id = o.id"
        " JOIN item AS i ON i.id = om.item_id"
    ):
        ores.setdefault(str(key), set()).add(f"ore:{name}")
    users: dict[str, list[int]] = {}
    left: dict[int, int] = {}
    for rid, keys in needs.items():
        left[rid] = len(keys)
        for k in keys:
            users.setdefault(k, []).append(rid)
    level: dict[str, int] = {}
    layer = sorted(set(start))
    depth = 0
    while layer:
        nxt: set[str] = set()
        todo = list(layer)
        while todo:  # slot keys reached through a member share the member's level
            k = todo.pop()
            if k in level:
                continue
            level[k] = depth
            todo.extend(ores.get(k, ()))
            if "@" in k and not k.startswith("fluid:"):
                reg = k.split("#", 1)[0].rsplit("@", 1)[0]
                todo.append(f"{reg}@{graph.WILDCARD}")
            for rid in users.get(k, ()):
                left[rid] -= 1
                if left[rid] == 0:
                    nxt.update(gives.get(rid, ()))
        layer = sorted(nxt - set(level))
        depth += 1
    return level


# --- the line ---------------------------------------------------------------------------------


@dataclass
class Chain:
    """A fuel's chain: the §7.1 engine's graph, machines and network for one fuel."""

    graph: Graph
    request: Request
    sized: Mapping[str, sizing.Sized]
    net: solve.Network
    skipped: tuple[str, ...] = ()  # recipes that make something from nothing (left out)


def _eu_per_craft(o: MachineOption) -> float:
    cps = float(o.sizing.crafts_per_second.value)
    return float(o.sizing.eut.value) * 20 / cps if cps > 0 else math.inf


def pick(options: Sequence[MachineOption]) -> MachineOption | None:
    """The machine a power line's chain runs a recipe on: base machines, unbadged first, then
    the least EU per craft (an overclock doubles it, and every EU comes out of the line's own
    output, D233), then the fewest machines (the most crafts per second) and the mte id. Steam
    machines only when nothing else runs the recipe (their steam is not planned)."""
    usable = [o for o in options if not o.addon and o.kind != "steam"]
    if not usable:
        return sizing.pick(tuple(options))
    return min(
        usable,
        key=lambda o: (
            bool(o.badges),
            _eu_per_craft(o),
            -float(o.sizing.crafts_per_second.value),
            len(o.hatches),
            o.mte_id,
            o.tier,
        ),
    )


def size(
    conn: sqlite3.Connection, g: Graph, player: machines.Player
) -> dict[str, sizing.Sized]:
    """WO-3.4's sizing (:func:`sizing.size`) with the power line's pick: multiblocks sized
    with one and two energy hatches of every tier up to the cap, so a recipe can run without
    overclocks."""
    p = replace(player, hatches=None)
    loaded: dict[str, list[machines.MachineInfo]] = {}
    out: dict[str, sizing.Sized] = {}
    for rec in g.recipes:
        if "manual" in rec.flags or rec.map is None:
            out[rec.id] = sizing.Sized(rec.id, None)
            continue
        info = machines.load_recipe(conn, rec.id, g.run_id)
        if info.map not in loaded:
            loaded[info.map] = machines.load_machines(conn, info.map, g.run_id)
        sel = machines.choose(info, loaded[info.map], p)
        out[rec.id] = sizing.Sized(rec.id, pick(sel.options))
    return out


def _chain(
    conn: sqlite3.Connection,
    fuel: str,
    req: PowerRequest,
    base: BaseInputs,
    tier: int,
    run_id: int,
    templates: chains.Templates | None,
    pin: str | None = None,
) -> Chain:
    boundary = tuple(k for k in req.free if is_stable_key(k) and k != fuel)
    chain_req = Request(
        fuel,
        qty=1.0,
        unit="/s",
        tier_cap=tier,
        boundary=boundary,
        routes=2,
        pins=(Pin(fuel, pin),) if pin else (),
    )
    g = graph.build(conn, chain_req, base, templates=templates, run_id=run_id)
    sized = size(conn, g, graph.player(chain_req, base))
    net = plan.network(g, chain_req, sized, base_only=False)
    procs = []
    skipped = []
    for p in net.procs:
        if not [k for k, _ in p.inputs if k != EU_KEY]:
            # The export lists no input this recipe uses up (an input it couldn't key): a
            # power line never counts on it (D233).
            skipped.append(p.id)
            continue
        s = sized.get(p.id)
        eu = 0.0
        if s is not None and s.option is not None and s.crafts_per_s > 0:
            eu = float(s.option.sizing.eut.value) * 20 / s.crafts_per_s
        ins = [(k, a) for k, a in p.inputs if k != EU_KEY]
        if eu > 0:
            ins.append((EU_KEY, eu))
        procs.append(replace(p, inputs=tuple(sorted(ins))))
    return Chain(
        g, chain_req, sized, replace(net, procs=tuple(procs)), tuple(sorted(skipped))
    )


def fuel_chains(
    conn: sqlite3.Connection,
    fuel: str,
    req: PowerRequest,
    base: BaseInputs,
    tier: int,
    run_id: int,
    templates: chains.Templates | None,
    levels: Mapping[str, int] | None = None,
) -> tuple[list[Chain], list[str]]:
    """A fuel's chains: the graph's own (its chosen recipe for the fuel), and one pinned to
    each other candidate the graph keeps for the fuel, side routes included (a fuel is often a
    byproduct: creosote of a pyrolyse oven), up to :data:`graph.TOP_N` in all (D61, D233).
    With ``levels`` (``self_sustaining``) the pins are the fuel's candidates (any status but
    rejected) whose inputs the closure reaches, the fewest steps from the free set first.
    Returns (chains, why each failed)."""
    out: list[Chain] = []
    errors: list[str] = []
    try:
        first = _chain(conn, fuel, req, base, tier, run_id, templates)
    except (graph.GraphError, RequestError, LookupError) as exc:
        return [], [f"fuel chain: {exc}"]
    out.append(first)
    item = first.graph.item(fuel)
    others = [c.recipe for c in item.candidates if c.status == "alternative"]
    if levels is not None:
        ranked = []
        for i, c in enumerate(item.candidates):
            if c.status in ("chosen", "rejected"):
                continue
            ports = sustain.db_ports(conn, c.recipe, run_id)
            if ports is None:
                continue
            need = [levels.get(k) for k in ports.used]
            if any(v is None for v in need):
                continue
            ranked.append((max([0, *(v for v in need if v is not None)]), i, c.recipe))
        others = [ref for _, _, ref in sorted(ranked)]
    for ref in others[: graph.TOP_N - 1]:
        try:
            out.append(_chain(conn, fuel, req, base, tier, run_id, templates, ref))
        except (graph.GraphError, RequestError, LookupError) as exc:
            errors.append(f"fuel chain pinned to {ref}: {exc}")
    return out, errors


def _yield(f: farms.Farm) -> tuple[float, str]:
    """A farm's products per EU, for picking one farm per product (most first)."""
    made = math.fsum(a for _, a in f.outputs)
    return (-(made / f.eu_per_op) if f.eu_per_op > 0 else -math.inf, f.id)


def best_farms(farm_list: Iterable[farms.Farm]) -> list[farms.Farm]:
    """One farm per product: the most per EU (a sub-step's simplest choice, D11, D233)."""
    best: dict[str, farms.Farm] = {}
    for f in sorted(farm_list, key=_yield):
        for k, _ in f.outputs:
            best.setdefault(k, f)
    return sorted({f.id: f for f in best.values()}.values(), key=lambda f: f.id)


def _members(
    conn: sqlite3.Connection, g: Graph, farm_list: Sequence[farms.Farm]
) -> list[solve.Proc]:
    """``member:`` recipes: an oredict or wildcard slot of the graph from a member the line
    farms itself (a tree farm's logs into ``ore:logWood``): per slot one member, the one whose
    farm gives the most per EU."""
    out = []
    for slot in sorted(i.id for i in g.items if i.kind in ("ore", "wildcard")):
        for f in sorted(farm_list, key=_yield):
            m = next(
                (k for k, _ in f.outputs if sustain.is_member(conn, slot, k)), None
            )
            if m is not None:
                out.append(
                    solve.Proc(
                        f"member:{slot}|{m}", ((m, 1.0),), ((slot, 1.0),), 0.0, (slot,)
                    )
                )
                break
    return out


def network(
    conn: sqlite3.Connection,
    chain: Chain,
    gen: Gen,
    units: float,
    req: PowerRequest,
    farm_list: Sequence[farms.Farm] = (),
) -> solve.Network:
    """One candidate's network: the chain, the generator fixed at ``units`` build units (the
    target), and for ``self_sustaining`` the farms, slot members and the import ban."""
    procs = [*chain.net.procs, gen.proc()]
    free_in = set(chain.net.free_in) - {EU_KEY}
    banned = {EU_KEY}
    if req.mode == "self_sustaining":
        chosen = best_farms(farm_list)
        procs.extend(f.proc() for f in chosen)
        procs.extend(_members(conn, chain.graph, chosen))
        free_in = set(req.free)
        banned = {k for p in procs for k, _ in p.inputs if k not in free_in}
    return solve.Network(
        procs=tuple(procs),
        targets=((UNIT, float(units)),),
        free_in=frozenset(free_in),
        free_out=frozenset(set(chain.net.free_out) | {EU_KEY, UNIT}),
        choices=chain.net.choices,
        banned=frozenset(banned),
    )


# --- one option as a plan ---------------------------------------------------------------------


def _machine(
    conn: sqlite3.Connection,
    mte: int | None,
    name: str | None,
    kind: str,
    tier: int | None,
) -> dict[str, Any] | None:
    if mte is None:
        return None
    if name is None:
        row = conn.execute(
            "SELECT name_en FROM machine WHERE mte_id = ?", (mte,)
        ).fetchone()
        name = row[0] if row else None
    return {
        "mte": mte,
        "name": name,
        "kind": kind,
        "tier": VN[tier] if tier is not None else None,
        "addon": False,
        "confidence": 1.0,
    }


def _upkeep(boundary_in: Sequence[Mapping[str, Any]], free: frozenset[str]) -> float:
    """Inputs from outside the free set per hour (fluids per 1000 L)."""
    return math.fsum(
        p["rate_s"] * 3600 / (1000 if str(p["id"]).startswith("fluid:") else 1)
        for p in boundary_in
        if p["id"] not in free
    )


@dataclass
class Option:
    gen: Gen
    chain: Chain
    units: float
    flow: solve.Flow
    net: solve.Network


def build_plan(
    conn: sqlite3.Connection,
    run_key: str,
    req: PowerRequest,
    opt: Option,
    farm_map: Mapping[str, farms.Farm],
    n: int,
) -> dict[str, Any]:
    """The option as plan JSON: the chain as a §7.1 plan (WO-3.4's ``plan._plan``), then the
    generator, farms and slot members as nodes of their own, and the ``power`` section."""
    g, gen, flow = opt.chain.graph, opt.gen, opt.flow
    ids = {r.id for r in g.recipes}
    sub_net = replace(opt.net, procs=tuple(p for p in opt.net.procs if p.id in ids))
    sub_flow = replace(
        flow,
        crafts=tuple((pid, x) for pid, x in flow.crafts if pid in ids),
        imports=tuple((k, v) for k, v in flow.imports if k not in (EU_KEY, UNIT)),
        exports=tuple((k, v) for k, v in flow.exports if k not in (EU_KEY, UNIT)),
    )
    fuel_rate = gen.fuel_per_s * opt.units
    chain_req = replace(opt.chain.request, qty=max(fuel_rate, 1e-12))
    p = plan._plan(
        g, chain_req, run_key, sub_net, sub_flow, opt.chain.sized, "base", n, False
    )
    nodes = p["nodes"]
    for node in nodes:
        node["kind"] = "recipe"
    node_of = {node["recipe"]: node["n"] for node in nodes}
    extra: list[dict[str, Any]] = []
    machines_used: list[dict[str, Any]] = []
    for pid, x in flow.crafts:
        if pid in ids or x <= 0:
            continue
        nid = f"n{len(nodes) + len(extra) + 1}"
        node_of[pid] = nid
        if pid == gen.id:
            ins, outs = gen.ports()
            node: dict[str, Any] = {
                "n": nid,
                "recipe": pid,
                "kind": "generator",
                "label": gen.label,
                "crafts_s": plan._num(x),
                "count": math.ceil(round(x, 9)),
                "machine": _machine(
                    conn,
                    gen.machines[0].mte,
                    gen.machines[0].name,
                    gen.machines[0].kind,
                    gen.machines[0].tier,
                ),
                "machines": [
                    {
                        "mte": m.mte,
                        "name": m.name,
                        "count": math.ceil(round(m.count * x, 9)),
                        "load": plan._num(m.count * x),
                    }
                    for m in gen.machines
                ],
                "eut": gen.eut,
                "eut_avg": 0.0,
                "ports": {
                    "in": {k: plan._num(v) for k, v in ins.items()},
                    "out": {k: plan._num(v) for k, v in outs.items()},
                },
                "fuel": {
                    "id": gen.fuel,
                    "rate_s": plan._num(gen.fuel_per_s * x),
                    "ref": gen.fuel_ref,
                },
                "parts": {k: v * math.ceil(round(x, 9)) for k, v in gen.parts},
                "spec": {
                    k: list(v) if isinstance(v, tuple) else v for k, v in gen.spec
                },
                "cites": list(gen.cites),
                "notes": list(gen.notes),
            }
            for m in gen.machines:
                machines_used.append(
                    {
                        "machine": {"mte": m.mte, "kind": m.kind},
                        "count": math.ceil(round(m.count * x, 9)),
                    }
                )
        elif pid.startswith("farm:"):
            f = farm_map[pid]
            count = math.ceil(round(x * f.seconds, 9))
            node = {
                "n": nid,
                "recipe": pid,
                "kind": "farm",
                "label": f.name,
                "source": f.source,
                "cite": f.cite,
                "crafts_s": plan._num(x),
                "count": count,
                "machine": _machine(conn, f.machine, None, "multiblock", None),
                "eut": f.eut,
                "eut_avg": plan._num(f.eu_per_op * x / 20),
                "util": plan._num(x * f.seconds / count) if count else None,
                "cycle_s": f.seconds,
            }
            if f.machine is not None:
                machines_used.append(
                    {
                        "machine": {"mte": f.machine, "kind": "multiblock"},
                        "count": count,
                    }
                )
        else:
            node = {"n": nid, "recipe": pid, "kind": "member", "crafts_s": plan._num(x)}
        extra.append(node)
    nodes.extend(extra)
    p["edges"] = plan._edges(opt.net, flow, node_of)
    rate = {k: v for k, v in flow.exports}
    gross = gen.eut * opt.units
    chain_eut = math.fsum(float(n.get("eut_avg") or 0.0) for n in nodes)
    net_eut = rate.get(EU_KEY, 0.0) / 20
    tier = gen.tier
    p.update(
        id=_option_id(req, run_key, gen, flow),
        kind=KIND,
        target={"id": EU_KEY, "eut": plan._num(net_eut)},
        tier_cap=p["tier_cap"],
        request=req.as_dict(),
        power={
            "mode": req.mode,
            "units": plan._num(opt.units),
            "scale": {"kind": req.scale, "bound": req.bound},
            "generator": {
                "id": gen.id,
                "kind": gen.kind,
                "label": gen.label,
                "eut": gen.eut,
                "tier": VN[tier],
                "cites": list(gen.cites),
            },
            "fuel": {
                "id": gen.fuel,
                "ref": gen.fuel_ref,
                "rate_s": plan._num(fuel_rate),
            },
            "gross_eut": plan._num(gross),
            "chain_eut": plan._num(chain_eut),
            "net_eut": plan._num(net_eut),
            "distribution": {
                "tier": VN[tier],
                "voltage": V[tier],
                "amps": math.ceil(round(net_eut / V[tier], 9)) if net_eut > 0 else 0,
                "note": "cables and transformers: WO-4.5",
            },
        },
        machines_used=machines_used,
    )
    return p


def _option_id(req: PowerRequest, run_key: str, gen: Gen, flow: solve.Flow) -> str:
    text = json.dumps([req.digest(), run_key, gen.id, list(flow.choice)])
    return "pw-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def rotor_key(
    conn: sqlite3.Connection, run_id: int, material: str, size: str
) -> str | None:
    """The stable key of GT's turbine rotor of ``material`` and ``size`` (its meta from the
    export's ``rotor_size`` row, its material from the item's ``GT.ToolStats`` NBT), or None
    when the DB lists no such item."""
    row = conn.execute(
        f"SELECT {graph._ITEM_KEY_SQL} FROM item AS i JOIN rotor_size AS s"
        " ON s.size = i.meta AND s.run_id = ?"
        " WHERE i.registry_name = ? AND s.name = ? AND json_valid(i.nbt_json)"
        " AND json_extract(i.nbt_json, '$.\"GT.ToolStats\".PrimaryMaterial') = ?"
        " ORDER BY json_extract(i.nbt_json, '$.\"GT.ToolStats\".SecondaryMaterial') IS NOT ?,"
        " i.nbt_hash LIMIT 1",
        (run_id, ROTOR_ITEM, size, material, material),
    ).fetchone()
    return str(row[0]) if row else None


def hatch_mte(conn: sqlite3.Connection, class_name: str, tier: int) -> int | None:
    """GT's hatch of ``class_name`` at ``tier`` (its MTE id; the lowest of several)."""
    row = conn.execute(
        "SELECT MIN(mte_id) FROM machine WHERE class_name = ? AND tier = ?",
        (class_name, tier),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def parts(
    conn: sqlite3.Connection, p: Mapping[str, Any], run_id: int
) -> tuple[list[effort.Component], list[str], int]:
    """The build components a power line chooses itself (WO-4.3, D236), per generator
    block: its turbine rotor (the request's material and size) and its dynamo hatch (the tier
    the line sized it on, 1 A), each as many as the line builds generators. Each is made by the
    effort model like any component. What the line doesn't choose is listed, not counted;
    the last value is how many parts the DB has no item for (the effort's ``uncounted``)."""
    out: list[effort.Component] = []
    notes: list[str] = []
    missing = 0
    for node in p["nodes"]:
        if node.get("kind") != "generator" or not node.get("count"):
            continue
        spec, count = node.get("spec") or {}, int(node["count"])
        of = int(spec.get("turbine_mte") or spec.get("mte") or node["machine"]["mte"])
        if spec.get("rotor"):
            material, size = spec["rotor"]
            key = rotor_key(conn, run_id, material, size)
            if key is None:
                missing += 1
                notes.append(
                    f"rotor {material} {size}: no such item in the DB, its making is not"
                    " counted"
                )
            else:
                out.append(effort.Component(key, count, of, "rotor"))
        if spec.get("dynamo_tier") is not None:
            tier = int(spec["dynamo_tier"])
            mte = hatch_mte(conn, DYNAMO_HATCH, tier)
            item = effort.machine_items(conn, [mte]).get(mte) if mte else None
            if item is None:
                missing += 1
                notes.append(
                    f"dynamo hatch {VN[tier]}: no such machine item in the DB, its making"
                    " is not counted"
                )
            else:
                out.append(effort.Component(item, count, of, "hatch"))
        if spec.get("kind") in ("gas_turbine", "plasma_turbine", "boiler_steam"):
            notes.append(
                f"{node['n']}: its other hatches (maintenance, input, output, muffler), the"
                " pipes between blocks and the boilers' throttle circuits are not counted:"
                " GT's hatch minimums aren't in the DB and their tiers and the layout are the"
                " builder's (D236)"
            )
    if any(n.get("kind") == "generator" for n in p["nodes"]):
        notes.append("distribution cables are not counted: cable runs come with WO-4.5")
    return out, notes, missing


def _metrics(
    conn: sqlite3.Connection,
    p: dict[str, Any],
    g: Graph,
    measure: effort.Effort | None,
    table: Mapping[str, metrics.Weight],
    free: frozenset[str],
) -> dict[str, Any]:
    chain_nodes = [n for n in p["nodes"] if str(n["recipe"]).startswith("r:")]
    chain_plan = {**p, "nodes": chain_nodes, "target": {"id": p["power"]["fuel"]["id"]}}
    eff_plan = {"nodes": [*chain_nodes, *p["machines_used"]]}
    if measure is not None:
        extra, extra_notes, extra_missing = parts(conn, p, g.run_id)
        eff = measure.route(eff_plan, extra, extra_notes, extra_missing)
    else:
        eff = {
            "eff_h": 0.0,
            "build_h": 0.0,
            "unlock_h": 0.0,
            "craft_s": effort.CRAFT_S,
            "rows": [],
            "components": [],
            "unlocks": [],
            "uncounted": 0,
            "notes": ["effort not computed"],
        }
    m = metrics.route(
        conn, g, chain_plan, measure.on_map if measure else {}, table, eff
    )
    virtual = [n for n in p["nodes"] if not str(n["recipe"]).startswith("r:")]
    extra_machines = sum(int(u["count"]) for u in p["machines_used"])
    m["machines"] += extra_machines
    m["power_machines"] = [
        {"mte": u["machine"]["mte"], "count": u["count"]} for u in p["machines_used"]
    ]
    c = dict(m["complexity"])
    c["nodes"] += len(virtual)
    c["score"] += len(virtual)
    m["complexity"] = c
    m["net_eut"] = p["power"]["net_eut"]
    m["gross_eut"] = p["power"]["gross_eut"]
    m["chain_eut"] = p["power"]["chain_eut"]
    m["upkeep"] = _upkeep(p["boundary"]["in"], free)
    return m


# --- the designer -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PowerDesign:
    request: PowerRequest
    tier_cap: int
    options: tuple[dict[str, Any], ...]  # ranked, at most request.options
    pool: int  # options solved and ranked
    rejected: tuple[tuple[str, str], ...]  # candidate -> why
    weights: str  # the weights line (rank.describe_power)
    notes: tuple[str, ...]


def read_prefs(vault: Path | None) -> rank.Prefs:
    """``me/prefs.md`` from the vault (read only), else the template's defaults (D67)."""
    if vault is not None and (vault / rank.PREFS).is_file():
        return rank.read_prefs(vault)
    try:
        return rank.parse_prefs(TEMPLATE_PREFS.read_text(encoding="utf-8"))
    except OSError:
        return rank.BALANCED


def _units(
    req: PowerRequest, net_unit: float, upkeep_unit: float
) -> tuple[float | None, str]:
    """Build units within the scale bound, from one unit's net EU/t and upkeep per hour;
    (None, why) when even one unit is over the bound."""
    if req.scale == "unit":
        return 1.0, ""
    bound = float(req.bound or 0)
    if req.scale == "generators":
        return bound, ""
    if req.scale == "eut_cap":
        if net_unit <= 0:
            return None, "no net EU/t"
        n = math.floor(round(bound / net_unit, 9))
        return (
            (float(n), "")
            if n >= 1
            else (None, f"one unit makes {net_unit:.6g} EU/t, over the cap")
        )
    if upkeep_unit <= 0:
        return 1.0, ""  # design() sizes it like the best bounded line
    n = math.floor(round(bound / upkeep_unit, 9))
    return (
        (float(n), "")
        if n >= 1
        else (None, f"one unit takes {upkeep_unit:.6g}/h, over the budget")
    )


def design(
    conn: sqlite3.Connection,
    req: PowerRequest,
    base: BaseInputs,
    *,
    prefs: rank.Prefs = rank.BALANCED,
    run_id: int | None = None,
    templates: chains.Templates | None = None,
    with_effort: bool = True,
    farms_path: Path = farms.FARMS,
) -> PowerDesign:
    """The ranked power-line options for ``req`` (see the module docstring)."""
    run = graph.machines.current_run(conn) if run_id is None else run_id
    run_key = plan._run_key(conn, run)
    tier = req.tier_cap if req.tier_cap is not None else base.tier_cap()
    free = frozenset(req.free)
    notes: list[str] = []
    gens, cnotes = candidates(conn, run, req, tier)
    notes.extend(cnotes)
    farm_list: list[farms.Farm] = []
    levels: dict[str, int] | None = None
    if req.mode == "self_sustaining":
        farm_list, fnotes = farms.load(conn, run, farms_path)
        notes.extend(fnotes)
        start = set(free) | {k for f in farm_list for k, _ in f.outputs}
        levels = reachable(conn, run, tier, start)
    farm_map = {f.id: f for f in farm_list}
    pool = prune(gens, req.candidates, levels)
    if len(pool) < len(gens):
        notes.append(
            f"{len(gens) - len(pool)} of {len(gens)} generator x fuel candidates pruned"
            f" before solving (per generator, the {req.candidates} with the most EU per"
            " unit of fuel"
            + (", reachable from the free set" if levels is not None else "")
            + ")"
        )
    if farm_list:
        kept = best_farms(farm_list)
        if len(kept) < len(farm_list):
            notes.append(
                f"{len(farm_list) - len(kept)} of {len(farm_list)} farms set aside"
                " (one farm per product, the most per EU)"
            )
    if not pool:
        raise PowerError(
            f"no generator and fuel at {VN[tier]} for mode {req.mode}"
            + (f" ({'; '.join(notes)})" if notes else "")
        )
    rejected: list[tuple[str, str]] = []
    chains_by_fuel: dict[str, tuple[list[Chain], list[str]]] = {}
    options: list[Option] = []
    for gen in pool:
        if gen.fuel not in chains_by_fuel:
            chains_by_fuel[gen.fuel] = fuel_chains(
                conn, gen.fuel, req, base, tier, run, templates, levels
            )
        found, errors = chains_by_fuel[gen.fuel]
        for why in errors:
            rejected.append((gen.id, why))
        for chain in found:
            for ref in chain.skipped:
                notes.append(f"{ref} left out: the export lists no input it uses up")
            net = network(conn, chain, gen, 1.0, req, farm_list)
            label = gen.id + (
                f" [{chain.request.pins[0].recipe}]" if chain.request.pins else ""
            )
            try:
                flows = solve.routes(net, CHAIN_ROUTES)
            except solve.SolveError as exc:
                rejected.append((label, _why(conn, chain, gen, req, free, str(exc))))
                continue
            for flow in flows:
                options.append(Option(gen, chain, 1.0, flow, net))
    built: list[dict[str, Any]] = []
    # The effort model reads the request's tier, boundary and constraints, not its target.
    boundary = tuple(k for k in free if is_stable_key(k))
    measure = (
        effort.Effort(
            conn,
            Request("fluid:water", tier_cap=tier, boundary=boundary),
            base,
            tier,
            craft_s=prefs.craft_s,
            templates=templates,
            run_id=run,
        )
        if with_effort
        else None
    )
    table = metrics.load_scarcity()
    sized: list[tuple[int, Option, dict[str, Any], float | None, str]] = []
    for n, opt in enumerate(options, 1):
        p = build_plan(conn, run_key, req, opt, farm_map, n)
        net_unit = float(p["power"]["net_eut"])
        units, why = _units(req, net_unit, _upkeep(p["boundary"]["in"], free))
        sized.append((n, opt, p, units, why))
    if req.scale == "raw_budget":
        # A line that takes nothing from outside the free set isn't bounded by the budget: it
        # gets as many units as the best line the budget does bound, so max_output never
        # favours a line that imports (D235).
        open_units = [
            u
            for _, _, p, u, _ in sized
            if u is not None and _upkeep(p["boundary"]["in"], free) > 0
        ]
        top_units = max(open_units, default=1.0)
        sized = [
            (
                n,
                opt,
                p,
                top_units,
                (
                    f"takes nothing from outside the free set: {top_units:g} units, as many"
                    " as the best line the budget bounds"
                ),
            )
            if u is not None and _upkeep(p["boundary"]["in"], free) <= 0
            else (n, opt, p, u, why)
            for n, opt, p, u, why in sized
        ]
    for n, opt, p, units, why in sized:
        if units is None:
            rejected.append((opt.gen.id, why))
            continue
        if units != 1.0:
            net = network(conn, opt.chain, opt.gen, units, req, farm_list)
            try:
                opt = Option(
                    opt.gen,
                    opt.chain,
                    units,
                    solve.solve_route(net, dict(opt.flow.choice)),
                    net,
                )
            except solve.SolveError as exc:
                rejected.append((opt.gen.id, f"at {units:g} units: {exc}"))
                continue
            p = build_plan(conn, run_key, req, opt, farm_map, n)
        if why:
            p.setdefault("warnings", []).append(why)
        proof = sustain.validate(conn, p, free, farm_map)
        p["closed"] = proof.as_dict()
        if req.mode == "self_sustaining":
            if not proof.ok:
                rejected.append(
                    (
                        opt.gen.id,
                        "validator: "
                        + "; ".join(
                            [
                                *(f"imports {k} {v:.6g}/s" for k, v in proof.imports),
                                *proof.problems,
                            ]
                        ),
                    )
                )
                continue
            p["bootstrap"] = sustain.bootstrap(conn, p, farm_map)
            p["bootstrap"]["sinks"] = p["closed"]["surplus"]
        elif p["power"]["net_eut"] <= 0:
            rejected.append(
                (opt.gen.id, "net EU/t is not above 0 after the chain's own use")
            )
            continue
        p["metrics"] = _metrics(conn, p, opt.chain.graph, measure, table, free)
        built.append(p)
    # The same line can come from two candidates (one chain, one generator): keep it once.
    unique = list({p["id"]: p for p in built}.values())
    ranked = rank.order_power(unique, req.mode, prefs)
    top = ranked[: req.options]
    ids = [p["id"] for p in top]
    for i, p in enumerate(top, 1):
        p["alternatives"] = [x for x in ids if x != p["id"]]
        p["name"] = _name(p, tier, i)
        p["metrics"] = plan._rounded(p["metrics"])
        p["rank"] = plan._rounded(p["rank"])
        p["gt"] = GT_PIN
    if len(top) < 2:
        notes.append(
            f"only {len(top)} option(s) found (D11 asks for 2-3): see rejected candidates"
        )
    return PowerDesign(
        req,
        tier,
        tuple(top),
        len(unique),
        tuple(rejected),
        rank.describe_power(prefs, req.mode),
        tuple(dict.fromkeys(notes)),
    )


def _why(
    conn: sqlite3.Connection,
    chain: Chain,
    gen: Gen,
    req: PowerRequest,
    free: frozenset[str],
    error: str,
) -> str:
    """Why a candidate has no line: for ``self_sustaining``, the imports its unbanned line
    would need, found by the validator."""
    if req.mode != "self_sustaining":
        return error
    loose = replace(req, mode="best")
    try:
        net = network(conn, chain, gen, 1.0, loose)
        flow = solve.routes(net, 1)[0]
    except solve.SolveError:
        return error
    p = build_plan(
        conn,
        plan._run_key(conn, chain.graph.run_id),
        loose,
        Option(gen, chain, 1.0, flow, net),
        {},
        1,
    )
    proof = sustain.validate(conn, p, free)
    parts = [f"{k} {v:.6g}/s" for k, v in proof.imports] + list(proof.problems)
    return "not closed: " + "; ".join(parts) if parts else error


def _name(p: Mapping[str, Any], tier: int, n: int) -> str:
    pw = p["power"]
    return " · ".join(
        [
            f"{pw['generator']['label']}",
            f"{pw['net_eut']:g} EU/t net",
            VN[tier],
            f"option {n}",
        ]
    )


def dumps(p: Mapping[str, Any]) -> str:
    return plan.dumps(p)
