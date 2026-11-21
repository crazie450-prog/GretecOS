"""``gos shell bakeoff``: the same shell briefs to several models, rated blind (WO-10.11,
D374-D377; :mod:`gregtec.build.shell_bakeoff`).

- ``gos shell bakeoff models``: the models a run can name: the built-in fakes (free) and
  gos.toml's ``[[bakeoff.models]]`` (label, provider, model id, effort, price or "unknown",
  whether its key is stored).
- ``gos shell bakeoff run PLAN_ID [PLAN_ID ...] --models A,B [--large PLAN_ID[,PLAN_ID]]
  [--cases FILE] [--limit N] [--cap-usd X] [--cap-tokens N] [--seed N] [--yes]``: every case of
  ``tests/evals/shell_bakeoff.tsv`` to every model named, around the saved plans (the small
  cases house ``PLAN_ID``, the large ones ``--large``, default the same). It prints what it will
  ask, what it can cost and which hosts it contacts, and waits for ``yes`` (``--yes`` skips the
  question, never the caps). Fake models need no key and cost nothing. Real ones need their
  key in the keyring (``gos key set anthropic`` / ``gos key set openai``), are refused while the
  AI is off (No-AI mode) or when the run could pass today's ``[budget] day_max_usd``, and stop
  at the run's cap with what they have. Ctrl+C stops cleanly too.
- ``gos shell bakeoff list`` and ``gos shell bakeoff show RUN``: the runs; one run as the rater
  sees it (letters, never names).
- ``gos shell bakeoff rate RUN [CASE PICK]``: your pick for one case (a letter, ``tie`` or
  ``bad``), or, with no case, each unrated case asked in turn.
- ``gos shell bakeoff report RUN [--reveal]``: the models named and the numbers, written to
  ``GOS_HOME/data/reports/shell-bakeoff-<run>.md``. Refused until every case is rated, unless
  ``--reveal`` (which closes the ratings).
- ``gos shell bakeoff delete RUN [--yes]``: says what it removes (the run's folder and its
  report), then removes it.

Exit 0 done, 1 refused, stopped or not run, 2 bad input or setup. Nothing here writes a world.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

COMMAND = "bakeoff"
Answer = Callable[[], str]


def add_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        COMMAND,
        help="the same briefs to several models through the gate, rated blind (WO-10.11)",
        description=(
            "A bake-off run asks each model named for a shell design and one follow-up per "
            "case, checks every answer with the shell gate, saves what passes apart from "
            "your own shells, and lets you rate the results blind (A, B, C). Fake models "
            "are free; real ones are paid: the estimate is printed and waits for yes."
        ),
    )
    cmds = p.add_subparsers(dest="bakeoff_command", required=True)
    m = cmds.add_parser("models", help="the models a run can name")
    r = cmds.add_parser("run", help="run the cases against the models named")
    r.add_argument(
        "plans",
        nargs="+",
        metavar="PLAN_ID",
        help="the saved plan(s) the small cases house (a line, and its power plan if any)",
    )
    r.add_argument(
        "--large",
        default=None,
        metavar="PLAN_ID[,PLAN_ID]",
        help="the saved plan(s) the large cases house (default: the same as PLAN_ID)",
    )
    r.add_argument(
        "--models",
        required=True,
        metavar="A,B",
        help="labels from `gos shell bakeoff models`, comma-separated",
    )
    r.add_argument("--cases", type=Path, default=None, help="another case file")
    r.add_argument("--limit", type=int, default=None, help="only the first N cases")
    r.add_argument(
        "--cap-usd", type=float, default=None, help="this run's hard cap in USD"
    )
    r.add_argument(
        "--cap-tokens",
        type=int,
        default=None,
        help="this run's hard cap in tokens for models without a price",
    )
    r.add_argument("--seed", type=int, default=None, help="the blind lettering's seed")
    r.add_argument(
        "--yes",
        action="store_true",
        help="spend without asking (the estimate is still printed and the caps still hold)",
    )
    ls = cmds.add_parser("list", help="the bake-off runs")
    s = cmds.add_parser("show", help="one run, blind: letters, blocks, your picks")
    s.add_argument("run")
    t = cmds.add_parser("rate", help="pick the better entry of a case (blind)")
    t.add_argument("run")
    t.add_argument("case", nargs="?", default=None)
    t.add_argument("pick", nargs="?", default=None, help="a letter, tie or bad")
    rep = cmds.add_parser("report", help="name the models and write the report")
    rep.add_argument("run")
    rep.add_argument(
        "--reveal",
        action="store_true",
        help="name the models although cases are unrated (closes the ratings)",
    )
    d = cmds.add_parser(
        "delete", help="remove a whole run (says what it removes first)"
    )
    d.add_argument("run")
    d.add_argument("--yes", action="store_true", help="don't ask")
    for each in (m, r, ls, s, t, rep, d):
        each.add_argument("--config", type=Path, default=None, help="gos.toml to read")


def run(
    args: argparse.Namespace,
    out: TextIO = sys.stdout,
    answer_in: Answer = lambda: sys.stdin.readline(),
) -> int:
    from gregtec import config
    from gregtec.build import shell
    from gregtec.build import shell_bakeoff as bo

    try:
        cfg = config.load(args.config)
        settings = bo.read_settings(args.config or config.default_path())
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    home = cfg.gos_home
    command = args.bakeoff_command
    try:
        if command == "models":
            return _models(settings, out)
        if command == "list":
            found = bo.runs(home)
            for r in found:
                print(_run_line(r), file=out)
            if not found:
                print("No bake-off run yet.", file=out)
            return 0
        if command == "show":
            return _show(bo.blind(bo.load(home, args.run)), out)
        if command == "rate":
            return _rate(home, args, out, answer_in)
        if command == "report":
            got = bo.report(home, args.run, reveal_unrated=args.reveal)
            print(got.text, file=out)
            print(f"Written: {got.path}", file=out)
            return 0
        if command == "delete":
            for line in bo.removal(home, args.run):
                print(f"Removes: {line}", file=out)
            if not args.yes:
                print("Type yes to remove it: ", end="", file=out, flush=True)
                if answer_in().strip().casefold() != "yes":
                    print("Nothing removed.", file=out)
                    return 1
            bo.delete(home, args.run)
            print(f"Removed run {args.run}.", file=out)
            return 0
        return _run(cfg, settings, args, out, answer_in)
    except shell.ShellError as exc:  # BakeoffError is one
        print(str(exc), file=out)
        return 2


def _price(spec: Any) -> str:  # noqa: ANN401 - a ModelSpec
    if spec.fake:
        return "free"
    if spec.price is None:
        return "price unknown (capped by tokens)"
    return (
        f"${spec.price.input:.2f} in / ${spec.price.output:.2f} out per million tokens"
    )


def _models(settings: Any, out: TextIO) -> int:  # noqa: ANN401 - Settings
    from gregtec.build import shell_bakeoff as bo
    from gregtec.llm import keys

    print("label\tprovider\tmodel\teffort\tprice\tkey", file=out)
    for spec in (*bo.FAKES.values(), *settings.models.values()):
        if spec.fake:
            key = "none needed"
        else:
            try:
                stored = keys.has_key(spec.provider)
            except Exception as exc:  # noqa: BLE001 - whatever the keyring raises
                key = f"keyring unavailable ({type(exc).__name__})"
            else:
                key = (
                    "stored"
                    if stored
                    else f"not stored (uv run gos key set {spec.provider})"
                )
        print(
            f"{spec.label}\t{spec.provider}\t{spec.model}\t{spec.effort or 'default'}\t"
            f"{_price(spec)}\t{key}",
            file=out,
        )
    print(
        f"# run cap: ${settings.cap_usd:.2f}, and {settings.cap_tokens:,} tokens for models "
        "without a price ([bakeoff] cap_usd, cap_tokens)",
        file=out,
    )
    return 0


def _run_line(r: dict[str, Any]) -> str:
    return (
        f"{r['id']}\t{r['status']}\t{r['cases']} cases x {r['models']} models\t"
        f"rated {r['rated']} of {r['rateable']}\t"
        f"{'models named' if r['revealed'] else 'blind'}"
    )


def _entry(e: dict[str, Any]) -> str:
    if not e["design"]:
        return f"{e['letter']}: no design"
    after = (
        f"{e['revise']['blocks']} blocks after the follow-up"
        if e["revise"]
        else "no follow-up version"
    )
    return f"{e['letter']}: {e['design']['blocks']} blocks, {after}"


def _show(view: dict[str, Any], out: TextIO) -> int:
    print(_run_line(view), file=out)
    for c in view["cases"]:
        print(
            f"{c['id']} ({c['theme']}, {c['line']} line, max {c['max_blocks']} blocks)",
            file=out,
        )
        print(f"  brief: {c['brief'] or '(the theme alone)'}", file=out)
        print(f"  follow-up: {c['followup']}", file=out)
        for e in c["entries"]:
            print(f"  {_entry(e)}", file=out)
        print(f"  your pick: {c['pick'] or 'not rated'}", file=out)
    return 0


def _rate(home: Path, args: argparse.Namespace, out: TextIO, answer_in: Answer) -> int:
    from gregtec.build import shell_bakeoff as bo

    if args.case is not None:
        if args.pick is None:
            print("Say the pick too: a letter, tie or bad.", file=out)
            return 2
        after = bo.rate(home, args.run, args.case, args.pick)
        left = bo.unrated(after)
        print(f"{args.case}: {args.pick}. {len(left)} case(s) left to rate.", file=out)
        return 0
    start = bo.load(home, args.run)
    view = bo.blind(start)
    todo = [c for c in view["cases"] if c["id"] in bo.unrated(start)]
    if not todo:
        print("Every case is rated.", file=out)
        return 0
    print(
        "Look at each case in the Build view's Bake-off panel first. For each case type a "
        f"letter, {bo.TIE}, or {bo.BAD} (none is good); Enter skips.",
        file=out,
    )
    for c in todo:
        print(f"{c['id']}: {c['brief'] or '(the theme alone)'}", file=out)
        print(f"  then: {c['followup']}", file=out)
        for e in c["entries"]:
            print(f"  {_entry(e)}", file=out)
        print("  pick: ", end="", file=out, flush=True)
        pick = answer_in().strip()
        if not pick:
            continue
        pick = pick.upper() if len(pick) == 1 else pick.casefold()
        try:
            bo.rate(home, args.run, c["id"], pick)
        except bo.BakeoffError as exc:
            print(f"  not recorded: {exc}", file=out)
    left = bo.unrated(bo.load(home, args.run))
    print(
        f"{len(left)} case(s) left to rate."
        if left
        else f"All rated. Now: gos shell bakeoff report {args.run}",
        file=out,
    )
    return 0


def _designers(models: Any, out: TextIO) -> dict[str, Any] | None:  # noqa: ANN401
    """Each model's designer; None (said) when a key is missing."""
    from gregtec.build import shell_fakes
    from gregtec.llm import designers as llm
    from gregtec.llm import keys

    out_map: dict[str, Any] = {}
    for m in models:
        if m.fake:
            out_map[m.label] = shell_fakes.FakeDesigner(m.model)
            continue
        try:
            key = keys.get_key(m.provider)
        except keys.MissingKeyError as exc:
            print(f"Not run: {exc}", file=out)
            return None
        except Exception as exc:  # noqa: BLE001 - whatever the keyring raises
            print(
                f"Not run: the keyring can't be read ({type(exc).__name__}).", file=out
            )
            return None
        if m.provider == "anthropic":
            out_map[m.label] = llm.AnthropicDesigner(
                llm.anthropic_client(key), m.model, m.effort
            )
        else:
            out_map[m.label] = llm.OpenAIDesigner(
                key, m.model, m.effort, url=llm.OPENAI_URL
            )
    return out_map


