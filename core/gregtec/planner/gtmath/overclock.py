"""GT's overclock calculator (WO-3.1, D173): GT 5.09.54.265's ``OverclockCalculator`` in Python.

:func:`calculate` takes a recipe (EU/t, ticks) and what the machine offers (:class:`Overclock`)
and gives the EU/t it draws, the ticks it takes and how many overclocks it made, rounded the way
GT rounds them. :func:`multiplier_under_one_tick` gives the parallel multiplier GT grants for
overclocks past one tick [E§2.2]. The rules, as GT runs them:

- **Power.** Recipe power = recipe EU/t x parallel x EU modifier x heat discount. Machine power =
  voltage x amperage with ``amperage_oc`` (multiblocks), else voltage x min(amperage, parallel).
- **Standard.** Overclocks = the number of 4x steps from the recipe power (at least 32 EU/t, so
  ULV counts as LV) up to the machine power, at most ``max_overclocks``; without ``amperage_oc``
  also at most the voltage tiers between machine and recipe. Each multiplies EU/t by
  ``eut_increase_per_oc`` (4) and divides the duration by ``duration_decrease_per_oc`` (2; 4 for
  perfect overclocks).
- **Heat** (the EBF family). Each full 1800 K the machine is above the recipe makes one of the
  overclocks perfect (duration / 4), and with ``heat_discount`` each full 900 K multiplies the
  recipe's EU/t by 0.95. A colder machine gets neither.
- **Laser.** Regular overclocks up to ``max_regular_overclocks``, then laser overclocks while the
  power lasts, the n-th costing 4 + 0.3 n times the EU (the 32 EU/t floor counts only for the
  regular steps).
- **None.** ``no_overclock``: the recipe as it is.
- **Rounding.** EU/t is rounded up to a long (``Long.MAX_VALUE`` when it overflows, GT's "power
  overflow"); the duration is truncated to an int, at least 1 tick (rounded up with no
  overclock).

Re-implemented from GT's behaviour (GT is LGPL-3.0; source read at tag 5.09.54.265, commit
``bb00f3f1023e``, nothing copied) and gated by GT's own unit tests, ported as test vectors in
``tests/unit/gtmath/`` (CLAUDE.md: a change here must keep them green). Inputs a real recipe never
has are refused (laser overclocks with no recipe power, which GT would loop on forever).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from gregtec.planner.gtmath.jmath import (
    INT_MAX,
    LONG_MAX,
    ceil,
    clamp,
    ddiv,
    dmax,
    dmin,
    floor,
    ldiv,
    lmul,
    log,
    log4,
    log4ceil,
    pow_int,
    round_long,
    to_int,
    to_long,
    wrap_int,
)

# OverclockCalculator's constants.
HEAT_DISCOUNT_THRESHOLD = 900
HEAT_OVERCLOCK_THRESHOLD = 1800
DURATION_DECREASE_PER_HEAT_OC = 4.0
DEFAULT_HEAT_DISCOUNT_EXPONENT = 0.95
UNLIMITED_TIER_SKIPS = INT_MAX


@dataclass(frozen=True, slots=True)
class Overclock:
    """An ``OverclockCalculator`` as set up: one field per setter, with GT's defaults."""

    recipe_eut: int = 0  # setRecipeEUt: the recipe's EU/t
    duration: int = 0  # setDuration: the recipe's ticks
    voltage: int = 0  # setEUt: the machine's voltage
    amperage: int = 1  # setAmperage
    parallel: int = 1  # setParallel: the machine's parallel when it overclocks
    current_parallel: int | None = None  # setCurrentParallel (None: never called)
    eut_modifier: float = 1.0  # setEUtDiscount: 0.9 is 10% less EU
    duration_modifier: float = 1.0  # setDurationModifier: 0.9 is 10% faster
    eut_increase_per_oc: float = 4.0  # setEUtIncreasePerOC
    duration_decrease_per_oc: float = (
        2.0  # setDurationDecreasePerOC; enablePerfectOC: 4
    )
    max_overclocks: int = INT_MAX  # setMaxOverclocks (below 0 counts as 0)
    max_regular_overclocks: int = (
        INT_MAX  # setMaxRegularOverclocks (below 0 counts as 0)
    )
    max_tier_skips: int = (
        1  # setMaxTierSkips; UNLIMITED_TIER_SKIPS: setUnlimitedTierSkips
    )
    amperage_oc: bool = False  # setAmperageOC
    laser_oc: bool = False  # setLaserOC
    no_overclock: bool = False  # setNoOverclock
    heat_oc: bool = False  # setHeatOC
    heat_discount: bool = False  # setHeatDiscount
    recipe_heat: int = 0  # setRecipeHeat
    machine_heat: int = 0  # setMachineHeat
    heat_discount_exponent: float = (
        DEFAULT_HEAT_DISCOUNT_EXPONENT  # setHeatDiscountMultiplier
    )
    duration_under_one_tick: float | None = (
        None  # setDurationUnderOneTickSupplier's value
    )

    def __post_init__(self) -> None:
        # GT's setters refuse these (IllegalArgumentException).
        if self.eut_increase_per_oc <= 0:
            raise ValueError("EUt increase can't be a negative number or zero")
        if self.duration_decrease_per_oc <= 0:
            raise ValueError("Duration decrease can't be a negative number or zero")

    @classmethod
    def no_oc(cls, eut: int, duration: int) -> Overclock:
        """``OverclockCalculator.ofNoOverclock``: the recipe's own EU/t and duration."""
        return cls(recipe_eut=eut, duration=duration, voltage=eut, no_overclock=True)

    def perfect(self) -> Overclock:
        """``enablePerfectOC``: each overclock divides the duration by 4."""
        return replace(self, duration_decrease_per_oc=4.0)


