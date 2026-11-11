"""The gtmath port's own parts (WO-3.1): Java arithmetic, tiers and coils, the input and output
rules of the parallel helper, and the processing-logic power rules.

GT's unit tests are in ``test_gt_unit_tests.py`` and GT's own classes' results in
``test_oracle_vectors.py``; these cover what neither reaches because it needs a running game in
GT (items, fluids, output buses, energy hatches). Expected values are worked out by hand from
GT 5.09.54.265's rules, named in each test.
"""

from __future__ import annotations

import math

import pytest

from gregtec import config
from gregtec.planner import gtmath
from gregtec.planner.gtmath import jmath
from gregtec.planner.gtmath.jmath import INT_MAX, INT_MIN, LONG_MAX, LONG_MIN
from gregtec.planner.gtmath.overclock import (
    UNLIMITED_TIER_SKIPS,
    Overclock,
    calculate,
    multiplier_under_one_tick,
)
from gregtec.planner.gtmath.parallel import (
    Check,
    Output,
    Parallel,
    Supply,
    determine,
    expected_output,
    parallel_from_inputs,
    worst_case_output,
)
from gregtec.planner.gtmath.processing import (
    EnergyHatch,
    Logic,
    Power,
    Recipe,
    Run,
    average_input_voltage,
    basic_machine,
    max_input_amps,
    max_input_eu,
    max_input_voltage,
    multiblock_power,
    process,
    total_eu_power,
)
from gregtec.planner.gtmath.tiers import (
    COILS,
    VN,
    VP,
    V,
    amperage_for_tier,
    coil_heat,
    ebf_heat,
    get_tier,
    get_tier_extended,
)


def _negative_zero(x: float) -> bool:
    return x == 0 and math.copysign(1.0, x) < 0


def test_the_port_is_of_the_pinned_gt() -> None:
    """A GT pin bump re-ports and re-vendors (docs/runbooks/pack_update.md)."""
    assert gtmath.GT_VERSION == config.GT_PIN
    assert len(gtmath.GT_COMMIT) == 40


# --- jmath: Java's arithmetic ---


def test_long_and_int_arithmetic_wraps() -> None:
    assert jmath.wrap_long(2**63) == LONG_MIN
    assert jmath.wrap_long(-(2**63) - 1) == LONG_MAX
    assert jmath.wrap_int(2**31) == INT_MIN
    assert jmath.wrap_int(2**32 + 5) == 5
    assert jmath.lmul(2**62, 2) == LONG_MIN
    assert jmath.lmul(2**62, 4) == 0
    assert jmath.lmul(-3, 7) == -21


def test_long_division_truncates_toward_zero() -> None:
    assert jmath.ldiv(7, 2) == 3
    assert jmath.ldiv(-7, 2) == -3
    assert jmath.ldiv(7, -2) == -3
    assert jmath.ldiv(-7, -2) == 3
    assert jmath.ldiv(LONG_MIN, -1) == LONG_MIN
    with pytest.raises(ZeroDivisionError):
        jmath.ldiv(1, 0)


def test_casts_from_double_truncate_and_saturate() -> None:
    assert jmath.to_long(-1.9) == -1
    assert jmath.to_long(math.nan) == 0
    assert jmath.to_long(1e30) == LONG_MAX
    assert jmath.to_long(-math.inf) == LONG_MIN
    assert jmath.to_int(-3.99) == -3
    assert jmath.to_int(2.0**31) == INT_MAX
    assert jmath.to_int(-1e12) == INT_MIN
    assert jmath.to_int(math.nan) == 0
    assert jmath.saturated_int(2**40) == INT_MAX
    assert jmath.saturated_int(-(2**40)) == INT_MIN
    assert jmath.saturated_int(12) == 12


def test_float_rounds_to_single_precision() -> None:
    assert jmath.to_float(0.9) == 0.8999999761581421
    assert jmath.to_float(0.5) == 0.5
    assert jmath.to_float(1e300) == math.inf
    assert jmath.to_float(-1e300) == -math.inf


def test_ceil_floor_and_round_follow_java() -> None:
    assert jmath.ceil(1.2) == 2.0
    assert _negative_zero(jmath.ceil(-0.5))
    assert jmath.ceil(math.inf) == math.inf
    assert math.isnan(jmath.ceil(math.nan))
    assert jmath.floor(-1.2) == -2.0
    assert _negative_zero(jmath.floor(-0.0))
    assert jmath.floor(-math.inf) == -math.inf
    assert jmath.round_long(2.5) == 3
    assert jmath.round_long(-2.5) == -2  # ties go up
    assert jmath.round_long(0.49999999999999994) == 0
    assert jmath.round_long(math.nan) == 0
    assert jmath.round_long(1e30) == LONG_MAX
    assert jmath.round_long(-1e30) == LONG_MIN


