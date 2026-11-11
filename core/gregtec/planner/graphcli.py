"""``gos graph``: the designer's recipe graph from the command line (WO-3.3).

- ``gos graph TARGET [--tier T] [--addons] [--json]``: resolves TARGET (words or a key) as the
  designer does (an ambiguous name prints the one clarifying question and exits 3), builds the
  graph from ``GOS_HOME/data/gos.sqlite``'s newest export run with My base's defaults (D63,
  D64) and prints a summary, or the whole graph as canonical JSON.
- ``gos graph --check-chains``: checks every chain template against the newest export run and
  lists the stale rows (exit 1 when any is stale); the pack-update playbook runs it.
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
import time
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.agents import prefetch
from gregtec.agents.tools.glossary import Glossary
from gregtec.db import access
from gregtec.planner import chains, graph, machines
from gregtec.planner.request import (
    DefaultBaseInputs,
    Request,
    RequestError,
    tier_index,
)

COMMANDS = ("graph",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "graph",
        help="the designer's recipe graph for a target, or check the chain templates (WO-3.3)",
        description=(
            "Builds the recipe graph backward from TARGET out of the newest export run in "
            "GOS_HOME/data/gos.sqlite (read-only) and prints a summary. --check-chains "
            "validates the chain templates instead."
        ),
    )
    p.add_argument("target", nargs="?", help="an item or fluid: words or its key")
    p.add_argument(
        "--tier",
        default=None,
        help="tier cap (LV, IV, ... or 0-14); default: My base's",
    )
    p.add_argument("--addons", action="store_true", help="allow addon machines (D12)")
    p.add_argument("--json", action="store_true", help="print the graph as JSON")
    p.add_argument(
        "--check-chains", action="store_true", help="validate the chain templates"
    )
    p.add_argument("--config", type=Path, default=None, help="gos.toml to read")


def run(args: argparse.Namespace, out: TextIO = sys.stdout) -> int:
    if isinstance(out, io.TextIOWrapper):
        out.reconfigure(errors="backslashreplace")
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
        if args.check_chains:
            return check_chains(conn, out)
        if not args.target:
            print("name a target, or pass --check-chains", file=out)
            return 2
        glossary = Glossary.find(cfg.gos_home / "vault")
        return show(conn, db, glossary, args, out)
    finally:
        conn.close()


def check_chains(conn: sqlite3.Connection, out: TextIO) -> int:
    try:
        templates = chains.load()
    except ValueError as exc:
        print(f"chain templates: {exc}", file=out)
        return 2
    run_id = machines.current_run(conn)
    stale = chains.validate(templates, conn, run_id)
    print(
        f"{len(templates.rows)} rows in {len(templates.chains)} templates "
        f"({', '.join(templates.chains)}) against export run {run_id}: "
        f"{len(stale)} stale",
        file=out,
    )
    for s in stale:
        print(f"stale: {s.text()}", file=out)
    return 1 if stale else 0


def show(
    conn: sqlite3.Connection,
    db: Path,
    glossary: Glossary,
    args: argparse.Namespace,
    out: TextIO,
) -> int:
    try:
        found = graph.resolve_target(conn, args.target, glossary, db)
    except graph.GraphError as exc:
        print(str(exc), file=out)
        return 2
    if isinstance(found, prefetch.Clarify):
        print(found.question, file=out)
        for n, c in enumerate(found.candidates, 1):
            print(f"  {n}. {c.name} [{c.key}]", file=out)
        return 3
    try:
        tier = (
            tier_index(int(args.tier) if args.tier.isdigit() else args.tier)
            if args.tier is not None
            else None
        )
        request = Request(
            found.key,
            tier_cap=tier,
            machines="base+addons" if args.addons else "base",
        )
        started = time.perf_counter()
        g = graph.build(conn, request, DefaultBaseInputs.from_db(conn))
        took = time.perf_counter() - started
    except (RequestError, graph.GraphError, LookupError, ValueError) as exc:
        print(f"refused: {exc}", file=out)
        return 2
    if args.json:
        print(g.canonical(), file=out)
        return 0
    target = g.item(g.target)
    print(f"target: {target.name} [{g.target}]  tier cap: {g.tier_cap}", file=out)
    print(
        f"graph: {len(g.items)} items, {len(g.recipes)} recipes, {len(g.edges)} edges, "
        f"{len(g.cycles)} loops, built in {took:.2f} s (export run {g.run_id})",
        file=out,
    )
    print(f"templates: {', '.join(g.templates) or 'none'}", file=out)
    print(f"primary choices: {', '.join(g.primary)}", file=out)
    for ref in target.chosen:
        print(f"  {ref}", file=out)
    for w in g.warnings:
        print(f"warning: {w}", file=out)
    return 0
