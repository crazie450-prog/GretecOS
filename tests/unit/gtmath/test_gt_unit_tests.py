"""GT's own unit tests of the ported math, as test vectors (WO-3.1, D173, D174).

Ported from GT5-Unofficial 5.09.54.265 (commit bb00f3f1023e). GT is LGPL-3.0, so only the facts
are here: each test's inputs and expected outputs, as data; no GT code.

- ``src/test/java/gregtech/overclock/GT_OverclockCalculator_UnitTest.java``: all 50 tests and
  the 17 rows of its 5 parameterized tests, 67 cases, each named after its GT test.
- ``src/test/java/gregtech/api/util/GTUtilityTest.java``: the tests of the helpers the port uses
  (``powInt``, ``log4``, ``log4ceil``, ``getTier``, ``ceilDiv``, ``getAmperageForTier``).
- ``src/test/java/gregtech/api/metatileentity/implementations/MTEMultiBlockBaseTest.java``: the
  energy hatch rule's 7 rows.

Expected values are GT's expected expressions evaluated by GT's own code, written out; the
comment next to each is GT's expression. CLAUDE.md: any change under ``planner/gtmath`` must keep
these green. A GT pin bump re-vendors them from the new tag (docs/runbooks/pack_update.md).
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from gregtec.planner.gtmath.jmath import (
    LONG_MAX,
    ceil_div,
    log4,
    log4ceil,
    pow_int,
    safe_int,
    to_float,
    to_int,
    to_long,
)
from gregtec.planner.gtmath.overclock import (
    Overclock,
    calculate,
    multiplier_under_one_tick,
)
from gregtec.planner.gtmath.processing import (
    EnergyHatch,
    check_exotic_and_normal_hatches,
)
from gregtec.planner.gtmath.tiers import VP, V, amperage_for_tier, get_tier

UV = V[8]  # TierEU.UV
UIV = 11  # VoltageIndex.UIV
PERFECT = 4.0  # enablePerfectOC()

# GT_OverclockCalculator_UnitTest: (GT test, the calculator as the test sets it up, EU/t,
# ticks). None: the test doesn't check it.
OC_CASES: list[tuple[str, Overclock, int, int | None]] = [
    (
        "fullPerfectOverclockEBF_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[5],
            duration=1024,
            heat_oc=True,
            heat_discount=True,
            recipe_heat=1800,
            machine_heat=1800 * 5,
        ),
        5096,  # ceil(VP[5] * 0.95^((1800 * 4) / 900))
        4,  # 1024 / 4^4
    ),
    (
        "imperfectOverclockEBFWithOneHeatDiscount_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[5],
            duration=1024,
            heat_oc=True,
            heat_discount=True,
            recipe_heat=1800,
            machine_heat=2700,
        ),
        7296,  # ceil(VP[5] * 0.95^1)
        64,  # 1024 / 2^4
    ),
    (
        "imperfectOverclockEBFWithoutHeatDiscounts_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[5],
            duration=1024,
            heat_oc=True,
            heat_discount=True,
            recipe_heat=1800,
            machine_heat=1800,
        ),
        VP[5],
        64,  # 1024 / 2^4
    ),
    (
        "perfectAndImperfectOverclockEBFWithTwoHeatDiscounts_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[5],
            duration=1024,
            heat_oc=True,
            heat_discount=True,
            recipe_heat=1800,
            machine_heat=3600,
        ),
        6932,  # ceil(VP[5] * 0.95^2)
        32,  # 1024 / (4.0 * 2^3)
    ),
    (
        "generalImperfectOverclock_Test",
        Overclock(recipe_eut=VP[1], voltage=V[6], duration=1024),
        VP[6],
        32,  # 1024 / 2^5
    ),
    (
        "generalPerfectOverclock_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[6],
            duration=2048,
            duration_decrease_per_oc=PERFECT,
        ),
        VP[6],
        2,  # 2048 / 4^5
    ),
    (
        "weirdHeatEBF_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[6],
            duration=2048,
            heat_oc=True,
            heat_discount=True,
            recipe_heat=486,
            machine_heat=3900,
        ),
        26339,  # ceil(VP[6] * 0.95^3)
        32,  # 2048 / (4.0 * 2^4)
    ),
    (
        "doubleEnergyHatchOC_Test",
        Overclock(
            recipe_eut=VP[1], voltage=V[6], amperage=4, duration=1024, amperage_oc=True
        ),
        VP[7],
        16,  # 1024 / 2^6
    ),
    (
        "doubleEnergyHatchOCForULV_Test",
        Overclock(
            recipe_eut=VP[0], voltage=V[6], amperage=4, duration=1024, amperage_oc=True
        ),
        28672,  # VP[0] * 4^6
        16,  # 1024 / 2^6
    ),
    (
        "multiAmpHatchOC_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[6],
            amperage=256,
            duration=1024,
            amperage_oc=True,
        ),
        VP[10],
        2,  # 1024 / 2^9
    ),
    (
        "weirdAmpHatchOC_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[6],
            amperage=320,
            duration=1024,
            amperage_oc=True,
        ),
        VP[10],
        2,  # 1024 / 2^9
    ),
    (
        "parallelImperfectOC_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[8],
            amperage=4,
            duration=1024,
            parallel=16,
            amperage_oc=True,
        ),
        VP[9],
        16,  # 1024 / 2^6
    ),
    (
        "parallelPerfectOC_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[8],
            amperage=4,
            duration=4096,
            parallel=16,
            amperage_oc=True,
            duration_decrease_per_oc=PERFECT,
        ),
        VP[9],
        1,  # 4096 / 4^6
    ),
    (
        "parallelMultiAmpOC_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[6],
            amperage=320,
            duration=1024,
            parallel=16,
            amperage_oc=True,
        ),
        VP[10],
        8,  # 1024 / 2^7
    ),
    (
        "weirdParallelPerfectOC_Test",
        Overclock(
            recipe_eut=3840,
            voltage=V[12],
            duration=3200,
            parallel=48,
            amperage=18,
            eut_modifier=0.8,
            amperage_oc=True,
            duration_decrease_per_oc=PERFECT,
        ),
        V[12] * 18,
        None,
    ),
    (
        "weirdParallelOC_Test",
        Overclock(recipe_eut=VP[1], voltage=V[8], duration=1024, parallel=8),
        VP[6] * 8,
        32,  # 1024 / 2^5
    ),
    (
        "weirdParallelOCV2_Test",
        Overclock(
            recipe_eut=2,
            voltage=V[8],
            duration=300,
            parallel=18,
            amperage=4,
            eut_modifier=0.9,
            amperage_oc=True,
        ),
        530842,
        2,  # (int) (300 / 2^7)
    ),
    (
        "weirdParallelAndAmpsOC_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[8],
            amperage=320,
            duration=1024,
            parallel=8,
            amperage_oc=True,
        ),
        VP[6] * 8 * 256,
        2,  # 1024 / 2^9
    ),
    (
        "weirdTimeOC_Test",
        Overclock(recipe_eut=VP[1], voltage=V[6], duration=77),
        VP[6],
        2,  # (int) (77 / 2^5)
    ),
    (
        "correctWhenOneTicking_Test",
        Overclock(recipe_eut=VP[1], voltage=V[6], duration=1),
        VP[1] * 4**5,
        1,
    ),
    (
        "imperfectOCWithEUtDiscount_Test",
        Overclock(recipe_eut=VP[1], voltage=V[6], eut_modifier=0.9, duration=1024),
        27648,  # (long) ceil(VP[1] * 0.9f) << 10
        32,  # 1024 / 2^5
    ),
    (
        "perfectOCWithEUtDiscount_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[6],
            eut_modifier=0.9,
            duration=1024,
            duration_decrease_per_oc=PERFECT,
        ),
        27648,  # (long) ceil(VP[1] * 0.9f) << 10
        1,  # 1024 / 4^5
    ),
    (
        "imperfectOCWithSpeedBoost_Test",
        Overclock(recipe_eut=VP[1], voltage=V[6], duration_modifier=0.9, duration=1024),
        VP[6],
        28,  # (int) (1024 * 0.9f / 2^5)
    ),
    (
        "perfectOCWithSpeedBoost_Test",
        Overclock(
            recipe_eut=VP[1],
            voltage=V[6],
            duration_modifier=0.9,
            duration=2048,
            duration_decrease_per_oc=PERFECT,
        ),
        VP[6],
        1,  # (int) ((2048 * 0.9f) / 4^5)
    ),
    (
        "perfectOC3TicksTo1Tick_Test",
        Overclock(
            recipe_eut=VP[4], voltage=V[5], duration=3, duration_decrease_per_oc=PERFECT
        ),
        VP[5],
        1,
    ),
    (
        "ulvRecipeWithDiscount_Test",
        Overclock(recipe_eut=VP[0], voltage=V[6], eut_modifier=0.9, duration=1024),
        6452,  # (long) ceil((VP[0] << 10) * 0.9f)
        32,  # 1024 / 2^5
    ),
    (
        "ulvRecipeWithDiscountWithParallel_Test",
        Overclock(
            recipe_eut=VP[0], voltage=V[5], eut_modifier=0.9, parallel=14, duration=1024
        ),
        5645,  # (long) ceil((VP[0] << 6) * 14 * 0.9f)
        128,  # 1024 / 2^3
    ),
    (
        "ulvRecipeCorrectWhenOneTicked_Test",
        Overclock(recipe_eut=VP[0], voltage=V[6], duration=1),
        VP[0] * 4**5,
        1,
    ),
    (
        "testNotPowerOverflowing_Test",
        Overclock(
            recipe_eut=16,
            parallel=64,
            amperage=64,
            amperage_oc=True,
            voltage=V[5],
            duration=30,
        ),
        16 * 4**7,
        1,
    ),
    (
        "testCorrectEUtWhenOverclockingUnderOneTick_Test",
        Overclock(
            recipe_eut=24,
            parallel=56,
            amperage=1,
            amperage_oc=True,
            duration_modifier=1 / 6.0,
            voltage=V[14] * 1_048_576,
            duration=56,
            current_parallel=6144,
        ),
        (24 * 56) * 4**20,
        None,
    ),
    (
        "testCorrectOverclockWhenCurrentParallelLessThanOriginalParallel",
        Overclock(
            recipe_eut=24,
            parallel=64,
            amperage=1,
            amperage_oc=True,
            voltage=V[6],
            duration=56,
            current_parallel=16,
        ),
        (24 * 16) * 4**3,
        7,  # 56 / 2^3
    ),
    (
        "stopsCorrectlyWhenOverclockingUnderOneTick",
        Overclock(
            recipe_eut=VP[2],
            voltage=V[9],
            parallel=4,
            duration=10,
            amperage_oc=False,
            amperage=16,
            current_parallel=16,
        ),
        (VP[2] * 4) * 4**7,
        None,
    ),
    (
        "ulvRecipeDoesntOverclockExtraWithAmperageWithoutAmperageOC",
        Overclock(
            recipe_eut=2,
            voltage=V[3],
            parallel=16,
            duration=20,
            amperage_oc=False,
            amperage=16,
        ),
        32 * 16,
        None,
    ),
    (
        "ulvRecipeDoesntOverclockExtraWithAmperageWithoutAmperageOCVersion2",
        Overclock(
            recipe_eut=2,
            voltage=V[3],
            parallel=64,
            duration=20,
            amperage_oc=False,
            amperage=16,
        ),
        32 * 64,
        None,
    ),
    (
        "ulvRecipeDoesntOverclockExtraWithAmperageWithoutAmperageOCVersion3",
        Overclock(
            recipe_eut=2,
            voltage=V[3],
            parallel=16,
            duration=20,
            amperage_oc=False,
            amperage=64,
        ),
        32 * 16,
        None,
    ),
    (
        "ulvRecipeDoesntOverclockExtraWithAmperageWithoutAmperageOCVersion4",
        Overclock(
            recipe_eut=8,
            voltage=V[3],
            parallel=16,
            duration=20,
            amperage_oc=False,
            amperage=16,
        ),
        (8 * 16) * 4**2,
        None,
    ),
    (
        "ulvRecipeDoesntOverclockExtraWithAmperageWithoutAmperageOCVersion5",
        Overclock(
            recipe_eut=8, voltage=V[3], parallel=16, duration=20, amperage_oc=False
        ),
        (8 * 16) * 4**1,
        None,
    ),
    (
        "ulvRecipeDoesntOverclockExtraWithAmperageWithoutAmperageOCVersion6",
        Overclock(
            recipe_eut=7,
            voltage=V[3],
            parallel=64,
            duration=160,
            amperage=64,
            amperage_oc=False,
        ),
        (7 * 64) * 4**2,
        None,
    ),
    (
        "ulvRecipeDoesntOverclockExtraWithAmperageWithoutAmperageOCVersion7",
        Overclock(
            recipe_eut=7,
            voltage=V[3],
            parallel=19,
            duration=160,
            amperage=19,
            amperage_oc=False,
        ),
        (7 * 19) * 4**2,
        None,
    ),
    (
        "ulvRecipeDoesntOverclockExtraWithAmperageWithoutAmperageOCVersion8",
        Overclock(
            recipe_eut=7,
            voltage=V[3],
            parallel=29,
            duration=160,
            amperage=25,
            amperage_oc=False,
        ),
        (7 * 29) * 4**2,
        None,
    ),
    (
        "ulvRecipeWorkCorrectlyWithCalculatingEutUnderOneTick",
        Overclock(
            recipe_eut=2,
            voltage=V[8],
            parallel=64,
            duration=300,
            amperage=64,
            amperage_oc=False,
        ),
        2 * 64 * 4**7,
        None,
    ),
    (
        "overclockWithAbnormalEnergyIncrease_Test",
        Overclock(
            recipe_eut=VP[1], voltage=V[6], duration=1024, eut_increase_per_oc=4.1
        ),
        34757,  # (long) ceil(VP[1] * 4.1^5)
        32,  # 1024 / 2^5
    ),
    (
        "overclockWithAbnormalDurationDecrease_Test",
        Overclock(
            recipe_eut=VP[1], voltage=V[6], duration=1024, duration_decrease_per_oc=2.1
        ),
        VP[6],
        25,  # (int) floor(1024 / 2.1^5)
    ),
    (
        "slightlyOverOneAmpRecipeWorksWithSingleEnergyHatch",
        Overclock(
            recipe_eut=614400, voltage=UV, duration=600, amperage=2, amperage_oc=False
        ),
        614400,
        600,
    ),
    (
        "laserOverclockAppliesEscalatingPenalty",
        Overclock(
            recipe_eut=30,
            voltage=10_000,
            duration=100,
            laser_oc=True,
            max_regular_overclocks=2,
        ),
        9495,  # (long) ceil(30 * 4^2 * 4.3 * 4.6)
        6,  # (int) (100 / 2^4)
    ),
    (
        "maxRegularOverclocksShiftsMixTowardsLaserOverclocks[laserOnly]",
        Overclock(
            recipe_eut=VP[4],
            voltage=V[9],
            duration=100,
            laser_oc=True,
            max_regular_overclocks=0,
        ),
        967_670,
        6,  # (int) (100 / 2^4)
    ),
    (
        "maxRegularOverclocksShiftsMixTowardsLaserOverclocks[mixed]",
        Overclock(
            recipe_eut=VP[4],
            voltage=V[9],
            duration=100,
            laser_oc=True,
            max_regular_overclocks=3,
        ),
        528_384,
        6,  # (int) (100 / 2^4)
    ),
    (
        "noLaserOverclockWithoutPowerHeadroom[tooLittle]",
        Overclock(
            recipe_eut=1000,
            voltage=4299,
            duration=100,
            laser_oc=True,
            max_regular_overclocks=0,
        ),
        1000,
        100,
    ),
    (
        "noLaserOverclockWithoutPowerHeadroom[justEnough]",
        Overclock(
            recipe_eut=1000,
            voltage=4400,
            duration=100,
            laser_oc=True,
            max_regular_overclocks=0,
        ),
        4300,
        50,
    ),
]


@pytest.mark.parametrize(
    ("oc", "eut", "duration"),
    [pytest.param(oc, e, d, id=name) for name, oc, e, d in OC_CASES],
)
def test_overclock_calculator(oc: Overclock, eut: int, duration: int | None) -> None:
    result = calculate(oc)
    assert result.eut == eut
    if duration is not None:
        assert result.duration == duration


def test_testCorrectEUtWhenOverclockingUnderOneTickWithHeat_Test() -> None:
    oc = Overclock(
        recipe_eut=1920,
        parallel=256,
        amperage=1,
        amperage_oc=True,
        heat_discount=True,
        heat_oc=True,
        recipe_heat=2000,
        machine_heat=15500,
        voltage=V[12] * 1_048_576,
        duration=250,
    )
    current = to_int(256 * multiplier_under_one_tick(oc))
    result = calculate(replace(oc, current_parallel=current))
    # Math.ceil(((long) 1920 * 256) * 4^14 * 0.95^15)
    assert result.eut == 61_127_291_352_876


def test_testNoOverclockCorrectWithUnderOneTickLogic_Test() -> None:
    oc = Overclock.no_oc(2_693_264_510, 100)
    assert multiplier_under_one_tick(replace(oc, parallel=24 * 64)) == 1


# The rows of the 5 parameterized tests.
FLOAT_PRECISION = [
    pytest.param(to_float(1 / 2.25), to_float(0.9), 42_467_327, id="float"),
    pytest.param(1 / 2.25, 0.9, 42_467_328, id="double"),
]
SUB_LV = [(8, 131_072), (16, 262_144), (30, 491_520)]
CLOSED_FORM = [(12.5, 328.0), (25.0, 164.0), (100.0, 41.0), (1.25, 3277.0)]
SUB_TWO_TICK = [(0.5, 4096.0), (1.0, 2048.0), (1.25, 1639.0)]
LASER_BUDGET = [
    (30, V[9] * 1024, 8),
    (VP[4], V[9], 4),
    (1920, V[10] * 4096, 6),
    (VP[1], V[12], 0),
    (500_000, V[11] * 64, 12),
]


@pytest.mark.parametrize(("speed_boost", "eut_discount", "eut"), FLOAT_PRECISION)
def test_floatPrecisionIssues(
    speed_boost: float, eut_discount: float, eut: int
) -> None:
    # GTNH issue 17207: Black Granite dust in an Industrial Centrifuge under UMV power.
    voltage = V[12]
    max_parallel = 6 * get_tier(voltage)
    oc = Overclock(
        recipe_eut=10,
        voltage=voltage,
        duration=20 * 20,
        duration_modifier=speed_boost,
        eut_modifier=eut_discount,
        parallel=max_parallel,
    )
    max_parallel = safe_int(to_long(max_parallel * multiplier_under_one_tick(oc)), 0)
    result = calculate(replace(oc, current_parallel=max_parallel))
    assert result.eut == eut


@pytest.mark.parametrize(("recipe_eut", "eut"), SUB_LV)
def test_laserOverclockDoesNotOverchargeSubLvRecipes(recipe_eut: int, eut: int) -> None:
    # The 32 EU/t floor applies to the tier ratio only, never to the consumption base.
    result = calculate(
        Overclock(recipe_eut=recipe_eut, voltage=V[8], duration=100, laser_oc=True)
    )
    assert result.eut == recipe_eut * 4**7 == eut


@pytest.mark.parametrize(("per_slice", "multiplier"), CLOSED_FORM)
def test_laserOverclockUnderOneTickMatchesNonLaserClosedForm(
    per_slice: float, multiplier: float
) -> None:
    oc = Overclock(
        recipe_eut=30,
        voltage=V[9] * 1024,
        laser_oc=True,
        max_regular_overclocks=8,
        duration_under_one_tick=per_slice,
    )
    assert multiplier_under_one_tick(oc) == multiplier
    assert multiplier_under_one_tick(oc) == math.ceil(2**12 / per_slice)


@pytest.mark.parametrize(("per_slice", "multiplier"), SUB_TWO_TICK)
def test_laserOverclockHandlesSubTwoTickDuration(
    per_slice: float, multiplier: float
) -> None:
    # neededOverclocks goes negative or zero here, which must not clamp the multiplier.
    oc = Overclock(
        recipe_eut=30,
        voltage=V[9] * 256,
        laser_oc=True,
        max_regular_overclocks=8,
        duration_under_one_tick=per_slice,
    )
    assert multiplier_under_one_tick(oc) == multiplier
    assert multiplier_under_one_tick(oc) == math.ceil(2**11 / per_slice)


@pytest.mark.parametrize(("recipe_eut", "machine_eut", "max_regular"), LASER_BUDGET)
def test_laserOverclockNeverExceedsMachinePower(
    recipe_eut: int, machine_eut: int, max_regular: int
) -> None:
    result = calculate(
        Overclock(
            recipe_eut=recipe_eut,
            voltage=machine_eut,
            duration=100,
            laser_oc=True,
            max_regular_overclocks=max_regular,
        )
    )
    assert result.eut <= machine_eut


def test_laserOverclockUnderOneTickMatchesAdvAssemblyLineUsage() -> None:
    slices, recipe_duration = 8, 200
    oc = Overclock(
        recipe_eut=30,
        duration_under_one_tick=recipe_duration / slices,
        parallel=1,
        voltage=V[9] * 1024 // slices,
        laser_oc=True,
        max_regular_overclocks=8,
    )
    max_parallel = safe_int(to_long(multiplier_under_one_tick(oc)), 0)
    assert max_parallel == 82
    result = calculate(replace(oc, current_parallel=max_parallel))
    assert result.duration == 1
    assert result.eut == 190_556_406
    assert result.eut <= V[9] * 1024 // slices


def test_every_case_of_the_gt_test_class_is_ported() -> None:
    """GT_OverclockCalculator_UnitTest has 50 @Test methods and 17 parameter rows (67 cases);
    two of the table's tests check two calculators each."""
    tests = {name.split("[")[0] for name, *_ in OC_CASES}
    flows = (
        3  # under one tick with heat, no-overclock multiplier, Adv. Assembly Line usage
    )
    assert len(tests) + flows == 50
    rows = [FLOAT_PRECISION, SUB_LV, CLOSED_FORM, SUB_TWO_TICK, LASER_BUDGET]
    assert sum(len(r) for r in rows) == 17


