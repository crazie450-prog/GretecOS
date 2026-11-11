"""The machine model and selection (WO-3.2, plan §7.1 steps 2 and 5, D109-D111, D142, D144).

For one recipe and the player's progress, :func:`select` lists every machine that can run it, as
typed :class:`MachineOption` rows, each sized by GT's own rules (:mod:`.gtmath`, WO-3.1) from the
DB (``recipe``, ``recipe_meta``, ``machine``, ``machine_map``, ``machine_fact``), and says why
each other machine on the recipe's map can't (:class:`Rejected`). Nothing here is estimated: a
number comes from the recipe, a machine fact or GT's math, and each carries its citations
(``r:<map>/<sig12>`` and ``mte:<id>``) and the facts it used.

**Machine kinds** (GT's class decides which rules apply; D176):

- ``singleblock``: ``MTEBasicMachineWithRecipe``, GT's electric single blocks (and the 1 A
  ``MTEBoxinator`` and ``MTEPotionBrewer``, which run a map recipe the same way before trying
  their own). A machine of tier
  t finds recipes up to V[t] x the recipe map's amperage and overclocks at V[t] x that amperage
  as one amp (:func:`gtmath.basic_machine`). It needs a cleanroom around it for a cleanroom
  recipe and a space dimension for a low-gravity one (``MTEBasicMachine.checkRecipe`` reads
  GT's special value -200/-100/-300; the ``cleanroom``/``low_gravity`` metadata is read too).
- ``steam``: the bronze and steel steam single blocks (``MTEBasicMachineBronze``/``Steel``):
  recipes up to 32 EU/t x the map's amperage, no overclocks, bronze EU/t x1 and duration x2,
  steel EU/t x2 and duration x1 (``SteamOverclockDescriber``); 2 L of steam per EU.
- ``multiblock``: sized from its ``machine_fact`` rows (best per key, a ``key[map]`` row first on
  that map): ``processing``, ``parallel``, ``speed`` (duration / it), ``eu_modifier``, ``oc``
  (standard, perfect, heat, heat_no_discount, limited_perfect, none, custom with ``oc_eu_mul``
  and ``oc_time_div``), ``perfect_overclocks``, ``max_overclocks``, ``tier_skips``, ``power``
  (average or total_eu), ``machine_heat``, ``exotic_hatches``; the machine pass's
  ``batch_mode``. Multiblocks ignore cleanroom and low gravity (only ``MTEBasicMachine`` checks
  them). Its energy hatches are the player's choice: by default one or two plain hatches of
  each tier he has reached (one plain hatch runs it at 1 A, D175).

**Variables** (D109): build variables come from the hatches (``vt``, ``vt_avg``, ``vt_eu``,
``vt_eu_floor``, ``input_eu``), the coils (``coil_tier``, ``coil_heat``) or the player's
:attr:`Player.build` (only inputs the hatches and coils don't set); a build input with a
documented absent value (``singularity`` 0) gets it when not given, the worst case (D177 (6));
recipe variables from the recipe; state variables are sized for the worst case by default with
the steady state as the ``alternative`` (D111, D142 (b)); choice variables belong to custom
machines with no recipe map, which no recipe selects. A formula whose variable has no value
rejects the machine and names the variable.

**Badges** (D142 (a), D144): a ``flag`` fact, a fact under 0.8 confidence, a ``partial`` fact,
a ``deprecated`` controller or a ``processing custom`` machine (its own recipe check, also
``low_confidence`` until D176's question is answered) is still offered, with a badge and the
fact's note. Addon machines (not from GT's own jar) say so (D12).
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from gregtec.db import access
from gregtec.ingest import machine_facts as mf
from gregtec.planner.gtmath import overclock as ocm
from gregtec.planner.gtmath.jmath import INT_MAX, to_int
from gregtec.planner.gtmath.parallel import Check
from gregtec.planner.gtmath.processing import (
    MAX_BATCH_SIZE,
    EnergyHatch,
    Logic,
    Power,
    Recipe,
    Run,
    average_input_voltage,
    basic_machine,
    max_input_eu,
    max_input_voltage,
    multiblock_power,
    process,
    total_eu_power,
)
from gregtec.planner.gtmath.tiers import (
    COILS,
    V,
    coil_heat,
    get_tier,
    get_tier_extended,
)

BASIC_MACHINE = "gregtech.api.metatileentity.implementations.MTEBasicMachineWithRecipe"
# MTEBasicMachine subclasses whose checkRecipe() returns MTEBasicMachine.checkRecipe()'s result
# first (a map recipe runs as on any single block) and only then tries its own: 1 A machines.
MAP_FIRST_SINGLE_BLOCKS = frozenset(
    {
        "gregtech.common.tileentities.machines.basic.MTEBoxinator",
        "gregtech.common.tileentities.machines.basic.MTEPotionBrewer",
    }
)
STEAM_PACKAGE = "gregtech.common.tileentities.machines.steam.MTESteam"
# SteamOverclockDescriber: (EU/t multiplier, duration multiplier) per variant.
STEAM_VARIANTS = {"Bronze": (1, 2), "Steel": (2, 1)}
STEAM_RECIPE_VOLTAGE = 32  # TierEU.LV: the voltage a steam machine finds recipes at
STEAM_PER_EU = 2  # SteamOverclockDescriber.convertEUToSteam: 2 L of steam per EU
# GT's special values (GTRecipeBuilder): -100 low gravity, -200 cleanroom, -300 both.
LOW_GRAVITY_VALUES = (-100, -300)
CLEANROOM_VALUES = (-200, -300)
THRESHOLD = mf.THRESHOLD  # D109/D111: facts under this confidence get a badge
HEAT_OVERCLOCK_THRESHOLD = 1800  # OverclockCalculator.HEAT_OVERCLOCK_THRESHOLD
# State variables (D111, D142 (b)): the worst case (the default) and the steady state. Only
# `ramp` has a steady value the facts define (1, full momentum); the others keep the worst.
STATE_WORST: dict[str, float] = {"ramp": 0.0, "overheated": 1.0, "wind_level": 1.0}
STATE_STEADY: dict[str, float] = {"ramp": 1.0, "overheated": 1.0, "wind_level": 1.0}
# The facts a multiblock is sized with (plain or per map).
SIZING_KEYS = (
    "parallel",
    "speed",
    "eu_modifier",
    "oc",
    "perfect_overclocks",
    "oc_eu_mul",
    "oc_time_div",
    "max_overclocks",
    "tier_skips",
    "machine_heat",
)

# Build variables the planner derives from the hatches and coils; Player.build can't set them.
DERIVED = frozenset(
    {"vt", "vt_avg", "vt_eu", "vt_eu_floor", "input_eu", "coil_tier", "coil_heat"}
)
# TecTech's laser target hatches start at 256 A; its multi-amp hatches stop at 64 A.
LASER_MIN_AMPS = 256

Kind = Literal["singleblock", "steam", "multiblock"]
State = Literal["worst", "steady"]


# --- inputs --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecipeInfo:
    """What machine selection reads of one recipe (DB row ``recipe`` + ``recipe_meta``)."""

    ref: str  # r:<map>/<sig12>[-n]
    map: str  # recipe map, unlocalized
    eut: int
    duration: int  # ticks
    map_amperage: int = 1  # recipe_map.amperage
    special_value: int | None = None
    meta: Mapping[str, float | str] = field(default_factory=dict)

    @property
    def heat(self) -> int:
        """The heat a heat machine compares with: GT's EBF family reads ``mSpecialValue``; a
        recipe without one (NEI-only) falls back to its ``coil_heat`` metadata."""
        if self.special_value is not None and self.special_value > 0:
            return self.special_value
        value = self.meta.get("coil_heat")
        return int(value) if isinstance(value, float | int) else 0

    def _flag(self, key: str, values: tuple[int, ...]) -> bool:
        value = self.meta.get(key)
        return self.special_value in values or (
            isinstance(value, float | int) and value != 0
        )

    @property
    def cleanroom(self) -> bool:
        """A single block needs a cleanroom (special value -200/-300, or the metadata)."""
        return self._flag("cleanroom", CLEANROOM_VALUES)

    @property
    def low_gravity(self) -> bool:
        """A single block needs a space dimension (special value -100/-300, or the metadata)."""
        return self._flag("low_gravity", LOW_GRAVITY_VALUES)

    def variables(self) -> dict[str, float]:
        """The recipe variables of D109 this recipe gives."""
        out = {"recipe_tier": float(get_tier(self.eut))}
        startup = self.meta.get("fusion_threshold")
        if isinstance(startup, float | int):
            out["recipe_startup_eu"] = float(startup)
        if self.special_value is not None:
            out["recipe_frc_tier"] = float(self.special_value)
        return out


@dataclass(frozen=True)
class MachineInfo:
    """One machine of the DB: its row, recipe maps and best fact per key."""

    mte_id: int
    name: str
    class_name: str
    mod: str | None
    multiblock: bool
    tier: int | None
    maps: tuple[str, ...]
    facts: Mapping[str, mf.Fact] = field(default_factory=dict)
    addon: bool = False

    @property
    def ref(self) -> str:
        return f"mte:{self.mte_id}"

    def fact(self, key: str, recipe_map: str) -> mf.Fact | None:
        """The fact for ``key`` on ``recipe_map``: ``key[map]`` first, then ``key``."""
        return self.facts.get(f"{key}[{recipe_map}]") or self.facts.get(key)

    @property
    def kind(self) -> Kind | None:
        if self.multiblock:
            return "multiblock"
        if (
            self.class_name == BASIC_MACHINE
            or self.class_name in MAP_FIRST_SINGLE_BLOCKS
        ):
            return "singleblock"
        if self.class_name.startswith(STEAM_PACKAGE) and _steam_variant(
            self.class_name
        ):
            return "steam"
        return None


def _steam_variant(class_name: str) -> tuple[int, int] | None:
    for suffix, mults in STEAM_VARIANTS.items():
        if class_name.endswith(suffix):
            return mults
    return None


@dataclass(frozen=True)
class Player:
    """The player's progress and choices for selection (plan §7.1 request, D63, D64).

    ``tier``: the GT voltage tier he has started (D142 (3)); 0 is stone or steam. ``steam``:
    he has reached the steam stage. ``owned``: mte ids he has (My base). ``coil_max``: the best
    coil tier he can build (0 Cupronickel ... 13 Eternal); None sizes each coil machine with
    the lowest coil that runs the recipe and reports it. ``hatches``: energy-hatch layouts to
    size multiblocks with, instead of one and two plain hatches of each tier 1..``tier``.
    ``build``: other build variables per machine (``casing_tier``, ``mode``, ...), by mte id.
    """

    tier: int
    steam: bool = True
    owned: frozenset[int] = frozenset()
    require_owned: bool = False
    coil_max: int | None = None
    cleanroom: bool = True
    low_gravity: bool = False
    batch_mode: bool = False
    state: State = "worst"
    addons: bool = True
    hatches: tuple[tuple[EnergyHatch, ...], ...] | None = None
    build: Mapping[int, Mapping[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.tier < len(V):
            raise ValueError(
                f"tier {self.tier} is not a GT voltage tier (0..{len(V) - 1})"
            )
        if self.coil_max is not None and not 0 <= self.coil_max < len(COILS):
            raise ValueError(
                f"coil_max {self.coil_max} is not a coil tier (0..{len(COILS) - 1})"
            )
        for layout in self.hatches or ():
            for h in layout:
                if not 0 < h.tier < len(V) or h.amps < 1:
                    raise ValueError(f"{h} is not an energy hatch GT has")
        for mte, inputs in self.build.items():
            for key in inputs:
                var = mf.VARIABLES.get(key)
                if var is None or var.kind != "build" or key in DERIVED:
                    raise ValueError(
                        f"build[{mte}][{key!r}]: only build inputs the hatches and coils "
                        "don't set can be given"
                    )

    def hatch_layouts(self) -> list[tuple[EnergyHatch, ...]]:
        if self.hatches is not None:
            return [tuple(h) for h in self.hatches]
        return [(EnergyHatch(t),) * n for t in range(1, self.tier + 1) for n in (1, 2)]


# --- outputs -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cited:
    """A number and where it comes from: the recipe (``r:``) and the machine (``mte:``), whose
    facts the option lists."""

    value: int | float
    refs: tuple[str, ...]


@dataclass(frozen=True)
class Sizing:
    """One machine's run of the recipe, as GT computes it."""

    state: str  # worst | steady | fixed (no state variable)
    eut: Cited  # EU/t drawn while running (steam machines: EU/t equivalent)
    duration: Cited  # ticks per run
    parallel: Cited  # recipes per run (batch parallels included)
    overclocks: Cited
    crafts_per_second: Cited  # recipes per second while running
    amps: Cited  # amps of the machine's tier the draw needs
    steam_per_tick: Cited | None = None  # L/t (steam machines)


