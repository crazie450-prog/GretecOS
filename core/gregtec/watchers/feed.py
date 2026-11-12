"""What the core keeps from the game watchers (WO-6.1, D257, D258): the paused state the dashboard
shows (``game.paused``) and the recent events, until WO-6.3's event store keeps them for good.

The watchers post batches with their latest paused state and a heartbeat every 10 s. The core
trusts that state only while the watchers are heard (:data:`HEARD_S`) and the instance they watch
has a running client: otherwise ``paused`` is None (unknown), as before WO-6.1, so a stopped
watcher can never leave the dashboard saying "paused". Event text is the game's, data only.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from gregtec.watchers.events import KINDS

HEARD_S = 30.0  # three missed heartbeats: the watchers are gone
KEEP = 500  # recent events kept in memory
DATA_MAX = 16 << 10  # one event's data, as JSON


@dataclass
class GameFeed:
    clock: Callable[[], float] = time.monotonic
    instance: str | None = None
    paused: bool | None = None
    since: float | None = None  # epoch seconds, from the watchers
    why: str | None = None
    heard: float | None = None  # self.clock() of the last post
    problems: list[str] = field(default_factory=list)
    refused: int = 0  # events dropped as malformed
    _events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=KEEP))
    _ids: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def receive(self, body: dict[str, Any]) -> bool:
        """Take one post. Returns whether the paused state changed."""
        import json

        instance = body.get("instance")
        state = body.get("paused")
        events = body.get("events")
        problems = body.get("problems")
        kept: list[dict[str, Any]] = []
        refused = 0
        for e in events if isinstance(events, list) else []:
            if (
                isinstance(e, dict)
                and e.get("kind") in KINDS
                and isinstance(e.get("id"), str)
                and 0 < len(e["id"]) <= 32
                and isinstance(e.get("ts"), int | float)
                and isinstance(e.get("source"), str)
                and len(e["source"]) <= 20
                and isinstance(e.get("data"), dict)
                and len(json.dumps(e["data"])) <= DATA_MAX
            ):
                kept.append({k: e[k] for k in ("id", "kind", "ts", "source", "data")})
            else:
                refused += 1
        with self._lock:
            before = self.paused
            self.heard = self.clock()
            if instance in ("play", "lab"):
                self.instance = instance
            if isinstance(state, dict) and state.get("paused") in (True, False, None):
                self.paused = state["paused"]
                since = state.get("since")
                self.since = float(since) if isinstance(since, int | float) else None
                why = state.get("why")
                self.why = why[:80] if isinstance(why, str) else None
            if isinstance(problems, list):
                self.problems = [str(p)[:300] for p in problems[:20]]
            for e in kept:
                if e["id"] in self._ids:
                    continue  # a retried post
                if len(self._events) == self._events.maxlen:
                    self._ids.discard(self._events[0]["id"])
                self._events.append(e)
                self._ids.add(e["id"])
            self.refused += refused
            return before != self.paused

    def paused_now(self, running: list[str] | None) -> bool | None:
        """``game.paused`` for the dashboard: the watchers' state while they are heard and their
        instance's client runs (``running`` None: not known yet, so trusted), else None."""
        with self._lock:
            if self.heard is None or self.clock() - self.heard > HEARD_S:
                return None
            if running is not None and self.instance not in running:
                return None
            return self.paused

    def recent(self, limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = [e for e in self._events if kind is None or e["kind"] == kind]
        return rows[-limit:]

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "instance": self.instance,
                "paused": self.paused,
                "since": self.since,
                "why": self.why,
                "heard_s_ago": None
                if self.heard is None
                else round(self.clock() - self.heard, 1),
                "problems": list(self.problems),
                "refused": self.refused,
            }
