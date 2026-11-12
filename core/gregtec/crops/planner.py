"""The crop breeding guide (plan 7.17 part 1 and 2, D78, D273, D274; WO-6.7).

For a target crop, :func:`guide` finds the cheapest way from the crops Clayton has (his list,
plus any crop an alternate seed plants, such as a wheat seed) and writes each step as data:

- **Layout**: which parents sit on single sticks around the double stick (north, south, east and
  west only), and what goes under each stick (a sub-soil block).
- **Requirements**, checked against what is known: soil and sub-soil blocks (his stock in My
  base), light, and the nutrient score the assumed setup gives against what the crop's tier
  needs.
- **Odds and times**, computed by :mod:`gregtec.crops.rules` from CropsNH's own rules and the
  crops' exported numbers: the chance per growth tick (every 256 ticks), the expected and 90%
  times, the time for the parents to reach their breeding threshold, the other children and
  weeds to clear.
- **Warnings** and the **keep rule**; Crop Breeder steps (its success chance at his tier, the
  fertilizer per run and the copies to make first), a Crop Manager step from LV, and a
  **stats plan** to 31/31/31 with the expected generations.

The path is chosen by code (fewest mutation-pool steps, then fewest Crop Breeder steps above his
tier, then fewest steps, then the shortest expected time): parent-combination recipes first, then
mutation pools, as plan 7.17 says. A model explains a guide; it never reorders or recomputes it.

**Every number is a** :class:`Num` **with its citations** (``crop:`` for crop data, ``r:`` for a
Crop Breeder recipe, ``mte:`` for a CropsNH machine), and every line is a :class:`Line` whose
text holds digits only through its numbers or the DB text it cites; :func:`check` is the
validator (WO-6.7 acceptance: every number cited).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from fractions import Fraction
from typing import Any

from gregtec.crops import rules
from gregtec.crops.facts import BreederRecipe, Facts
from gregtec.ingest.crops import Crop

CITE = re.compile(r"^(r|q|mte|crop|plan|w|vein|pos):\S+$")
DIGIT = re.compile(r"[0-9]")
DIRECTIONS = ("north", "south", "east", "west")
DEFAULT_TARGET = (rules.MAX_STAT, rules.MAX_STAT, rules.MAX_STAT)
CONFIDENCE = 0.9  # the "90% of the time" figures
BREEDER_CONFIDENCE = (
    0.99  # copies to make before a Crop Breeder run (a failure eats them)
)
POOL_CANDIDATES = 4  # cheapest members of a pool tried as parents
STAT_PARENTS = 2  # parents around a double stick in the stats plan
TIER_NAMES = ("ULV", "LV", "MV", "HV", "EV", "IV", "LuV", "ZPM", "UV", "UHV")

Stats = tuple[int, int, int]  # Growth, Gain, Resistance


class GuideError(ValueError):
    """No guide: an unknown crop, or no way to it; the message says why."""


# --- numbers and lines with their citations --------------------------------------------------


@dataclass(frozen=True)
class Num:
    """A number in a guide, with the citations it was computed from."""

    value: float
    unit: str
    refs: tuple[str, ...]
    text: str  # how it reads ("12.5%", "3 h 4 min")

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "text": self.text,
            "refs": list(self.refs),
        }


@dataclass(frozen=True)
class Line:
    """A sentence; ``{0}``, ``{1}`` ... are its numbers. Digits elsewhere in it come only from
    the DB text it cites in ``refs`` (a crop's requirement text, a block's name)."""

    template: str
    nums: tuple[Num, ...] = ()
    refs: tuple[str, ...] = ()

    def text(self) -> str:
        return self.template.format(*(n.text for n in self.nums))

    def as_dict(self) -> dict[str, Any]:
        cites = list(
            dict.fromkeys((*self.refs, *(r for n in self.nums for r in n.refs)))
        )
        return {
            "text": self.text(),
            "nums": [n.as_dict() for n in self.nums],
            "refs": cites,
        }


def _pct(p: Fraction | float, refs: Iterable[str]) -> Num:
    v = float(p) * 100
    text = f"{v:.3g}%" if v < 10 else f"{v:.1f}%".replace(".0%", "%")
    return Num(round(v, 6), "%", tuple(dict.fromkeys(refs)), text)


def _secs(s: float, refs: Iterable[str]) -> Num:
    return Num(round(s, 1), "s", tuple(dict.fromkeys(refs)), _duration(s))


def _count(n: float, unit: str, refs: Iterable[str], digits: int = 0) -> Num:
    text = f"{n:,.{digits}f}" if digits else f"{round(n):,}"
    return Num(
        round(n, digits) if digits else round(n),
        unit,
        tuple(dict.fromkeys(refs)),
        f"{text} {unit}".strip(),
    )


def _duration(s: float) -> str:
    s = max(0.0, s)
    if s < 90:
        return f"{round(s)} s"
    m = round(s / 60)
    if m < 90:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} d {h} h" if h else f"{d} d"


# --- what a guide holds ----------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """One block of a layout grid (row 0 is north)."""

    role: str  # parent, double (the empty double stick), empty, manager
    crop: str | None = (
        None  # the crop on a parent stick, the child for the double stick
    )
    name: str | None = None
    under: str | None = None  # a sub-soil block to put under the farmland, by name


@dataclass(frozen=True)
class Requirement:
    what: str  # soil, sub_soil, light, nutrients, catalyst, machine, analyzed
    status: str  # met, needed, check
    line: Line


@dataclass
class Step:
    n: int
    kind: str  # have, obtain, sticks, pool, breeder, crop_manager
    child: str
    child_name: str
    parents: tuple[str, ...] = ()
    title: str = ""
    layout: list[list[Cell]] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    numbers: list[Line] = field(default_factory=list)
    warnings: list[Line] = field(default_factory=list)
    keep: list[Line] = field(default_factory=list)
    refs: tuple[str, ...] = ()
    expected_s: float | None = None  # for the path's total
    done: bool = False


@dataclass
class StatPlan:
    target: Stats
    variations: tuple[int, ...]
    lines: list[Line] = field(default_factory=list)
    warnings: list[Line] = field(default_factory=list)


@dataclass
class Guide:
    target: str
    target_name: str
    tier: int
    run_key: str
    steps: list[Step]
    stats: StatPlan | None
    total: Line | None
    notes: list[Line] = field(default_factory=list)

    def next_step(self) -> Step | None:
        return next((s for s in self.steps if not s.done), None)


@dataclass(frozen=True)
class Setup:
    """The conditions a guide assumes for every stick (the Crops view shows them)."""

    water: int = (
        rules.MAX_WATER_BONUS_AT
    )  # kept wet (rain, a watering can, a Crop Manager)
    sky: bool = True
    liked_biome_tags: int = 0
    humidity: float = 0.0  # the worst case: a dry biome gives nothing


# --- the path --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Option:
    kind: str  # have, obtain, sticks, pool, breeder
    child: str
    parents: tuple[str, ...] = ()  # pool parents may repeat a crop
    outcome: rules.Outcome | None = None
    recipe: BreederRecipe | None = None
    machine_only: bool = False
    seed: str | None = None  # obtain: the alternate seed item (registry@meta)
    chance: Fraction = Fraction(0)


Cost = tuple[
    int, int, int, float
]  # pool steps, Crop Breeder steps above his tier, steps, s


def _add(a: Cost, b: Cost) -> Cost:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3])


class _Planner:
    def __init__(
        self,
        facts: Facts,
        have: Mapping[str, Stats],
        tier: int,
        setup: Setup,
        alternate_seeds: bool,
        stock: frozenset[str],
    ) -> None:
        self.f = facts
        self.crops = facts.data.crops
        self.have = dict(have)
        self.tier = tier
        self.setup = setup
        self.stock = stock
        self.triples = [
            (r.child, frozenset(r.parents), not r.machine_only)
            for r in facts.data.recipes
        ]
        self.pools = {p.name: p.members for p in facts.data.pools}
        self.by_child: dict[str, list[Any]] = {}
        for r in facts.data.recipes:
            self.by_child.setdefault(r.child, []).append(r)
        self.breeder: dict[tuple[str, frozenset[str]], BreederRecipe] = {
            (b.child, b.parents): b for b in facts.breeder
        }
        self.seeds: dict[str, str] = {}
        if alternate_seeds:
            for s in facts.data.seeds:
                self.seeds.setdefault(s.crop, f"{s.registry_name}@{s.meta or 0}")
        self._attempts: dict[
            tuple[tuple[str, ...], frozenset[str] | None], rules.Outcome
        ] = {}

    # crop facts
    def crop(self, cid: str) -> Crop:
        return self.crops[cid]

    def stats_of(self, cid: str) -> Stats:
        return self.have.get(cid, (rules.MIN_STAT, rules.MIN_STAT, rules.MIN_STAT))

    def fertilizer(self) -> int:
        """Fertilizer in the sticks the guide assumes: a Crop Manager's 200 from LV, else the
        100 a hand tops them to."""
        if self.tier >= rules.LV and self.f.lowest("crop_manager") is not None:
            return rules.CROP_MANAGER_FERTILIZER_CAP
        return rules.MANUAL_FERTILIZER_MAX

    def nutrients(self) -> int:
        s = self.setup
        return rules.nutrients(
            s.liked_biome_tags, s.humidity, s.sky, s.water, self.fertilizer()
        )

    def neighbour(self, cid: str) -> rules.Neighbour:
        c = self.crop(cid)
        return rules.Neighbour(
            cid, c.crossing_threshold >= 0, c.breeding_threshold >= 0
        )

    def attempt(
        self, parents: Sequence[str], aim: frozenset[str] | None
    ) -> rules.Outcome:
        key = (tuple(sorted(parents)), aim)
        if key not in self._attempts:
            self._attempts[key] = rules.attempt(
                [self.neighbour(p) for p in key[0]], self.triples, self.pools, aim
            )
        return self._attempts[key]

    def grows_here(self, child: str, spot: str) -> bool:
        """Whether ``child`` can take the double stick laid out for ``spot``: the same soil
        lists, and its sub-soil block (if any) is one ``spot``'s block also is."""
        a, b = self.crop(child), self.crop(spot)
        if child == spot:
            return True
        if not set(a.soil_lists) & set(b.soil_lists) and "global" not in a.soil_lists:
            return False
        needs = {r.text() for r in a.sub_soils}
        if not needs:
            return True
        return bool(needs & {r.text() for r in b.sub_soils})

    def ready_ticks(self, cid: str) -> int | None:
        c = self.crop(cid)
        rate = rules.growth_rate(self.nutrients(), c.tier, self.stats_of(cid)[0])
        return rules.growth_ticks_to(
            c.growth_duration,
            rate,
            max(c.breeding_threshold, c.crossing_threshold, 0.0),
        )

    # options
    def options(
        self, cid: str, costs: Mapping[str, Cost]
    ) -> list[tuple[Cost, _Option]]:
        out: list[tuple[Cost, _Option]] = []
        if cid in self.have:
            return [((0, 0, 0, 0.0), _Option("have", cid))]
        if cid in self.seeds:
            out.append(((0, 0, 1, 0.0), _Option("obtain", cid, seed=self.seeds[cid])))
        for r in self.by_child.get(cid, []):
            parents = tuple(sorted(set(r.parents)))
            if not all(p in costs for p in parents):
                continue
            base: Cost = (0, 0, 0, 0.0)
            for p in parents:
                base = _add(base, costs[p])
            b = self.breeder.get((cid, frozenset(parents)))
            if r.machine_only:
                tier = b.voltage_tier if b is not None else rules.LV
                above = 1 if tier > self.tier else 0
                chance = Fraction(
                    rules.breeder_chance(max(tier, self.tier, rules.LV)), 100
                )
                secs = (b.duration_ticks / rules.TICKS_PER_SECOND) if b else 0.0
                out.append(
                    (
                        _add(base, (0, above, 1, secs / float(chance))),
                        _Option(
                            "breeder",
                            cid,
                            parents,
                            recipe=b,
                            machine_only=True,
                            chance=chance,
                        ),
                    )
                )
                continue
            outcome = self.attempt(parents, frozenset(parents))
            p = outcome.chance(cid)
            if p <= 0:
                continue
            secs = self._wait(parents, p)
            out.append(
                (
                    _add(base, (0, 0, 1, secs)),
                    _Option("sticks", cid, parents, outcome, recipe=b, chance=p),
                )
            )
        return out

    def pool_options(
        self, cid: str, costs: Mapping[str, Cost]
    ) -> list[tuple[Cost, _Option]]:
        out: list[tuple[Cost, _Option]] = []
        for pool, members in sorted(self.pools.items()):
            if cid not in members:
                continue
            known = sorted(
                (m for m in members if m in costs and m != cid),
                key=lambda m: (costs[m], m),
            )[:POOL_CANDIDATES]
            for i, a in enumerate(known):
                for b in known[i:]:
                    if (
                        self.crop(a).breeding_threshold < 0
                        or self.crop(b).breeding_threshold < 0
                    ):
                        continue
                    outcome = self.attempt((a, b), None)
                    p = outcome.chance(cid)
                    if p <= 0:
                        continue
                    base = costs[a] if a == b else _add(costs[a], costs[b])
                    secs = self._wait((a, b), p)
                    out.append(
                        (
                            _add(base, (1, 0, 1, secs)),
                            _Option("pool", cid, (a, b), outcome, chance=p),
                        )
                    )
        return out

    def _wait(self, parents: Sequence[str], p: Fraction) -> float:
        ready = [self.ready_ticks(x) for x in parents]
        if any(t is None for t in ready):
            return float("inf")
        ticks = max(t for t in ready if t is not None) + 1 / float(p)
        return ticks * rules.TICK_RATE / rules.TICKS_PER_SECOND

    def solve(self) -> dict[str, tuple[Cost, _Option]]:
        best: dict[str, tuple[Cost, _Option]] = {}
        for _ in range(len(self.crops) + 1):
            costs = {c: v[0] for c, v in best.items()}
            changed = False
            for cid in self.crops:
                opts = self.options(cid, costs)
                if not any(
                    o.kind in ("have", "obtain", "sticks", "breeder") for _, o in opts
                ):
                    opts += self.pool_options(cid, costs)
                if not opts:
                    continue
                pick = min(opts, key=lambda co: (co[0], co[1].kind, co[1].parents))
                if cid not in best or pick[0] < best[cid][0]:
                    best[cid] = pick
                    changed = True
            if not changed:
                break
        return best


