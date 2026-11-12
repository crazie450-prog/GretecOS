"""CropsNH 2.0.140's breeding, growth and machine rules, re-implemented from its behaviour
(plan 7.17, D78, D273; research [I]).

CropsNH is LGPL-3.0: its source (tag ``2.0.140``, commit ``db0a9936bdcc``, the version GTNH
2.9.0's manifest pins) was read to learn these rules and nothing is copied from it. Each function
names the behaviour it reproduces; the unit tests' vectors are worked by hand from that
behaviour (tests/unit/crops/test_rules.py), and ``nutrients``, ``growth_rate`` and
``child_stat`` are also checked against CropsNH's own ``TileEntityCropSticks`` run from its jar
(tests/unit/crops/oracle, test_rules_oracle.py). The pack's config (GT-New-Horizons-Modpack tag
``2.9.0``, commit ``79f9a03``, ``config/cropsnh/Configuration.cfg``) keeps every default used
here: breeding chance 3, growth multiplier 1.0, the stat variation lists, thresholds 50 and 101,
weed spawn 100 and weed spread 50.

Java's integer arithmetic is kept where it changes a result (division truncates toward zero,
``(int)`` casts truncate, float32 for a crop's growth fraction). Probabilities are exact
:class:`fractions.Fraction` values; the planner turns them into numbers for people.

Nothing here reads the DB: a crop's own numbers (tier, growth duration, thresholds, the Growth
above which it spreads weeds) come from the export (``crop:<id>``) and are passed in.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

# --- the crop stick (TileEntityCropSticks) ---------------------------------------------------

TICK_RATE = 256  # game ticks between two growth ticks of a crop stick
TICKS_PER_SECOND = 20
MIN_STAT, MAX_STAT = 1, 31
BREEDING_CHANCE = (
    3  # an empty double stick tries to breed 1 time in 3 growth ticks (config)
)
CROSS_CHANCE = Fraction(1, 2)  # an attempt first tries to copy a neighbour's species
WEED_SPAWN_CHANCE = (
    100  # an empty (single or double) stick grows a weed 1 in 100 (config)
)
WEED_SPREAD_CHANCE = 50  # a weedy crop spreads when rand(50) - Growth <= 2 (config)
GROWTH_MULTIPLIER = 1.0  # config

# nutrients (getNutrientsPerCycle) and growth speed (getGrowthRate)
BASE_GROWTH_SPEED = 6
BASE_NUTRIENT_VALUE = 5
MAX_WATER_BONUS_AT = 100
MAX_FERTILIZER_BONUS_AT = 100
SKY_ACCESS_BONUS = 2
MAX_LIKED_BIOME_TAGS = 2
LIKED_BIOME_BONUS = 14
LOW_HUMIDITY, HIGH_HUMIDITY = 0.5, 0.8
NUTRIENT_POINT_SCALE = 5
NUTRIENTS_NEEDED_PER_TIER = 10

# fertilizer: by hand up to 100 (bone meal and the like); a Crop Manager tops sticks up to 200
MANUAL_FERTILIZER_MAX = 100
CROP_MANAGER_FERTILIZER_CAP = 200
# every growth tick a stick uses 1 water and 1 fertilizer while it has any (onGrowthTick), crop
# or not; rain adds water, nothing adds fertilizer but a hand or a Crop Manager
UPKEEP_PER_GROWTH_TICK = 1
MED_FERTILIZER = 50  # Medium Fertilizer Breeding/Spreading Threshold (config)
HIGH_FERTILIZER = 101  # High Fertilizer Breeding/Spreading Threshold (config)

# the stat variation lists (config): one entry is drawn at random, so repeats weigh more
LOW_BREEDING = (-1, -1, 0, 1, 1)
MED_BREEDING = (-2, -1, 0, 1, 2)
HIGH_BREEDING = (0, 0, 0, 1, 2, 3, 4)
LOW_SPREADING = (-1, 0, 0)
MED_SPREADING = (0,)
HIGH_SPREADING = (0, 0, 0, 1, 2, 3, 4)

# --- the machines (GT MetaTileEntities in CropsNH) ---------------------------------------------

LV, MV, HV, EV, IV, LUV, ZPM = 1, 2, 3, 4, 5, 6, 7  # GT voltage tier indices
BREEDER_MIN_CHANCE = 40  # percent at LV
BREEDER_CHANCE_PER_TIER = 10
BREEDER_FERTILIZER_PER_TIER = 144  # mB of enriched fertilizer per tier of the child
BREEDER_FERTILIZER_PER_STAT = 72  # mB per point of Growth + Gain + Resistance
SEED_GENERATOR_FERTILIZER_PER_STAT = 36
CROP_MANAGER_VERTICAL_RADIUS = 2


def _f32(x: float) -> float:
    """``x`` rounded to a Java float."""
    return float(struct.unpack("<f", struct.pack("<f", x))[0])


BREED_FRACTION_DEFAULT = _f32(0.8)  # CropCard's breeding and crossing thresholds


def _jdiv(a: int, b: int) -> int:
    """Java's ``int / int`` (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