def test_double_division_min_max_and_log_never_raise() -> None:
    assert jmath.ddiv(1.0, 0.0) == math.inf
    assert jmath.ddiv(-1.0, 0.0) == -math.inf
    assert jmath.ddiv(1.0, -0.0) == -math.inf
    assert math.isnan(jmath.ddiv(0.0, 0.0))
    assert jmath.ddiv(7.0, 2.0) == 3.5
    assert math.isnan(jmath.dmax(math.nan, 1.0))
    assert math.isnan(jmath.dmin(1.0, math.nan))
    assert not _negative_zero(jmath.dmax(0.0, -0.0))
    assert _negative_zero(jmath.dmin(0.0, -0.0))
    assert jmath.dmax(2.0, 3.0) == 3.0
    assert jmath.dmin(2.0, 3.0) == 2.0
    assert jmath.log(0.0) == -math.inf
    assert math.isnan(jmath.log(-1.0))
    assert jmath.log(math.inf) == math.inf
    assert jmath.log(1.0) == 0.0


def test_log_is_correctly_rounded_on_every_machine() -> None:
    assert jmath.log(2.0) == 0.6931471805599453
    assert jmath.log(10.0) == 2.302585092994046
    # What the JVM's Math.log gives (x86-64) where glibc's log is one unit off (D175).
    assert jmath.log(9170.0) == float.fromhex("0x1.23f54a1c504c1p+3")
    assert jmath.log(1352.7) == float.fromhex("0x1.cd6e4fb882fdfp+2")
    assert jmath.log(5e-324) == -744.4400719213812  # the smallest double
    # The overclocks needed to reach one tick are ceil(log(ticks) / log(2 or 4)): a power of
    # two comes out whole for every duration a recipe has...
    for k in range(1, 29):
        assert jmath.ceil(jmath.ddiv(jmath.log(2.0**k), jmath.log(2.0))) == k
        assert jmath.ceil(jmath.ddiv(jmath.log(4.0**k), jmath.log(4.0))) == k
    # ...and one above at 2^29 ticks, where the quotient of the two rounded logarithms lands
    # a unit in the last place over 29 (IEEE 754, so in Java too).
    assert jmath.ceil(jmath.ddiv(jmath.log(2.0**29), jmath.log(2.0))) == 30


def test_gt_utility_helpers() -> None:
    assert jmath.pow_int(3.0, 5) == 243.0
    assert jmath.pow_int(4.0, -1) == 0.25
    assert jmath.pow_int(2.0, 1024) == math.inf
    assert jmath.pow_int(0.0, -1) == math.inf  # 1.0 / 0.0
    assert [jmath.log4(a) for a in (-5, 1, 4, 15, 16)] == [0, 0, 1, 1, 2]
    assert [jmath.log4ceil(a) for a in (1, 4, 5, 16, 17)] == [0, 1, 2, 2, 3]
    assert jmath.safe_int(2**40, 0) == INT_MAX
    assert jmath.safe_int(2**40, 7) == INT_MAX - 7
    assert (
        jmath.safe_int(-(2**31) - 1, 0) == INT_MAX
    )  # (int) of a long keeps the low bits
    assert jmath.clamp(5, 0, 3) == 3
    assert jmath.clamp(-5, 0, 3) == 0
    assert jmath.clamp(5, 0, -1) == -1  # the upper bound wins
    assert jmath.add_safe(LONG_MAX, 1) == LONG_MAX
    assert jmath.add_safe(LONG_MIN, -1) == LONG_MIN
    assert jmath.add_safe(5, -7) == -2
    assert jmath.mul_safe(2**62, 4) == LONG_MAX
    assert jmath.mul_safe(-(2**62), 4) == LONG_MIN
    assert jmath.mul_safe(-(2**62), -4) == LONG_MAX
    assert jmath.mul_safe(6, 7) == 42


# --- tiers: GTValues, GTUtility.getTier, HeatingCoilLevel, the EBF's heat ---


