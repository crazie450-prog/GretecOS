"""``gos voice ...`` (WO-5.1, D179): hands the command line to the voice client's own.

The voice client is this workspace's ``voice/`` package (``gregtec_voice``, the ``voice``
dependency group), so the core imports it only when ``gos voice`` runs.
"""

from __future__ import annotations

import argparse
import sys

COMMANDS = ("voice",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    voice = commands.add_parser(
        "voice",
        help="the voice client: run, test, keys, devices, bench, stt, record, eval",
        add_help=False,
        prefix_chars="+",  # every argument, --help included, goes on to gos voice's parser
    )
    voice.add_argument("voice_args", nargs=argparse.REMAINDER)


def run(args: argparse.Namespace) -> int:
    try:
        from gregtec_voice import cli
    except ModuleNotFoundError as exc:
        if exc.name is None or not exc.name.startswith("gregtec_voice"):
            raise
        print(
            "The voice client isn't installed here: run uv sync (it is in the default "
            "dependency groups).",
            file=sys.stderr,
        )
        return 2
    return cli.main(args.voice_args)
