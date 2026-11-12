"""GT's large turbines (WO-4.1, D230): ``TurbineStatCalculator`` and ``fluidIntoPower``.

GT 5.09.54.265's turbine math in Python, for the five large turbines of plan §7.2 (steam, HP
steam, supercritical steam, gas, plasma), the 2.9 controllers and their legacy copies [E§4.1]:

- :class:`RotorStats` is ``TurbineStatCalculator``: a rotor's efficiency, optimal flows, optimal
  EU/t and overflow tier from its size (``ToolTurbine*``: speed multiplier, base damage) and its
  material (tool speed, tool quality, steam/gas/plasma multipliers). Both come from the export
  (``turbine_rotor`` in the DB), never from here.
- :func:`fluid_into_power` is the controllers' ``fluidIntoPower``: the EU/t a turbine makes from
  the fluid it is given in one tick, the litres it takes and its optimal flow, with the rotor's
  overflow tier (150/300/450 % of optimal for gas, more for plasma), the overflow-efficiency
  penalty, tight or loose fitting and the dynamo cap.
- :func:`dynamo_cap` is ``getMaximumOutput``: the dynamo hatch's voltage x amps. GT keeps the
  *last* dynamo's value, not the sum, so a turbine's output never rises with a second dynamo.

``fluidIntoPower``'s result is the controller's target output. It runs once per processing cycle
(:func:`cycle_ticks`): every tick for steam, HP steam, supercritical steam and gas, every 20 ticks
for plasma (both plasma controllers set ``mMaxProgresstime = 20``). The litres it takes are per
cycle (L/t, or L/s for plasma) and the EU/t it returns holds for the whole cycle, so ``FUEL_VALUE``
is EU per litre for plasma too. Each cycle the controller moves its output toward the target by
the larger of 10 EU/t and 1 % of the gap, and its efficiency climbs to 100 % (``mEfficiencyIncrease``
10 per tick, plasma 200 per 20-tick cycle: 1000 ticks either way). The steady output a scanner
shows in the Lab is this number (docs/runbooks/turbines_lab.md).

Java's arithmetic is kept to the bit: ``float`` steps round to single precision, ``(int)`` and
``(long)`` truncate and saturate, ``GTUtility.safeInt(long)`` caps at ``Integer.MAX_VALUE - 1``
only above GT's MAX+ voltage and wraps below it, and ``Math.pow`` is the correctly rounded power
(as ``jmath.log``, D175). Re-implemented from GT's behaviour (GT is LGPL-3.0; source read at tag
5.09.54.265, nothing copied) and checked against GT's own classes:
``tests/unit/gtmath/oracle/make_turbine_vectors.py`` (D231).
"""

from __future__ import annotations

import decimal
import functools
import math
from dataclasses import dataclass
from enum import Enum

from gregtec.planner.gtmath.jmath import (
    INT_MAX,
    INT_MIN,
    ceil,
    safe_int,
    to_float,
    to_int,
    to_long,
    wrap_int,
)
from gregtec.planner.gtmath.tiers import V

_F = to_float  # one ``float`` step: round to single precision


class Kind(Enum):
    """The five large turbines (plan §7.2); the fluid each one burns."""

    STEAM = "steam"  # steam (any steam GT knows)
    HP_STEAM = "hp_steam"  # superheated steam
    SC_STEAM = "sc_steam"  # supercritical steam
    GAS = "gas"  # gasTurbineFuels
    PLASMA = "plasma"  # plasmaFuels


# The kinds whose fuel value comes from a fuel map (the others burn steam).
FUELLED = frozenset({Kind.GAS, Kind.PLASMA})


def cycle_ticks(kind: Kind) -> int:
    """The controller's processing cycle once generating (``mMaxProgresstime``): 20 ticks for
    plasma, else 1. :func:`fluid_into_power`'s flow is litres per cycle."""
    return 20 if kind is Kind.PLASMA else 1


