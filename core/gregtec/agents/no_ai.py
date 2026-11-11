"""No-AI mode (plan 7.16, QA:A1) and the credits flow (D47, D48): WO-2.8, D164-D166.

:class:`AiMode` says whether Gregtec OS may call a model now, and why not when it may not:

- **no key** stored in the keyring (the core's key check, every 30 s);
- **the API refused or failed**: 401/403 (the key is wrong, revoked or not allowed), a network
  failure or timeout, a 5xx or overload, or the API's own "credit balance is too low" and spend
  limit errors (:func:`classify`; a plain 429 or a bad request is not an outage);
- **the credit pause** (D48): the cycle's spend crossed the last of ``[budget] credit_alerts_pct``
  (100%), or Clayton chose "No-AI mode" on a credit alert;
- **Clayton's own switch** (Settings: "Turn the AI off").

While it is off the router still answers instant commands, Search, recipe cards, uses, BOMs and
saved plans from SQLite with no model call (:class:`gregtec.agents.ask.Asker` asks
:meth:`AiMode.allowed` first), and anything else gets :data:`NEEDS_AI`, which says what needs the
AI. **Leaving it** (plan 7.16): after an API problem, automatically when the API answers again
(:meth:`AiMode.probe_due` + a free ``models.list`` call, or the next real request once a minute:
only a real request can tell whether credits are back); after a stored key appears, at the next
key check; after the credit pause, only by Clayton's answer ("Resume AI") or when the next cycle's
credits arrive; after his own switch, when he switches back.

:class:`CreditWatch` meters the cycle (``llm_usage``, D118) against ``[budget]
monthly_included_usd``: each threshold crossed raises the banner state once per cycle and waits
for an answer (:data:`CHOICES`: ``billing``, ``no_ai``, ``stop``, plus ``resume``); crossing the
last one switches to no-AI mode until he answers (D48). The answer is the decision API that the
dashboard's banner calls now and WO-9.2's Discord buttons will call. Its state and the AI switch
are kept in the core DB's ``kv`` table, so a restart neither forgets a pause nor raises a crossed
threshold twice. GOS never buys credits.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import anthropic
from keyring.errors import KeyringError

from gregtec.llm.smoke import CREDIT_LOW, TIER_CAP, _error_parts, explain_api_error
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

log = logging.getLogger(__name__)

# Why the AI is off, in the order the banner names them (the first that applies).
CHOICE, CREDITS, NO_KEY, AUTH, CREDITS_API, NETWORK, OUTAGE = (
    "choice",
    "credits",
    "no_key",
    "auth",
    "credits_api",
    "network",
    "outage",
)
REASONS = (CHOICE, CREDITS, NO_KEY, AUTH, CREDITS_API, NETWORK, OUTAGE)
# The API problems that clear by themselves once the API answers again (plan 7.16).
API_PROBLEMS = frozenset({AUTH, CREDITS_API, NETWORK, OUTAGE})
LABELS = {
    CHOICE: "you turned the AI off",
    CREDITS: "paused for credits",
    NO_KEY: "no Anthropic key is stored",
    AUTH: "the API refused the key",
    CREDITS_API: "the API says the credits are used up",
    NETWORK: "the API can't be reached",
    OUTAGE: "the API is down or overloaded",
}
WORKS = "instant commands, Search, recipe cards, uses, BOMs and saved plans"
NEEDS_AI = (
    "That needs the AI (design, advice, explanations, screen reading and wiki research), "
    "and it is offline: {why}. I can do lookups: recipes, uses, items, quests and saved plans."
)
PROBE_S = (
    60.0  # how often an API problem is checked again, by a probe or a real request
)
KV_CHOICE = "ai.choice"
KV_CREDITS = "credits.state"
# D48's "Open billing": the Console's billing page (plan 4.4).
BILLING_URL = "https://console.anthropic.com/settings/billing"
BILLING, NO_AI, STOP, RESUME = "billing", "no_ai", "stop", "resume"
CHOICES = (BILLING, NO_AI, STOP, RESUME)


@dataclass(frozen=True)
class Problem:
    reason: str  # one of API_PROBLEMS
    detail: str


def classify(exc: BaseException) -> Problem | None:
    """The API problem ``exc`` (or the error it wraps) shows, or None when it doesn't mean the
    AI is offline (a plain 429, a bad request: the next request may well work)."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, anthropic.APIError):
            break
        cur = cur.__cause__ or cur.__context__
    if not isinstance(cur, anthropic.APIError):
        return None
    if isinstance(cur, anthropic.APIConnectionError):  # APITimeoutError is one too
        return Problem(NETWORK, f"no answer from the API ({type(cur).__name__})")
    if not isinstance(cur, anthropic.APIStatusError):
        return None
    status = cur.status_code
    message, code = _error_parts(cur)
    lowered = message.lower()
    detail = explain_api_error(status, message, code)[:300]
    money = (
        CREDIT_LOW.search(lowered)
        or code in ("billing_error", TIER_CAP)
        or "api usage limits" in lowered
        or status == 402
    )
    if money:
        return Problem(CREDITS_API, detail)
    if status in (401, 403):
        return Problem(AUTH, detail)
    if status >= 500:  # 500, 529 overloaded
        return Problem(OUTAGE, detail)
    return None


