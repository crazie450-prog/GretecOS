"""``gos pack``: the pack-update playbook's commands (WO-1.9).

- ``gos pack watch``: the release watcher (daily; cached, backed off).
- ``gos pack stock [VERSION]``: Play's mods against a GTNH release (D87).
- ``gos pack update``: start (``--start VERSION [--simulate]``), walk and record the checklist.
- ``gos pack adopt yes|no``: Clayton's answer to "adopt?".
- ``gos pack status``: the open update's checklist and pending question.
- ``gos pack diff [OLD NEW]``: the recipe, crop and oredict diff of two runs in the DB.
- ``gos pack pin [RUN]``: the GT pin check of a run (exit 1 on a mismatch).
- ``gos pack fixture-plans DIR SPEC...``: fixture plans for a dry run (WO-1.9 acceptance, L10).
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.db import access
from gregtec.ingest import fingerprint
from gregtec.packupdate import diff, gtpin, plans, playbook, releases

COMMANDS = ("pack",)
CONFIG_HELP = (
    "gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
    "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults."
)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    pack = commands.add_parser(
        "pack",
        help="pack updates: release watcher, diff, the update checklist (WO-1.9)",
    )
    cmds = pack.add_subparsers(dest="pack_command", required=True)
    watch = cmds.add_parser(
        "watch",
        help="check for a newer stable GTNH release and addon releases",
        description=(
            "Reads DAXXL's release manifests and the GitHub releases feeds (plain HTTPS, no "
            "API key), at most once a day per URL unless --force, backing off after a "
            "failure; lists Play's mods (names only) to pick the addons that matter. Records "
            "what it saw in GOS_HOME/data/packupdate/watch.json."
        ),
    )
    watch.add_argument(
        "--force", action="store_true", help="fetch now, ignore the cache"
    )
    stock = cmds.add_parser(
        "stock",
        help="compare Play's mod files with a GTNH release's (D87)",
        description="Lists Play's mods/ (names only) against DAXXL's manifest and assets.",
    )
    stock.add_argument("version", nargs="?", default=None, help="default: the adopted")
    stock.add_argument(
        "--force", action="store_true", help="fetch now, ignore the cache"
    )
    update = cmds.add_parser(
        "update",
        help="walk the pack-update checklist (start one with --start VERSION)",
        description=(
            "Runs the open update's steps from the first not done and stops where Clayton "
            "must act, saying what to do: release summary, world backup, Lab update, Lab "
            "export, diff, adopt?, Play update (Clayton's update_play.py), Play export, "
            "affected-plan flags. Never adopts by itself."
        ),
    )
    update.add_argument("--start", metavar="VERSION", help="open an update to VERSION")
    update.add_argument(
        "--simulate",
        action="store_true",
        help="with --start: a dry run (Play is never changed; nothing is adopted)",
    )
    update.add_argument(
        "--fixture-plans",
        type=Path,
        metavar="DIR",
        help="with --start: gos-plan/0 plans to check at the diff (dry runs)",
    )
    update.add_argument("--id", default=None, help="update id (default: the open one)")
    update.add_argument(
        "--confirm",
        choices=("backup",),
        help="mark a step done by hand (a backup made another way)",
    )
    update.add_argument("--note", default="", help="with --confirm or --abandon")
    update.add_argument(
        "--redo", choices=playbook.STEPS, help="run this step and every later one again"
    )
    update.add_argument("--abandon", action="store_true", help="close the open update")
    adopt = cmds.add_parser(
        "adopt",
        help="answer the open update's question: adopt the new pack or not",
        description="Clayton's answer (D44). Refused as gos-dev and while the GT pin differs.",
    )
    adopt.add_argument("answer", choices=("yes", "no"))
    adopt.add_argument("--id", default=None, help="update id (default: the open one)")
    adopt.add_argument("--note", default="", help="why, for the record")
    status = cmds.add_parser("status", help="the open (or named) update's checklist")
    status.add_argument("--id", default=None, help="update id")
    pdiff = cmds.add_parser(
        "diff",
        help="recipe, crop and oredict diff of two runs in the DB, by mod, with plan flags",
    )
    pdiff.add_argument(
        "runs", nargs="*", metavar="RUN", help="OLD NEW (default: last two)"
    )
    pin = cmds.add_parser(
        "pin", help="check a run's GT version against the GT pin (exit 1 if it differs)"
    )
    pin.add_argument("run", nargs="?", default=None, help="run key (default: newest)")
    fx = cmds.add_parser(
        "fixture-plans",
        help="write gos-plan/0 fixture plans from the DB's newest run (for a dry run)",
        description=(
            "Each SPEC is <plan id>=<registry>@<meta> (cites every recipe making that item) or "
            "<plan id>=crop:<crop id>. Reads the DB only; writes DIR/<plan id>.json."
        ),
    )
    fx.add_argument("dir", type=Path)
    fx.add_argument("spec", nargs="+")
    for each in (watch, stock, update, adopt, status, pdiff, pin, fx):
        each.add_argument("--config", type=Path, default=None, help=CONFIG_HELP)


def run(args: argparse.Namespace, out: TextIO = sys.stdout) -> int:
    if isinstance(out, io.TextIOWrapper):
        out.reconfigure(errors="backslashreplace")  # pack names and release titles
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        return _run(cfg, args, out)
    except (playbook.UpdateError, diff.DiffError, releases.WatchError) as exc:
        print(f"refused: {exc}", file=out)
        return 1


def _run(cfg: config.Config, args: argparse.Namespace, out: TextIO) -> int:
    home = cfg.gos_home
    cmd = args.pack_command
    if cmd == "watch":
        w = releases.watch(
            home,
            str(playbook.adopted(home)["version"]),
            cfg.instance("play"),
            force=args.force,
        )
        text = "\n".join(w.lines()) + "\n"
        path = home / releases.REPORTS / f"pack-watch-{w.checked_utc[:10]}.txt"
        fingerprint.atomic_write(path, text.encode("utf-8"))
        print(text + f"Saved: {path}", file=out)
        return 0
    if cmd == "stock":
        version = args.version or str(playbook.adopted(home)["version"])
        result, notes = releases.stock(
            home, version, cfg.instance("play"), force=args.force
        )
        for line in (result.lines() if result else []) + [f"note: {n}" for n in notes]:
            print(line, file=out)
        return 0 if result is not None else 1
    if cmd == "update":
        return _update(cfg, args, out)
    if cmd == "adopt":
        u = playbook.find(home, args.id)
        for line in playbook.answer(home, u, args.answer == "yes", args.note):
            print(line, file=out)
        return 0
    if cmd == "status":
        u = playbook.find(home, args.id)
        print("\n".join(playbook.status_lines(u)), file=out)
        return 0
    if cmd == "diff":
        return _diff(cfg, args.runs, out)
    if cmd == "fixture-plans":
        s = diff.side(access.db_path(home), None, home)
        conn = diff.connect(s.db)
        try:
            made = [
                plans.fixture(conn, s.run_id, s.run_key, spec) for spec in args.spec
            ]
        except ValueError as exc:
            print(f"refused: {exc}", file=out)
            return 1
        finally:
            conn.close()
        for p in made:
            path = args.dir / f"{p.id}.json"
            fingerprint.atomic_write(path, plans.dumps(p))
            print(f"{path}: {len(p.cites)} cites ({p.name})", file=out)
        return 0
    if cmd == "pin":
        s = diff.side(access.db_path(home), args.run, home)
        problem = gtpin.problem(s.gt_version)
        print(problem or f"run {s.run_key}: GT {s.gt_version} = the pin", file=out)
        return 1 if problem else 0
    raise AssertionError(cmd)


def _update(cfg: config.Config, args: argparse.Namespace, out: TextIO) -> int:
    home = cfg.gos_home
    if args.start:
        u = playbook.start(home, args.start, simulate=args.simulate)
        print(f"opened update {u.id} ({u.folder})", file=out)
        if args.fixture_plans:
            for line in playbook.copy_fixture_plans(u, args.fixture_plans):
                print(f"  {line}", file=out)
    else:
        u = playbook.find(home, args.id)
        if args.abandon:
            playbook.abandon(u, args.note or "by Clayton")
            print(f"update {u.id} closed", file=out)
            return 0
        if args.confirm:
            playbook.confirm(u, args.confirm, args.note)
        if args.redo:
            playbook.redo(u, args.redo)
    if u.finished():
        print("\n".join(playbook.status_lines(u)), file=out)
        return 0
    return playbook.walk(playbook.Ctx(cfg, out), u)


def _diff(cfg: config.Config, refs: list[str], out: TextIO) -> int:
    db = access.db_path(cfg.gos_home)
    if len(refs) not in (0, 2):
        print("Give two runs (OLD NEW), or none for the DB's last two.", file=out)
        return 2
    if not refs:
        conn = diff.connect(db)
        try:
            keys = [
                str(r[0])
                for r in conn.execute(
                    "SELECT run_key FROM export_run ORDER BY created_at DESC, run_key DESC"
                    " LIMIT 2"
                )
            ]
        finally:
            conn.close()
        if len(keys) < 2:
            print("The DB holds fewer than two runs: name two.", file=out)
            return 2
        refs = [keys[1], keys[0]]
    old = diff.side(db, refs[0], cfg.gos_home)
    new = diff.side(db, refs[1], cfg.gos_home)
    s = diff.summarise(
        old,
        new,
        play_old=fingerprint.run_entries(cfg.gos_home, old.run_key),
        play_new=fingerprint.run_entries(cfg.gos_home, new.run_key),
    )
    conn = diff.connect(db)
    try:
        found, problems = plans.from_db(conn, cfg.gos_home)
        flagged = plans.check(
            found,
            lambda key: plans.view(conn, key),
            plans.RunView(conn, new.run_id, new.run_key),
        )
    finally:
        conn.close()
    print(diff.report(s, plans.lines(flagged, len(found), problems)), end="", file=out)
    return 0