# --- growth ---------------------------------------------------------------------------------


def nutrients(
    liked_biome_tags: int,
    humidity: float,
    sky: bool,
    water: int,
    fertilizer: int,
) -> int:
    """A stick's nutrient points (``getNutrientsPerCycle``): base 5, +1 per 10 water and per 10
    fertilizer (each counted up to 100, rounded up), +2 with sky access, then the larger of the
    humidity bonus and 14 per liked biome tag (at most 2)."""
    liked = min(MAX_LIKED_BIOME_TAGS, liked_biome_tags)
    water = min(MAX_WATER_BONUS_AT, water)
    fertilizer = min(MAX_FERTILIZER_BONUS_AT, fertilizer)
    out = BASE_NUTRIENT_VALUE + _jdiv(water + 9, 10) + _jdiv(fertilizer + 9, 10)
    out += SKY_ACCESS_BONUS if sky else 0
    wet = _f32(
        _f32(_f32(humidity) - _f32(LOW_HUMIDITY))
        / _f32(_f32(HIGH_HUMIDITY) - _f32(LOW_HUMIDITY))
    )
    wet = _f32(max(0.0, min(1.0, wet)) * LIKED_BIOME_BONUS)
    return out + max(int(wet), liked * LIKED_BIOME_BONUS)


def upkeep_ticks(start: int, level: int) -> int:
    """Growth ticks a stick filled to ``start`` water or fertilizer still holds at least
    ``level`` when the tick's growth and breeding run (the 1 a tick is taken after them)."""
    if start < level:
        return 0
    return (start - level) // UPKEEP_PER_GROWTH_TICK + 1


def growth_rate(nutrient_points: int, tier: int, growth: int) -> int:
    """Growth progress per growth tick (``getGrowthRate``); 0 or less makes the crop sick."""
    scaled = nutrient_points * NUTRIENT_POINT_SCALE
    need = tier * NUTRIENTS_NEEDED_PER_TIER
    if need < 0:
        return 0
    base = BASE_GROWTH_SPEED + growth
    if scaled >= need:
        return _jdiv(base * (100 + (scaled - need)), 100)
    return max(_jdiv(base * (100 - (need - scaled) * 4), 100), 0)