@dataclass(frozen=True)
class UsedFact:
    key: str
    value: str
    source: str
    confidence: float
    note: str


@dataclass(frozen=True)
class MachineOption:
    """One way to run the recipe: a machine, its tier or energy hatches, its coils."""

    recipe: str  # r: ref
    mte_id: int
    name: str
    kind: Kind
    mod: str | None
    addon: bool
    tier: int  # machine tier (single blocks), hatch tier (multiblocks), 0 for steam
    hatches: tuple[EnergyHatch, ...]
    coil_tier: int | None
    min_coil_tier: int | None
    batch_mode: bool
    batch_supported: bool
    requires: tuple[str, ...]  # cleanroom, low_gravity
    owned: bool
    badges: tuple[str, ...]  # flagged, low_confidence, partial, deprecated, custom
    notes: tuple[str, ...]
    facts: tuple[UsedFact, ...]
    sizing: Sizing  # the player's state (worst by default)
    alternative: Sizing | None = (
        None  # the other state, when a state variable changes it
    )

    @property
    def ref(self) -> str:
        return f"mte:{self.mte_id}"


@dataclass(frozen=True)
class Rejected:
    mte_id: int
    name: str
    reason: str


@dataclass(frozen=True)
class Selection:
    recipe: RecipeInfo
    options: tuple[MachineOption, ...]
    rejected: tuple[Rejected, ...]


