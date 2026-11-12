"""``gos plan``: the designer's routes from the command line (WO-3.4, WO-3.5).

- ``gos plan TARGET [--tier T] [--qty 2/s | --batch N [--within 2h]] [--routes N] [--addons]
  [--exact] [--pin ITEM=RECIPE ...] [--json] [--out DIR]``: resolves TARGET as ``gos graph``
  does (an ambiguous name prints the one clarifying question and exits 3), solves up to N routes
  on the newest export run in ``GOS_HOME/data/gos.sqlite`` (read-only) with My base's defaults,
  and prints a summary per route (with its effort breakdown table, WO-4.3), or the plans as canonical JSON lines (``--json``), or writes
  each plan to ``DIR/<id>.json`` (``--out``), or saves every route into GOS_HOME (``--save``,
  WO-3.6: ``data/plans/<id>.json``, its exports, the plan row, the vault note). Routes come
  ranked (WO-3.5) with the weights from the vault's ``me/prefs.md`` (read only; balanced
  without one, D67), and the summary prints the weights first; ``--explain`` adds the templated
  explanation built from the metrics (the model-written one is the deep lane's, WO-3.7).
- ``--request FILE``: a request as JSON (``Request.from_json``) instead of TARGET and the flags.
- ``gos plan TARGET --alternatives ITEM [--all]``: the candidate recipes for one node of the
  graph (D61), with ``--all`` the unchecked and rejected ones too.
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.agents import prefetch
from gregtec.agents.tools.glossary import Glossary
from gregtec.base.state import MyBase
from gregtec.db import access
from gregtec.planner import alternatives, effort, explain, graph, plan, rank, solve
from gregtec.planner.outputs import book, store
from gregtec.planner.request import (
    Pin,
    Request,
    RequestError,
    parse_duration,
    parse_qty,
    tier_index,
)
from gregtec.vault.io import VaultIO

COMMANDS = ("plan",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "plan",
        help="solve production routes for a target (WO-3.4)",
        description=(
            "Builds the recipe graph for TARGET, sizes each machine and solves up to N routes "
            "(staged MILP) on the newest export run in GOS_HOME/data/gos.sqlite (read-only)."
        ),
    )
    p.add_argument("target", nargs="?", help="an item or fluid: words or its key")
    p.add_argument("--request", type=Path, default=None, help="a request as JSON")
    p.add_argument("--tier", default=None, help="tier cap (LV, IV, ... or 0-14)")
    p.add_argument(
        "--qty", default="1/s", help="a rate: 2/s, 30/min, 100/h (default 1/s)"
    )
    p.add_argument("--batch", default=None, help="a batch quantity instead of a rate")
    p.add_argument(
        "--within", default=None, help="the batch's deadline: 90s, 30min, 2h"
    )
    p.add_argument("--routes", type=int, default=3, help="routes to solve, 1-3")
    p.add_argument("--addons", action="store_true", help="allow addon machines (D12)")
    p.add_argument(
        "--exact", action="store_true", help="whole machine counts (integer MILP)"
    )
    p.add_argument(
        "--pin",
        action="append",
        default=[],
        metavar="ITEM=RECIPE",
        help="pin a node (D61)",
    )
    p.add_argument(
        "--alternatives", default=None, metavar="ITEM", help="list a node's recipes"
    )
    p.add_argument("--all", action="store_true", help="with --alternatives: show all")
    p.add_argument("--json", action="store_true", help="print the plans as JSON lines")
    p.add_argument(
        "--explain",
        action="store_true",
        help="add the explanation built from the metrics",
    )
    p.add_argument(
        "--out", type=Path, default=None, help="write DIR/<id>.json per plan"
    )
    p.add_argument(
        "--save",
        action="store_true",
        help="save every route: data/plans/<id>.json, its exports, the plan row and the "
        "vault note plans/<id>.md (WO-3.6)",
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
        vault = cfg.gos_home / "vault"
        prefs = rank.read_prefs(vault)
        return _run(conn, db, Glossary.find(vault), args, out, prefs, cfg.gos_home)
    finally:
        conn.close()


def _request(
    conn: sqlite3.Connection,
    db: Path,
    glossary: Glossary,
    args: argparse.Namespace,
    out: TextIO,
) -> Request | int:
    if args.request is not None:
        return Request.from_json(args.request.read_text(encoding="utf-8"))
    if not args.target:
        print("name a target, or pass --request FILE", file=out)
        return 2
    found = graph.resolve_target(conn, args.target, glossary, db)
    if isinstance(found, prefetch.Clarify):
        print(found.question, file=out)
        for n, c in enumerate(found.candidates, 1):
            print(f"  {n}. {c.name} [{c.key}]", file=out)
        return 3
    tier = (
        tier_index(int(args.tier) if args.tier.isdigit() else args.tier)
        if args.tier is not None
        else None
    )
    pins = []
    for text in args.pin:
        node, sep, recipe = text.partition("=")
        if not sep:
            raise RequestError(f"--pin {text!r}: write ITEM=RECIPE")
        pins.append(Pin(node, recipe))
    common = {
        "tier_cap": tier,
        "machines": "base+addons" if args.addons else "base",
        "pins": tuple(pins),
        "routes": args.routes,
    }
    if args.batch is not None:
        qty, unit = parse_qty(args.batch)
        if unit is not None:
            raise RequestError("--batch is a quantity, without /s, /min or /h")
        within = parse_duration(args.within) if args.within else None
        return Request(found.key, mode="batch", qty=qty, within_s=within, **common)  # type: ignore[arg-type]
    if args.within is not None:
        raise RequestError("--within is for --batch only")
    qty, unit = parse_qty(args.qty)
    return Request(found.key, qty=qty, unit=unit, **common)  # type: ignore[arg-type]


def _save(
    conn: sqlite3.Connection, db: Path, home: Path, design: plan.Design, out: TextIO
) -> None:
    """Persist every route of ``design`` (WO-3.6, :mod:`gregtec.planner.outputs.store`)."""
    rw = access.connect(db)
    try:
        vault = VaultIO(home / "vault")
        for p in design.plans:
            saved = store.save(home, p, rw, book.read(conn, p), vault)
            print(f"saved {saved.id}: {saved.json_path}", file=out)
            for problem in saved.problems:
                print(f"  {problem}", file=out)
    finally:
        rw.close()


def _run(
    conn: sqlite3.Connection,
    db: Path,
    glossary: Glossary,
    args: argparse.Namespace,
    out: TextIO,
    prefs: rank.Prefs = rank.BALANCED,
    home: Path | None = None,
) -> int:
    try:
        request = _request(conn, db, glossary, args, out)
        if isinstance(request, int):
            return request
        base = MyBase.from_db(conn)
        if args.alternatives is not None:
            g = graph.build(conn, request, base)
            for a in alternatives.node_alternatives(g, args.alternatives, args.all):
                reason = f"  ({a['reason']})" if a["reason"] else ""
                print(f"{a['status']:<11} {a['recipe']}{reason}", file=out)
            return 0
        design = plan.design(conn, request, base, exact=args.exact, prefs=prefs)
    except (
        OSError,
        KeyError,
        LookupError,
        ValueError,
        graph.GraphError,
        solve.SolveError,
    ) as exc:
        print(f"refused: {exc}", file=out)
        return 2
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        for p in design.plans:
            (args.out / f"{p['id']}.json").write_text(plan.dumps(p), encoding="utf-8")
    if getattr(args, "save", False):
        if home is None:
            print("refused: --save needs GOS_HOME", file=out)
            return 2
        _save(conn, db, home, design, out)
    if args.json:
        for p in design.plans:
            print(plan.dumps(p), file=out)
        return 0
    print(rank.describe(prefs), file=out)
    for note in prefs.notes:
        print(f"  prefs: {note}", file=out)
    for p in design.plans:
        r = p["route"]
        m, k = p["metrics"], p["rank"]
        machines = sum(n["count"] or 0 for n in p["nodes"])
        print(f"{p['id']}  {p['name']}  [{r['column']}]", file=out)
        print(
            f"  score {k['score']:g}: {m['machines']} machines, {len(m['new_types'])} new "
            f"types, {m['eut_avg']:g} EU/t avg, effort {m['eff_h']:g} h, scarcity "
            f"{m['scarcity']:g}, complexity {m['complexity']['score']}",
            file=out,
        )
        for line in effort.text(m):
            print(line, file=out)
        print(
            f"  {len(p['nodes'])} nodes, {machines} machines, gates: "
            f"{', '.join(r['gates']) or 'none'}, every machine runs: {r['all_run']}",
            file=out,
        )
        for c in r["choices"]:
            print(f"  choice {c['node']}: {c['recipe']}", file=out)
        for w in p["warnings"]:
            print(f"  warning: {w}", file=out)
    if args.explain and design.plans:
        g = design.graph
        doc = explain.facts(design.plans, g.item(g.target).name, prefs)
        print(explain.template(doc), file=out)
    return 0