# --- writing the steps -----------------------------------------------------------------------


def _layout(p: _Planner, parents: Sequence[str], child: str) -> list[list[Cell]]:
    """A 3x3 grid: the double stick in the middle, parents on its north, south, east and west
    (corners are empty: diagonals don't count)."""
    grid = [[Cell("empty") for _ in range(3)] for _ in range(3)]
    spots = [(0, 1), (2, 1), (1, 2), (1, 0)]  # north, south, east, west
    for (r, c), cid in zip(spots, parents, strict=False):
        grid[r][c] = Cell("parent", cid, p.f.crop_name(cid), _under(p, cid))
    grid[1][1] = Cell("double", child, p.f.crop_name(child), _under(p, child))
    return grid


def _under(p: _Planner, cid: str) -> str | None:
    subs = p.crop(cid).sub_soils
    if not subs:
        return None
    names = [p.f.names.get(f"{r.name}@{r.meta}", r.text()) for r in subs]
    return " or ".join(dict.fromkeys(names[:3])) + (
        " (or another listed)" if len(names) > 3 else ""
    )


def _requirements(p: _Planner, cid: str, role: str) -> list[Requirement]:
    c = p.crop(cid)
    ref = c.ref
    out: list[Requirement] = []
    soils = ", ".join(c.soil_lists) or c.soil
    out.append(
        Requirement(
            "soil",
            "check",
            Line(f"{role} {c.name}: soil {soils}", refs=(ref,)),
        )
    )
    if c.sub_soils:
        keys = {f"{r.name}@{r.meta}" for r in c.sub_soils}
        have = sorted(keys & p.stock)
        under = _under(p, cid) or "its block"
        text = next((r.text for r in c.requirements if r.kind == "sub_soil"), "")
        out.append(
            Requirement(
                "sub_soil",
                "met" if have else "needed",
                Line(
                    f"{role} {c.name}: {text or 'a block under it'}: {under} directly under "
                    "the soil block" + (" (in My base's stock)" if have else ""),
                    refs=(ref,),
                ),
            )
        )
    for r in c.requirements:
        if r.kind in ("light_min", "light_max"):
            out.append(
                Requirement(
                    "light", "check", Line(f"{role} {c.name}: {r.text}", refs=(ref,))
                )
            )
        elif r.kind == "other":
            out.append(
                Requirement(
                    "other", "check", Line(f"{role} {c.name}: {r.text}", refs=(ref,))
                )
            )
    need = rules.nutrients_needed(c.tier)
    have_n = p.nutrients()
    rate = rules.growth_rate(have_n, c.tier, p.stats_of(cid)[0])
    nums = (
        _count(need, "points", (ref,)),
        _count(have_n, "points", (ref,)),
    )
    if rate <= 0:
        status, tail = "needed", ": too few, it gets sick; add liked biomes or humidity"
    elif have_n >= need:
        status, tail = "met", ""
    else:
        status, tail = "check", ": it grows, but slower than its base speed"
    out.append(
        Requirement(
            "nutrients",
            status,
            Line(
                f"{role} {c.name}: needs {{0}} for full speed; the assumed setup gives {{1}}"
                + tail,
                nums,
            ),
        )
    )
    return out


