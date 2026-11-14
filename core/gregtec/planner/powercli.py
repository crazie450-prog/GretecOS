"""``gos power``: the power catalog from the command line (WO-4.1, D230).

- ``gos power fuels [--map NAME]``: every fuel of GT's fuel maps in the newest export run, with
  its value, unit and citation (``r:<map>/<sig12>``).
- ``gos power generators``: the generator catalog: each generator's kind, new or legacy
  controller, fuel maps and how the planner sizes it.
- ``gos power turbine KIND --rotor MATERIAL --size SIZE --dynamo TIER [--amps N] [--fuel FLUID]
  [--loose] [--legacy]``: a large turbine's output at half its optimal flow, its optimal flow and
  its maximum flow, from the DB's rotor and fuel rows and the GT turbine port. These are the
  numbers ``docs/runbooks/turbines_lab.md`` checks in the Lab.
- ``gos power naquadah [--fuel FLUID] [--coolant FLUID] [--excited FLUID] [--dynamo TIER]``
  (WO-4.8, D326): GoodGenerator's Large Naquadah Reactor: its liquid air and boost lists as the
  export read them, and for each fuel of its map (or one) a steady run: EU/t, litres of fuel,
  liquid air, coolant and excited fluid a run and a second, the depleted fuel it gives back, and
  the smallest dynamo hatch up to ``--dynamo`` that holds it. The numbers
  ``docs/runbooks/turbines_lab.md`` section 4 checks in the Lab.
- ``gos power design MODE [--tier T] [--scale unit|generators|eut_cap|raw_budget --bound N]
  [--free KEY ...] [--kind KIND ...] [--fuel KEY ...] [--rotor MATERIAL SIZE] [--candidates K]
  [--options N] [--no-effort] [--json]`` (WO-4.2): 2-3 ranked power lines for one of the four
  objective modes (``best``, ``max_output``, ``output_per_effort``, ``self_sustaining``), with
  the weights printed first (``me/prefs.md``, read only; the template's defaults without one),
  each line's generator, fuel chain, net EU/t, its effort breakdown table (WO-4.3: per part
  and recipe map, the crafts, machines and hours), the validator's verdict and, for
  ``self_sustaining``, its bootstrap kit. ``--json`` prints the plans as canonical JSON lines.
  ``--accept N`` (WO-4.4): Clayton accepts option N: its JSON and ``plan`` row are saved, its
  line note written and its net EU/t joins the EU ledger as a source (``base/power.md``).

Read-only on ``GOS_HOME/data/gos.sqlite`` except ``--accept``; every number comes from the DB and
``gtmath``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.base.state import MyBase
from gregtec.db import access
from gregtec.planner import effort, fuels, generators, power
from gregtec.planner.gtmath import naquadah as gt_naquadah
from gregtec.planner.gtmath import turbine as gt_turbine
from gregtec.planner.gtmath.tiers import VN
from gregtec.planner.gtmath.turbine import Kind
from gregtec.planner.machines import current_run
from gregtec.planner.request import RequestError

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
    n = sub.add_parser(
        "naquadah", help="the Large Naquadah Reactor's runs on its fuels (WO-4.8)"
    )
    n.add_argument("--fuel", default=None, help="one fuel, by fluid name")
    n.add_argument("--coolant", default=None, help="a coolant of its list, by fluid")
    n.add_argument("--excited", default=None, help="an excited fluid of its list")
    n.add_argument(
        "--dynamo",
        type=_tier,
        default=len(VN) - 1,
        help="the highest dynamo hatch tier to pick from (default: any)",
    )
    d = sub.add_parser(
        "design", help="2-3 ranked power lines for an objective mode (WO-4.2)"
    )
    d.add_argument("mode", choices=list(power.MODES))
    d.add_argument(
        "--tier", type=_tier, default=None, help="tier cap (default: My base's)"
    )
    d.add_argument("--scale", choices=list(power.SCALES), default="unit")
    d.add_argument(
        "--bound",
        type=float,
        default=None,
        help="generators: a count; eut_cap: net EU/t; raw_budget: inputs per hour",
    )
    d.add_argument(
        "--free",
        nargs="+",
        default=None,
        metavar="KEY",
        help=f"the free set (default: {' '.join(power.DEFAULT_FREE)})",
    )
    d.add_argument("--kind", nargs="+", default=(), choices=list(power.KINDS))
    d.add_argument(
        "--fuel", nargs="+", default=(), metavar="KEY", help="fuels to consider"
    )
    d.add_argument(
        "--rotor",
        nargs=2,
        default=list(power.DEFAULT_ROTOR),
        metavar=("MATERIAL", "SIZE"),
        help="turbine rotor (default Steel large)",
    )
    d.add_argument("--candidates", type=int, default=power.TOP_FUELS)
    d.add_argument("--options", type=int, default=3)
    d.add_argument("--no-effort", action="store_true", help="skip the effort model")
    d.add_argument("--json", action="store_true", help="the plans as JSON lines")
    d.add_argument(
        "--accept",
        type=int,
        default=None,
        metavar="N",
        help="accept option N (1-3, as ranked): its JSON, plan row, line note and its source "
        "in the EU ledger (WO-4.4)",
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
        if args.power_cmd == "design":
            return show_design(conn, args, out, cfg.gos_home)
        if args.power_cmd == "naquadah":
            return show_naquadah(conn, args, out)
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


def show_naquadah(
    conn: sqlite3.Connection, args: argparse.Namespace, out: TextIO
) -> int:
    run = current_run(conn)
    entries = [
        e for e in generators.catalog(conn, run) if e.sizing == "naquadah_reactor"
    ]
    if not entries:
        print("no Large Naquadah Reactor in the export run", file=out)
        return 2
    e = entries[0]
    reactor = generators.reactor(conn, e.mte_id, run)
    if reactor is None:
        print(
            f"{e.ref}: the export run has no reactor lists: export again with this"
            " GregtecLink, then `gos db build`",
            file=out,
        )
        return 2

    def pick(
        name: str | None, boosts: tuple[gt_naquadah.Boost, ...], what: str
    ) -> gt_naquadah.Boost | None:
        if name is None:
            return None
        b = next((b for b in boosts if b.fluid == name), None)
        if b is None:
            raise ValueError(
                f"{name!r} is not one of its {what}: "
                + ", ".join(x.fluid for x in boosts)
            )
        return b

    try:
        coolant = pick(args.coolant, reactor.coolants, "coolants")
        excited = pick(args.excited, reactor.excited, "excited fluids")
    except ValueError as exc:
        print(str(exc), file=out)
        return 2
    takes = generators.exotic(conn, e.mte_id)
    print(f"{e.name} ({e.ref}), exotic hatches: {takes or 'none'}", file=out)
    print(f"liquid air: {reactor.air} L of {reactor.air_fluid} a check", file=out)
    for what, boosts, unit in (
        ("coolant", reactor.coolants, "%"),
        ("excited", reactor.excited, "x"),
    ):
        for i, b in enumerate(boosts, 1):
            print(
                f"{what} {i}: {b.fluid}, {b.amount} L a check, {b.value}{unit}",
                file=out,
            )
    fuel_rows = generators.reactor_fuels(conn, run)
    if args.fuel is not None:
        fuel_rows = [f for f in fuel_rows if f.fluid == args.fuel]
        if not fuel_rows:
            print(f"{args.fuel!r} is not a fuel of {generators.NAQUADAH_MAP}", file=out)
            return 2
    print(
        "fuel\tbasic EU/t\tburn ticks\tEU/t\tfuel L/run\tair L/run\tcoolant L/run"
        "\texcited L/run\tfuel L/s\tair L/s\tdepleted L/s\tdynamo\tref",
        file=out,
    )
    for f in sorted(fuel_rows, key=lambda f: f.basic):
        s = gt_naquadah.steady(
            reactor,
            basic=f.basic,
            duration=f.duration,
            coolant=coolant,
            excited=excited,
        )
        d = generators.dynamo(conn, run, eut=s.eut, tier_cap=args.dynamo, takes=takes)
        hatch = f"{d.name or d.ref} ({d.ref})" if d else "none holds it"
        print(
            f"{f.fluid}\t{f.basic}\t{f.duration}\t{s.eut}\t{s.fuel}\t{s.air}\t{s.coolant}"
            f"\t{s.excited}\t{s.per_second(s.fuel):.6g}\t{s.per_second(s.air):.6g}"
            f"\t{s.per_second(s.depleted):.6g}\t{hatch}\t{f.ref}",
            file=out,
        )
    return 0


def show_design(
    conn: sqlite3.Connection, args: argparse.Namespace, out: TextIO, home: Path
) -> int:
    vault = home / "vault"
    if args.json and getattr(args, "accept", None) is not None:
        print("--accept prints what it did: leave out --json", file=out)
        return 2
    try:
        req = power.PowerRequest(
            mode=args.mode,
            tier_cap=args.tier,
            scale=args.scale,
            bound=args.bound,
            free=tuple(args.free) if args.free else power.DEFAULT_FREE,
            kinds=tuple(args.kind),
            fuels=tuple(args.fuel),
            rotor=(args.rotor[0], args.rotor[1]),
            candidates=args.candidates,
            options=args.options,
        )
        prefs = power.read_prefs(vault)
        d = power.design(
            conn, req, MyBase.from_db(conn), prefs=prefs, with_effort=not args.no_effort
        )
    except (RequestError, power.PowerError, LookupError) as exc:
        print(str(exc).strip("'\""), file=out)
        return 2
    if args.json:
        for p in d.options:
            print(power.dumps(p), file=out)
        return 0 if d.options else 1
    print(d.weights, file=out)
    for n in (*d.notes, *(prefs.notes)):
        print(f"note: {n}", file=out)
    if not d.options:
        print(f"no power line at {VN[d.tier_cap]} for mode {req.mode}", file=out)
        for gid, why in d.rejected:
            print(f"  {gid}: {why}", file=out)
        return 1
    for p in d.options:
        pw, r = p["power"], p["rank"]
        print(f"{r['position']}. {p['name']}  [{p['id']}]", file=out)
        print(
            f"   net {pw['net_eut']:g} EU/t = {pw['gross_eut']:g} out - {pw['chain_eut']:g}"
            f" the chain uses; fuel {pw['fuel']['id']} {pw['fuel']['rate_s']:g}/s"
            f" ({pw['fuel']['ref']})",
            file=out,
        )
        print(
            f"   score {r['score']:g}, upkeep"
            f" {p['metrics']['upkeep']:g}/h, machines {p['metrics']['machines']}",
            file=out,
        )
        for line in effort.text(p["metrics"], indent="   "):
            print(line, file=out)
        proof = p["closed"]
        if proof["ok"]:
            print("   closed: no import outside the free set (validator)", file=out)
        else:
            parts = [f"{i['id']} {i['rate_s']:g}/s" for i in proof["imports"]]
            print("   imports: " + (", ".join(parts) or "-"), file=out)
        for node in p["nodes"]:
            count = node.get("count")
            print(
                f"     {node['n']} {node['recipe']} x{count if count else '-'}",
                file=out,
            )
        kit = p.get("bootstrap")
        if kit:
            items = [
                *(f"{x['id']} x{x['qty']}" for x in kit["seeds_saplings"]),
                *(f"{x['id']} x{x['qty']}" for x in kit["kept_inputs"]),
                *(f"{x['tool']} x{x['qty']}" for x in kit["tools"]),
                *(f"{x['part']} x{x['qty']}" for x in kit["parts"]),
                *(f"{x['id']} x{x['qty']} (starter fuel)" for x in kit["starter_fuel"]),
            ]
            print("   bootstrap kit: " + "; ".join(items), file=out)
    print(f"({d.pool} lines ranked; {len(d.rejected)} candidates rejected)", file=out)
    if getattr(args, "accept", None) is not None:
        return accept_option(home, d, args.accept, out)
    return 0


def accept_option(home: Path, d: power.PowerDesign, n: int, out: TextIO) -> int:
    """``--accept N``: Clayton accepts option N of this design (WO-4.4, D238)."""
    from gregtec.planner import designer, plancli

    if not 1 <= n <= len(d.options):
        print(f"--accept {n}: there are {len(d.options)} options", file=out)
        return 2
    try:
        done = designer.accept_power(home, d.options[n - 1])
    except designer.DesignerError as exc:
        print(f"refused: {exc}", file=out)
        return 2
    print(f"{done.id}: accepted; line note {done.line or '-'}", file=out)
    plancli.print_moved(done, out)
    return 0
