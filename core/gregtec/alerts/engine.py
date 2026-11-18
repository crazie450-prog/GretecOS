"""The alert engine: telemetry rules with hysteresis, a cooldown and staleness (WO-7.5; plan 7.5,
7.9 "Pause caveat"; D46, D71, D76, R7; D280, D281).

:class:`AlertEngine` reads the live base (:class:`gregtec.bridge.live.Live`) once a second
(:meth:`AlertEngine.tick`, from the core's watcher) and keeps one alert per rule and subject:

- **Firing** takes the condition holding on fresh readings for the rule's ``hold_s``
  (:class:`~gregtec.alerts.rules.AlertRules`), and not within ``cooldown_s`` of the same alert
  clearing. **Clearing** takes the condition gone, on fresh readings, for ``clear_s``. Both
  holds run on message time: from when the core received the first message showing the state
  to when it received the newest one, so a silent OC adds nothing to them (plan 7.9: a gap is
  never extrapolated) and a single reading followed by silence never fires or clears. A store's
  fill has a band besides: it fires below ``power_low_pct`` and clears only at
  ``power_clear_pct``. So a value flapping around a threshold fires once, and a flapping
  condition at most once per ``cooldown_s``.
- **Stale, never alerted on.** While a binding's game is paused (the watchers' state, D258,
  with no OC message since the pause began) or its OC computers are silent
  (:data:`~gregtec.bridge.telemetry.STALE_S`), nothing fires or clears there: a firing alert is
  kept and marked ``stale`` (``paused`` or ``silent``), and a condition's timers start again on
  the next fresh reading, since a gap is never extrapolated (plan 7.9). One stale reading (one
  computer of several, a reading the agent left out) holds only its own alerts.
- **Crafting done** is an event: a craft GOS started (WO-7.1's ``craft``) reported ``done``
  fires once, as ``info``, and stays shown for ``done_show_s``.

**Where alerts go** (D281). The dashboard: every firing ``warn``/``err`` alert as an
:func:`AlertCard <gregtec.cards.build.alert>` in ``/api/state`` (Now, Glance), and each binding's
alerts and held state with ``GET /api/base/live`` (the Base view, crafting done included). The
OC status board: power low and crafting done lines (maintenance and stalled machines are on it
already as WO-7.4's problems). A voice line: each event is pushed on ``/ws`` as
``{"type": "alert", "data": event}`` and the voice client speaks a ``fired`` event's ``say``.
Fires of one tick are said as one line per binding (the first fired event carries it, the
others ``say`` None): a power loss that stops every multiblock is "Lab: power low and 6
machines stopped.", not seven lines. The ``alert`` table
(Appendix D): one row per fired alert, ``state`` ``firing`` then ``cleared``; a row still
``firing`` when the engine starts (the core stopped while it fired) becomes ``lost``, since
the new engine fires afresh in a row of its own if the condition is still there.

**WO-9.2's interface.** :attr:`AlertEngine.sinks`: each is called with every
:class:`AlertEvent` (``fired``, ``cleared``, ``stale``, ``fresh``), after the engine's own
state has moved, outside its lock, a failing sink logged and skipped. Routing to Discord and
the phone, quiet hours and dedupe across channels belong to WO-9.2; :meth:`AlertEngine.active`
gives what is firing now (to rebuild after a restart or a missed event).

Game text (machine and item names) reaches the titles and the voice line as data: fixed
templates, never instructions (plan 0.1 principle 8).
"""

from __future__ import annotations

import dataclasses
import logging
import math
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from gregtec.alerts.rules import LEVEL, RULES, AlertRules, Seen, observe, place
from gregtec.bridge.live import Live
from gregtec.cards import build as cards_build
from gregtec.store import Store

log = logging.getLogger("gregtec.alerts")

SETTLE_S = 30.0  # see AlertEngine.settled: a few telemetry messages past a rule's hold
PAUSE_GRACE_S = 5.0  # a message this soon after the pause line was written before it
DONE_KEPT = 256  # crafting-done ids remembered, so each fires once
EVENTS = ("fired", "cleared", "stale", "fresh")
SOURCE = "OC telemetry"