def probe_api(key: str) -> Problem | None:
    """Whether the API answers with ``key``: ``models.list``, which costs nothing. It can't see
    credits (a model call can), so a credits problem is left for a real request to clear."""
    client = anthropic.Anthropic(
        api_key=key, timeout=anthropic.Timeout(10.0, connect=3.0), max_retries=0
    )
    try:
        client.models.list(limit=1)
    except anthropic.APIError as exc:
        found = classify(exc)
        return found or Problem(OUTAGE, explain_api_error(None, str(exc))[:300])
    finally:
        client.close()
    return None


def _kv(store: Store | None, key: str) -> str | None:
    if store is None:
        return None
    rows = store.rows("SELECT value FROM kv WHERE key = ?", (key,))
    return rows[0]["value"] if rows else None


def _set_kv(store: Store | None, key: str, value: str | None) -> None:
    if store is None:
        return
    with store.transaction() as conn:
        if value is None:
            conn.execute("DELETE FROM kv WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO "
                "UPDATE SET value = excluded.value, upd = excluded.upd",
                (key, value, int(time.time())),
            )


class AiMode:
    """Whether a model may be called now, and why not. Thread-safe; the asker, the core's
    watcher and the routes share one."""

    def __init__(
        self,
        store: Store | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.clock = clock
        self.wall = wall
        self._lock = threading.Lock()
        self.key_stored: bool | None = None  # None: not checked yet (counts as stored)
        self.problem: Problem | None = None
        self.problem_since: float | None = None  # wall clock
        self._tried: float = 0.0  # clock of the last check of the problem
        self.credit_pause = False  # set by CreditWatch (D48)
        self.choice_off = _kv(store, KV_CHOICE) == "off"
        self.on_change: Callable[[], None] | None = None

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # a push that fails mustn't fail the request
                log.exception("ai mode push failed")

    def reasons(self) -> list[str]:
        with self._lock:
            out = []
            if self.choice_off:
                out.append(CHOICE)
            if self.credit_pause:
                out.append(CREDITS)
            if self.key_stored is False:
                out.append(NO_KEY)
            if self.problem is not None:
                out.append(self.problem.reason)
            return out

    def allowed(self) -> bool:
        """May a model be called now? An API problem alone lets one request through a minute
        (:meth:`probe_due` decides), so a real request can find the API back."""
        with self._lock:
            if self.choice_off or self.credit_pause or self.key_stored is False:
                return False
            if self.problem is None:
                return True
            now = self.clock()
            if now - self._tried >= PROBE_S:
                self._tried = now
                return True
            return False

    def probe_due(self) -> bool:
        """Whether the watcher should probe the API now: an API problem that a free probe can
        clear (not credits: only a model call sees those), checked at most once a minute."""
        with self._lock:
            if self.problem is None or self.problem.reason == CREDITS_API:
                return False
            if self.choice_off or self.credit_pause or self.key_stored is False:
                return False
            now = self.clock()
            if now - self._tried < PROBE_S:
                return False
            self._tried = now
            return True

    def key_seen(self, stored: bool) -> None:
        with self._lock:
            changed = stored != self.key_stored
            self.key_stored = stored
            if changed and stored and self.problem is not None:
                # A new key may well be the fix: check it at once.
                self._tried = self.clock() - PROBE_S
        if changed:
            self._changed()

    def failed(self, exc: BaseException) -> Problem | None:
        """A model call failed: note the problem it shows, if it means the AI is offline."""
        found = classify(exc)
        if found is None:
            return None
        with self._lock:
            fresh = self.problem is None or self.problem.reason != found.reason
            if self.problem is None:
                self.problem_since = self.wall()
            self.problem = found
            self._tried = self.clock()
        if fresh:
            log.warning(
                "AI offline", extra={"reason": found.reason, "detail": found.detail}
            )
            self._changed()
        return found

    def succeeded(self) -> None:
        """The API answered: an API problem is over (plan 7.16: automatically)."""
        with self._lock:
            had = self.problem
            self.problem, self.problem_since = None, None
        if had is not None:
            log.info("AI back online", extra={"was": had.reason})
            self._changed()

    def probe(self, check: Callable[[], Problem | None]) -> None:
        """Run ``check`` (e.g. :func:`probe_api` with the stored key) and note its result."""
        try:
            found = check()
        except (OSError, LookupError, KeyringError, anthropic.APIError) as exc:
            # the keyring (no key, locked), the network: still offline
            log.info("AI probe failed", extra={"error": type(exc).__name__})
            return
        if found is None:
            self.succeeded()
            return
        with self._lock:
            # A probe can't see credits: an auth or network problem it finds replaces the one
            # noted, anything else is kept.
            if self.problem is not None and self.problem.reason != found.reason:
                self.problem = found

    def set_credit_pause(self, on: bool) -> None:
        with self._lock:
            changed = on != self.credit_pause
            self.credit_pause = on
        if changed:
            log.info("credit pause", extra={"on": on})
            self._changed()

    def set_choice(self, online: bool) -> None:
        """Clayton's own switch (plan 7.16): off holds until he turns it back on."""
        with self._lock:
            changed = online == self.choice_off
            self.choice_off = not online
        _set_kv(self.store, KV_CHOICE, None if online else "off")
        if changed:
            self._changed()

    def status(self) -> dict[str, Any]:
        """``/api/state``'s ``ai``: online, why not (the first reason), every reason, since when,
        and what still works."""
        reasons = self.reasons()
        with self._lock:
            problem, since = self.problem, self.problem_since
        if not reasons:
            return {
                "online": True,
                "reason": None,
                "reasons": [],
                "detail": "online",
                "since": None,
                "works": WORKS,
            }
        first = reasons[0]
        detail = LABELS[first]
        if problem is not None and first == problem.reason:
            detail = f"{detail}: {problem.detail}"
        return {
            "online": False,
            "reason": first,
            "reasons": reasons,
            "detail": detail[:500],
            "since": int(since)
            if since is not None and first in API_PROBLEMS
            else None,
            "works": WORKS,
        }

    def why(self) -> str:
        """The reason the AI is off, for :data:`NEEDS_AI`."""
        reasons = self.reasons()
        return LABELS[reasons[0]] if reasons else "it isn't answering"


@dataclass
class _Cycle:
    """The credit flow's state for one cycle, as kept in ``kv``."""

    start: str  # the cycle's start (ISO), as UsageMeter.cycle says it
    raised: list[int]  # thresholds whose crossing was raised this cycle
    answers: dict[str, str]  # threshold (as text) -> billing | no_ai | stop | resume
    hold: bool = False  # no-AI until "resume" or the next cycle (D48, plan 7.16)

    def as_json(self) -> str:
        return json.dumps(
            {
                "start": self.start,
                "raised": self.raised,
                "answers": self.answers,
                "hold": self.hold,
            }
        )

    @classmethod
    def load(cls, raw: str | None) -> _Cycle | None:
        try:
            d = json.loads(raw) if raw else None
            if not isinstance(d, dict):
                return None
            return cls(
                str(d["start"]),
                [int(x) for x in d.get("raised", [])],
                {str(k): str(v) for k, v in dict(d.get("answers", {})).items()},
                bool(d.get("hold", False)),
            )
        except (ValueError, KeyError, TypeError):
            return None


class CreditWatch:
    """D48: the cycle's spend against the included credits; a banner per threshold crossed,
    the no-AI switch at the last one, and the answer (:meth:`decide`)."""

    def __init__(
        self,
        meter: UsageMeter,
        mode: AiMode,
        *,
        stop: Callable[[], None] | None = None,
    ) -> None:
        self.meter = meter
        self.mode = mode
        self.stop = stop  # D48 "Stop": shut GOS down as `gos stop` does
        self._lock = threading.Lock()
        self._cycle: _Cycle | None = _Cycle.load(_kv(meter.store, KV_CREDITS))
        self._last: dict[str, Any] | None = None  # the newest cycle summary
        self.on_raise: Callable[[int, dict[str, Any]], None] | None = None
        if self._cycle is not None:
            mode.set_credit_pause(self._cycle.hold)

    def _save(self) -> None:
        assert self._cycle is not None
        _set_kv(self.meter.store, KV_CREDITS, self._cycle.as_json())

    def check(self, now: datetime | None = None) -> dict[str, Any]:
        """Read the cycle; raise each newly crossed threshold once and, at the last one, pause
        the AI unless Clayton already resumed it at that threshold. A new cycle starts afresh
        ("the next cycle's credits arrive"). Returns :meth:`state`."""
        cycle = self.meter.cycle(now)
        raised: list[int] = []
        with self._lock:
            self._last = cycle
            if self._cycle is None or self._cycle.start != cycle["cycle_start"]:
                self._cycle = _Cycle(cycle["cycle_start"], [], {})
                dirty = True
            else:
                dirty = False
            top = max(self.meter.alerts_pct) if self.meter.alerts_pct else None
            for pct in sorted(cycle["crossed_pct"]):
                if pct in self._cycle.raised:
                    continue
                self._cycle.raised.append(pct)
                raised.append(pct)
                dirty = True
                if pct == top and self._cycle.answers.get(str(pct)) != RESUME:
                    self._cycle.hold = True
            if dirty:
                self._save()
            hold = self._cycle.hold
        self.mode.set_credit_pause(hold)
        for pct in raised:
            log.warning(
                "credit alert", extra={"pct": pct, "spent_usd": cycle["spent_usd"]}
            )
            if self.on_raise is not None:
                self.on_raise(pct, cycle)
        return self.state()

    def decide(self, choice: str) -> dict[str, Any]:
        """Clayton's answer to the credit alert (D48's buttons, plus "Resume AI", plan 7.16):

        - ``billing``: noted; the caller opens :data:`BILLING_URL`. Nothing else changes, so a
          pause holds and the banner offers "Resume AI".
        - ``no_ai``: no-AI mode until he resumes or the next cycle starts.
        - ``resume``: the AI comes back for this cycle (the last threshold, once crossed, doesn't
          pause it again; a later threshold still does).
        - ``stop``: noted, then GOS stops (:attr:`stop`); any pause holds over the restart.

        ValueError for any other choice. Returns :meth:`state`."""
        if choice not in CHOICES:
            raise ValueError(f"choice must be one of {', '.join(CHOICES)}")
        if self._last is None:
            self.check()
        with self._lock:
            assert self._cycle is not None
            at = max(self._cycle.raised) if self._cycle.raised else 0
            self._cycle.answers[str(at)] = choice
            if choice == NO_AI:
                self._cycle.hold = True
            elif choice == RESUME:
                self._cycle.hold = False
            self._save()
            hold = self._cycle.hold
        log.info("credit decision", extra={"choice": choice, "at_pct": at})
        self.mode.set_credit_pause(hold)
        if choice == RESUME and self.mode.choice_off:
            self.mode.set_choice(
                True
            )  # "Resume AI" means the AI, whatever turned it off
        state = self.state()
        if choice == STOP and self.stop is not None:
            self.stop()
        return state

    def state(self) -> dict[str, Any]:
        """``/api/state``'s ``credits``: the banner's level, the threshold it is about, whether it
        waits for an answer, the pause, and what the buttons do."""
        with self._lock:
            cycle, mine = self._last, self._cycle
            if cycle is None or mine is None:
                return {"level": None, "threshold": None, "pending": False}
            top = max(self.meter.alerts_pct) if self.meter.alerts_pct else None
            at = max(mine.raised) if mine.raised else None
            answer = mine.answers.get(str(at)) if at is not None else None
            hold = mine.hold
        level = None if at is None else "err" if at == top else "warn"
        if hold:
            level = "err"
        actions = []
        if at is not None or hold:
            actions = [BILLING, RESUME if hold else NO_AI, STOP]
        return {
            "level": level,
            "threshold": at,
            "pct": cycle["pct"],
            "spent_usd": cycle["spent_usd"],
            "budget_usd": cycle["budget_usd"],
            "cycle_start": cycle["cycle_start"],
            "cycle_end": cycle["cycle_end"],
            "answer": answer,
            "pending": at is not None and answer is None,
            "paused": hold,
            "actions": actions,
            "billing_url": BILLING_URL,
        }
