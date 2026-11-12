"""``gos watch run`` (WO-6.1): the game watchers in the foreground, as the supervisor runs them."""

from __future__ import annotations

import argparse

from gregtec.watchers import runner

COMMANDS = ("watch",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    watch = commands.add_parser(
        "watch",
        help="the game watchers (logs, saves, pause): run them in the foreground",
    )
    sub = watch.add_subparsers(dest="watch_command", required=True)
    run = sub.add_parser(
        "run",
        help="watch Play's (or Lab's) logs and save files and send events to the core",
        description=(
            "Reads the instance's logs and world through safe_open (never holding a handle) "
            "and sends typed events to the core; the supervisor runs it while the instance "
            "plays (D259). Ctrl+C stops it."
        ),
    )
    runner.add_arguments(run)


def run(args: argparse.Namespace) -> int:
    return runner.run(args)
