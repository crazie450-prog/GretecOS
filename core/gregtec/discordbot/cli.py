"""``gos discord run|check`` and ``python -m gregtec.discordbot`` (WO-9.1).

- ``run [--supervised]``: the bot, until stopped. The supervisor starts it at login with the core
  (plan 4.2, D77); ``--supervised`` stops on a ``stop`` line or the end of stdin (D115). Exit
  :data:`EXIT_SETUP` (2) when it can't run here: no ``[discord] owner_id``, no bot token, no
  core token, or Discord refused the token; the supervisor then leaves it stopped, with
  ``logs\\discord.log`` saying why.
- ``check``: the settings and whether both secrets are stored; contacts nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from gregtec import applog
from gregtec.discordbot import settings as bot_settings
from gregtec.llm import keys

log = logging.getLogger("gregtec.discordbot")

COMMANDS = ("discord",)
EXIT_SETUP = 2  # gregtec.supervisor.EXIT_SETUP: left stopped


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser("discord", help="the Discord bot (WO-9.1)")
    sub = p.add_subparsers(dest="discord_command", required=True)
    for name, text in (
        ("run", "run the bot until stopped"),
        ("check", "check [discord] in gos.toml and the stored secrets"),
    ):
        q = sub.add_parser(name, help=text)
        q.add_argument(
            "--config", type=Path, default=None, help="gos.toml (default: GOS_HOME's)"
        )
        if name == "run":
            q.add_argument("--supervised", action="store_true", help=argparse.SUPPRESS)


def problems() -> list[str]:
    """Secrets the bot needs and doesn't have (names only, never values)."""
    out = []
    if not keys.has_key(keys.DISCORD):
        out.append("no Discord bot token: gos key set discord")
    if not keys.has_key(bot_settings.CORE_TOKEN_KEY):
        out.append("no core token for the bot: gos token new discord")
    return out


def run(args: argparse.Namespace, out: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    try:
        cfg, s = bot_settings.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't start the Discord bot: {exc}", file=out)
        if args.discord_command == "run":
            _log_setup_problem(args.config, str(exc))
        return EXIT_SETUP
    missing = problems()
    if args.discord_command == "check":
        print(
            f"owner_id: set; guild_id: {'set' if s.guild_id else 'not set'}", file=out
        )
        print(f"phone links: {s.dashboard_url or 'off (no tailnet name)'}", file=out)
        for p in missing:
            print(f"problem: {p}", file=out)
        return 1 if missing else 0
    applog.setup("discord", cfg.logs_dir)
    if missing:
        for p in missing:
            log.error("can't start", extra={"why": p})
        return EXIT_SETUP
    return _run_bot(cfg.core_port, s, supervised=args.supervised)


def _log_setup_problem(config_path: Path | None, why: str) -> None:
    try:
        from gregtec import config

        applog.setup("discord", config.load(config_path).logs_dir)
        log.error("can't start", extra={"why": why[:300]})
    except (OSError, ValueError):
        pass


def _run_bot(port: int, s: bot_settings.BotSettings, *, supervised: bool) -> int:
    import discord

    from gregtec.discordbot.adapter import GosClient
    from gregtec.discordbot.core_api import HttpCore

    client = GosClient(s, HttpCore(port, keys.get_key(bot_settings.CORE_TOKEN_KEY)))

    async def main() -> int:
        if supervised:
            loop = asyncio.get_running_loop()
            _watch_stdin(lambda: loop.call_soon_threadsafe(_close, client))
        try:
            async with client:
                await client.start(keys.get_key(keys.DISCORD))
        except discord.LoginFailure:
            log.error("can't start", extra={"why": "Discord refused the bot token"})
            return EXIT_SETUP
        return 0

    code = asyncio.run(main())
    log.info("discord bot stopped", extra={"exit_code": code})
    return code


def _close(client: object) -> None:
    asyncio.ensure_future(client.close())  # type: ignore[attr-defined]


def _watch_stdin(stop: Callable[[], object]) -> None:
    def watch() -> None:
        try:
            for line in sys.stdin.buffer:
                if line.strip() == b"stop":
                    break
        except (OSError, ValueError, AttributeError):
            pass
        log.info("supervisor asked the bot to stop")
        stop()

    threading.Thread(target=watch, name="gos-discord-stdin", daemon=True).start()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gregtec.discordbot")
    commands = parser.add_subparsers(dest="command", required=True)
    add_parsers(commands)
    args = parser.parse_args(["discord", *(sys.argv[1:] if argv is None else argv)])
    return run(args)
