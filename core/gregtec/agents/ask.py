"""One request, end to end (WO-2.3): router -> instant command, fast lane or deep lane (D150-D152).

:meth:`Asker.ask` is what ``POST /api/ask`` and ``gos ask`` run. It returns a :class:`Reply`:
the text to say, a card for the dashboard (data, never HTML: WO-2.6 draws it), and how the
answer was made (lane, citations, whether its numbers were checked, time to first token).

- An unsure item match asks (QA:D11): the reply is the question, its card the top three, and the
  next request may pick one ("the second one", "2", or its name), which re-runs the first request
  on that item. "Repeat" says the question again and keeps it open; anything else starts afresh.
- A confirm or cancel that found nothing waiting (its reply's ``pending`` is False) changed
  nothing, so it isn't kept as the last reply: "repeat" still says the answer before it (D179).
- With no fast lane (no key stored, or the API failing) a fast-lane request gets the instant
  answer for its card when pre-retrieval found one, else "AI is offline; I can do lookups"
  (plan 7.16). With an :class:`~gregtec.agents.no_ai.AiMode` (the core's, WO-2.8) the asker asks
  it first, so in no-AI mode (no key, an API problem, the credit pause, Clayton's switch) no
  model is called: a fast-lane request gets those lookups, and deep-lane work gets a line saying
  it needs the AI. A failed call tells the mode what it showed, a good one that the API is back.
- Saved plans ("my plans", "show plan X", "BOM for X") are instant commands
  (:mod:`gregtec.agents.saved_plans`), so they work with the AI offline.
- "persona <name>" switches the persona with no model call (WO-5.6, D206), "which persona"
  names it; every reply carries the active persona (``persona``: id, name, voice), so the voice
  client speaks it in that persona's voice (D207).
- :meth:`Asker.prewarm` writes the fast lane's prompt cache ahead of a request (the voice client
  calls it as the talk key goes down, D152, D179), unless a fast-lane call started within the
  last 4 minutes: each call refreshes the 5-minute cache, so the cache is still warm then.
"""

from __future__ import annotations

import io
import json
import logging
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO

import anthropic

from gregtec import config
from gregtec.agents import prefetch, router, saved_plans
from gregtec.agents.deep import (
    DONE,
    NEEDS_APPROVAL,
    OFFLINE,
    DeepLaneService,
    DeepTask,
)
from gregtec.agents.deep_config import Settings
from gregtec.agents.deep_lane import DeepLane, HandoffRecorder
from gregtec.agents.fast_lane import (
    NOT_CHECKED,
    FastLane,
    LaneAnswer,
    LaneError,
    refs,
)
from gregtec.agents.no_ai import NEEDS_AI, AiMode
from gregtec.agents.personas import STATE_FILE, Personas, UnknownPersona
from gregtec.agents.tools.catalog import default_registry
from gregtec.agents.tools.registry import Registry
from gregtec.agents.tools.tsv import Parsed
from gregtec.db import access
from gregtec.llm.fake import EchoClient
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

log = logging.getLogger(__name__)

TEXT_MAX = 1000  # characters of one request
DEEP_WAIT_S = 1800.0  # how long gos ask waits for a deep-lane task
LANE_RETRY_S = 30.0  # how often a missing lane (no key yet) is looked for again
# A fast-lane call this recent keeps the 5-minute prompt cache warm (each read refreshes it);
# the margin covers the seconds between the talk key going down and the request (D179).
PREWARM_FRESH_S = 240.0
WARMED, WARM, BUSY, NO_LANE, FAILED = "warmed", "warm", "busy", "offline", "failed"
AI_OFFLINE = "AI is offline; I can do lookups."
NOTHING_PENDING = "Nothing is waiting for confirmation."
NOTHING_TO_REPEAT = "I haven't said anything yet."
EMPTY = "Ask me something: a recipe, an item, a machine or a quest."
# NOT_CHECKED for a turn with no card to point at (the model looked things up itself)
NOT_CHECKED_NO_CARD = (
    "I can't give that answer with checked numbers. Ask for the item's recipe or uses and "
    "I'll show the rows with their ids."
)
HANDED_OFF = "That's one for the deep lane; I've handed it over."
DEEP_OFFLINE = (
    "The deep lane is offline, so I can't take that on now; I can do lookups."
)
_ORDINALS = {
    "1": 0, "one": 0, "first": 0, "the first": 0, "the first one": 0, "number one": 0,
    "2": 1, "two": 1, "second": 1, "the second": 1, "the second one": 1, "number two": 1,
    "3": 2, "three": 2, "third": 2, "the third": 2, "the third one": 2, "number three": 2,
}  # fmt: skip
_YES = frozenset({"yes", "yeah", "yep", "that one", "it", "that"})


