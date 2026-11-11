"""Invariants of the gtmath port (WO-3.1): the EU budget and monotonicity in tier.

Seeded random samples, so every run checks the same cases. They hold for GT's own rules, so a
change under ``planner/gtmath`` that breaks one has changed what GT does:

- **EU budget.** A machine that can run a recipe never draws more than its energy input offers
  (voltage x amperage), however it overclocks, parallelises or batches; the parallel helper
  never starts more recipes than the EU/t pays for or the inputs hold.
- **Monotonic in tier.** More voltage never makes a recipe slower or cheaper: ticks don't rise,
  EU/t and overclocks don't fall, a multiblock's recipes per second don't fall, and a machine
  that runs a recipe at one tier runs it at every higher one. The same for hotter coils.
- Perfect overclocks are never slower than standard ones, and batch mode doesn't slow a machine
  (to the precision of GT's ``float`` batch factor).

GT compares powers in hundredths of an EU/t (``getTiersAbove``), so the budget is exact for a
recipe power that is a whole number and within half a hundredth of a 4x step otherwise; the
last test pins that case.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace

from gregtec.planner.gtmath.jmath import to_long
from gregtec.planner.gtmath.overclock import (
    Overclock,
    calculate,
    heat_discount_multiplier,
)
from gregtec.planner.gtmath.parallel import Check, Parallel, Supply, determine
from gregtec.planner.gtmath.processing import (
    EnergyHatch,
    Logic,
    Power,
    Recipe,
    basic_machine,
    multiblock_power,
    process,
)
from gregtec.planner.gtmath.tiers import VP, V, ebf_heat

SEED = 31
CASES = 1500
EU_MODIFIERS = [1.0, 0.9, 0.8, 0.75, 0.5, 0.95, 0.85, 0.6, 1.1, 0.7]
SPEEDS = [1.0, 0.5, 0.8, 1 / 2.25, 1 / 3.0]
DURATIONS = [1, 2, 3, 5, 10, 20, 64, 100, 128, 200, 1000]
PARALLELS = [1, 2, 4, 8, 16, 64, 256]


def _recipe(rng: random.Random, top_tier: int = 10) -> tuple[Recipe, int]:
    """A recipe of a random tier up to ``top_tier`` (a practical, full or odd voltage)."""
    tier = rng.randint(0, top_tier)
    eut = rng.choice([VP[tier], V[tier], rng.randint(max(1, V[tier] // 4), V[tier])])
    duration = rng.choice([*DURATIONS, rng.randint(1, 4000)])
    return Recipe(eut=eut, duration=duration, heat=rng.randint(0, 6000)), tier


def _budget(machine_power: int, recipe_power: float) -> int:
    """The most a 4x overclock draws: the machine's power, plus GT's half a hundredth of a
    step when the recipe power isn't a whole number."""
    if recipe_power == int(recipe_power):
        return machine_power
    slack = 0.5 / (100 * recipe_power - 0.5)
    return math.ceil(machine_power * (1 + slack))


def test_overclocks_stay_within_the_machines_power() -> None:
    rng = random.Random(SEED)
    checked = 0
    for _ in range(CASES * 4):
        tier = rng.randint(1, 13)
        amps = rng.choice([1, 2, 4, 6, 16, 64, 256, 1024, rng.randint(1, 100)])
        parallel = rng.choice([*PARALLELS, rng.randint(1, 300)])
        heat = rng.random() < 0.3
        oc = Overclock(
            recipe_eut=rng.choice(
                [
                    VP[rng.randint(0, tier)],
                    V[rng.randint(0, tier)],
                    rng.randint(1, V[tier]),
                ]
            ),
            duration=rng.randint(1, 5000),
            voltage=V[tier],
            amperage=amps,
            parallel=parallel,
            eut_modifier=rng.choice(EU_MODIFIERS),
            duration_decrease_per_oc=rng.choice([2.0, 4.0]),
            amperage_oc=rng.random() < 0.7,
            heat_oc=heat,
            heat_discount=heat,
            recipe_heat=rng.randint(0, 9000) if heat else 0,
            machine_heat=rng.randint(0, 14000) if heat else 0,
        )
        recipe_power = (
            oc.recipe_eut * parallel * oc.eut_modifier * heat_discount_multiplier(oc)
        )
        machine_power = V[tier] * (amps if oc.amperage_oc else min(amps, parallel))
        if recipe_power > machine_power:
            continue  # GT runs it without overclocks, at the recipe's own power
        checked += 1
        assert calculate(oc).eut <= _budget(machine_power, recipe_power), oc
    assert checked > CASES


