"""``gos eval advisor`` (WO-6.5). Kept light: every ``gos`` command builds its parser, so the eval
(which imports the deep lane) is imported only when it runs."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

COMMAND = "advisor"
CONFIG_HELP = (
    "gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
    "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults."
)


def add_parsers(eval_cmds: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    e = eval_cmds.add_parser(
        COMMAND,
        help="the advisor's three scenarios, end to end through the deep lane (WO-6.5)",
        description=(
            "Builds each scenario of tests/evals/advisor_scenarios.py (early, trouble, "
            "stocked) in a temporary GOS_HOME, asks 'What should I do next?' where the router "
            "sends it, and checks the answer by code: GOS's steps in GOS's order, every "
            "number cited and every id real, nothing past the quest frontier. The real run "
            "uses the stored Anthropic key and writes a ratings sheet for Clayton "
            "(data/reports/advisor-<date>.tsv)."
        ),
    )
    e.add_argument(
        "--fake",
        action="store_true",
        help="an adversarial oracle model behind the real CLI: no key, no cost",
    )
    e.add_argument("--limit", type=int, default=None, help="only the first N scenarios")
    e.add_argument("--config", type=Path, default=None, help=CONFIG_HELP)


def run(args: argparse.Namespace, out: TextIO) -> int:
    from gregtec.advisor import evaluate

    return evaluate.main(args.config, args.fake, out, limit=args.limit)