def flow_unit(kind: Kind) -> str:
    """The unit of :func:`fluid_into_power`'s flow, as GT's scanner shows it: L/t, or L/s for
    the plasma turbine's 20-tick cycle."""
    return "L/t" if cycle_ticks(kind) == 1 else "L/s"


@dataclass(frozen=True)
class RotorSize:
    """One rotor size's ``ToolTurbine*`` stats (floats as GT stores them)."""

    name: str
    speed_multiplier: float
    base_damage: float
    durability_multiplier: float


@dataclass(frozen=True)
class RotorMaterial:
    """A rotor material's tool stats (``Materials``: ``mToolSpeed``, ``mToolQuality``,
    ``mDurability``, ``mSteamMultiplier``, ``mGasMultiplier``, ``mPlasmaMultiplier``)."""

    name: str
    tool_speed: float
    tool_quality: int
    durability: int
    steam_multiplier: float
    gas_multiplier: float
    plasma_multiplier: float


def _pow(base: float, exponent: float) -> float:
    """``Math.pow`` for the positive bases the turbines use, correctly rounded."""
    if exponent == 0:
        return 1.0
    if not (math.isfinite(base) and math.isfinite(exponent)) or base <= 0:
        return math.pow(base, exponent)
    return _pow_exact(base, exponent)


@functools.lru_cache(maxsize=4096)
def _pow_exact(base: float, exponent: float) -> float:
    ctx = decimal.Context(prec=50)
    return float((decimal.Decimal(exponent) * decimal.Decimal(base).ln(ctx)).exp(ctx))


def _round_float(x: float) -> int:
    """``Math.round(float)``: the nearest int, ties up; NaN is 0; saturates."""
    if math.isnan(x):
        return 0
    if x >= INT_MAX:
        return INT_MAX
    if x <= INT_MIN:
        return INT_MIN
    return math.floor(x + 0.5)


def gt_safe_int(number: int) -> int:
    """``GTUtility.safeInt(long)``: above GT's MAX+ voltage ``Integer.MAX_VALUE - 1``, below
    ``Integer.MIN_VALUE`` that, else ``(int) number`` (which wraps between the two)."""
    if number > V[-1]:
        return safe_int(V[-1], 1)
    if number < INT_MIN:
        return INT_MIN
    return wrap_int(number)


def _f_mul_int(f: float, i: int) -> float:
    """``float * int`` (the int converted to float first)."""
    return _F(f * _F(float(i)))