@dataclass
class Reply:
    text: str
    lane: str  # instant, fast, deep or no_ai
    intent: str
    asked: bool = False  # a clarifying question instead of an answer
    card: dict[str, Any] | None = None
    target: dict[str, str] | None = None  # the resolved item
    refs: list[str] = field(default_factory=list)
    checked: bool = True  # every number in text was found in the turn's tool results
    ttft_ms: int | None = None  # request in -> first text token (instant: -> reply)
    total_ms: int = 0
    calls: int = 0  # model calls
    tool_calls: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    usd: float = 0.0
    problems: list[str] = field(default_factory=list)
    handoff: dict[str, Any] | None = None
    error: str | None = None
    # confirm and cancel: whether an action was waiting for them (D59; actions arrive with
    # WO-7.1). The voice client's cancel hotkey stays quiet when none was (D179).
    pending: bool | None = None
    # the persona the reply was made in (WO-5.6): id, name, voice, ...; None without packs
    persona: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "lane": self.lane,
            "intent": self.intent,
            "asked": self.asked,
            "card": self.card,
            "target": self.target,
            "refs": self.refs,
            "checked": self.checked,
            "ttft_ms": self.ttft_ms,
            "total_ms": self.total_ms,
            "calls": self.calls,
            "tool_calls": self.tool_calls,
            "usage": self.usage,
            "usd": round(self.usd, 6),
            "problems": self.problems,
            "handoff": self.handoff,
            "error": self.error,
            "pending": self.pending,
            "persona": self.persona,
        }


@dataclass(frozen=True)
class _Pending:
    route: router.Route
    clarify: prefetch.Clarify


def _name(c: prefetch.Candidate | None) -> str:
    return (c.name or c.key) if c else ""


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _flagged(row: dict[str, str]) -> bool:
    """A recipe row flagged ``fake?`` (its NEI handler doesn't say) or ``recycling``."""
    return bool({"fake?", "recycling"} & set(row.get("flags", "").split()))


def card_text(pre: prefetch.Prefetch, intent: str) -> str | None:
    """The instant (no-model) answer for a pre-retrieved card; None when there is none."""
    main = pre.main
    if main is None:
        return None
    if main.is_error:
        return main.text.removeprefix("error: ").strip()
    p: Parsed = main.parsed
    name = _name(pre.target) or p.head.get("item", "").split(" [")[0]
    first = p.rows[0] if p.rows else None
    total = p.total if p.total is not None else len(p.rows)
    which = "first"
    if main.tool in ("recipe_find", "recipe_uses") and first is not None:
        # The recipe-lookup skill's rule (D172): the first row without a flag, if any.
        plain = next((r for r in p.rows if not _flagged(r)), first)
        which = "first" if plain is first else "first unflagged one"
        first = plain
    if main.tool == "recipe_find":
        if first is None:
            return f"{name}: no recipe in the DB makes it."
        return (
            f"{name}: {_count(total, 'recipe')}, lowest tier first. The {which} is "
            f"{first['machine']} ({first['ref']})."
        )
    if main.tool == "recipe_uses":
        if first is None:
            return f"{name}: no recipe in the DB takes it."
        return (
            f"{name} goes into {_count(total, 'recipe')}. The {which} is "
            f"{first['machine']} ({first['ref']})."
        )
    if main.tool == "machine_info":
        return f"{p.head.get('name', name)} ({p.head.get('machine', '')}): the card lists its facts."
    if main.tool in ("quest_for_item", "quest_find", "quest_frontier"):
        if first is None:
            return f"No quest matches {name or 'that'}."
        return (
            f"{_count(total, 'quest')} match. The first is {first['name']} "
            f"({first['quest']})."
        )
    return None