def _sticks_step(p: _Planner, n: int, o: _Option) -> Step:
    child = p.crop(o.child)
    parents = list(o.parents)
    if o.kind == "pool" and len(set(parents)) == 1:
        parents = [parents[0], parents[0]]
    refs = tuple(dict.fromkeys([child.ref, *(p.crop(x).ref for x in parents)]))
    assert o.outcome is not None
    grows = {c: q for c, q in o.outcome.children.items() if p.grows_here(c, o.child)}
    any_child = sum(grows.values(), Fraction(0))
    pt = o.chance
    step = Step(
        n,
        o.kind,
        o.child,
        child.name,
        tuple(parents),
        refs=refs,
    )
    names = " + ".join(p.f.crop_name(x) for x in parents)
    if o.kind == "pool":
        pools = [
            pool
            for pool, m in sorted(p.pools.items())
            if o.child in m and all(x in m for x in parents)
        ]
        step.title = (
            f"Breed {child.name} from {names} (mutation pool {', '.join(pools[:2])})"
        )
    else:
        step.title = f"Breed {child.name} from {names} (parent-combination recipe)"
    step.layout = _layout(p, parents, o.child)
    for x in dict.fromkeys(parents):
        step.requirements += _requirements(p, x, "Parent")
    step.requirements += _requirements(p, o.child, "Child")
    ready = [p.ready_ticks(x) for x in dict.fromkeys(parents)]
    ready_s = max((t or 0) for t in ready) * rules.TICK_RATE / rules.TICKS_PER_SECOND
    expected_ticks = 1 / float(pt)
    secs = expected_ticks * rules.TICK_RATE / rules.TICKS_PER_SECOND
    t90 = rules.attempts_for(pt, CONFIDENCE) or 0
    step.expected_s = ready_s + secs
    step.numbers += [
        Line(
            "Chance per growth tick (every {0}): {1}",
            (_count(rules.TICK_RATE, "ticks", refs), _pct(pt, refs)),
        ),
        Line(
            "Parents reach their breeding threshold ({0} grown) after {1}",
            (
                _pct(max(p.crop(x).breeding_threshold for x in parents), refs),
                _secs(ready_s, refs),
            ),
        ),
        Line(
            "Then {0} expected, {1} for a {2} chance",
            (
                _secs(secs, refs),
                _secs(t90 * rules.TICK_RATE / rules.TICKS_PER_SECOND, refs),
                _pct(CONFIDENCE, refs),
            ),
        ),
    ]
    others = (any_child - pt) / pt if pt else Fraction(0)
    if others > 0:
        step.numbers.append(
            Line(
                "Expect {0} other children to clear before it (copies of a parent, or other "
                "mutations)",
                (_count(float(others), "", refs, 1),),
            )
        )
    weed = (1 - any_child) / rules.WEED_SPAWN_CHANCE
    weed_first = weed / (weed + any_child) if (weed + any_child) else Fraction(0)
    step.warnings.append(
        Line(
            "Weeds: an empty double stick grows a weed {0} of growth ticks; {1} chance one "
            "comes before any child. Weed-EX in the sticks stops it; pull weeds at once",
            (_pct(Fraction(1, rules.WEED_SPAWN_CHANCE), refs), _pct(weed_first, refs)),
        )
    )
    if o.outcome.ambiguous:
        step.warnings.append(
            Line(
                "A smaller recipe of these parents may also match (CropsNH's registration "
                "order decides): the chance shown is its lowest",
                refs=refs,
            )
        )
    extra = [x for x in DIRECTIONS[len(parents) :]]
    if extra:
        step.warnings.append(
            Line(
                f"Keep {' and '.join(extra)} of the double stick free of other crops: an "
                "extra species can block the recipe or change the odds",
                refs=refs,
            )
        )
    for x in dict.fromkeys(parents):
        c = p.crop(x)
        g, _, r = p.stats_of(x)
        if rules.spreads_weeds(g, r, c.weeds_above):
            step.warnings.append(
                Line(
                    f"{c.name} spreads weeds: Growth above {c.weeds_above} with Resistance "
                    "below Growth ({0} a growth tick once mature)",
                    (_pct(rules.weed_spread_chance(g), (c.ref,)),),
                    refs=(c.ref,),
                )
            )
    if o.recipe is not None and p.tier >= o.recipe.voltage_tier and p.tier >= rules.LV:
        step.numbers.append(
            Line(
                "At your tier the Crop Breeder makes it instead: {0} a run (see its step)",
                (_pct(Fraction(rules.breeder_chance(p.tier), 100), (o.recipe.ref,)),),
            )
        )
    step.keep += _keep(p, o.child)
    return step