def test_tier_tables() -> None:
    assert len(V) == len(VP) == len(VN) == 16
    assert V[0] == 8 and V[1] == 32 and V[14] == 2**31 - 8  # Integer.MAX_VALUE - 7
    assert all(V[t] == 8 * 4**t for t in range(14))
    assert VP[0] == 7 and VP[1] == 30 and VP[5] == 7680
    assert VN[1] == "LV" and VN[9] == "UHV" and VN[14] == "MAX"


def test_tier_of_a_voltage() -> None:
    assert [get_tier(v) for v in (0, 8, 9, 32, 33, 512, 513)] == [0, 0, 1, 1, 2, 3, 4]
    assert get_tier(V[14]) == 14
    assert get_tier(V[14] + 1) == 15
    assert get_tier(2**40) == 15
    assert get_tier_extended(2**40) == 19  # 8 * 4^18 < 2^40 <= 8 * 4^19: no cap at 15
    assert get_tier_extended(V[13]) == 13
    assert amperage_for_tier(V[3] * 2 + 1, 3) == 3
    assert amperage_for_tier(V[3] * 2, 3) == 2


def test_heating_coils() -> None:
    assert len(COILS) == 14
    assert coil_heat(0) == 1801  # Cupronickel
    assert coil_heat(2) == 3601  # Nichrome
    assert coil_heat(13) == 13501  # Eternal
    for bad in (-1, 14):
        with pytest.raises(ValueError, match="no heating coil"):
            coil_heat(bad)


def test_ebf_heat_gains_100_k_per_voltage_tier_above_mv() -> None:
    assert ebf_heat(0, V[2]) == 1801  # one MV hatch: no bonus
    assert ebf_heat(0, V[3]) == 1901  # an HV hatch
    assert ebf_heat(0, 2 * V[2]) == 1901  # two MV hatches: their summed voltage is HV
    assert ebf_heat(0, V[1]) == 1701  # below MV it loses heat
    assert ebf_heat(5, V[8]) == coil_heat(5) + 600


# --- overclock: what GT's tests and the oracle don't say ---


def test_laser_overclocks_refuse_what_gt_never_returns_from() -> None:
    oc = Overclock(recipe_eut=0, voltage=V[5], duration=100, laser_oc=True)
    with pytest.raises(
        ValueError, match="laser overclocks need a recipe power above 0"
    ):
        calculate(oc)
    with pytest.raises(
        ValueError, match="laser overclocks need a recipe power above 0"
    ):
        multiplier_under_one_tick(oc)


def test_setters_refuse_what_gt_refuses() -> None:
    with pytest.raises(ValueError, match="EUt increase"):
        Overclock(eut_increase_per_oc=0.0)
    with pytest.raises(ValueError, match="Duration decrease"):
        Overclock(duration_decrease_per_oc=-1.0)


def test_no_overclock_and_perfect_constructors() -> None:
    oc = Overclock.no_oc(480, 77)
    assert (oc.recipe_eut, oc.voltage, oc.duration, oc.no_overclock) == (
        480,
        480,
        77,
        True,
    )
    assert calculate(oc).eut == 480 and calculate(oc).duration == 77
    assert Overclock().perfect().duration_decrease_per_oc == 4.0


# --- parallel: inputs (GTRecipe.maxParallelCalculatedByInputs) ---


def test_inputs_that_never_limit() -> None:
    assert parallel_from_inputs(8, None) == 8.0
    assert parallel_from_inputs(8, ()) == 8.0


def test_item_inputs_limit_the_parallel() -> None:
    assert parallel_from_inputs(8, [Supply(2, 16)]) == 8.0
    assert (
        parallel_from_inputs(8, [Supply(2, 7)]) == 3.5
    )  # there, but not 4 crafts' worth
    assert parallel_from_inputs(8, [Supply(2, 7), Supply(1, 2)]) == 2.0
    assert parallel_from_inputs(8, [Supply(1, 0)]) == 0.0  # missing


def test_an_unconsumed_input_never_limits() -> None:
    # A circuit or catalyst: per_craft 0. GT counts no amount of it here.
    assert parallel_from_inputs(8, [Supply(0, 1)]) == 8.0
    assert parallel_from_inputs(8, [Supply(0, 0)]) == 8.0
    assert parallel_from_inputs(8, [Supply(0, 0, fluid=True)]) == 8.0


def test_fluids_are_counted_before_items() -> None:
    fluid = Supply(1000, 2500, fluid=True)
    assert parallel_from_inputs(8, [fluid]) == 2.5
    # The item is then asked for 2.5 crafts and holds 2.
    assert parallel_from_inputs(8, [Supply(1, 2), fluid]) == 2.0
    assert parallel_from_inputs(8, [Supply(1000, 0, fluid=True)]) == 0.0
    assert parallel_from_inputs(8, [fluid, Supply(1, 0)]) == 0.0


