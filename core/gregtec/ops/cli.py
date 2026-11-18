"""``gos backup list|restore`` and ``gos relocate check`` (WO-11.2, D332, D335).

- ``backup list``: the DB and vault backups in ``GOS_HOME/data/backups`` and whether each has
  its manifest.
- ``backup restore --verify``: restore the newest DB and vault backups (``--date YYYYMMDD``: that
  day's) into a temporary folder, check them (:func:`gregtec.ops.backups.restore`) and remove the
  folder; ``--to DIR`` restores into ``DIR`` (new or empty, never in or around the live data or
  vault) and keeps it, for Clayton's manual swap (docs/runbooks/ops.md). Exit 1 when a check
  fails, with what failed.
- ``relocate check``: the relocation config check (:mod:`gregtec.ops.relocation`, D45).

Reading is all these do in GOS_HOME: gos-dev may run them (``data`` is read only to it, D82).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.ops import backups, relocation

COMMANDS = ("backup", "relocate")


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "backup", help="the DB and vault backups: list them, restore and check one"
    )
    sub = p.add_subparsers(dest="backup_command", required=True)
    q = sub.add_parser("list", help="the backups and their manifests")
    q.add_argument("--config", type=Path, default=None)
    q = sub.add_parser(
        "restore",
        help="restore the newest DB and vault backups into a folder and check them",
    )
    q.add_argument("--config", type=Path, default=None)
    q.add_argument(
        "--verify",
        action="store_true",
        help="restore into a temporary folder, check, then remove it",
    )
    q.add_argument(
        "--to",
        type=Path,
        default=None,
        help="restore into this new or empty folder (outside GOS_HOME's data and vault) and keep it",
    )
    q.add_argument("--date", default=None, help="YYYYMMDD: that day's backups")
    q.add_argument(
        "--from",
        dest="source",
        type=Path,
        default=None,
        help="the backups folder (default: GOS_HOME/data/backups)",
    )
    r = commands.add_parser(
        "relocate",
        help="moving the core to another PC later (D45): check the config, no server code",
    )
    rsub = r.add_subparsers(dest="relocate_command", required=True)
    c = rsub.add_parser(
        "check", help="what the move needs, from gos.toml and OC's config"
    )
    c.add_argument("--config", type=Path, default=None)
    c.add_argument(
        "--core-ip",
        default=None,
        help="the spare PC's LAN address the clients would use (checks OC's first rule for it)",
    )
    c.add_argument(
        "--oc-config",
        type=Path,
        default=None,
        help="an OpenComputers.cfg to check (default: Lab's)",
    )


def run(args: argparse.Namespace, out: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    if args.command == "relocate":
        return relocation.run(cfg, args.core_ip, args.oc_config, out)
    folder = getattr(args, "source", None) or cfg.data_dir / "backups"
    if args.backup_command == "list":
        items = backups.listing(folder)
        if not items:
            print(f"No backups in {folder}.", file=out)
            return 0
        for item in items:
            mark = "manifest" if item.has_manifest else "NO MANIFEST"
            print(f"{item.kind:<5} {item.day}  {item.path.name}  {mark}", file=out)
        return 0
    if args.date is not None and not (len(args.date) == 8 and args.date.isdigit()):
        print("--date is YYYYMMDD", file=out)
        return 2
    if not args.verify and args.to is None:
        print(
            "Say --verify (restore into a temporary folder and check) or --to DIR.",
            file=out,
        )
        return 2
    live = (cfg.data_dir, cfg.gos_home / "vault")
    if args.to is not None:
        why = backups.refuse_dest(args.to, live)
        if why is not None:
            print(f"Refused: {why}.", file=out)
            return 2
        args.to.mkdir(parents=True, exist_ok=True)
        dest, temporary = args.to, False
    else:
        dest, temporary = Path(tempfile.mkdtemp(prefix="gos-restore-")), True
    try:
        checks = backups.restore(folder, dest, args.date)
    finally:
        if temporary:
            shutil.rmtree(dest, ignore_errors=True)
    for check in checks:
        print(
            f"{'ok  ' if check.ok else 'FAIL'} {check.what}: {check.detail}", file=out
        )
    failed = [c for c in checks if not c.ok]
    if failed:
        print(
            f"RESTORE CHECK FAILED: {len(failed)} of {len(checks)} checks. Don't rely on "
            "this backup; try an older day (gos backup list, --date).",
            file=out,
        )
        return 1
    where = "a temporary folder, now removed" if temporary else str(dest)
    print(f"Restore verified into {where}.", file=out)
    return 0
