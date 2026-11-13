"""The bot's logic, apart from discord.py (WO-9.1, plan 7.12, [QA:V4], [QA:V9], D220).

:class:`Bot` takes every interaction Discord sends (:meth:`Bot.interaction`): slash commands,
buttons, select menus and modals, through one door. The adapter (:mod:`.adapter`) wraps
discord.py's objects in :class:`Interaction`; the tests drive the same door with fakes.

**Clayton only, on everything** ([QA:V9], plan 4.6). The first thing :meth:`Bot.interaction`
does is compare the user id with ``[discord] owner_id``. Anyone else's command, click, choice or
form is dropped: no response at all (Discord shows its own "interaction failed", which says
nothing about GOS), no call to the core, nothing in the journal. So no one else's text reaches
``/api/ask``, and no one else's "confirm" or button press confirms as ``discord`` (D220). The
bot asks for no message-content intent and reads no channel or DM messages: only Clayton's
slash commands carry text to the core, and that text is data to the core (plan 0.1 principle 8).

**Commands** (every one answers within :data:`ANSWER_S`, [QA:V4]):

- ``/ask text``: ``POST /api/ask`` (the router picks the fast or the deep lane; no-AI mode
  answers lookups and says what needs the AI, plan 7.16). If the answer takes longer than
  :data:`ANSWER_S`, "still working" is posted then and the answer after it. A deep-lane handoff
  is followed (``GET /api/deep/tasks/{id}``) and its result posted when it ends; a task paused
  at its cap says to approve it on the dashboard (approving spends money: Clayton's paired
  devices and ``gos`` only, D156).
- ``/plan target amount [within] [tier]``: a queued design job (:mod:`gregtec.remote.jobs`),
  acknowledged at once; the result (routes in rank.py's order, the PNG diagram, the BOM TSV, the
  phone-dashboard link) comes as a follow-up, or by DM when the interaction has run out
  (Discord's interaction tokens last 15 minutes) or the bot restarted.
- ``/power mode [tier]``: a queued power design job; its options come the same way.
- ``/status``: the AI's state, the live base (OC telemetry) and the EU ledger.
- ``/alerts on|off|quiet [hours]``: Discord alerts on, off, or quiet for 1-24 hours.
- ``/persona [name]``: list the packs, or switch (``POST /api/persona``).

**Confirm buttons** (D59, D220). After an answer, if an action is pending that this bot hasn't
offered yet, its read-back is posted with Confirm and Cancel buttons naming the action id; a
press calls ``POST /api/actions/{id}/confirm`` (or ``cancel``) as the client ``discord``, which
the action desk checks exactly as it does the dashboard's: the id must be the pending one, one
pending action, 60 s expiry. A press removes the buttons from its message first.

**Vault.** Every answer is also written to the day's journal through the core
(``POST /api/discord/journal``, :class:`gregtec.vault.io.VaultIO`); design jobs are journalled by
the core's queue and their plans saved as notes by the designer (WO-3.6).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from gregtec.discordbot import texts
from gregtec.discordbot.core_api import CoreApi, CoreError
from gregtec.discordbot.settings import BotSettings

log = logging.getLogger("gregtec.discordbot")

T = TypeVar("T")

COMMANDS = ("ask", "plan", "power", "status", "alerts", "persona")
KINDS = ("command", "component", "select", "modal", "autocomplete")
ANSWER_S = 8.0  # a reply (or "still working") is posted by then: inside the 10 s bound
DEEP_POLL_S = 3.0
DEEP_MAX_S = (
    45 * 60.0
)  # the deep lane stops a paused task after 30 min (APPROVAL_WAIT_S)
JOB_POLL_S = 2.0
FOLLOWUP_TTL_S = 14 * 60.0  # Discord's interaction tokens last 15 minutes
TEXT_MAX = 1000  # gregtec.agents.ask.TEXT_MAX
POWER_MODES = ("best", "max_output", "output_per_effort", "self_sustaining")
ALERT_MODES = ("on", "off", "quiet")
PLAN_FILES = (("png", "flow.png"), ("bom", "bom.tsv"))  # gregtec.planner.outputs.FILES
CONFIRM, CANCEL = "confirm", "cancel"
CUSTOM_PREFIX = "gos:"


@dataclass(frozen=True)
class Button:
    custom_id: str
    label: str
    danger: bool = False


@dataclass(frozen=True)
class Message:
    text: str
    files: tuple[tuple[str, bytes], ...] = ()
    buttons: tuple[Button, ...] = ()


class Interaction(Protocol):
    """One Discord interaction, as the bot needs it. ``ack`` is the first response (a deferred
    "thinking" for a command, removing the buttons for a component); ``send`` a follow-up."""

    id: str
    user_id: int
    kind: str

    async def ack(self) -> None: ...
    async def send(self, msg: Message) -> None: ...


class Clock(Protocol):
    def now(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


async def in_thread[R](fn: Callable[[], R]) -> R:
    return await asyncio.to_thread(fn)


Runner = Callable[[Callable[[], Any]], Awaitable[Any]]
Dm = Callable[[Message], Awaitable[None]]


def confirm_buttons(action_id: int) -> tuple[Button, ...]:
    return (
        Button(f"{CUSTOM_PREFIX}{CONFIRM}:{action_id}", "Confirm"),
        Button(f"{CUSTOM_PREFIX}{CANCEL}:{action_id}", "Cancel", danger=True),
    )


class Bot:
    def __init__(
        self,
        settings: BotSettings,
        core: CoreApi,
        *,
        dm: Dm,
        clock: Clock | None = None,
        run: Runner = in_thread,
    ) -> None:
        self.settings = settings
        self.core = core
        self.dm = dm
        self.clock = clock or RealClock()
        self.run = run
        # job id -> (the interaction that asked, when): answered as a follow-up while fresh
        self.waiting: dict[str, tuple[Interaction, float]] = {}
        self.offered: set[int] = set()  # action ids whose buttons were posted
        self.ignored = 0  # interactions dropped because they weren't Clayton's
        self.tasks: set[asyncio.Task[None]] = set()

    # --- the one door

    def allowed(self, it: Interaction) -> bool:
        return it.user_id == self.settings.owner_id

    async def interaction(
        self, it: Interaction, name: str, options: Mapping[str, Any] | None = None
    ) -> None:
        """Every interaction: ``name`` is the command's name or the component's custom id."""
        if not self.allowed(it):
            self.ignored += 1
            # No id or text: the log says only that something was dropped (D82).
            log.info(
                "ignored an interaction from another user", extra={"kind": it.kind}
            )
            return
        options = options or {}
        if it.kind == "command" and name in COMMANDS:
            await getattr(self, f"cmd_{name}")(it, options)
        elif it.kind == "component" and name.startswith(CUSTOM_PREFIX):
            await self.button(it, name[len(CUSTOM_PREFIX) :])
        else:  # a select, modal or command this bot doesn't offer (an old registration)
            await it.ack()
            await it.send(Message(texts.GONE))

    # --- helpers

    async def call(self, fn: Callable[[], T]) -> T:
        result: T = await self.run(fn)
        return result

    async def timed(self, it: Interaction, fn: Callable[[], T]) -> T:
        """``fn``'s result (in a worker thread), within :meth:`within`'s bound."""
        return await self.within(it, self.call(fn))

    async def within(self, it: Interaction, aw: Awaitable[T]) -> T:
        """``aw``'s result; if it isn't there within :data:`ANSWER_S`, "still working" is
        posted then (so every command answers within 10 s) and the result awaited."""
        task = asyncio.ensure_future(aw)
        timer = asyncio.ensure_future(self.clock.sleep(ANSWER_S))
        await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
        if not task.done():
            await it.send(Message(texts.STILL_WORKING))
        timer.cancel()
        return await task

    async def failed(self, it: Interaction, exc: CoreError) -> None:
        await it.send(Message(texts.core_error(exc.detail, exc.status)))

    async def journal(self, kind: str, text: str) -> None:
        """The answer in the day's journal (through the core's VaultIO); never the answer's
        failure."""
        try:
            await self.call(lambda: self.core.journal(kind, text))
        except CoreError as exc:
            log.info("not journalled", extra={"status": exc.status})

    def spawn(self, coro: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coro)  # type: ignore[arg-type]
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # --- commands

    async def cmd_ask(self, it: Interaction, options: Mapping[str, Any]) -> None:
        await it.ack()
        text = " ".join(str(options.get("text") or "").split())
        if not text or len(text) > TEXT_MAX:
            await it.send(Message(f"Ask in 1 to {TEXT_MAX} characters."))
            return
        try:
            reply = await self.timed(it, lambda: self.core.ask(text))
        except CoreError as exc:
            await self.failed(it, exc)
            return
        await it.send(Message(texts.ask_reply(reply)))
        await self.journal(
            "ask", f"{text} -> ({reply.get('lane')}) {reply.get('text')}"
        )
        handoff = reply.get("handoff") or {}
        if reply.get("lane") == "deep" and isinstance(handoff.get("id"), str):
            self.spawn(self.follow_deep(it, handoff["id"]))
        else:
            await self.offer_pending(it)

    async def follow_deep(self, it: Interaction, task_id: str) -> None:
        """Post a deep-lane task's result when it ends (polled; the core keeps the task)."""
        started = self.clock.now()
        told_paused = False
        sent_at = started
        while self.clock.now() - started < DEEP_MAX_S:
            await self.clock.sleep(DEEP_POLL_S)
            try:
                task = await self.call(lambda: self.core.deep_task(task_id))
            except CoreError as exc:
                if exc.status == 404:  # the core restarted and lost it
                    await self.post(it, sent_at, Message(texts.deep_result({})))
                    return
                continue  # the core is restarting: try again
            status = task.get("status")
            if status == "needs_approval" and not told_paused:
                told_paused = True
                await self.post(it, sent_at, Message(texts.DEEP_PAUSED))
            if status in ("done", "failed", "stopped", "offline"):
                await self.post(it, sent_at, Message(texts.deep_result(task)))
                await self.journal(
                    "deep", f"{task_id} {status}: {task.get('text') or ''}"
                )
                await self.offer_pending(it, sent_at)
                return
        log.info("stopped following a deep-lane task", extra={"task": task_id})

    async def post(self, it: Interaction | None, asked_at: float, msg: Message) -> None:
        """A follow-up to ``it`` while its token lasts, else a DM to Clayton."""
        if it is not None and self.clock.now() - asked_at < FOLLOWUP_TTL_S:
            try:
                await it.send(msg)
                return
            except Exception:
                log.info("follow-up failed; sending by DM")
        await self.dm(msg)

    async def offer_pending(
        self, it: Interaction, asked_at: float | None = None
    ) -> None:
        """The pending action's read-back with Confirm and Cancel, once per action."""
        try:
            pending = (await self.call(self.core.actions)).get("pending")
        except CoreError:
            return
        if not isinstance(pending, dict) or not isinstance(pending.get("id"), int):
            return
        action_id = pending["id"]
        if action_id in self.offered:
            return
        self.offered.add(action_id)
        msg = Message(texts.readback(pending), buttons=confirm_buttons(action_id))
        await self.post(it, self.clock.now() if asked_at is None else asked_at, msg)

    async def cmd_plan(self, it: Interaction, options: Mapping[str, Any]) -> None:
        await it.ack()
        target = " ".join(str(options.get("target") or "").split())
        amount = " ".join(str(options.get("amount") or "").split())
        if not target or not amount:
            await it.send(
                Message("Give a target and a rate (2/s, 30/min) or a quantity.")
            )
            return
        spec: dict[str, Any] = {"target": target}
        spec["rate" if "/" in amount else "batch"] = amount
        for key in ("within", "tier"):
            if options.get(key):
                spec[key] = str(options[key])
        await self.queue(it, "plan", spec)

    async def cmd_power(self, it: Interaction, options: Mapping[str, Any]) -> None:
        await it.ack()
        mode = str(options.get("mode") or "")
        if mode not in POWER_MODES:
            await it.send(Message(f"mode is one of {', '.join(POWER_MODES)}"))
            return
        spec: dict[str, Any] = {"mode": mode}
        if options.get("tier"):
            spec["tier"] = str(options["tier"])
        await self.queue(it, "power", spec)

    async def queue(self, it: Interaction, kind: str, spec: dict[str, Any]) -> None:
        try:
            out = await self.timed(it, lambda: self.core.submit_job(kind, spec, it.id))
        except CoreError as exc:
            await self.failed(it, exc)
            return
        job = out.get("job") or {}
        if isinstance(job.get("id"), str):
            self.waiting[job["id"]] = (it, self.clock.now())
        await it.send(Message(texts.job_queued(job, bool(out.get("created")))))

    async def cmd_status(self, it: Interaction, options: Mapping[str, Any]) -> None:
        await it.ack()
        errors: list[str] = []

        async def part(
            what: str, fn: Callable[[], dict[str, Any]]
        ) -> dict[str, Any] | None:
            try:
                return await self.call(fn)
            except CoreError as exc:
                errors.append(f"{what}: {texts.core_error(exc.detail, exc.status)}")
                return None

        ai, live, ledger = await self.within(
            it,
            asyncio.gather(
                part("AI", self.core.ai),
                part("base", self.core.live),
                part("ledger", self.core.ledger),
            ),
        )
        await it.send(Message(texts.status(live, ledger, ai, errors)))
        await self.journal("status", "status asked")
        await self.offer_pending(it)

    async def cmd_alerts(self, it: Interaction, options: Mapping[str, Any]) -> None:
        await it.ack()
        mode = options.get("mode")
        hours = options.get("hours")
        if mode not in ALERT_MODES:
            await it.send(Message("mode is on, off or quiet"))
            return
        if mode == "quiet" and hours is None:
            await it.send(Message("quiet needs hours (1-24)"))
            return
        try:
            state = await self.timed(
                it,
                lambda: self.core.set_alerts(
                    str(mode), float(hours) if mode == "quiet" else None
                ),
            )
        except CoreError as exc:
            await self.failed(it, exc)
            return
        await it.send(Message(texts.alerts(state)))
        await self.journal("alerts", str(state.get("mode")))

    async def cmd_persona(self, it: Interaction, options: Mapping[str, Any]) -> None:
        await it.ack()
        name = " ".join(str(options.get("name") or "").split())
        try:
            if not name:
                listing = await self.timed(it, self.core.personas)
                await it.send(Message(texts.personas(listing)))
                return
            out = await self.timed(it, lambda: self.core.set_persona(name))
        except CoreError as exc:
            await self.failed(it, exc)
            return
        persona = out.get("persona") or {}
        await it.send(Message(f"Persona: {persona.get('name')} ({persona.get('id')})."))
        await self.journal("persona", str(persona.get("id")))

    # --- buttons

    async def button(self, it: Interaction, custom: str) -> None:
        verb, _, raw = custom.partition(":")
        await it.ack()  # removes the buttons: a second press can't send it again
        if verb not in (CONFIRM, CANCEL) or not raw.isdigit():
            await it.send(Message(texts.GONE))
            return
        action_id = int(raw)
        fn = self.core.confirm if verb == CONFIRM else self.core.cancel
        try:
            out = await self.timed(it, lambda: fn(action_id))
        except CoreError as exc:
            # 409: not the pending one any more (replaced, expired, done): the desk's words.
            await it.send(
                Message(texts.clip(exc.detail if exc.status else texts.OFFLINE))
            )
            return
        await it.send(Message(texts.clip(str(out.get("text") or "Done."))))
        await self.journal("action", f"{verb} {action_id}: {out.get('text')}")

    # --- delivering finished jobs

    async def deliver(self) -> int:
        """Post every finished, undelivered job of this bot's; the number posted."""
        try:
            jobs = await self.call(self.core.undelivered)
        except CoreError:
            return 0
        posted = 0
        for job in jobs:
            job_id = str(job.get("id"))
            msg = await self.job_message(job)
            it, asked_at = self.waiting.get(job_id, (None, 0.0))
            await self.post(it, asked_at, msg)
            try:
                await self.call(lambda job_id=job_id: self.core.delivered(job_id))
            except CoreError as exc:
                log.info(
                    "delivered mark failed", extra={"job": job_id, "status": exc.status}
                )
                continue  # posted; marked on the next pass (at least once)
            self.waiting.pop(job_id, None)
            posted += 1
        return posted

    async def job_message(self, job: Mapping[str, Any]) -> Message:
        res = job.get("result") or {}
        if job.get("kind") == "power":
            return Message(texts.power_result(job, self.settings))
        text = texts.plan_result(job, self.settings)
        files: list[tuple[str, bytes]] = []
        ids = [i for i in res.get("ids") or () if isinstance(i, str)]
        if job.get("state") == "done" and ids:
            for fmt, name in PLAN_FILES:
                try:
                    data = await self.call(
                        lambda fmt=fmt: self.core.export(ids[0], fmt)
                    )
                except CoreError as exc:
                    text = texts.clip(f"{text}\n({fmt} not attached: {exc.detail})")
                    continue
                files.append((f"{ids[0]}-{name}", data))
        return Message(text, files=tuple(files))

    async def deliver_forever(self) -> None:
        while True:
            try:
                await self.deliver()
            except Exception:
                log.exception("job delivery failed")
            await self.clock.sleep(JOB_POLL_S)