class Asker:
    def __init__(
        self,
        registry: Registry,
        deep: DeepLane,
        lane: Callable[[], FastLane | None] | FastLane | None,
        *,
        clock: Callable[[], float] = time.perf_counter,
        ai: AiMode | None = None,
        home: Path | None = None,
        personas: Personas | None = None,
    ) -> None:
        self.registry = registry
        self.personas = personas  # WO-5.6; None: no persona commands
        self.home = home  # GOS_HOME, for saved plans' JSON (None: the DB's own)
        self.deep = deep
        self.clock = clock
        self.ai = (
            ai  # no-AI mode (WO-2.8); None: only a missing lane or an error stops it
        )
        if callable(lane) and not isinstance(lane, FastLane):
            self._factory: Callable[[], FastLane | None] | None = lane
            self._lane: FastLane | None = None
        else:
            self._factory, self._lane = None, lane
        self._looked = -LANE_RETRY_S
        self._lock = threading.Lock()
        self._last: Reply | None = None
        self._pending: _Pending | None = None
        self._warm_at: float | None = None  # clock when the last fast-lane call started
        self._warming = False

    def lane(self) -> FastLane | None:
        """The fast lane; a missing one (no key stored yet) is looked for every 30 s."""
        with self._lock:
            if self._lane is None and self._factory is not None:
                now = self.clock()
                if now - self._looked >= LANE_RETRY_S:
                    self._looked = now
                    try:
                        self._lane = self._factory()
                    except Exception:  # the keyring can fail; lookups still work
                        log.exception("can't set up the fast lane")
            return self._lane

    def ask(self, text: str, *, task: str = "ask") -> Reply:
        started = self.clock()
        text = " ".join(text.split())[:TEXT_MAX]
        if not text:
            return Reply(EMPTY, router.INSTANT, "empty", ttft_ms=0)
        with self._lock:
            pending, self._pending = self._pending, None
        route = self._picked(pending, text) or router.route(text)
        if route.intent == router.REPEAT and pending is not None:
            with self._lock:  # the repeated question can still be answered
                self._pending = pending
        reply = self._reply(route, started, task)
        if self.personas is not None and reply.persona is None:
            reply.persona = self.personas.info()
        reply.total_ms = round((self.clock() - started) * 1000)
        if reply.ttft_ms is None and reply.calls == 0:
            reply.ttft_ms = reply.total_ms
        # A confirm or cancel that found nothing waiting changed nothing (the voice client's
        # cancel hotkey silences GOS with one, D179): "repeat" still says the answer before it.
        if route.intent != router.REPEAT and reply.pending is not False:
            with self._lock:
                self._last = reply
        return reply

    def _picked(self, pending: _Pending | None, text: str) -> router.Route | None:
        """The pending request re-run on the candidate ``text`` picks, if it picks one."""
        if pending is None:
            return None
        said = router.normalise(text)
        found = pending.clarify.candidates
        index = _ORDINALS.get(said)
        if index is None and len(found) == 1 and said in _YES:
            index = 0
        if index is None:
            named = [i for i, c in enumerate(found) if c.name.casefold() == said]
            named = named or [
                i
                for i, c in enumerate(found)
                if said and re.search(rf"\b{re.escape(said)}\b", c.name.casefold())
            ]
            index = named[0] if len(named) == 1 else None
        if index is None or index >= len(found):
            return None
        return replace(pending.route, subject=found[index].key)

    def _reply(self, route: router.Route, started: float, task: str) -> Reply:
        if route.kind == router.INSTANT:
            if route.intent == router.PLAN and self._ai_usable():
                reply = self._plans(route, fall_through=True)
                if reply is None:  # names no saved plan: the AI answers it, as before
                    return self._reply(
                        router.route(route.text, plans=False), started, task
                    )
                return reply
            return self._instant(route)
        if route.kind == router.DEEP:
            reasons = self.ai.reasons() if self.ai is not None else []
            if reasons:
                assert self.ai is not None
                text = NEEDS_AI.format(why=self.ai.why())
                return Reply(text, "no_ai", route.intent, error=reasons[0])
            item = self.deep.handoff(route.text, route.text, "router")
            text = DEEP_OFFLINE if item.status == OFFLINE else HANDED_OFF
            return Reply(text, router.DEEP, route.intent, handoff=item.as_dict())
        pre = prefetch.prefetch(self.registry, route)
        if pre.clarify is not None:
            return self._ask_back(route, pre)
        if self.ai is not None and not self.ai.allowed():
            return self._offline(route, pre, (self.ai.reasons() or ["offline"])[0])
        lane = self.lane()
        if lane is None:
            return self._offline(route, pre, None)
        try:
            answer = lane.answer(route.text, pre, started=started, task=task)
        except LaneError as exc:
            log.warning("fast lane failed", extra={"error": str(exc)[:200]})
            if self.ai is not None:
                self.ai.failed(exc)
            return self._offline(route, pre, str(exc))
        if self.ai is not None:
            self.ai.succeeded()
        with self._lock:
            self._warm_at = started
        return self._fast(route, pre, answer)

    def _ai_usable(self) -> bool:
        """Whether a model could answer now: the AI isn't off and a fast lane is set up."""
        if self.ai is not None and self.ai.reasons():
            return False
        return self.lane() is not None

    def prewarm(self) -> str:
        """Write the fast lane's prompt cache for the request about to come (D152, D179).

        Returns :data:`WARMED`, :data:`WARM` (a call started within :data:`PREWARM_FRESH_S`
        already keeps it), :data:`BUSY` (another pre-warm is running), :data:`NO_LANE` (no key
        stored) or :data:`FAILED` (the API failed; the request itself will say so).
        """
        if self.ai is not None and self.ai.reasons():
            return NO_LANE
        lane = self.lane()
        if lane is None:
            return NO_LANE
        with self._lock:
            if self._warming:
                return BUSY
            now = self.clock()
            if self._warm_at is not None and now - self._warm_at < PREWARM_FRESH_S:
                return WARM
            self._warming = True
        try:
            lane.prewarm()
        except anthropic.APIError as exc:
            log.warning(
                "fast-lane pre-warm failed", extra={"error": type(exc).__name__}
            )
            if self.ai is not None:
                self.ai.failed(exc)
            return FAILED
        finally:
            with self._lock:
                self._warming = False
        with self._lock:
            self._warm_at = now
        return WARMED

    def _base(
        self, route: router.Route, pre: prefetch.Prefetch, lane: str, text: str
    ) -> Reply:
        return Reply(
            text,
            lane,
            route.intent,
            card=pre.main.card(self.registry.env.db) if pre.main else None,
            target=(
                {
                    "key": pre.target.key,
                    "name": _name(pre.target),
                    "kind": pre.target.kind,
                }
                if pre.target
                else None
            ),
            refs=refs(text),
        )

    def _ask_back(self, route: router.Route, pre: prefetch.Prefetch) -> Reply:
        assert pre.clarify is not None
        with self._lock:
            self._pending = _Pending(route, pre.clarify)
        reply = self._base(route, pre, router.INSTANT, pre.clarify.question)
        reply.asked, reply.card = True, pre.clarify.card()
        return reply

    def _offline(
        self, route: router.Route, pre: prefetch.Prefetch, error: str | None
    ) -> Reply:
        text = card_text(pre, route.intent) or AI_OFFLINE
        if text != AI_OFFLINE:
            text = f"{text} ({AI_OFFLINE})"
        reply = self._base(route, pre, "no_ai", text)
        reply.error = error
        return reply

    def _fast(
        self, route: router.Route, pre: prefetch.Prefetch, answer: LaneAnswer
    ) -> Reply:
        text = answer.text
        if text == NOT_CHECKED and pre.main is None:
            text = NOT_CHECKED_NO_CARD
        reply = self._base(route, pre, router.FAST, text)
        reply.checked = answer.checked
        reply.ttft_ms = answer.ttft_ms
        reply.calls = answer.calls
        reply.tool_calls = answer.tool_calls
        reply.problems = answer.problems
        reply.usd = answer.usd
        reply.usage = {
            "input": answer.usage.input,
            "output": answer.usage.output,
            "cache_read": answer.usage.cache_read,
            "cache_write": answer.usage.cache_write,
        }
        if "handoff" in answer.tool_calls:
            reply.handoff = {"source": "fast_lane"}
        return reply

    def _instant(self, route: router.Route) -> Reply:
        intent = route.intent
        if intent == router.REPEAT:
            with self._lock:
                last = self._last
            if last is None:
                return Reply(NOTHING_TO_REPEAT, router.INSTANT, intent)
            return replace(
                last, lane=router.INSTANT, intent=intent, calls=0, ttft_ms=None
            )
        if intent in (router.CONFIRM, router.CANCEL):
            # The confirm flow (D59) arrives with WO-7.1's actions; nothing can be pending yet.
            return Reply(NOTHING_PENDING, router.INSTANT, intent, pending=False)
        if intent == router.TIER:
            out = self.registry.call("quest_frontier", {"n": 1}, lane="fast")
            block = prefetch.Block("quest_frontier", {"n": 1}, out.text, out.is_error)
            card = block.card()
            if out.is_error:
                return Reply(card["error"], router.INSTANT, intent, card=card)
            tier = block.parsed.head.get("tier", "unknown")
            return Reply(f"Your tier: {tier}.", router.INSTANT, intent, card=card)
        if intent == router.PERSONA:
            return self._persona(route)
        if intent in (router.PLANS, router.PLAN):
            reply = self._plans(route)
            assert reply is not None  # only fall_through gives None
            return reply
        pre = prefetch.prefetch(self.registry, route, tier=False)
        if pre.clarify is not None:
            return self._ask_back(route, pre)
        if pre.unresolved is not None:
            text = f"No item matches {pre.unresolved!r}; try other words."
        else:
            text = card_text(pre, intent) or "I couldn't look that up."
        return self._base(route, pre, router.INSTANT, text)

    def _persona(self, route: router.Route) -> Reply:
        """Switch to the persona named, or say which one is on (no model call, D206)."""
        intent = route.intent
        if self.personas is None:
            return Reply("Personas aren't set up here.", router.INSTANT, intent)
        if route.subject is None:
            listing = self.personas.listing()
            active = self.personas.active
            names = ", ".join(str(p["name"]) for p in listing["packs"])  # type: ignore[union-attr]
            if active is None:
                return Reply("No persona pack loads.", router.INSTANT, intent)
            text = f"Persona: {active.name}. Say persona and a name to switch: {names}."
            return Reply(text, router.INSTANT, intent, persona=active.info())
        try:
            pack = self.personas.set(route.subject, by="router")
        except UnknownPersona as exc:
            return Reply(str(exc), router.INSTANT, intent, error="unknown persona")
        greeting = pack.samples[0] if pack.samples else ""
        text = f"{pack.name} here. {greeting}".strip()
        return Reply(text, router.INSTANT, intent, persona=pack.info())

    def _plans(
        self, route: router.Route, *, fall_through: bool = False
    ) -> Reply | None:
        """Saved plans from the DB and their JSON (WO-2.8): the list, or one plan's BOM card.
        ``fall_through``: None when no saved plan matches (the AI answers the question)."""
        found, problems = saved_plans.load(self.registry.env.db, self.home)
        if route.intent == router.PLANS:
            card = saved_plans.list_card(found, problems)
            if not found:
                text = "No plans are saved yet."
            else:
                names = ", ".join(p.name for p in found[:3])
                more = "" if len(found) <= 3 else ", ..."
                text = f"{_count(len(found), 'saved plan')}: {names}{more}."
            return Reply(text, router.INSTANT, route.intent, card=card)
        words = route.subject or ""
        hits = saved_plans.find(found, words)
        if not hits and fall_through:
            return None
        if not hits:
            return Reply(
                f"No saved plan matches {words!r}.",
                router.INSTANT,
                route.intent,
                card=saved_plans.list_card(found, problems) if found else None,
            )
        if len(hits) > 1:
            text = f"{_count(len(hits), 'plan')} match {words!r}; say which one."
            card = saved_plans.list_card(hits, [])
            return Reply(text, router.INSTANT, route.intent, card=card)
        plan = hits[0]
        if plan.bom is None:
            text = f"{plan.name} (plan:{plan.id}): no BOM is saved with it."
        else:
            text = (
                f"{plan.name} (plan:{plan.id}): the BOM has "
                f"{_count(len(plan.bom), 'row')}."
            )
        return Reply(
            text,
            router.INSTANT,
            route.intent,
            card=saved_plans.bom_card(plan),
            refs=[f"plan:{plan.id}"],
        )