@dataclass(frozen=True)
class RotorStats:
    """``TurbineStatCalculator`` for one rotor (size + material); every value a ``float``
    unless noted."""

    size: RotorSize
    material: RotorMaterial

    @property
    def max_durability(self) -> int:
        """``MaxDamage`` of a new rotor: 100 x (long)(durability x the size's multiplier)."""
        durability = _F(float(self.material.durability))
        return 100 * to_long(_F(durability * self.size.durability_multiplier))

    @property
    def combat_damage(self) -> float:
        """``getToolCombatDamage``: the size's base damage + the material's tool quality."""
        return _F(self.size.base_damage + _F(float(self.material.tool_quality)))

    @property
    def base_efficiency(self) -> float:
        """``getBaseEfficiency`` (steam, gas and plasma efficiency too): 0.5 + (0.5 + damage)
        x 0.1."""
        return _F(0.5 + _F(_F(0.5 + self.combat_damage) * _F(0.1)))

    @property
    def loose_efficiency(self) -> float:
        """``getLooseEfficiency``: (float)(-0.2f + round(base x 85) x 0.01)."""
        rounded = _round_float(_F(self.base_efficiency * 85.0))
        return _F(_F(-0.2) + rounded * 0.01)

    @property
    def loose_steam_efficiency(self) -> float:
        return _F(self.loose_efficiency * _F(0.9))

    @property
    def loose_gas_efficiency(self) -> float:
        return _F(self.loose_efficiency * _F(0.95))

    @property
    def loose_plasma_efficiency(self) -> float:
        return self.loose_efficiency

    @property
    def optimal_flow(self) -> float:
        """``getOptimalFlow``: speed multiplier x tool speed x 50."""
        return _F(_F(self.size.speed_multiplier * self.material.tool_speed) * 50.0)

    @property
    def optimal_steam_flow(self) -> float:
        return _F(self.optimal_flow * self.material.steam_multiplier)

    @property
    def optimal_gas_flow(self) -> float:
        return _F(self.optimal_flow * self.material.gas_multiplier)

    @property
    def optimal_plasma_flow(self) -> float:
        return _f_mul_int(_F(self.optimal_flow * self.material.plasma_multiplier), 42)

    def _loose_factor(self, base: float) -> float:
        exponent = _F(_F(self.base_efficiency - _F(0.8)) * 20.0)
        return _F(_pow(_F(base), exponent))

    @property
    def optimal_loose_steam_flow(self) -> float:
        """3 x the steam flow x 1.1^((efficiency - 0.8) x 20)."""
        return _F(_F(3.0 * self.optimal_steam_flow) * self._loose_factor(1.1))

    @property
    def optimal_loose_gas_flow(self) -> float:
        """2 x the gas flow x 1.05^((efficiency - 0.8) x 20)."""
        return _F(_F(2.0 * self.optimal_gas_flow) * self._loose_factor(1.05))

    @property
    def optimal_loose_plasma_flow(self) -> float:
        """2 x the plasma flow x 1.03^((efficiency - 0.8) x 20)."""
        return _F(_F(2.0 * self.optimal_plasma_flow) * self._loose_factor(1.03))

    @property
    def optimal_steam_eut(self) -> float:
        return _F(_F(self.optimal_steam_flow * self.base_efficiency) * 0.5)

    @property
    def optimal_gas_eut(self) -> float:
        return _F(self.optimal_gas_flow * self.base_efficiency)

    @property
    def optimal_plasma_eut(self) -> float:
        return _F(self.optimal_plasma_flow * self.base_efficiency)

    @property
    def optimal_loose_steam_eut(self) -> float:
        return _F(_F(self.optimal_loose_steam_flow * self.loose_steam_efficiency) * 0.5)

    @property
    def optimal_loose_gas_eut(self) -> float:
        return _F(self.optimal_loose_gas_flow * self.loose_gas_efficiency)

    @property
    def optimal_loose_plasma_eut(self) -> float:
        return _F(self.optimal_loose_plasma_flow * self.loose_plasma_efficiency)

    @property
    def overflow_tier(self) -> int:
        """``getOverflowEfficiency`` (an int): 1 + min(2, tool quality / 3)."""
        q = self.material.tool_quality
        third = q // 3 if q >= 0 else -(-q // 3)  # Java's int division truncates
        return to_int(1 + min(2.0, float(third)))


def dynamo_cap(tier: int, amps: int = 1) -> int:
    """``getMaximumOutput``: a dynamo hatch's voltage x amps (``V[tier]`` x amps). GT takes the
    last valid dynamo's value, so a turbine is capped by one dynamo however many it has."""
    return amps * V[tier]


@dataclass(frozen=True)
class Power:
    """What one ``fluidIntoPower`` call gives: the EU/t, the litres it takes per cycle
    (:func:`cycle_ticks`: per tick, or per 20 ticks for plasma) and the optimal flow the
    controller reports in the same unit (``realOptFlow``: int for gas and plasma, the float
    flow for steam)."""

    eut: int
    flow: int
    optimal_flow: float
    max_flow: int


