"""Where an alert goes: the routing policy and ``[notify]`` in gos.toml (WO-9.2, plan 7.12
"Alerts", 7.5, 7.9; D46, D48, D71, D76, D298).

Every alert has a **level** (``info``, ``warn``, ``err``) and a **key**; its **kind** puts it in
a group, and the group and the moment decide its channels (:func:`decide`, one pure function, so
the test matrix drives it directly):

| group    | kinds                                         | dashboard | voice     | in game  | Discord DM |
|----------|-----------------------------------------------|-----------|-----------|----------|------------|
| base     | telemetry rules (WO-7.5), notices (WO-6.5)    | always    | present   | game on  | away (D46) |
| direct   | ``credits`` (D48), ``test``                   | always    | no        | no       | always     |
| away     | ``game_paused``, ``gos_offline`` (D76)        | always    | no        | no       | away       |

- **Present / away** (D76): away is no keyboard or mouse input for 10 minutes
  (:mod:`gregtec.presence`); unknown counts as present.
- **Dedupe across channels:** voice and a Discord DM both mean "Clayton was told", so an alert
  goes to at most one of them: a base alert said while he was there isn't DMed when he walks
  away, and one DMed while he was away isn't said when he comes back. "DMed" needs a bot: while
  none has read the outbox lately, nothing is queued for Discord (``no Discord bot``), so voice
  still carries a base alert when he is back, and a DM the bot never posted, once dropped, is
  not counted as sent. The dashboard (cards,
  banners, the pause chip) and the in-game chat line are passive and always carry it.
- **Quiet hours** (``quiet_hours``, local time) hold voice and DMs below ``quiet_min_level``;
  Discord's ``/alerts quiet <h>`` (D297) holds DMs the same way, and ``/alerts off`` stops them.
  A held alert isn't dropped: if it is still active when the quiet ends (and the other
  conditions still hold), it goes then.
- **Cooldown** (``cooldown_s``): after an alert's key went to a channel, a new occurrence of the
  same key (it cleared and fired again) waits that long there (base group only; the router
  keeps the times). ``game_paused`` and ``gos_offline`` are once per pause or outage, the credit
  DM once per threshold per cycle.

``[notify]`` (every key optional; **the defaults are pending Clayton**, D298):

| key               | default | meaning                                                           |
|-------------------|---------|-------------------------------------------------------------------|
| quiet_hours       | ""      | ``"23:00-07:00"`` (local, may wrap midnight); "" for none          |
| quiet_min_level   | "err"   | what still goes out in quiet hours (and with ``/alerts quiet``)    |
| discord_min_level | "warn"  | the lowest level of a base alert that is DMed while away           |
| cooldown_s        | 1800    | a base alert's key, per channel, after it went there               |
| offline_after_s   | 60      | the core silent this long is "GOS offline" (the bot's watch)       |

These are configuration (when GOS speaks up), not game numbers.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gregtec.alerts.rules import RULES

CHANNELS = ("dashboard", "voice", "in_game", "discord")
LEVELS = ("info", "warn", "err")
DISCORD_MODES = ("on", "off", "quiet")  # /alerts (D297)
DIRECT = ("credits", "test")
AWAY_ONLY = ("game_paused", "gos_offline")
NOTICE_KINDS = ("power_deficit", "plan_buildable", "quest_opened")  # WO-6.5
BASE = (*RULES, *NOTICE_KINDS)
KINDS = (*BASE, *DIRECT, *AWAY_ONLY)
SEND = "send"
CREDIT_PREFIX = (
    "gos:credit:"  # a credit DM's buttons (the bot's door checks the user first)
)
CREDIT_LABELS = {
    "billing": "Open billing",
    "no_ai": "No-AI mode",
    "resume": "Resume AI",
    "stop": "Stop",
}
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def rank(level: str) -> int:
    return LEVELS.index(level)


def group_of(kind: str) -> str:
    if kind in DIRECT:
        return "direct"
    if kind in AWAY_ONLY:
        return "away"
    return "base"


@dataclass(frozen=True)
class NotifyPolicy:
    """``[notify]`` (see the module docstring); defaults pending Clayton (D298)."""

    quiet_hours: tuple[int, int] | None = None  # (start, end) minutes after midnight
    quiet_min_level: str = "err"
    discord_min_level: str = "warn"
    cooldown_s: int = 1800
    offline_after_s: int = 60

    def in_quiet_hours(self, local: datetime) -> bool:
        if self.quiet_hours is None:
            return False
        start, end = self.quiet_hours
        minute = local.hour * 60 + local.minute
        if start < end:
            return start <= minute < end
        return minute >= start or minute < end  # wraps midnight

    def quiet_holds(self, level: str) -> bool:
        """Whether quiet (hours or ``/alerts quiet``) holds an alert of ``level``."""
        return rank(level) < rank(self.quiet_min_level)


def _hhmm(text: str) -> int:
    m = _HHMM.match(text.strip())
    if m is None:
        raise ValueError(f"{text!r} is not HH:MM")
    return int(m.group(1)) * 60 + int(m.group(2))


def parse(table: Mapping[str, Any]) -> NotifyPolicy:
    """``[notify]``; ValueError says what is wrong."""
    unknown = sorted(
        set(table)
        - {
            "quiet_hours",
            "quiet_min_level",
            "discord_min_level",
            "cooldown_s",
            "offline_after_s",
        }
    )
    if unknown:
        raise ValueError(f"gos.toml: [notify] has unknown keys {', '.join(unknown)}")
    d = NotifyPolicy()
    raw = table.get("quiet_hours", "")
    if not isinstance(raw, str):
        raise ValueError(  # noqa: TRY004 - config.py turns it into a ConfigError
            'gos.toml: [notify] quiet_hours is text like "23:00-07:00"'
        )
    hours: tuple[int, int] | None = None
    if raw.strip():
        parts = raw.split("-")
        try:
            if len(parts) != 2:
                raise ValueError(raw)
            hours = (_hhmm(parts[0]), _hhmm(parts[1]))
        except ValueError:
            raise ValueError(
                'gos.toml: [notify] quiet_hours is "HH:MM-HH:MM" (e.g. "23:00-07:00") or ""'
            ) from None
        if hours[0] == hours[1]:
            raise ValueError(
                "gos.toml: [notify] quiet_hours must not start and end together"
            )
    levels = {}
    for key in ("quiet_min_level", "discord_min_level"):
        value = table.get(key, getattr(d, key))
        if value not in LEVELS:
            raise ValueError(f"gos.toml: [notify] {key} is one of {', '.join(LEVELS)}")
        levels[key] = str(value)
    numbers = {}
    for key, lo, hi in (("cooldown_s", 0, 86_400), ("offline_after_s", 10, 3600)):
        value = table.get(key, getattr(d, key))
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not lo <= value <= hi
        ):
            raise ValueError(
                f"gos.toml: [notify] {key} is a whole number of seconds from {lo} to {hi}"
            )
        numbers[key] = value
    return NotifyPolicy(quiet_hours=hours, **levels, **numbers)


@dataclass(frozen=True)
class Context:
    """The moment an alert is routed in."""

    away: bool  # D76: no input for 10 minutes
    quiet_hours: bool  # inside [notify] quiet_hours now
    discord_mode: str = "on"  # /alerts: on, off, quiet (quiet's hours not over)
    voice_up: bool = True  # a voice client is connected to the core
    game_up: bool = True  # a game's GregtecLink client is connected
    discord_up: bool = (
        True  # the Discord bot read the outbox lately (the router's view)
    )


def decide(
    channel: str,
    kind: str,
    level: str,
    ctx: Context,
    policy: NotifyPolicy,
    sent_on: Collection[str] = (),
) -> str:
    """:data:`SEND`, or why ``channel`` doesn't carry this alert now. ``sent_on``: the channels
    this occurrence already went to. Cooldowns are the router's (it keeps the history)."""
    group = group_of(kind)
    if channel == "dashboard":
        return SEND
    if channel == "voice":
        if group != "base":
            return "not a voice alert"
        if ctx.away:
            return "away"
        if not ctx.voice_up:
            return "no voice client"
        if "discord" in sent_on:
            return "already DMed"
        if ctx.quiet_hours and policy.quiet_holds(level):
            return "quiet hours"
        return SEND
    if channel == "in_game":
        if group != "base":
            return "not an in-game alert"
        if not ctx.game_up:
            return "no game connected"
        return SEND
    if channel != "discord":
        raise ValueError(f"no channel {channel!r}")
    if ctx.discord_mode == "off":
        return "/alerts off"
    if not ctx.discord_up:
        return "no Discord bot"  # not "DMed": voice can still carry it when he is back
    if group == "base":
        if rank(level) < rank(policy.discord_min_level):
            return "below discord_min_level"
        if "voice" in sent_on:
            return "already said"
    if group != "direct" and not ctx.away:
        return "present"
    if ctx.quiet_hours and policy.quiet_holds(level):
        return "quiet hours"
    if ctx.discord_mode == "quiet" and policy.quiet_holds(level):
        return "/alerts quiet"
    return SEND


def credit_buttons(state: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """The credit banner's own actions as DM buttons (D48, D165): Open billing, No-AI mode (or
    Resume AI while paused), Stop. Each names the cycle, so an old DM's button can't answer a
    later cycle's alert."""
    cycle = str(state.get("cycle_start") or "")[:10]
    out = []
    for choice in state.get("actions") or ():
        if choice in CREDIT_LABELS:
            out.append(
                {
                    "custom_id": f"{CREDIT_PREFIX}{choice}:{cycle}",
                    "label": CREDIT_LABELS[choice],
                    "danger": choice == "stop",
                }
            )
    return tuple(out)
