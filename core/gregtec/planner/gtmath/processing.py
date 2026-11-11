"""GT's processing logic and its power rules (WO-3.1, D173, D175).

How a machine's energy input sets the voltage and amperage its recipes see, and how GT's
``ProcessingLogic`` turns one recipe into parallels, EU/t and ticks through the parallel helper
(:mod:`.parallel`) and the overclock calculator (:mod:`.overclock`) [E§2.3]:

- **Multiblock power** (``MTEMultiBlockBase.setProcessingLogicPower``, machine fact
  ``power=average``): voltage = the hatches' average voltage (rounded down); amperage = 1 with
  exactly one plain energy hatch and no exotic one, otherwise the hatches' summed working amps
  (2 per plain hatch, 4/16/64 per multi-amp hatch, the laser amps of a laser hatch); amperage
  overclocks on. A plain multiblock counts only plain hatches; an extended-power one
  (``MTEExtendedPowerMultiBlockBase``) counts its exotic ones too.
- **Total-EU power** (mega machines, ``power=total_eu``): all the input EU/t as one amp of that
  voltage, with unlimited tier skips.
- **Single blocks** (``MTEBasicMachine``): a recipe is found only up to V[tier] x the recipe
  map's amperage; it overclocks at V[tier] x the machine's amperage as one amp, with no
  parallels and no amperage overclocks.
- **The run** (``ProcessingLogic.process`` for one recipe): the parallel helper's checks and
  parallels, the overclock of the group, then the run's ticks = overclocked ticks x the batch
  stretch (truncated), and GT's overflow checks: EU/t that overflowed a long, or ticks an int,
  fail; a plain multiblock stores its EU/t in an int, so above 2^31 - 1 it fails too.

Which machine runs which power rule, and its parallels, modifiers and overclock kind, are the
machine model's (WO-3.2, from ``machine_fact``); they become the fields of :class:`Logic`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from gregtec.planner.gtmath.jmath import (
    INT_MAX,
    LONG_MAX,
    add_safe,
    lmul,
    mul_safe,
    saturated_int,
    to_int,
    wrap_int,
)
from gregtec.planner.gtmath.overclock import (
    DEFAULT_HEAT_DISCOUNT_EXPONENT,
    UNLIMITED_TIER_SKIPS,
    Overclock,
    calculate,
)
from gregtec.planner.gtmath.parallel import Check, Output, Parallel, Supply, determine
from gregtec.planner.gtmath.tiers import V

PLAIN_HATCH_AMPS = 2  # MTEHatchEnergy.maxAmperesIn()
MAX_BATCH_SIZE = 128  # MTEMultiBlockBase.getMaxBatchSize()


@dataclass(frozen=True, slots=True)
class EnergyHatch:
    """An energy input of a multiblock: GT's plain energy hatch (2 A), or an exotic one
    (TecTech's multi-amp, laser target and wireless hatches, ``ExoticEnergyInputHelper``),
    whose working amps leave out the extra amps it accepts for line loss."""

    tier: int
    amps: int = PLAIN_HATCH_AMPS  # maxWorkingAmperesIn()
    exotic: bool = False

    @property
    def voltage(self) -> int:
        return V[self.tier]

    @classmethod
    def multi_amp(cls, tier: int, amps: int) -> EnergyHatch:
        """A TecTech multi-amp (4, 16, 64 A) or laser target hatch (256 A and up)."""
        return cls(tier=tier, amps=amps, exotic=True)


def _counted(hatches: Iterable[EnergyHatch], extended: bool) -> list[EnergyHatch]:
    # A plain multiblock's accessors read mEnergyHatches only; an extended-power one's read
    # its exotic hatches as well.
    return [h for h in hatches if extended or not h.exotic]


def max_input_voltage(hatches: Iterable[EnergyHatch], *, extended: bool = False) -> int:
    """``getMaxInputVoltage()``: the hatches' voltages summed, amps left out (the EBF's heat
    and many per-tier parallel formulas read its tier)."""
    return sum(h.voltage for h in _counted(hatches, extended))


def average_input_voltage(
    hatches: Iterable[EnergyHatch], *, extended: bool = False
) -> int:
    """``getAverageInputVoltage()``: the hatches' voltages averaged, rounded down; 0 without
    hatches."""
    counted = _counted(hatches, extended)
    return sum(h.voltage for h in counted) // len(counted) if counted else 0


def max_input_amps(hatches: Iterable[EnergyHatch], *, extended: bool = False) -> int:
    """``getMaxInputAmps()``: the hatches' working amps summed."""
    return sum(h.amps for h in _counted(hatches, extended))


