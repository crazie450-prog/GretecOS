"""Machine sizing and integerising (WO-3.4, plan §7.1 steps 5 and 6, D14, D111, D142 (a) (b), D193).

**The machine per recipe** (:func:`pick`) comes from WO-3.2's options (:func:`machines.choose`,
GT's math from WO-3.1), sized for the same player the graph was checked for
(:func:`graph.player`). Of those options, the planner takes (D193): for the base column only
base machines, for the addon column an addon machine where one runs the recipe (D12); then
unbadged before badged (a flagged, low-confidence, partial, deprecated or custom machine keeps
its badge and notes when it is the only one, D142 (a)); a single block before a multiblock
before a steam machine (the simplest at the tier, D11); the highest tier (the most overclocks,
so the fewest machines); fewer energy hatches (one hatch runs a multi at 1 A, D175); then the
mte id. Swapping the machine is WO-3.7's.

**Numbers per node** (:func:`node_size`): ``count = ceil(rate x ticks_eff / (20 x parallel))``
(``rate / crafts_per_second`` of one machine), utilisation ``rate / (count x crafts/s)``, EU/t
per machine while running, average EU/t (all machines, over time) and peak (all running at
once), amps per machine at its tier. A machine with a state variable carries both sizings:
the worst case (the default, used for the count) and the steady state (D111, D142 (b)).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from gregtec.planner import machines
from gregtec.planner.graph import Graph
from gregtec.planner.machines import MachineInfo, MachineOption, Player, Sizing

KIND_ORDER = {"singleblock": 0, "multiblock": 1, "steam": 2}


@dataclass(frozen=True)
class Sized:
    """The machine picked for one recipe; ``option`` None for a manual recipe (crafting table,
    furnace by hand) or a recipe no option fits."""

    recipe: str
    option: MachineOption | None

    @property
    def crafts_per_s(self) -> float:
        """Crafts per second of one machine (worst case); 0 without a machine."""
        return float(self.option.sizing.crafts_per_second.value) if self.option else 0.0

    @property
    def seconds(self) -> float:
        """Machine-seconds per craft, the solver's machine weight (0 without a machine)."""
        cps = self.crafts_per_s
        return 1.0 / cps if cps > 0 else 0.0


@dataclass(frozen=True)
class NodeSize:
    count: int
    util: float
    eut: int  # per machine while running
    eut_avg: float  # all machines, averaged over time
    eut_peak: int  # all machines running at once
    amps: int  # per machine, at its tier
    ticks_eff: int  # ticks per run
    parallel: int
    oc: int  # overclocks
    crafts_per_s: float  # of one machine
    steam_per_tick: float | None = None  # L/t per machine (steam machines)


def pick(
    options: tuple[MachineOption, ...], addon: bool = False
) -> MachineOption | None:
    """The option the planner sizes a recipe with (see the module docstring)."""
    usable = [o for o in options if addon or not o.addon]
    if not usable:
        return None
    return min(
        usable,
        key=lambda o: (
            o.addon != addon,
            bool(o.badges),
            KIND_ORDER[o.kind],
            -o.tier,
            len(o.hatches),
            o.mte_id,
            tuple((h.tier, h.amps, h.exotic) for h in o.hatches),
            o.coil_tier if o.coil_tier is not None else -1,
        ),
    )


def size(
    conn: sqlite3.Connection,
    graph: Graph,
    player: Player,
    *,
    addon: bool = False,
    excluded_mtes: frozenset[int] = frozenset(),
) -> dict[str, Sized]:
    """The machine for every recipe of ``graph`` (manual recipes get none)."""
    loaded: dict[str, list[MachineInfo]] = {}
    out: dict[str, Sized] = {}
    for rec in graph.recipes:
        if "manual" in rec.flags or rec.map is None:
            out[rec.id] = Sized(rec.id, None)
            continue
        info = machines.load_recipe(conn, rec.id, graph.run_id)
        if info.map not in loaded:
            loaded[info.map] = [
                m
                for m in machines.load_machines(conn, info.map, graph.run_id)
                if m.mte_id not in excluded_mtes
            ]
        sel = machines.choose(info, loaded[info.map], player)
        out[rec.id] = Sized(rec.id, pick(sel.options, addon))
    return out


def node_size(sizing: Sizing, crafts_s: float, count: int | None = None) -> NodeSize:
    """One node's numbers at ``crafts_s`` crafts per second; ``count`` (an exact count) or the
    ceiling."""
    cps = float(sizing.crafts_per_second.value)
    if count is None:
        count = math.ceil(round(crafts_s / cps, 9)) if cps > 0 and crafts_s > 0 else 0
    util = crafts_s / (count * cps) if count and cps > 0 else 0.0
    eut = int(sizing.eut.value)
    return NodeSize(
        count=count,
        util=util,
        eut=eut,
        eut_avg=eut * (crafts_s / cps if cps > 0 else 0.0),
        eut_peak=eut * count,
        amps=int(sizing.amps.value),
        ticks_eff=int(sizing.duration.value),
        parallel=int(sizing.parallel.value),
        oc=int(sizing.overclocks.value),
        crafts_per_s=cps,
        steam_per_tick=(
            float(sizing.steam_per_tick.value)
            if sizing.steam_per_tick is not None
            else None
        ),
    )
