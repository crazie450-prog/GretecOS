"""``gos eval screen``'s arguments (WO-8.2). Kept apart from :mod:`.eval`, which imports the
Anthropic SDK: every ``gos`` command builds its parser, so this module stays light."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

COMMAND = "screen"


def add_parsers(evals: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``screen`` to ``gos eval``'s subcommands."""
    p = evals.add_parser(
        COMMAND,
        help="screen reading's 5 screenshot cases, scored (WO-8.2)",
        description=(
            "Sends each case of tests/evals/screen_cases.tsv to the screen lane as one frame "
            "and scores the reply: the item named, the ids cited, numbers checked, no decoy "
            "or injected number said, no tool outside the lane's set. --fake: synthetic "
            "frames and a fake vision model (no key, no cost). Otherwise the vision model "
            "with the stored key, on --images DIR/<id>.png (Lab screenshots) or the "
            "synthetic frames: the worst-case cost is printed and the run waits for 'yes'."
        ),
    )
    p.add_argument("--fake", action="store_true", help="the fake vision model")
    p.add_argument(
        "--images",
        type=Path,
        default=None,
        help="a folder of <case id>.png screenshots (the real model only)",
    )
    p.add_argument("--yes", action="store_true", help="don't ask before spending")
    p.add_argument("--cases", type=Path, default=None, help="another case file")
    p.add_argument("--limit", type=int, default=None, help="only the first N cases")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
        "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
    )


def run(args: argparse.Namespace, out: TextIO) -> int:
    from gregtec.screen import eval as screen_eval

    return screen_eval.main(
        args.config, args.cases, args.fake, args.images, args.yes, args.limit, out
    )