def max_input_eu(hatches: Iterable[EnergyHatch], *, extended: bool = False) -> int:
    """``getMaxInputEu()``: voltage x working amps of every hatch, summed (saturating)."""
    total = 0
    for h in _counted(hatches, extended):
        total = add_safe(total, mul_safe(h.voltage, h.amps))
    return total


def check_exotic_and_normal_hatches(hatches: Sequence[EnergyHatch]) -> str | None:
    """``checkExoticAndNormalEnergyHatches``, the structure rule of multiblocks that take "one
    TecTech hatch or up to two energy hatches": why ``hatches`` break it, or None."""
    exotic = sum(1 for h in hatches if h.exotic)
    plain = len(hatches) - exotic
    if exotic == 0 and plain == 0:
        return "needs an energy hatch"
    if exotic and (plain or exotic != 1):
        return "one TecTech hatch or up to two energy hatches"
    if plain > 2:
        return "one TecTech hatch or up to two energy hatches"
    return None


@dataclass(frozen=True, slots=True)
class Power:
    """What a machine's ``setProcessingLogicPower`` gives its processing logic."""

    voltage: int  # setAvailableVoltage
    amperage: int  # setAvailableAmperage
    amperage_oc: bool = True  # setAmperageOC
    # setMaxTierSkips; UNLIMITED_TIER_SKIPS is setUnlimitedTierSkips.
    max_tier_skips: int = 1


def multiblock_power(
    hatches: Sequence[EnergyHatch], *, extended: bool = False
) -> Power:
    """GT's default multiblock power (machine fact ``power=average``) [E§2.3]."""
    plain = sum(1 for h in hatches if not h.exotic)
    single_amp = plain == 1 and len(hatches) == 1
    return Power(
        voltage=average_input_voltage(hatches, extended=extended),
        amperage=1 if single_amp else max_input_amps(hatches, extended=extended),
    )


def total_eu_power(hatches: Sequence[EnergyHatch], *, extended: bool = True) -> Power:
    """Mega machines' power (``power=total_eu``): the total input EU/t as one amp, unlimited
    tier skips. Mega machines are extended-power multiblocks, so exotic hatches count."""
    return Power(
        voltage=max_input_eu(hatches, extended=extended),
        amperage=1,
        max_tier_skips=UNLIMITED_TIER_SKIPS,
    )


@dataclass(frozen=True, slots=True)
class Recipe:
    """What the rules read of a recipe."""

    eut: int  # mEUt
    duration: int  # mDuration, ticks
    # The heat it needs (mSpecialValue, COIL_HEAT) on heat maps; 0 elsewhere.
    heat: int = 0


@dataclass(frozen=True, slots=True)
class Logic:
    """A multiblock's ``ProcessingLogic`` as set up for a recipe check: its power, GT's other
    setters, and the calculator changes machines make in ``createOverclockCalculator``.
    Defaults are GT's."""

    power: Power
    max_parallel: int = 1  # setMaxParallel
    eu_modifier: float = 1.0  # setEuModifier: recipe EU/t x this
    speed_boost: float = 1.0  # setSpeedBonus: duration x this (0.5 is twice as fast)
    batch_size: int = 1  # setBatchSize: MAX_BATCH_SIZE with batch mode on
    oc_time_reduction: float = 2.0  # setOverclock; enablePerfectOverclock: 4
    oc_power_increase: float = 4.0
    protect_items: bool = False  # setVoidProtection
    protect_fluids: bool = False
    item_output_limit: int | None = None  # the machine's output slot limits
    fluid_output_limit: int | None = None
    chance_multiplier: float = 1.0
    # Calculator changes: the EBF family's heat, laser overclocks, caps, no overclock.
    heat_oc: bool = False
    heat_discount: bool = False
    machine_heat: int = 0
    recipe_heat: int | None = None  # the heat compared with; None: the recipe's own
    # Refuse recipes hotter than the machine (insufficient_heat), as the EBF does.
    check_heat: bool = False
    heat_discount_exponent: float = DEFAULT_HEAT_DISCOUNT_EXPONENT
    laser_oc: bool = False
    max_overclocks: int = INT_MAX
    max_regular_overclocks: int = INT_MAX
    no_overclock: bool = False
    duration_under_one_tick: float | None = None
    # MTEMultiBlockBase keeps EU/t in an int; MTEExtendedPowerMultiBlockBase in a long.
    int_power: bool = True

    def overclock(self, recipe: Recipe) -> Overclock:
        """``createOverclockCalculator`` for ``recipe``."""
        return Overclock(
            recipe_eut=recipe.eut,
            amperage=self.power.amperage,
            voltage=self.power.voltage,
            max_tier_skips=self.power.max_tier_skips,
            duration=recipe.duration,
            duration_modifier=self.speed_boost,
            eut_modifier=self.eu_modifier,
            amperage_oc=self.power.amperage_oc,
            duration_decrease_per_oc=self.oc_time_reduction,
            eut_increase_per_oc=self.oc_power_increase,
            heat_oc=self.heat_oc,
            heat_discount=self.heat_discount,
            recipe_heat=recipe.heat if self.recipe_heat is None else self.recipe_heat,
            machine_heat=self.machine_heat,
            heat_discount_exponent=self.heat_discount_exponent,
            laser_oc=self.laser_oc,
            max_overclocks=self.max_overclocks,
            max_regular_overclocks=self.max_regular_overclocks,
            no_overclock=self.no_overclock,
            duration_under_one_tick=self.duration_under_one_tick,
        )


