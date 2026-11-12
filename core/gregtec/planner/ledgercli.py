"""``gos ledger``: the EU ledger from the command line (WO-4.4, D238, D239).

- ``gos ledger``: the ledger as ``base/power.md`` holds it (facts, then the lines, sources,
  per-tier and warnings tables), every row printed (the note cuts long tables at its 150-line
  cap); exit 1 when there is a warning.
- ``--json``: the same as JSON.
- ``--write``: rewrite ``base/power.md`` from the ledger's record (through the vault's writer),
  for a vault deployed after plans were accepted.

Reads ``GOS_HOME/data/gos.sqlite``; every number is an accepted plan's (:mod:`.ledger`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.db import access
from gregtec.planner import ledger

COMMANDS = ("ledger",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "ledger",
        help="the EU ledger: accepted plans' EU/t per tier, warnings (WO-4.4)",
        description="Reads the ledger's record in GOS_HOME/data/gos.sqlite.",
    )
    p.add_argument("--json", action="store_true", help="print it as JSON")
    p.add_argument(
        "--write", action="store_true", help="rewrite the vault's base/power.md"
    )
    p.add_argument("--config", type=Path, default=None, help="gos.toml to read")


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
        led = ledger.read(conn)
    finally:
        conn.close()
    if args.write:
        path, problems = ledger.write(cfg.gos_home, led)
        for problem in problems:
            print(problem, file=out)
        if path is None:
            return 2
        print(f"wrote {path}", file=out)
    if args.json:
        print(json.dumps(led.as_dict(), sort_keys=True), file=out)
    elif not args.write:
        print(ledger.render(led, cap=None), end="", file=out)
    return 1 if led.warnings else 0
