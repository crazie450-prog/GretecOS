"""GoodGenerator's Large Naquadah Reactor (WO-4.8, D326): ``MTELargeNaquadahReactor`` (mte 15537
in GT 5.09.54.265, GoodGenerator's multiblock moved into GT's package), its fuel map
``gg.recipe.naquadah_reactor`` and its coolant and excited-fluid boosts.

Re-implemented from GT 5.09.54.265's behaviour (GT is LGPL-3.0; source read at the tag, nothing
copied): ``MTELargeNaquadahReactor.checkProcessing_EM``, ``onRunningTick``, ``consumeFuel``,
``getExcited``, ``consumeCoolant`` and ``addAutoEnergy``, ``TTMultiblockBase.onPostTick`` (the run
loop) and ``GoodGenerator``'s ``FuelRecipeLoader.RegisterFuel`` (the map's recipes).

- **The fuel map.** One recipe per fuel: 1 L of the fuel in, 1 L of its depleted fluid out, the
  burn time (the recipe's duration, ticks) and the **basic output** (``LNG_BASIC_OUTPUT``, the
  recipe's special value, EU/t). GoodGenerator's config (``GGConfigLoader``) sets both per fuel;
  the export reads them from the recipes, never from here.
- **A run.** ``checkProcessing_EM`` finds the fuel's recipe among the input hatches' fluids,
  looks for the first **excited fluid** of the reactor's list that is present in its full amount
  (it does not take it), and takes ``m`` litres of the fuel, ``m`` the excited fluid's multiplier
  (1 without one; it takes ``m`` litres whatever the recipe's input amount, and fails the check if
  fewer are there). The run lasts the burn time and puts out ``m`` litres of the depleted fluid at
  its end; the next run is checked on the same tick (``onPostTick``), so runs follow each other
  with no gap.
- **Each second of a run** (every tick whose progress is a multiple of 20: progress 0, 20, ...
  below the burn time, so ``ceil(burn time / 20)`` checks a run, not burn time / 20): the reactor
  takes the config's **liquid air** per second (2400 L at GoodGenerator's default) or makes
  nothing until the next check (the check's tick puts out nothing at all); then the first coolant
  of its list that is there in its amount (taken; its efficiency %, else 100 %); then the excited
  fluid it locked at the check, in its amount (taken: the run's multiplier, else x1 for this
  second). Its output is ``basic x efficiency x multiplier / 100`` EU/t (long arithmetic, rounded
  down), put into its one dynamo hatch every tick.
- **The dynamo.** One dynamo hatch (GT's, TecTech's multi-amp or laser), and a tick's output
  above its voltage x amps stops the machine (``INSUFFICIENT_DYNAMO``).

The coolant and excited lists (fluid, litres a second, efficiency or multiplier) and the liquid
air rate are GoodGenerator's config as the game has it: the export reads them from the reactor's
own lists (D326), and the planner takes them from the DB.

Checked against GT's own class by ``tests/unit/gtmath/oracle/GtNaquadahOracle.java``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

CHECK_TICKS = 20  # onRunningTick: mProgresstime % 20 == 0


@dataclass(frozen=True)
class Boost:
    """A coolant (``value``: efficiency %) or an excited fluid (``value``: the multiplier) as the
    reactor's list has it: ``amount`` litres a check."""

    fluid: str
    amount: int
    value: int


@dataclass(frozen=True)
class Reactor:
    """What the reactor's math reads: GoodGenerator's config as the game has it."""

    air: int  # liquid air per check (LiquidAirConsumptionPerSecond); 0: none taken
    coolants: tuple[Boost, ...] = ()  # in the reactor's order (the first present wins)
    excited: tuple[Boost, ...] = ()
    air_fluid: str = "liquidair"


