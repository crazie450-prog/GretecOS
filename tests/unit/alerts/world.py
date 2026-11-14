"""A fake world for the alert router's tests (WO-9.2): a fake clock (UTC wall time), fake input
idle, fake channels (voice lines, in-game chat lines, the DM outbox on a real ``gos.sqlite``) and
one way to raise each alert kind as its real source does."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gregtec.advisor.checks import Finding
from gregtec.advisor.notices import Notice
from gregtec.alerts.engine import Alert, AlertEvent
from gregtec.alerts.policy import NotifyPolicy
from gregtec.alerts.router import AlertRouter
from gregtec.presence import FakeIdle
from gregtec.store import Store

DAY = 2_000_000_000 - 2_000_000_000 % 86400  # a UTC midnight
NOON = DAY + 12 * 3600
NIGHT = DAY + 2 * 3600  # inside 23:00-07:00
QUIET = NotifyPolicy(quiet_hours=(23 * 60, 7 * 60))
LEVEL = {
    "power_low": "err",
    "maintenance": "err",
    "machine_stalled": "warn",
    "crafting_done": "info",
    "power_deficit": "warn",
    "plan_buildable": "info",
    "credits80": "warn",
    "credits100": "err",
    "game_paused": "warn",
}


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


class Clock:
    def __init__(self, t: float) -> None:
        self.t = float(t)

    def __call__(self) -> float:
        return self.t


@dataclass
class World:
    """One router and everything around it. ``restart()`` makes a new router on the same DB
    (the core restarted), keeping the alerts' sources as they are."""

    path: Path
    clock: Clock
    policy: NotifyPolicy = field(default_factory=NotifyPolicy)
    voice_up: bool = True
    game_up: bool = True
    bot_up: bool = True  # a Discord bot reads the outbox
    mode: str = "on"
    said: list[str] = field(default_factory=list)
    chat: list[str] = field(default_factory=list)
    credit: dict[str, Any] | None = None
    paused: tuple[str, float | None] | None = None
    store: Store = field(init=False)
    idle: FakeIdle = field(init=False)
    router: AlertRouter = field(init=False)
    _fired: dict[str, Alert] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.store = Store(self.path)
        self.idle = FakeIdle(self.clock)
        self.router = self._router()

    def _router(self) -> AlertRouter:
        r = AlertRouter(
            self.store, self.policy, idle=self.idle, clock=self.clock, local=utc
        )
        r.credits = lambda: self.credit
        r.paused = lambda: self.paused
        r.discord_mode = lambda: self.mode
        r.voice_up = lambda: self.voice_up
        r.game_up = lambda: self.game_up
        r.discord_up = lambda: self.bot_up

        def say(text: str) -> bool:
            if not self.voice_up:
                return False
            self.said.append(text)
            return True

        def chat(text: str) -> bool:
            if not self.game_up:
                return False
            self.chat.append(text)
            return True

        r.say, r.chat = say, chat
        return r

    def restart(self) -> None:
        self.router = self._router()

    def close(self) -> None:
        self.store.close()

    # --- time and presence

    def at(self, t: float) -> None:
        self.clock.t = float(t)

    def run(self, seconds: float, step: float = 1.0) -> None:
        """Tick once a second (as the core's watcher does) for ``seconds``."""
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t = min(end, self.clock.t + step)
            self.router.tick()

    def away(self, minutes: float = 11) -> None:
        self.idle.touch(self.clock.t - minutes * 60)

    def present(self) -> None:
        self.idle.touch()

    # --- what went out

    def dms(self) -> list[dict[str, Any]]:
        """Every DM queued, delivered or not (not dropped ones)."""
        rows = self.store.rows(
            "SELECT id, text, buttons, level FROM alert_dm WHERE dropped IS NULL ORDER BY id"
        )
        return [dict(r) for r in rows]

    def got(self) -> dict[str, int]:
        return {
            "voice": len(self.said),
            "in_game": len(self.chat),
            "discord": len(self.dms()),
        }

    # --- raising alerts as their sources do

    def fire(self, rule: str, subject: str = "lab/42", binding: str = "lab") -> None:
        name = {"power_low": "LSC", "maintenance": "EBF", "machine_stalled": "EBF"}.get(
            rule, "64 Steel Ingot"
        )
        title = {
            "power_low": f"Power low: {name}",
            "maintenance": f"Maintenance: {name}",
            "machine_stalled": f"Machine stalled: {name}",
            "crafting_done": f"Crafting done: {name}",
        }[rule]
        a = Alert(
            key=f"{rule}:{subject}",
            rule=rule,
            binding=binding,
            subject=subject,
            level=LEVEL[rule],
            title=title,
            body=f"Lab: {rule} reading. tel:{subject}",
            say=f"Lab: {rule.replace('_', ' ')}.",
            cite=f"tel:{subject}",
            fired_at=self.clock.t,
        )
        self._fired[a.key] = a
        self.router.alert_event(
            AlertEvent("fired", binding, self.clock.t, a, say=a.say)
        )

    def clear(self, rule: str, subject: str = "lab/42", binding: str = "lab") -> None:
        a = self._fired.pop(f"{rule}:{subject}")
        self.router.alert_event(AlertEvent("cleared", binding, self.clock.t, a))

    def notice(self, kind: str) -> None:
        f = Finding(
            kind=kind,
            key="ledger:running" if kind == "power_deficit" else "plan:pl-1",
            title="Power deficit"
            if kind == "power_deficit"
            else "You can build EBF steel from stock",
            detail="net -120 EU/t [plan:pl-1]",
            refs=("plan:pl-1",),
            link="#/power",
        )
        say = (
            "Heads up: power deficit."
            if kind == "power_deficit"
            else "You can build it."
        )
        self.router.notice(Notice(kind, 1, f.title, say, (f,), int(self.clock.t)))

    def credits(self, pct: int, *, answered: bool = False) -> None:
        self.credit = {
            "level": "err" if pct >= 100 else "warn",
            "threshold": pct,
            "pct": float(pct),
            "spent_usd": float(pct),
            "budget_usd": 100.0,
            "cycle_start": "2033-05-01T00:00:00",
            "cycle_end": "2033-06-01T00:00:00",
            "answer": "no_ai" if answered else None,
            "pending": not answered,
            "paused": pct >= 100,
            "actions": ["billing", "resume" if pct >= 100 else "no_ai", "stop"],
            "billing_url": "https://console.anthropic.com/settings/billing",
        }

    def pause(self, instance: str = "lab", since: float | None = None) -> None:
        self.paused = (instance, self.clock.t if since is None else since)

    def unpause(self) -> None:
        self.paused = None


RAISE: dict[str, Callable[[World], None]] = {
    "power_low": lambda w: w.fire("power_low"),
    "machine_stalled": lambda w: w.fire("machine_stalled"),
    "crafting_done": lambda w: w.fire("crafting_done"),
    "power_deficit": lambda w: w.notice("power_deficit"),
    "plan_buildable": lambda w: w.notice("plan_buildable"),
    "credits80": lambda w: w.credits(80),
    "credits100": lambda w: w.credits(100),
    "game_paused": lambda w: w.pause(),
}