class Unsized(Exception):
    """A machine can't be sized for the recipe; the message says why."""


# --- single blocks -------------------------------------------------------------------------


def _amps(eut: int, tier: int) -> int:
    return max(1, -(-eut // V[tier])) if eut > 0 else 0


def _sizing(run: Run, state: str, refs: tuple[str, ...], tier: int) -> Sizing:
    return Sizing(
        state=state,
        eut=Cited(run.eut, refs),
        duration=Cited(run.duration, refs),
        parallel=Cited(run.parallel, refs),
        overclocks=Cited(run.overclocks, refs),
        crafts_per_second=Cited(run.crafts_per_second, refs),
        amps=Cited(_amps(run.eut, tier), refs),
    )


def _requirements(recipe: RecipeInfo, player: Player) -> tuple[str, ...]:
    """What a single block needs around it for ``recipe``; Unsized if the player can't."""
    needs = []
    if recipe.cleanroom:
        if not player.cleanroom:
            raise Unsized("a single block needs a cleanroom for this recipe")
        needs.append("cleanroom")
    if recipe.low_gravity:
        if not player.low_gravity:
            raise Unsized("a single block needs low gravity (a space dimension)")
        needs.append("low_gravity")
    return tuple(needs)


def size_singleblock(
    recipe: RecipeInfo, machine: MachineInfo, player: Player
) -> MachineOption:
    """An electric single block (``MTEBasicMachineWithRecipe``) running ``recipe``."""
    tier = machine.tier
    if tier is None or not 0 < tier < len(V):
        raise Unsized("no voltage tier in the machine row")
    if tier > player.tier:
        raise Unsized(f"tier {tier} is above the player's tier {player.tier}")
    needs = _requirements(recipe, player)
    # MTEBasicMachineWithRecipe takes the map's amperage; the map-first classes are 1 A.
    amps = 1 if machine.class_name in MAP_FIRST_SINGLE_BLOCKS else recipe.map_amperage
    run = basic_machine(
        Recipe(eut=recipe.eut, duration=recipe.duration),
        tier,
        amperage=amps,
        map_amperage=amps,
    )
    if not run.ok:
        raise Unsized(f"{run.check}: the recipe needs {recipe.eut} EU/t")
    refs = (recipe.ref, machine.ref)
    return _option(
        recipe,
        machine,
        player,
        "singleblock",
        tier,
        (),
        needs,
        _sizing(run, "fixed", refs, tier),
    )


def size_steam(
    recipe: RecipeInfo, machine: MachineInfo, player: Player
) -> MachineOption:
    """A bronze or steel steam single block running ``recipe``."""
    if not player.steam:
        raise Unsized("the player hasn't reached the steam stage")
    mults = _steam_variant(machine.class_name)
    assert mults is not None
    if recipe.eut > STEAM_RECIPE_VOLTAGE * recipe.map_amperage:
        raise Unsized(
            f"no_recipe: steam machines find recipes up to {STEAM_RECIPE_VOLTAGE} EU/t"
        )
    needs = _requirements(recipe, player)
    eu_mul, time_mul = mults
    oc = ocm.calculate(
        replace(
            ocm.Overclock.no_oc(recipe.eut, recipe.duration),
            eut_modifier=float(eu_mul),
            duration_modifier=float(time_mul),
        )
    )
    run = Run(Check.SUCCESS, eut=oc.eut, duration=max(1, oc.duration), parallel=1)
    refs = (recipe.ref, machine.ref)
    sizing = replace(
        _sizing(run, "fixed", refs, 1),
        amps=Cited(0, refs),
        steam_per_tick=Cited(STEAM_PER_EU * run.eut, refs),
    )
    return _option(recipe, machine, player, "steam", 0, (), needs, sizing)


# --- multiblocks ---------------------------------------------------------------------------


def _number(fact: mf.Fact, env: Mapping[str, float]) -> float:
    if fact.value == "unlimited":
        return math.inf
    missing = sorted(mf.variables(fact.value) - set(env))
    if missing:
        names = ", ".join(f"{n} ({mf.VARIABLES[n].kind})" for n in missing)
        raise Unsized(
            f"{fact.key} = {fact.value} needs {names}: give build inputs in Player.build"
        )
    try:
        value = mf.evaluate(fact.value, env)
    except ValueError as e:  # sqrt of a negative number
        raise Unsized(f"{fact.key}: {e}") from None
    except (ZeroDivisionError, OverflowError):
        raise Unsized(f"{fact.key}: {fact.value} has no value here") from None
    if math.isnan(value):
        raise Unsized(f"{fact.key}: {fact.value} has no value here")
    return value


def _int(value: float) -> int:
    """A formula's value where GT keeps an int (parallels, overclock counts, heat)."""
    return INT_MAX if math.isinf(value) and value > 0 else to_int(value)


@dataclass(frozen=True)
class _Plan:
    """A multiblock's facts on one map, read once."""

    facts: dict[str, mf.Fact]  # key -> the fact used (plain key names)
    oc: str
    power: str
    extended: bool
    exotic: str | None  # the exotic_hatches fact: multiamp, laser or any
    batch: bool
    custom: bool

    def variables(self) -> set[str]:
        out: set[str] = set()
        for key in SIZING_KEYS:
            f = self.facts.get(key)
            if f is not None and f.value not in (mf.NA, "unlimited"):
                out |= mf.variables(f.value)
        return out


def _plan(machine: MachineInfo, recipe_map: str) -> _Plan:
    proc = machine.facts.get("processing")
    if proc is not None and proc.value in ("generator", "none"):
        raise Unsized(
            f"processing {proc.value}: it runs no recipes for a production line"
        )
    facts: dict[str, mf.Fact] = {}
    for key in (*SIZING_KEYS, "power", "exotic_hatches"):
        f = machine.fact(key, recipe_map)
        if f is not None:
            facts[key] = f
    for key in ("parallel", "oc"):
        if key not in facts or facts[key].value == mf.NA:
            raise Unsized(f"no {key} fact on {recipe_map}")
    oc = facts["oc"].value
    power = facts["power"].value if "power" in facts else "average"
    if power not in ("average", "total_eu"):
        raise Unsized(
            f"power {power}: its voltage is in the fact's note, not a fact (D176)"
        )
    if oc == "custom" and not ("oc_eu_mul" in facts and "oc_time_div" in facts):
        raise Unsized("oc custom without oc_eu_mul and oc_time_div (see the note)")
    if oc == "limited_perfect" and "perfect_overclocks" not in facts:
        raise Unsized("oc limited_perfect without perfect_overclocks")
    if oc in ("heat", "heat_no_discount") and "machine_heat" not in facts:
        raise Unsized(f"oc {oc} without a machine_heat fact")
    batch = machine.facts.get("batch_mode")
    return _Plan(
        facts=facts,
        oc=oc,
        power=power,
        extended=power == "total_eu" or "exotic_hatches" in facts,
        exotic=facts["exotic_hatches"].value if "exotic_hatches" in facts else None,
        batch=batch is not None and batch.value == "true",
        custom=proc is not None and proc.value == "custom",
    )


def _hatch_variables(
    hatches: Sequence[EnergyHatch], extended: bool
) -> dict[str, float]:
    total = max_input_eu(hatches, extended=extended)
    floor_tier = 0
    while 8 * 4 ** (floor_tier + 1) <= total:
        floor_tier += 1
    return {
        "vt": float(get_tier(max_input_voltage(hatches, extended=extended))),
        "vt_avg": float(get_tier(average_input_voltage(hatches, extended=extended))),
        "vt_eu": float(get_tier_extended(total)),
        "vt_eu_floor": float(floor_tier) if total >= 8 else 0.0,
        "input_eu": float(total),
    }


def _logic(
    plan: _Plan,
    env: Mapping[str, float],
    hatches: Sequence[EnergyHatch],
    batch: bool,
) -> Logic:
    f = plan.facts
    power: Power = (
        total_eu_power(hatches)
        if plan.power == "total_eu"
        else multiblock_power(hatches, extended=plan.extended)
    )
    if "tier_skips" in f:
        skips = _number(f["tier_skips"], env)
        power = replace(
            power,
            max_tier_skips=ocm.UNLIMITED_TIER_SKIPS
            if math.isinf(skips)
            else _int(skips),
        )
    speed = (
        _number(f["speed"], env) if "speed" in f and f["speed"].value != mf.NA else 1.0
    )
    if speed <= 0:
        raise Unsized(f"speed {f['speed'].value} is {speed:g} here")
    eu = (
        _number(f["eu_modifier"], env)
        if "eu_modifier" in f and f["eu_modifier"].value != mf.NA
        else 1.0
    )
    parallel = _int(_number(f["parallel"], env))
    if parallel < 1:
        raise Unsized(f"parallel {f['parallel'].value} is {parallel} here")
    logic = Logic(
        power=power,
        max_parallel=parallel,
        eu_modifier=eu,
        speed_boost=1.0 / speed,
        batch_size=MAX_BATCH_SIZE if batch else 1,
        int_power=not plan.extended,
    )
    if "max_overclocks" in f:
        logic = replace(
            logic, max_overclocks=max(0, _int(_number(f["max_overclocks"], env)))
        )
    oc = plan.oc
    if oc == "perfect":
        return replace(logic, oc_time_reduction=4.0)
    if oc == "none":
        return replace(logic, no_overclock=True)
    if oc == "custom":
        return replace(
            logic,
            oc_power_increase=_number(f["oc_eu_mul"], env),
            oc_time_reduction=_number(f["oc_time_div"], env),
        )
    if oc in ("heat", "heat_no_discount"):
        return replace(
            logic,
            heat_oc=True,
            heat_discount=oc == "heat",
            machine_heat=_int(_number(f["machine_heat"], env)),
            check_heat=True,
        )
    if oc == "limited_perfect":
        # GT gives these machines heat overclocks against a recipe heat of 0, with a machine
        # heat that buys exactly that many perfect overclocks; the rest are standard.
        perfect = max(0, _int(_number(f["perfect_overclocks"], env)))
        return replace(
            logic,
            heat_oc=True,
            recipe_heat=0,
            machine_heat=min(INT_MAX, perfect * HEAT_OVERCLOCK_THRESHOLD),
        )
    if oc != "standard":
        raise Unsized(f"oc {oc} is not a rule WO-3.2 knows")
    return logic


def _run(
    recipe: RecipeInfo,
    plan: _Plan,
    env: Mapping[str, float],
    hatches: Sequence[EnergyHatch],
    batch: bool,
) -> Run:
    logic = _logic(plan, env, hatches, batch)
    return process(
        Recipe(eut=recipe.eut, duration=recipe.duration, heat=recipe.heat), logic
    )


def size_multiblock(
    recipe: RecipeInfo,
    machine: MachineInfo,
    player: Player,
    hatches: Sequence[EnergyHatch],
) -> MachineOption:
    """A multiblock with ``hatches`` running ``recipe``, sized from its facts."""
    plan = _plan(machine, recipe.map)
    if not hatches:
        raise Unsized("no energy hatch")
    top = max(h.tier for h in hatches)
    if top > player.tier:
        raise Unsized(f"a tier {top} hatch is above the player's tier {player.tier}")
    if any(h.exotic for h in hatches) and not plan.extended:
        raise Unsized("it takes no exotic energy hatches")
    for h in hatches:
        if h.exotic and plan.exotic in ("multiamp", "laser"):
            laser = h.amps >= LASER_MIN_AMPS
            if laser != (plan.exotic == "laser"):
                kind = "laser" if laser else "multi-amp"
                raise Unsized(f"exotic_hatches {plan.exotic}: it takes no {kind} hatch")
    names = plan.variables()
    given = {k: float(v) for k, v in player.build.get(machine.mte_id, {}).items()}
    # A build input with a documented absent value gets it (the worst case, D177 (6)).
    # (names holds the oc fact's word too, so look each name up.)
    defaulted: dict[str, float] = {}
    for n in sorted(names - set(given)):
        var = mf.VARIABLES.get(n)
        if var is not None and var.worst is not None:
            defaulted[n] = var.worst
    # Player.build first: what the hatches and the recipe set always wins.
    base: dict[str, float] = {
        **defaulted,
        **given,
        **recipe.variables(),
        **_hatch_variables(hatches, plan.extended),
    }
    coils = bool(names & {"coil_tier", "coil_heat"})
    batch = player.batch_mode and plan.batch
    states = names & set(STATE_WORST)

    def env_for(coil: int | None, state: Mapping[str, float]) -> dict[str, float]:
        env = dict(base)
        if coil is not None:
            env["coil_tier"] = float(coil)
            env["coil_heat"] = float(coil_heat(coil))
        env.update({k: v for k, v in state.items() if k in states})
        return env

    worst = player.state == "worst"
    first = STATE_WORST if worst else STATE_STEADY
    min_coil: int | None = None
    coil: int | None = None
    if coils:
        last = Unsized("no coil runs it")
        for c in range(len(COILS)):
            run = _run(recipe, plan, env_for(c, first), hatches, batch)
            if run.ok:
                min_coil = c
                break
            last = Unsized(_why(run))
        if min_coil is None:
            raise last
        if player.coil_max is not None and min_coil > player.coil_max:
            raise Unsized(
                f"needs {COILS[min_coil]} coils (tier {min_coil}); the player has up to "
                f"{COILS[player.coil_max]}"
            )
        coil = player.coil_max if player.coil_max is not None else min_coil
    run = _run(recipe, plan, env_for(coil, first), hatches, batch)
    if not run.ok:
        raise Unsized(_why(run))
    refs = (recipe.ref, machine.ref)
    hatch_tier = get_tier(max(h.voltage for h in hatches))
    sizing = _sizing(
        run, ("worst" if worst else "steady") if states else "fixed", refs, hatch_tier
    )
    other: Sizing | None = None
    if states:
        alt_state = STATE_STEADY if worst else STATE_WORST
        alt = _run(recipe, plan, env_for(coil, alt_state), hatches, batch)
        if alt.ok and (alt.eut, alt.duration, alt.parallel) != (
            run.eut,
            run.duration,
            run.parallel,
        ):
            other = _sizing(alt, "steady" if worst else "worst", refs, hatch_tier)
    option = _option(
        recipe,
        machine,
        player,
        "multiblock",
        top,
        tuple(hatches),
        (),
        sizing,
        plan=plan,
        batch=batch,
        coil=coil,
        min_coil=min_coil,
    )
    notes = tuple(
        f"sized without {n}: {n} = {w:g}, the worst case (D177 (6))"
        for n, w in defaulted.items()
    )
    return replace(option, alternative=other, notes=(*option.notes, *notes))


def _why(run: Run) -> str:
    if run.check is Check.INSUFFICIENT_HEAT:
        return f"insufficient_heat: the recipe needs {run.required} K"
    if run.required is not None:
        return f"{run.check}: needs {run.required}"
    return str(run.check)


# --- options -------------------------------------------------------------------------------


def _used(machine: MachineInfo, plan: _Plan | None) -> tuple[UsedFact, ...]:
    keys: list[tuple[str, mf.Fact]] = []
    if plan is not None:
        keys = sorted(plan.facts.items())
    for extra in ("processing", "batch_mode", "flag", "partial", "deprecated"):
        f = machine.facts.get(extra)
        if f is not None:
            keys.append((extra, f))
    return tuple(
        UsedFact(f.key, f.value, f.source, f.confidence, f.note) for _, f in keys
    )


def _badges(
    machine: MachineInfo, plan: _Plan | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    badges: list[str] = []
    notes: list[str] = []
    flag = machine.facts.get("flag")
    if flag is not None:
        badges.append("flagged")
        notes.append(f"flagged: {flag.note}")
    if plan is not None:
        low = [f for f in plan.facts.values() if f.confidence < THRESHOLD]
        if low or plan.custom:
            badges.append("low_confidence")
        if low:
            notes.append(
                "low confidence: "
                + ", ".join(f"{f.key} {f.confidence:.2f} ({f.source})" for f in low)
            )
        if plan.custom:
            # A D142 (a) machine until Clayton answers (D176): no fact says its own check
            # sizes like ProcessingLogic.
            badges.append("custom")
            notes.append(
                "its own recipe check (processing custom): sized from its facts with GT's "
                "processing-logic rules, which no fact confirms for it (low confidence)"
            )
    partial = machine.facts.get("partial")
    if partial is not None:
        badges.append("partial")
        notes.append(f"partial ({partial.value}): {partial.note}")
    if machine.facts.get("deprecated") is not None:
        badges.append("deprecated")
        notes.append("a legacy controller GT will remove")
    return tuple(badges), tuple(notes)


def _option(
    recipe: RecipeInfo,
    machine: MachineInfo,
    player: Player,
    kind: Kind,
    tier: int,
    hatches: tuple[EnergyHatch, ...],
    requires: tuple[str, ...],
    sizing: Sizing,
    *,
    plan: _Plan | None = None,
    batch: bool = False,
    coil: int | None = None,
    min_coil: int | None = None,
) -> MachineOption:
    badges, notes = _badges(machine, plan)
    if coil is not None and player.coil_max is None:
        notes = (*notes, f"sized with the lowest coil that runs it: {COILS[coil]}")
    return MachineOption(
        recipe=recipe.ref,
        mte_id=machine.mte_id,
        name=machine.name,
        kind=kind,
        mod=machine.mod,
        addon=machine.addon,
        tier=tier,
        hatches=hatches,
        coil_tier=coil,
        min_coil_tier=min_coil,
        batch_mode=batch,
        batch_supported=plan.batch if plan is not None else False,
        requires=requires,
        owned=machine.mte_id in player.owned,
        badges=badges,
        notes=notes,
        facts=_used(machine, plan),
        sizing=sizing,
    )


def options_for(
    recipe: RecipeInfo, machine: MachineInfo, player: Player
) -> tuple[list[MachineOption], Rejected | None]:
    """Every way ``machine`` can run ``recipe`` for ``player``, or why it can't."""

    def rejected(reason: str) -> tuple[list[MachineOption], Rejected]:
        return [], Rejected(machine.mte_id, machine.name, reason)

    if recipe.map not in machine.maps:
        return rejected(f"it doesn't run {recipe.map}")
    if machine.addon and not player.addons:
        return rejected("addon machines are off (D12)")
    if player.require_owned and machine.mte_id not in player.owned:
        return rejected("not owned (require_owned)")
    kind = machine.kind
    try:
        if kind == "singleblock":
            return [size_singleblock(recipe, machine, player)], None
        if kind == "steam":
            return [size_steam(recipe, machine, player)], None
        if kind is None:
            return rejected(
                f"{machine.class_name.rsplit('.', 1)[-1]}: a single block with its own "
                "recipe check, not modelled (D176)"
            )
    except Unsized as e:
        return rejected(str(e))
    out: list[MachineOption] = []
    reasons: list[tuple[int, int, str]] = []
    for i, hatches in enumerate(player.hatch_layouts()):
        try:
            out.append(size_multiblock(recipe, machine, player, hatches))
        except Unsized as e:
            reasons.append((max_input_eu(hatches, extended=True), i, str(e)))
    if out:
        return out, None
    # The strongest layout's reason (the most input EU/t, the later one on a tie) is what
    # stands between the player and the recipe.
    return rejected(
        max(reasons)[2] if reasons else "no energy hatch layout at the player's tier"
    )


def _order(o: MachineOption) -> tuple[object, ...]:
    kinds = {"steam": 0, "singleblock": 1, "multiblock": 2}
    return (
        o.addon,
        kinds[o.kind],
        o.tier,
        len(o.hatches),
        tuple((h.tier, h.amps, h.exotic) for h in o.hatches),
        o.mte_id,
    )


def choose(
    recipe: RecipeInfo, machines: Iterable[MachineInfo], player: Player
) -> Selection:
    """Every option of ``machines`` for ``recipe``: base before addon (D12), then steam, single
    blocks, multiblocks, by tier and hatch layout, then mte id (a fixed order; ranking is
    planner/rank.py's)."""
    options: list[MachineOption] = []
    rejected: list[Rejected] = []
    for machine in sorted(machines, key=lambda m: m.mte_id):
        found, why = options_for(recipe, machine, player)
        options.extend(found)
        if why is not None:
            rejected.append(why)
    return Selection(recipe, tuple(sorted(options, key=_order)), tuple(rejected))


# --- the DB ---------------------------------------------------------------------------------


def current_run(conn: sqlite3.Connection) -> int:
    rows = access.query(conn, "current_run")
    if not rows:
        raise LookupError("no export run in the DB")
    return int(rows[0]["id"])


def load_recipe(
    conn: sqlite3.Connection, ref: str, run_id: int | None = None
) -> RecipeInfo:
    """Recipe ``ref`` of export run ``run_id`` (default: the newest run)."""
    run = current_run(conn) if run_id is None else run_id
    rows = access.query(conn, "recipe_by_ref", {"run_id": run, "ref": ref})
    if not rows:
        raise LookupError(f"no recipe {ref} in run {run}")
    r = rows[0]
    if r["map"] is None or r["eut"] is None or r["duration_ticks"] is None:
        raise LookupError(
            f"{ref} is not a GT machine recipe (no map, EU/t or duration)"
        )
    meta: dict[str, float | str] = {}
    for m in access.query(conn, "recipe_conditions", {"ids": f"[{int(r['id'])}]"}):
        value = m["value_num"] if m["value_num"] is not None else m["value_text"]
        if value is not None:
            meta[str(m["key"])] = value
    amp = conn.execute(
        "SELECT amperage FROM recipe_map WHERE id = ?", (r["recipe_map_id"],)
    ).fetchone()
    return RecipeInfo(
        ref=str(r["ref"]),
        map=str(r["map"]),
        eut=int(r["eut"]),
        duration=int(r["duration_ticks"]),
        map_amperage=int(amp[0]) if amp and amp[0] else 1,
        special_value=r["special_value"],
        meta=meta,
    )


def base_mods(conn: sqlite3.Connection, run_id: int) -> set[str]:
    """Mod ids from GT's own jar in ``run_id`` (D109's base pack), by the jar's sha256, else
    its file name; ``{"gregtech"}`` when the run lists no mods."""
    gt = conn.execute(
        "SELECT jar, jar_sha256 FROM mod WHERE run_id = ? AND modid = 'gregtech'",
        (run_id,),
    ).fetchone()
    if gt is None:
        return {"gregtech"}
    col, value = ("jar_sha256", gt[1]) if gt[1] else ("jar", gt[0])
    return {
        str(row[0])
        for row in conn.execute(
            f"SELECT modid FROM mod WHERE run_id = ? AND {col} = ?", (run_id, value)
        )
    }


def load_machines(
    conn: sqlite3.Connection, recipe_map: str, run_id: int | None = None
) -> list[MachineInfo]:
    """The machines that run ``recipe_map``, with their best facts (named query
    ``machine_facts``)."""
    run = current_run(conn) if run_id is None else run_id
    base = base_mods(conn, run)
    rows = conn.execute(
        "SELECT m.id, m.mte_id, coalesce(m.name_en, m.class_name), m.class_name, m.mod_id,"
        " m.is_multiblock, m.tier FROM machine AS m"
        " JOIN machine_map AS mm ON mm.machine_id = m.id"
        " JOIN recipe_map AS rm ON rm.id = mm.recipe_map_id"
        " WHERE rm.unloc_name = ? ORDER BY m.mte_id",
        (recipe_map,),
    ).fetchall()
    out = []
    for mid, mte, name, cls, mod, multi, tier in rows:
        maps = tuple(
            str(r[0])
            for r in conn.execute(
                "SELECT rm.unloc_name FROM machine_map AS mm"
                " JOIN recipe_map AS rm ON rm.id = mm.recipe_map_id"
                " WHERE mm.machine_id = ? ORDER BY mm.priority",
                (mid,),
            )
        )
        facts = {
            str(f["key"]): mf.Fact(
                str(f["key"]),
                str(f["value"]),
                str(f["source"]),
                float(f["confidence"] if f["confidence"] is not None else 0.0),
                str(f["note"] or ""),
            )
            for f in access.query(conn, "machine_facts", {"mte_id": mte})
        }
        out.append(
            MachineInfo(
                mte_id=int(mte),
                name=str(name),
                class_name=str(cls),
                mod=mod,
                multiblock=bool(multi),
                tier=tier,
                maps=maps,
                facts=facts,
                addon=mod is not None and mod not in base,
            )
        )
    return out


def owned_machines(conn: sqlite3.Connection) -> frozenset[int]:
    """The mte ids My base lists with a count above 0 (``base_machine``, D63)."""
    return frozenset(
        int(r[0])
        for r in conn.execute(
            "SELECT m.mte_id FROM base_machine AS b JOIN machine AS m ON m.id = b.machine_id"
            " WHERE b.count > 0"
        )
    )


def select(
    conn: sqlite3.Connection, ref: str, player: Player, run_id: int | None = None
) -> Selection:
    """The machine options for recipe ``ref`` (plan §7.1 steps 2 and 5) from the DB."""
    recipe = load_recipe(conn, ref, run_id)
    return choose(recipe, load_machines(conn, recipe.map, run_id), player)
