"""GT's 2.9 large boilers in steady state (WO-4.2, D233): ``MTELargeBoilerBase`` and its four
controllers (bronze, steel, titanium, tungstensteel).

Re-implemented from GT 5.09.54.265's behaviour (GT is LGPL-3.0; source read at the tag, nothing
copied). What the planner needs is the boiler at its steady state: full efficiency, one fuel kind
burning, no throttle circuit, maintenance done.

- **Steam.** Every tick a running boiler makes ``lEUt x 2 x efficiency / 10000`` steam, its
  ``EUT_GENERATED`` x 2 at full efficiency (10000, :data:`MAX_EFFICIENCY`; 12500 only while a
  solid and a fluid fuel burn together, which the planner does not plan). A superheated boiler
  (titanium, tungstensteel) gives a third of that as superheated steam (``superToNormalSteam``).
- **Water.** One litre of water per :data:`STEAM_PER_WATER` steam (``GTValues.STEAM_PER_WATER``),
  per superheated steam x 3 for the superheated boilers, the remainder carried over tick to tick
  (so the average is exact).
- **Fuel.** A fluid fuel is found in ``largeBoilerFakeFuels``; its recipe duration is the burn
  time in ticks GT stores for 1000 L (the steel boiler's, D230). The boiler takes 1000 L at once
  and burns ``runtimeBoost(duration)`` ticks (its burn counter is set to twice that and falls by 2
  a tick while one fuel kind burns): bronze x 2, steel x 1, titanium x 3 / 10, tungstensteel
  x 15 / 100, in Java's integer arithmetic. The superheated boilers take only GT's allowed fuels
  (:data:`ALLOWED_FUELS`, ``LargeBoilerFuelBackend.ALLOWED_FUELS``).
- **Solid fuel.** An item in the input bus burns by its furnace burn value
  (``GTModHandler.getFuelValue``, exported per solid fuel of ``largeBoilerFakeFuels``, D380): the
  boiler takes it when the value is at least 80 and at least its ``EUT_GENERATED``, one item at
  a time. Its raw burn time is the value / 80, the remainder carried from item to item
  (``excessFuel``), stretched by ``LargeBoilerFuelBackend.getBurntimeRatioTicks(ticks, 20)``
  (:func:`burntime_ratio_ticks`: past 10 s, x ``1 + ln(seconds / 10) x 0.025``, rounded half up
  to 0.01 s) and then by ``runtimeBoost`` as a fluid's (:func:`solid_burn_ticks`: the average
  over the carry's cycle, exact).
- **Throttle.** A programmed circuit ``c`` (1-25) in the controller lowers ``lEUt`` to
  ``max(25, EUT_GENERATED - c x 25)`` (``x 75`` for the superheated boilers,
  ``adjustEUtForConfig``) and stretches the burn time by ``EUT_GENERATED / lEUt``
  (``adjustBurnTimeForConfig``, the remainder carried), so EU per litre of fuel holds and fuel
  and water fall with the steam (:func:`throttled_eut`).

Checked against GT's own classes by ``tests/unit/gtmath/oracle/GtBoilerOracle.java`` (D234).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from gregtec.planner.gtmath import jmath

STEAM_PER_WATER = 160  # GTValues.STEAM_PER_WATER
MAX_EFFICIENCY = 10000
SUPER_TO_NORMAL = 3  # MTELargeBoilerBase.superToNormalSteam
THROTTLE_STEP = {
    False: 25,
    True: 75,
}  # THROTTLE_STEAM_NORMAL / _SUPERHEATED, EU/t a circuit
MIN_EUT = 25  # MIN_STEAM_OUTPUT: the throttle's floor
MAX_CIRCUIT = 25  # setupCircuit reads circuits 1-25
SOLID_DIVISOR = 80  # checkProcessing: a solid's raw burn time is its burn value / 80
RATIO_DIVIDER = 20  # checkProcessing's getBurntimeRatioTicks(burnTime, 20)
# LargeBoilerFuelBackend.ALLOWED_FUELS: the fluids and the two blocks (registry name@meta) the
# superheated boilers take.
ALLOWED_FUELS = frozenset(
    {
        "gregtech:gt.blockreinforced@6",
        "gregtech:gt.blockreinforced@7",
        "ether",
        "gasoline",
        "nitrofuel",
        "ethanol gasoline",
        "jet fuel no.3",
        "jet fuel a",
        "highoctanegasoline",
    }
)


@dataclass(frozen=True)
class Boiler:
    """One 2.9 large boiler controller class."""

    class_name: str
    eut: int  # EUT_GENERATED
    superheated: bool
    boost: tuple[
        int, int
    ]  # runtimeBoost: x numerator / denominator (Java int division)

    def runtime_boost(self, ticks: int) -> int:
        n, d = self.boost
        return ticks * n // d

    @property
    def steam_fluid(self) -> str:
        return "ic2superheatedsteam" if self.superheated else "steam"


BOILERS: dict[str, Boiler] = {
    b.class_name: b
    for b in (
        Boiler("MTELargeBoilerBronze", 480, False, (2, 1)),
        Boiler("MTELargeBoilerSteel", 1200, False, (1, 1)),
        Boiler("MTELargeBoilerTitanium", 4800, True, (3, 10)),
        Boiler("MTELargeBoilerTungstenSteel", 19200, True, (15, 100)),
    )
}


class BoilerError(ValueError):
    """The boiler can't burn this fuel."""