def test_laser_overclocks_stay_within_the_machines_power() -> None:
    rng = random.Random(SEED + 1)
    for _ in range(CASES):
        tier = rng.randint(4, 13)
        amps = rng.choice([1, 16, 64, 256, 4096, 2**20])
        oc = Overclock(
            recipe_eut=rng.choice([VP[rng.randint(0, tier)], rng.randint(1, V[tier])]),
            duration=rng.randint(1, 5000),
            voltage=V[tier] * amps,
            eut_modifier=rng.choice(EU_MODIFIERS),
            laser_oc=True,
            max_regular_overclocks=rng.choice([0, 2, 4, 8, 2**31 - 1]),
        )
        if oc.recipe_eut * oc.eut_modifier > oc.voltage:
            continue
        result = calculate(oc)
        assert result.eut <= oc.voltage, oc
        assert result.duration >= 1


def _logic(rng: random.Random, power: Power) -> Logic:
    heat = rng.random() < 0.3
    return Logic(
        power=power,
        max_parallel=rng.choice([*PARALLELS, rng.randint(1, 512)]),
        eu_modifier=rng.choice(EU_MODIFIERS),
        speed_boost=rng.choice(SPEEDS),
        batch_size=rng.choice([1, 1, 128]),
        oc_time_reduction=rng.choice([2.0, 4.0]),
        heat_oc=heat,
        heat_discount=heat,
        machine_heat=rng.randint(0, 9000) if heat else 0,
        int_power=False,
    )


def test_a_run_never_draws_more_than_the_energy_input() -> None:
    rng = random.Random(SEED + 2)
    ran = 0
    for _ in range(CASES):
        recipe, tier = _recipe(rng)
        hatches = [EnergyHatch(rng.randint(tier, 13))] * rng.choice([1, 2, 3, 4])
        power = multiblock_power(hatches)
        logic = _logic(rng, power)
        inputs = (
            Supply(rng.randint(1, 64), rng.randint(0, 5000)),
            Supply(rng.choice([0, 144, 1000]), rng.randint(0, 64000), fluid=True),
        )
        run = process(recipe, logic, inputs=inputs)
        if not run.ok:
            assert (run.eut, run.duration) == (0, 0)
            continue
        ran += 1
        available = power.voltage * power.amperage
        group = determine(
            Parallel(
                recipe_eut=recipe.eut,
                duration=recipe.duration,
                available_eut=available,
                max_parallel=logic.max_parallel,
                eut_modifier=logic.eu_modifier,
                batch_size=logic.batch_size,
                overclock=logic.overclock(recipe),
                inputs=inputs,
            )
        )
        assert group.overclock is not None
        discount = heat_discount_multiplier(logic.overclock(recipe))
        each = to_long(math.ceil(recipe.eut * logic.eu_modifier * discount))
        # The parallels the EU/t pays for (batch parallels stretch the run instead).
        assert group.overclock.parallel * each <= available
        recipe_power = (
            recipe.eut * group.overclock.parallel * logic.eu_modifier * discount
        )
        assert run.eut <= _budget(available, recipe_power), (recipe, logic)
        assert run.duration >= 1
        # Nothing is taken that the machine doesn't hold.
        for supply in inputs:
            assert supply.per_craft * run.parallel <= supply.available
    assert ran > CASES // 4


def test_a_whole_number_recipe_power_is_never_overdrawn() -> None:
    rng = random.Random(SEED + 3)
    for _ in range(CASES):
        recipe, tier = _recipe(rng)
        power = multiblock_power(
            [EnergyHatch(rng.randint(tier, 13))] * rng.randint(1, 4)
        )
        logic = replace(
            _logic(rng, power), eu_modifier=1.0, heat_oc=False, heat_discount=False
        )
        run = process(recipe, logic)
        assert run.ok, (recipe, logic)
        assert run.eut <= power.voltage * power.amperage


def test_the_calculator_is_monotonic_in_voltage_tier() -> None:
    rng = random.Random(SEED + 4)
    for _ in range(CASES):
        recipe, tier = _recipe(rng)
        base = Overclock(
            recipe_eut=recipe.eut,
            duration=recipe.duration,
            amperage=rng.choice([1, 2, 4, 16]),
            parallel=rng.choice([1, 1, 4, 16]),
            amperage_oc=rng.random() < 0.5,
            duration_decrease_per_oc=rng.choice([2.0, 4.0]),
            duration_modifier=rng.choice(SPEEDS),
        )
        previous = None
        for t in range(tier, 15):
            result = calculate(replace(base, voltage=V[t]))
            if previous is not None:
                assert result.duration <= previous.duration, (base, t)
                assert result.eut >= previous.eut, (base, t)
                assert result.overclocks >= previous.overclocks, (base, t)
            previous = result


