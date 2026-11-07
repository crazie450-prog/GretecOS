"""``gos eval recipe-lookup``'s arguments (WO-2.7). Kept apart from :mod:`.recipe_eval`, which
imports the Anthropic and Agent SDKs: every ``gos`` command builds its parser, so this module
stays light and the eval is imported only when it runs."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

COMMAND = "recipe-lookup"


def add_parsers(evals: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``recipe-lookup`` to ``gos eval``'s subcommands."""
    p = evals.add_parser(
        COMMAND,
        help="the recipe-lookup skill's 50 questions, scored against the DB (WO-2.7)",
        description=(
            "Asks every row of tests/evals/recipe_lookup.tsv through the router (instant, "
            "fast or deep lane as routed) and scores it against GOS_HOME's DB: the ids it "
            "cites and the numbers it states must be the DB rows' (target 95%% correct), and "
            "no number may be uncited (target 0). Uses the stored Anthropic key unless "
            "--fake."
        ),
    )
    p.add_argument(
        "--fake",
        action="store_true",
        help="oracle models for both lanes (the real CLI for the deep lane): no key, no cost",
    )
    p.add_argument("--questions", type=Path, default=None, help="another question file")
    p.add_argument("--limit", type=int, default=None, help="only the first N rows")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="also write the per-row report (TSV) here",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
        "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
    )


def run(args: argparse.Namespace, out: TextIO) -> int:
    from gregtec.agents import recipe_eval

    return recipe_eval.main(
        args.config, args.questions, args.fake, args.limit, args.out, out
    )
