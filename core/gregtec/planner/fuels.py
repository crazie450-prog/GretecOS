"""The fuel catalog (WO-4.1, D230; plan §7.2 "Catalog"): GT's fuel maps, decoded one by one.

Every fuel is a recipe in one of GT 5.09.54.265's fuel maps [E§4.1]: one input (a fluid, or an
item such as a cell or a fuel rod), EU/t and duration 0, and ``mSpecialValue`` = ``FUEL_VALUE``.
What that number means depends on the map, so each map is read by its own rule (:data:`MAPS`,
from GT's maps, frontends and the generators that burn them, read at the pin):

- **EU per litre**: diesel, extreme diesel, gas turbine, hot, semi-fluid, plasma, magic and
  rocket fuels whose input is a fluid, or a fluid container GT indexes by its fluid (most fuels
  are registered as cells: the fuel is the fluid in it, ``fuel_container``). Liquid engines and
  turbines read ``FUEL_VALUE`` as EU per litre [E§4.1]: the Large Combustion Engine burns output /
  value L/t, the gas turbine makes value EU per litre at its efficiency, a single block value x
  efficiency / 100; the plasma turbine is the exception (value / 20 per litre, ``gtmath.turbine``).
  NEI's page shows rocket fuels at 3x (``FuelFrontend``'s multiplier); GT's rocket fuel
  generators burn them at 1x, so the catalog keeps GT's value (D231).
- **EU per item**: an item that holds no fluid (naquadah fuel rods, magic items), special x
  1000 EU per item at 100 % (a single block: special x 10 x efficiency).
- **Days** (RTG pellets): special x 365 (``gt.nei.rtg.days``), how long a pellet lasts.
- **Large boiler burn time** (``largeBoilerFakeFuels``, NEI-only recipes GT builds from the other
  maps and furnace fuels): for a fluid, the steel boiler's ticks per 1000 L (the recipe's
  duration, GT's ``getBurntimeRatio`` already applied); for a solid fuel, its furnace burn value
  / 1600 (special).

A single-block generator then turns a fuel into EU at its own efficiency
(:func:`single_block_eu`, ``MTEBasicGenerator.getFuelValue``); GregtecLink exports what GT's own
generators give for each fuel, and the harness tests check this module against it map by map.
Numbers come only from the DB (``fuel`` rows built by ``gos db build``), cited ``r:<map>/<sig12>``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum


class Unit(Enum):
    EU_PER_L = "EU/L"
    EU_PER_ITEM = "EU/item"
    DAYS = "days"
    BOILER_TICKS = "steel boiler ticks per 1000 L"
    BURN_RATIO = "furnace burn value / 1600"


@dataclass(frozen=True)
class FuelMap:
    """One of GT's fuel maps and how its ``FUEL_VALUE`` reads."""

    map: str  # the recipe map's unlocalized name, as the DB keys it
    gt_name: str  # its field in RecipeMaps (plan §7.2's list)
    nei_multiplier: (
        int  # what FuelFrontend multiplies the value by on NEI's page (display only)
    )
    rule: str  # "eu" (EU per litre or item), "days" (RTG) or "boiler" (large boilers)
    burned_by: str  # what burns it