# binding -> when its game paused (epoch s; math.inf: paused, start unknown), or None
PausedSince = Callable[[str], float | None]


@dataclass
class Alert:
    key: str  # <rule>:<subject>
    rule: str
    binding: str
    subject: str
    level: str  # err, warn, info
    title: str
    body: str
    say: str
    cite: str
    fired_at: float
    value: float | None = None
    unit: str | None = None
    stale: str | None = None  # None: fresh; "paused" or "silent": held
    row: int | None = None  # its alert table row

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "rule": self.rule,
            "binding": self.binding,
            "subject": self.subject,
            "level": self.level,
            "title": self.title,
            "body": self.body,
            "say": self.say,
            "cite": self.cite,
            "fired_at": int(self.fired_at),
            "value": self.value,
            "unit": self.unit,
            "stale": self.stale,
        }

    def card(self) -> dict[str, Any]:
        """The dashboard's AlertCard (warn and err only)."""
        body = self.body
        if self.stale:
            why = "the game is paused" if self.stale == "paused" else "OC is silent"
            body = f"Held: {why}, so this is the last fresh reading, not a current one. {body}"
        return cards_build.alert(
            f"oc-{self.key}",
            self.level,
            self.title,
            body,
            value=self.value,
            unit=self.unit,
            source=SOURCE,
            ts=int(self.fired_at),
        )


@dataclass(frozen=True)
class AlertEvent:
    kind: str  # fired, cleared, stale, fresh
    binding: str
    ts: float
    alert: Alert | None = None  # fired, cleared: a copy as it was then
    why: str | None = None  # stale: paused or silent
    say: str | None = None  # fired: the line to say for this tick's fires (see tick)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": self.kind,
            "binding": self.binding,
            "ts": int(self.ts),
            "why": self.why,
            "say": self.say if self.kind == "fired" else None,
            "alert": self.alert.as_dict() if self.alert is not None else None,
        }


Sink = Callable[[AlertEvent], None]


@dataclass
class _Track:
    cond_since: float | None = None
    ok_since: float | None = None
    firing: Alert | None = None
    cleared_at: float | None = None


def _texts(rule: str, where: str, s: Seen, cfg: AlertRules) -> tuple[str, str, str]:
    """(title, body, say) for a continuous rule's alert: fixed templates around the readings."""
    if rule == "power_low":
        pct = f"{s.value:.1f}" if s.value is not None else "?"
        return (
            f"Power low: {s.name}",
            (
                f"{where}: {pct} % stored (fires below {cfg.power_low_pct:g} %, clears at "
                f"{cfg.power_clear_pct:g} %). {s.cite}"
            ),
            f"{where}: power low. {s.name} at {pct} percent.",
        )
    if rule == "maintenance":
        if s.value is None:
            what = "needs maintenance"
        else:
            n = int(s.value)
            what = f"{n} maintenance problem{'s' if n != 1 else ''}"
        return (
            f"Maintenance: {s.name}",
            f"{where}: {what}, from its sensor lines. {s.cite}",
            f"{where}: {s.name} needs maintenance.",
        )
    word = s.word or "stalled"
    why = (
        "has a job and work allowed, but isn't running (power, input or a full output)"
        if word == "stalled"
        else "work disabled and not by GOS (GT shuts a multiblock down on power loss or "
        "maintenance; or by hand)"
    )
    return (
        f"Machine {word}: {s.name}",
        f"{where}: {why}. {s.cite}",
        f"{where}: {s.name} has {word}.",
    )


def _named(a: Alert) -> str:
    return a.title.split(": ", 1)[-1]


def summary(where: str, fired: list[Alert]) -> str:
    """One voice line for several alerts that fired in one tick on one binding: fixed
    templates, a count where a rule fired more than once."""
    parts = []
    for rule in RULES:
        mine = [a for a in fired if a.rule == rule]
        n = len(mine)
        if not n:
            continue
        if rule == "power_low":
            parts.append("power low" if n == 1 else f"power low on {n} stores")
        elif rule == "maintenance":
            parts.append(
                f"{_named(mine[0])} needs maintenance"
                if n == 1
                else f"{n} machines need maintenance"
            )
        elif rule == "machine_stalled":
            words = sorted({a.title.split(":", 1)[0].split()[-1] for a in mine})
            word = " or ".join(words)
            parts.append(
                f"{_named(mine[0])} has {word}" if n == 1 else f"{n} machines {word}"
            )
        else:
            parts.append(f"{_named(mine[0])} crafted" if n == 1 else f"{n} crafts done")
    said = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    return f"{where}: {said}."


