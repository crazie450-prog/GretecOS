"""``gos eval design-cost``'s arguments (WO-3.7, [QA:A4], D253). Kept apart from
:mod:`.design_cost`, which imports the Agent SDK: every ``gos`` command builds its parser."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

COMMAND = "design-cost"


def add_parsers(evals: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``design-cost`` to ``gos eval``'s subcommands."""
    p = evals.add_parser(
        COMMAND,
        help="the deep lane's cost on 10 design tasks, and task_max_usd from it (WO-3.7)",
        description=(
            "Hands every row of tests/evals/design_tasks.tsv to the deep lane (one fresh "
            "session each, the line-designer skill and its tools), prints the cost per task "
            "(the SDK's and the lane's own estimate, which drives the cap) and the "
            "[budget] task_max_usd the measurements give. A task that reaches the measuring "
            "cap is stopped, never approved: a run costs at most 10 caps. Uses the stored "
            "Anthropic key and records llm_usage unless --fake (the real CLI against a local "
            "model that replays recorded token counts: no key, no cost)."
        ),
    )
    p.add_argument(
        "--fake",
        action="store_true",
        help="replay tests/evals/design_tokens.tsv's token counts: no key, no cost",
    )
    p.add_argument("--tasks", type=Path, default=None, help="another task file")
    p.add_argument(
        "--recording", type=Path, default=None, help="with --fake: another token file"
    )
    p.add_argument("--limit", type=int, default=None, help="only the first N rows")
    p.add_argument(
        "--measure-cap",
        type=float,
        default=None,
        metavar="USD",
        help="the per-task cap while measuring (default: [budget] task_max_usd)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where to write the table (TSV). Default: "
        "GOS_HOME/data/reports/design-costs-<date>.tsv",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="write the measured task_max_usd into gos.toml's [budget] (a real run only)",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="gos.toml to read (and, with --apply, to change). Default: "
        "$GOS_HOME/config/gos.toml, else ~/GregtecOS/config/gos.toml.",
    )


def run(args: argparse.Namespace, out: TextIO) -> int:
    from gregtec.agents import design_cost

    return design_cost.main(
        args.config,
        tasks=args.tasks,
        recording=args.recording,
        fake=args.fake,
        limit=args.limit,
        measure_cap=args.measure_cap,
        table_path=args.out,
        apply=args.apply,
        out=out,
    )