def _keep(p: _Planner, cid: str) -> list[Line]:
    c = p.crop(cid)
    variations = rules.stat_variations(False, p.fertilizer())
    g = p.stats_of(cid)
    return [
        Line(
            "Check each child with the Plant Lens; keep the best and replace the weakest parent",
            refs=(c.ref,),
        ),
        Line(
            "Removing a crop (or a spade harvest) returns its seed {0} of the time at "
            "Resistance {1}: leave a good child planted where it is when you can",
            (
                _pct(rules.seed_drop_chance(g[2]), (c.ref,)),
                _count(g[2], "", (c.ref,)),
            ),
        ),
        Line(
            "Child stats: the parents' average plus one of {0} steps at the assumed fertilizer "
            "({1})",
            (
                _count(len(variations), "", (c.ref,)),
                _count(p.fertilizer(), "", (c.ref,)),
            ),
        ),
    ]


def _breeder_step(p: _Planner, n: int, o: _Option) -> Step:
    child = p.crop(o.child)
    b = o.recipe
    run_tier = b.voltage_tier if b else rules.LV
    # the machine he has (his tier), else the lowest that runs the recipe
    tier = max(p.tier, run_tier, rules.LV)
    machine = p.f.machine("crop_breeder", tier) or p.f.lowest("crop_breeder")
    mref = (machine.ref,) if machine else ()
    refs = tuple(
        dict.fromkeys(
            [
                child.ref,
                *(p.crop(x).ref for x in o.parents),
                *mref,
                *((b.ref,) if b else ()),
            ]
        )
    )
    names = " + ".join(p.f.crop_name(x) for x in o.parents)
    step = Step(n, "breeder", o.child, child.name, o.parents, refs=refs)
    step.title = f"Crop Breeder: {child.name} from {names}"
    step.layout = []
    stats = rules.breeder_child_stats([p.stats_of(x) for x in o.parents])
    chance = rules.breeder_chance(tier)
    fert = rules.breeder_fertilizer(child.tier, *stats)
    runs = rules.runs_for(chance, BREEDER_CONFIDENCE)
    max_sum = rules.breeder_max_stat_sum(tier, child.tier)
    mrefs = tuple(dict.fromkeys([*mref, child.ref]))
    step.requirements.append(
        Requirement(
            "machine",
            "met" if p.tier >= run_tier and p.tier >= rules.LV else "needed",
            Line(
                f"A Crop Breeder of {TIER_NAMES[min(run_tier, 9)]} or above"
                + (f" ({machine.name})" if machine else ""),
                refs=refs,
            ),
        )
    )
    step.requirements.append(
        Requirement(
            "analyzed",
            "check",
            Line("Its parents' seeds must be analyzed (scanned) first", refs=refs),
        )
    )
    if b is not None:
        for name, amount in b.catalysts:
            step.requirements.append(
                Requirement(
                    "catalyst",
                    "check",
                    Line(
                        f"Catalyst: {{0}} x {name}",
                        (_count(amount, "", (b.ref,)),),
                        refs=(b.ref,),
                    ),
                )
            )
    step.numbers += [
        Line(
            "Success chance at your tier: {0}; a failure uses up the parents and the "
            "fertilizer",
            (_pct(Fraction(chance, 100), mrefs),),
        ),
        Line(
            "Enriched fertilizer per run: {0} (for {1} of Growth + Gain + Resistance)",
            (
                _count(fert, "mB", (child.ref, *mref)),
                Num(
                    sum(stats), "stat points", (child.ref,), f"{sum(stats)} stat points"
                ),
            ),
        ),
        Line(
            "Make {0} of each parent first (Seed Generator): enough runs to succeed with a "
            "{1} chance",
            (
                _count(runs, "copies", mrefs),
                _pct(BREEDER_CONFIDENCE, mrefs),
            ),
        ),
        Line(
            "Highest Growth + Gain + Resistance its tank allows for this child: {0}",
            (_count(max_sum, "stat points", mrefs),),
        ),
    ]
    if b is not None:
        step.numbers.append(
            Line(
                "One run: {0} at {1}",
                (
                    _secs(b.duration_ticks / rules.TICKS_PER_SECOND, (b.ref,)),
                    _count(b.eut, "EU/t", (b.ref,)),
                ),
            )
        )
        step.expected_s = b.duration_ticks / rules.TICKS_PER_SECOND * 100 / chance
    gen = p.f.machine("seed_generator", tier) or p.f.lowest("seed_generator")
    if gen is not None:
        pstats = [p.stats_of(x) for x in o.parents]
        step.numbers.append(
            Line(
                "A Seed Generator copy costs {0} for the costliest parent; its tank allows "
                "{1} stat points",
                (
                    _count(
                        max(rules.seed_generator_fertilizer(*s) for s in pstats),
                        "mB",
                        (gen.ref,),
                    ),
                    _count(
                        rules.seed_generator_max_stat_sum(max(gen.tier, rules.LV)),
                        "",
                        (gen.ref,),
                    ),
                ),
            )
        )
    if sum(stats) > max_sum:
        step.warnings.append(
            Line(
                "Its tank can't hold one run's fertilizer at these stats: use a higher-tier "
                "Crop Breeder",
                refs=mrefs,
            )
        )
    step.warnings.append(
        Line(
            "A failed run destroys its inputs: never load the last seed of a parent",
            refs=mrefs,
        )
    )
    return step