# GTUtilityTest: powInt equals Math.pow (Java's doubles, written in hex).
POW_INT = [
    (0.95, 1, "0x1.e666666666666p-1"),
    (0.95, 2, "0x1.ce147ae147ae1p-1"),
    (0.95, 3, "0x1.b6f9db22d0e55p-1"),
    (-0.95, 1, "-0x1.e666666666666p-1"),
    (-0.95, 2, "0x1.ce147ae147ae1p-1"),
    (-0.95, 3, "-0x1.b6f9db22d0e55p-1"),
    (0.95, -1, "0x1.0d79435e50d79p0"),
    (0.95, -2, "0x1.1ba81104f6c8p0"),
    (0.95, -3, "0x1.2a95f6f7c066cp0"),
    (-0.95, -1, "-0x1.0d79435e50d79p0"),
    (-0.95, -2, "0x1.1ba81104f6c8p0"),
    (-0.95, -3, "-0x1.2a95f6f7c066cp0"),
    (0.95, 0, "0x1.0p0"),
    (-0.95, 0, "0x1.0p0"),
    (0.0, 0, "0x1.0p0"),
    (-0.0, 0, "0x1.0p0"),
]


@pytest.mark.parametrize(("base", "exp", "expected"), POW_INT)
def test_powInt(base: float, exp: int, expected: str) -> None:
    """testPowIntPositive, testPowIntNegative, testPowIntZero, testPowIntZeroZero."""
    assert pow_int(base, exp) == float.fromhex(expected)


