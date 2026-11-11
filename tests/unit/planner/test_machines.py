"""The machine model and selection (WO-3.2): table tests of recipe x machine x tier.

Each row of :data:`CASES` is one recipe, one machine (its DB row and ``machine_fact`` rows, as
D109 writes them) and the player's progress, with the expected outcome: the options it gives,
each sized by GT's math (:mod:`gregtec.planner.gtmath`, WO-3.1) set up by hand from the facts'
meaning, or the reason it is rejected. A few rows also pin literal numbers worked out by hand
from GT 5.09.54.265's rules, so the table can't agree with the model by sharing a mistake.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from gregtec import store
from gregtec.db import access
from gregtec.ingest.machine_facts import Fact
from gregtec.planner import machines as m
from gregtec.planner.gtmath.jmath import INT_MAX
from gregtec.planner.gtmath.overclock import UNLIMITED_TIER_SKIPS, Overclock, calculate
from gregtec.planner.gtmath.processing import (
    MAX_BATCH_SIZE,
    EnergyHatch,
    Logic,
    Recipe,
    Run,
    basic_machine,
    multiblock_power,
    process,
    total_eu_power,
)
from gregtec.planner.gtmath.tiers import V, coil_heat, ebf_heat, get_tier

LV, MV, HV, EV, IV = 1, 2, 3, 4, 5


def fact(
    key: str, value: str, conf: float = 0.95, source: str = "curated", note: str = ""
) -> Fact:
    return Fact(key, value, source, conf, note)


def facts(*rows: Fact) -> dict[str, Fact]:
    return {f.key: f for f in rows}


def logic_facts(
    parallel: str = "1",
    speed: str = "1",
    eu: str = "1",
    oc: str = "standard",
    *extra: Fact,
    batch: bool = False,
) -> dict[str, Fact]:
    return facts(
        fact("processing", "logic"),
        fact("parallel", parallel),
        fact("speed", speed),
        fact("eu_modifier", eu),
        fact("oc", oc),
        fact("batch_mode", "true" if batch else "false", 1.0, "code"),
        *extra,
    )


def recipe(
    eut: int,
    duration: int,
    recipe_map: str = "gt.recipe.a",
    special: int | None = 0,
    amps: int = 1,
    **meta: float,
) -> m.RecipeInfo:
    return m.RecipeInfo(
        ref=f"r:{recipe_map}/{eut:06d}{duration:06d}",
        map=recipe_map,
        eut=eut,
        duration=duration,
        map_amperage=amps,
        special_value=special,
        meta=meta,
    )


def single(mte: int, tier: int, recipe_map: str = "gt.recipe.a") -> m.MachineInfo:
    return m.MachineInfo(
        mte, f"Single {tier}", m.BASIC_MACHINE, "gregtech", False, tier, (recipe_map,)
    )


def steam(mte: int, variant: str, recipe_map: str = "gt.recipe.a") -> m.MachineInfo:
    cls = f"gregtech.common.tileentities.machines.steam.MTESteamMacerator{variant}"
    return m.MachineInfo(
        mte, f"Steam {variant}", cls, "gregtech", False, 1, (recipe_map,)
    )


def multi(
    mte: int,
    f: dict[str, Fact],
    maps: tuple[str, ...] = ("gt.recipe.a",),
    mod: str = "gregtech",
    addon: bool = False,
) -> m.MachineInfo:
    return m.MachineInfo(
        mte, f"Multi {mte}", f"test.Multi{mte}", mod, True, None, maps, f, addon
    )


def hatches(tier: int, n: int = 1) -> tuple[EnergyHatch, ...]:
    return (EnergyHatch(tier),) * n


def mlogic(hs: Sequence[EnergyHatch], **kw: object) -> Logic:
    """A plain multiblock's processing logic with ``hs`` (GT's default power rule)."""
    return Logic(power=multiblock_power(hs), **kw)  # type: ignore[arg-type]


def run(r: m.RecipeInfo, logic: Logic) -> Run:
    return process(Recipe(eut=r.eut, duration=r.duration, heat=r.heat), logic)


Expected = list[tuple[tuple[EnergyHatch, ...] | int, Run]]


@dataclass(frozen=True)
class Case:
    id: str
    recipe: m.RecipeInfo
    machine: m.MachineInfo
    player: m.Player
    # The options, by hatch layout (multiblocks) or machine tier, each with GT's run; or the
    # rejection's reason (a substring).
    expect: Callable[[], Expected] | str
    coil: int | None = None  # the coil tier every option is sized with
    badges: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    literal: tuple[int, int, int] | None = (
        None  # (EU/t, ticks, parallel) of the first option
    )
    batch: bool = False
    alternative: Callable[[], Run] | None = None  # the other state of the first option
    tags: frozenset[str] = field(default_factory=frozenset)


R_LOW = recipe(2, 400)  # a 2 EU/t macerator recipe
R_LV = recipe(30, 100)
R_EBF = recipe(120, 500, special=1500)  # steel-like: 1500 K
R_HOT = recipe(1920, 1000, special=4500)
R_CLEAN = recipe(30, 200, special=-200, cleanroom=1.0)
R_GRAV = recipe(30, 200, special=-100, low_gravity=1.0)
R_SHORT = recipe(30, 10)
R_TWO_AMP = recipe(60, 100, amps=2)
R_MV = recipe(120, 200)

EBF_HEAT = fact("machine_heat", "coil_heat + 100*(vt - 2)")
EBF = multi(1000, logic_facts("1", "1", "1", "heat", EBF_HEAT))
LCR = multi(1001, logic_facts("1", "1", "1", "perfect", batch=True))
PYRO = multi(1002, logic_facts("1", "(1 + coil_tier)/2", "1", "standard"))
MEGA = multi(
    1003,
    logic_facts(
        "256",
        "1",
        "1",
        "heat",
        fact("machine_heat", "coil_heat + 100*(vt_eu - 2)"),
        fact("power", "total_eu"),
        fact("tier_skips", "unlimited"),
    ),
)
CENTRIFUGE = multi(1004, logic_facts("floor((4 + 4*ramp)*vt)", "2 + ramp", "0.9"))
LIMITED = multi(
    1005, logic_facts("1", "1", "1", "limited_perfect", fact("perfect_overclocks", "2"))
)
CUSTOM_OC = multi(
    1006,
    logic_facts(
        "1",
        "1",
        "1",
        "custom",
        fact("oc_eu_mul", "2"),
        fact("oc_time_div", "2"),
        fact("max_overclocks", "1"),
    ),
)
NO_OC = multi(1007, logic_facts("8", "1/1.6", "1.25", "none"))
FLAGGED = multi(
    1008,
    facts(
        fact("processing", "logic", 0.5, "tooltip"),
        fact("parallel", "1", 0.5, "tooltip"),
        fact("oc", "standard", 0.5, "tooltip"),
        fact(
            "flag", "review", 0.9, note="parallels depend on a state no planner models"
        ),
    ),
)
PARTIAL = multi(
    1009,
    logic_facts("1", "1", "1", "standard", fact("partial", "outputs", note="drops")),
)
GENERATOR = multi(
    1010,
    facts(fact("processing", "generator"), fact("parallel", "n/a"), fact("oc", "n/a")),
)
FIXED = multi(1011, logic_facts("8", "1", "1", "none", fact("power", "fixed")))
CASING = multi(1012, logic_facts("2*casing_tier", "1", "1", "standard"))
PER_MAP = multi(
    1013,
    logic_facts("1", "1", "1", "standard", fact("parallel[gt.recipe.b]", "4")),
    maps=("gt.recipe.a", "gt.recipe.b"),
)
EXOTIC = multi(
    1014, logic_facts("4", "1", "1", "standard", fact("exotic_hatches", "multiamp"))
)
ADDON = multi(
    1015, logic_facts("2", "1", "1", "standard"), mod="otheraddon", addon=True
)
BATCH = multi(1016, logic_facts("4", "1", "1", "standard", batch=True))
NO_BATCH = multi(1017, logic_facts("4", "1", "1", "standard", batch=False))
CUSTOM_PROC = multi(
    1018,
    facts(
        fact("processing", "custom"),
        fact("parallel", "1"),
        fact("speed", "1"),
        fact("eu_modifier", "1"),
        fact("oc", "standard"),
    ),
)
DEPRECATED = multi(
    1019, logic_facts("1", "1", "1", "standard", fact("deprecated", "true"))
)
SCANNER = m.MachineInfo(
    1020,
    "Scanner",
    "gregtech.common.tileentities.machines.basic.MTEScanner",
    "gregtech",
    False,
    1,
    ("gt.recipe.a",),
)

ZERO_PARALLEL = multi(1021, logic_facts("0", "1", "1", "standard"))
LATEX = multi(1022, logic_facts("1 + 8*singularity", "1", "1", "standard"))
LASER = multi(
    1023, logic_facts("4", "1", "1", "standard", fact("exotic_hatches", "laser"))
)
BOXINATOR = m.MachineInfo(
    1024,
    "Packager",
    "gregtech.common.tileentities.machines.basic.MTEBoxinator",
    "gregtech",
    False,
    MV,
    ("gt.recipe.a",),
)

PLAYER_LV = m.Player(tier=LV)
PLAYER_MV = m.Player(tier=MV)
PLAYER_HV = m.Player(tier=HV)
PLAYER_EV = m.Player(tier=EV)


def every_layout(
    top: int, make: Callable[[tuple[EnergyHatch, ...]], Run], low: int = 1
) -> Expected:
    out: Expected = []
    for t in range(low, top + 1):
        for n in (1, 2):
            hs = hatches(t, n)
            r = make(hs)
            if r.ok:
                out.append((hs, r))
    return out


def ebf_logic(hs: Sequence[EnergyHatch], coil: int) -> Logic:
    heat = ebf_heat(coil, sum(h.voltage for h in hs))
    return mlogic(
        hs, heat_oc=True, heat_discount=True, machine_heat=heat, check_heat=True
    )


CASES: list[Case] = [
    # --- single blocks: tier and amperage rules (MTEBasicMachine) ---
    Case(
        "single LV runs a 2 EU/t recipe without overclocking",
        R_LOW,
        single(301, LV),
        PLAYER_LV,
        lambda: [(LV, basic_machine(Recipe(2, 400), LV))],
        literal=(2, 400, 1),
    ),
    Case(
        "single MV overclocks a 2 EU/t recipe once",
        R_LOW,
        single(302, MV),
        PLAYER_MV,
        lambda: [(MV, basic_machine(Recipe(2, 400), MV))],
        literal=(8, 200, 1),
    ),
    Case(
        "single HV at HV overclocks a 30 EU/t recipe twice",
        R_LV,
        single(303, HV),
        PLAYER_HV,
        lambda: [(HV, basic_machine(Recipe(30, 100), HV))],
        literal=(480, 25, 1),
    ),
    Case(
        "single above the player's tier",
        R_LV,
        single(303, HV),
        PLAYER_MV,
        "above the player's tier",
    ),
    Case(
        "single LV can't find a 120 EU/t recipe",
        R_MV,
        single(301, LV),
        PLAYER_EV,
        "no_recipe",
    ),
    Case(
        "single LV on a 2 A map finds a 60 EU/t recipe",
        R_TWO_AMP,
        single(301, LV),
        PLAYER_LV,
        lambda: [(LV, basic_machine(Recipe(60, 100), LV, amperage=2, map_amperage=2))],
        literal=(60, 100, 1),
    ),
    Case(
        "single needs a cleanroom",
        R_CLEAN,
        single(301, LV),
        PLAYER_LV,
        lambda: [(LV, basic_machine(Recipe(30, 200), LV))],
        requires=("cleanroom",),
    ),
    Case(
        "single without a cleanroom",
        R_CLEAN,
        single(301, LV),
        m.Player(tier=LV, cleanroom=False),
        "needs a cleanroom",
    ),
    Case(
        "single without low gravity", R_GRAV, single(301, LV), PLAYER_LV, "low gravity"
    ),
    Case(
        "single in space",
        R_GRAV,
        single(301, LV),
        m.Player(tier=LV, low_gravity=True),
        lambda: [(LV, basic_machine(Recipe(30, 200), LV))],
        requires=("low_gravity",),
    ),
    Case(
        "single block with its own recipe check is not modelled",
        R_LV,
        SCANNER,
        PLAYER_LV,
        "not modelled",
    ),
    # --- steam single blocks (SteamOverclockDescriber) ---
    Case(
        "bronze steam: duration x2",
        R_LOW,
        steam(106, "Bronze"),
        m.Player(tier=0),
        lambda: [(0, Run(m.Check.SUCCESS, eut=2, duration=800, parallel=1))],
        literal=(2, 800, 1),
    ),
    Case(
        "steel steam: EU/t x2",
        R_LOW,
        steam(107, "Steel"),
        m.Player(tier=0),
        lambda: [(0, Run(m.Check.SUCCESS, eut=4, duration=400, parallel=1))],
        literal=(4, 400, 1),
    ),
    Case(
        "steam before the steam stage",
        R_LOW,
        steam(106, "Bronze"),
        m.Player(tier=0, steam=False),
        "steam stage",
    ),
    Case(
        "steam can't find a 120 EU/t recipe",
        R_MV,
        steam(106, "Bronze"),
        PLAYER_EV,
        "up to 32 EU/t",
    ),
    # --- multiblocks: hatch layouts, amperage, overclock kinds ---
    Case(
        "LCR: perfect overclocks, one hatch runs at 1 A",
        R_LV,
        LCR,
        PLAYER_HV,
        lambda: every_layout(
            HV, lambda hs: run(R_LV, mlogic(hs, oc_time_reduction=4.0))
        ),
        literal=(30, 100, 1),
    ),
    Case(
        "multiblock with a cleanroom recipe needs no cleanroom",
        R_CLEAN,
        LCR,
        m.Player(tier=LV, cleanroom=False),
        lambda: every_layout(
            LV, lambda hs: run(R_CLEAN, mlogic(hs, oc_time_reduction=4.0))
        ),
    ),
    Case(
        "multiblock runs a low-gravity recipe anywhere",
        R_GRAV,
        LCR,
        PLAYER_LV,
        lambda: every_layout(
            LV, lambda hs: run(R_GRAV, mlogic(hs, oc_time_reduction=4.0))
        ),
    ),
    Case(
        "EBF: lowest coil that runs it, heat from the hatches' summed voltage",
        R_EBF,
        EBF,
        PLAYER_HV,
        lambda: every_layout(HV, lambda hs: run(R_EBF, ebf_logic(hs, 0))),
        coil=0,
    ),
    Case(
        "EBF with the player's best coil gets heat overclocks and discounts",
        R_EBF,
        EBF,
        m.Player(tier=HV, coil_max=6),
        lambda: every_layout(HV, lambda hs: run(R_EBF, ebf_logic(hs, 6))),
        coil=6,
    ),
    Case(
        "EBF: a 4500 K recipe needs coil tier 3 at EV with two hatches",
        R_HOT,
        EBF,
        m.Player(tier=EV, hatches=(hatches(EV, 2),)),
        lambda: [(hatches(EV, 2), run(R_HOT, ebf_logic(hatches(EV, 2), 3)))],
        coil=3,
    ),
    Case(
        "EBF: the player's coils are too cold",
        R_HOT,
        EBF,
        m.Player(tier=EV, coil_max=1),
        "needs TPV-Alloy coils",
    ),
    Case(
        "Pyrolyse: Cupronickel runs at 50% speed",
        recipe(96, 640),
        PYRO,
        m.Player(tier=MV, hatches=(hatches(MV),)),
        lambda: [
            (
                hatches(MV),
                run(recipe(96, 640), mlogic(hatches(MV), speed_boost=1 / 0.5)),
            )
        ],
        coil=0,
        literal=(96, 1280, 1),
    ),
    Case(
        "Pyrolyse with HSS-G coils",
        recipe(96, 640),
        PYRO,
        m.Player(tier=MV, coil_max=4, hatches=(hatches(MV),)),
        lambda: [
            (
                hatches(MV),
                run(recipe(96, 640), mlogic(hatches(MV), speed_boost=1 / 2.5)),
            )
        ],
        coil=4,
    ),
    Case(
        "mega EBF: total EU as one amp, 256 parallels, unlimited tier skips",
        R_EBF,
        MEGA,
        m.Player(tier=HV, coil_max=2, hatches=(hatches(HV, 2),)),
        lambda: [
            (
                hatches(HV, 2),
                run(
                    R_EBF,
                    Logic(
                        power=total_eu_power(hatches(HV, 2)),
                        max_parallel=256,
                        heat_oc=True,
                        heat_discount=True,
                        machine_heat=coil_heat(2) + 100 * (get_tier(4 * V[HV]) - 2),
                        check_heat=True,
                        int_power=False,
                    ),
                ),
            )
        ],
        coil=2,
    ),
    Case(
        "limited perfect: the first 2 overclocks are perfect",
        R_LV,
        LIMITED,
        m.Player(tier=EV, hatches=(hatches(EV),)),
        lambda: [
            (
                hatches(EV),
                run(
                    R_LV,
                    mlogic(hatches(EV), heat_oc=True, recipe_heat=0, machine_heat=3600),
                ),
            )
        ],
        # 30 EU/t at 2048 EU/t: 3 overclocks, 2 perfect (/4 /4) then one standard (/2).
        literal=(1920, 3, 1),
    ),
    Case(
        "custom overclocks: EU x2, time /2, at most one",
        R_LV,
        CUSTOM_OC,
        m.Player(tier=HV, hatches=(hatches(HV),)),
        lambda: [
            (
                hatches(HV),
                run(
                    R_LV,
                    mlogic(
                        hatches(HV),
                        oc_power_increase=2.0,
                        oc_time_reduction=2.0,
                        max_overclocks=1,
                    ),
                ),
            )
        ],
        literal=(60, 50, 1),
    ),
    Case(
        "no overclocks: steam-multi-like modifiers, 8 parallels",
        R_LV,
        NO_OC,
        m.Player(tier=MV, hatches=(hatches(MV),)),
        lambda: [
            (
                hatches(MV),
                run(
                    R_LV,
                    mlogic(
                        hatches(MV),
                        max_parallel=8,
                        speed_boost=1 / (1 / 1.6),
                        eu_modifier=1.25,
                        no_overclock=True,
                    ),
                ),
            )
        ],
    ),
    Case(
        "batch mode on a machine that has it",
        R_SHORT,
        BATCH,
        m.Player(tier=HV, batch_mode=True, hatches=(hatches(HV, 2),)),
        lambda: [
            (
                hatches(HV, 2),
                run(
                    R_SHORT,
                    mlogic(hatches(HV, 2), max_parallel=4, batch_size=MAX_BATCH_SIZE),
                ),
            )
        ],
        batch=True,
    ),
    Case(
        "batch mode asked for on a machine without it",
        R_SHORT,
        NO_BATCH,
        m.Player(tier=HV, batch_mode=True, hatches=(hatches(HV, 2),)),
        lambda: [
            (hatches(HV, 2), run(R_SHORT, mlogic(hatches(HV, 2), max_parallel=4)))
        ],
    ),
    Case(
        "batch mode off by default",
        R_SHORT,
        BATCH,
        m.Player(tier=HV, hatches=(hatches(HV, 2),)),
        lambda: [
            (hatches(HV, 2), run(R_SHORT, mlogic(hatches(HV, 2), max_parallel=4)))
        ],
    ),
    Case(
        "run-state input: worst case by default, steady alternative",
        R_LV,
        CENTRIFUGE,
        m.Player(tier=HV, hatches=(hatches(HV, 2),)),
        lambda: [
            (
                hatches(HV, 2),
                run(
                    R_LV,
                    mlogic(
                        hatches(HV, 2),
                        max_parallel=4 * 4,
                        speed_boost=1 / 2,
                        eu_modifier=0.9,
                    ),
                ),
            )
        ],
        alternative=lambda: run(
            R_LV,
            mlogic(
                hatches(HV, 2), max_parallel=8 * 4, speed_boost=1 / 3, eu_modifier=0.9
            ),
        ),
    ),
    Case(
        "run-state input sized steady when the player asks",
        R_LV,
        CENTRIFUGE,
        m.Player(tier=HV, state="steady", hatches=(hatches(HV, 2),)),
        lambda: [
            (
                hatches(HV, 2),
                run(
                    R_LV,
                    mlogic(
                        hatches(HV, 2),
                        max_parallel=8 * 4,
                        speed_boost=1 / 3,
                        eu_modifier=0.9,
                    ),
                ),
            )
        ],
        alternative=lambda: run(
            R_LV,
            mlogic(
                hatches(HV, 2), max_parallel=4 * 4, speed_boost=1 / 2, eu_modifier=0.9
            ),
        ),
    ),
    Case(
        "per-map parallel fact wins on its map",
        recipe(30, 100, "gt.recipe.b"),
        PER_MAP,
        m.Player(tier=MV, hatches=(hatches(MV, 2),)),
        lambda: [
            (
                hatches(MV, 2),
                run(
                    recipe(30, 100, "gt.recipe.b"),
                    mlogic(hatches(MV, 2), max_parallel=4),
                ),
            )
        ],
    ),
    Case(
        "per-map machine falls back to the plain fact",
        R_LV,
        PER_MAP,
        m.Player(tier=MV, hatches=(hatches(MV, 2),)),
        lambda: [(hatches(MV, 2), run(R_LV, mlogic(hatches(MV, 2))))],
    ),
    Case(
        "exotic hatches on a machine that takes them",
        R_LV,
        EXOTIC,
        m.Player(tier=HV, hatches=((EnergyHatch.multi_amp(HV, 16),),)),
        lambda: [
            (
                (EnergyHatch.multi_amp(HV, 16),),
                run(
                    R_LV,
                    Logic(
                        power=multiblock_power(
                            (EnergyHatch.multi_amp(HV, 16),), extended=True
                        ),
                        max_parallel=4,
                        int_power=False,
                    ),
                ),
            )
        ],
    ),
    Case(
        "exotic hatches on a plain multiblock",
        R_LV,
        LCR,
        m.Player(tier=HV, hatches=((EnergyHatch.multi_amp(HV, 16),),)),
        "no exotic energy hatches",
    ),
    Case(
        "before LV no multiblock has a hatch",
        R_LV,
        LCR,
        m.Player(tier=0),
        "no energy hatch layout",
    ),
    Case(
        "recipe above every hatch the player has", R_HOT, LCR, PLAYER_MV, "insufficient"
    ),
    # --- facts: flags, confidence, inputs (D111, D142 (a), D144) ---
    Case(
        "flagged machine on low-confidence facts is offered with badges",
        R_LV,
        FLAGGED,
        m.Player(tier=LV, hatches=(hatches(LV),)),
        lambda: [(hatches(LV), run(R_LV, mlogic(hatches(LV))))],
        badges=("flagged", "low_confidence"),
    ),
    Case(
        "partial machine is offered with a badge",
        R_LV,
        PARTIAL,
        m.Player(tier=LV, hatches=(hatches(LV),)),
        lambda: [(hatches(LV), run(R_LV, mlogic(hatches(LV))))],
        badges=("partial",),
    ),
    Case(
        "custom processing is sized from its facts as a low-confidence machine",
        R_LV,
        CUSTOM_PROC,
        m.Player(tier=LV, hatches=(hatches(LV),)),
        lambda: [(hatches(LV), run(R_LV, mlogic(hatches(LV))))],
        badges=("custom", "low_confidence"),
    ),
    Case(
        "a parallel formula below 1 rejects with its value",
        R_LV,
        ZERO_PARALLEL,
        m.Player(tier=LV, hatches=(hatches(LV),)),
        "parallel 0 is 0 here",
    ),
    Case(
        "an absent build input is sized at its worst case",
        R_LV,
        LATEX,
        m.Player(tier=LV, hatches=(hatches(LV),)),
        lambda: [(hatches(LV), run(R_LV, mlogic(hatches(LV), max_parallel=1)))],
    ),
    Case(
        "a build input the player gives beats its worst case",
        R_LV,
        LATEX,
        m.Player(tier=LV, hatches=(hatches(LV),), build={1022: {"singularity": 1}}),
        lambda: [(hatches(LV), run(R_LV, mlogic(hatches(LV), max_parallel=9)))],
    ),
    Case(
        "a laser hatch on a multi-amp machine",
        R_LV,
        EXOTIC,
        m.Player(tier=HV, hatches=((EnergyHatch.multi_amp(HV, 256),),)),
        "takes no laser hatch",
    ),
    Case(
        "a multi-amp hatch on a laser machine",
        R_LV,
        LASER,
        m.Player(tier=HV, hatches=((EnergyHatch.multi_amp(HV, 16),),)),
        "takes no multi-amp hatch",
    ),
    Case(
        "a laser hatch on a laser machine",
        R_LV,
        LASER,
        m.Player(tier=HV, hatches=((EnergyHatch.multi_amp(HV, 256),),)),
        lambda: [
            (
                (EnergyHatch.multi_amp(HV, 256),),
                run(
                    R_LV,
                    Logic(
                        power=multiblock_power(
                            (EnergyHatch.multi_amp(HV, 256),), extended=True
                        ),
                        max_parallel=4,
                        int_power=False,
                    ),
                ),
            )
        ],
    ),
    Case(
        "boxinator runs a map recipe as a 1 A single block",
        R_TWO_AMP,
        BOXINATOR,
        m.Player(tier=MV),
        lambda: [(MV, basic_machine(Recipe(60, 100), MV))],
        literal=(60, 100, 1),
    ),
    Case(
        "deprecated controller is offered with a badge",
        R_LV,
        DEPRECATED,
        m.Player(tier=LV, hatches=(hatches(LV),)),
        lambda: [(hatches(LV), run(R_LV, mlogic(hatches(LV))))],
        badges=("deprecated",),
    ),
    Case(
        "generator runs no production recipe",
        R_LV,
        GENERATOR,
        PLAYER_LV,
        "processing generator",
    ),
    Case("fixed power is not a fact yet", R_LV, FIXED, PLAYER_LV, "power fixed"),
    Case(
        "a build input without a value", R_LV, CASING, PLAYER_LV, "casing_tier (build)"
    ),
    Case(
        "a build input the player gives",
        R_LV,
        CASING,
        m.Player(tier=LV, hatches=(hatches(LV, 2),), build={1012: {"casing_tier": 3}}),
        lambda: [(hatches(LV, 2), run(R_LV, mlogic(hatches(LV, 2), max_parallel=6)))],
    ),
    Case(
        "addon machine",
        R_LV,
        ADDON,
        m.Player(tier=LV, hatches=(hatches(LV),)),
        lambda: [(hatches(LV), run(R_LV, mlogic(hatches(LV), max_parallel=2)))],
    ),
    Case("addons off", R_LV, ADDON, m.Player(tier=LV, addons=False), "addon"),
    Case(
        "not owned when owned machines are required",
        R_LV,
        LCR,
        m.Player(tier=LV, require_owned=True),
        "not owned",
    ),
    Case(
        "machine without the recipe's map",
        recipe(30, 100, "gt.recipe.z"),
        LCR,
        PLAYER_LV,
        "doesn't run",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_table(case: Case) -> None:
    options, rejected = m.options_for(case.recipe, case.machine, case.player)
    if isinstance(case.expect, str):
        assert options == []
        assert rejected is not None and case.expect in rejected.reason, rejected
        return
    assert rejected is None, rejected
    expected = case.expect()
    assert expected, "a table row must expect at least one option"
    got = [(o.hatches if o.kind == "multiblock" else o.tier, o.sizing) for o in options]
    assert [k for k, _ in got] == [k for k, _ in expected]
    for (_, sizing), (_, want) in zip(got, expected, strict=True):
        assert want.ok
        assert (sizing.eut.value, sizing.duration.value, sizing.parallel.value) == (
            want.eut,
            want.duration,
            want.parallel,
        )
        assert sizing.overclocks.value == want.overclocks
        assert sizing.crafts_per_second.value == pytest.approx(want.crafts_per_second)
    first = options[0]
    if case.literal is not None:
        s = first.sizing
        assert (s.eut.value, s.duration.value, s.parallel.value) == case.literal
    for o in options:
        assert o.coil_tier == case.coil
        assert o.batch_mode is case.batch
        assert set(case.badges) <= set(o.badges)
        if not case.badges:
            assert o.badges == ()
        assert o.requires == case.requires
        assert o.addon is case.machine.addon
        # Every number cites the recipe and the machine.
        for value in (
            o.sizing.eut,
            o.sizing.duration,
            o.sizing.parallel,
            o.sizing.crafts_per_second,
        ):
            assert value.refs == (case.recipe.ref, f"mte:{case.machine.mte_id}")
    if case.alternative is not None:
        alt = case.alternative()
        assert first.alternative is not None
        assert (
            first.alternative.eut.value,
            first.alternative.duration.value,
            first.alternative.parallel.value,
        ) == (
            alt.eut,
            alt.duration,
            alt.parallel,
        )
        assert first.sizing.state == case.player.state
        assert (
            first.alternative.state
            == {"worst": "steady", "steady": "worst"}[case.player.state]
        )
    else:
        assert first.alternative is None


def test_table_covers_the_acceptance_kinds() -> None:
    kinds = {c.machine.kind for c in CASES}
    assert kinds == {"singleblock", "steam", "multiblock", None}
    assert any(c.coil is not None for c in CASES)  # coils
    assert any(c.requires == ("cleanroom",) for c in CASES)  # cleanroom
    assert any(c.batch for c in CASES)  # batch mode
    assert any("flagged" in c.badges for c in CASES)  # flagged machines


def test_ebf_literal_two_hv_hatches() -> None:
    """Worked by hand: 120 EU/t, 500 ticks, 1500 K on two HV hatches and Cupronickel coils.
    Heat 1801 + 100 x (EV - MV) = 2001 K (the hatches' voltages sum to 1024 EU/t, EV), 501 K
    over the recipe: no heat overclock or discount. 4 A x 512 = 2048 EU/t is 2 tiers above
    120: 1920 EU/t, 125 ticks."""
    options, _ = m.options_for(R_EBF, EBF, m.Player(tier=HV, hatches=(hatches(HV, 2),)))
    s = options[0].sizing
    assert (s.eut.value, s.duration.value, s.parallel.value, s.overclocks.value) == (
        1920,
        125,
        1,
        2,
    )
    assert options[0].min_coil_tier == 0


def test_lcr_literal_one_hv_hatch() -> None:
    """30 EU/t, 100 ticks on one HV hatch (512 EU/t at 1 A): 2 perfect overclocks, 480 EU/t,
    100 / 16 = 6 ticks."""
    options, _ = m.options_for(R_LV, LCR, m.Player(tier=HV, hatches=(hatches(HV),)))
    s = options[0].sizing
    assert (s.eut.value, s.duration.value, s.overclocks.value) == (480, 6, 2)
    assert s.amps.value == 1


def test_choose_orders_and_rejects() -> None:
    machines = [EBF, ADDON, single(301, LV), steam(106, "Bronze"), SCANNER]
    sel = m.choose(R_LV, machines, PLAYER_LV)
    kinds = [(o.addon, o.kind) for o in sel.options]
    assert kinds == sorted(
        kinds,
        key=lambda k: (k[0], {"steam": 0, "singleblock": 1, "multiblock": 2}[k[1]]),
    )
    assert sel.options[-1].addon
    # The scanner has its own recipe check; the EBF runs a recipe that needs no heat.
    assert {r.mte_id for r in sel.rejected} == {1020}
    assert any(o.mte_id == 1000 for o in sel.options)
    assert (
        m.choose(R_LV, list(reversed(machines)), PLAYER_LV) == sel
    )  # same order every run


def test_owned_is_marked() -> None:
    options, _ = m.options_for(R_LV, LCR, m.Player(tier=LV, owned=frozenset({1001})))
    assert options and all(o.owned for o in options)
    options, _ = m.options_for(
        R_LV, LCR, m.Player(tier=LV, owned=frozenset({1001}), require_owned=True)
    )
    assert options


def test_used_facts_are_listed_with_their_sources() -> None:
    options, _ = m.options_for(R_LV, FLAGGED, m.Player(tier=LV, hatches=(hatches(LV),)))
    used = {f.key: f for f in options[0].facts}
    assert used["parallel"].source == "tooltip" and used["parallel"].confidence == 0.5
    assert used["flag"].note.startswith("parallels")
    assert any(n.startswith("flagged:") for n in options[0].notes)


def test_recipe_flags_and_heat() -> None:
    assert (
        recipe(30, 1, special=-300).cleanroom
        and recipe(30, 1, special=-300).low_gravity
    )
    assert not recipe(30, 1, special=3200).cleanroom
    assert recipe(
        30, 1, special=3200, cleanroom=1.0
    ).cleanroom  # the metadata alone counts
    assert recipe(30, 1, special=None, coil_heat=2000.0).heat == 2000
    assert (
        recipe(30, 1, special=1500, coil_heat=2000.0).heat == 1500
    )  # GT reads mSpecialValue
    assert recipe(30, 1, special=-200).heat == 0
    assert recipe(120, 1).variables()["recipe_tier"] == MV


def test_hatch_variables() -> None:
    v = m._hatch_variables(hatches(IV, 2), extended=False)
    assert v["vt"] == get_tier(2 * V[IV]) == 6  # summed voltage: LuV
    assert v["vt_avg"] == IV
    assert v["input_eu"] == 4 * V[IV]
    assert v["vt_eu"] == 6
    assert v["vt_eu_floor"] == 6  # 4 A of IV is LuV
    assert (
        m._hatch_variables(hatches(IV, 1), extended=False)["vt_eu_floor"] == 5
    )  # 2 A of IV


def test_default_layouts() -> None:
    layouts = m.Player(tier=MV).hatch_layouts()
    assert layouts == [hatches(LV), hatches(LV, 2), hatches(MV), hatches(MV, 2)]
    assert m.Player(tier=0).hatch_layouts() == []


@pytest.mark.parametrize(
    "key", ["vt", "vt_eu", "input_eu", "recipe_tier", "coil_tier", "ramp", "nope"]
)
def test_build_inputs_cant_override_derived_values(key: str) -> None:
    # Review MEDIUM-1: a build entry for `vt` once turned 8 parallels at LV into 32.
    with pytest.raises(ValueError, match="build"):
        m.Player(tier=LV, build={1: {key: 14}})


def test_build_inputs_go_under_hatch_values() -> None:
    vt_multi = multi(1030, logic_facts("8*vt", "1", "1", "standard"))
    player = m.Player(tier=LV, hatches=(hatches(LV),))
    options, _ = m.options_for(recipe(1, 100), vt_multi, player)
    assert options[0].sizing.parallel.value == 8


@pytest.mark.parametrize(
    "kw",
    [
        {"tier": -1},
        {"tier": len(V)},
        {"tier": LV, "coil_max": 14},
        {"tier": LV, "coil_max": -1},
        {"tier": LV, "hatches": ((EnergyHatch(0),),)},
        {"tier": LV, "hatches": ((EnergyHatch(len(V)),),)},
        {"tier": LV, "hatches": ((EnergyHatch(LV, amps=0),),)},
    ],
)
def test_player_out_of_range(kw: dict[str, object]) -> None:
    # Review LOW-1: these used to raise from deep inside selection and abort choose().
    with pytest.raises(ValueError):
        m.Player(**kw)  # type: ignore[arg-type]


def test_absent_build_input_is_noted() -> None:
    options, _ = m.options_for(R_LV, LATEX, m.Player(tier=LV, hatches=(hatches(LV),)))
    assert any("singularity = 0" in n for n in options[0].notes)
    options, _ = m.options_for(
        R_LV,
        LATEX,
        m.Player(tier=LV, hatches=(hatches(LV),), build={1022: {"singularity": 1}}),
    )
    assert not any("singularity" in n for n in options[0].notes)


def test_rejection_names_the_strongest_layout_whatever_the_order() -> None:
    # Review LOW-5: callers can pass layouts in any order.
    strong, weak = hatches(MV, 2), hatches(LV)
    for layouts in ((strong, weak), (weak, strong)):
        _, why = m.options_for(R_HOT, LCR, m.Player(tier=MV, hatches=layouts))
        assert why is not None
        _, alone = m.options_for(R_HOT, LCR, m.Player(tier=MV, hatches=(strong,)))
        assert alone is not None and why.reason == alone.reason


def test_map_first_single_blocks() -> None:
    assert BOXINATOR.kind == "singleblock"
    brewer = replace(
        BOXINATOR,
        class_name="gregtech.common.tileentities.machines.basic.MTEPotionBrewer",
    )
    assert brewer.kind == "singleblock"
    assert SCANNER.kind is None


def test_unlimited_tier_skips_and_overflow_guard() -> None:
    assert m._int(float("inf")) == INT_MAX
    assert UNLIMITED_TIER_SKIPS < 0 or UNLIMITED_TIER_SKIPS > 1
    # Steam's no-overclock path is GT's calculator with the modifiers.
    oc = calculate(
        Overclock(
            recipe_eut=2,
            duration=400,
            voltage=2,
            no_overclock=True,
            duration_modifier=2.0,
        )
    )
    assert (oc.eut, oc.duration) == (2, 800)


# --- the DB loader ---------------------------------------------------------------------------


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = access.connect(tmp_path / "gos.sqlite")
    x = conn.execute
    x(
        "INSERT INTO export_run(id, created_at, fingerprint, run_key) VALUES (1, '2026-11-11', '', 'r1')"
    )
    for modid, sha in (("gregtech", "aa"), ("bartworks", "aa"), ("otheraddon", "bb")):
        x(
            "INSERT INTO mod(run_id, modid, jar, jar_sha256) VALUES (1, ?, 'x.jar', ?)",
            (modid, sha),
        )
    x(
        "INSERT INTO recipe_map(id, unloc_name, amperage) VALUES (1, 'gt.recipe.blastfurnace', 1)"
    )
    x(
        "INSERT INTO recipe(id, run_id, source, recipe_map_id, eut, duration_ticks, special_value,"
        " signature, content_hash, ref) VALUES (1, 1, 'gt', 1, 120, 500, 1500, 's', 'c',"
        " 'r:gt.recipe.blastfurnace/0059870c0b67')"
    )
    x(
        "INSERT INTO recipe_meta(recipe_id, key, value_num) VALUES (1, 'coil_heat', 1500)"
    )
    x("INSERT INTO recipe_meta(recipe_id, key, value_text) VALUES (1, 'nei_desc', 'x')")
    machines = [
        (
            1,
            1000,
            "gregtech.common.tileentities.machines.multi.MTEElectricBlastFurnace",
            "gregtech",
            1,
            None,
        ),
        (2, 12730, "bartworks.Mega", "bartworks", 1, None),
        (3, 5000, "other.AddonEBF", "otheraddon", 1, None),
    ]
    for mid, mte, cls, mod, multi_, tier in machines:
        x(
            "INSERT INTO machine(id, mte_id, class_name, mod_id, name_en, is_multiblock, tier)"
            " VALUES (?,?,?,?,?,?,?)",
            (mid, mte, cls, mod, f"M{mte}", multi_, tier),
        )
        x(
            "INSERT INTO machine_map(machine_id, recipe_map_id, priority) VALUES (?, 1, 0)",
            (mid,),
        )
    rows = [
        (1, "processing", "logic", "curated", 0.95),
        (1, "parallel", "1", "curated", 0.95),
        (1, "parallel", "4", "tooltip", 0.85),  # the curated row wins (D109)
        (1, "speed", "1", "curated", 0.95),
        (1, "eu_modifier", "1", "curated", 0.95),
        (1, "oc", "heat", "curated", 0.95),
        (1, "machine_heat", "coil_heat + 100*(vt - 2)", "curated", 0.95),
        (1, "batch_mode", "true", "code", 1.0),
        (2, "processing", "logic", "curated", 0.9),
        (2, "parallel", "256", "curated", 0.9),
        (2, "oc", "heat", "curated", 0.9),
        (2, "machine_heat", "coil_heat + 100*(vt_eu - 2)", "curated", 0.9),
        (2, "power", "total_eu", "curated", 0.9),
        (2, "tier_skips", "unlimited", "curated", 0.9),
        (3, "processing", "logic", "tooltip", 0.5),
        (3, "parallel", "1", "tooltip", 0.5),
        (3, "oc", "standard", "tooltip", 0.5),
    ]
    for mid, key, value, source, conf in rows:
        x(
            "INSERT INTO machine_fact(machine_id, key, value, source, confidence, note)"
            " VALUES (?,?,?,?,?, '')",
            (mid, key, value, source, conf),
        )
    conn.executescript(store.SCHEMA)  # My base's tables are the core's (D210)
    x(
        "INSERT INTO my_base_machine(mte_id, source, count, upd) VALUES (1000, 'manual', 2, 0)"
    )
    x(
        "INSERT INTO my_base_machine(mte_id, source, count, upd) VALUES (12730, 'manual', 0, 0)"
    )
    return conn


def test_select_from_the_db(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        ref = "r:gt.recipe.blastfurnace/0059870c0b67"
        r = m.load_recipe(conn, ref)
        assert (r.eut, r.duration, r.special_value, r.map_amperage) == (
            120,
            500,
            1500,
            1,
        )
        assert r.meta == {"coil_heat": 1500.0}  # nei_desc is left out
        owned = m.owned_machines(conn)
        assert owned == frozenset({1000})
        player = m.Player(tier=HV, owned=owned, hatches=(hatches(HV, 2),))
        sel = m.select(conn, ref, player)
        by = {o.mte_id: o for o in sel.options}
        assert set(by) == {1000, 12730, 5000}
        assert [o.mte_id for o in sel.options][-1] == 5000  # the addon comes last (D12)
        ebf = by[1000]
        assert ebf.owned and not ebf.addon and ebf.batch_supported
        s = ebf.sizing
        assert (s.eut.value, s.duration.value, s.parallel.value) == (
            1920,
            125,
            1,
        )  # curated parallel
        assert {f.key: f.source for f in ebf.facts}["parallel"] == "curated"
        assert not by[12730].addon and not by[12730].owned
        assert by[5000].addon and "low_confidence" in by[5000].badges
        with pytest.raises(LookupError):
            m.load_recipe(conn, "r:gt.recipe.blastfurnace/000000000000")
    finally:
        conn.close()


def test_select_needs_a_run(tmp_path: Path) -> None:
    conn = access.connect(tmp_path / "gos.sqlite")
    try:
        with pytest.raises(LookupError):
            m.select(conn, "r:x/1", PLAYER_LV)
    finally:
        conn.close()
