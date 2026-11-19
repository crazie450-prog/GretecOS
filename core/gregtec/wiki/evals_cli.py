"""``gos eval wiki``'s arguments (WO-8.4, D363). Kept apart from :mod:`.evals`, which imports
the lanes: every ``gos`` command builds its parser, so this module stays light."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

COMMAND = "wiki"


def add_parsers(evals: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``wiki`` to ``gos eval``'s subcommands."""
    p = evals.add_parser(
        COMMAND,
        help="plan 9's wiki questions, scored against your wiki cache (WO-8.4)",
        description=(
            "Asks every case of $GOS_HOME/data/evals/wiki_questions.tsv as a deep-lane task "
            "and scores the answer against the wiki cache: it must cite each case page by "
            "its w:<Title>@<revid> id at the cached revision, say the case's facts, say none "
            "of its absent phrases, state no number the cited pages don't hold, and carry "
            "the wiki's attribution line. A case whose page changed since it was confirmed "
            "isn't checked (and fails). --make-cases --page TITLE writes cases from your "
            "own crawl, each one confirmed by you. --fake answers with the researcher's "
            "tools and no model."
        ),
    )
    p.add_argument(
        "--fake",
        action="store_true",
        help="answer with the wiki tools and no model: no key, no cost",
    )
    p.add_argument(
        "--cases",
        type=Path,
        default=None,
        help="another case file (default $GOS_HOME/data/evals/wiki_questions.tsv)",
    )
    p.add_argument("--limit", type=int, default=None, help="only the first N cases")
    p.add_argument(
        "--make-cases",
        action="store_true",
        help="propose a case for each --page from your wiki cache and keep the ones you confirm",
    )
    p.add_argument(
        "--page",
        action="append",
        default=[],
        metavar="TITLE",
        help="a wiki page to make a case from (with --make-cases; repeat it)",
    )
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
    from gregtec.wiki import evals

    return evals.main(
        args.config,
        args.cases,
        args.fake,
        args.yes,
        args.page,
        args.make_cases,
        out,
        limit=args.limit,
    )
