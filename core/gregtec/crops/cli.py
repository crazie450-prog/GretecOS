"""``gos crops guide|have|target|check`` (WO-6.7): the breeding guide in a terminal, what
Clayton has, the crop he follows, and the citation check over guides (WO-6.7's acceptance)."""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from gregtec import config, store
from gregtec.crops import facts as crop_facts
from gregtec.crops import planner, state, view
from gregtec.db import access

COMMANDS = frozenset({"guide", "have", "target", "check"})


def _config_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
        "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
    )


def add_parsers(crop_cmds: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    g = crop_cmds.add_parser(
        "guide",
        help="the breeding guide to a crop (default: the target), step by step",
        description=(
            "Prints the guide to a crop from the crops you have (gos crops have) at your tier: "
            "each step's layout, requirements, odds, times, warnings and keep rule, then the "
            "stats plan. Every number shows its citations. --next prints only the next step."
        ),
    )
    g.add_argument("crop", nargs="?", help="crop name or id (default: the target)")
    g.add_argument("--next", action="store_true", help="only the next step not done")
    g.add_argument("--tier", type=int, default=None, help="GT tier to plan at (1 = LV)")
    _config_arg(g)
    h = crop_cmds.add_parser(
        "have",
        help="list your crops, or set one's best stats (Growth Gain Resistance)",
    )
    h.add_argument("crop", nargs="?", help="crop name or id")
    h.add_argument("stats", nargs="*", type=int, help="Growth Gain Resistance (1-31)")
    h.add_argument("--remove", action="store_true", help="drop the crop from your list")
    _config_arg(h)
    t = crop_cmds.add_parser("target", help="show or set the crop the guide follows")
    t.add_argument("crop", nargs="?", help="crop name or id")
    t.add_argument("--clear", action="store_true", help="follow no crop")
    _config_arg(t)
    c = crop_cmds.add_parser(
        "check",
        help="build guides and check every number is cited (default: the PM's five picks)",
    )
    c.add_argument("crop", nargs="*", help="crop names or ids")
    c.add_argument("--tier", type=int, default=None, help="GT tier to plan at (1 = LV)")
    _config_arg(c)


def _print_lines(out: TextIO, title: str, lines: Sequence[planner.Line]) -> None:
    for x in lines:
        refs = " ".join(x.as_dict()["refs"])
        print(f"    {title}{x.text()}" + (f"  [{refs}]" if refs else ""), file=out)


def print_step(out: TextIO, s: planner.Step) -> None:
    state_ = "done" if s.done else "to do"
    print(f"  {s.n}. {s.title} ({s.kind}, {state_})", file=out)
    for row in s.layout:
        cells = []
        for cell in row:
            if cell.role == "empty":
                cells.append(".")
            else:
                mark = "[x]" if cell.role == "double" else ""
                cells.append(
                    f"{cell.name}{mark}" + (f" on {cell.under}" if cell.under else "")
                )
        print("    | " + " | ".join(cells), file=out)
    for q in s.requirements:
        _print_lines(out, f"{q.status}: ", [q.line])
    _print_lines(out, "", s.numbers)
    _print_lines(out, "warning: ", s.warnings)
    _print_lines(out, "keep: ", s.keep)


def print_guide(out: TextIO, g: planner.Guide, only_next: bool = False) -> None:
    print(f"# {g.target_name} (crop:{g.target}), run {g.run_key}", file=out)
    steps = [g.next_step()] if only_next else g.steps
    for s in steps:
        if s is None:
            print("  every step is done", file=out)
            continue
        print_step(out, s)
    if only_next:
        return
    if g.total is not None:
        _print_lines(out, "", [g.total])
    if g.stats is not None:
        print("  stats plan:", file=out)
        _print_lines(out, "", g.stats.lines)
        _print_lines(out, "warning: ", g.stats.warnings)


def _open(cfg: config.Config, write: bool) -> sqlite3.Connection:
    path = access.db_path(cfg.gos_home)
    if not path.is_file():
        raise FileNotFoundError(f"{path} doesn't exist; run gos db build first")
    return store.connect(path) if write else access.connect(path, readonly=True)


def _pick(cur: view.Current, words: str, out: TextIO) -> str | None:
    found = view.find(cur.facts, words)
    if not found:
        print(f"No crop matches {words!r}", file=out)
        return None
    if len(found) > 1:
        names = ", ".join(cur.facts.crop_name(f) for f in found[:8])
        print(f"{words!r} matches several crops ({names}); using {found[0]}", file=out)
    return found[0]


def run(args: argparse.Namespace, out: TextIO) -> int:
    """0 on success; 1 for a missing crop, guide or problem; 2 for a bad gos.toml."""
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    cmd = args.crops_command
    write = cmd in ("have", "target") and (
        bool(getattr(args, "stats", None))
        or getattr(args, "remove", False)
        or getattr(args, "clear", False)
        or (cmd == "target" and args.crop is not None)
    )
    try:
        conn = _open(cfg, write)
    except (OSError, access.SchemaError, sqlite3.Error) as exc:
        print(f"No crops: {exc}", file=out)
        return 1
    try:
        cur = view.current(conn)
        if getattr(args, "tier", None) is not None:
            cur.tier = args.tier
        if cmd == "guide":
            crop = _pick(cur, args.crop, out) if args.crop else None
            if args.crop and crop is None:
                return 1
            g = cur.guide(crop)
            if g is None:
                print(
                    "No target: name a crop, or set one with gos crops target", file=out
                )
                return 1
            print_guide(out, g, args.next)
            return 0
        if cmd == "have":
            if args.crop is None:
                for cid, s in cur.have.items():
                    print(
                        f"crop:{cid}\t{cur.facts.crop_name(cid)}\t{s[0]}/{s[1]}/{s[2]}",
                        file=out,
                    )
                if not cur.have:
                    print("No crops listed (alternate seeds still count)", file=out)
                return 0
            crop = _pick(cur, args.crop, out)
            if crop is None:
                return 1
            if args.remove:
                state.remove_have(conn, crop)
            else:
                stats = args.stats or [1, 1, 1]
                state.set_have(conn, crop, list(stats), cur.facts.data.crops)
            print(f"crop:{crop} saved", file=out)
            return 0
        if cmd == "target":
            if args.clear:
                state.set_target(conn, None)
                print("No target", file=out)
                return 0
            if args.crop is None:
                t = cur.target
                print(
                    "No target"
                    if t is None
                    else f"crop:{t.crop}\t{cur.facts.crop_name(t.crop)}",
                    file=out,
                )
                return 0
            crop = _pick(cur, args.crop, out)
            if crop is None:
                return 1
            state.set_target(conn, crop, None, cur.facts.data.crops)
            print(f"Following crop:{crop}", file=out)
            return 0
        # check
        crops = [c for w in args.crop if (c := _pick(cur, w, out))] or [
            c for c in view.PM_PICKS if c in cur.facts.data.crops
        ]
        code = 0
        for cid in crops:
            g = cur.guide(cid)
            assert g is not None
            problems = planner.check(g, cur.facts)
            nums = sum(1 for o in planner._walk(g) if isinstance(o, planner.Num))
            print(
                f"{g.target_name} (crop:{cid}): {len(g.steps)} steps, {nums} numbers, "
                f"{len(problems)} problems",
                file=out,
            )
            for p in problems:
                print(f"  {p}", file=out)
            code = code or (1 if problems else 0)
        return code
    except (crop_facts.NoCropData, planner.GuideError, state.CropStateError) as exc:
        print(str(exc), file=out)
        return 1
    finally:
        conn.close()