MAPS: tuple[FuelMap, ...] = (
    FuelMap(
        "gt.recipe.dieselgeneratorfuel",
        "dieselFuels",
        1,
        "eu",
        "combustion generators, Large Combustion Engine",
    ),
    FuelMap(
        "gt.recipe.extremedieselgeneratorfuel",
        "extremeDieselFuels",
        1,
        "eu",
        "Extreme Combustion Engine (diesel fuels of 1500 EU/L or more)",
    ),
    FuelMap(
        "gt.recipe.gasturbinefuel",
        "gasTurbineFuels",
        1,
        "eu",
        "gas turbines, Large Gas Turbine",
    ),
    FuelMap(
        "gt.recipe.thermalgeneratorfuel", "hotFuels", 1, "eu", "geothermal generators"
    ),
    FuelMap(
        "gt.recipe.semifluidboilerfuels",
        "denseLiquidFuels",
        1,
        "eu",
        "large boilers (through largeBoilerFakeFuels)",
    ),
    FuelMap(
        "gt.recipe.semifluidgeneratorfuels",
        "semiFluidFuels",
        1,
        "eu",
        "semi-fluid generators, Large Semifluid Burner",
    ),
    FuelMap(
        "gt.recipe.plasmageneratorfuels",
        "plasmaFuels",
        1,
        "eu",
        "plasma generators, Large Plasma Turbine",
    ),
    FuelMap("gt.recipe.magicfuels", "magicFuels", 1, "eu", "magic energy converters"),
    FuelMap(
        "gt.recipe.smallnaquadahreactor",
        "smallNaquadahReactorFuels",
        1,
        "eu",
        "naquadah reactor (EV)",
    ),
    FuelMap(
        "gt.recipe.largenaquadahreactor",
        "largeNaquadahReactorFuels",
        1,
        "eu",
        "naquadah reactor (IV)",
    ),
    FuelMap(
        "gt.recipe.fluidnaquadahreactor",
        "hugeNaquadahReactorFuels",
        1,
        "eu",
        "naquadah reactor (LuV)",
    ),
    FuelMap(
        "gt.recipe.hugenaquadahreactor",
        "extremeNaquadahReactorFuels",
        1,
        "eu",
        "naquadah reactor (ZPM)",
    ),
    FuelMap(
        "gt.recipe.extrahugenaquadahreactor",
        "ultraHugeNaquadahReactorFuels",
        1,
        "eu",
        "naquadah reactor (UV)",
    ),
    FuelMap(
        "gt.recipe.rocketenginefuel",
        "rocketFuels",
        3,
        "eu",
        "rocket fuel generators, Large Rocket Engine",
    ),
    FuelMap("gt.recipe.RTGgenerators", "rtgFuels", 1, "days", "radioisotope generator"),
    FuelMap(
        "gt.recipe.largeboilerfakefuels",
        "largeBoilerFakeFuels",
        1,
        "boiler",
        "large boilers (NEI's burn times)",
    ),
)
BY_MAP = {m.map: m for m in MAPS}
BY_NAME = {m.gt_name: m for m in MAPS}


@dataclass(frozen=True)
class Decoded:
    unit: Unit
    value: float


def decode(fuel_map: FuelMap, *, special: int, duration: int, fluid: bool) -> Decoded:
    """One fuel recipe's ``FUEL_VALUE`` in its map's unit (see the module doc). ``fluid``: the
    fuel is a fluid (a fluid input, or a fluid container GT indexes by its fluid)."""
    if fuel_map.rule == "days":
        return Decoded(Unit.DAYS, float(special * 365))
    if fuel_map.rule == "boiler":
        if fluid:
            return Decoded(Unit.BOILER_TICKS, float(duration))
        return Decoded(Unit.BURN_RATIO, float(special))
    if fluid:
        return Decoded(Unit.EU_PER_L, float(special))
    return Decoded(Unit.EU_PER_ITEM, float(special * 1000))


def single_block_eu(
    *, special: int, efficiency: int, fluid_input: bool, consumed: int = 1
) -> int:
    """``MTEBasicGenerator.getFuelValue``: the EU a single-block generator of ``efficiency`` %
    makes from one operation of a fuel (``consumed`` litres of a fluid, or one item), before
    GT's int cap. Fluid: special x efficiency x litres / 100 (long arithmetic, truncated). Item:
    special x 10 x efficiency (a cell is 1000 L, so 1000 x special x efficiency / 100)."""
    if fluid_input:
        return special * efficiency * consumed // 100
    return special * 10 * efficiency


@dataclass(frozen=True)
class Fuel:
    """A ``fuel`` row: one fuel of one map, decoded, with its citation."""

    ref: str  # r:<map>/<sig12>
    map: str
    gt_name: str
    input: str  # the fluid's name, or registry@meta of the item
    fluid: str | None  # the fluid it burns (a container's), else None
    fuel_value: int  # GT's FUEL_VALUE (the special value), raw
    unit: str
    value: float


def fuels(
    conn: sqlite3.Connection, *, gt_name: str | None = None, run_id: int | None = None
) -> list[Fuel]:
    """The ``fuel`` rows of the newest export run (or ``run_id``), optionally of one map; GT's
    hidden duplicates left out."""
    if run_id is None:
        row = conn.execute("SELECT MAX(run_id) FROM fuel").fetchone()
        if row is None or row[0] is None:
            return []
        run_id = int(row[0])
    sql = (
        "SELECT ref, map, gt_name, input, fluid, fuel_value, unit, value FROM fuel"
        " WHERE run_id = ? AND NOT hidden"
    )
    args: list[object] = [run_id]
    if gt_name is not None:
        sql += " AND gt_name = ?"
        args.append(gt_name)
    sql += " ORDER BY gt_name, input, ref"
    return [
        Fuel(r[0], r[1], r[2], r[3], r[4], int(r[5]), str(r[6]), float(r[7]))
        for r in conn.execute(sql, args)
    ]