@dataclass
class Agents:
    """The tool registry, the deep-lane door, the meter and the fast lane, set up from
    gos.toml for the core, ``gos ask`` and ``gos eval fast-lane``."""

    registry: Registry
    deep: HandoffRecorder | DeepLaneService
    meter: UsageMeter | None
    vault: Path | None
    fast: FastLane | None = None
    store: Store | None = None  # closed by close() when setup() opened it
    home: Path | None = None  # GOS_HOME (saved plans' JSON)
    personas: Personas | None = None  # the persona packs (WO-5.6)

    def lane(self) -> FastLane | None:
        """A fast lane with the stored key (None: no key stored)."""
        lane = FastLane.from_keyring(self.registry, meter=self.meter, vault=self.vault)
        if lane is not None and self.personas is not None:
            lane.persona = self.personas.block
        return lane

    def asker(self, ai: AiMode | None = None) -> Asker:
        """``ai``: the core's no-AI mode (WO-2.8); None for none."""
        lane: FastLane | Callable[[], FastLane | None] = self.fast or self.lane
        return Asker(
            self.registry,
            self.deep,
            lane,
            ai=ai,
            home=self.home,
            personas=self.personas,
        )

    def close(self) -> None:
        if isinstance(self.deep, DeepLaneService):
            self.deep.stop()
        if self.store is not None:
            self.store.close()
            self.store = None