def test_single_blocks_are_monotonic_in_tier() -> None:
    rng = random.Random(SEED + 5)
    for _ in range(CASES):
        recipe, tier = _recipe(rng)
        previous = None
        for t in range(15):
            run = basic_machine(recipe, t)
            assert run.ok == (recipe.eut <= V[t])
            if not run.ok:
                assert previous is None  # once it runs, it runs at every higher tier
                continue
            assert run.eut <= V[t]
            assert run.parallel == 1 and run.duration >= 1
            if previous is not None:
                assert run.duration <= previous.duration
                assert run.eut >= previous.eut
                assert run.overclocks >= previous.overclocks
            previous = run
        assert previous is not None and tier <= 14


def test_multiblock_throughput_is_monotonic_in_hatch_tier() -> None:
    rng = random.Random(SEED + 6)
    for _ in range(CASES // 3):
        recipe, tier = _recipe(rng)
        hatches = rng.randint(1, 4)
        template = _logic(rng, Power(0, 0))
        previous = None
        for t in range(max(tier - 1, 0), 15):
            power = multiblock_power([EnergyHatch(t)] * hatches)
            run = process(recipe, replace(template, power=power))
            if previous is not None and previous.ok:
                assert run.ok, (recipe, template, t)
                assert run.crafts_per_second >= previous.crafts_per_second, (
                    recipe,
                    template,
                    t,
                )
            previous = run
        assert previous is not None and previous.ok


def test_hotter_coils_never_slow_an_ebf() -> None:
    rng = random.Random(SEED + 7)
    for _ in range(CASES // 3):
        recipe, tier = _recipe(rng, top_tier=6)
        hatches = [EnergyHatch(rng.randint(max(tier, 1), 8))] * rng.randint(1, 2)
        power = multiblock_power(hatches)
        voltage = sum(h.voltage for h in hatches)
        previous = None
        for coil in range(14):
            logic = Logic(
                power=power,
                heat_oc=True,
                heat_discount=True,
                check_heat=True,
                machine_heat=ebf_heat(coil, voltage),
            )
            run = process(recipe, logic)
            assert run.ok == (recipe.heat <= logic.machine_heat)
            if not run.ok:
                assert run.check is Check.INSUFFICIENT_HEAT
                assert previous is None
                continue
            discount = heat_discount_multiplier(logic.overclock(recipe))
            available = power.voltage * power.amperage
            assert run.eut <= _budget(available, recipe.eut * discount)
            if previous is not None:
                assert run.crafts_per_second >= previous.crafts_per_second
            previous = run
        assert previous is not None  # the hottest coil runs every recipe sampled


def test_perfect_overclocks_are_never_slower() -> None:
    rng = random.Random(SEED + 8)
    for _ in range(CASES):
        recipe, tier = _recipe(rng)
        oc = Overclock(
            recipe_eut=recipe.eut,
            duration=recipe.duration,
            voltage=V[rng.randint(tier, 14)],
            amperage=rng.choice([1, 4, 16]),
            amperage_oc=rng.random() < 0.5,
        )
        standard, perfect = calculate(oc), calculate(oc.perfect())
        assert perfect.eut == standard.eut
        assert perfect.overclocks == standard.overclocks
        assert perfect.duration <= standard.duration


def test_batch_mode_never_slows_a_machine() -> None:
    rng = random.Random(SEED + 9)
    for _ in range(CASES):
        recipe, tier = _recipe(rng)
        power = multiblock_power(
            [EnergyHatch(rng.randint(tier, 13))] * rng.randint(1, 4)
        )
        logic = replace(_logic(rng, power), batch_size=1)
        plain = process(recipe, logic)
        batch = process(recipe, replace(logic, batch_size=128))
        assert batch.check is plain.check
        if not plain.ok:
            continue  # a 110% EU modifier on a recipe at the hatch's full voltage
        # The extra recipes stretch the run, not the power.
        assert batch.eut == plain.eut
        assert batch.parallel >= plain.parallel
        # To one part in a million: GT's batch factor is a float, and parallels stop at an int.
        assert batch.crafts_per_second >= plain.crafts_per_second * (1 - 1e-6)
        if batch.parallel > plain.parallel:
            assert plain.duration < 128


def test_gt_counts_a_power_half_a_hundredth_under_a_step() -> None:
    """The one way past the budget, and GT's own (oracle vector
    ``edge:hundredths-1000-8.333466666666666``): 250.004 EU/t counts as 250.00, a full 4x step
    under 1000, so the overclock draws ceil(1000.016) EU/t."""
    oc = Overclock(
        recipe_eut=30,
        duration=100,
        voltage=1000,
        amperage_oc=True,
        eut_modifier=250.004 / 30,
    )
    result = calculate(oc)
    assert (result.eut, result.overclocks) == (1001, 1)
    assert result.eut <= _budget(1000, 30 * oc.eut_modifier)
    # Half a hundredth more and it is no longer a step.
    over = replace(oc, eut_modifier=250.006 / 30)
    assert (calculate(over).eut, calculate(over).overclocks) == (251, 0)
