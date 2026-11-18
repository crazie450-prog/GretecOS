"""``gos theme``: building themes from the command line (WO-10.6, D314, D315). Read-only.

- ``gos theme list``: every theme's current version (starters and the vault's), and the files
  that don't load.
- ``gos theme check [ID ...]``: each theme's palette (default: every theme's) against the newest
  block catalog in ``GOS_HOME/data/gos.sqlite``. Exit 0: all pass; 1: an entry refused or a
  theme doesn't load; 2: setup.
- ``gos theme select [--theme ID] [--prompt TEXT] [--line-type T] [--at DIM X Z]``: the brief
  a build would get in each of plan 7.10's modes (a theme, a prompt only, or blank).

Themes are created and refined by voice or text through the deep lane's ``theme_save``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.build import palette, themes
from gregtec.db import access
from gregtec.vault.io import VaultIO

COMMANDS = ("theme",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "theme",
        help="building themes: list, check palettes, try the selection modes (WO-10.6)",
        description="Reads the vault's themes, the starter themes and, for check, the DB.",
    )
    sub = p.add_subparsers(dest="theme_command", required=True)
    listing = sub.add_parser("list", help="every theme's current version")
    check = sub.add_parser(
        "check", help="check theme palettes against the block catalog"
    )
    check.add_argument("ids", nargs="*", help="theme ids (default: every theme)")
    select = sub.add_parser(
        "select", help="the brief a build gets: a theme, a prompt only, or blank"
    )
    select.add_argument("--theme", default=None, help="a theme id")
    select.add_argument("--prompt", default=None, help="Clayton's words for the build")
    select.add_argument(
        "--line-type",
        default=themes.GENERAL,
        choices=list(themes.LINE_TYPES),
        help="the build's line type (default general)",
    )
    select.add_argument(
        "--at",
        nargs=3,
        type=int,
        metavar=("DIM", "X", "Z"),
        default=None,
        help="the site, for themes used nearby (blank mode)",
    )
    for each in (listing, check, select):
        each.add_argument("--config", type=Path, default=None, help="gos.toml to read")


def run(args: argparse.Namespace, out: TextIO = sys.stdout) -> int:
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    store = themes.Themes(VaultIO(cfg.gos_home / "vault"))
    found, problems = store.load()
    if args.theme_command == "list":
        for t in found:
            print(
                f"{t.id}\tv{t.v}\t{t.source}\t{t.name}\t{', '.join(t.fits)}", file=out
            )
        for p in problems:
            print(f"not loaded: {p}", file=out)
        return 0
    if args.theme_command == "select":
        nearby = []
        if args.at is not None:
            dim, x, z = args.at
            nearby = themes.near(themes.load_uses(cfg.gos_home), dim, x, z)
        try:
            brief = themes.select(
                found,
                theme_id=args.theme,
                prompt=args.prompt,
                line_type=args.line_type,
                nearby=nearby,
            )
        except themes.ThemeError as exc:
            print(str(exc), file=out)
            return 2
        print(f"mode: {brief.mode}", file=out)
        print(f"line type: {brief.line_type} ({brief.concept})", file=out)
        print(
            f"theme: {brief.theme.id if brief.theme else '-'} ({brief.why})", file=out
        )
        if brief.prompt:
            print(f"prompt: {brief.prompt}", file=out)
        if brief.adaptation:
            print(f"adaptation: {brief.adaptation}", file=out)
        return 0
    return _check(cfg, found, problems, args.ids, out)


def _check(
    cfg: config.Config,
    found: list[themes.Theme],
    problems: list[str],
    ids: list[str],
    out: TextIO,
) -> int:
    db = access.db_path(cfg.gos_home)
    if not db.is_file():
        print(f"{db} doesn't exist: run `gos db build` first", file=out)
        return 2
    by_id = {t.id: t for t in found}
    if unknown := [i for i in ids if i not in by_id]:
        print(
            f"no theme {', '.join(unknown)}; the themes: {', '.join(by_id)}", file=out
        )
        return 2
    wanted = [by_id[i] for i in ids] if ids else found
    failed = bool(problems) and not ids
    conn = access.connect(db, readonly=True)
    try:
        for t in wanted:
            result = themes.check(conn, t)
            mark = "ok     " if result.ok else "REFUSED"
            print(f"{mark} {t.id} v{t.v} (catalog run {result.run_key})", file=out)
            for r in result.refused:
                print(f"          refused: {r}", file=out)
            for w in result.warnings:
                print(f"          warning: {w}", file=out)
            failed |= not result.ok
    except palette.CatalogError as exc:
        print(str(exc), file=out)
        return 2
    finally:
        conn.close()
    if not ids:
        for p in problems:
            print(f"not loaded: {p}", file=out)
    return 1 if failed else 0