def setup(
    cfg: config.Config,
    *,
    store: Store | None = None,
    fake: bool = False,
    deep_lane: bool = True,
) -> Agents:
    """``store``: the core's own (else one is opened when the DB exists, for the meter).
    ``fake``: the fast lane runs :class:`gregtec.llm.fake.EchoClient` (no key, no cost) with no
    meter, so its simulated calls never reach ``llm_usage`` (the credit alerts, the TTFT data).
    ``deep_lane`` False (or ``fake``): handoffs are only recorded (:class:`HandoffRecorder`), as
    ``gos eval fast-lane`` needs; else the real deep lane (WO-2.4), started on the first
    handoff or by the core."""
    vault = cfg.gos_home / "vault"
    opened = None
    if store is None and access.db_path(cfg.gos_home).is_file():
        store = opened = Store(access.db_path(cfg.gos_home))
    meter = (
        UsageMeter(
            store, cfg.monthly_included_usd, cfg.credit_alerts_pct, cfg.cycle_day
        )
        if store is not None
        else None
    )
    deep: HandoffRecorder | DeepLaneService
    if fake or not deep_lane:
        deep = HandoffRecorder()
        registry = default_registry(cfg.gos_home, deep=deep)
    else:
        deep = DeepLaneService.for_home(cfg.gos_home, Settings.from_config(cfg), meter)
        registry = default_registry(cfg.gos_home, deep=deep)
        deep.registry = registry
    personas = Personas(
        vault if vault.is_dir() else None,
        default=cfg.persona_default,
        random_per_session=cfg.persona_random,
        state=cfg.data_dir / STATE_FILE,
    )
    if isinstance(deep, DeepLaneService):
        deep.persona = personas.context  # its UserPromptSubmit hook (plan 4.4)
    agents = Agents(
        registry,
        deep,
        meter,
        vault if vault.is_dir() else None,
        store=opened,
        home=cfg.gos_home,
        personas=personas,
    )
    if fake:
        agents.fast = FastLane(EchoClient(), registry, meter=None, vault=agents.vault)
        agents.fast.persona = personas.block
    return agents