def _obtain_step(p: _Planner, n: int, o: _Option) -> Step:
    c = p.crop(o.child)
    item = p.f.names.get(o.seed or "", o.seed or "")
    step = Step(n, "obtain", o.child, c.name, refs=(c.ref,))
    step.title = f"Plant {item} to start {c.name}"
    step.numbers.append(
        Line(
            "It plants the crop at {0}/{0}/{0}",
            (Num(rules.MIN_STAT, "stat", (c.ref,), str(rules.MIN_STAT)),),
        )
    )
    step.requirements += _requirements(p, o.child, "Crop")
    step.expected_s = 0.0
    return step


def _manager_step(p: _Planner, n: int, target: str) -> Step | None:
    m = p.f.machine("crop_manager", p.tier)
    if m is None or p.tier < rules.LV:
        return None
    c = p.crop(target)
    refs = (m.ref, c.ref)
    radius = rules.crop_manager_radius(m.tier)
    step = Step(n, "crop_manager", target, c.name, refs=refs)
    step.title = (
        f"{m.name}: water, fertilizer past 100 and Weed-EX for the breeding field"
    )
    step.layout = []
    step.numbers += [
        Line(
            "It reaches {0} on each side ({1} square) and {2} up and down",
            (
                _count(radius, "blocks", refs),
                _count(2 * radius + 1, "blocks", refs),
                _count(rules.CROP_MANAGER_VERTICAL_RADIUS, "blocks", refs),
            ),
        ),
        Line(
            "It tops sticks up to {0} fertilizer; at {1} or more no child stat drops: each "
            "gains up to {3} ({2} of the time nothing)",
            (
                _count(rules.CROP_MANAGER_FERTILIZER_CAP, "", refs),
                _count(rules.HIGH_FERTILIZER, "", refs),
                _pct(
                    Fraction(rules.HIGH_BREEDING.count(0), len(rules.HIGH_BREEDING)),
                    refs,
                ),
                _count(max(rules.HIGH_BREEDING), "", refs),
            ),
        ),
    ]
    return step


