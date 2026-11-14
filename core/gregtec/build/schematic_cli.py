"""``gos schematic``: write a test shell, read a capture, compare two schematics (WO-10.4).

- ``gos schematic sample PALETTE``: a test shell with every entry of PALETTE (one
  ``registry@meta`` per line) once, through :func:`gregtec.build.schematic.write_shell` (the
  palette rule against the newest block catalog and a Lab world's registry), written to
  ``GOS_HOME/data/schematics/shells/`` (or ``--out``). GOS never writes into the Lab: Clayton
  copies the file into the Lab's ``config/worldedit/schematics/`` himself (runbook
  ``schematic_roundtrip.md``). Exit 0 written, 1 refused, 2 bad input or setup.
- ``gos schematic read FILE``: a capture's size, writer, offset and block states. Exit 0, or 2.
- ``gos schematic compare EXPECTED ACTUAL``: whether every cell holds the same block state
  (registry name + meta), and whether the offsets match. Exit 0 identical, 1 different, 2
  unreadable.

``--source-world`` names the ids of a file without a mapping from that world's ``level.dat``.
Files are read through ``safe_open`` (a capture may sit in the Lab's folder while WorldEdit
writes it) and never held.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.build import palette, schematic, worldreg
from gregtec.db import access
from gregtec.db import ores as ore_db
from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest.fingerprint import atomic_write

COMMANDS = ("schematic",)
FILE_MAX = 256 << 20
SAMPLE_NAME = "gos_roundtrip"


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "schematic",
        help="write a test shell, read a capture, compare schematics (WO-10.4)",
        description="MCEdit .schematic files as worldedit-gtnh and Schematica-GTNH use them.",
    )
    sub = p.add_subparsers(dest="schematic_command", required=True)
    sample = sub.add_parser(
        "sample",
        help="a test shell holding every palette entry once, after the palette rule",
        description=(
            "Applies the palette rule (as `gos palette check`) and writes the shell to "
            "GOS_HOME/data/schematics/shells/. Copy it into the Lab's "
            "config/worldedit/schematics/ yourself; GOS never writes into the Lab."
        ),
    )
    sample.add_argument(
        "palette", type=Path, help="one <registry name>@<meta> per line"
    )
    sample.add_argument(
        "--out", type=Path, default=None, help="the .schematic to write"
    )
    sample.add_argument(
        "--over",
        choices=("split", "addblocks"),
        default="split",
        help="above 255 block types: split into parts (default) or write AddBlocks",
    )
    where = sample.add_mutually_exclusive_group()
    where.add_argument(
        "--world",
        default=None,
        help="the world's folder in the Lab's saves/ (default [paths] lab_world)",
    )
    where.add_argument(
        "--world-dir",
        type=Path,
        default=None,
        help="a Lab or test world folder (read-only); GOS never reads Play on its own",
    )
    sample.add_argument("--config", type=Path, default=None, help="gos.toml to read")
    read = sub.add_parser("read", help="what a .schematic holds")
    read.add_argument("file", type=Path)
    compare = sub.add_parser(
        "compare",
        help="whether two schematics hold the same blocks (paste -> //copy check)",
    )
    compare.add_argument("expected", type=Path, help="the file that was pasted")
    compare.add_argument("actual", type=Path, help="the //copy of the paste")
    for each in (read, compare):
        each.add_argument(
            "--source-world",
            type=Path,
            default=None,
            help="the world a file without a mapping was copied in (its level.dat, read-only)",
        )


def run(args: argparse.Namespace, out: TextIO = sys.stdout) -> int:
    if args.schematic_command == "sample":
        return _sample(args, out)
    ids: dict[int, str] | None = None
    if args.source_world is not None:
        try:
            ids = worldreg.read_block_ids(args.source_world)
        except worldreg.WorldRegistryError as exc:
            print(f"Can't read the source world's ids: {exc}", file=out)
            return 2
    files = (
        [args.file]
        if args.schematic_command == "read"
        else [args.expected, args.actual]
    )
    captures: list[schematic.Capture] = []
    for f in files:
        try:
            captures.append(schematic.read(read_bytes(f, limit=FILE_MAX), ids))
        except (OSError, schematic.CaptureError) as exc:
            print(f"Can't read {f.name}: {exc}", file=out)
            return 2
    if args.schematic_command == "read":
        for line in schematic.summary(captures[0]):
            print(line, file=out)
        return 0
    want, got = (c.schematic for c in captures)
    diffs = schematic.diff(want, got)
    for line in diffs:
        print(line, file=out)
    same_offset = want.offset == got.offset
    print(
        f"offset: {want.offset} pasted, {got.offset} copied"
        + (
            ""
            if same_offset
            else " (differ: did you //copy from the spot you pasted from?)"
        ),
        file=out,
    )
    if diffs:
        print("DIFFERENT: the copy doesn't hold the pasted blocks", file=out)
        return 1
    print(
        f"identical: {want.size}, {len(want.used())} block states in every cell",
        file=out,
    )
    return 0


def _sample(args: argparse.Namespace, out: TextIO) -> int:
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    db = access.db_path(cfg.gos_home)
    if not db.is_file():
        print(f"{db} doesn't exist: run `gos db build` first", file=out)
        return 2
    try:
        entries = palette.parse(args.palette.read_text(encoding="utf-8-sig"))
        shell = schematic.sample(entries)
    except (
        OSError,
        UnicodeDecodeError,
        palette.PaletteError,
        schematic.SchematicError,
    ) as exc:
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
        written = schematic.write_shell(conn, registry, shell, args.over)
    except schematic.ShellRefused as exc:
        for line in exc.lines:
            print(line, file=out)
        return 1
    except (palette.CatalogError, schematic.SchematicError) as exc:
        print(str(exc), file=out)
        return 2
    finally:
        conn.close()
    target = (
        args.out or cfg.data_dir / "schematics" / "shells" / f"{SAMPLE_NAME}.schematic"
    )
    for part in written.parts:
        path = (
            target
            if not part.name
            else target.with_name(f"{target.stem}-{part.name}.schematic")
        )
        try:
            atomic_write(path, part.data)
        except OSError as exc:
            print(f"Can't write {path}: {exc}", file=out)
            return 2
        s = part.schematic
        print(f"wrote {path} ({s.size}, WEOffset {s.offset})", file=out)
    for note in written.notes:
        print(f"note: {note}", file=out)
    return 0