def wait_deep(
    deep: DeepLaneService,
    out: TextIO,
    approve: Callable[[DeepTask], bool],
    timeout: float = DEEP_WAIT_S,
) -> list[DeepTask]:
    """Wait for every task this process handed to the deep lane; a task that pauses at its
    cap continues when ``approve`` says yes, else it is stopped. Prints each outcome."""
    done = []
    for task in deep.tasks():
        found = deep.wait(task.id, timeout)
        while found is not None and found.status == NEEDS_APPROVAL:
            if approve(found):
                deep.approve(found.id)
                found = deep.wait(found.id, timeout)
            else:
                deep.stop_task(found.id)
                found = deep.wait(found.id, 30)
        if found is None:
            continue
        print(
            f"deep lane {found.id}: {found.status}, ${found.cost_usd:.4f} "
            f"(cap ${found.cap_usd:.2f}), {found.turns} turns",
            file=out,
        )
        if found.text:
            print(found.text, file=out)
        if found.error:
            print(f"  error: {found.error}", file=out)
        done.append(found)
    return done


def _ask_approval(task: DeepTask) -> bool:
    """Ask at the terminal whether a paused task may spend another cap (no terminal: no)."""
    if not sys.stdin.isatty():
        return False
    answer = input(
        f"The deep-lane task paused at ${task.spent_usd:.2f} (cap ${task.cap_usd:.2f}). "
        "Approve another cap's worth? [y/N] "
    )
    return answer.strip().lower() in ("y", "yes")


