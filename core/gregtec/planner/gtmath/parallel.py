"""GT's parallel helper (WO-3.1, D173): GT 5.09.54.265's ``ParallelHelper`` in Python.

:func:`determine` decides how many recipes a multiblock runs at once and the overclock of the
group, as GT's ``ParallelHelper.build()`` does [E§2.4]:

1. The recipe must fit the power: its EU/t x EU modifier x heat discount, rounded up, at most
   the available EU/t (``insufficient_power``); and the voltage: its EU/t at most voltage x
   4^tier skips (``insufficient_voltage``).
2. Max parallel x the sub-tick multiplier (overclocks past one tick buy parallels), then x the
   batch size in batch mode.
3. With void protection, at most what the output room holds, counting a chanced output at its
   worst case; outputs past the machine's output-slot limit are dropped first.
4. At most what the available EU/t pays for (each parallel at the rounded-up EU/t), then what
   the inputs allow.
5. The overclock of that group (:mod:`.overclock`).
6. Batch mode: while the overclocked duration is under 128 ticks, extra parallels up to the
   batch size, 128 ticks and the inputs left, stretching the duration by the same factor (a
   ``float`` factor, as GT computes it).

The items and fluids themselves (bus layout, ore-dictionary matching, the order GT fills output
hatches in) are the caller's model: a :class:`Supply` is one recipe input and how much the
machine holds of it, an :class:`Output` one output and the room for it. GT has no unit tests for
this class at the pinned tag; it is checked against GT's own class instead (D174).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from gregtec.planner.gtmath.jmath import (
    ceil,
    ddiv,
    dmin,
    floor,
    ldiv,
    lmul,
    safe_int,
    to_float,
    to_int,
    to_long,
    wrap_int,
)
from gregtec.planner.gtmath.overclock import (
    Overclock,
    OverclockResult,
    calculate,
    heat_discount_multiplier,
    max_allowed_recipe_eut,
    multiplier_under_one_tick,
)

# ParallelHelper.MAX_BATCH_MODE_TICK_TIME: batch mode only stretches runs shorter than this.
MAX_BATCH_MODE_TICK_TIME = 128.0
CHANCE_SCALE = 10000  # chances are in 1/10000


class Check(StrEnum):
    """GT's ``CheckRecipeResult`` ids for the outcomes of these rules."""

    SUCCESS = "success"
    NONE = "none"  # max parallel 0: nothing was checked
    NO_RECIPE = "no_recipe"  # a single block doesn't find a recipe above its voltage
    INSUFFICIENT_POWER = "insufficient_power"
    INSUFFICIENT_VOLTAGE = "insufficient_voltage"
    INSUFFICIENT_HEAT = "insufficient_heat"
    ITEM_OUTPUT_FULL = "item_output_full"
    FLUID_OUTPUT_FULL = "fluid_output_full"
    INTERNAL_ERROR = "internal_error"  # no parallel can run: an input is missing
    POWER_OVERFLOW = "power_overflow"
    DURATION_OVERFLOW = "duration_overflow"


@dataclass(frozen=True, slots=True)
class Supply:
    """One recipe input and what the machine holds of it (merge an item that fills several
    slots into one supply, as GT merges a recipe's item inputs)."""

    # What one craft takes; 0 when it isn't consumed (a circuit, a catalyst).
    per_craft: int
    available: int  # what the machine holds of it
    fluid: bool = False


@dataclass(frozen=True, slots=True)
class Output:
    """One recipe output."""

    amount: int  # items or L per craft
    chance: int = CHANCE_SCALE  # in 1/10000
    fluid: bool = False
    # With void protection: the room for it in the output buses or hatches, in items or L.
    # None: no limit (an ME output that takes it all, or no void protection).
    space: int | None = None


