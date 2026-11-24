"""``gos shell``: decorative shells around a line (WO-10.7, plan 7.10, D316-D318).

- ``gos shell design PLAN_ID [PLAN_ID ...] [--theme ID] [--prompt TEXT] [--line-type T]
  [--palette FILE] [--exclude BLOCK ...] [--max-blocks N] [--headroom N] [--design FILE]
  [--mode layout|site]``: lays the plans out (WO-10.5), designs the shell (the procedural
  generator, or the operations in ``--design``, a JSON list in the architect's vocabulary),
  checks it (repairs a small breach, refuses anything else) and saves the build in
  ``GOS_HOME/data/build/shells/<sh-id>/``. ``--mode site`` (D422) designs the building first
  inside a site sized from the machines and fits the machines into it afterwards; ``layout``
  (the default) builds around the laid-out machines. Works with no AI and no key (No-AI mode).
  Exit 0 saved, 1 refused (nothing written), 2 bad input or setup.
- ``gos shell list`` and ``gos shell show BUILD [--card]``: saved builds; one build's palette
  manifest, notes and paste steps (or its dashboard card).
- ``gos shell export BUILD [--world NAME | --world-dir DIR] [--over split|addblocks] [--out FILE]``:
  designs the build again from its files, checks it again, applies the palette rule against the
  block catalog and the **Lab** world's registry (its ``level.dat``, read-only), and writes the
  ``.schematic`` to ``GOS_HOME/data/schematics/shells/`` (never into Play, Prism's instances,
  Export or the Lab: Clayton copies it, runbook ``shells_lab.md``). Exit 0 written, 1 refused, 2.

Iterating (WO-10.10, D367): every saved design starts a **design** whose versions form a chain
(``data/build/designs/``); nothing is ever deleted.

- ``gos shell versions [DESIGN]``: the designs, or one design's versions (what was asked, by whom,
  blocks, cost; ``*`` marks the current one).
- ``gos shell revise [--design DS] [--theme ID] [--max-blocks N] [--headroom N] WORDS...``: a new
  version of the current design (or ``--design``) by GOS's own reviser (no AI, free), through the
  same gate; refused: nothing written, the current version stays. Exit 0, 1 refused, 2.
- ``gos shell refit BUILD``: a site build's (D422) new version with the same building and its
  plans' machines as they are today fitted into it again (export names it when they changed);
  free, no AI. Exit 0, 1 refused (they no longer fit: revise the building), 2.
- ``gos shell undo [DESIGN]`` and ``gos shell current DESIGN BUILD``: make the previous version, or
  any version, current again.
- ``gos shell compare BUILD BUILD``: two versions' materials and block counts side by side.
- ``gos shell bakeoff ...``: the same briefs to several models through the gate, rated blind
  (WO-10.11; :mod:`gregtec.build.shell_bakeoff_cli`). Its shells are kept apart from these.

``--palette FILE`` holds extra swatches, one ``<role> <registry name>@<meta>`` a line (a
prompt-only shell needs its wall and roof there).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from gregtec import config, doctor
from gregtec.build import (
    palette,
    schematic,
    shell,
    shell_bakeoff_cli,
    shell_store,
    worldreg,
)
from gregtec.build import themes as theme_lib
from gregtec.db import access
from gregtec.db import ores as ore_db
from gregtec.vault.io import VaultIO

COMMANDS = ("shell",)
DESIGN_MAX = 1 << 20


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "shell",
        help="design, show and export decorative shells around a line (WO-10.7)",
        description="Shells hold plain blocks only; GOS never pastes machines.",
    )
    sub = p.add_subparsers(dest="shell_command", required=True)
    d = sub.add_parser("design", help="design a shell around saved plans' layout")
    d.add_argument("plans", nargs="+", metavar="PLAN_ID")
    d.add_argument("--theme", default=None, help="th-<name> (default: blank mode)")
    d.add_argument("--prompt", default="", help="Clayton's words for the build")
    d.add_argument(
        "--line-type",
        default=theme_lib.GENERAL,
        choices=list(theme_lib.LINE_TYPES),
    )
    d.add_argument("--palette", type=Path, default=None, help="extra swatches file")
    d.add_argument("--exclude", action="append", default=[], metavar="BLOCK@META")
    d.add_argument("--max-blocks", type=int, default=None)
    d.add_argument("--headroom", type=int, default=None)
    d.add_argument(
        "--design", type=Path, default=None, help="a JSON list of operations"
    )
    d.add_argument(
        "--mode",
        choices=shell_store.MODES,
        default="layout",
        help="layout: build around the laid-out machines (default); site: design the "
        "building first and fit the machines into it (D422)",
    )
    sub.add_parser("list", help="the saved shell builds")
    v = sub.add_parser("versions", help="the shell designs, or one design's versions")
    v.add_argument("design", nargs="?", default=None)
    r = sub.add_parser(
        "revise", help="a new version from your words (GOS's reviser, no AI)"
    )
    r.add_argument("words", nargs="+")
    r.add_argument(
        "--design", default=None, help="ds-<12 hex> (default: the current one)"
    )
    r.add_argument("--theme", default=None, help="th-<name>: another theme's palette")
    r.add_argument("--max-blocks", type=int, default=None)
    r.add_argument("--headroom", type=int, default=None)
    f = sub.add_parser(
        "refit", help="a site build's machines as they are today, fitted again (D422)"
    )
    f.add_argument("build")
    u = sub.add_parser("undo", help="make the current version's parent current again")
    u.add_argument("design", nargs="?", default=None)
    c = sub.add_parser("current", help="make one version of a design current")
    c.add_argument("design")
    c.add_argument("build")
    k = sub.add_parser("compare", help="two versions' materials and block counts")
    k.add_argument("a")
    k.add_argument("b")
    s = sub.add_parser("show", help="one build's manifest, notes and paste steps")
    s.add_argument("build")
    s.add_argument("--card", action="store_true", help="print the dashboard card")
    e = sub.add_parser("export", help="write a build's .schematic for the Lab")
    e.add_argument("build")
    where = e.add_mutually_exclusive_group()
    where.add_argument(
        "--world",
        default=None,
        help="the world in the Lab's saves/ (default [paths] lab_world)",
    )
    where.add_argument(
        "--world-dir",
        type=Path,
        default=None,
        help="a Lab world or a test copy only, never Play (only its level.dat registry is read)",
    )
    e.add_argument("--over", choices=("split", "addblocks"), default="split")
    e.add_argument("--out", type=Path, default=None, help="the .schematic to write")
    shell_bakeoff_cli.add_parsers(sub)  # gos shell bakeoff ... (WO-10.11)
    for each in (d, sub.choices["list"], s, e, v, r, f, u, c, k):
        each.add_argument("--config", type=Path, default=None, help="gos.toml to read")


def extra_swatches(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        if len(parts) < 2:
            raise shell.ShellError(
                f"a palette line is <role> <registry name>@<meta>: {line!r}"
            )
        out.append((parts[0], parts[1]))
    return out


def run(args: argparse.Namespace, out: TextIO = sys.stdout) -> int:
    if args.shell_command == shell_bakeoff_cli.COMMAND:
        return shell_bakeoff_cli.run(args, out)
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    home = cfg.gos_home
    try:
        if args.shell_command == "list":
            for b in shell_store.builds(home):
                sx, sy, sz = b["size"]
                print(
                    f"{b['id']}\t{b['status']}\t{b['source']}\t{b['theme'] or '-'}\t"
                    f"{sx}x{sy}x{sz}\t{b['blocks']} blocks\tplans {', '.join(b['plans'])}",
                    file=out,
                )
            return 0
        if args.shell_command == "show":
            return _show(home, args, out)
        if args.shell_command in ("versions", "undo", "current", "compare"):
            return _chain(home, args, out)
    except shell.ShellError as exc:
        print(str(exc), file=out)
        return 2
    db = access.db_path(home)
    if not db.is_file():
        print(f"{db} doesn't exist: run `gos db build` first", file=out)
        return 2
    if args.shell_command == "design":
        return _design(cfg, args, out)
    if args.shell_command in ("revise", "refit"):
        return _revise(cfg, args, out)
    return _export(cfg, args, out)


def _version_line(v: dict[str, Any], current: str) -> str:
    cost = (
        "free"
        if v["cost_usd"] == 0
        else ("see Costs" if v["cost_usd"] is None else f"${v['cost_usd']:.2f}")
    )
    mark = "*" if v["build"] == current else " "
    return (
        f"{mark} v{v['n']}\t{v['build']}\t{v['status']}\t{v['blocks']} blocks\t{v['by']}, "
        f"{cost}\tfrom {v['parent'] or '-'}\t{v['asked']}"
    )


def _chain(home: Path, args: argparse.Namespace, out: TextIO) -> int:
    from gregtec.build import shell_versions as versions

    if args.shell_command == "compare":
        got = versions.compare(home, args.a, args.b)
        print(
            f"{args.a}: {got['a']['blocks']} blocks; {args.b}: {got['b']['blocks']} blocks "
            f"({got['blocks_delta']:+d})",
            file=out,
        )
        for m in got["materials"]:
            print(f"  {m['block']}\t{m['a']}\t{m['b']}\t{m['delta']:+d}", file=out)
        for line in got["ops"]:
            print(f"  ops: {line}", file=out)
        return 0
    if args.shell_command == "undo":
        d, cur = versions.undo(home, args.design)
        print(
            f"design {d['id']}: v{cur['n']} {cur['build']} is current again", file=out
        )
        return 0
    if args.shell_command == "current":
        d = versions.make_current(home, args.design, args.build)
        print(f"design {d['id']}: {d['current']} is current", file=out)
        return 0
    if args.design is None:
        now = versions.current_id(home)
        for s in versions.designs(home):
            mark = "*" if s["id"] == now else " "
            print(
                f"{mark} {s['id']}\tv{s['current_n']} of {s['versions']}\t{s['current']}\t"
                f"{s['blocks']} blocks\tplans {', '.join(s['plans'])}\t{s['asked']}",
                file=out,
            )
        return 0
    d = versions.get(home, args.design)
    for v in d["versions"]:
        print(_version_line(v, d["current"]), file=out)
    return 0


def _revise(cfg: config.Config, args: argparse.Namespace, out: TextIO) -> int:
    from gregtec.build import shell_versions as versions

    home = cfg.gos_home
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        if args.shell_command == "refit":
            got = versions.refit(conn, home, args.build)
        else:
            got = versions.revise(
                conn,
                home,
                VaultIO(home / "vault"),
                args.design,
                " ".join(args.words),
                theme_id=args.theme,
                max_blocks=args.max_blocks,
                headroom=args.headroom,
            )
    except (shell.ShellError, palette.CatalogError) as exc:
        print(str(exc), file=out)
        return 2
    finally:
        conn.close()
    if not got.saved:
        print(f"REFUSED: nothing written; {got.parent} stays current", file=out)
        for why in got.reasons:
            print(f"  {why}", file=out)
        return 1
    assert got.version is not None
    print(_version_line(got.version, got.build), file=out)
    for c in got.changes:
        print(f"  {c}", file=out)
    print(
        f"next: gos shell export {got.build} (writes the .schematic for the Lab)",
        file=out,
    )
    return 0


def _show(home: Path, args: argparse.Namespace, out: TextIO) -> int:
    if args.card:
        print(json.dumps(shell_store.card(home, args.build), indent=1), file=out)
        return 0
    rec = shell_store.load(home, args.build)
    for line in render(rec):
        print(line, file=out)
    print("Paste (Lab only):", file=out)
    for step in shell_store.paste_steps(rec):
        print(f"  {step}", file=out)
    print(f"  Export it first: gos shell export {rec['id']}", file=out)
    return 0


def render(rec: dict[str, Any]) -> list[str]:
    sx, sy, sz = rec["bounds"]["size"]
    lines = [
        f"Shell {rec['id']}: {rec['status']} ({rec['source']}), {rec['blocks']} blocks of "
        f"{rec['max_blocks']}, {sx} x {sz} blocks and {sy} high, theme "
        f"{rec.get('theme') or 'none'}, layout {rec['layout']} of "
        + ", ".join(f"plan:{p}" for p in rec["plans"]),
        "palette (role, block, name, count):",
    ]
    lines += [
        f"  {m['role']}\t{m['block']}\t{m['name'] or ''}\t{m['count']}"
        for m in rec["materials"]
    ]
    lines += [f"note: {n}" for n in rec["notes"]]
    return lines


def _design(cfg: config.Config, args: argparse.Namespace, out: TextIO) -> int:
    home = cfg.gos_home
    try:
        extra = (
            extra_swatches(args.palette.read_text(encoding="utf-8-sig"))
            if args.palette is not None
            else []
        )
        ops = None
        if args.design is not None:
            raw = args.design.read_bytes()
            if len(raw) > DESIGN_MAX:
                raise shell.ShellError(
                    f"{args.design} is larger than {DESIGN_MAX} bytes"
                )
            ops = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(ops, list):
                raise shell.ShellError("the design file is a JSON list of operations")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"Can't read the input: {exc}", file=out)
        return 2
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        made = shell_store.make(
            conn,
            home,
            VaultIO(home / "vault"),
            args.plans,
            theme_id=args.theme,
            prompt=args.prompt,
            line_type=args.line_type,
            extra=extra,
            exclude=args.exclude,
            max_blocks=args.max_blocks,
            headroom=args.headroom,
            ops=ops,
            mode=args.mode,
        )
    except (shell.ShellError, palette.CatalogError) as exc:
        print(str(exc), file=out)
        return 2
    finally:
        conn.close()
    if not made.outcome.ok:
        print(
            f"REFUSED: nothing written. {made.build} ({made.outcome.source})", file=out
        )
        for why in made.outcome.reasons:
            print(f"  {why}", file=out)
        return 1
    from gregtec.build import shell_versions as versions

    design = versions.start(
        home,
        made.build,
        asked=args.prompt or "gos shell design",
        by="architect" if made.outcome.source == "architect" else "procedural",
    )
    print(f"build {made.build} saved in {made.saved} (design {design['id']})", file=out)
    for line in render(made.record):
        print(line, file=out)
    print(
        f"next: gos shell export {made.build} (writes the .schematic for the Lab)",
        file=out,
    )
    return 0


def _export(cfg: config.Config, args: argparse.Namespace, out: TextIO) -> int:
    home = cfg.gos_home
    try:
        rec = shell_store.load(home, args.build)
        world = args.world_dir or ore_db.world_folder(cfg, "lab", args.world)[0]
        registry = worldreg.read(world)
    except shell.ShellError as exc:
        print(str(exc), file=out)
        return 2
    except (ore_db.OresError, worldreg.WorldRegistryError) as exc:
        print(f"Can't read the Lab world's registry: {exc}", file=out)
        return 2
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        exported = shell_store.export(
            conn, home, args.build, registry, args.over, args.out
        )
    except schematic.ShellRefused as exc:
        for line in exc.lines:
            print(line, file=out)
        print("REFUSED: nothing written", file=out)
        return 1
    except shell.ShellError as exc:
        print(f"REFUSED: nothing written. {exc}", file=out)
        return 1
    except (palette.CatalogError, schematic.SchematicError) as exc:
        print(str(exc), file=out)
        return 2
    finally:
        conn.close()
    folders = doctor.guarded(cfg)
    for path in exported.paths:
        if (why := doctor.touch_problem(path, folders)) is not None:
            # GOS never writes into the Lab, Export or Play; Clayton copies the file (D309 (6))
            print(
                f"Won't write {path}: it {why}. Write it elsewhere and copy it",
                file=out,
            )
            return 2
    try:
        shell_store.write(exported)
    except OSError as exc:
        print(f"Can't write: {exc}", file=out)
        return 2
    for part, path in zip(exported.written.parts, exported.paths, strict=True):
        s = part.schematic
        print(f"wrote {path} ({s.size}, WEOffset {s.offset})", file=out)
    for note in exported.written.notes:
        print(f"note: {note}", file=out)
    print("Paste (Lab only):", file=out)
    for step in shell_store.paste_steps(rec):
        print(f"  {step}", file=out)
    return 0