def _overflow_efficiency(kind: Kind, total: int, optimal: int, overflow: int) -> float:
    """``getOverflowEfficiency(totalFlow, actualOptimalFlow, overflowMultiplier)``."""
    if total > optimal:
        divisor = {
            Kind.STEAM: overflow + 1,
            Kind.HP_STEAM: overflow + 2,
            Kind.GAS: overflow * 3 - 1,
            Kind.PLASMA: overflow * 3 + 1,
        }[kind]
        over = _F(float(abs(wrap_int(total - optimal))))
        return _F(1.0 - _F(over / _f_mul_int(_F(float(optimal)), divisor)))
    return _F(1.0 - abs(_F(_F(float(wrap_int(total - optimal))) / _F(float(optimal)))))


def _int_times_float(i: int, f: float) -> int:
    """``i *= f`` for an int ``i``: ``(int)(i * f)`` in float."""
    return to_int(_F(_F(float(i)) * f))


def fluid_into_power(
    kind: Kind,
    rotor: RotorStats,
    *,
    available: int,
    cap: int,
    fuel_value: int = 0,
    loose: bool = False,
    legacy: bool = False,
) -> Power:
    """One processing cycle of a large turbine (one tick, or 20 for plasma: :func:`cycle_ticks`):
    ``fluidIntoPower`` of the 2.9 controller (``legacy``: the ``*Legacy`` one) given
    ``available`` litres of its fluid in its input hatches.

    ``fuel_value`` is the fuel map's ``FUEL_VALUE`` (gas and plasma, ``fuel`` rows in the DB);
    ``cap`` is :func:`dynamo_cap` (``maxPower`` for the supercritical turbines, the same number
    once the structure is formed). No fluid (``available`` 0) makes nothing."""
    if kind in FUELLED and fuel_value <= 0:
        raise ValueError(f"a {kind.value} turbine needs the fuel's FUEL_VALUE")
    if available < 0:
        raise ValueError("available litres can't be negative")
    if kind is Kind.GAS:
        return _gas(rotor, available, cap, fuel_value, loose)
    if kind is Kind.PLASMA:
        return _plasma(rotor, available, cap, fuel_value, loose)
    if kind is Kind.SC_STEAM:
        return _sc_steam(rotor, available, cap, loose, legacy)
    return _steam(kind, rotor, available, cap, loose)


def _capped(eut: int, cap: int) -> int:
    return gt_safe_int(cap) if eut > cap else eut


def _gas(rotor: RotorStats, available: int, cap: int, fuel: int, loose: bool) -> Power:
    if available == 0:
        return Power(0, 0, 0.0, 0)
    if rotor.optimal_gas_eut < _F(float(fuel)):
        # A rotor too weak for the fuel burns 1 L and makes its optimal EU/t (both controllers).
        return Power(gt_safe_int(to_long(rotor.optimal_gas_eut)), 1, 1.0, 1)
    flow_f = rotor.optimal_loose_gas_flow if loose else rotor.optimal_gas_flow
    optimal = gt_safe_int(to_long(_F(flow_f / _F(float(fuel)))))
    max_flow = gt_safe_int(
        to_long(_F(_F(float(optimal)) * _f_mul_int(1.5, rotor.overflow_tier)))
    )
    total = min(available, max_flow) if max_flow > 0 else 0
    if total <= 0:
        return Power(0, 0, float(optimal), max_flow)
    eut = gt_safe_int(total * fuel)
    if total != optimal:
        eut = _int_times_float(
            eut, _overflow_efficiency(Kind.GAS, total, optimal, rotor.overflow_tier)
        )
    eff = rotor.loose_gas_efficiency if loose else rotor.base_efficiency
    eut = gt_safe_int(to_long(_F(_F(float(eut)) * eff)))
    return Power(_capped(eut, cap), total, float(optimal), max_flow)