@dataclass(frozen=True, slots=True)
class Parallel:
    """A ``ParallelHelper`` as set up for one recipe."""

    recipe_eut: int  # the recipe's EU/t
    duration: int  # the recipe's ticks
    available_eut: int  # setAvailableEUt: the machine's EU/t, amperage included
    max_parallel: int = 1  # setMaxParallel
    eut_modifier: float = 1.0  # setEUtModifier
    batch_size: int = 1  # enableBatchMode: batch mode is on above 1
    overclock: Overclock | None = None  # setCalculator; None: GT's default calculator
    inputs: tuple[Supply, ...] | None = None  # None: the inputs never limit
    outputs: tuple[Output, ...] = ()
    protect_items: bool = False  # void protection for items
    protect_fluids: bool = False  # void protection for fluids
    # The machine's getItemOutputLimit() and getFluidOutputLimit(); None: every output.
    item_output_limit: int | None = None
    fluid_output_limit: int | None = None
    chance_multiplier: float = 1.0  # setChanceMultiplier


@dataclass(frozen=True, slots=True)
class ParallelResult:
    """What GT's parallel helper reports after ``build()``."""

    check: Check
    parallel: int = 0  # getCurrentParallel(), batch parallels included
    duration_multiplier: float = 1.0  # getDurationMultiplierDouble()
    overclock: OverclockResult | None = None  # the calculator's result, on success
    required: int | None = None  # the EU/t (power) or recipe EU/t (voltage) it lacked
    # The max parallel after the sub-tick multiplier, the batch size and the output room.
    max_parallel: int = 0


def parallel_from_inputs(max_parallel: int, inputs: Sequence[Supply] | None) -> float:
    """``GTRecipe.maxParallelCalculatedByInputs``: how many crafts the inputs allow, up to
    ``max_parallel``; 0 when a consumed input is missing. A fraction means the inputs are
    there but not enough for one more. Fluids are counted first, then items, as GT does.
    Whether the machine holds the recipe's unconsumed inputs (a circuit) is the recipe
    lookup's question, not this one's: GT doesn't ask it again here."""
    current = float(max_parallel)
    if inputs is None:
        return current
    for s in (s for s in inputs if s.fluid):
        if s.per_craft <= 0:
            continue  # GT doesn't count an unconsumed fluid's amount
        if s.available <= 0:
            return 0.0
        current = dmin(current, ddiv(float(s.available), float(s.per_craft)))
        if current <= 0:
            return 0.0
    for s in (s for s in inputs if not s.fluid):
        if s.available >= s.per_craft * current:
            continue
        if s.available <= 0:
            return 0.0
        current = dmin(current, ddiv(float(s.available), float(s.per_craft)))
    return current


def _consumed(
    inputs: tuple[Supply, ...] | None, crafts: int
) -> tuple[Supply, ...] | None:
    """The inputs left after ``crafts`` crafts took theirs."""
    if inputs is None:
        return None
    return tuple(
        replace(s, available=s.available - s.per_craft * crafts) for s in inputs
    )


def worst_case_output(output: Output, chance_multiplier: float = 1.0) -> int:
    """What void protection reserves room for per parallel: the amount times its chance (x the
    chance multiplier) rounded up to whole outputs (``VoidProtectionHelper``)."""
    scale = ceil(ddiv(chance_multiplier * output.chance, float(CHANCE_SCALE)))
    return to_int(output.amount * scale)


