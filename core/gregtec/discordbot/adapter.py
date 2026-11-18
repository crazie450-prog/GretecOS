"""discord.py glue for :class:`gregtec.discordbot.handlers.Bot` (WO-9.1). Only this module
imports discord.py (MIT).

- **Intents:** none. No message content, members or presences: the bot reads no messages,
  only interactions, which Discord sends without any intent.
- **Gates** ([QA:V9]): the command tree's ``interaction_check`` drops every command and
  autocomplete that isn't from ``[discord] owner_id`` before any callback runs, and each
  callback goes through :meth:`Bot.interaction`, which checks again. Every component (button,
  select) and modal goes to :meth:`Bot.interaction` from ``on_interaction``; the buttons' own
  view (:class:`ButtonsView`) never runs a callback (its check always says no), so there is one
  door. Nothing is sent to anyone else.
- **Mentions** are off on every message: text from the core is data and can't ping anyone.
- **Commands** sync to ``[discord] guild_id`` (at once) when set, else globally, with
  ``default_permissions`` set to administrators, so in his server only he sees them.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import discord
from discord import app_commands

from gregtec import presence
from gregtec.config import Config
from gregtec.discordbot import texts
from gregtec.discordbot.alerts import (
    STATE_FILE,
    AlertDesk,
    status_token,
    supervisor_says_stopped,
)
from gregtec.discordbot.core_api import CoreApi
from gregtec.discordbot.handlers import (
    ALERT_MODES,
    POWER_MODES,
    Bot,
    Message,
)
from gregtec.discordbot.settings import BotSettings

log = logging.getLogger("gregtec.discordbot")

NO_MENTIONS = discord.AllowedMentions.none()
VIEW_TIMEOUT_S = 180.0


def kind_of(inter: discord.Interaction) -> str:
    t = inter.type
    if t == discord.InteractionType.application_command:
        return "command"
    if t == discord.InteractionType.autocomplete:
        return "autocomplete"
    if t == discord.InteractionType.modal_submit:
        return "modal"
    if t == discord.InteractionType.component:
        data: Any = inter.data or {}
        return "component" if data.get("component_type") == 2 else "select"
    return "other"


class DiscordInteraction:
    """:class:`gregtec.discordbot.handlers.Interaction` over a discord.py interaction."""

    def __init__(self, inter: discord.Interaction) -> None:
        self.inter = inter
        self.id = str(inter.id)
        self.user_id = inter.user.id
        self.kind = kind_of(inter)

    async def ack(self) -> None:
        if self.inter.response.is_done():
            return
        if self.kind == "component":
            await self.inter.response.edit_message(view=None)
        else:
            await self.inter.response.defer(thinking=True)

    async def send(self, msg: Message) -> None:
        await self.inter.followup.send(**payload(msg))


class ButtonsView(discord.ui.View):
    """Confirm and Cancel; presses are handled by ``on_interaction``, never here. It times
    out (leaving discord.py's view store) after the action desk's 60 s expiry: a later press
    still reaches ``on_interaction``, and the desk answers that it expired."""

    def __init__(self, msg: Message) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        for b in msg.buttons:
            self.add_item(
                discord.ui.Button(
                    label=b.label,
                    custom_id=b.custom_id,
                    style=discord.ButtonStyle.danger
                    if b.danger
                    else discord.ButtonStyle.primary,
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return False


def payload(msg: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"content": msg.text, "allowed_mentions": NO_MENTIONS}
    if msg.files:
        out["files"] = [
            discord.File(io.BytesIO(data), filename=name) for name, data in msg.files
        ]
    if msg.buttons:
        out["view"] = ButtonsView(msg)
    return out


class GateTree(app_commands.CommandTree):
    def __init__(self, client: discord.Client, owner_id: int) -> None:
        super().__init__(client)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return interaction.user.id == self.owner_id

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError, /
    ) -> None:
        # A stranger's command fails the gate and lands here too: tell_failed stays silent.
        log.info("command error", extra={"error": type(error).__name__})
        await tell_failed(interaction, self.owner_id)


async def tell_failed(inter: discord.Interaction, owner_id: int) -> None:
    """Clayton's interaction raised: a generic reply, so it doesn't sit on "thinking…" until
    Discord gives up. Anyone else gets nothing ([QA:V9])."""
    if inter.user.id != owner_id:
        return
    try:
        if inter.response.is_done():
            await inter.followup.send(
                content=texts.FAILED, allowed_mentions=NO_MENTIONS
            )
        else:
            await inter.response.send_message(
                content=texts.FAILED, allowed_mentions=NO_MENTIONS
            )
    except Exception:
        log.info("failure notice not sent", exc_info=True)


class GosClient(discord.Client):
    def __init__(
        self, settings: BotSettings, core: CoreApi, cfg: Config | None = None
    ) -> None:
        super().__init__(intents=discord.Intents.none(), allowed_mentions=NO_MENTIONS)
        self.settings = settings
        self.bot = Bot(settings, core, dm=self.dm)
        self.tree = GateTree(self, settings.owner_id)
        add_commands(self.tree, self.bot)
        self._deliver: asyncio.Task[None] | None = None
        # Alert DMs, the credit buttons and "GOS offline" (WO-9.2).
        self.desk: AlertDesk | None = None
        if cfg is not None:
            self.desk = AlertDesk(
                self.bot,
                cfg.notify,
                idle=presence.system_idle(),
                state_file=cfg.run_dir / STATE_FILE,
                core_stopped=supervisor_says_stopped(
                    cfg.supervisor_port, status_token()
                ),
            )
            self.bot.credit = self.desk.credit_button
        self._alerts: asyncio.Task[None] | None = None

    async def dm(self, msg: Message) -> None:
        user = await self.fetch_user(self.settings.owner_id)
        await user.send(**payload(msg))

    async def setup_hook(self) -> None:
        if self.settings.guild_id is not None:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self._deliver = asyncio.ensure_future(self.bot.deliver_forever())
        if self.desk is not None:
            self._alerts = asyncio.ensure_future(self.desk.run_forever())
        log.info("discord bot ready")

    async def on_interaction(self, inter: discord.Interaction) -> None:
        if inter.type not in (
            discord.InteractionType.component,
            discord.InteractionType.modal_submit,
        ):
            return  # commands go through the tree (and its gate)
        data: Any = inter.data or {}
        try:
            await self.bot.interaction(
                DiscordInteraction(inter), str(data.get("custom_id"))
            )
        except Exception as exc:  # noqa: BLE001 (any bug: logged, and Clayton is told)
            log.info("interaction error", extra={"error": type(exc).__name__})
            await tell_failed(inter, self.settings.owner_id)

    async def close(self) -> None:
        if self._deliver is not None:
            self._deliver.cancel()
        if self._alerts is not None:
            self._alerts.cancel()
        await super().close()


def add_commands(tree: app_commands.CommandTree, bot: Bot) -> None:
    """The six commands of plan 7.12; each hands its options to the bot's one door."""
    admin = app_commands.default_permissions(administrator=True)

    async def go(
        inter: discord.Interaction, name: str, options: dict[str, Any]
    ) -> None:
        await bot.interaction(DiscordInteraction(inter), name, options)

    @app_commands.command(name="ask", description="Ask GOS (fast or deep lane)")
    @app_commands.describe(text="your question")
    @admin
    async def ask(inter: discord.Interaction, text: str) -> None:
        await go(inter, "ask", {"text": text})

    @app_commands.command(
        name="plan", description="Design a production line (queued; PNG + BOM)"
    )
    @app_commands.describe(
        target="item or fluid",
        amount="a rate (2/s, 30/min) or a quantity (64)",
        within="for a quantity: how long (2h)",
        tier="highest tier (LV..MAX); default: yours",
    )
    @admin
    async def plan(
        inter: discord.Interaction,
        target: str,
        amount: str,
        within: str | None = None,
        tier: str | None = None,
    ) -> None:
        await go(
            inter,
            "plan",
            {"target": target, "amount": amount, "within": within, "tier": tier},
        )

    @app_commands.command(name="power", description="Design a power line (queued)")
    @app_commands.describe(mode="objective", tier="highest tier (LV..MAX)")
    @app_commands.choices(
        mode=[app_commands.Choice(name=m, value=m) for m in POWER_MODES]
    )
    @admin
    async def power(
        inter: discord.Interaction,
        mode: app_commands.Choice[str],
        tier: str | None = None,
    ) -> None:
        await go(inter, "power", {"mode": mode.value, "tier": tier})

    @app_commands.command(name="status", description="Telemetry and the EU ledger")
    @admin
    async def status(inter: discord.Interaction) -> None:
        await go(inter, "status", {})

    @app_commands.command(name="alerts", description="Discord alerts on, off or quiet")
    @app_commands.describe(mode="on, off or quiet", hours="quiet for 1-24 hours")
    @app_commands.choices(
        mode=[app_commands.Choice(name=m, value=m) for m in ALERT_MODES]
    )
    @admin
    async def alerts(
        inter: discord.Interaction,
        mode: app_commands.Choice[str],
        hours: app_commands.Range[int, 1, 24] | None = None,
    ) -> None:
        await go(inter, "alerts", {"mode": mode.value, "hours": hours})

    @app_commands.command(name="persona", description="List personas, or switch")
    @app_commands.describe(name="a persona's name, or random")
    @admin
    async def persona(inter: discord.Interaction, name: str | None = None) -> None:
        await go(inter, "persona", {"name": name})

    for command in (ask, plan, power, status, alerts, persona):
        tree.add_command(command)
