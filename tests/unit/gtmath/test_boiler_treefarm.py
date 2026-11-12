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
