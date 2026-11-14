"""The generator catalog (WO-4.1, D230-D232; plan §7.2 "Catalog"): GT's generators as machine-model
entries, and the sizing the power designer can do with them today.

Every generator of the export's machine pass is listed by its GT class (``machine.class_name``;
the machine is keyed ``mte:<id>``, the meta of ``gregtech:gt.blockmachines``, never a numeric
item ID) as one :class:`Entry`: its kind, whether it is a 2.9 controller or the ``*Legacy`` copy
GT 2.9 keeps registered for existing builds [E§4.1] (tracked separately: both are entries, and a
legacy controller is never offered for a new build, D231), the fuel maps it burns and how it is
sized:

- ``turbine``: the five large turbines, new and legacy, sized by ``gtmath.turbine`` (GT's
  ``TurbineStatCalculator`` and ``fluidIntoPower``, oracle-tested) from the DB's rotor stats and
  fuel values (:func:`turbine`);
- ``single_block``: an ``MTEBasicGenerator`` whose fuel map is one of :data:`fuels.MAPS` read as
  EU (rule ``"eu"``), sized by its exported efficiency and the fuel map's value
  (:func:`gregtec.planner.fuels.single_block_eu`, checked against GT's own ``getFuelValue`` for
  every fuel of the dev runtime). Other basic generators (no recipe map, such as the steam
  turbine and the magical energy absorber; a map the catalog does not decode, such as
  bartworks' acid generator; or GT++'s RTG, whose map reads as EU per pellet) are catalogued
  ``not_modelled``;
- ``naquadah_reactor``: GoodGenerator's Large Naquadah Reactor (``MTELargeNaquadahReactor``,
  WO-4.8, D326), sized by ``gtmath.naquadah`` (oracle-tested) from its fuel map's recipes, its own
  boost lists and liquid air (the export's ``reactor_boost`` rows) and the smallest dynamo hatch
  its ``exotic_hatches`` fact lets it use whose voltage x amps holds its output (:func:`dynamo`);
  its legacy copy (``MTEMultiNqGeneratorLegacy``) stays ``not_modelled``;
- ``not_modelled``: catalogued with kind, legacy flag and fuel maps, not sized yet: large
  combustion engines, large boilers, XL turbines and the rest (WO-4.2 sizes the ones its
  objective modes need; D232).

Numbers come only from the DB and gtmath, each with its citation (``r:`` for a fuel, ``mte:`` for
the generator, ``rotor:<material>/<size>`` for the rotor's export row).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from gregtec.planner import fuels
from gregtec.planner.gtmath import naquadah as gt_naquadah
from gregtec.planner.gtmath import turbine as gt_turbine
from gregtec.planner.gtmath.tiers import V
from gregtec.planner.gtmath.turbine import Kind


@dataclass(frozen=True)
class ClassInfo:
    """What GT's class is (read at the pin)."""

    kind: str
    legacy: bool
    sizing: str  # turbine | single_block | naquadah_reactor | not_modelled
    turbine: Kind | None = None
    note: str = ""


def _turbine(kind: Kind, legacy: bool) -> ClassInfo:
    return ClassInfo(
        f"large_turbine_{kind.value}",
        legacy,
        "turbine",
        kind,
        "rotor in the controller",
    )


