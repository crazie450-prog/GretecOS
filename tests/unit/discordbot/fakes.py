"""Fakes for the Discord bot's tests (WO-9.1): a fake clock, fake interactions (Discord's
gateway as the bot's one door sees it) and a fake core whose design jobs go through the real
durable queue (:class:`gregtec.remote.jobs.JobQueue`) on a real ``gos.sqlite``.

No Discord, no network, no model: every test drives :class:`gregtec.discordbot.handlers.Bot`
with these.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from gregtec.discordbot.core_api import CoreError
from gregtec.discordbot.handlers import Bot, Message
from gregtec.discordbot.settings import BotSettings
from gregtec.remote.jobs import JobQueue

CLAYTON = 111_111_111_111_111_111
STRANGER = 222_222_222_222_222_222
SETTINGS = BotSettings(
    owner_id=CLAYTON,
    guild_id=333_333_333_333_333_333,
    dashboard_url="https://pc.tail1.ts.net",
)
PNG = b"\x89PNG\r\n\x1a\nfake"
BOM = b"item\tcount\nSteel Ingot\t1\n"
_ids = itertools.count(1_300_000_000_000_000_000)


async def settle(rounds: int = 30) -> None:
    """Let every ready task run."""
    for _ in range(rounds):
        await asyncio.sleep(0)


class FakeClock:
    """The bot's clock, moved by hand: ``sleep`` waits until :meth:`advance` passes its time."""

    def __init__(self) -> None:
        self.t = 0.0
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._sleepers.append((self.t + seconds, fut))
        await fut

    async def advance(self, seconds: float, step: float = 0.5) -> None:
        end = self.t + seconds
        await settle()
        while self.t < end:
            self.t = min(end, self.t + step)
            for due, fut in self._sleepers:
                if due <= self.t and not fut.done():
                    fut.set_result(None)
            self._sleepers = [(d, f) for d, f in self._sleepers if not f.done()]
            await settle()


@dataclass
class FakeInteraction:
    """One interaction from Discord's gateway: who, what kind, and what the bot answered (with
    the fake time of each answer)."""

    user_id: int
    kind: str
    clock: FakeClock
    id: str = field(default_factory=lambda: str(next(_ids)))
    acks: list[float] = field(default_factory=list)
    sent: list[tuple[float, Message]] = field(default_factory=list)
    expired: bool = False  # Discord's token ran out: a follow-up fails

    async def ack(self) -> None:
        self.acks.append(self.clock.now())

    async def send(self, msg: Message) -> None:
        if self.expired:
            raise RuntimeError("404 Unknown Webhook")
        self.sent.append((self.clock.now(), msg))

    @property
    def texts(self) -> list[str]:
        return [m.text for _, m in self.sent]

    @property
    def answered(self) -> bool:
        return bool(self.acks or self.sent)

    def first_answer_at(self) -> float:
        return min([*self.acks, *(t for t, _ in self.sent)])


def ask_reply(
    text: str = "Steel Ingot: Electric Blast Furnace [r:abc123].", **more: object
) -> dict[str, Any]:
    return {"text": text, "lane": "fast", "refs": ["r:abc123"], **more}