def _plasma(
    rotor: RotorStats, available: int, cap: int, fuel: int, loose: bool
) -> Power:
    if available == 0:
        return Power(0, 0, 0.0, 0)
    flow_f = rotor.optimal_loose_plasma_flow if loose else rotor.optimal_plasma_flow
    optimal = gt_safe_int(to_long(ceil(flow_f * 20 / float(fuel))))
    factor = _F(_f_mul_int(1.5, rotor.overflow_tier) + 1.0)
    max_flow = gt_safe_int(to_long(_F(_F(float(optimal)) * factor)))
    total = min(available, max_flow) if max_flow > 0 else 0
    if total <= 0:
        return Power(0, 0, float(optimal), max_flow)
    eut = gt_safe_int(to_long((fuel / 20.0) * total))
    if total != optimal:
        eut = _int_times_float(
            eut, _overflow_efficiency(Kind.PLASMA, total, optimal, rotor.overflow_tier)
        )
    eff = rotor.loose_plasma_efficiency if loose else rotor.base_efficiency
    eut = gt_safe_int(to_long(_F(eff * _F(float(eut)))))
    return Power(_capped(eut, cap), total, float(optimal), max_flow)


def _steam(
    kind: Kind, rotor: RotorStats, available: int, cap: int, loose: bool
) -> Power:
    real = rotor.optimal_loose_steam_flow if loose else rotor.optimal_steam_flow
    if kind is Kind.STEAM:
        # (0.5f * overflow + 1) is a float; realOptFlow a double.
        factor: float = _F(_f_mul_int(0.5, rotor.overflow_tier) + 1.0)
    else:
        # HP steam: (0.5f * overflow + 1.5) is a double.
        factor = _f_mul_int(0.5, rotor.overflow_tier) + 1.5
    max_flow = gt_safe_int(to_long(real * factor))
    total = min(available, max_flow) if max_flow > 0 else 0
    if total <= 0:
        return Power(0, 0, real, max_flow)
    eff = rotor.loose_steam_efficiency if loose else rotor.base_efficiency
    half = kind is Kind.STEAM  # steam makes half an EU per litre, superheated steam one
    optimal = gt_safe_int(to_long(real))
    eut = total
    if total == optimal:
        x = _F(_F(float(eut)) * eff)
        eut = gt_safe_int(to_long(_F(x * 0.5) if half else x))
    else:
        # Both steam turbines pass the overflow tier they read when the rotor went in, which in
        # steady state is the rotor's own.
        eut = _int_times_float(
            eut, _overflow_efficiency(kind, total, optimal, rotor.overflow_tier)
        )
        x = _F(_F(float(eut)) * eff)
        eut = max(1, gt_safe_int(to_long(_F(x * 0.5) if half else x)))
    return Power(_capped(eut, cap), total, real, max_flow)


def _sc_steam(
    rotor: RotorStats, available: int, cap: int, loose: bool, legacy: bool
) -> Power:
    real = rotor.optimal_loose_steam_flow if loose else rotor.optimal_steam_flow
    max_flow = gt_safe_int(to_long(real * _F(1.25)))
    total = min(available, max_flow) if max_flow > 0 else 0
    if total <= 0:
        return Power(0, 0, real, max_flow)
    eff = rotor.loose_steam_efficiency if loose else rotor.base_efficiency
    eut = total
    if total == real:
        eut = gt_safe_int(to_long(_F(_F(float(eut)) * eff)))
    elif legacy:
        # goodgenerator's legacy turbine: (float) |(total - real) / (float) real| in double,
        # then tEU *= efficiency.
        efficiency = _F(1.0 - _F(abs((total - real) / _F(real))))
        eut = _int_times_float(eut, efficiency)
        eut = max(1, gt_safe_int(to_long(_F(_F(float(eut)) * eff))))
    else:
        efficiency = _F(1.0 - abs(_F(_F(_F(float(total)) - _F(real)) / _F(real))))
        eut = max(1, gt_safe_int(to_long(_F(_F(_F(float(eut)) * efficiency) * eff))))
    return Power(_capped(eut, cap), total, real, max_flow)