# By GT class simple name. Everything else that the machine pass marks as a generator (an
# MTEBasicGenerator in gt_power.json) is a single block; other classes are not generators.
CLASSES: dict[str, ClassInfo] = {
    "MTELargeTurbineSteam": _turbine(Kind.STEAM, False),
    "MTELargeTurbineHPSteam": _turbine(Kind.HP_STEAM, False),
    "MTELargeTurbineSCSteam": _turbine(Kind.SC_STEAM, False),
    "MTELargeTurbineGas": _turbine(Kind.GAS, False),
    "MTELargeTurbinePlasma": _turbine(Kind.PLASMA, False),
    "MTELargeTurbineSteamLegacy": _turbine(Kind.STEAM, True),
    "MTELargeTurbineHPSteamLegacy": _turbine(Kind.HP_STEAM, True),
    "MTESupercriticalFluidTurbineLegacy": _turbine(Kind.SC_STEAM, True),
    "MTELargeTurbineGasLegacy": _turbine(Kind.GAS, True),
    "MTELargeTurbinePlasmaLegacy": _turbine(Kind.PLASMA, True),
    "MTEXLTurbineSteam": ClassInfo("xl_turbine_steam", False, "not_modelled"),
    "MTEXLTurbineHPSteam": ClassInfo("xl_turbine_hp_steam", False, "not_modelled"),
    "MTEXLTurbineSCSteam": ClassInfo("xl_turbine_sc_steam", False, "not_modelled"),
    "MTEXLTurbineGas": ClassInfo("xl_turbine_gas", False, "not_modelled"),
    "MTEXLTurbinePlasma": ClassInfo("xl_turbine_plasma", False, "not_modelled"),
    "MTELargerTurbineSteamLegacy": ClassInfo("xl_turbine_steam", True, "not_modelled"),
    "MTELargerTurbineHPSteamLegacy": ClassInfo(
        "xl_turbine_hp_steam", True, "not_modelled"
    ),
    "MTELargerTurbineSCSteamLegacy": ClassInfo(
        "xl_turbine_sc_steam", True, "not_modelled"
    ),
    "MTELargerTurbineGasLegacy": ClassInfo("xl_turbine_gas", True, "not_modelled"),
    "MTELargerTurbinePlasmaLegacy": ClassInfo(
        "xl_turbine_plasma", True, "not_modelled"
    ),
    "MTELargeCombustionEngine": ClassInfo("lce", False, "not_modelled"),
    "MTEDieselEngineLegacy": ClassInfo("lce", True, "not_modelled"),
    "MTEExtremeCombustionEngine": ClassInfo("ece", False, "not_modelled"),
    "MTEExtremeDieselEngineLegacy": ClassInfo("ece", True, "not_modelled"),
    "MTELargeBoilerBronze": ClassInfo("large_boiler_bronze", False, "not_modelled"),
    "MTELargeBoilerSteel": ClassInfo("large_boiler_steel", False, "not_modelled"),
    "MTELargeBoilerTitanium": ClassInfo("large_boiler_titanium", False, "not_modelled"),
    "MTELargeBoilerTungstenSteel": ClassInfo(
        "large_boiler_tungstensteel", False, "not_modelled"
    ),
    "MTELargeBoilerBronzeLegacy": ClassInfo(
        "large_boiler_bronze", True, "not_modelled"
    ),
    "MTELargeBoilerSteelLegacy": ClassInfo("large_boiler_steel", True, "not_modelled"),
    "MTELargeBoilerTitaniumLegacy": ClassInfo(
        "large_boiler_titanium", True, "not_modelled"
    ),
    "MTELargeBoilerTungstenSteelLegacy": ClassInfo(
        "large_boiler_tungstensteel", True, "not_modelled"
    ),
    "MTEBoilerBronze": ClassInfo("small_boiler", False, "not_modelled"),
    "MTEBoilerSteel": ClassInfo("small_boiler", False, "not_modelled"),
    "MTEBoilerLava": ClassInfo("small_boiler", False, "not_modelled"),
    "MTEBoilerSolar": ClassInfo("small_boiler", False, "not_modelled"),
    "MTEBoilerSolarSteel": ClassInfo("small_boiler", False, "not_modelled"),
    "MTEAdvancedBoilerLV": ClassInfo("small_boiler", False, "not_modelled"),
    "MTEAdvancedBoilerMV": ClassInfo("small_boiler", False, "not_modelled"),
    "MTEAdvancedBoilerHV": ClassInfo("small_boiler", False, "not_modelled"),
    "MTEThermalBoiler": ClassInfo("heat_exchanger", False, "not_modelled"),
    "MTEThermalBoilerLegacy": ClassInfo("heat_exchanger", True, "not_modelled"),
    "MTELargeNaquadahReactor": ClassInfo(
        "naquadah_reactor_multi", False, "naquadah_reactor"
    ),
    "MTEMultiNqGeneratorLegacy": ClassInfo(
        "naquadah_reactor_multi", True, "not_modelled"
    ),
    "MTENuclearReactor": ClassInfo("lftr", False, "not_modelled"),
    "MTEThoriumHighTempReactor": ClassInfo("thtr", False, "not_modelled"),
    "MTEHighTempGasCooledReactor": ClassInfo("htgr", False, "not_modelled"),
    "MTELargeRocketEngine": ClassInfo("rocket_engine", False, "not_modelled"),
    "MTELargeSemifluidGenerator": ClassInfo("large_semifluid", False, "not_modelled"),
    "MTEUniversalChemicalFuelEngine": ClassInfo(
        "chemical_fuel_engine", False, "not_modelled"
    ),
    "MTEUniversalChemicalFuelEngineLegacy": ClassInfo(
        "chemical_fuel_engine", True, "not_modelled"
    ),
    "MTESOFuelCellMK1": ClassInfo("fuel_cell", False, "not_modelled"),
    "MTESOFuelCellMK2": ClassInfo("fuel_cell", False, "not_modelled"),
    "MTESolarTower": ClassInfo("solar_tower", False, "not_modelled"),
}
SINGLE_BLOCK = ClassInfo("single_block", False, "single_block")
SINGLE_BLOCK_NOT_MODELLED = ClassInfo("single_block", False, "not_modelled")