def _lines(events: list[AlertEvent]) -> list[AlertEvent]:
    """Each binding's fires of one tick get one voice line, on the first of them."""
    out = []
    done: set[str] = set()
    for e in events:
        if e.kind != "fired" or e.alert is None:
            out.append(e)
            continue
        if e.binding in done:
            out.append(dataclasses.replace(e, say=None))
            continue
        done.add(e.binding)
        mine = [
            x.alert
            for x in events
            if x.kind == "fired" and x.binding == e.binding and x.alert is not None
        ]
        say = mine[0].say if len(mine) == 1 else summary(place(e.binding), mine)
        out.append(dataclasses.replace(e, say=say))
    return out


class AlertEngine:
    """The alerts for every binding (see the module docstring). Thread-safe."""

    def __init__(
        self,
        live: Live,
        rules: AlertRules | None = None,
        *,
        paused: PausedSince | None = None,
        store: Store | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.live = live
        self.rules = rules or AlertRules()
        self.paused = paused
        self.store = store
        self.clock = clock or live.clock
        self.sinks: list[Sink] = []
        self._lock = threading.Lock()
        self._tracks: dict[tuple[str, str], _Track] = {}  # (binding, key)
        self._held: dict[str, str | None] = {}  # binding -> why it is held, or None
        self._fresh: dict[
            str, float
        ] = {}  # binding -> evaluated on fresh readings since
        self._done: dict[str, Alert] = {}  # crafting-done alerts still shown
        self._done_seen: OrderedDict[str, None] = OrderedDict()
        if store is not None:
            self._lose_old_rows(store)

    # --- reading

    def held(self, binding: str) -> str | None:
        with self._lock:
            return self._held.get(binding)

    def settled(self, binding: str, rule: str) -> bool:
        """Whether ``binding`` has been evaluated on fresh readings in this run for longer than
        ``rule`` needs to fire (its hold, plus :data:`SETTLE_S` for the readings' cadence): an
        alert of that rule still firing would have fired again by now. The alert router asks
        this before it forgets what was firing before a restart (WO-9.2)."""
        with self._lock:
            since = self._fresh.get(binding)
        return (
            since is not None
            and self.clock() - since >= self.rules.hold_s(rule) + SETTLE_S
        )

    def active(self, binding: str | None = None) -> list[Alert]:
        """Every firing alert (held ones included) and the crafting done still shown, worst
        first, newest first within a level."""
        with self._lock:
            found = [t.firing for t in self._tracks.values() if t.firing is not None]
            found += list(self._done.values())
        found = [dataclasses.replace(a) for a in found if binding in (None, a.binding)]
        order = ("err", "warn", "info")
        return sorted(found, key=lambda a: (order.index(a.level), -a.fired_at))

    def cards(self) -> list[dict[str, Any]]:
        """The firing warn and err alerts as AlertCards, for ``/api/state``."""
        return [a.card() for a in self.active() if a.level in ("warn", "err")]

    def for_binding(self, binding: str) -> dict[str, Any]:
        """What ``GET /api/base/live`` adds to a binding: its alerts and why they are held."""
        return {
            "held": self.held(binding),
            "alerts": [a.as_dict() for a in self.active(binding)],
        }

    def board(self, binding: str) -> list[tuple[str, str]]:
        """Status-board lines (text, level) for ``binding``: power low and crafting done, the
        two the board doesn't already show as WO-7.4's problems."""
        out = []
        for a in self.active(binding):
            if a.rule == "power_low":
                pct = f"{a.value:.1f}%" if a.value is not None else "?%"
                out.append((f"LOW  {a.title[len('Power low: ') :]} {pct}", "bad"))
            elif a.rule == "crafting_done":
                out.append((f"DONE {a.title[len('Crafting done: ') :]}", "ok"))
        return out

    # --- evaluating

    def tick(self) -> list[AlertEvent]:
        """Evaluate every binding that has reported; send the events; return them."""
        snap = self.live.snapshot()
        now = self.clock()
        events: list[AlertEvent] = []
        with self._lock:
            for base in snap["bindings"]:
                events += self._binding(base, now)
            for key, a in list(self._done.items()):
                if now - a.fired_at >= self.rules.done_show_s:
                    del self._done[key]
                    events.append(
                        AlertEvent("cleared", a.binding, now, dataclasses.replace(a))
                    )
        events = _lines(events)
        self._record(events)
        for e in events:
            for sink in list(self.sinks):
                try:
                    sink(e)
                except Exception:
                    log.exception("alert sink failed", extra={"event": e.kind})
        return events

    def _why_held(self, base: Mapping[str, Any], now: float) -> str | None:
        binding = base["binding"]
        since = self.paused(binding) if self.paused is not None else None
        age = base.get("age_s")
        last = now - age if isinstance(age, int | float) else None
        if since is not None and (
            math.isinf(since) or last is None or last <= since + PAUSE_GRACE_S
        ):
            return "paused"
        if base.get("status") != "connected":
            return "silent"
        return None

    def _binding(self, base: Mapping[str, Any], now: float) -> list[AlertEvent]:
        binding = base["binding"]
        events: list[AlertEvent] = []
        why = self._why_held(base, now)
        before = self._held.get(binding)
        self._held[binding] = why
        if why is None:
            self._fresh.setdefault(binding, now)
        else:
            self._fresh.pop(binding, None)
        if why != before and (why is None) != (before is None):
            events.append(
                AlertEvent("stale" if why else "fresh", binding, now, why=why)
            )
        mine = {k: t for (b, k), t in self._tracks.items() if b == binding}
        heard = [a["at"] for a in base.get("agents") or [] if a.get("at") is not None]
        newest = max(heard) if heard else now
        if why is not None:
            for t in mine.values():
                t.cond_since = t.ok_since = None
                if t.firing is not None:
                    t.firing.stale = why
            return events
        seen = observe(
            base,
            self.rules,
            lambda rule, subject: (
                (t := mine.get(f"{rule}:{subject}")) is not None
                and t.firing is not None
            ),
        )
        where = place(binding)
        keys = set()
        for s in seen:
            key = f"{s.rule}:{s.subject}"
            keys.add(key)
            track = self._tracks.setdefault((binding, key), _Track())
            event = self._step(track, binding, key, s, where, now)
            if event is not None:
                events.append(event)
        # A full snapshot left it out (the machine is gone): its condition is gone too.
        for key, t in mine.items():
            if key not in keys and t.firing is not None:
                gone = Seen(
                    t.firing.rule, t.firing.subject, False, "", t.firing.cite, at=newest
                )
                event = self._step(t, binding, key, gone, where, now)
                if event is not None:
                    events.append(event)
        for key, t in mine.items():
            idle = t.firing is None and t.cond_since is None
            if idle and (
                t.cleared_at is None or now - t.cleared_at >= self.rules.cooldown_s
            ):
                del self._tracks[(binding, key)]
        if "crafting_done" in self.rules.rules:
            events += self._crafts(base, where, now)
        return events

    def _step(
        self, t: _Track, binding: str, key: str, s: Seen, where: str, now: float
    ) -> AlertEvent | None:
        cfg = self.rules
        if (
            s.on is None
        ):  # no fresh reading of it: hold, and start the timers again later
            t.cond_since = t.ok_since = None
            if t.firing is not None:
                t.firing.stale = "silent"
            return None
        if t.firing is not None:
            t.firing.stale = None
        at = (
            s.at if s.at is not None else now
        )  # message time: when the core received it
        if s.on:
            t.ok_since = None
            if t.firing is not None:
                title, body, say = _texts(s.rule, where, s, cfg)
                t.firing.title, t.firing.body, t.firing.value = title, body, s.value
                return None
            if t.cond_since is None:
                t.cond_since = at
            cooled = t.cleared_at is None or now - t.cleared_at >= cfg.cooldown_s
            if at - t.cond_since >= cfg.hold_s(s.rule) and cooled:
                title, body, say = _texts(s.rule, where, s, cfg)
                t.firing = Alert(
                    key,
                    s.rule,
                    binding,
                    s.subject,
                    LEVEL[s.rule],
                    title,
                    body,
                    say,
                    s.cite,
                    now,
                    s.value,
                    s.unit,
                )
                return AlertEvent(
                    "fired", binding, now, dataclasses.replace(t.firing), say=say
                )
            return None
        t.cond_since = None
        if t.firing is None:
            return None
        if t.ok_since is None:
            t.ok_since = at
        if at - t.ok_since < cfg.clear_s:
            return None
        gone, t.firing, t.ok_since, t.cleared_at = t.firing, None, None, now
        return AlertEvent("cleared", binding, now, gone)

    def _crafts(
        self, base: Mapping[str, Any], where: str, now: float
    ) -> list[AlertEvent]:
        binding = base["binding"]
        events = []
        ae2 = base.get("ae2") or {}
        for c in ae2.get("crafts") or []:
            if c.get("state") != "done" or c.get("stale") or c.get("id") is None:
                continue
            key = f"crafting_done:{binding}/{c['id']}"
            if key in self._done_seen:
                continue
            self._done_seen[key] = None
            while len(self._done_seen) > DONE_KEPT:
                self._done_seen.popitem(last=False)
            what = f"{c.get('amount')} {c.get('name')}"
            alert = Alert(
                key,
                "crafting_done",
                binding,
                f"{binding}/{c['id']}",
                LEVEL["crafting_done"],
                f"Crafting done: {what}",
                f"{where}: AE2 finished the craft GOS started (action {c['id']}). {c['cite']}",
                f"{where}: crafting done, {what}.",
                c["cite"],
                now,
                c.get("amount") if isinstance(c.get("amount"), int | float) else None,
                None,
            )
            self._done[key] = alert
            events.append(
                AlertEvent(
                    "fired", binding, now, dataclasses.replace(alert), say=alert.say
                )
            )
        return events

    # --- the alert table (Appendix D)

    @staticmethod
    def _lose_old_rows(store: Store) -> None:
        """Rows a previous engine left ``firing`` (the core stopped while they fired) become
        ``lost``: no engine will clear them, and WO-9.2 reads ``firing`` as firing now."""
        marks = ", ".join("?" for _ in RULES)
        try:
            store.try_write(
                "UPDATE alert SET state = 'lost' "
                f"WHERE state = 'firing' AND kind IN ({marks})",
                RULES,
            )
        except sqlite3.Error as exc:
            log.warning("old alert rows not closed", extra={"why": str(exc)[:200]})

    def _record(self, events: list[AlertEvent]) -> None:
        if self.store is None:
            return
        for e in events:
            a = e.alert
            if a is None or e.kind not in ("fired", "cleared"):
                continue
            try:
                if e.kind == "fired":
                    with self.store.transaction() as conn:
                        cur = conn.execute(
                            "INSERT INTO alert(ts, kind, level, msg, ref, state) "
                            "VALUES (?, ?, ?, ?, ?, 'firing')",
                            (
                                int(a.fired_at),
                                a.rule,
                                a.level,
                                f"{a.title}. {a.body}"[:1000],
                                a.cite,
                            ),
                        )
                        row = cur.lastrowid
                    with self._lock:
                        self._set_row(a.key, a.binding, row)
                else:
                    row = a.row
                    if row is not None:
                        self.store.try_write(
                            "UPDATE alert SET state = 'cleared' WHERE id = ?", (row,)
                        )
            except sqlite3.Error as exc:
                log.warning("alert not recorded", extra={"why": str(exc)[:200]})

    def _set_row(self, key: str, binding: str, row: int | None) -> None:
        t = self._tracks.get((binding, key))
        if t is not None and t.firing is not None:
            t.firing.row = row
        elif key in self._done:
            self._done[key].row = row