def _stat_plan(p: _Planner, target: str, aim: Stats) -> StatPlan:
    c = p.crop(target)
    fert = p.fertilizer()
    variations = rules.stat_variations(False, fert)
    plan = StatPlan(aim, variations)
    start = p.stats_of(target)
    outcome = p.attempt((target,) * STAT_PARENTS, None)
    per_tick = outcome.chance(target)
    ready = p.ready_ticks(target) or 0
    refs = (c.ref,)
    names = ("Growth", "Gain", "Resistance")
    worst = 0.0
    for i, name in enumerate(names):
        gens = rules.generations_to(start[i], aim[i], STAT_PARENTS, variations)
        if gens is None:
            plan.lines.append(
                Line(f"{name}: can't reach the target at this fertilizer", refs=refs)
            )
            continue
        worst = max(worst, gens)
        plan.lines.append(
            Line(
                f"{name} {{0}} to {{1}}: about {{2}} children",
                (
                    _count(start[i], "", refs),
                    _count(aim[i], "", refs),
                    _count(gens, "", refs, 1),
                ),
            )
        )
    if per_tick > 0:
        per_child = 1 / float(per_tick) + ready
        secs = worst * per_child * rules.TICK_RATE / rules.TICKS_PER_SECOND
        plan.lines.append(
            Line(
                "With {0} of it around a double stick: {1} a copy per growth tick; the slowest "
                "stat takes about {2}",
                (
                    _count(STAT_PARENTS, "", refs),
                    _pct(per_tick, refs),
                    _secs(secs, refs),
                ),
            )
        )
    plan.lines.append(
        Line(
            "Children vary by one of {0} steps at {1} fertilizer"
            + (
                " (a Crop Manager)"
                if fert > rules.MANUAL_FERTILIZER_MAX
                else " (by hand)"
            ),
            (_count(len(variations), "", refs), _count(fert, "", refs)),
        )
    )
    if aim[0] > c.weeds_above:
        plan.warnings.append(
            Line(
                f"{c.name} spreads weeds once Growth passes {c.weeds_above} while Resistance "
                "is lower: raise Resistance first and keep it at or above Growth",
                refs=refs,
            )
        )
    if p.tier < rules.HV:
        b = p.f.machine("crop_breeder", max(p.tier, rules.LV))
        if b is not None:
            plan.warnings.append(
                Line(
                    "Your Crop Breeder's tank allows at most {0} stat points for this crop: "
                    "raise stats on crop sticks",
                    (
                        _count(
                            rules.breeder_max_stat_sum(b.tier, c.tier),
                            "",
                            (b.ref, c.ref),
                        ),
                    ),
                )
            )
    return plan