def _single_block(recipe_map: str | None) -> ClassInfo:
    """A basic generator is sized only when its map's FUEL_VALUE reads as EU (D232)."""
    fuel_map = fuels.BY_MAP.get(recipe_map or "")
    if fuel_map is not None and fuel_map.rule == "eu":
        return SINGLE_BLOCK
    return SINGLE_BLOCK_NOT_MODELLED


@dataclass(frozen=True)
class Entry:
    """One generator of the export, as the catalog sees it."""

    mte_id: int
    name: str | None
    class_name: str
    kind: str
    legacy: bool
    sizing: str
    tier: int | None
    fuel_maps: tuple[str, ...]
    efficiency: int | None  # single blocks: GT's fuel efficiency (%)
    eu_out: int | None  # single blocks: voltage out
    amps_out: int | None
    turbine: Kind | None = None

    @property
    def ref(self) -> str:
        return f"mte:{self.mte_id}"


def _simple(class_name: str) -> str:
    return class_name.rsplit(".", 1)[-1]


def catalog(conn: sqlite3.Connection, run_id: int | None = None) -> list[Entry]:
    """Every generator of the newest export run (or ``run_id``), by mte id."""
    if run_id is None:
        row = conn.execute("SELECT MAX(id) FROM export_run").fetchone()
        run_id = int(row[0]) if row and row[0] is not None else -1
    gens = {
        int(r[0]): r[1:]
        for r in conn.execute(
            "SELECT mte_id, efficiency, eu_out, amps_out, recipe_map FROM generator"
            " WHERE run_id = ?",
            (run_id,),
        )
    }
    maps: dict[int, list[str]] = {}
    for mte_id, name in conn.execute(
        "SELECT m.mte_id, rm.unloc_name FROM machine_map AS mm JOIN machine AS m"
        " ON m.id = mm.machine_id JOIN recipe_map AS rm ON rm.id = mm.recipe_map_id"
        " ORDER BY m.mte_id, mm.priority"
    ):
        maps.setdefault(int(mte_id), []).append(str(name))
    out = []
    for mte_id, name, class_name, tier in conn.execute(
        "SELECT mte_id, name_en, class_name, tier FROM machine ORDER BY mte_id"
    ):
        info = CLASSES.get(_simple(class_name))
        gen = gens.get(int(mte_id))
        if info is None and gen is not None:
            info = _single_block(gen[3])
        if info is None:
            continue
        fuel_maps = tuple(maps.get(int(mte_id), ()))
        if gen is not None and gen[3] and gen[3] not in fuel_maps:
            fuel_maps = (gen[3], *fuel_maps)
        out.append(
            Entry(
                mte_id=int(mte_id),
                name=name,
                class_name=class_name,
                kind=info.kind,
                legacy=info.legacy,
                sizing=info.sizing,
                tier=tier,
                fuel_maps=fuel_maps,
                efficiency=gen[0] if gen else None,
                eu_out=gen[1] if gen else None,
                amps_out=gen[2] if gen else None,
                turbine=info.turbine,
            )
        )
    return out