@dataclass(frozen=True, slots=True)
class Run:
    """One run of a recipe check: what the machine does per run, or why it can't run."""

    check: Check
    eut: int = 0  # EU/t drawn while it runs
    duration: int = 0  # ticks per run
    parallel: int = 0  # recipes per run
    overclocks: int = 0
    required: int | None = None  # the EU/t, recipe EU/t or heat it lacked

    @property
    def ok(self) -> bool:
        return self.check is Check.SUCCESS

    @property
    def crafts_per_second(self) -> float:
        """Recipes per second while running (20 ticks a second); 0 when it can't run."""
        return self.parallel * 20 / self.duration if self.ok else 0.0


def process(
    recipe: Recipe,
    logic: Logic,
    *,
    inputs: tuple[Supply, ...] | None = None,
    outputs: tuple[Output, ...] = (),
) -> Run:
    """``ProcessingLogic.process`` for one recipe the machine's recipe map matched, then the
    multiblock's own overflow check (``postCheckRecipe``)."""
    if logic.check_heat and recipe.heat > logic.machine_heat:
        return Run(Check.INSUFFICIENT_HEAT, required=recipe.heat)
    helper = determine(
        Parallel(
            recipe_eut=recipe.eut,
            duration=recipe.duration,
            available_eut=lmul(logic.power.voltage, logic.power.amperage),
            max_parallel=logic.max_parallel,
            eut_modifier=logic.eu_modifier,
            batch_size=logic.batch_size,
            overclock=logic.overclock(recipe),
            inputs=inputs,
            outputs=outputs,
            protect_items=logic.protect_items,
            protect_fluids=logic.protect_fluids,
            item_output_limit=logic.item_output_limit,
            fluid_output_limit=logic.fluid_output_limit,
            chance_multiplier=logic.chance_multiplier,
        )
    )
    oc = helper.overclock
    if helper.check is not Check.SUCCESS or oc is None:
        return Run(helper.check, required=helper.required)
    if oc.eut == LONG_MAX:
        return Run(Check.POWER_OVERFLOW, parallel=helper.parallel)
    if oc.duration == INT_MAX:
        return Run(Check.DURATION_OVERFLOW, parallel=helper.parallel)
    ticks = oc.duration * helper.duration_multiplier
    if ticks >= INT_MAX:
        return Run(Check.DURATION_OVERFLOW, parallel=helper.parallel)
    if logic.int_power and oc.eut > INT_MAX:
        return Run(Check.POWER_OVERFLOW, parallel=helper.parallel)
    return Run(
        Check.SUCCESS,
        eut=oc.eut,
        duration=to_int(ticks),
        parallel=helper.parallel,
        overclocks=oc.overclocks,
    )


def basic_machine(
    recipe: Recipe, tier: int, *, amperage: int = 1, map_amperage: int = 1
) -> Run:
    """A single-block machine of ``tier`` (``MTEBasicMachine`` with GT's EU overclocks)."""
    if lmul(V[tier], map_amperage) < recipe.eut:
        return Run(Check.NO_RECIPE, required=recipe.eut)
    oc = calculate(
        Overclock(
            recipe_eut=recipe.eut,
            duration=recipe.duration,
            voltage=saturated_int(V[tier] * amperage),
        )
    )
    # mEUt = (int) getConsumption(); mMaxProgresstime = Math.max(1, getDuration()).
    return Run(
        Check.SUCCESS,
        eut=wrap_int(oc.eut),
        duration=max(1, oc.duration),
        parallel=1,
        overclocks=oc.overclocks,
    )
