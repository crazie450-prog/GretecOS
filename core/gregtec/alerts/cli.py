"""``gos alerts routed|test`` (WO-9.2): what the alert router sees, and a test DM.

- ``routed``: the moment (away, quiet hours, Discord's ``/alerts``, voice and game connected),
  each active alert and where it went or why not yet (``GET /api/alerts/routed``).
- ``test``: one test DM through the router and the bot, whatever his presence
  (``POST /api/alerts/test``): the runbook's first real-Discord step.

Both talk to the running core with its admin token (``GOS_HOME\\run\\core.json``, Clayton only).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config, control

COMMANDS = ("alerts",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser("alerts", help="alert routing: what went where; a test DM")
    sub = p.add_subparsers(dest="alerts_command", required=True)
    for name, text in (
        ("routed", "the active alerts and where each went"),
        ("test", "send one test DM through the bot"),
    ):
        q = sub.add_parser(name, help=text)
        q.add_argument(
            "--config", type=Path, default=None, help="gos.toml (default: GOS_HOME's)"
        )


def run(args: argparse.Namespace, out: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    core = control.running(cfg.run_dir / control.CORE_FILE, control.CORE_SERVICE)
    if core is None:
        print("The core isn't running: start it (gos serve).", file=out)
        return 1
    method, path = (
        ("GET", "/api/alerts/routed")
        if args.alerts_command == "routed"
        else ("POST", "/api/alerts/test")
    )
    try:
        result = control.call(core["port"], method, path, token=core["token"])
    except control.ControlError as exc:
        print(str(exc), file=out)
        return 1
    if args.alerts_command == "test":
        print(
            "Test DM queued: the Discord bot posts it within a few seconds.", file=out
        )
        return 0
    print(json.dumps(result, indent=2), file=out)
    return 0
