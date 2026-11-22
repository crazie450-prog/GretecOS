"""``gos layout``: a line layout from saved plans (WO-10.5, D312).

``gos layout PLAN_ID [PLAN_ID ...] [--option NAME=VALUE ...] [--json | --card] [--out FILE]``

Lays out the plans' machines (a production plan, and the power plan that feeds it if named too)
with :mod:`gregtec.build.layout` on GOS_HOME's DB and prints a summary: the bounding volume, each
machine with its position, facing and whether it is reached, and the warnings. ``--json`` prints
the layout JSON, ``--card`` the dashboard's preview card; ``--out FILE`` writes the layout JSON
there (atomically) instead. Options: ``corridor_width``, ``corridor_height``, ``clearance``,
``clearance_top``, ``chase_height``, ``max_run`` (:data:`gregtec.build.layout.LIMITS`). Exit 1
when a machine isn't placed or isn't reached. Reads the DB and the plans; writes only ``--out``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, TextIO

from gregtec import config
from gregtec.build import layout
from gregtec.db import access
from gregtec.packupdate.plans import PLAN_ID

COMMANDS = ("layout",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "layout",
        help="lay out a saved plan's machines with corridors and chases (WO-10.5)",
        description="Reads GOS_HOME/data/gos.sqlite and data/plans/<id>.json.",
    )
    p.add_argument("plans", nargs="+", metavar="PLAN_ID", help="saved plan id(s)")
    p.add_argument(
        "--option",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=f"one of {', '.join(layout.LIMITS)}; repeatable",
    )
    shown = p.add_mutually_exclusive_group()
    shown.add_argument("--json", action="store_true", help="print the layout JSON")
    shown.add_argument("--card", action="store_true", help="print the preview card")
    p.add_argument("--out", type=Path, default=None, help="write the layout JSON here")
    p.add_argument("--config", type=Path, default=None, help="gos.toml to read")


def run(args: argparse.Namespace, out: TextIO = sys.stdout) -> int:
    from gregtec.planner.outputs import store

    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        opts = layout.Options.parse(args.option)
    except layout.LayoutError as exc:
        print(str(exc), file=out)
        return 2
    plans: list[dict[str, Any]] = []
    for pid in args.plans:
        if not PLAN_ID.fullmatch(pid):
            print(f"{pid!r} is not a plan id", file=out)
            return 2
        try:
            plans.append(store.load(cfg.gos_home, pid))
        except (OSError, ValueError) as exc:
            print(f"plan {pid} isn't saved: {exc}", file=out)
            return 2
    db = access.db_path(cfg.gos_home)
    if not db.is_file():
        print(f"{db} doesn't exist: run `gos db build` first", file=out)
        return 2
    conn = access.connect(db, readonly=True)
    try:
        lay = layout.plan_layout(conn, plans, opts)
    except layout.LayoutError as exc:
        print(str(exc), file=out)
        return 2
    except sqlite3.Error as exc:
        print(f"Can't read the DB: {exc}", file=out)
        return 2
    finally:
        conn.close()
    text = json.dumps(lay, indent=1, ensure_ascii=False) + "\n"
    if args.out is not None:
        from gregtec import doctor
        from gregtec.vault.io import atomic_write

        folders = [*doctor.guarded(cfg), ("the vault", cfg.gos_home / "vault")]
        if (why := doctor.touch_problem(args.out, folders)) is not None:
            # GOS never writes into the Lab, Export or Play (D309 (6)); the vault only through
            # gregtec.vault
            print(
                f"Won't write {args.out}: it {why}. Write it elsewhere and copy it",
                file=out,
            )
            return 2
        atomic_write(args.out, text.encode("utf-8"))
        print(f"wrote {args.out}", file=out)
    elif args.json:
        out.write(text)
    elif args.card:
        print(json.dumps(layout.card(lay), indent=1, ensure_ascii=False), file=out)
    if not args.json and not args.card:
        for line in render(lay):
            print(line, file=out)
    bad = lay["unplaced"] or any(not p["reach"]["ok"] for p in lay["placements"])
    return 1 if bad else 0


def render(lay: dict[str, Any]) -> list[str]:
    sx, sy, sz = lay["bounds"]["size"]
    names = " + ".join(f"plan:{p['id']}" for p in lay["plans"])
    head = (
        f"Layout {lay['id']} of {names}: {sx} x {sz} blocks, {sy} high; "
        f"{len(lay['placements'])} machines, {len(lay['corridors'])} corridors, "
        f"{len(lay['chases'])} chases, entrance {lay['entrances'][0]['side']}"
    )
    lines = [head]
    for p in lay["placements"]:
        r = p["reach"]
        state = "reached" if r["ok"] else "NOT reached"
        lines.append(
            f"  {p['id']}\t{p['name'] or '?'}\tmte:{p['mte']}\tat {p['controller']} facing "
            f"{p['facing']}\t{state} ({r['reached']}/{r['hatch_cells']} hatch cells)"
        )
    lines += [f"  warning: {w}" for w in lay["warnings"]]
    return lines