def _steps(
    p: _Planner, best: Mapping[str, tuple[Cost, _Option]], target: str
) -> list[Step]:
    order: list[str] = []
    seen: set[str] = set()

    def visit(cid: str) -> None:
        if cid in seen:
            return
        seen.add(cid)
        _, o = best[cid]
        for x in dict.fromkeys(o.parents):
            visit(x)
        order.append(cid)

    visit(target)
    steps: list[Step] = []
    for cid in order:
        _, o = best[cid]
        n = len(steps) + 1
        if o.kind == "have":
            continue
        if o.kind == "obtain":
            steps.append(_obtain_step(p, n, o))
        elif o.kind == "breeder":
            steps.append(_breeder_step(p, n, o))
        else:
            steps.append(_sticks_step(p, n, o))
    return steps


def guide(
    facts: Facts,
    target: str,
    have: Mapping[str, Stats] | None = None,
    tier: int = 0,
    *,
    setup: Setup | None = None,
    aim: Stats = DEFAULT_TARGET,
    alternate_seeds: bool = True,
    stock: Iterable[str] = (),
    owned: Iterable[int] = (),
) -> Guide:
    """The guide to ``target`` (a crop id) from ``have`` (crop id -> best stats) at GT voltage
    tier ``tier`` (0 before LV). ``stock``: My base's item keys (``registry@meta``);
    ``owned``: the GT mte ids of the machines My base lists."""
    if target not in facts.data.crops:
        raise GuideError(f"no crop {target!r} in the crop data (run {facts.run_key})")
    p = _Planner(
        facts, have or {}, tier, setup or Setup(), alternate_seeds, frozenset(stock)
    )
    best = p.solve()
    c = p.crop(target)
    if target not in best:
        raise GuideError(
            f"no way to {c.name} ({c.ref}) from your crops: no recipe or pool reaches it"
        )
    steps = _steps(p, best, target)
    manager = _manager_step(p, len(steps) + 1, target)
    if manager is not None:
        steps.insert(0, manager)
        for i, s in enumerate(steps, 1):
            s.n = i
    managers = {m.mte_id for m in facts.machines.get("crop_manager", [])}
    has_manager = bool(managers & set(owned))
    for s in steps:
        # a crop he has is never a step (the path starts from it); a Crop Manager step is done
        # once My base lists one
        s.done = has_manager if s.kind == "crop_manager" else s.child in p.have
    total_s = sum(s.expected_s or 0.0 for s in steps)
    total = (
        Line(
            "Expected time for every step, one after another: {0}",
            (
                _secs(
                    total_s,
                    tuple(
                        dict.fromkeys(
                            r for s in steps for r in s.refs if r.startswith("crop:")
                        )
                    ),
                ),
            ),
        )
        if steps
        else None
    )
    notes = [
        Line(
            "Odds and times are computed from CropsNH's rules and these crops' exported "
            "numbers, with the setup assumed: watered, sky access, no liked biome",
            refs=(c.ref,),
        )
    ]
    return Guide(
        target,
        c.name,
        tier,
        facts.run_key,
        steps,
        _stat_plan(p, target, aim),
        total,
        notes,
    )


