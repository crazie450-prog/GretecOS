"""``gos watch run`` (WO-6.1): the game watchers in the foreground, as the supervisor runs them.
``gos watch census`` (WO-6.2): a whole tile-entity census of a world, printed (the Lab check)."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from gregtec import config
from gregtec.ingest import fingerprint
from gregtec.watchers import census, runner

COMMANDS = ("watch",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    watch = commands.add_parser(
        "watch",
        help="the game watchers (logs, saves, pause): run them in the foreground",
    )
    sub = watch.add_subparsers(dest="watch_command", required=True)
    run = sub.add_parser(
        "run",
        help="watch Play's (or Lab's) logs and save files and send events to the core",
        description=(
            "Reads the instance's logs and world through safe_open (never holding a handle) "
            "and sends typed events to the core; the supervisor runs it while the instance "
            "plays (D259). Ctrl+C stops it."
        ),
    )
    runner.add_arguments(run)
    cen = sub.add_parser(
        "census",
        help="count the GT machines a world's saved chunks hold (tile entities only)",
        description=(
            "Reads every region file of the world through safe_open (read only) and prints "
            "each GT machine kind (mte:<id>) with how many there are, as of the last save "
            "(WO-6.2, D260). Save the game first (Esc, or /save-all)."
        ),
    )
    cen.add_argument("--instance", choices=("play", "lab"), default="lab")
    cen.add_argument(
        "--world",
        default=None,
        help="the world's folder in saves/ (default: [paths] world or lab_world)",
    )
    cen.add_argument("--config", type=Path, default=None)
    cen.add_argument(
        "--json", action="store_true", help="every machine with its position, as JSON"
    )


def run(args: argparse.Namespace) -> int:
    if args.watch_command == "census":
        return run_census(args)
    return runner.run(args)


def run_census(args: argparse.Namespace) -> int:
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"can't read gos.toml: {exc}", file=sys.stderr)
        return 2
    if args.instance == "play" and config.running_as_dev():
        print("gos-dev doesn't read Play: use --instance lab", file=sys.stderr)
        return 2
    try:
        game = fingerprint.game_folder(cfg.instance(args.instance))
    except fingerprint.FingerprintError as exc:
        print(f"no game folder: {exc}", file=sys.stderr)
        return 2
    name = args.world or (cfg.world if args.instance == "play" else cfg.lab_world)
    if args.world is not None and not runner.world_name_ok(args.world):
        print(
            f"--world must be a folder name in saves/, not {args.world!r}",
            file=sys.stderr,
        )
        return 2
    if not name or not (game / "saves" / name).is_dir():
        print(f"no world folder {name!r} in {game / 'saves'}", file=sys.stderr)
        return 2
    try:
        found = census.census(game / "saves" / name)
    except census.ChunkError as exc:
        print(f"census incomplete: {exc}", file=sys.stderr)
        return 1
    if args.json:
        rows = [
            {"dim": d, "x": x, "y": y, "z": z, "mte_id": m, "ref": f"mte:{m}"}
            for (d, x, y, z), m in sorted(found.items())
        ]
        print(json.dumps(rows, indent=1))
        return 0
    names = _names(cfg.data_dir / "gos.sqlite", set(found.values()))
    counts = Counter(found.values())
    print("mte\tcount\tname")
    for mte, n in sorted(counts.items()):
        print(f"mte:{mte}\t{n}\t{names.get(mte, '')}")
    print(f"{sum(counts.values())} machines, {len(counts)} kinds")
    return 0


def _names(db: Path, ids: set[int]) -> dict[int, str]:
    """English names from the recipe DB, read only, when it is there."""
    if not ids or not db.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        marks = ",".join("?" * len(ids))
        return {
            int(m): str(n)
            for m, n in conn.execute(
                f"SELECT mte_id, name_en FROM machine WHERE mte_id IN ({marks})",
                sorted(ids),
            )
            if n
        }
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
