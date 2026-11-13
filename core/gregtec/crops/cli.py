"""``gos crops guide|have|target|check`` (WO-6.7): the breeding guide in a terminal, what
Clayton has, the crop he follows, and the citation check over guides (WO-6.7's acceptance);
``gos crops station`` (WO-7.6): the breeding station's plan for a crop, and ``--parts`` the
station's parts and the recipes that make them (V31). It never proposes: starting the station is
a world action, proposed from the dashboard or by voice and confirmed by Clayton (D59)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from gregtec import config, store
from gregtec.crops import facts as crop_facts
from gregtec.crops import planner, state, station, view
from gregtec.db import access

COMMANDS = frozenset({"guide", "have", "target", "check", "station"})


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
    s = crop_cmds.add_parser(
        "station",
        help="the breeding station's plan for a crop (default: the target), or --parts",
        description=(
            "Prints the plan the OC breeding station would run for a crop from your crops "
            "(gos crops have) at your tier: each Crop Breeder step with its chance, runs, "
            "copies made first and fertilizer, the crops worth a Seed Generator, and the "
            "crop_start command it becomes. --parts lists the station's parts and the recipes "
            "that make them. It proposes nothing: start the station from the dashboard or by "
            "voice, and confirm it."
        ),
    )
    s.add_argument("crop", nargs="?", help="crop name or id (default: the target)")
    s.add_argument(
        "--count", type=int, default=station.TARGET_COUNT, help="seeds to stock"
    )
    s.add_argument("--tier", type=int, default=None, help="GT tier to plan at (1 = LV)")
    s.add_argument("--json", action="store_true", help="print the plan as JSON")
    s.add_argument(
        "--parts", action="store_true", help="the station's parts and recipes"
    )
    _config_arg(s)


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


def _pick(
    cur: view.Current, words: str, out: TextIO, write: bool = False
) -> str | None:
    """The crop ``words`` names. Reading, several matches show the first; writing, they are
    refused (his list and target are never saved to a guessed crop)."""
    found = view.find(cur.facts, words)
    if not found:
        print(f"No crop matches {words!r}", file=out)
        return None
    if len(found) > 1:
        names = ", ".join(f"{cur.facts.crop_name(f)} (crop:{f})" for f in found[:8])
        if write:
            print(
                f"{words!r} matches several crops ({names}): name one exactly, or give "
                "its crop: id; nothing saved",
                file=out,
            )
            return None
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
            crop = _pick(cur, args.crop, out, write=True)
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
            crop = _pick(cur, args.crop, out, write=True)
            if crop is None:
                return 1
            state.set_target(conn, crop, None, cur.facts.data.crops)
            print(f"Following crop:{crop}", file=out)
            return 0
        if cmd == "station":
            return _station(args, conn, cur, out)
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
    except (
        crop_facts.NoCropData,
        planner.GuideError,
        state.CropStateError,
        station.StationError,
    ) as exc:
        print(str(exc), file=out)
        return 1
    finally:
        conn.close()


def _station(
    args: argparse.Namespace, conn: sqlite3.Connection, cur: view.Current, out: TextIO
) -> int:
    if args.parts:
        rows = station.parts(conn, cur.facts)
        if args.json:
            print(json.dumps(rows, indent=2), file=out)
            return 0
        for r in rows:
            if not r["in_db"]:
                print(f"{r['part']}: not in this DB (no export of it here)", file=out)
                continue
            mv = "craftable at MV" if r["craftable_at_mv"] else "NOT craftable at MV"
            refs = ", ".join(
                f"{x['ref']} ({x['source']}, tier {x['voltage_tier']})"
                for x in r["recipes"]
            )
            print(f"{r['part']}: {mv} [{refs or 'no recipe'}]", file=out)
        return 0
    crop = _pick(cur, args.crop, out) if args.crop else None
    if args.crop and crop is None:
        return 1
    crop = crop or (cur.target.crop if cur.target else None)
    if crop is None:
        print("No target: name a crop, or set one with gos crops target", file=out)
        return 1
    plan = station.build(cur.facts, cur.have, crop, cur.tier, args.count)
    if args.json:
        print(
            json.dumps({"plan": station.as_dict(plan), "args": plan.args()}, indent=2),
            file=out,
        )
        return 0
    for line in plan.lines:
        print(line, file=out)
    print(
        f"About {station.expected_fertilizer(plan)} mB of enriched fertilizer for the "
        f"breeder's runs on average [{', '.join(plan.refs[:2])}]",
        file=out,
    )
    print(
        f"Plan {plan.id}: {station.as_dict(plan)['args_bytes']} bytes as a command",
        file=out,
    )
    return 0