def _held(cfg: Any, store: Any, meter: Any, amount: float) -> str | None:  # noqa: ANN401
    """Why a paid run may not start now: the AI is off (D48's pause, his switch), or today's
    spend plus the most this run can spend would pass the day cap (D334)."""
    from gregtec.agents.no_ai import AiMode, CreditWatch
    from gregtec.ops.costs import CostWatch

    mode = AiMode(store)
    CreditWatch(meter, mode).check()
    if reasons := mode.reasons():
        return (
            f"the AI is off ({', '.join(reasons)}): nothing is spent. The fake models "
            "still run"
        )
    day = CostWatch.of(store, cfg).caps()["day"]
    if day["spent_usd"] + amount > day["cap_usd"]:
        return (
            f"today's spend ${day['spent_usd']:.2f} plus this run's ${amount:.2f} would pass "
            f"the day cap ${day['cap_usd']:.2f} ([budget] day_max_usd): lower --cap-usd, or "
            "run it another day"
        )
    return None


def _run(
    cfg: Any,  # noqa: ANN401 - Config
    settings: Any,  # noqa: ANN401 - Settings
    args: argparse.Namespace,
    out: TextIO,
    answer_in: Answer,
) -> int:
    from gregtec.build import shell_bakeoff as bo
    from gregtec.db import access
    from gregtec.llm.usage import UsageMeter
    from gregtec.store import Store
    from gregtec.vault.io import VaultIO

    home = cfg.gos_home
    labels = [x.strip() for x in args.models.split(",") if x.strip()]
    models = settings.pick(labels)
    cap_usd = settings.cap_usd if args.cap_usd is None else args.cap_usd
    cap_tokens = settings.cap_tokens if args.cap_tokens is None else args.cap_tokens
    if cap_usd <= 0 or cap_tokens <= 0:
        print("The caps must be above 0.", file=out)
        return 2
    try:
        cases = bo.load_cases(args.cases or bo.CASES)
    except OSError as exc:
        print(f"Can't read the cases: {exc}", file=out)
        return 2
    if args.limit is not None:
        cases = cases[: max(1, args.limit)]
    large = (
        [x.strip() for x in args.large.split(",") if x.strip()]
        if args.large
        else list(args.plans)
    )
    plans = {"small": list(args.plans), "large": large}
    db = access.db_path(home)
    if not db.is_file():
        print(f"{db} doesn't exist: run `gos db build` first", file=out)
        return 2
    conn = access.connect(db, readonly=True)
    store = None
    try:
        vault = VaultIO(home / "vault")
        prepared = bo.prepare(conn, home, vault, cases, plans)
        est = bo.estimate(prepared, models, cap_usd, cap_tokens)
        for line in est.lines:
            print(line, file=out)
        real = [m for m in models if not m.fake]
        meter = None
        amount = 0.0
        if real:
            store = Store(db)
            meter = UsageMeter(
                store, cfg.monthly_included_usd, cfg.credit_alerts_pct, cfg.cycle_day
            )
            priced = any(m.price is not None for m in real)
            amount = min(est.worst_usd, cap_usd) if priced else 0.0
            if why := _held(cfg, store, meter, amount):
                print(f"Not run: {why}.", file=out)
                return 1
        designers = _designers(models, out)
        if designers is None:
            return 1
        if real and not args.yes:
            print(
                f"Type yes to spend up to ${amount:.2f}: ", end="", file=out, flush=True
            )
            if answer_in().strip().casefold() != "yes":
                print("Not run.", file=out)
                return 1
        run = bo.create(
            home,
            prepared,
            models,
            plans,
            seed=args.seed,
            cap_usd=cap_usd,
            cap_tokens=cap_tokens,
        )
        print(f"Run {run['id']} (Ctrl+C stops it cleanly):", file=out)
        runner = bo.Runner(
            conn,
            home,
            vault,
            run,
            designers,
            meter=meter,
            say=lambda line: print(line, file=out, flush=True),
        )
        run = runner.execute(prepared)
    finally:
        conn.close()
        if store is not None:
            store.close()
    spent = run["spent"]
    print(
        f"Run {run['id']}: {run['status']}"
        + (f" ({run['why']})" if run["why"] else "")
        + f". {spent['calls']} calls, ${spent['usd']:.2f}"
        + (
            f", {spent['unpriced_tokens']:,} unpriced tokens"
            if spent["unpriced_tokens"]
            else ""
        )
        + ".",
        file=out,
    )
    if broke := bo.broken_off(run):
        print(broke, file=out)
    print(
        f"Next: look at it in the Build view's Bake-off panel, rate it there or with "
        f"`gos shell bakeoff rate {run['id']}`, then `gos shell bakeoff report {run['id']}`. "
        f"To remove it: `gos shell bakeoff delete {run['id']}`.",
        file=out,
    )
    return 0 if run["status"] == bo.DONE else 1
