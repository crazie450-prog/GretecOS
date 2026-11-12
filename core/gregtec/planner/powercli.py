"""``gos power``: the power catalog from the command line (WO-4.1, D230).

- ``gos power fuels [--map NAME]``: every fuel of GT's fuel maps in the newest export run, with
  its value, unit and citation (``r:<map>/<sig12>``).
- ``gos power generators``: the generator catalog: each generator's kind, new or legacy
  controller, fuel maps and how the planner sizes it.
- ``gos power turbine KIND --rotor MATERIAL --size SIZE --dynamo TIER [--amps N] [--fuel FLUID]
  [--loose] [--legacy]``: a large turbine's output at half its optimal flow, its optimal flow and
  its maximum flow, from the DB's rotor and fuel rows and the GT turbine port. These are the
  numbers ``docs/runbooks/turbines_lab.md`` checks in the Lab.

Read-only on ``GOS_HOME/data/gos.sqlite``; every number comes from the DB and ``gtmath``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.db import access
from gregtec.planner import fuels, generators
from gregtec.planner.gtmath import turbine as gt_turbine
from gregtec.planner.gtmath.tiers import VN
from gregtec.planner.gtmath.turbine import Kind

COMMANDS = ("power",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "power",
        help="the power catalog: fuels, generators, turbine output (WO-4.1)",
        description="Reads the newest export run in GOS_HOME/data/gos.sqlite (read-only).",
    )
    p.add_argument("--config", type=Path, default=None, help="gos.toml to read")
    sub = p.add_subparsers(dest="power_cmd", required=True)
    f = sub.add_parser("fuels", help="every fuel of GT's fuel maps, decoded")
    f.add_argument(
        "--map", default=None, help="one map, by GT's name (dieselFuels, ...)"
    )
    sub.add_parser("generators", help="the generator catalog")
    t = sub.add_parser(
        "turbine", help="a large turbine's output for one rotor and dynamo"
    )
    t.add_argument("kind", choices=[k.value for k in Kind])
    t.add_argument(
        "--rotor", required=True, help="rotor material, GT's name (Titanium, ...)"
    )
    t.add_argument("--size", required=True, choices=list(generators.SIZES))
    t.add_argument(
        "--dynamo",
        required=True,
        type=_tier,
        help=f"dynamo hatch tier ({VN[0]}..{VN[-1]} or 0-{len(VN) - 1})",
    )
    t.add_argument(
        "--amps", type=_amps, default=1, help="the dynamo hatch's amps (default 1)"
    )
    t.add_argument("--fuel", default=None, help="gas and plasma: the fuel's fluid name")
    t.add_argument("--loose", action="store_true", help="loose fitting (screwdriver)")
    t.add_argument(
        "--legacy", action="store_true", help="the legacy (pre-2.9) controller"
    )


def _tier(text: str) -> int:
    if text.isdigit():
        if int(text) >= len(VN):
            raise argparse.ArgumentTypeError(f"tier {text} is above {len(VN) - 1}")
        return int(text)
    names = [n.casefold() for n in VN]
    if text.casefold() not in names:
        raise argparse.ArgumentTypeError(f"unknown tier {text!r}")
    return names.index(text.casefold())


def _amps(text: str) -> int:
    try:
        amps = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"amps {text!r} is not a number") from None
    if amps < 1:
        raise argparse.ArgumentTypeError("amps must be 1 or more")
    return amps


def run(args: argparse.Namespace, out: TextIO = sys.stdout) -> int:
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    db = access.db_path(cfg.gos_home)
    if not db.is_file():
        print(f"{db} doesn't exist: run `gos db build` first", file=out)
        return 2
    conn = access.connect(db, readonly=True)
    try:
        if args.power_cmd == "fuels":
            return show_fuels(conn, args.map, out)
        if args.power_cmd == "generators":
            return show_generators(conn, out)
        return show_turbine(conn, args, out)
    finally:
        conn.close()


def show_fuels(conn: sqlite3.Connection, gt_name: str | None, out: TextIO) -> int:
    if gt_name is not None and gt_name not in fuels.BY_NAME:
        print(
            f"unknown fuel map {gt_name!r}: one of {', '.join(fuels.BY_NAME)}", file=out
        )
        return 2
    rows = fuels.fuels(conn, gt_name=gt_name)
    if not rows:
        print(
            "no fuel rows: run `gos db build` on an export made with WO-4.1", file=out
        )
        return 1
    print("map\tfuel\tFUEL_VALUE\tvalue\tunit\tref", file=out)
    for f in rows:
        print(
            f"{f.gt_name}\t{f.fluid or f.input}\t{f.fuel_value}\t{f.value:g}\t{f.unit}\t{f.ref}",
            file=out,
        )
    return 0


def show_generators(conn: sqlite3.Connection, out: TextIO) -> int:
    entries = generators.catalog(conn)
    if not entries:
        print("no generators: run `gos db build` first", file=out)
        return 1
    print("ref\tname\tkind\tcontroller\tsizing\tefficiency\tfuel maps", file=out)
    for e in entries:
        maps = ",".join(
            fuels.BY_MAP[m].gt_name if m in fuels.BY_MAP else m for m in e.fuel_maps
        )
        eff = "" if e.efficiency is None else f"{e.efficiency}%"
        print(
            f"{e.ref}\t{e.name}\t{e.kind}\t{'legacy' if e.legacy else 'new'}\t{e.sizing}"
            f"\t{eff}\t{maps}",
            file=out,
        )
    return 0


def show_turbine(
    conn: sqlite3.Connection, args: argparse.Namespace, out: TextIO
) -> int:
    try:
        report = generators.turbine(
            conn,
            kind=Kind(args.kind),
            material=args.rotor,
            size=args.size,
            dynamo_tier=args.dynamo,
            dynamo_amps=args.amps,
            fuel=args.fuel,
            loose=args.loose,
            legacy=args.legacy,
        )
    except (generators.CatalogError, ValueError) as exc:
        print(str(exc).strip("'\""), file=out)
        return 2
    r = report.rotor
    print(
        f"{'legacy' if report.legacy else '2.9'} large {report.kind.value} turbine, "
        f"{report.material} {report.size} rotor ({'loose' if report.loose else 'tight'}), "
        f"{VN[report.dynamo_tier]} dynamo x{report.dynamo_amps} (cap {report.cap} EU/t)",
        file=out,
    )
    print(
        f"rotor: efficiency {r.base_efficiency:.4g}, optimal flow {r.optimal_flow:g}, "
        f"overflow tier {r.overflow_tier}",
        file=out,
    )
    if report.fuel is not None:
        print(
            f"fuel: {report.fuel.fluid}, FUEL_VALUE {report.fuel.fuel_value}", file=out
        )
    unit = gt_turbine.flow_unit(report.kind)
    if unit != "L/t":
        print(
            f"flow per {gt_turbine.cycle_ticks(report.kind)}-tick cycle ({unit}):"
            " regulate the input per second; EU/t holds every tick",
            file=out,
        )
    print(f"flow\t{unit}\tEU/t\toptimal flow shown ({unit})", file=out)
    for run in report.runs:
        print(f"{run.label}\t{run.flow}\t{run.eut}\t{int(run.optimal_flow)}", file=out)
    print("refs: " + " ".join(report.refs), file=out)
    return 0