def test_testPowIntDomain() -> None:
    for i in range(1025):
        assert pow_int(2, i) == (math.inf if i > 1023 else math.ldexp(1.0, i))
    for i in range(513):
        assert pow_int(4, i) == (math.inf if i > 511 else math.ldexp(1.0, 2 * i))


def _floor_log4(x: int) -> int:
    k = 0
    while 4 ** (k + 1) <= x:
        k += 1
    return k


def _ceil_log4(x: int) -> int:
    k = 0
    while 4**k < x:
        k += 1
    return k


# GT's loops: i = 2, 4, ... 2^16, each checked at i - 1, i and i + 1 (as int and as long).
LOG_INPUTS = sorted({j for b in range(1, 17) for j in (2**b - 1, 2**b, 2**b + 1)})


def test_testLog4() -> None:
    # GT asserts each equals (int) (Math.log(x) / Math.log(4)).
    for x in LOG_INPUTS:
        assert log4(x) == _floor_log4(x)


def test_testLog4Ceil() -> None:
    # GT asserts each equals (int) Math.ceil(Math.log(x) / Math.log(4)).
    for x in LOG_INPUTS:
        assert log4ceil(x) == _ceil_log4(x)


def test_testLog4Zero() -> None:
    assert log4(0) == 0


def test_testGetTier() -> None:
    assert get_tier(-1) == 0
    assert get_tier(1) == 0
    assert get_tier(V[0]) == 0
    for i in range(1, 16):
        assert get_tier(V[i - 1] + 1) == i
        assert get_tier(V[i]) == i


@pytest.mark.parametrize(
    ("lhs", "rhs", "expected"),
    [(15, 8, 2), (-7, 2, -3), (-15, 8, -1), (7, -2, -3), (-7, -2, 4), (0, 5, 0)],
)
def test_testCeilDiv(lhs: int, rhs: int, expected: int) -> None:
    assert ceil_div(lhs, rhs) == expected


def test_testGetAmperageForTier() -> None:
    assert amperage_for_tier(V[UIV], UIV) == 1
    assert amperage_for_tier(LONG_MAX, UIV) == 274_877_906_944


# MTEMultiBlockBaseTest: (exotic hatches, energy hatches, structure valid).
HATCH_RULE = [
    (0, 0, False),
    (2, 0, False),
    (1, 0, True),
    (1, 1, False),
    (0, 1, True),
    (0, 2, True),
    (0, 3, False),
]


@pytest.mark.parametrize(("exotic", "normal", "valid"), HATCH_RULE)
def test_checkExoticAndNormalEnergyHatches(
    exotic: int, normal: int, valid: bool
) -> None:
    hatches = [EnergyHatch.multi_amp(5, 16)] * exotic + [EnergyHatch(5)] * normal
    assert (check_exotic_and_normal_hatches(hatches) is None) == valid