@dataclass(frozen=True, slots=True)
class OverclockResult:
    """What GT's calculator reports after ``calculate()``."""

    eut: int  # getConsumption(): EU/t, a long
    duration: int  # getDuration(): ticks, an int
    overclocks: int  # getPerformedOverclocks()
    parallel: int  # the parallel the recipe power counts
    current_parallel: int  # getCurrentParallel()


def heat_surplus(oc: Overclock) -> int:
    """Heat above the recipe's; never below 0 (GT since #8321)."""
    return max(0, wrap_int(oc.machine_heat - oc.recipe_heat))


def heat_discount_multiplier(oc: Overclock) -> float:
    """``calculateHeatDiscountMultiplier``: ``heat_discount_exponent`` to the number of full
    900 K above the recipe, 1 without ``heat_discount``."""
    discounts = heat_surplus(oc) // HEAT_DISCOUNT_THRESHOLD if oc.heat_discount else 0
    return pow_int(oc.heat_discount_exponent, discounts)


def max_allowed_recipe_eut(voltage: int, max_tier_skips: int) -> int:
    """``getMaxAllowedRecipeEUt``: the highest recipe EU/t a machine of ``voltage`` may run,
    voltage x 4^tier skips (``Long.MAX_VALUE`` for unlimited skips) [E§2.2]."""
    if max_tier_skips == UNLIMITED_TIER_SKIPS:
        return LONG_MAX
    return to_long(floor(float(voltage) * pow_int(4.0, max_tier_skips)))