def _helper(**changes: object) -> Parallel:
    # 30 EU/t, 200 ticks, power for 16 at once; GT's default calculator.
    base: dict[str, object] = {
        "recipe_eut": 30,
        "duration": 200,
        "available_eut": 30 * 16,
        "max_parallel": 16,
    }
    return Parallel(**{**base, **changes})  # type: ignore[arg-type]


def test_the_parallel_is_the_lowest_of_power_inputs_and_max() -> None:
    assert determine(_helper()).parallel == 16
    assert determine(_helper(max_parallel=4)).parallel == 4
    assert determine(_helper(available_eut=30 * 5 + 29)).parallel == 5
    result = determine(_helper(inputs=(Supply(2, 7), Supply(1000, 9000, fluid=True))))
    assert result.check is Check.SUCCESS
    assert result.parallel == 3
    # The three overclock once as a group: 90 EU/t fits four times into the 480.
    assert result.overclock is not None
    assert (result.overclock.eut, result.overclock.duration) == (360, 100)


def test_nothing_is_checked_without_a_parallel() -> None:
    assert determine(_helper(max_parallel=0)) == determine(_helper(max_parallel=-2))
    assert determine(_helper(max_parallel=0)).check is Check.NONE


def test_a_missing_input_is_gts_internal_error() -> None:
    assert determine(_helper(inputs=(Supply(1, 0),))).check is Check.INTERNAL_ERROR
    # Inputs for less than one craft, too.
    assert determine(_helper(inputs=(Supply(4, 3),))).check is Check.INTERNAL_ERROR


# --- parallel: outputs and void protection (VoidProtectionHelper's worst case) ---


def test_void_protection_reserves_the_worst_case() -> None:
    assert worst_case_output(Output(2)) == 2
    assert worst_case_output(Output(2, chance=2500)) == 2  # a chance counts as certain
    assert worst_case_output(Output(2, chance=15000)) == 4  # 150%: up to two outputs
    assert (
        worst_case_output(Output(2, chance=6000), 2.0) == 4
    )  # 120% with the multiplier
    assert worst_case_output(Output(0)) == 0


def test_void_protection_limits_the_parallel_to_the_output_room() -> None:
    room = (Output(1, space=5),)
    result = determine(_helper(outputs=room, protect_items=True))
    assert (result.check, result.parallel, result.max_parallel) == (Check.SUCCESS, 5, 5)
    # Off: the room isn't looked at.
    assert determine(_helper(outputs=room)).parallel == 16
    # No limit known (an ME output takes everything).
    assert determine(_helper(outputs=(Output(1),), protect_items=True)).parallel == 16
    chanced = (Output(2, chance=2500, space=5),)
    assert determine(_helper(outputs=chanced, protect_items=True)).parallel == 2
    fluid = (Output(144, fluid=True, space=1000),)
    assert determine(_helper(outputs=fluid, protect_fluids=True)).parallel == 6
    assert determine(_helper(outputs=fluid, protect_items=True)).parallel == 16


def test_full_outputs_refuse_the_recipe() -> None:
    item, fluid = Output(1, space=0), Output(144, fluid=True, space=100)
    assert (
        determine(_helper(outputs=(item,), protect_items=True)).check
        is Check.ITEM_OUTPUT_FULL
    )
    assert (
        determine(_helper(outputs=(fluid,), protect_fluids=True)).check
        is Check.FLUID_OUTPUT_FULL
    )
    both = _helper(outputs=(fluid, item), protect_items=True, protect_fluids=True)
    assert determine(both).check is Check.ITEM_OUTPUT_FULL  # items are checked first


def test_outputs_past_the_machines_slot_limit_are_dropped_first() -> None:
    outputs = (Output(1, space=64), Output(1, space=0))
    full = _helper(outputs=outputs, protect_items=True)
    assert determine(full).check is Check.ITEM_OUTPUT_FULL
    limited = _helper(outputs=outputs, protect_items=True, item_output_limit=1)
    assert determine(limited).parallel == 16
    fluids = (Output(1, fluid=True, space=64), Output(1, fluid=True, space=0))
    limited = _helper(outputs=fluids, protect_fluids=True, fluid_output_limit=1)
    assert determine(limited).parallel == 16