def _output_room(
    outputs: Sequence[Output], max_parallel: int, chance_multiplier: float
) -> int:
    for o in outputs:
        if o.space is None:
            continue
        per = worst_case_output(o, chance_multiplier)
        if per <= 0:
            continue
        max_parallel = min(max_parallel, max(o.space, 0) // per)
    return max_parallel


def determine(p: Parallel) -> ParallelResult:
    """``ParallelHelper.build()``: the parallel, overclock and batch stretch for one recipe."""
    if p.max_parallel <= 0:
        return ParallelResult(Check.NONE)
    calc = p.overclock
    if calc is None:
        calc = Overclock(
            voltage=p.available_eut,
            recipe_eut=p.recipe_eut,
            duration=p.duration,
            eut_modifier=p.eut_modifier,
        )
    discount = heat_discount_multiplier(calc)
    parallel_eut = to_long(ceil(p.recipe_eut * p.eut_modifier * discount))
    if p.available_eut < parallel_eut:
        return ParallelResult(Check.INSUFFICIENT_POWER, required=parallel_eut)
    if p.recipe_eut > max_allowed_recipe_eut(calc.voltage, calc.max_tier_skips):
        return ParallelResult(Check.INSUFFICIENT_VOLTAGE, required=p.recipe_eut)

    original = p.max_parallel
    calc = replace(calc, parallel=original)
    max_parallel = original
    if calc.duration_under_one_tick is not None:
        if calc.duration_under_one_tick < 1:
            max_parallel = safe_int(
                to_long(ddiv(original, calc.duration_under_one_tick)), 0
            )
    else:
        max_parallel = safe_int(to_long(original * multiplier_under_one_tick(calc)), 0)
    before_batch = max_parallel
    batch = p.batch_size > 1
    if batch:
        max_parallel = safe_int(lmul(max_parallel, p.batch_size), 0)

    items = [o for o in p.outputs if not o.fluid]
    fluids = [o for o in p.outputs if o.fluid]
    if p.item_output_limit is not None:
        items = items[: max(p.item_output_limit, 0)]
    if p.fluid_output_limit is not None:
        fluids = fluids[: max(p.fluid_output_limit, 0)]
    if p.protect_items and items:
        max_parallel = _output_room(items, max_parallel, p.chance_multiplier)
        if max_parallel <= 0:
            return ParallelResult(Check.ITEM_OUTPUT_FULL, max_parallel=max_parallel)
    if p.protect_fluids and fluids:
        max_parallel = _output_room(fluids, max_parallel, p.chance_multiplier)
        if max_parallel <= 0:
            return ParallelResult(Check.FLUID_OUTPUT_FULL, max_parallel=max_parallel)

    before_batch = min(max_parallel, before_batch)
    if parallel_eut > 0:
        actual = wrap_int(min(before_batch, ldiv(p.available_eut, parallel_eut)))
    else:
        actual = before_batch
    current = to_int(parallel_from_inputs(actual, p.inputs))
    if current <= 0:
        return ParallelResult(Check.INTERNAL_ERROR, max_parallel=max_parallel)

    oc = calculate(replace(calc, current_parallel=current))
    multiplier = 0.0
    if batch and oc.duration < MAX_BATCH_MODE_TICK_TIME:
        batch_max = ddiv(MAX_BATCH_MODE_TICK_TIME, float(oc.duration))
        room = dmin(batch_max - 1, float(p.batch_size - 1))
        max_extra = to_int(floor(dmin(current * room, float(max_parallel - current))))
        left = _consumed(p.inputs, current)
        extra = to_int(parallel_from_inputs(max_extra, left))
        # 1.0f + (float) extra / current: single precision, as GT computes it.
        multiplier = to_float(1.0 + to_float(to_float(extra) / to_float(current)))
        current += extra
    return ParallelResult(
        Check.SUCCESS,
        parallel=current,
        duration_multiplier=multiplier if batch and multiplier > 0 else 1.0,
        overclock=oc,
        max_parallel=max_parallel,
    )


def expected_chanced_multiplier(chance: int, parallel: int) -> float:
    """The mean of GT's ``calculateIntegralChancedOutputMultiplier(chance, parallel)``: whole
    hundred-percents count every time, the rest is a binomial roll (or its normal
    approximation, whose clipping past 3 standard deviations is left out)."""
    if chance < 0:
        raise ValueError(f"a chance can't be negative ({chance})")
    whole, part = divmod(chance, CHANCE_SCALE)
    return whole * parallel + parallel * part / CHANCE_SCALE


def expected_output(
    output: Output, parallel: int, chance_multiplier: float = 1.0
) -> float:
    """The expected amount of ``output`` from a run of ``parallel`` recipes (GT rolls the chance
    ``(int)(chance x chance_multiplier)``); the planner plans with it [E§2.6]."""
    chance = to_int(output.chance * chance_multiplier)
    return output.amount * expected_chanced_multiplier(chance, parallel)