def tiers_above(power: float, base: float) -> int:
    """``getTiersAbove``: how many 4x steps ``power`` is above ``base`` (taken as at least 32),
    -1 when it is below. Compared in hundredths, as GT does, so a value a hair under a step
    from floating-point error still counts."""
    if power < base:
        return -1
    scale = 100
    if power < float(LONG_MAX // scale):
        scaled_power = round_long(power * scale)
        scaled_base = round_long(base * scale)
        return wrap_int(log4(ldiv(scaled_power, max(scaled_base, 32 * scale))))
    return wrap_int(log4(to_long(ddiv(power, dmax(base, 32.0)))))


def _parallel(oc: Overclock) -> int:
    # setCurrentParallel lowers the parallel to the one actually run.
    if oc.current_parallel is None:
        return oc.parallel
    return min(oc.parallel, oc.current_parallel)


def _base_duration(oc: Overclock) -> float:
    if oc.duration_under_one_tick is not None:
        return oc.duration_under_one_tick
    return oc.duration * oc.duration_modifier


def _powers(oc: Overclock, parallel: int) -> tuple[float, float]:
    recipe_power = (
        float(lmul(oc.recipe_eut, parallel))
        * oc.eut_modifier
        * heat_discount_multiplier(oc)
    )
    amps = oc.amperage if oc.amperage_oc else min(oc.amperage, parallel)
    return recipe_power, float(lmul(oc.voltage, amps))


def _voltage_tier(eu: int) -> int:
    # (int) Math.max(GTUtility.log4ceil(eu / 8), 1): LV is 1, ULV counts as LV.
    return wrap_int(max(log4ceil(ldiv(eu, 8)), 1))


def _laser(
    oc: Overclock, recipe_power: float, machine_power: float
) -> tuple[int, int, float]:
    """(regular overclocks, laser overclocks, EU/t before rounding)."""
    power_for_tiers = dmax(recipe_power, 32.0)
    ratio = to_long(ddiv(machine_power, power_for_tiers))
    max_regular = max(oc.max_regular_overclocks, 0)
    if oc.eut_increase_per_oc == 4:
        regular = wrap_int(max(0, min(max_regular, log4(ratio))))
    else:
        steps = ddiv(log(float(ratio)), log(oc.eut_increase_per_oc))
        regular = to_int(dmax(0.0, dmin(float(max_regular), steps)))
    eut = recipe_power * pow_int(oc.eut_increase_per_oc, regular)
    if not eut > 0:
        # GT multiplies a power of 0 (or below) forever and never leaves its loop.
        raise ValueError(
            f"laser overclocks need a recipe power above 0 EU/t (got {recipe_power})"
        )
    used = eut
    lasers = 0
    eut *= oc.eut_increase_per_oc + 0.3 * (lasers + 1)
    while eut <= machine_power:
        lasers += 1
        used = eut
        eut *= oc.eut_increase_per_oc + 0.3 * (lasers + 1)
    return regular, lasers, used


def calculate(oc: Overclock) -> OverclockResult:
    """``calculate()``: the overclocked EU/t, ticks and overclock count."""
    duration = _base_duration(oc)
    parallel = _parallel(oc)
    current = max(oc.current_parallel or 0, parallel)
    recipe_power, machine_power = _powers(oc, parallel)
    tiers = tiers_above(machine_power, recipe_power)

    if oc.no_overclock:
        eut = to_long(ceil(recipe_power))
        return OverclockResult(eut, to_int(ceil(duration)), 0, parallel, current)

    if oc.laser_oc:
        regular, lasers, used = _laser(oc, recipe_power, machine_power)
        overclocks = regular + lasers
        ticks = ddiv(duration, pow_int(oc.duration_decrease_per_oc, overclocks))
        eut = to_long(ceil(used))
        return OverclockResult(
            eut, to_int(dmax(ticks, 1.0)), overclocks, parallel, current
        )

    overclocks = min(max(oc.max_overclocks, 0), tiers)
    if not oc.amperage_oc:
        voltage_tiers = _voltage_tier(oc.voltage) - _voltage_tier(oc.recipe_eut)
        overclocks = min(overclocks, voltage_tiers)
    # Never below 0, so a recipe needing more than 1 A still runs on a single hatch.
    overclocks = max(overclocks, 0)
    heat_overclocks = min(
        heat_surplus(oc) // HEAT_OVERCLOCK_THRESHOLD if oc.heat_oc else 0, overclocks
    )
    regular = overclocks - heat_overclocks
    eut = to_long(ceil(recipe_power * pow_int(oc.eut_increase_per_oc, overclocks)))
    duration = ddiv(duration, pow_int(DURATION_DECREASE_PER_HEAT_OC, heat_overclocks))
    duration = ddiv(duration, pow_int(oc.duration_decrease_per_oc, regular))
    return OverclockResult(
        eut, to_int(dmax(duration, 1.0)), overclocks, parallel, current
    )


def multiplier_under_one_tick(oc: Overclock) -> float:
    """``calculateMultiplierUnderOneTick``: how many times the parallel GT allows because the
    overclocks go past one tick (1 when they don't). GT's parallel helper multiplies the max
    parallel by it, so power past one tick still buys throughput [E§2.2]."""
    if oc.no_overclock:
        return 1.0
    duration = _base_duration(oc)
    parallel = _parallel(oc)
    recipe_power, machine_power = _powers(oc, parallel)
    voltage_tiers = _voltage_tier(oc.voltage) - _voltage_tier(oc.recipe_eut)
    power_tiers = tiers_above(machine_power, recipe_power)

    if oc.laser_oc:
        regular, lasers, _ = _laser(oc, recipe_power, machine_power)
        step = oc.duration_decrease_per_oc
        needed = to_int(ceil(ddiv(log(duration), log(step))))
        overclocks = regular + lasers
        extra = max(wrap_int(overclocks - needed), 0)
        if overclocks < needed:
            return 1.0
        correction = ddiv(pow_int(step, needed), duration)
        return ceil(correction * pow_int(step, extra))

    overclocks = clamp(
        oc.max_overclocks, 0, power_tiers if oc.amperage_oc else voltage_tiers
    )
    heat_overclocks = min(
        heat_surplus(oc) // HEAT_OVERCLOCK_THRESHOLD if oc.heat_oc else 0, overclocks
    )
    regular = overclocks - heat_overclocks
    after_heat = ddiv(duration, pow_int(DURATION_DECREASE_PER_HEAT_OC, heat_overclocks))
    # Overclocks needed to reach one tick: by heat alone, and regular ones after the heat ones.
    needed_heat = to_int(ceil(ddiv(log(duration), log(DURATION_DECREASE_PER_HEAT_OC))))
    needed_regular = to_int(
        ceil(ddiv(log(after_heat), log(oc.duration_decrease_per_oc)))
    )
    extra_heat = max(wrap_int(heat_overclocks - needed_heat), 0)
    extra_regular = max(wrap_int(regular - needed_regular), 0)
    heat_multiplier = pow_int(DURATION_DECREASE_PER_HEAT_OC, extra_heat)
    regular_multiplier = pow_int(oc.duration_decrease_per_oc, extra_regular)
    # The fraction of a step lost to whole ticks.
    if heat_overclocks >= needed_heat:
        correction = ddiv(pow_int(DURATION_DECREASE_PER_HEAT_OC, needed_heat), duration)
    elif regular >= needed_regular:
        correction = ddiv(
            pow_int(oc.duration_decrease_per_oc, needed_regular), after_heat
        )
    else:
        return 1.0
    return ceil(heat_multiplier * regular_multiplier * correction)
