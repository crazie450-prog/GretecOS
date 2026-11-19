"""``gos footprints``: the footprint library from the command line (WO-10.2, D305, D306).

- ``gos footprints list``: one line per footprint (facing north, the tiers it was built with):
  key, size, cells, hatch cells, source, lineage; and the structure rows that make none.
- ``gos footprints show CONTROLLER [--variant V] [--facing F] [--tier CH=V ...]``: one footprint
  as JSON (cells, bounding box, hatch markers, materials, tier choices). CONTROLLER is
  ``<registry>@<meta>`` or ``mte:<id>``.
- ``gos footprints coverage [--all] [--json]``: which multis in the accepted plans, My base and
  the machine catalog have a footprint and which need a Lab capture.
- ``gos footprints capture FILE [--dry-run]``: reads a Lab capture (:mod:`gregtec.build.capture`):
  a WorldEdit ``//schem save`` ``.schematic`` (WO-10.9, D350, D351; ``--controller x,y,z`` when the
  DB's machine table doesn't single out its controller, ``--facing`` when the controller's NBT has
  none, ``--trigger``/``--channel`` as built, ``--source-world`` for a file without a mapping) or a
  ``gos-capture`` JSON file, told apart by content; and loads it into ``GOS_HOME/data/gos.sqlite``.
- ``gos footprints compare FILE``: a capture (either kind) of a multi the library has against its
  footprint (L1's "cells match structure"); exit 1 when a cell differs.

Everything but ``capture`` only reads the DB. A capture file is read once through ``safe_open``
and never written; nothing here reads a world (``--source-world`` reads only its ``level.dat``).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.build import capture, coverage, worldreg
from gregtec.build import footprints as fp
from gregtec.db import access

COMMANDS = ("footprints",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "footprints",
        help="multiblock footprints, their coverage and Lab captures (WO-10.2)",
        description="Reads the structure table in GOS_HOME/data/gos.sqlite.",
    )
    sub = p.add_subparsers(dest="footprints_command", required=True)
    ls = sub.add_parser("list", help="every footprint, facing north")
    ls.add_argument("--json", action="store_true", help="JSON lines, one per footprint")
    show = sub.add_parser("show", help="one footprint as JSON")
    show.add_argument("controller", help="<registry name>@<meta>, or mte:<id>")
    show.add_argument(
        "--variant", default=None, help="a variant key (default: smallest)"
    )
    show.add_argument(
        "--facing",
        default=fp.BUILT_FACING,
        help=f"{', '.join(fp.FACINGS)} (default north)",
    )
    show.add_argument(
        "--tier",
        action="append",
        default=[],
        metavar="CHANNEL=VALUE",
        help="a tier channel's value (coil, glass, pipe, ...); repeatable",
    )
    cov = sub.add_parser(
        "coverage",
        help="which needed multis have a footprint, which need a Lab capture",
    )
    cov.add_argument(
        "--all", action="store_true", help="also list every catalog multi without"
    )
    cov.add_argument("--json", action="store_true", help="the report as JSON")
    cap = sub.add_parser(
        "capture",
        help="load a Lab capture (a //schem save .schematic, or gos-capture JSON) into the DB",
    )
    cap.add_argument("file", type=Path, help="the capture file")
    cap.add_argument(
        "--dry-run", action="store_true", help="read and check it, write nothing"
    )
    cmp = sub.add_parser(
        "compare",
        help="a Lab capture of a multi the library has, against its footprint",
    )
    cmp.add_argument("file", type=Path, help="the capture file")
    for each in (cap, cmp):
        each.add_argument(
            "--controller",
            default=None,
            metavar="X,Y,Z",
            help="a .schematic's controller cell (box coordinates, as `gos schematic read` "
            "lists the tile entities), when the DB doesn't single it out",
        )
        each.add_argument(
            "--facing",
            default=None,
            help="where the controller's front faced, when the .schematic saved none: "
            + ", ".join(fp.FACINGS),
        )
        each.add_argument(
            "--trigger",
            type=int,
            default=None,
            metavar="N",
            help="the projector's trigger stack size it was built with (default 1)",
        )
        each.add_argument(
            "--channel",
            action="append",
            default=None,
            metavar="CHANNEL=VALUE",
            help="a StructureLib channel set with /sl_channel set; repeatable",
        )
        each.add_argument(
            "--source-world",
            type=Path,
            default=None,
            help="the Lab world a .schematic without a mapping was copied in (its level.dat)",
        )
    for each in (ls, show, cov, cap, cmp):
        each.add_argument("--config", type=Path, default=None, help="gos.toml to read")


def run(args: argparse.Namespace, out: TextIO = sys.stdout) -> int:
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    db = access.db_path(cfg.gos_home)
    cmd = args.footprints_command
    if cmd == "capture":
        return _capture(db, args, out)
    if not db.is_file():
        print(f"{db} doesn't exist: run `gos db build` first", file=out)
        return 2
    conn = access.connect(db, readonly=True)
    try:
        if cmd == "list":
            return _list(conn, args, out)
        if cmd == "show":
            return _show(conn, args, out)
        if cmd == "compare":
            return _compare(conn, args, out)
        plans, bad = coverage.accepted_plans(cfg.gos_home, conn)
        report = coverage.coverage(conn, plans, unreadable=bad)
    except sqlite3.Error as exc:
        print(f"Can't read the DB: {exc}", file=out)
        return 2
    finally:
        conn.close()
    if args.json:
        print(coverage.dumps(report), file=out)
    else:
        for line in coverage.render(report, all_catalog=args.all):
            print(line, file=out)
    return 0


def _list(conn: sqlite3.Connection, args: argparse.Namespace, out: TextIO) -> int:
    lib = fp.library(conn)
    for f in lib.footprints:
        if args.json:
            row = {
                "key": f.key,
                "name": f.name,
                "size": list(f.size),
                "cells": len(f.cells),
                "hatch_cells": len(f.hatch_cells),
                "source": f.source,
                "lineage": f.lineage,
                "family": f.family,
            }
            print(json.dumps(row, ensure_ascii=False), file=out)
            continue
        size = "x".join(str(n) for n in f.size)
        print(
            f"{f.key}\t{f.name or '?'}\t{size}\t{len(f.cells)} cells\t"
            f"{len(f.hatch_cells)} hatch cells\t{f.source}\t{f.lineage or '-'}",
            file=out,
        )
    if not args.json:
        print(f"{len(lib.footprints)} footprints (x4 facings)", file=out)
        for s in lib.skipped:
            print(
                f"  no footprint: {s.controller} {s.variant} ({s.source}): {s.reason}",
                file=out,
            )
    return 0


def _show(conn: sqlite3.Connection, args: argparse.Namespace, out: TextIO) -> int:
    try:
        reg, meta = fp.parse_controller(args.controller)
        f = fp.library(conn).get(
            reg,
            meta,
            args.variant,
            fp.parse_facing(args.facing),
            fp.parse_tiers(args.tier),
        )
    except fp.FootprintError as exc:
        print(str(exc), file=out)
        return 1
    print(json.dumps(f.to_json(), indent=1, ensure_ascii=False), file=out)
    return 0


def _options(args: argparse.Namespace) -> capture.SchematicOptions:
    """The ``.schematic`` options (FootprintError or WorldRegistryError when one is malformed)."""
    at = None
    if args.controller is not None:
        parts = args.controller.replace(" ", "").split(",")
        try:
            x, y, z = (int(v) for v in parts)
        except ValueError:
            raise fp.FootprintError(
                f"--controller {args.controller!r} is not x,y,z"
            ) from None
        at = (x, y, z)
    return capture.SchematicOptions(
        controller_at=at,
        facing=None if args.facing is None else fp.parse_facing(args.facing),
        trigger_stack_size=args.trigger,
        channels=None if args.channel is None else fp.parse_tiers(args.channel),
        source_ids=None
        if args.source_world is None
        else worldreg.read_block_ids(args.source_world),
    )


def _read(args: argparse.Namespace, conn: sqlite3.Connection | None) -> capture.Capture:
    return capture.read_any(args.file, conn, _options(args))


def _capture(db: Path, args: argparse.Namespace, out: TextIO) -> int:
    ro = access.connect(db, readonly=True) if db.is_file() else None
    try:
        cap = _read(args, ro)
        f = fp.build(cap.row())
    except (capture.CaptureError, fp.FootprintError) as exc:
        print(f"Not a capture: {exc}", file=out)
        return 1
    except worldreg.WorldRegistryError as exc:
        print(f"Can't read the source world's ids: {exc}", file=out)
        return 2
    except sqlite3.Error as exc:
        print(f"Can't read the DB: {exc}", file=out)
        return 2
    finally:
        if ro is not None:
            ro.close()
    size = "x".join(str(n) for n in f.size)
    print(
        f"{cap.controller} {cap.variant}: {size}, {len(f.cells)} cells, "
        f"{len(f.hatch_cells)} hatch cells (captured facing {cap.facing}, {cap.tool})",
        file=out,
    )
    if cap.trigger_stack_size == 1 and not cap.channels:
        print(
            "note: variant t1, no channels: if the projection was built at another tier, "
            "say so (--trigger/--channel for a .schematic, trigger_stack_size/channels in a "
            "gos-capture file)",
            file=out,
        )
    if args.dry_run:
        return 0
    if not db.is_file():
        print(f"{db} doesn't exist: run `gos db build` first", file=out)
        return 2
    conn = access.connect(db)
    try:
        loaded = capture.load(conn, cap, args.file)
    except capture.CaptureError as exc:
        print(f"Not loaded: {exc}", file=out)
        return 1
    except sqlite3.Error as exc:
        print(f"Not loaded: {exc}", file=out)
        return 2
    finally:
        conn.close()
    verb = "replaced the earlier capture" if loaded.replaced else "loaded"
    print(f"{verb}: {loaded.footprint.key} (structure run {loaded.run_id})", file=out)
    return 0


def _compare(conn: sqlite3.Connection, args: argparse.Namespace, out: TextIO) -> int:
    try:
        result = capture.compare(_read(args, conn), fp.library(conn))
    except (
        capture.CaptureError,
        fp.FootprintError,
        worldreg.WorldRegistryError,
    ) as exc:
        print(f"Not compared: {exc}", file=out)
        return 2
    f = result.footprint
    if not result.differences:
        print(f"{f.key}: every cell matches ({len(f.cells)} cells)", file=out)
        return 0
    print(f"{f.key}: {len(result.differences)} cell(s) differ", file=out)
    for line in result.differences[:200]:
        print(f"  {line}", file=out)
    return 1
