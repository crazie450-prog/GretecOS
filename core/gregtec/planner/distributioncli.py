"""``gos dist``: storage and distribution checks from the command line (WO-4.5, D240-D242).

- ``gos dist buffer --tier HV --peak 2400 --gen 1800 --window 120``: the deficit window
  ``(peak - gen) x window`` EU at the tier, a battery-buffer option and an LSC option, and which
  one is recommended (``--tier-cap`` lets the LSC use cells up to that tier; ``--max-buffers``).
- ``gos dist cable --tier HV --amps 4 --segment "1x Copper Cable=20" --segment mte:1246=5``:
  a cable run's checks (overvoltage, amp overload, loss), and the lowest-loss cable that would
  carry it; exit 1 when the run fails a check.
- ``gos dist transformer --from EV --to LV --amps 8``: the transformer chain between the tiers.
- ``gos dist ledger --window 120 [--scope all|running]``: buffers and transformers for the EU
  ledger's tiers (WO-4.4).

``--json`` prints any of them as JSON. Reads ``GOS_HOME/data/gos.sqlite`` (read-only); every
number is the DB's, cited ``mte:``/``item:``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from gregtec import config
from gregtec.db import access
from gregtec.planner import distribution as dist
from gregtec.planner import ledger

COMMANDS = ("dist",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "dist",
        help="storage and distribution: buffers/LSC, cable runs, transformers (WO-4.5)",
        description="Reads GOS_HOME/data/gos.sqlite.",
    )
    sub = p.add_subparsers(dest="dist_command", required=True)

    def common(q: argparse.ArgumentParser) -> None:
        q.add_argument("--json", action="store_true", help="print it as JSON")
        q.add_argument("--config", type=Path, default=None, help="gos.toml to read")

    b = sub.add_parser("buffer", help="size storage for a deficit window")
    b.add_argument("--tier", required=True, help="the tier the lines draw at")
    b.add_argument("--peak", type=float, required=True, help="the lines' peak EU/t")
    b.add_argument(
        "--gen", type=float, required=True, help="generation at the tier, EU/t"
    )
    b.add_argument("--window", type=float, required=True, help="the window, seconds")
    b.add_argument(
        "--tier-cap", default=None, help="highest tier for LSC cells (the tier)"
    )
    b.add_argument("--max-buffers", type=int, default=dist.MAX_BUFFERS)
    common(b)

    c = sub.add_parser("cable", help="check a cable run")
    v = c.add_mutually_exclusive_group(required=True)
    v.add_argument("--tier", help="the voltage: one amp of this tier")
    v.add_argument("--voltage", type=int, help="the voltage, EU")
    c.add_argument("--amps", type=float, required=True, help="the amps the run carries")
    c.add_argument(
        "--segment",
        action="append",
        required=True,
        metavar="CABLE=BLOCKS",
        help="a segment: cable name or mte:<id>, = its length (repeat, source first)",
    )
    common(c)

    t = sub.add_parser("transformer", help="place transformers between two tiers")
    t.add_argument("--from", dest="source", required=True, help="the source's tier")
    t.add_argument("--to", dest="line", required=True, help="the lines' tier")
    t.add_argument("--amps", type=float, required=True, help="the amps the lines draw")
    common(t)

    g = sub.add_parser(
        "ledger", help="buffers and transformers for the EU ledger's tiers"
    )
    g.add_argument(
        "--window", type=float, required=True, help="the peak window, seconds"
    )
    g.add_argument("--scope", choices=ledger.SCOPES, default="all")
    g.add_argument("--max-buffers", type=int, default=dist.MAX_BUFFERS)
    common(g)


def _segments(cat: dist.Catalog, specs: list[str]) -> list[dist.Segment]:
    out = []
    for spec in specs:
        key, sep, length = spec.rpartition("=")
        if not sep or not key:
            raise dist.DistributionError(f"segment {spec!r} isn't CABLE=BLOCKS")
        try:
            blocks = int(length)
        except ValueError:
            raise dist.DistributionError(
                f"segment {spec!r}: {length!r} isn't a length"
            ) from None
        out.append(dist.Segment(dist.find_cable(cat, key), blocks))
    return out


def _buffer_lines(s: dist.BufferSizing) -> list[str]:
    lines = [
        f"{s.tier}: peak {s.peak_eut:g} EU/t, generation {s.gen_eut:g} EU/t, window {s.window_s:g} s",
        f"  deficit {s.deficit_eut:g} EU/t x {s.window_s:g} s = {s.energy_eu} EU",
    ]
    for opt in (s.battery_buffer, s.lsc):
        if opt is None:
            continue
        lines.append(
            f"  {opt.kind}: {opt.count} x {opt.unit} = {opt.capacity_eu} EU"
            f" ({opt.detail}) [{', '.join(opt.refs)}]"
        )
    lines.append(f"  recommend: {s.recommend} ({s.reason})")
    lines.extend(f"  note: {n}" for n in s.notes)
    return lines


def _transformer_lines(p: dist.TransformerPlan) -> list[str]:
    lines = [
        f"{p.source} -> {p.line}: {p.amps:g} A at {p.line}, {p.source_amps} A at {p.source}"
    ]
    for st in p.steps:
        a, b = (st.high, st.low) if st.mode == "down" else (st.low, st.high)
        lines.append(
            f"  {a} -> {b} ({st.mode}): {st.count} x {st.unit.name} [{st.unit.ref}]"
            f" ({st.unit.amps_in} A in, {st.unit.amps_out} A out each; gives {st.amps_out:g} A,"
            f" draws {st.amps_in} A)"
        )
    lines.extend(f"  note: {n}" for n in p.notes)
    return lines


def _cable_lines(chk: dist.CableCheck, better: dist.Cable | None) -> list[str]:
    lines = [
        f"run: {chk.voltage} V, {chk.amps:g} A over "
        + " + ".join(
            f"{s.length} x {s.cable.name} [{s.cable.ref}]" for s in chk.segments
        ),
        (
            f"  limits: {chk.max_voltage} V, {chk.max_amps} A;"
            f" loss {chk.loss_per_amp} V per amp -> {chk.voltage_out} V out,"
            f" {chk.loss_eut:g} EU/t lost, {chk.delivered_eut:g} EU/t delivered"
        ),
    ]
    lines.extend(f"  problem: {p}" for p in chk.problems)
    lines.append("  ok" if chk.ok else f"  burns: {', '.join(chk.burns) or 'none'}")
    if better is not None:
        lines.append(
            f"  lowest-loss cable for {chk.voltage} V, {chk.amps:g} A: {better.name}"
            f" [{better.ref}] ({better.voltage} V, {better.amps} A, loss {better.loss})"
        )
    return lines


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
        cat = dist.catalog(conn)
        tiers = ledger.read(conn).tiers if args.dist_command == "ledger" else {}
    finally:
        conn.close()
    try:
        return _run(args, cat, tiers, out)
    except dist.DistributionError as exc:
        print(str(exc), file=out)
        return 2


def _run(
    args: argparse.Namespace, cat: dist.Catalog, tiers: dict[str, Any], out: TextIO
) -> int:
    cmd = args.dist_command
    if cmd != "cable" and cat.run_id is None:
        raise dist.DistributionError(
            "the DB has no storage data: build it from an export made with WO-4.5's GregtecLink"
        )
    data: Any
    status = 0
    if cmd == "buffer":
        s = dist.size_buffer(
            cat,
            args.tier,
            args.peak,
            args.gen,
            args.window,
            tier_cap=args.tier_cap,
            max_buffers=args.max_buffers,
        )
        data, lines = s.as_dict(), _buffer_lines(s)
    elif cmd == "cable":
        volts = args.voltage if args.voltage is not None else dist.voltage(args.tier)
        chk = dist.check_run(_segments(cat, args.segment), volts, args.amps)
        better = dist.suggest_cable(cat, volts, args.amps)
        data = {**chk.as_dict(), "suggest": None if better is None else better.ref}
        lines = _cable_lines(chk, better)
        status = 0 if chk.ok else 1
    elif cmd == "transformer":
        p = dist.place_transformers(cat, args.source, args.line, args.amps)
        data, lines = p.as_dict(), _transformer_lines(p)
    else:
        rep = dist.from_ledger(
            cat,
            tiers[args.scope],
            args.window,
            scope=args.scope,
            max_buffers=args.max_buffers,
        )
        data = rep.as_dict()
        lines = [f"ledger ({rep.scope}), window {rep.window_s:g} s"]
        for b in rep.buffers:
            lines.extend(_buffer_lines(b))
        for p in rep.transformers:
            lines.extend(_transformer_lines(p))
        lines.extend(f"note: {n}" for n in rep.notes)
    if args.json:
        print(json.dumps(data, sort_keys=True), file=out)
    else:
        print("\n".join(lines), file=out)
    return status
