"""CropsNH's rules (gregtec.crops.rules, D273). Every vector is worked by hand from CropsNH
2.0.140's behaviour (TileEntityCropSticks, MTECropBreeder, MTESeedGenerator, MTECropManager,
MutationRegistry, MutationPool, CropCard) and the 2.9.0 pack config; the machine numbers are
also checked against CropsNH's own tooltips in a real export (tests/integration)."""

from __future__ import annotations

from fractions import Fraction as F

import pytest

from gregtec.crops import rules as R


def test_nutrients_follow_the_points_formula() -> None:
    # base 5 + water 100 (10) + fertilizer 100 (10) + sky 2; no biome, dry
    assert R.nutrients(0, 0.0, True, 100, 100) == 27
    # past 100 counts as 100; 1 water rounds up to 1 point
    assert R.nutrients(0, 0.0, False, 200, 200) == 25
    assert R.nutrients(0, 0.0, False, 1, 0) == 6
    # two liked tags 14 each, a third doesn't count
    assert R.nutrients(3, 0.0, False, 0, 0) == 5 + 28
    # humidity: 0.8 is the full 14; 0.65 is just under half in Java floats (0.65f - 0.5f is
    # 0.14999998), so 6.9999986 truncates to 6; the larger of the two bonuses counts
    assert R.nutrients(0, 0.8, False, 0, 0) == 19
    assert R.nutrients(0, 0.65, False, 0, 0) == 11
    assert R.nutrients(1, 0.65, False, 0, 0) == 19


def test_growth_rate_and_sickness() -> None:
    # 27 points x5 = 135 >= 60 needed at tier 6: (6 + 1) x (100 + 75) / 100 = 12 (int)
    assert R.growth_rate(27, 6, 1) == 12
    # short: 50 of 60 -> 7 x (100 - 40) / 100 = 4
    assert R.growth_rate(10, 6, 1) == 4
    # far short: Java's division truncates -2.8 to -2, then 0: the crop gets sick
    assert R.growth_rate(5, 6, 1) == 0
    assert R.growth_rate(27, 1, 31) == (6 + 31) * (100 + 125) // 100
    assert R.nutrients_needed(6) == 12 and R.nutrients_needed(1) == 2


def test_growth_ticks_to_the_breeding_threshold() -> None:
    assert R.growth_ticks_to(600, 12, R.BREED_FRACTION_DEFAULT) == 40  # 480 of 600
    assert R.growth_ticks_to(600, 7, 0.8) == 69  # 483 >= 480; 68 x 7 = 476 isn't
    assert R.growth_ticks_to(600, 0, 0.8) is None
    assert R.growth_ticks_to(600, 1000, 0.8) == 1  # capped at the duration


def test_stat_variation_lists_by_parents_and_fertilizer() -> None:
    assert R.stat_variations(False, 49) == (-1, -1, 0, 1, 1)
    assert R.stat_variations(False, 50) == (-2, -1, 0, 1, 2)
    assert R.stat_variations(False, 100) == (-2, -1, 0, 1, 2)
    assert R.stat_variations(False, 101) == (0, 0, 0, 1, 2, 3, 4)
    assert R.stat_variations(True, 0) == (-1, 0, 0)
    assert R.stat_variations(True, 50) == (0,)
    assert R.stat_variations(True, 101) == (0, 0, 0, 1, 2, 3, 4)


def test_child_stats_average_down_then_vary_and_clamp() -> None:
    assert R.child_stat([10, 13], R.LOW_BREEDING) == {
        10: F(2, 5),
        11: F(1, 5),
        12: F(2, 5),
    }
    assert R.child_stat([1, 1], R.LOW_BREEDING) == {1: F(3, 5), 2: F(2, 5)}
    assert R.child_stat([31, 30], R.HIGH_BREEDING) == {30: F(3, 7), 31: F(4, 7)}
    assert R.expected(R.child_stat([10, 10], R.HIGH_BREEDING)) == 10 + F(10, 7)


N = R.Neighbour


def test_two_parents_with_a_recipe() -> None:
    out = R.attempt(
        [N("a", True, True), N("b", True, True)],
        [("c", frozenset({"a", "b"}), True)],
        {},
        frozenset({"a", "b"}),
    )
    # 1/3 tries; half copy a parent (each 1/2), half breed the recipe
    assert out.children == {"a": F(1, 12), "b": F(1, 12), "c": F(1, 6)}
    assert not out.ambiguous and out.any_child() == F(1, 3)


def test_a_crop_breeder_only_recipe_falls_through_to_the_pools() -> None:
    out = R.attempt(
        [N("a", True, True), N("b", True, True)],
        [("c", frozenset({"a", "b"}), False)],
        {"P": ("a", "b", "x")},
    )
    assert out.chance("c") == 0
    assert (
        out.chance("x") == F(1, 6) / 3
    )  # the pool's three members share the breed half


def test_identical_parents_can_pool_mutate() -> None:
    # V30: two seeds of one crop count twice toward a pool (createPoolQueue keeps duplicates)
    out = R.attempt(
        [N("w", True, True), N("w", True, True)],
        [],
        {"Green": ("c", "q", "w")},
    )
    assert out.children == {"c": F(1, 18), "q": F(1, 18), "w": F(1, 6) + F(1, 18)}


