"""Hand-written checks of the turbine port (WO-4.1, D230); GT's own numbers are in
test_turbine_vectors.py."""

from __future__ import annotations

import pytest

from gregtec.planner.gtmath.jmath import INT_MAX, INT_MIN
from gregtec.planner.gtmath.tiers import V
from gregtec.planner.gtmath.turbine import (
    Kind,
    Power,
    RotorMaterial,
    RotorSize,
    RotorStats,
    dynamo_cap,
    fluid_into_power,
    gt_safe_int,
)

# Test inputs only (shaped like a rotor, not any real material's stats).
SIZE = RotorSize(
    "large", speed_multiplier=3.0, base_damage=5.0, durability_multiplier=3.0
)
MATERIAL = RotorMaterial("Testium", 7.0, 3, 1600, 1.0, 1.0, 1.0)
ROTOR = RotorStats(SIZE, MATERIAL)
ALL = [(k, legacy) for k in Kind for legacy in (False, True)]


def test_safe_int_caps_above_max_plus_and_wraps_below_it() -> None:
    assert gt_safe_int(5) == 5
    assert gt_safe_int(V[-1] + 1) == INT_MAX - 1
    assert gt_safe_int(INT_MAX + 1) == INT_MIN  # (int) of a long, as GT does below MAX+
    assert gt_safe_int(-(2**40)) == INT_MIN


def test_dynamo_cap_is_one_hatch() -> None:
    assert dynamo_cap(5) == V[5]
    assert dynamo_cap(4, 16) == 16 * V[4]


def test_fuelled_turbines_need_a_fuel_value() -> None:
    for kind in (Kind.GAS, Kind.PLASMA):
        with pytest.raises(ValueError, match="FUEL_VALUE"):
            fluid_into_power(kind, ROTOR, available=10, cap=V[5])
    with pytest.raises(ValueError, match="negative"):
        fluid_into_power(Kind.STEAM, ROTOR, available=-1, cap=V[5])


@pytest.mark.parametrize(("kind", "legacy"), ALL)
def test_no_fluid_makes_nothing(kind: Kind, legacy: bool) -> None:
    p = fluid_into_power(
        kind, ROTOR, available=0, cap=V[5], fuel_value=100, legacy=legacy
    )
    assert p.eut == 0 and p.flow == 0


@pytest.mark.parametrize(("kind", "legacy"), ALL)
@pytest.mark.parametrize("loose", [False, True])
def test_flow_and_output_are_bounded(kind: Kind, legacy: bool, loose: bool) -> None:
    previous: Power | None = None
    for available in (1, 2, 5, 10, 50, 100, 1000, 10_000, 100_000, INT_MAX):
        p = fluid_into_power(
            kind,
            ROTOR,
            available=available,
            cap=V[5],
            fuel_value=50,
            loose=loose,
            legacy=legacy,
        )
        assert 0 <= p.eut <= V[5]
        assert p.flow <= min(available, max(p.max_flow, 1))
        if previous is not None:
            assert p.flow >= previous.flow  # more fluid never burns less
        previous = p
    assert previous is not None and previous.flow == max(previous.max_flow, 1)


def test_optimal_flow_gives_the_rotors_optimal_output() -> None:
    p = fluid_into_power(Kind.GAS, ROTOR, available=10_000, cap=2**40, fuel_value=50)
    at_opt = fluid_into_power(
        Kind.GAS, ROTOR, available=int(p.optimal_flow), cap=2**40, fuel_value=50
    )
    assert at_opt.eut == int(at_opt.flow * 50 * ROTOR.base_efficiency)
    # Overflow costs efficiency: the extra litres make less than their share.
    assert p.eut / p.flow < at_opt.eut / at_opt.flow


def test_a_rotor_too_weak_for_the_fuel_burns_one_litre() -> None:
    fuel = int(ROTOR.optimal_gas_eut) + 1
    p = fluid_into_power(Kind.GAS, ROTOR, available=500, cap=2**40, fuel_value=fuel)
    assert p.flow == 1 and p.eut == int(ROTOR.optimal_gas_eut)


def test_the_cap_binds() -> None:
    p = fluid_into_power(
        Kind.PLASMA, ROTOR, available=10**6, cap=V[1], fuel_value=10**6
    )
    assert p.eut == V[1]