def checks(duration: int) -> int:
    """How many per-second checks a run of ``duration`` ticks has (progress 0, 20, ... < it)."""
    return max(0, -(-duration // CHECK_TICKS))


@dataclass(frozen=True)
class Steady:
    """One run in steady state, every tank full (per run, then per second)."""

    duration: int  # ticks
    checks: int
    fuel: int  # litres of the fuel a run takes
    depleted: int  # litres of the depleted fluid a run puts out
    air: int  # litres of liquid air a run takes
    coolant: int  # litres of the coolant a run takes
    excited: int  # litres of the excited fluid a run takes
    efficiency: int  # %
    multiplier: int
    eut: int  # EU/t out, every tick

    @property
    def eu(self) -> int:
        """EU a run puts out."""
        return self.eut * self.duration

    def per_second(self, litres: int) -> float:
        return litres * 20 / self.duration


def output(basic: int, efficiency: int, multiplier: int) -> int:
    """``trueOutput``: ``(long) basic x (long) efficiency x (long) multiplier / 100``."""
    return basic * efficiency * multiplier // 100


def steady(
    reactor: Reactor,
    *,
    basic: int,
    duration: int,
    coolant: Boost | None = None,
    excited: Boost | None = None,
) -> Steady:
    """The reactor on one fuel with every tank full: ``coolant`` and ``excited`` are the boosts
    it is fed (one of its lists' entries each, or none)."""
    if duration <= 0:
        raise ValueError(
            f"burn time {duration} ticks: a fuel recipe lasts at least 1 tick"
        )
    if coolant is not None and coolant not in reactor.coolants:
        raise ValueError(f"{coolant.fluid} is not one of the reactor's coolants")
    if excited is not None and excited not in reactor.excited:
        raise ValueError(f"{excited.fluid} is not one of the reactor's excited fluids")
    n = checks(duration)
    mult = excited.value if excited else 1
    eff = coolant.value if coolant else 100
    return Steady(
        duration=duration,
        checks=n,
        fuel=mult,
        depleted=mult,
        air=n * reactor.air,
        coolant=n * coolant.amount if coolant else 0,
        excited=n * excited.amount if excited else 0,
        efficiency=eff,
        multiplier=mult,
        eut=output(basic, eff, mult),
    )


# --- the run loop, tick by tick (what the oracle checks) ----------------------------------------


@dataclass
class Tanks:
    """The input hatches' fluids, litres by fluid name."""

    litres: dict[str, int] = field(default_factory=dict)

    def take(self, fluid: str | None, amount: int) -> bool:
        """``consumeFuel``: take ``amount`` from one stack holding at least that much."""
        if fluid is None or self.litres.get(fluid, 0) < amount:
            return False
        self.litres[fluid] -= amount
        return True

    def has(self, fluid: str, amount: int) -> bool:
        return self.litres.get(fluid, 0) >= amount


@dataclass(frozen=True)
class RunResult:
    """One run as the oracle reports it."""

    ok: bool  # the check found the fuel
    duration: int = 0
    fuel: int = 0
    air: int = 0
    coolant: int = 0
    excited: int = 0
    depleted: int = 0
    eu: int = 0
    eut: int = 0  # trueOutput after the run's last tick
    efficiency: int = 0
    stopped: str = ""


@dataclass
class _State:
    true_output: int = 0
    true_eff: int = 0


def simulate(
    reactor: Reactor,
    tanks: Tanks,
    *,
    fuel: str,
    basic: int,
    duration: int,
    cap: int,
    runs: int,
    coolants: Sequence[str] = (),
    excited: Sequence[str] = (),
) -> list[RunResult]:
    """``runs`` runs of the reactor on ``fuel`` (one recipe: ``basic`` EU/t, ``duration`` ticks)
    from ``tanks``, its one dynamo taking ``cap`` EU/t (voltage x amps). ``coolants`` and
    ``excited`` name which of the reactor's list entries the case's tanks hold (the rest are
    absent). Stops at a failed check or a machine stop, as the oracle does."""
    state = _State()
    present_cool = [b for b in reactor.coolants if b.fluid in set(coolants)]
    present_exc = [b for b in reactor.excited if b.fluid in set(excited)]
    out: list[RunResult] = []
    for _ in range(runs):
        before = dict(tanks.litres)
        if not tanks.has(fuel, 1):
            out.append(RunResult(False))
            break
        lock = next((b for b in present_exc if tanks.has(b.fluid, b.amount)), None)
        mult = lock.value if lock else 1
        if not tanks.take(fuel, mult):
            out.append(RunResult(False))
            break
        eu = 0
        stopped = ""
        for t in range(duration):
            if t % CHECK_TICKS == 0:
                if reactor.air != 0 and not tanks.take(reactor.air_fluid, reactor.air):
                    state.true_output = 0
                    state.true_eff = 0
                    continue  # no output this tick (onRunningTick returns first)
                cool = next(
                    (b for b in present_cool if tanks.take(b.fluid, b.amount)), None
                )
                eff = cool.value if cool else 100
                time = mult if lock and tanks.take(lock.fluid, lock.amount) else 1
                state.true_output = output(basic, eff, time)
                state.true_eff = eff
            if cap >= state.true_output:
                eu += state.true_output
            else:
                stopped = "insufficient_dynamo"
                break

        def used(fluid: str, before: dict[str, int] = before) -> int:
            return before.get(fluid, 0) - tanks.litres.get(fluid, 0)

        out.append(
            RunResult(
                True,
                duration=duration,
                fuel=used(fuel),
                air=used(reactor.air_fluid),
                coolant=sum(used(b.fluid) for b in present_cool),
                excited=sum(used(b.fluid) for b in present_exc),
                depleted=mult,
                eu=eu,
                eut=state.true_output,
                efficiency=state.true_eff,
                stopped=stopped,
            )
        )
        if stopped:
            break
    return out
