"""The rehearsal's Discord bot (WO-11.3): the real bot (:class:`gregtec.discordbot.handlers.Bot`,
its one door, its job delivery) and the real alert desk (:class:`gregtec.discordbot.alerts.
AlertDesk`: alert DMs, credit buttons, "GOS offline"), talking to the real core over HTTP with
the keyring's ``token-discord`` (:class:`gregtec.discordbot.core_api.HttpCore`), behind a fake
gateway instead of discord.py's: no Discord, no network beyond loopback (D158 (4)).

The fake gateway is :class:`rfakes.Mailbox` ``discord``: ``{"op": "command", "name": "ask",
"options": {...}, "user": <id>}`` and ``{"op": "button", "custom": "gos:...", "user": <id>}``
come in as interactions; every reply, follow-up and DM (its text and button ids) goes to
``discord/out.jsonl``. Away/present for the DMs is the control folder's ``idle_s``.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import rfakes

from gregtec.discordbot import settings as bot_settings
from gregtec.discordbot.alerts import (
    STATE_FILE,
    AlertDesk,
    status_token,
    supervisor_says_stopped,
)
from gregtec.discordbot.core_api import HttpCore
from gregtec.discordbot.handlers import Bot, Message
from gregtec.llm import keys

EXIT_SETUP = 2
_ids = itertools.count(1_400_000_000_000_000_000)


def shown(msg: Message) -> dict[str, Any]:
    return {
        "text": msg.text,
        "buttons": [b.custom_id for b in msg.buttons],
        "files": [name for name, _ in msg.files],
    }


class Inter:
    """One interaction through the fake gateway."""

    def __init__(
        self, box: rfakes.Mailbox, user_id: int, kind: str, ref: object
    ) -> None:
        self.box = box
        self.id = str(next(_ids))
        self.user_id = user_id
        self.kind = kind
        self.ref = ref

    async def ack(self) -> None:
        self.box.record({"ref": self.ref, "ack": True, "t": time.time()})

    async def send(self, msg: Message) -> None:
        self.box.record({"ref": self.ref, **shown(msg), "t": time.time()})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_discord.py")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--supervised", action="store_true")
    args = parser.parse_args(argv)
    cfg, s = bot_settings.load(args.config)
    try:
        token = keys.get_key(bot_settings.CORE_TOKEN_KEY)
    except keys.MissingKeyError:
        print("no core token for the bot", file=sys.stderr)
        return EXIT_SETUP
    control = rfakes.control_dir()
    box = rfakes.Mailbox(control, "discord")

    async def dm(msg: Message) -> None:
        box.record({"dm": True, **shown(msg), "t": time.time()})

    bot = Bot(s, HttpCore(cfg.core_port, token), dm=dm)
    desk = AlertDesk(
        bot,
        cfg.notify,
        idle=rfakes.FileIdle(control),
        state_file=cfg.run_dir / STATE_FILE,
        core_stopped=supervisor_says_stopped(cfg.supervisor_port, status_token()),
    )
    bot.credit = desk.credit_button

    async def gateway(stop: asyncio.Event) -> None:
        while not stop.is_set():
            for cmd in box.commands():
                user = int(cmd.get("user") or s.owner_id)
                if cmd.get("op") == "command":
                    it = Inter(box, user, "command", cmd.get("ref"))
                    bot.spawn(
                        bot.interaction(
                            it, str(cmd.get("name")), cmd.get("options") or {}
                        )
                    )
                elif cmd.get("op") == "button":
                    it = Inter(box, user, "component", cmd.get("ref"))
                    bot.spawn(bot.interaction(it, str(cmd.get("custom"))))
            await asyncio.sleep(0.1)

    async def run() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        if args.supervised:
            threading.Thread(
                target=_watch_stdin,
                args=(lambda: loop.call_soon_threadsafe(stop.set),),
                daemon=True,
            ).start()
        tasks = [
            asyncio.ensure_future(bot.deliver_forever()),
            asyncio.ensure_future(desk.run_forever()),
            asyncio.ensure_future(gateway(stop)),
        ]
        box.record({"started": True, "t": time.time()})
        await stop.wait()
        for t in tasks:
            t.cancel()
        return 0

    return asyncio.run(run())


def _watch_stdin(stop: object) -> None:
    try:
        for line in sys.stdin.buffer:
            if line.strip() == b"stop":
                break
    except (OSError, ValueError, AttributeError):
        pass
    stop()  # type: ignore[operator]


if __name__ == "__main__":
    sys.exit(main())