# --- turbines ------------------------------------------------------------------------------------

SIZES = {"small": 170, "normal": 172, "large": 174, "huge": 176}


class CatalogError(LookupError):
    """The DB lacks what was asked for (a rotor material, a fuel, power data)."""


def rotor(
    conn: sqlite3.Connection, material: str, size: str, run_id: int
) -> gt_turbine.RotorStats:
    """A rotor from the run's ``rotor_material`` and ``rotor_size`` rows."""
    if size not in SIZES:
        raise CatalogError(f"rotor size {size!r}: one of {', '.join(SIZES)}")
    s = conn.execute(
        "SELECT name, speed_multiplier, base_damage, durability_multiplier FROM rotor_size"
        " WHERE run_id = ? AND size = ?",
        (run_id, SIZES[size]),
    ).fetchone()
    m = conn.execute(
        "SELECT name, tool_speed, tool_quality, durability, steam_multiplier, gas_multiplier,"
        " plasma_multiplier FROM rotor_material WHERE run_id = ? AND lower(name) = lower(?)",
        (run_id, material),
    ).fetchone()
    if s is None:
        raise CatalogError(
            "no rotor sizes in the DB: the export run has no gt_power.json"
        )
    if m is None:
        raise CatalogError(f"no turbine rotor material {material!r} in the export run")
    return gt_turbine.RotorStats(
        gt_turbine.RotorSize(str(s[0]), float(s[1]), float(s[2]), float(s[3])),
        gt_turbine.RotorMaterial(
            str(m[0]),
            float(m[1]),
            int(m[2]),
            int(m[3]),
            float(m[4]),
            float(m[5]),
            float(m[6]),
        ),
    )


TURBINE_FUEL_MAP = {Kind.GAS: "gasTurbineFuels", Kind.PLASMA: "plasmaFuels"}


@dataclass(frozen=True)
class FuelPick:
    fluid: str
    fuel_value: int
    ref: str  # r:<map>/<sig12>


def turbine_fuel(
    conn: sqlite3.Connection, kind: Kind, fluid: str, run_id: int
) -> FuelPick:
    """A gas or plasma turbine's fuel by fluid name, from the ``fuel`` rows of its map."""
    gt_name = TURBINE_FUEL_MAP[kind]
    rows = conn.execute(
        "SELECT fluid, fuel_value, ref FROM fuel WHERE run_id = ? AND gt_name = ? AND NOT hidden"
        " AND lower(fluid) = lower(?) ORDER BY fluid_input DESC, ref",
        (run_id, gt_name, fluid),
    ).fetchall()
    if not rows:
        raise CatalogError(f"{fluid!r} is not a fuel of {gt_name} in the export run")
    values = {int(r[1]) for r in rows}
    if len(values) > 1:
        # GT keeps one fuel per fluid (FuelBackend hides the others); the DB keeps only GT's.
        raise CatalogError(f"{fluid!r} has {len(values)} fuel values in {gt_name}")
    return FuelPick(str(rows[0][0]), int(rows[0][1]), str(rows[0][2]))


@dataclass(frozen=True)
class TurbineRun:
    """A turbine's steady state at one flow: GT's fluidIntoPower once the controller settled.
    ``flow`` and ``optimal_flow`` are litres per cycle of ``ticks`` ticks (L/t, or L/s for the
    plasma turbine's 20-tick cycle: ``unit``); ``eut`` holds for every tick of the cycle."""

    label: str
    flow: int  # litres burned per cycle
    eut: int  # EU/t out
    optimal_flow: float  # what the scanner calls the optimal flow (per cycle)
    ticks: int = 1  # the cycle: gt_turbine.cycle_ticks(kind)

    @property
    def unit(self) -> str:
        return "L/t" if self.ticks == 1 else "L/s"  # GT's scanner: 't' or 's'


@dataclass(frozen=True)
class TurbineReport:
    kind: Kind
    legacy: bool
    material: str
    size: str
    loose: bool
    dynamo_tier: int
    dynamo_amps: int
    cap: int
    fuel: FuelPick | None
    rotor: gt_turbine.RotorStats
    runs: tuple[TurbineRun, ...]
    refs: tuple[str, ...]