def main(config_path: Path | None, text: str, as_json: bool, out: TextIO) -> int:
    """``gos ask "<text>"``: 0 answered, 1 an error or no-AI answer, 2 bad gos.toml. A request
    handed to the deep lane is waited for here (D157); with ``--json`` its outcome is the
    reply's ``deep`` list, and a task that pauses at its cap is stopped (JSON can't ask)."""
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    agents = setup(cfg)
    try:
        reply = agents.asker().ask(text)
        deep = agents.deep
        if isinstance(deep, DeepLaneService) and deep.tasks():
            if as_json:
                done = wait_deep(deep, io.StringIO(), lambda _task: False)
                data = {**reply.as_dict(), "deep": [t.as_dict() for t in done]}
                print(json.dumps(data, indent=2, ensure_ascii=False), file=out)
            else:
                print(reply.text, file=out)
                done = wait_deep(deep, out, _ask_approval)
            return 0 if done and all(t.status == DONE for t in done) else 1
    finally:
        agents.close()
    if as_json:
        print(json.dumps(reply.as_dict(), indent=2, ensure_ascii=False), file=out)
    else:
        print(reply.text, file=out)
        card = reply.card or {}
        if card.get("type") == "choices":
            for c in card["candidates"]:
                print(f"  {c['n']}. {c['name']} [{c['key']}]", file=out)
        elif card.get("tool"):
            print(f"  card: {card['type']} from {card['tool']}", file=out)
        ttft = "-" if reply.ttft_ms is None else f"{reply.ttft_ms} ms"
        checked = "numbers checked" if reply.checked else "NOT checked"
        print(
            f"  lane {reply.lane}, first token {ttft}, total {reply.total_ms} ms, "
            f"{reply.calls} model calls, {checked}",
            file=out,
        )
        if reply.error:
            print(f"  error: {reply.error}", file=out)
    return 1 if reply.lane == "no_ai" else 0
