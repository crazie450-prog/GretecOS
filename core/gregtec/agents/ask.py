"""One request, end to end (WO-2.3): router -> instant command, fast lane or deep lane (D150-D152).

:meth:`Asker.ask` is what ``POST /api/ask`` and ``gos ask`` run. It returns a :class:`Reply`:
the text to say, a card for the dashboard (data, never HTML: WO-2.6 draws it), and how the
answer was made (lane, citations, whether its numbers were checked, time to first token).

- An unsure item match asks (QA:D11): the reply is the question, its card the top three, and the
  next request may pick one ("the second one", "2", or its name), which re-runs the first request
  on that item. "Repeat" says the question again and keeps it open; anything else starts afresh.
- With no fast lane (no key stored, or the API failing) a fast-lane request gets the instant
  answer for its card when pre-retrieval found one, else "AI is offline; I can do lookups"
  (plan 7.16; WO-2.8 adds the mode switch, banners and the credit flow).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO

from gregtec import config
from gregtec.agents import prefetch, router
from gregtec.agents.deep_lane import DeepLane, HandoffRecorder
from gregtec.agents.fast_lane import (
    NOT_CHECKED,
    FastLane,
    LaneAnswer,
    LaneError,
    refs,
)
from gregtec.agents.tools.catalog import default_registry
from gregtec.agents.tools.registry import Registry
from gregtec.agents.tools.tsv import Parsed
from gregtec.db import access
from gregtec.llm.fake import EchoClient
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

log = logging.getLogger(__name__)

TEXT_MAX = 1000  # characters of one request
LANE_RETRY_S = 30.0  # how often a missing lane (no key yet) is looked for again
AI_OFFLINE = "AI is offline; I can do lookups."
NOTHING_PENDING = "Nothing is waiting for confirmation."
NOTHING_TO_REPEAT = "I haven't said anything yet."
EMPTY = "Ask me something: a recipe, an item, a machine or a quest."
# NOT_CHECKED for a turn with no card to point at (the model looked things up itself)
NOT_CHECKED_NO_CARD = (
    "I can't give that answer with checked numbers. Ask for the item's recipe or uses and "
    "I'll show the rows with their ids."
)
HANDED_OFF = (
    "That's one for the deep lane; I've handed it over. It arrives with WO-2.4."
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
        }


@dataclass(frozen=True)
class _Pending:
    route: router.Route
    clarify: prefetch.Clarify


def _name(c: prefetch.Candidate | None) -> str:
    return (c.name or c.key) if c else ""


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


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
    if main.tool == "recipe_find":
        if first is None:
            return f"{name}: no recipe in the DB makes it."
        return (
            f"{name}: {_count(total, 'recipe')}, lowest tier first. The first is "
            f"{first['machine']} ({first['ref']})."
        )
    if main.tool == "recipe_uses":
        if first is None:
            return f"{name}: no recipe in the DB takes it."
        return (
            f"{name} goes into {_count(total, 'recipe')}. The first is {first['machine']} "
            f"({first['ref']})."
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
    ) -> None:
        self.registry = registry
        self.deep = deep
        self.clock = clock
        if callable(lane) and not isinstance(lane, FastLane):
            self._factory: Callable[[], FastLane | None] | None = lane
            self._lane: FastLane | None = None
        else:
            self._factory, self._lane = None, lane
        self._looked = -LANE_RETRY_S
        self._lock = threading.Lock()
        self._last: Reply | None = None
        self._pending: _Pending | None = None

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
        reply.total_ms = round((self.clock() - started) * 1000)
        if reply.ttft_ms is None and reply.calls == 0:
            reply.ttft_ms = reply.total_ms
        if route.intent != router.REPEAT:
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
            return self._instant(route)
        if route.kind == router.DEEP:
            item = self.deep.handoff(route.text, route.text, "router")
            return Reply(HANDED_OFF, router.DEEP, route.intent, handoff=item.as_dict())
        pre = prefetch.prefetch(self.registry, route)
        if pre.clarify is not None:
            return self._ask_back(route, pre)
        lane = self.lane()
        if lane is None:
            return self._offline(route, pre, None)
        try:
            answer = lane.answer(route.text, pre, started=started, task=task)
        except LaneError as exc:
            log.warning("fast lane failed", extra={"error": str(exc)[:200]})
            return self._offline(route, pre, str(exc))
        return self._fast(route, pre, answer)

    def _base(
        self, route: router.Route, pre: prefetch.Prefetch, lane: str, text: str
    ) -> Reply:
        return Reply(
            text,
            lane,
            route.intent,
            card=pre.main.card() if pre.main else None,
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
            return Reply(NOTHING_PENDING, router.INSTANT, intent)
        if intent == router.TIER:
            out = self.registry.call("quest_frontier", {"n": 1}, lane="fast")
            block = prefetch.Block("quest_frontier", {"n": 1}, out.text, out.is_error)
            if out.is_error:
                return Reply(
                    block.card()["error"], router.INSTANT, intent, card=block.card()
                )
            tier = block.parsed.head.get("tier", "unknown")
            return Reply(
                f"Your tier: {tier}.", router.INSTANT, intent, card=block.card()
            )
        pre = prefetch.prefetch(self.registry, route, tier=False)
        if pre.clarify is not None:
            return self._ask_back(route, pre)
        if pre.unresolved is not None:
            text = f"No item matches {pre.unresolved!r}; try other words."
        else:
            text = card_text(pre, intent) or "I couldn't look that up."
        return self._base(route, pre, router.INSTANT, text)


@dataclass
class Agents:
    """The tool registry, the deep-lane door, the meter and the fast lane, set up from
    gos.toml for the core, ``gos ask`` and ``gos eval fast-lane``."""

    registry: Registry
    deep: HandoffRecorder
    meter: UsageMeter | None
    vault: Path | None
    fast: FastLane | None = None
    store: Store | None = None  # closed by close() when setup() opened it

    def lane(self) -> FastLane | None:
        """A fast lane with the stored key (None: no key stored)."""
        return FastLane.from_keyring(self.registry, meter=self.meter, vault=self.vault)

    def asker(self) -> Asker:
        if self.fast is not None:
            return Asker(self.registry, self.deep, self.fast)
        return Asker(self.registry, self.deep, self.lane)

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None


def setup(
    cfg: config.Config, *, store: Store | None = None, fake: bool = False
) -> Agents:
    """``store``: the core's own (else one is opened when the DB exists, for the meter).
    ``fake``: the fast lane runs :class:`gregtec.llm.fake.EchoClient` (no key, no cost) with no
    meter, so its simulated calls never reach ``llm_usage`` (the credit alerts, the TTFT data)."""
    deep = HandoffRecorder()
    registry = default_registry(cfg.gos_home, deep=deep)
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
    agents = Agents(
        registry, deep, meter, vault if vault.is_dir() else None, store=opened
    )
    if fake:
        agents.fast = FastLane(EchoClient(), registry, meter=None, vault=agents.vault)
    return agents


def main(config_path: Path | None, text: str, as_json: bool, out: TextIO) -> int:
    """``gos ask "<text>"``: 0 answered, 1 an error or no-AI answer, 2 bad gos.toml."""
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    agents = setup(cfg)
    try:
        reply = agents.asker().ask(text)
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
        elif card.get("columns"):
            print(f"  card: {card['tool']}, rows {card.get('span')}", file=out)
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
