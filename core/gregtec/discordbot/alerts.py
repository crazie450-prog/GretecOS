"""Alert DMs from the bot (WO-9.2, plan 7.12 "Alerts", 4.2; D46, D48, D76, D298-D300).

:class:`AlertDesk` runs in the bot every :data:`POLL_S`:

- **The outbox.** The core's alert router queues each DM (:mod:`gregtec.alerts.router`); the
  desk lists the undelivered ones (``GET /api/alerts/outbox``), DMs each to ``owner_id``, then
  marks it (``POST /api/alerts/outbox/{id}/delivered``). At least once, as WO-9.1's jobs (D295):
  a bot that dies between the post and the mark posts that DM again, and nothing else does.
- **Credit buttons** (D48): ``gos:credit:<choice>:<cycle>``. They reach the bot's one door like
  every interaction, so anyone but Clayton is dropped before anything happens ([QA:V9]); his
  press goes through the core's own budget control, ``POST /api/credits/decision`` as the client
  ``discord`` (D165), never around it. A button names its cycle, so an old DM can't answer a later
  cycle's alert. **Stop** asks once more ("Yes, stop GOS"), as the dashboard's banner does.
- **"GOS offline"** (D76, plan 4.2) can't come from the core, since it is the core that is down:
  the desk sends it. The bot is its own process under the supervisor (D296), so it outlives a
  crashed or hung core. When the core hasn't answered for ``[notify] offline_after_s`` (60 s) and
  Clayton is away (no input for 10 minutes, :mod:`gregtec.presence`), it DMs once per outage,
  whichever came second; not when the supervisor says the core was stopped on purpose
  (``gos stop``, D48's Stop: state ``stopped``). ``/alerts`` off or quiet and quiet hours apply as
  for the core's DMs (the last ``/alerts`` state the core gave is kept). When the core answers
  again after such a DM, "GOS is back" follows, under the same policy (so ``/alerts off``, quiet
  hours and his being back at the PC silence it). The outage is kept in
  ``GOS_HOME\\run\\discord-alerts.json``, so a bot restart doesn't send it twice; an outage
  that wasn't DMed yet starts again when the bot starts, so one from before a deliberate stop
  is never counted from days ago.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from gregtec import control
from gregtec.alerts import policy as pol
from gregtec.discordbot import texts
from gregtec.discordbot.core_api import CoreError
from gregtec.discordbot.handlers import Bot, Button, Interaction, Message
from gregtec.presence import IdleSource, NoIdle, is_away

log = logging.getLogger("gregtec.discordbot")

POLL_S = 5.0
STATE_FILE = "discord-alerts.json"
CHOICES = ("billing", "no_ai", "resume", "stop", "stop!")
OFFLINE_DM = (
    "[warn] GOS offline: the core on your PC hasn't answered for {s} s (it crashed or can't "
    "start; the supervisor keeps trying). AFK alerts, /plan and the phone dashboard are down "
    "until it is back; logs\\core.log on the PC says why."
)
BACK_DM = "GOS is back: the core answers again."


def supervisor_says_stopped(port: int, token: str | None) -> Callable[[], bool]:
    """Whether the supervisor's table says the core was stopped on purpose (``gos stop``,
    D48's Stop). Its status token is the one every managed process gets (D115). Unknown (no
    token, no answer) is False: an outage is reported."""

    def check() -> bool:
        if not token:
            return False
        try:
            status = control.call(port, "GET", "/status", token=token, timeout=2.0)
        except control.ControlError:
            return False
        for proc in status.get("processes") or ():
            if proc.get("name") == "core":
                return proc.get("state") == "stopped"
        return False

    return check


def status_token() -> str | None:
    return os.environ.get("GOS_SUPERVISOR_TOKEN") or None


class AlertDesk:
    def __init__(
        self,
        bot: Bot,
        policy: pol.NotifyPolicy,
        *,
        idle: IdleSource | None = None,
        state_file: Path | None = None,
        core_stopped: Callable[[], bool] = lambda: False,
        wall: Callable[[], float] = time.time,
        local: Callable[[float], datetime] = datetime.fromtimestamp,
    ) -> None:
        self.bot = bot
        self.policy = policy
        self.idle = idle or NoIdle()
        self.state_file = state_file
        self.core_stopped = core_stopped
        self.wall = wall
        self.local = local
        self.state: dict[str, Any] = {"down_since": None, "sent": False, "alerts": None}
        self._load()

    # --- the outage record

    def _load(self) -> None:
        if self.state_file is None:
            return
        try:
            saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(saved, dict):
            self.state.update(
                {k: saved.get(k) for k in ("down_since", "sent", "alerts")}
            )
        if not self.state.get(
            "sent"
        ):  # not DMed: count it from this start, if it lasts
            self.state["down_since"] = None

    def _save(self) -> None:
        if self.state_file is None:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except OSError as exc:
            log.info("alert state not saved", extra={"why": str(exc)[:200]})

    def _mode(self, now: float) -> str:
        saved = self.state.get("alerts") or {}
        mode = saved.get("mode") if isinstance(saved, Mapping) else None
        if mode == "quiet":
            until = saved.get("until")
            if not isinstance(until, int | float) or until <= now:
                return "on"
        return str(mode) if mode in pol.DISCORD_MODES else "on"

    # --- one pass

    async def poll(self) -> int:
        """Deliver the outbox, then the offline watch. Returns the DMs posted."""
        try:
            box = await self.bot.call(self.bot.core.alert_outbox)
        except CoreError as exc:
            if exc.offline:
                await self.core_down()
            return 0
        await self.core_up(box.get("alerts"))
        posted = 0
        for dm in box.get("dms") or ():
            if await self.post(dm):
                posted += 1
        return posted

    async def post(self, dm: Mapping[str, Any]) -> bool:
        buttons = tuple(
            Button(
                str(b["custom_id"]), str(b.get("label") or "?"), bool(b.get("danger"))
            )
            for b in dm.get("buttons") or ()
            if isinstance(b, Mapping)
            and str(b.get("custom_id", "")).startswith(pol.CREDIT_PREFIX)
        )
        try:
            await self.bot.dm(
                Message(texts.clip(str(dm.get("text") or "")), buttons=buttons)
            )
        except Exception as exc:  # noqa: BLE001 (Discord refused: tried again next pass)
            log.info("alert DM not posted", extra={"error": type(exc).__name__})
            return False
        dm_id = int(dm["id"])
        try:
            await self.bot.call(lambda: self.bot.core.alert_delivered(dm_id))
        except (
            CoreError
        ) as exc:  # posted; the mark is tried again next pass (at least once)
            log.info("alert DM mark failed", extra={"status": exc.status})
        return True

    async def core_up(self, alerts: object) -> None:
        if isinstance(alerts, Mapping):
            self.state["alerts"] = dict(alerts)
        if self.state.get("down_since") is None:
            return
        was_sent = bool(self.state.get("sent"))
        self.state.update(down_since=None, sent=False)
        self._save()
        if was_sent and self._decide(self.wall()) == pol.SEND:
            try:
                await self.bot.dm(Message(BACK_DM))
            except Exception:  # noqa: BLE001 (best effort)
                log.info("back-online DM not posted")

    async def core_down(self) -> None:
        now = self.wall()
        if self.state.get("down_since") is None:
            self.state.update(down_since=now, sent=False)
            self._save()
            return
        if self.state.get("sent"):
            return
        down_s = now - float(self.state["down_since"])
        if down_s < self.policy.offline_after_s:
            return
        try:
            on_purpose = await self.bot.call(self.core_stopped)
        except Exception:  # noqa: BLE001 (unknown: report the outage)
            on_purpose = False
        if on_purpose:
            return
        if self._decide(now) != pol.SEND:
            return
        try:
            await self.bot.dm(Message(OFFLINE_DM.format(s=int(down_s))))
        except Exception as exc:  # noqa: BLE001 (Discord unreachable too: next pass)
            log.info("offline DM not posted", extra={"error": type(exc).__name__})
            return
        log.warning("GOS offline DM sent", extra={"down_s": int(down_s)})
        self.state["sent"] = True
        self._save()

    def _decide(self, now: float) -> str:
        """The core's policy for a ``gos_offline`` DM (and its "GOS is back")."""
        try:
            idle = self.idle.idle_s()
        except OSError:
            idle = None
        ctx = pol.Context(
            away=is_away(idle),
            quiet_hours=self.policy.in_quiet_hours(self.local(now)),
            discord_mode=self._mode(now),
        )
        return pol.decide("discord", "gos_offline", "warn", ctx, self.policy)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:
                log.exception("alert desk pass failed")
            await self.bot.clock.sleep(POLL_S)

    # --- the credit DM's buttons (through the bot's one door: Clayton only)

    async def credit_button(self, it: Interaction, raw: str) -> None:
        choice, _, cycle = raw.partition(":")
        if choice not in CHOICES or not cycle:
            await it.send(Message(texts.GONE))
            return
        if choice == "stop":
            await it.send(
                Message(
                    "Stop GOS on your PC? Everything stops until gos serve starts it again.",
                    buttons=(
                        Button(
                            f"{pol.CREDIT_PREFIX}stop!:{cycle}", "Yes, stop GOS", True
                        ),
                    ),
                )
            )
            return
        try:
            now = await self.bot.call(self.bot.core.ai)
        except CoreError as exc:
            await it.send(Message(texts.core_error(exc.detail, exc.status)))
            return
        credits = now.get("credits") or {}
        if str(credits.get("cycle_start") or "")[:10] != cycle:
            await it.send(
                Message("That credit alert is from an earlier cycle; nothing changed.")
            )
            return
        decision = "stop" if choice == "stop!" else choice
        try:
            out = await self.bot.call(lambda: self.bot.core.credit_decision(decision))
        except CoreError as exc:  # 409: no alert open to answer (the desk's own words)
            await it.send(
                Message(texts.clip(exc.detail if exc.status else texts.OFFLINE))
            )
            return
        await it.send(Message(credit_reply(decision, out)))
        await self.bot.journal("action", f"credits {decision}")
        if decision == "billing":  # the alert stays open: offer what is left
            left = [
                b
                for b in pol.credit_buttons(out.get("credits") or {})
                if not b["custom_id"].startswith(f"{pol.CREDIT_PREFIX}billing:")
            ]
            if left:
                await it.send(
                    Message(
                        "Then:",
                        buttons=tuple(
                            Button(b["custom_id"], b["label"], b["danger"])
                            for b in left
                        ),
                    )
                )


def credit_reply(choice: str, out: Mapping[str, Any]) -> str:
    if choice == "billing":
        return f"Billing: {out.get('open')} (GOS never buys credits)."
    if choice == "no_ai":
        return "No-AI mode is on until you resume it or the next cycle starts."
    if choice == "resume":
        return "The AI is back on for this cycle."
    return "Stopping GOS. gos serve on the PC starts it again."
