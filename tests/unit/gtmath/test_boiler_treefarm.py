"""The large-boiler and Tree Growth Simulator ports (WO-4.2, D233), worked examples of
GT 5.09.54.265's arithmetic (MTELargeBoilerBase, MTETreeFarm). GT's own classes check the ports
in test_boiler_vectors.py."""

from __future__ import annotations

from fractions import Fraction

import pytest

from gregtec.planner.gtmath import boiler, treefarm
from gregtec.planner.gtmath.tiers import VP

BRONZE = boiler.BOILERS["MTELargeBoilerBronze"]
STEEL = boiler.BOILERS["MTELargeBoilerSteel"]
TITANIUM = boiler.BOILERS["MTELargeBoilerTitanium"]
TUNGSTEN = boiler.BOILERS["MTELargeBoilerTungstenSteel"]


def test_runtime_boost_is_javas_integer_arithmetic() -> None:
    assert BRONZE.runtime_boost(16) == 32
    assert STEEL.runtime_boost(16) == 16
    assert TITANIUM.runtime_boost(16) == 4  # 16 * 3 / 10 = 4.8 -> 4
    assert TUNGSTEN.runtime_boost(16) == 2  # 16 * 15 / 100 = 2.4 -> 2
    assert (
        TUNGSTEN.runtime_boost(6) == 0
    )  # the boiler then burns 1 tick (steady's floor)


def test_steady_state_of_a_normal_boiler() -> None:
    s = boiler.steady(STEEL, fluid="creosote", duration=16)
    # lEUt x 2 at full efficiency: 2400 L/t of steam, 1 L of water per 160 of steam.
    assert s.steam == 2400 and s.water == Fraction(2400, 160)
    # 1000 L burns runtimeBoost(16) = 16 ticks.
    assert s.burn_ticks == 16 and s.fuel == Fraction(1000, 16)
    b = boiler.steady(BRONZE, fluid="creosote", duration=16)
    assert b.steam == 960 and b.burn_ticks == 32 and b.fuel == Fraction(1000, 32)
    assert BRONZE.steam_fluid == "steam"


def test_superheated_boilers_take_only_gts_allowed_fuels() -> None:
    s = boiler.steady(TITANIUM, fluid="gasoline", duration=591)
    assert s.steam == Fraction(4800 * 2, 3)  # superheated steam: a third
    assert s.water == Fraction(4800 * 2, 160 * 3)
    assert s.burn_ticks == 591 * 3 // 10
    assert TITANIUM.steam_fluid == "ic2superheatedsteam"
    with pytest.raises(boiler.BoilerError):
        boiler.steady(TUNGSTEN, fluid="creosote", duration=16)
    # A burn time that rounds to 0 still burns one tick per 1000 L.
    assert boiler.steady(TUNGSTEN, fluid="ether", duration=6).burn_ticks == 1


def test_burntime_ratio_stretches_long_burns() -> None:
    # Up to 10 s (200 ticks) the ratio is 1: the ticks come back.
    assert [boiler.burntime_ratio_ticks(t) for t in (1, 15, 20, 200)] == [
        1,
        15,
        20,
        200,
    ]
    # 1250 ticks = 62.5 s x (1 + ln(6.25) x 0.025) = 65.3634... -> 65.36 s -> 1307 ticks.
    assert boiler.burntime_ratio_ticks(1250) == 1307


def test_a_solid_fuel_burns_by_its_furnace_value() -> None:
    # Charcoal, 1600: 1600 / 80 = 20 raw ticks; steel x 1, bronze x 2.
    s = boiler.steady(STEEL, item="minecraft:coal@1", burn_value=1600)
    assert s.burn_ticks == 20 and s.fuel == Fraction(1, 20)
    assert s.steam == 2400 and s.water == Fraction(2400, 160)
    assert boiler.solid_burn_ticks(BRONZE, 1600) == 40
    # 1650 % 80 = 50 is carried: of every 80 items, 50 burn 21 raw ticks and 30 burn 20.
    assert boiler.solid_burn_ticks(STEEL, 1650) == Fraction(30 * 20 + 50 * 21, 80)
    # 100 000: 1250 raw ticks, stretched to 1307; titanium x 3 / 10.
    assert boiler.solid_burn_ticks(STEEL, 100_000) == 1307
    assert boiler.solid_burn_ticks(TITANIUM, 100_000) == 1307 * 3 // 10
    # The throttle stretches the burn time: EU per item holds.
    t = boiler.steady(STEEL, item="minecraft:coal@1", burn_value=1600, circuit=24)
    assert t.steam == 1200 and t.fuel == Fraction(1, 40)


def test_a_boiler_takes_a_solid_only_at_its_own_eut_or_more() -> None:
    # GT's check: value / 80 > 0 and value x 2 / EUT_GENERATED > 1.
    assert boiler.solid_burn_ticks(BRONZE, 480) == 12
    for spec, value in ((BRONZE, 479), (STEEL, 1199), (BRONZE, 79)):
        with pytest.raises(boiler.BoilerError):
            boiler.solid_burn_ticks(spec, value)
    # The superheated boilers take only GT's two allowed blocks.
    with pytest.raises(boiler.BoilerError):
        boiler.steady(TITANIUM, item="minecraft:coal@1", burn_value=100_000)
    block = "gregtech:gt.blockreinforced@6"
    assert boiler.steady(TITANIUM, item=block, burn_value=100_000).burn_ticks == 392
    with pytest.raises(boiler.BoilerError):  # below the tungstensteel boiler's 19 200
        boiler.steady(TUNGSTEN, item=block, burn_value=16_000)


def test_tree_farm_multipliers() -> None:
    # 2 t^2 - 2 t + 5: LV 5, MV 9, HV 17, EV 29.
    assert [treefarm.tier_multiplier(t) for t in (1, 2, 3, 4)] == [5, 9, 17, 29]
    assert treefarm.eut(1) == VP[1] and treefarm.eut(0) == VP[1]
    # NEI lists 5 logs (mode multiplier applied); LV x 5, buzzsaw x 2.
    assert treefarm.output(5, 1, "buzzsaw") == 50
    assert treefarm.output(5, 2, "saw") == 45
    assert treefarm.output(5, 4, "chainsaw") == 5 * 29 * 4
    assert treefarm.TOOLS["buzzsaw"] == ("log", 2, True)
    assert treefarm.TICKS == 100 and treefarm.TOOL_CHARGE == 32
