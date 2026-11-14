"""``gos palette``: the block catalog and the palette rule from the command line (WO-10.3).

- ``gos palette check FILE``: applies plan 7.10's palette rule (:mod:`gregtec.build.palette`) to
  the palette in FILE (one ``registry@meta`` per line) against the newest block catalog in
  ``GOS_HOME/data/gos.sqlite`` and a Lab world's registry (its ``level.dat``, read-only through
  ``safe_open``). Shells are pasted only in Lab worlds, so the world is ``[paths] lab_world`` in
  the Lab, ``--world`` there, or ``--world-dir``. Exit 0: every entry accepted; 1: something
  refused; 2: bad input or setup.
- ``gos palette blocks [TEXT]``: lists catalog states (``--mod``, ``--plain``), as the
  ``block_find`` tool does.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.build import palette, worldreg
from gregtec.db import access
from gregtec.db import blocks as block_db
from gregtec.db import ores as ore_db

COMMANDS = ("palette",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "palette",
        help="the block catalog and the shell palette rule (WO-10.3)",
        description="Reads GOS_HOME/data/gos.sqlite and, for check, a Lab world's level.dat.",
    )
    sub = p.add_subparsers(dest="palette_command", required=True)
    check = sub.add_parser(
        "check",
        help="check a shell palette against the block catalog and a Lab world's registry",
        description=(
            "Refuses every entry that is air or GT's machine block, isn't in the world's "
            "registry (level.dat FML ItemData), isn't a placeable state in the block catalog, "
            "has a tile entity, or has a meta above 15 (worldedit-gtnh #30). Read-only."
        ),
    )
    check.add_argument("file", type=Path, help="one <registry name>@<meta> per line")
    where = check.add_mutually_exclusive_group()
    where.add_argument(
        "--world",
        default=None,
        help="the world's folder in the Lab's saves/ (default [paths] lab_world)",
    )
    where.add_argument(
        "--world-dir", type=Path, default=None, help="a world folder anywhere"
    )
    blocks = sub.add_parser("blocks", help="list block states in the catalog")
    blocks.add_argument(
        "text", nargs="?", default="", help="words in the name or registry name"
    )
    blocks.add_argument(
        "--mod", default=None, help="only this mod (the registry domain)"
    )
    blocks.add_argument(
        "--plain", action="store_true", help="only states without a tile entity"
    )
    for each in (check, blocks):
        each.add_argument("--config", type=Path, default=None, help="gos.toml to read")


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
    if args.palette_command == "blocks":
        return _blocks(db, args, out)
    try:
        entries = palette.parse(args.file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, palette.PaletteError) as exc:
        print(f"Can't read the palette: {exc}", file=out)
        return 2
    try:
        world = args.world_dir or ore_db.world_folder(cfg, "lab", args.world)[0]
        registry = worldreg.read(world)
    except (ore_db.OresError, worldreg.WorldRegistryError) as exc:
        print(f"Can't read the world's registry: {exc}", file=out)
        return 2
    conn = access.connect(db, readonly=True)
    try:
        report = palette.check(conn, entries, registry)
    except palette.CatalogError as exc:
        print(str(exc), file=out)
        return 2
    finally:
        conn.close()
    for line in palette.render(report):
        print(line, file=out)
    return 0 if report.ok else 1


def _blocks(db: Path, args: argparse.Namespace, out: TextIO) -> int:
    conn = access.connect(db, readonly=True)
    try:
        found = block_db.catalog_run(conn)
        if found is None:
            print("no block catalog in the DB yet", file=out)
            return 2
        rows = block_db.find(
            conn,
            found[0],
            args.text,
            mod=args.mod,
            plain=args.plain,
            limit=1000,
            offset=0,
        )
    except sqlite3.Error as exc:
        print(f"Can't read the catalog: {exc}", file=out)
        return 2
    finally:
        conn.close()
    print(f"run: {found[1]}", file=out)
    for r in rows:
        print("\t".join("" if v is None else str(v) for v in r), file=out)
    return 0