# --- the validator ---------------------------------------------------------------------------


def _walk(obj: object) -> Iterator[object]:
    yield obj
    if is_dataclass(obj) and not isinstance(obj, type):
        for f in fields(obj):
            yield from _walk(getattr(obj, f.name))
    elif isinstance(obj, list | tuple):
        for x in obj:
            yield from _walk(x)
    elif isinstance(obj, dict):
        for x in obj.values():
            yield from _walk(x)


def check(g: Guide, facts: Facts) -> list[str]:
    """Problems with ``g``'s citations (empty: every number cited, WO-6.7's acceptance).

    Every :class:`Num` has at least one citation; every citation is well formed and names
    something in the DB (a crop of the run, a Crop Breeder recipe, a CropsNH machine); a
    :class:`Line` holds digits outside its numbers only when it cites the DB text they come
    from."""
    crops = facts.data.crops
    machines = {m.ref for ms in facts.machines.values() for m in ms}
    problems: list[str] = []

    def known(ref: str) -> bool:
        if not CITE.match(ref):
            return False
        if ref.startswith("crop:"):
            return ref.removeprefix("crop:") in crops
        if ref.startswith("r:"):
            return ref in facts.recipe_refs
        if ref.startswith("mte:"):
            return ref in machines
        return False

    for obj in _walk(g):
        if isinstance(obj, Num):
            if not obj.refs:
                problems.append(f"uncited number {obj.text!r}")
            for r in obj.refs:
                if not known(r):
                    problems.append(
                        f"number {obj.text!r} cites {r!r}, which the DB lacks"
                    )
        elif isinstance(obj, Line):
            bare = re.sub(r"\{[0-9]+\}", "", obj.template)
            if DIGIT.search(bare) and not obj.refs:
                problems.append(f"digits in an uncited line: {obj.template!r}")
            for r in obj.refs:
                if not known(r):
                    problems.append(f"line cites {r!r}, which the DB lacks")
    return problems


# --- for the API, the tools and the view ----------------------------------------------------


def step_dict(s: Step) -> dict[str, Any]:
    return {
        "n": s.n,
        "kind": s.kind,
        "child": s.child,
        "child_name": s.child_name,
        "ref": f"crop:{s.child}",
        "parents": [{"crop": x, "ref": f"crop:{x}"} for x in s.parents],
        "title": s.title,
        "layout": [
            [
                {"role": c.role, "crop": c.crop, "name": c.name, "under": c.under}
                for c in row
            ]
            for row in s.layout
        ],
        "requirements": [
            {"what": r.what, "status": r.status, **r.line.as_dict()}
            for r in s.requirements
        ],
        "numbers": [x.as_dict() for x in s.numbers],
        "warnings": [x.as_dict() for x in s.warnings],
        "keep": [x.as_dict() for x in s.keep],
        "refs": list(s.refs),
        "done": s.done,
    }


def guide_dict(g: Guide) -> dict[str, Any]:
    nxt = g.next_step()
    return {
        "target": g.target,
        "target_name": g.target_name,
        "ref": f"crop:{g.target}",
        "tier": g.tier,
        "tier_name": TIER_NAMES[min(g.tier, 9)] if g.tier > 0 else "Steam",
        "run": g.run_key,
        "steps": [step_dict(s) for s in g.steps],
        "next": nxt.n if nxt is not None else None,
        "total": g.total.as_dict() if g.total else None,
        "stats": None
        if g.stats is None
        else {
            "target": list(g.stats.target),
            "variations": list(g.stats.variations),
            "lines": [x.as_dict() for x in g.stats.lines],
            "warnings": [x.as_dict() for x in g.stats.warnings],
        },
        "notes": [x.as_dict() for x in g.notes],
    }