def turbine(
    conn: sqlite3.Connection,
    *,
    kind: Kind,
    material: str,
    size: str,
    dynamo_tier: int,
    dynamo_amps: int = 1,
    fuel: str | None = None,
    loose: bool = False,
    legacy: bool = False,
    run_id: int | None = None,
) -> TurbineReport:
    """A large turbine with one rotor and one dynamo hatch: its output at the optimal flow, the
    maximum flow it takes, and half the optimal flow. Gas and plasma turbines need ``fuel``
    (a fluid of their fuel map)."""
    if run_id is None:
        row = conn.execute("SELECT MAX(run_id) FROM rotor_material").fetchone()
        if row is None or row[0] is None:
            raise CatalogError(
                "no turbine rotors in the DB: run `gos db build` on a newer export"
            )
        run_id = int(row[0])
    stats = rotor(conn, material, size, run_id)
    pick = None
    fuel_value = 0
    if kind in gt_turbine.FUELLED:
        if fuel is None:
            raise CatalogError(f"a {kind.value} turbine needs a fuel")
        pick = turbine_fuel(conn, kind, fuel, run_id)
        fuel_value = pick.fuel_value
    cap = gt_turbine.dynamo_cap(dynamo_tier, dynamo_amps)

    def at(available: int) -> gt_turbine.Power:
        return gt_turbine.fluid_into_power(
            kind,
            stats,
            available=available,
            cap=cap,
            fuel_value=fuel_value,
            loose=loose,
            legacy=legacy,
        )

    full = at(2**31 - 1)
    optimal = max(1, int(full.optimal_flow))
    runs = []
    for label, flow in (
        ("half optimal", max(1, optimal // 2)),
        ("optimal", optimal),
        ("maximum", max(1, full.max_flow)),
    ):
        p = at(flow)
        runs.append(
            TurbineRun(
                label, p.flow, p.eut, p.optimal_flow, gt_turbine.cycle_ticks(kind)
            )
        )
    refs = [f"rotor:{stats.material.name}/{size}"]
    if pick is not None:
        refs.append(pick.ref)
    return TurbineReport(
        kind=kind,
        legacy=legacy,
        material=stats.material.name,
        size=size,
        loose=loose,
        dynamo_tier=dynamo_tier,
        dynamo_amps=dynamo_amps,
        cap=cap,
        fuel=pick,
        rotor=stats,
        runs=tuple(runs),
        refs=tuple(refs),
    )


def voltage(tier: int) -> int:
    return V[tier]


# --- the Large Naquadah Reactor (WO-4.8, D326) ----------------------------------------------------

NAQUADAH_MAP = "gg.recipe.naquadah_reactor"
# The dynamo hatches the planner offers (GT++'s buffered dynamo is left out: its buffer isn't
# modelled), by class simple name, with the exotic kind each needs the controller to take.
DYNAMO_KINDS = {
    "MTEHatchDynamo": None,
    "MTEHatchDynamoMulti": "multiamp",
    "MTEHatchDynamoTunnel": "laser",
}
# What each exotic_hatches fact value lets a multiblock take (machine_facts, WO-1.7).
EXOTIC = {
    None: frozenset({None}),
    "multiamp": frozenset({None, "multiamp"}),
    "laser": frozenset({None, "laser"}),
    "any": frozenset({None, "multiamp", "laser"}),
}


def reactor(
    conn: sqlite3.Connection, mte_id: int, run_id: int
) -> gt_naquadah.Reactor | None:
    """The reactor's liquid air and boost lists from the run's ``reactor_boost`` rows (the
    export read them from the reactor's own class), or None when the run has none."""
    rows = conn.execute(
        "SELECT kind, fluid, amount, value FROM reactor_boost WHERE run_id = ? AND mte_id = ?"
        " ORDER BY kind, rank",
        (run_id, mte_id),
    ).fetchall()
    air = [r for r in rows if r[0] == "air"]
    if not air:
        return None

    def boosts(kind: str) -> tuple[gt_naquadah.Boost, ...]:
        return tuple(
            gt_naquadah.Boost(str(r[1]), int(r[2]), int(r[3]))
            for r in rows
            if r[0] == kind
        )

    return gt_naquadah.Reactor(
        air=int(air[0][2]),
        coolants=boosts("coolant"),
        excited=boosts("excited"),
        air_fluid=str(air[0][1]),
    )


@dataclass(frozen=True)
class ReactorFuel:
    """One recipe of the reactor's fuel map: 1 L of ``fluid`` for ``duration`` ticks at
    ``basic`` EU/t, giving back 1 L of ``depleted``."""

    ref: str
    fluid: str
    depleted: str
    basic: int
    duration: int


def reactor_fuels(conn: sqlite3.Connection, run_id: int) -> list[ReactorFuel]:
    """The run's ``gg.recipe.naquadah_reactor`` recipes (GT's own rows), by ref."""
    out = []
    for ref, special, duration, fluid, depleted in conn.execute(
        "SELECT r.ref, r.special_value, r.duration_ticks, fi.name, fo.name FROM recipe AS r"
        " JOIN recipe_map AS m ON m.id = r.recipe_map_id"
        " JOIN recipe_input AS i ON i.recipe_id = r.id JOIN fluid AS fi ON fi.id = i.fluid_id"
        " LEFT JOIN recipe_output AS o ON o.recipe_id = r.id"
        " LEFT JOIN fluid AS fo ON fo.id = o.fluid_id"
        " WHERE r.run_id = ? AND m.unloc_name = ? AND r.source IN ('gt', 'gt+nei')"
        " AND NOT r.fake ORDER BY r.ref",
        (run_id, NAQUADAH_MAP),
    ):
        if special is None or duration is None or depleted is None:
            continue
        out.append(
            ReactorFuel(
                str(ref), str(fluid), str(depleted), int(special), int(duration)
            )
        )
    return out


@dataclass(frozen=True)
class Dynamo:
    """A dynamo hatch from the run's ``dynamo_hatch`` rows."""

    mte_id: int
    name: str | None
    class_name: str
    tier: int
    eu_out: int
    amps_out: int

    @property
    def cap(self) -> int:
        """What GT's generators check an output against: voltage x amps."""
        return self.eu_out * self.amps_out

    @property
    def ref(self) -> str:
        return f"mte:{self.mte_id}"


def exotic(conn: sqlite3.Connection, mte_id: int) -> str | None:
    """The multiblock's ``exotic_hatches`` fact (its most confident row), or None."""
    row = conn.execute(
        "SELECT f.value FROM machine_fact AS f JOIN machine AS m ON m.id = f.machine_id"
        " WHERE m.mte_id = ? AND f.key = 'exotic_hatches' ORDER BY f.confidence DESC LIMIT 1",
        (mte_id,),
    ).fetchone()
    return str(row[0]) if row else None


def dynamo(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    eut: int,
    tier_cap: int,
    takes: str | None,
) -> Dynamo | None:
    """The dynamo hatch a multiblock generator of ``eut`` EU/t needs, at or below
    ``tier_cap``: of the kinds its ``exotic_hatches`` fact (``takes``) allows, the lowest tier
    whose voltage x amps holds the output, then GT's 1 A hatch before a multi-amp one before a
    laser, then the fewest amps (D326). None when none does."""
    allowed = EXOTIC.get(takes, EXOTIC[None])
    best: tuple[tuple[int, int, int, int], Dynamo] | None = None
    for mte_id, class_name, tier, eu_out, amps, name in conn.execute(
        "SELECT d.mte_id, d.class_name, d.tier, d.eu_out, d.amps_out, m.name_en"
        " FROM dynamo_hatch AS d LEFT JOIN machine AS m ON m.mte_id = d.mte_id"
        " WHERE d.run_id = ? AND d.tier <= ?",
        (run_id, tier_cap),
    ):
        kind = DYNAMO_KINDS.get(_simple(str(class_name)), "unknown")
        if kind == "unknown" or kind not in allowed:
            continue
        d = Dynamo(
            int(mte_id), name, str(class_name), int(tier), int(eu_out), int(amps)
        )
        if d.cap < eut:
            continue
        rank = (d.tier, list(DYNAMO_KINDS.values()).index(kind), d.amps_out, d.mte_id)
        if best is None or rank < best[0]:
            best = (rank, d)
    return best[1] if best else None
