"""``gos eval hover``'s arguments (WO-8.4, D362). Kept apart from :mod:`.hover_eval`, which
imports the lanes: every ``gos`` command builds its parser, so this module stays light."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

COMMAND = "hover"


def add_parsers(evals: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``hover`` to ``gos eval``'s subcommands."""
    p = evals.add_parser(
        COMMAND,
        help="plan 9's 10 hover-item questions, scored against the DB (WO-8.4)",
        description=(
            "Asks every row of tests/evals/hover_items.tsv through the router with the row's "
            "item as GregtecLink's hovered item (registry name, meta, NBT) and scores the "
            "reply against GOS_HOME's DB: it must be about that exact item, name it, cite "
            "rows that make or take it and state no uncited number. A case whose item the DB "
            "lacks is reported 'not in this DB' and fails the run. --fake answers with an "
            "oracle model (no key, no cost); without it the stored key is used, the "
            "worst-case cost is printed first and the run waits for yes."
        ),
    )
    p.add_argument(
        "--fake",
        action="store_true",
        help="an oracle fast-lane model that reads only the looked-up rows: no key, no cost",
    )
    p.add_argument("--cases", type=Path, default=None, help="another case file")
    p.add_argument("--limit", type=int, default=None, help="only the first N rows")
    p.add_argument(
        "--yes",
        action="store_true",
        help="spend without asking (the estimate is still printed and the caps still hold)",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
        "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
    )


def run(args: argparse.Namespace, out: TextIO) -> int:
    from gregtec.agents import hover_eval

    return hover_eval.main(
        args.config, args.cases, args.fake, args.yes, args.limit, out
    )
