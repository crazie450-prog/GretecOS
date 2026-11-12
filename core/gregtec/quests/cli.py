"""The quest guide's commands (WO-6.4): ``gos quests summaries`` and ``gos eval quest-guide``.
Kept light: every ``gos`` command builds its parser, so the eval (which imports the Anthropic
SDK) is imported only when it runs."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

SUMMARIES = "summaries"
EVAL = "quest-guide"
CONFIG_HELP = (
    "gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
    "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults."
)


def add_parsers(
    quest_cmds: argparse._SubParsersAction[argparse.ArgumentParser],
    eval_cmds: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add ``summaries`` to ``gos quests`` and ``quest-guide`` to ``gos eval``."""
    s = quest_cmds.add_parser(
        SUMMARIES,
        help="write the questline summaries into the vault (quests/qs-<line>.md)",
        description=(
            "One note per quest line the book shows, from the loaded quests: progress counts "
            "and the quests he can start now, never a quest past his frontier. Only notes "
            "whose content changed are written. `gos quests load` runs this too."
        ),
    )
    s.add_argument("--config", type=Path, default=None, help=CONFIG_HELP)
    e = eval_cmds.add_parser(
        EVAL,
        help="the quest guide's 20 questions on its fixture pack (WO-6.4)",
        description=(
            "Builds the self-authored quest pack (tests/evals/quest_pack.py) in a temporary "
            "GOS_HOME, asks every row of tests/evals/quest_guide.tsv through the router and "
            "scores each answer against the quest DB: the q: ids and items it must name, and "
            "no spoiler of a quest past the frontier beyond the level the question asked for. "
            "Uses the stored Anthropic key unless --fake."
        ),
    )
    e.add_argument(
        "--fake",
        action="store_true",
        help="an oracle model that asks for full spoilers every time: no key, no cost",
    )
    e.add_argument("--questions", type=Path, default=None, help="another question file")
    e.add_argument("--limit", type=int, default=None, help="only the first N rows")
    e.add_argument("--config", type=Path, default=None, help=CONFIG_HELP)


def run_summaries(args: argparse.Namespace, out: TextIO) -> int:
    from gregtec.quests import summaries

    return summaries.main(args.config, out)


def run_eval(args: argparse.Namespace, out: TextIO) -> int:
    from gregtec.quests import questeval

    return questeval.main(args.config, args.questions, args.fake, args.limit, out)
