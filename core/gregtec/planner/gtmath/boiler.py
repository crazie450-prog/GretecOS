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
  (:data:`ALLOWED_FUELS`, ``LargeBoilerFuelBackend.ALLOWED_FUELS``). Solid fuels need the item's
  furnace burn value, which the export does not carry (the fake-fuel map keeps it divided by 1600,
  rounded down), so they are not sized here (D233).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

STEAM_PER_WATER = 160  # GTValues.STEAM_PER_WATER
MAX_EFFICIENCY = 10000
SUPER_TO_NORMAL = 3  # MTELargeBoilerBase.superToNormalSteam
# LargeBoilerFuelBackend.ALLOWED_FUELS: the fluids (and two blocks) the superheated boilers take.
ALLOWED_FUELS = frozenset(
    {
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
    """A boiler burning one fluid fuel at full efficiency, per tick (exact fractions)."""

    steam: Fraction  # L/t of steam (superheated steam for a superheated boiler)
    water: Fraction  # L/t
    fuel: Fraction  # L/t of fuel
    burn_ticks: int  # ticks 1000 L of the fuel lasts


def steady(boiler: Boiler, *, fluid: str, duration: int) -> Steady:
    """``boiler`` burning ``fluid`` whose ``largeBoilerFakeFuels`` duration is ``duration``."""
    if boiler.superheated and fluid not in ALLOWED_FUELS:
        raise BoilerError(f"{boiler.class_name} does not take {fluid}")
    burn = max(1, boiler.runtime_boost(duration))
    equivalent = Fraction(boiler.eut * 2 * MAX_EFFICIENCY, 10000)
    if boiler.superheated:
        steam = equivalent / SUPER_TO_NORMAL
        water = equivalent / (STEAM_PER_WATER * SUPER_TO_NORMAL)
    else:
        steam = equivalent
        water = equivalent / STEAM_PER_WATER
    return Steady(steam, water, Fraction(1000, burn), burn)