def test_batch_parallels_respect_the_output_room() -> None:
    # 20 ticks, batch 128: up to 128 / 20 = 6.4 times the run, were there room.
    free = determine(_helper(duration=20, batch_size=128))
    assert (free.parallel, free.duration_multiplier) == (102, 6.375)
    tight = determine(
        _helper(
            duration=20,
            batch_size=128,
            outputs=(Output(1, space=40),),
            protect_items=True,
        )
    )
    assert (tight.parallel, tight.duration_multiplier) == (40, 2.5)


def test_expected_output_of_chanced_outputs() -> None:
    assert expected_output(Output(3), 8) == 24.0
    assert expected_output(Output(3, chance=2500), 8) == 6.0
    assert expected_output(Output(3, chance=12500), 8) == 30.0  # one each, plus 25%
    assert expected_output(Output(3, chance=2500), 8, 1.5) == 9.0  # (int) (2500 * 1.5)
    assert expected_output(Output(3, chance=0), 8) == 0.0
    with pytest.raises(ValueError, match="chance can't be negative"):
        expected_output(Output(3, chance=-1), 8)


# --- processing: energy hatches (MTEMultiBlockBase.setProcessingLogicPower) ---


def test_energy_hatches() -> None:
    assert (EnergyHatch(3).voltage, EnergyHatch(3).amps, EnergyHatch(3).exotic) == (
        512,
        2,
        False,
    )
    laser = EnergyHatch.multi_amp(9, 256)
    assert (laser.voltage, laser.amps, laser.exotic) == (V[9], 256, True)


def test_one_plain_hatch_runs_at_one_amp() -> None:
    assert multiblock_power([EnergyHatch(3)]) == Power(voltage=512, amperage=1)
    assert multiblock_power([EnergyHatch(3)], extended=True) == Power(512, 1)