def test_a_recipe_needs_two_distinct_parents_and_cant_cross_falls_to_breeding() -> None:
    out = R.attempt(
        [N("a", False, True), N("b", False, True)],
        [("c", frozenset({"a", "b"}), True)],
        {},
    )
    assert out.children == {"c": F(1, 3)}  # no crosser: every try breeds
    # two seeds of one crop: half the tries copy it; no recipe has one parent, no pool here
    out = R.attempt(
        [N("a", True, True), N("a", True, True)], [("c", frozenset({"a"}), True)], {}
    )
    assert out.children == {"a": F(1, 6)}


def test_a_smaller_recipe_of_the_same_parents_makes_the_odds_a_lower_bound() -> None:
    abc = frozenset({"a", "b", "c"})
    out = R.attempt(
        [N("a", True, True), N("b", True, True), N("c", True, True)],
        [("x", abc, True), ("y", frozenset({"a", "b"}), True)],
        {},
        abc,
    )
    assert out.ambiguous and out.chance("x") == F(1, 12)


def test_unready_parents_dont_breed() -> None:
    out = R.attempt(
        [N("a", False, False), N("b", True, True)],
        [("c", frozenset({"a", "b"}), True)],
        {},
    )
    assert out.children == {
        "b": F(1, 6)
    }  # only b copies; breeding needs two ready parents


def test_attempts_for_a_confidence() -> None:
    assert R.attempts_for(F(1, 2), 0.9) == 4  # 1 - 0.5^4 = 0.9375
    assert R.attempts_for(F(0), 0.9) is None and R.attempts_for(F(1), 0.9) == 1


def test_weeds_and_seed_drops() -> None:
    assert R.spreads_weeds(25, 24, 24) and not R.spreads_weeds(24, 1, 24)
    assert not R.spreads_weeds(25, 25, 24)  # Resistance at Growth stops it
    assert R.spreads_weeds(9, 1, 8)  # Necrobloom's own threshold is 8
    assert R.weed_spread_chance(24) == F(27, 50) and R.weed_spread_chance(48) == 1
    assert R.seed_drop_chance(1) == F(1, 31) and R.seed_drop_chance(31) == 1


@pytest.mark.parametrize(
    ("tier", "chance", "tank", "slots"),
    [
        (1, 40, 3200, 3),
        (2, 50, 6400, 3),
        (3, 60, 12800, 6),
        (4, 70, 25600, 6),
        (5, 80, 256000, 6),
        (6, 90, 256000, 6),
        (7, 100, 256000, 6),
        (8, 100, 256000, 6),
    ],
)
def test_crop_breeder_by_tier(tier: int, chance: int, tank: int, slots: int) -> None:
    assert R.breeder_chance(tier) == chance
    assert R.breeder_tank(tier) == tank
    assert R.breeder_slots(tier) == slots


def test_crop_breeder_fertilizer_and_stat_cap() -> None:
    # NEI's Crop Breeder page shows 7,848 L for Auronia (tier 8) at 31/31/31
    assert R.breeder_fertilizer(8, 31, 31, 31) == 8 * 144 + 93 * 72 == 7848
    assert R.breeder_fertilizer(0, 0, 0, 0) == 2  # each part at least 1
    # LV: (3200 - 6 x 144) / 72 = 32 stat points for a tier-6 child; HV holds 31/31/31
    assert R.breeder_max_stat_sum(1, 6) == 32
    assert R.breeder_max_stat_sum(2, 6) == 76
    assert R.breeder_max_stat_sum(3, 6) == 93
    assert R.breeder_child_stats([(10, 5, 7), (13, 6, 8)]) == (11, 5, 7)
    assert (
        R.runs_for(40, 0.99) == 10
        and R.runs_for(60, 0.99) == 6
        and R.runs_for(100, 0.99) == 1
    )


def test_seed_generator_and_crop_manager() -> None:
    assert [R.seed_generator_tank(t) for t in (1, 2, 3, 4, 5)] == [
        1600,
        3200,
        6400,
        12800,
        256000,
    ]
    assert R.seed_generator_fertilizer(31, 31, 31) == 93 * 36
    assert (
        R.seed_generator_max_stat_sum(1) == 44
        and R.seed_generator_max_stat_sum(3) == 93
    )
    # the tooltips' ranges: 11, 15, 19 ... blocks square
    assert [2 * R.crop_manager_radius(t) + 1 for t in (1, 2, 3, 7)] == [11, 15, 19, 35]


def test_generations_to_a_stat() -> None:
    assert R.generations_to(30, 31, 2, R.HIGH_BREEDING) == pytest.approx(7 / 4)
    assert R.generations_to(1, 2, 2, R.LOW_BREEDING) == pytest.approx(5 / 2)
    assert R.generations_to(5, 5, 2, R.HIGH_BREEDING) == 0.0
    assert R.generations_to(1, 31, 2, R.MED_SPREADING) is None
    slow = R.generations_to(1, 31, 2, R.MED_BREEDING)
    fast = R.generations_to(1, 31, 2, R.HIGH_BREEDING)
    assert slow is not None and fast is not None and fast < slow