def designed(kind: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    """The queue's runner in these tests: a stand-in for the designer's result (its shape is
    :func:`gregtec.remote.routes.run_job`'s)."""
    if kind == "power":
        return {
            "ids": ["pw-00000001"],
            "mode": spec["mode"],
            "mode_label": "Best",
            "tier_cap": None,
            "options": [
                {
                    "id": "pw-00000001",
                    "name": "Steam turbines",
                    "position": 1,
                    "of": 1,
                    "net_eut": 120,
                    "gross_eut": 128,
                    "generator": "Large Steam Turbine",
                    "fuel": "Steam",
                }
            ],
        }
    return {
        "ids": ["pl-00000001", "pl-00000002"],
        "problems": [],
        "routes": [
            {
                "id": "pl-00000001",
                "name": "EBF steel",
                "column": "base",
                "position": 1,
                "of": 2,
                "target": "Steel Ingot",
                "machines": 3,
                "eut_avg": 120.0,
                "eff_h": 1.5,
                "warnings": [],
            },
            {
                "id": "pl-00000002",
                "name": "Steel dust",
                "column": "base",
                "position": 2,
                "of": 2,
                "target": "Steel Ingot",
                "machines": 5,
                "eut_avg": 96,
                "eff_h": 2.0,
                "warnings": [],
            },
        ],
    }


@dataclass
class FakeCore:
    """The core's routes as the bot calls them. Each call is recorded in ``calls``; ``delay``
    (fake seconds) makes every call that long; ``offline`` makes every call fail as if the core
    were down. Design jobs go to ``queue`` (a real :class:`JobQueue`)."""

    queue: JobQueue
    clock: FakeClock
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    delay: float = 0.0
    offline: bool = False
    answer: Callable[[str], dict[str, Any]] = lambda text: ask_reply()
    pending: dict[str, Any] | None = None
    deep: list[dict[str, Any]] = field(
        default_factory=list
    )  # deep_task's answers in turn
    confirm_error: CoreError | None = None
    ai_online: bool = True
    alerts_state: dict[str, Any] = field(
        default_factory=lambda: {"mode": "on", "until": None}
    )
    journal_lines: list[tuple[str, str]] = field(default_factory=list)
    # WO-9.2: the alert outbox, the credit banner's state and the decisions made
    outbox: list[dict[str, Any]] = field(default_factory=list)
    marked: list[int] = field(default_factory=list)
    credits_state: dict[str, Any] = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)
    decision_error: CoreError | None = None

    def _rec(self, name: str, *args: object) -> None:
        self.calls.append((name, args))
        if self.offline:
            raise CoreError(f"{name}: no answer on port 8765")

    # CoreApi
    def ask(self, text: str) -> dict[str, Any]:
        self._rec("ask", text)
        return self.answer(text)

    def deep_task(self, task_id: str) -> dict[str, Any]:
        self._rec("deep_task", task_id)
        if len(self.deep) > 1:
            return self.deep.pop(0)
        return self.deep[0]

    def submit_job(self, kind: str, spec: dict[str, Any], key: str) -> dict[str, Any]:
        self._rec("submit_job", kind, spec, key)
        job, created = self.queue.submit("discord", key, kind, spec)
        return {"job": job.as_dict(), "created": created}

    def undelivered(self) -> list[dict[str, Any]]:
        self._rec("undelivered")
        return [j.as_dict() for j in self.queue.jobs("discord", undelivered=True)]

    def delivered(self, job_id: str) -> dict[str, Any]:
        self._rec("delivered", job_id)
        done = self.queue.delivered(job_id, "discord")
        if done is None:
            raise CoreError(f"POST /api/jobs/{job_id}/delivered: 409 not finished", 409)
        return done.as_dict()

    def export(self, plan_id: str, fmt: str) -> bytes:
        self._rec("export", plan_id, fmt)
        return PNG if fmt == "png" else BOM

    def ledger(self) -> dict[str, Any]:
        self._rec("ledger")
        return {
            "ledger": {
                "tiers": {
                    "all": [
                        {
                            "tier": "MV",
                            "gen_eut": 512,
                            "use_eut_avg": 300.5,
                            "net_eut": 211.5,
                            "plans": ["plan:pl-00000001"],
                        }
                    ]
                },
                "warnings": [],
            }
        }

    def live(self) -> dict[str, Any]:
        self._rec("live")
        return {
            "bindings": [
                {
                    "binding": "lab",
                    "agents": [{"stale": False}],
                    "problems": [
                        {
                            "level": "warn",
                            "text": "EBF out of power",
                            "cite": "tel:lab/42",
                        }
                    ],
                    "power": [{"name": "LSC", "pct": 81.5, "cite": "tel:lab/43"}],
                }
            ]
        }

    def ai(self) -> dict[str, Any]:
        self._rec("ai")
        if self.ai_online:
            return {"ai": {"online": True}, "credits": self.credits_state}
        return {
            "ai": {"online": False, "reason": "no API key"},
            "credits": self.credits_state,
        }

    def personas(self) -> dict[str, Any]:
        self._rec("personas")
        return {
            "active": "ship-ai",
            "packs": [
                {"id": "ship-ai", "name": "Ship AI"},
                {"id": "plain", "name": "Plain"},
            ],
        }

    def set_persona(self, name: str) -> dict[str, Any]:
        self._rec("set_persona", name)
        if name not in ("ship-ai", "plain", "random"):
            raise CoreError(f"POST /api/persona: 404 no persona called {name}", 404)
        return {"persona": {"id": "plain", "name": "Plain"}}

    def set_alerts(self, mode: str, hours: float | None) -> dict[str, Any]:
        self._rec("set_alerts", mode, hours)
        self.alerts_state = {
            "mode": mode,
            "until": 1_800_007_200 if mode == "quiet" else None,
        }
        return self.alerts_state

    def journal(self, kind: str, text: str) -> None:
        self._rec("journal", kind, text)
        self.journal_lines.append((kind, text))

    def actions(self) -> dict[str, Any]:
        self._rec("actions")
        return {"pending": self.pending, "recent": []}

    def confirm(self, action_id: int) -> dict[str, Any]:
        self._rec("confirm", action_id)
        if self.confirm_error is not None:
            raise self.confirm_error
        self.pending = None
        return {"ok": True, "text": "Done: 64 Stainless Steel Ingot queued."}

    def cancel(self, action_id: int) -> dict[str, Any]:
        self._rec("cancel", action_id)
        self.pending = None
        return {"ok": True, "text": "Cancelled."}

    def alert_outbox(self) -> dict[str, Any]:
        self._rec("alert_outbox")
        return {"dms": list(self.outbox), "alerts": self.alerts_state}

    def alert_delivered(self, dm_id: int) -> dict[str, Any]:
        self._rec("alert_delivered", dm_id)
        self.marked.append(dm_id)
        self.outbox = [d for d in self.outbox if d["id"] != dm_id]
        return {"delivered": dm_id}

    def credit_decision(self, choice: str) -> dict[str, Any]:
        self._rec("credit_decision", choice)
        if self.decision_error is not None:
            raise self.decision_error
        self.decisions.append(choice)
        return {
            "choice": choice,
            "open": "https://console.anthropic.com/settings/billing"
            if choice == "billing"
            else None,
            "credits": self.credits_state,
        }

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


def make_bot(core: FakeCore, dms: list[Message]) -> Bot:
    """A bot over ``core`` on ``core.clock``; its DMs to Clayton go to ``dms``. Core calls run
    inline (no worker thread) after ``core.delay`` fake seconds."""

    async def dm(msg: Message) -> None:
        dms.append(msg)

    async def run(fn: Callable[[], Any]) -> Any:  # noqa: ANN401 - whatever fn returns
        if core.delay:
            await core.clock.sleep(core.delay)
        return fn()

    return Bot(SETTINGS, core, dm=dm, clock=core.clock, run=run)


def drive(
    bot: Bot, it: FakeInteraction, name: str, options: Mapping[str, Any] | None = None
) -> Awaitable[None]:
    return bot.interaction(it, name, options)