@dataclass(frozen=True)
class Steady:
    """A boiler burning one fuel at full efficiency, per tick (exact fractions)."""

    steam: Fraction  # L/t of steam (superheated steam for a superheated boiler)
    water: Fraction  # L/t
    fuel: Fraction  # L/t of a fluid fuel, items/t of a solid one
    burn_ticks: Fraction  # ticks 1000 L of the fuel lasts, or one item (its average)


def throttled_eut(boiler: Boiler, circuit: int) -> int:
    """``adjustEUtForConfig``: the boiler's ``lEUt`` with programmed circuit ``circuit`` (0:
    none)."""
    if not 0 <= circuit <= MAX_CIRCUIT:
        raise BoilerError(f"circuit {circuit}: 0 to {MAX_CIRCUIT}")
    return max(MIN_EUT, boiler.eut - THROTTLE_STEP[boiler.superheated] * circuit)


def burntime_ratio_ticks(ticks: int, divider: int = RATIO_DIVIDER) -> int:
    """``LargeBoilerFuelBackend.getBurntimeRatioTicks``: ``ticks / divider`` (seconds for 20)
    x ``max(1, 1 + ln(seconds / 10) x 0.025)`` in doubles, rounded half up to two decimals as a
    ``BigDecimal`` of the double, x 20, truncated."""
    seconds = ticks / divider
    ratio = seconds * max(1.0, 1 + jmath.log(seconds / 10) * 0.025)
    return int(Decimal(ratio).quantize(Decimal("0.01"), ROUND_HALF_UP) * 20)


def solid_burn_ticks(boiler: Boiler, burn_value: int) -> Fraction:
    """The ticks one item of furnace burn value ``burn_value`` lasts in ``boiler``, unthrottled:
    GT carries ``burn_value % 80`` from item to item, so of every 80 items that many burn
    ``burn_value / 80 + 1`` raw ticks and the others ``burn_value / 80``; each raw time goes
    through :func:`burntime_ratio_ticks` and ``runtimeBoost`` (at least 1 tick). The average
    over those 80 items, exact."""
    raw, carried = divmod(burn_value, SOLID_DIVISOR)
    if raw <= 0 or burn_value * 2 // boiler.eut <= 1:
        raise BoilerError(
            f"{boiler.class_name} does not take a burn value of {burn_value}"
        )
    low, high = (
        max(1, boiler.runtime_boost(burntime_ratio_ticks(t))) for t in (raw, raw + 1)
    )
    if high * boiler.eut > jmath.INT_MAX:
        raise BoilerError(f"burn value {burn_value} is past GT's int arithmetic")
    return Fraction((SOLID_DIVISOR - carried) * low + carried * high, SOLID_DIVISOR)


def steady(
    boiler: Boiler,
    *,
    fluid: str | None = None,
    duration: int = 0,
    item: str | None = None,
    burn_value: int = 0,
    circuit: int = 0,
) -> Steady:
    """``boiler`` burning ``fluid`` whose ``largeBoilerFakeFuels`` duration is ``duration``, or
    the solid ``item`` (registry name@meta) whose furnace burn value is ``burn_value``,
    throttled by ``circuit`` (0: none). ``burn_ticks`` is the unthrottled burn time of 1000 L
    or of one item; the throttle stretches it by ``eut / lEUt`` (``fuel`` has it)."""
    fuel = item if fluid is None else fluid
    if boiler.superheated and fuel not in ALLOWED_FUELS:
        raise BoilerError(f"{boiler.class_name} does not take {fuel}")
    if fluid is None:
        burn, amount = solid_burn_ticks(boiler, burn_value), 1
    else:
        burn, amount = Fraction(max(1, boiler.runtime_boost(duration))), 1000
    eut = throttled_eut(boiler, circuit)
    equivalent = Fraction(eut * 2 * MAX_EFFICIENCY, 10000)
    if boiler.superheated:
        steam = equivalent / SUPER_TO_NORMAL
        water = equivalent / (STEAM_PER_WATER * SUPER_TO_NORMAL)
    else:
        steam = equivalent
        water = equivalent / STEAM_PER_WATER
    return Steady(steam, water, Fraction(amount * eut) / (burn * boiler.eut), burn)
