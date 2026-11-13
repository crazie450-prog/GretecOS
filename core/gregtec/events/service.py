"""The core's event service (WO-6.3): the event store, sessions, the journal and the age recaps,
on one connection of the core's own DB (the core is its single owner, plan 4.2).

- :meth:`EventService.record`: a watchers post's events into the store (``POST
  /api/game/events``, :mod:`gregtec.watchers.routes`);
- :meth:`EventService.tick` (every :data:`TICK_S` from the core, in a thread): idle sessions
  closed, the reported tier's age noted (a rise keeps the finished age's recap), the session
  scribe's due journal blocks, and once an hour the open age's best net power;
- :meth:`EventService.activity` and :meth:`EventService.recap`: what the Activity view and the
  tier-up recap read (:mod:`gregtec.events.routes`).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec import store
from gregtec.events import ages, journal
from gregtec.events import store as events
from gregtec.vault.io import VaultError, VaultIO

log = logging.getLogger("gregtec.events")

TICK_S = 10.0
BEST_EVERY_S = 3600.0
SESSIONS_SHOWN = 30
EVENTS_SHOWN = 300
_DAILY = re.compile(r"(\d{4}-\d{2}-\d{2})(?:-(\d{1,2}))?\.md")


@dataclass
class EventService:
    db: Path
    scribe: journal.Scribe | None = None
    worlds: Mapping[str, str | None] = field(default_factory=dict)  # instance -> world
    clock: Callable[[], float] = time.time
    next_best: float = 0.0
    started: float | None = None  # the first tick
    heard: bool = False  # the watchers have posted since then
    problems: list[str] = field(default_factory=list)
    _conn: sqlite3.Connection | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = store.connect(self.db)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # --- writing

    def record(
        self,
        instance: str | None,
        items: Iterable[Mapping[str, Any]],
        link: bool = False,
    ) -> events.Recorded:
        """Store a post's events: the watchers', or GregtecLink's (``link``). The link's are
        not the watchers having posted, and after a core restart they close no session as
        idle until the watchers have (their backlog) or the grace has passed (D264 (4))."""
        with self._lock:
            world = self.worlds.get(instance or "")
            grace = link and not self._grace_over(self.clock())
            found = events.record(self._db(), instance, items, world, grace)
            if not link:
                self.heard = True
        if found.skipped:
            log.warning("game events skipped", extra={"skipped": found.skipped})
        return found

    def tick(self, stage: str | None, provisional: bool = False) -> list[str]:
        """One round of upkeep (``stage``, ``provisional``: :func:`.routes.stage_of`); returns
        the journal logs written."""
        now = self.clock()
        with self._lock:
            conn = self._db()
            if self.started is None:
                self.started = now
            # After a core restart mid-game the watchers' backlog comes first: closing the open
            # session as idle before it would split it (D264).
            if self._grace_over(now):
                events.close_idle(conn, now)
            if stage is not None:
                ages.observe(conn, stage, now, provisional)
            if now >= self.next_best:
                ages.update_best(conn, now)
                self.next_best = now + BEST_EVERY_S
            if self.scribe is None:
                return []
            written = self.scribe.run(conn, now)
            self.problems = list(self.scribe.problems)
            return written

    def _grace_over(self, now: float) -> bool:
        """Whether the watchers have posted since the core started, or 15 minutes have
        passed since its first tick (none yet: the core has just started)."""
        return self.heard or (
            self.started is not None and now >= self.started + events.IDLE_S
        )

    # --- reading

    def recap(self, stage: str | None, provisional: bool = False) -> dict[str, Any]:
        """The newest finished age's recap (a rise the dashboard just saw is noted first)."""
        with self._lock:
            conn = self._db()
            if stage is not None:
                ages.observe(conn, stage, self.clock(), provisional)
            found = ages.recaps(conn)
            cur = conn.execute(
                "SELECT age, stage, start_ts FROM age_mark WHERE end_ts IS NULL "
                "ORDER BY age DESC LIMIT 1"
            ).fetchone()
        return {
            "recap": found[0] if found else None,
            "age": dict(cur) if cur is not None else None,
        }

    def activity(
        self, session_id: int | None = None, limit: int = EVENTS_SHOWN
    ) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            conn = self._db()
            rows = conn.execute(
                "SELECT * FROM session ORDER BY start_ts DESC, id DESC LIMIT ?",
                (SESSIONS_SHOWN,),
            ).fetchall()
            sessions = []
            for r in rows:
                s = events.session_dict(r, now)
                s["counts"] = events.counts(conn, r["id"])
                sessions.append(s)
            picked = None
            if session_id is not None:
                row = conn.execute(
                    "SELECT * FROM session WHERE id = ?", (session_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(session_id)
                picked = events.session_dict(row, now)
                picked["counts"] = events.counts(conn, session_id)
            elif sessions:
                picked = sessions[0]
            timeline: list[dict[str, Any]] = []
            if picked is not None:
                timeline = [
                    events.event_dict(r)
                    for r in conn.execute(
                        "SELECT * FROM event WHERE session_id = ? ORDER BY ts DESC, id DESC "
                        "LIMIT ?",
                        (picked["id"], limit),
                    )
                ]
            loose = conn.execute(
                "SELECT COUNT(*) FROM event WHERE session_id IS NULL"
            ).fetchone()[0]
            recaps = ages.recaps(conn)
        return {
            "ts": int(now),
            "sessions": sessions,
            "session": picked,
            "events": timeline,
            "outside_sessions": int(loose),
            "journal": self.journal_of(picked) if picked else None,
            "recaps": recaps,
            "problems": list(self.problems),
        }

    def journal_of(self, session: Mapping[str, Any]) -> dict[str, Any] | None:
        """The session's lines in the daily logs it was written to (read through VaultIO)."""
        if self.scribe is None or session.get("instance") != "play":
            return None
        vault: VaultIO = self.scribe.vault
        first = dt.date.fromtimestamp(session["start_ts"])
        last = dt.date.fromtimestamp(
            self.clock()
        )  # a block is written later than its events
        paths, lines = [], []
        try:
            _, folder = vault.path("log/index.md")
        except VaultError:
            return {"paths": [], "lines": [], "written": session.get("journal")}
        found = []
        if folder.parent.is_dir():
            for f in folder.parent.iterdir():
                m = _DAILY.fullmatch(f.name)
                if m and first <= dt.date.fromisoformat(m[1]) <= last:
                    found.append((m[1], int(m[2] or 1), f"log/{f.name}"))
        for _, _, rel in sorted(found):
            try:
                _, text = vault.read(rel)
            except VaultError:
                continue
            mine = journal.journal_lines(text, int(session["id"]))
            if mine:
                paths.append(rel)
                lines += mine
        return {"paths": paths, "lines": lines, "written": session.get("journal")}