def test_more_hatches_give_their_working_amps_at_the_average_voltage() -> None:
    assert multiblock_power([EnergyHatch(3)] * 2) == Power(512, 4)
    mixed = [EnergyHatch(3), EnergyHatch(4)]
    assert multiblock_power(mixed) == Power((512 + 2048) // 2, 4)
    assert max_input_voltage(mixed) == 2560
    assert average_input_voltage(mixed) == 1280
    assert max_input_amps(mixed) == 4
    assert max_input_eu(mixed) == 512 * 2 + 2048 * 2


def test_exotic_hatches_count_for_extended_power_multiblocks_only() -> None:
    exotic = [EnergyHatch.multi_amp(5, 16)]
    assert multiblock_power(exotic, extended=True) == Power(V[5], 16)
    # A plain multiblock's accessors don't see it.
    assert multiblock_power(exotic) == Power(0, 0)
    both = [EnergyHatch(5), EnergyHatch.multi_amp(5, 16)]
    assert multiblock_power(both, extended=True) == Power(V[5], 18)
    assert max_input_voltage(both) == V[5]
    assert max_input_voltage(both, extended=True) == 2 * V[5]
    assert average_input_voltage([]) == 0


def test_total_eu_power_is_one_amp_of_everything() -> None:
    power = total_eu_power([EnergyHatch.multi_amp(8, 64), EnergyHatch(8)])
    assert power == Power(V[8] * 66, 1, True, UNLIMITED_TIER_SKIPS)
    assert (
        max_input_eu([EnergyHatch.multi_amp(14, 2**40)] * 4, extended=True) == LONG_MAX
    )


def test_power_defaults_are_a_multiblocks() -> None:
    assert Power(512, 1).amperage_oc is True
    assert Power(512, 1).max_tier_skips == 1


# --- processing: a run (ProcessingLogic.process, MTEMultiBlockBase.postCheckRecipe) ---

EBF = Logic(
    power=Power(V[5], 1),
    heat_oc=True,
    heat_discount=True,
    machine_heat=9000,
    check_heat=True,
)


def test_an_ebf_run_is_gts_ebf_overclock() -> None:
    # GT's fullPerfectOverclockEBF_Test, through the processing logic.
    run = process(Recipe(eut=VP[1], duration=1024, heat=1800), EBF)
    assert run == Run(Check.SUCCESS, eut=5096, duration=4, parallel=1, overclocks=4)
    assert run.ok
    assert run.crafts_per_second == 5.0


def test_a_recipe_hotter_than_the_machine_is_refused() -> None:
    run = process(Recipe(eut=VP[1], duration=1024, heat=9001), EBF)
    assert (run.check, run.required, run.ok) == (Check.INSUFFICIENT_HEAT, 9001, False)
    assert run.crafts_per_second == 0.0
    # Without the check (a machine that doesn't compare heat) it runs.
    unchecked = Logic(power=Power(V[5], 1), machine_heat=9000)
    assert process(Recipe(eut=VP[1], duration=1024, heat=9001), unchecked).ok


def test_the_logics_own_recipe_heat_replaces_the_recipes() -> None:
    cold = Logic(
        power=Power(V[5], 1),
        heat_oc=True,
        heat_discount=True,
        machine_heat=9000,
        recipe_heat=9000,
    )
    run = process(Recipe(eut=VP[1], duration=1024, heat=1800), cold)
    assert (run.eut, run.duration) == (VP[5], 64)  # no heat bonus left


def test_a_plain_multiblock_cant_draw_more_than_an_int() -> None:
    recipe = Recipe(eut=VP[13], duration=100)
    power = Power(V[13], 1024)
    extended = process(recipe, Logic(power=power, max_parallel=64, int_power=False))
    assert extended.ok and extended.eut > INT_MAX
    plain = process(recipe, Logic(power=power, max_parallel=64))
    assert (plain.check, plain.parallel) == (Check.POWER_OVERFLOW, extended.parallel)


def test_a_run_passes_the_helpers_refusals_on() -> None:
    logic = Logic(power=Power(V[3], 1))
    short = process(Recipe(eut=V[3] + 1, duration=100), logic)
    assert (short.check, short.required) == (Check.INSUFFICIENT_POWER, V[3] + 1)
    amps = Logic(power=Power(V[3], 64))
    high = process(Recipe(eut=V[4] + 1, duration=100), amps)
    assert (high.check, high.required) == (Check.INSUFFICIENT_VOLTAGE, V[4] + 1)
    assert process(Recipe(eut=V[4], duration=100), amps).ok  # one tier skipped
    none = process(Recipe(30, 100), logic, inputs=(Supply(1, 0),))
    assert none.check is Check.INTERNAL_ERROR
    full = process(
        Recipe(30, 100),
        Logic(power=Power(V[3], 1), protect_items=True),
        outputs=(Output(1, space=0),),
    )
    assert full.check is Check.ITEM_OUTPUT_FULL


def test_batch_mode_stretches_the_run_not_the_power() -> None:
    logic = Logic(power=Power(V[1], 1), max_parallel=1)
    plain = process(Recipe(30, 20), logic)
    assert (plain.eut, plain.duration, plain.parallel) == (30, 20, 1)
    batch = process(Recipe(30, 20), Logic(power=Power(V[1], 1), batch_size=128))
    assert (batch.eut, batch.duration, batch.parallel) == (30, 120, 6)
    assert batch.crafts_per_second == plain.crafts_per_second


# --- processing: single blocks (MTEBasicMachine) ---


def test_a_single_block_overclocks_by_voltage_tier() -> None:
    recipe = Recipe(eut=30, duration=200)
    assert basic_machine(recipe, 1) == Run(Check.SUCCESS, 30, 200, 1, 0)
    assert basic_machine(recipe, 2) == Run(Check.SUCCESS, 120, 100, 1, 1)
    assert basic_machine(recipe, 3) == Run(Check.SUCCESS, 480, 50, 1, 2)
    # Never below one tick, and no parallels for the overclocks past it.
    assert basic_machine(Recipe(30, 2), 6) == Run(Check.SUCCESS, 30720, 1, 1, 5)


def test_a_single_block_finds_no_recipe_above_its_voltage() -> None:
    run = basic_machine(Recipe(eut=33, duration=100), 1)
    assert (run.check, run.required, run.ok) == (Check.NO_RECIPE, 33, False)
    assert basic_machine(Recipe(eut=32, duration=100), 1).ok
    # A recipe map with more amps finds it; the machine then runs it without overclocks.
    wide = basic_machine(Recipe(eut=33, duration=100), 1, map_amperage=2)
    assert (wide.eut, wide.duration, wide.overclocks) == (33, 100, 0)


def test_a_single_blocks_amperage_is_part_of_its_voltage() -> None:
    # V[tier] x amperage, as one amp: two LV amps overclock no 30 EU/t recipe...
    assert basic_machine(Recipe(30, 200), 1, amperage=2).overclocks == 0
    # ...and four are MV's voltage.
    assert basic_machine(Recipe(30, 200), 1, amperage=4) == Run(
        Check.SUCCESS, 120, 100, 1, 1
    )