def nutrients_needed(tier: int) -> int:
    """The fewest nutrient points at which a crop of ``tier`` grows at full base speed."""
    return -(-tier * NUTRIENTS_NEEDED_PER_TIER // NUTRIENT_POINT_SCALE)


def growth_ticks_to(duration: int, rate: int, fraction: float) -> int | None:
    """Growth ticks from planting until ``progress / duration`` (as Java floats) reaches
    ``fraction``; None when the crop doesn't grow (``rate`` 0 or less)."""
    step = int(rate * GROWTH_MULTIPLIER)
    if step <= 0 or duration <= 0:
        return None
    target = _f32(fraction)
    n = max(0, math.floor(duration * target / step) - 1)
    while (
        _f32(min(1.0, _f32(float(min(duration, n * step))) / _f32(float(duration))))
        < target
    ):
        n += 1
    return n


# --- breeding on crop sticks ----------------------------------------------------------------


def stat_variations(single_parent: bool, min_fertilizer: int) -> tuple[int, ...]:
    """The variation list a child's stats draw from (``getStatVariation``): spreading lists for
    one contributing parent, breeding lists for two or more, by the lowest fertilizer among
    them."""
    if single_parent:
        if min_fertilizer >= HIGH_FERTILIZER:
            return HIGH_SPREADING
        if min_fertilizer >= MED_FERTILIZER:
            return MED_SPREADING
        return LOW_SPREADING
    if min_fertilizer >= HIGH_FERTILIZER:
        return HIGH_BREEDING
    if min_fertilizer >= MED_FERTILIZER:
        return MED_BREEDING
    return LOW_BREEDING


def child_stat(
    parents: Sequence[int], variations: Sequence[int]
) -> dict[int, Fraction]:
    """One child stat's distribution (``variateStat``): the contributing parents' average
    (integer division) plus one random draw from ``variations``, clamped to 1..31."""
    avg = _jdiv(sum(parents), len(parents))
    out: dict[int, Fraction] = {}
    each = Fraction(1, len(variations))
    for v in variations:
        s = max(MIN_STAT, min(MAX_STAT, avg + v))
        out[s] = out.get(s, Fraction(0)) + each
    return dict(sorted(out.items()))


def expected(dist: Mapping[int, Fraction]) -> Fraction:
    return sum((Fraction(k) * p for k, p in dist.items()), Fraction(0))


@dataclass(frozen=True)
class Neighbour:
    """A crop on a single stick next to the double stick (north, south, east or west)."""

    crop: str
    can_cross: bool  # grown to its crossing threshold, and the crop can cross at all
    can_breed: bool  # grown to its breeding threshold


@dataclass(frozen=True)
class Outcome:
    """What one breeding attempt on an empty double stick gives, per growth tick.

    ``children`` maps each species that can appear to its chance per growth tick (exact, before
    the soil and growth-requirement check on the double stick, which the guide's layout meets
    for the target). ``ambiguous`` is True when a parent-combination recipe smaller than the
    one aimed at might also match (it depends on CropsNH's crop registration order, which the
    export doesn't carry): then the target's chance is the lowest it can be."""

    children: dict[str, Fraction]
    ambiguous: bool = False

    def chance(self, crop: str) -> Fraction:
        return self.children.get(crop, Fraction(0))

    def any_child(self) -> Fraction:
        return sum(self.children.values(), Fraction(0))


def attempt(
    neighbours: Sequence[Neighbour],
    recipes: Iterable[tuple[str, frozenset[str], bool]],
    pools: Mapping[str, Sequence[str]],
    aim: frozenset[str] | None = None,
) -> Outcome:
    """The species a double stick's growth tick can give, and their chances.

    Behaviour (``attemptBreeding`` and ``getBreedingResult``): 1 in 3 growth ticks it tries; half
    the time it copies one of the neighbours that can cross (each equally likely); otherwise,
    or when none can cross, the neighbours that can breed are matched against the
    parent-combination recipes (``recipes``: child, parent set, breeds in the world), one match
    chosen at random; a match that can't breed in the world (Crop Breeder only) falls through
    to the mutation pools: a random pool with two or more of those neighbours as members
    (identical seeds count twice), then a random member of it.

    Recipe matching walks the distinct breedable species sorted by registration order and
    matches the recipes whose parents are the first two, three or four of them. Without that
    order, a recipe is counted as matching when its parents are a subset of the neighbours'
    species; ``aim`` (the recipe the layout is for: all of the neighbours' species) always
    matches, and the other subset recipes make the result ``ambiguous``.
    """
    out: dict[str, Fraction] = {}

    def add(crop: str, p: Fraction) -> None:
        if p:
            out[crop] = out.get(crop, Fraction(0)) + p

    tries = Fraction(1, BREEDING_CHANCE)
    crossers = [n for n in neighbours if n.can_cross]
    breed_share = Fraction(1)
    if crossers:
        for n in crossers:
            add(n.crop, tries * CROSS_CHANCE / len(crossers))
        breed_share = 1 - CROSS_CHANCE
    breeders = [n.crop for n in neighbours if n.can_breed]
    ambiguous = False
    if len(breeders) >= 2:
        species = frozenset(breeders)
        matches = [
            (child, parents, world)
            for child, parents, world in recipes
            if len(parents) >= 2 and parents <= species
        ]
        if aim is not None:
            ambiguous = any(parents != aim for _, parents, _ in matches)
            # the guide's layout aims at one recipe: count the others as matching too, so the
            # aimed child's chance is its lowest
        p_branch = tries * breed_share
        falls = Fraction(0)
        if matches and len(species) >= 2:
            each = p_branch / len(matches)
            for child, _, world in matches:
                if world:
                    add(child, each)
                else:
                    falls += each
        else:
            falls = p_branch
        if falls:
            members = {pool: set(m) for pool, m in pools.items()}
            hits = [
                pool
                for pool, m in sorted(members.items())
                if sum(1 for b in breeders if b in m) >= 2
            ]
            for pool in hits:
                listed = sorted(members[pool])
                for crop in listed:
                    add(crop, falls / len(hits) / len(listed))
    return Outcome(dict(sorted(out.items())), ambiguous)


def attempts_for(p: Fraction, confidence: float) -> int | None:
    """Growth ticks until something with chance ``p`` per tick has happened with probability
    ``confidence`` (geometric); None when ``p`` is 0."""
    if p <= 0:
        return None
    if p >= 1:
        return 1
    return math.ceil(math.log(1 - confidence) / math.log(1 - float(p)))


def weed_spread_chance(growth: int) -> Fraction:
    """Per growth tick, a mature crop that spreads weeds does so when ``rand(50) - Growth <= 2``."""
    return Fraction(min(WEED_SPREAD_CHANCE, max(0, growth + 3)), WEED_SPREAD_CHANCE)


def spreads_weeds(growth: int, resistance: int, weeds_above: int) -> bool:
    """A mature crop spreads weeds when its Resistance is below its Growth and its Growth is
    above the crop's own threshold (``CropCard.spreadsWeeds``; 24 for most crops)."""
    return resistance < growth and growth > weeds_above


def seed_drop_chance(resistance: int) -> Fraction:
    """Removing a crop (or harvesting with a spade) gives its seed back when
    ``Resistance > rand(31)``."""
    return Fraction(max(0, min(MAX_STAT, resistance)), MAX_STAT)


# --- the machines ---------------------------------------------------------------------------


def basic_machine_capacity(tier: int) -> int:
    """A GT basic machine's fluid tank by tier (GT 5.09.54.265 ``getCapacityForTier``)."""
    return {0: 8000, 1: 16000, 2: 32000, 3: 64000, 4: 128000}.get(tier, 256000)


def breeder_chance(tier: int) -> int:
    """The Crop Breeder's success chance in percent: 40 at LV, +10 per tier, 100 from ZPM."""
    return min(100, BREEDER_MIN_CHANCE + (tier - LV) * BREEDER_CHANCE_PER_TIER)


def breeder_fertilizer(child_tier: int, growth: int, gain: int, resistance: int) -> int:
    """mB of enriched fertilizer one Crop Breeder run takes (paid on a failure too)."""
    return max(1, child_tier * BREEDER_FERTILIZER_PER_TIER) + max(
        1, (growth + gain + resistance) * BREEDER_FERTILIZER_PER_STAT
    )


def breeder_tank(tier: int) -> int:
    """The Crop Breeder's fertilizer tank: a fifth of a basic machine's below IV."""
    cap = basic_machine_capacity(tier)
    return cap // 5 if tier < IV else cap


def breeder_slots(tier: int) -> int:
    return 3 if tier < HV else 6


def breeder_max_stat_sum(tier: int, child_tier: int) -> int:
    """The highest Growth + Gain + Resistance a child of ``child_tier`` can have from a Crop
    Breeder of ``tier``: one run's fertilizer has to fit its tank (what the LV and MV tooltips
    call "cannot breed high-stat seeds")."""
    room = breeder_tank(tier) - max(1, child_tier * BREEDER_FERTILIZER_PER_TIER)
    return max(0, min(3 * MAX_STAT, room // BREEDER_FERTILIZER_PER_STAT))


def breeder_child_stats(
    parents: Sequence[tuple[int, int, int]],
) -> tuple[int, int, int]:
    """A Crop Breeder child's Growth, Gain, Resistance: the recipe parents' averages."""
    n = len(parents)
    return (
        _jdiv(sum(p[0] for p in parents), n),
        _jdiv(sum(p[1] for p in parents), n),
        _jdiv(sum(p[2] for p in parents), n),
    )


def runs_for(chance_pct: int, confidence: float) -> int:
    """Crop Breeder runs (each using one of every parent) to succeed once with probability
    ``confidence``."""
    if chance_pct >= 100:
        return 1
    return math.ceil(math.log(1 - confidence) / math.log(1 - chance_pct / 100))


def seed_generator_fertilizer(growth: int, gain: int, resistance: int) -> int:
    """mB of enriched fertilizer one Seed Generator copy takes."""
    return max(1, (growth + gain + resistance) * SEED_GENERATOR_FERTILIZER_PER_STAT)


def seed_generator_tank(tier: int) -> int:
    cap = basic_machine_capacity(tier)
    return cap // 10 if tier < IV else cap


def seed_generator_max_stat_sum(tier: int) -> int:
    return min(
        3 * MAX_STAT, seed_generator_tank(tier) // SEED_GENERATOR_FERTILIZER_PER_STAT
    )


def crop_manager_radius(tier: int) -> int:
    """Blocks the Crop Manager reaches on each side horizontally: 3 + 2 x tier."""
    return 3 + max(0, 2 * tier)


# --- stat plans -----------------------------------------------------------------------------


def generations_to(
    start: int, target: int, parents: int, variations: Sequence[int]
) -> float | None:
    """Expected children until one has a stat of at least ``target``, from ``parents`` crops of
    one species all at ``start``, keeping a child only when it beats the weakest parent and
    then replacing that parent (the plan's keep rule). Exact for the model (a Markov chain on
    the parents' stats); None when the variations can never get there."""
    if start >= target:
        return 0.0
    if max(variations) <= 0 and start < target:
        return None
    each = 1 / len(variations)
    memo: dict[tuple[int, ...], float | None] = {}

    def solve(state: tuple[int, ...]) -> float | None:
        if state in memo:
            return memo[state]
        avg = _jdiv(sum(state), len(state))
        stay = 0.0
        rest = 1.0
        for v in variations:
            c = max(MIN_STAT, min(MAX_STAT, avg + v))
            if c >= target:
                continue
            if c <= state[0]:
                stay += each
                continue
            nxt = solve(tuple(sorted((c, *state[1:]))))
            if nxt is None:
                memo[state] = None
                return None
            rest += each * nxt
        out = None if stay >= 1.0 else rest / (1 - stay)
        memo[state] = out
        return out

    return solve(tuple([start] * parents))
