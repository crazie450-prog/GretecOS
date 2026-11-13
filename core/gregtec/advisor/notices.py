"""Proactive notices (WO-6.5, plan 7.5 "Proactive notices", D71, D76, D267).

The core calls :meth:`Notices.tick` every :data:`TICK_S`. Each enabled kind (``[policy]
notices``; D71's defaults :data:`DEFAULT`: a power deficit, a stalled machine, a plan buildable
from stock; ``quest_opened`` is opt-in) is checked by :mod:`.checks` and becomes a notice when:

- **it is new**: its subject (a plan, an OC entry, a quest; :attr:`.checks.Finding.key`) wasn't
  true at the last tick, or has been true for its hold (a draining LSC for
  :data:`.checks.DRAIN_HOLD_S`, so one reading's flicker says nothing). A subject is announced
  once while it holds; once it clears, it can be announced again. Only a check that ran and found
  it gone clears it: while its kind's check failed this tick (the DB busy with a build or a
  backup), or while its OC entry has gone quiet (the game paused; a ``tel:`` subject heard
  more than WO-7.1's ``STALE_S`` but at most :data:`UNKNOWN_GRACE_S` ago), it is unknown and
  keeps its marks, so a pause doesn't announce the same problem again (review M2);
- **its kind's cooldown is over**: one notice per kind per :data:`COOLDOWN_S` (10 minutes, plan
  7.5); the subjects of a kind that are due together go in one notice ("2 machines stalled"),
  and a subject that came up during the cooldown waits for its end (if it still holds);
- **the priority gate is open** (:data:`PRIORITY`): no notice of a higher priority was given in
  the last :data:`COOLDOWN_S`, so an opportunity never talks over a problem. A problem is never
  held back by an opportunity.

A notice is handed to ``deliver`` (the core: a ``notice`` push to every socket, which the voice
client speaks, D76, and the dashboard shows) with a spoken line that holds no ids or numbers (the
dashboard and the advisor carry them, cited). Problem notices (``power_deficit``,
``machine_stalled``) stay in :meth:`Notices.alerts` while they hold: the dashboard's alert cards.
When the alerts change without a notice (a problem cleared), ``changed`` is called (the core
pushes the state and a ``notice.cleared`` message, so the cards and Now's steps refresh).
The state (what was announced, each kind's last notice) is kept in ``kv`` ``notices.state``, so a
core restart doesn't announce again what was already said. Only the core writes it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.advisor import checks
from gregtec.bridge.telemetry import STALE_S
from gregtec.config import NOTICES, NOTICES_DEFAULT

log = logging.getLogger("gregtec.advisor")

TICK_S = 10.0
COOLDOWN_S = 600  # plan 7.5: a 10-minute cooldown
QUEST_WINDOW_S = 900  # quest_opened: a quest done in the last 15 minutes
KV_STATE = "notices.state"
UNKNOWN_GRACE_S = (
    3600  # a quiet OC entry's marks are kept this long, then it counts as cleared
)
HIGH, NORMAL, LOW = 2, 1, 0
PRIORITY: dict[str, int] = {
    "power_deficit": HIGH,
    "machine_stalled": HIGH,
    "plan_buildable": NORMAL,
    "quest_opened": LOW,
}
KINDS = NOTICES  # [policy] notices' kinds, in this order
DEFAULT = NOTICES_DEFAULT  # D71: on unless [policy] notices says otherwise
PROBLEMS = ("power_deficit", "machine_stalled")  # shown as alert cards while they hold


@dataclass(frozen=True)
class Notice:
    kind: str
    priority: int
    title: str
    say: str  # the spoken line: no ids, no numbers
    findings: tuple[checks.Finding, ...]
    ts: int

    @property
    def refs(self) -> list[str]:
        return list(dict.fromkeys(r for f in self.findings for r in f.refs))

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "priority": self.priority,
            "title": self.title,
            "say": self.say,
            "detail": [f.detail for f in self.findings],
            "refs": self.refs,
            "link": self.findings[0].link,
            "ts": self.ts,
        }


def _say(kind: str, found: Sequence[checks.Finding]) -> tuple[str, str]:
    """(title, spoken line) for a kind's due findings."""
    n = len(found)
    if kind == "power_deficit":
        if any(f.key == "ledger:running" for f in found):
            return found[0].title, (
                "Heads up: power deficit. Your running lines use more than they make."
            )
        return found[0].title, "Heads up: power deficit. A power store is draining."
    if kind == "machine_stalled":
        if n == 1:
            return found[0].title, "Heads up: a machine has stalled."
        return f"Machines stalled: {n}", f"Heads up: {n} machines have stalled."
    if kind == "plan_buildable":
        name = found[0].title.removeprefix("You can build ").removesuffix(" from stock")
        if n == 1:
            return found[0].title, f"You have everything to build {name} in stock."
        return f"{n} plans buildable from stock", (
            f"You have everything in stock for {n} of your accepted lines."
        )
    title = found[0].title if n == 1 else f"{n} quests just opened up"
    return title, title + "."


def _keys(holding: Mapping[str, Sequence[checks.Finding]]) -> dict[str, list[str]]:
    return {k: sorted(f.key for f in v) for k, v in holding.items() if v}


@dataclass
class Notices:
    db: Path  # GOS_HOME/data/gos.sqlite
    enabled: Sequence[str] = DEFAULT
    deliver: Callable[[Notice], None] | None = None
    changed: Callable[[], None] | None = None  # the alerts changed without a notice
    clock: Callable[[], float] = time.time
    cooldown_s: int = COOLDOWN_S
    problems: list[str] = field(default_factory=list)
    failed: set[str] = field(
        default_factory=set
    )  # the kinds whose check failed last tick
    _holding: dict[str, list[checks.Finding]] = field(default_factory=dict)
    _conn: sqlite3.Connection | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def home(self) -> Path:
        return self.db.parent.parent

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            from gregtec import store

            self._conn = store.connect(self.db)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # --- state

    def _state(self, conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (KV_STATE,)).fetchone()
        try:
            st = json.loads(row[0]) if row else {}
        except ValueError:
            st = {}
        if not isinstance(st, dict):
            st = {}
        return {
            "seen": dict(st.get("seen") or {}),  # key -> first ts it held (this spell)
            "said": dict(st.get("said") or {}),  # key -> ts announced (this spell)
            "last": dict(st.get("last") or {}),  # kind -> ts of its last notice
            "kinds": dict(st.get("kinds") or {}),  # key -> its kind
        }

    def _save(self, conn: sqlite3.Connection, st: Mapping[str, Any], now: int) -> None:
        conn.execute(
            "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE"
            " SET value = excluded.value, upd = excluded.upd",
            (KV_STATE, json.dumps(st, sort_keys=True), now),
        )
        conn.commit()

    # --- checking

    def check(self, conn: sqlite3.Connection, now: float) -> list[checks.Finding]:
        """Every enabled kind's findings now (a check that fails is reported in
        :attr:`problems` and its kind in :attr:`failed`, not fatal)."""
        out: list[checks.Finding] = []
        self.problems = []
        self.failed = set()
        for kind in KINDS:
            if kind not in self.enabled:
                continue
            try:
                if kind == "power_deficit":
                    out += checks.power_deficit(conn, now)
                elif kind == "machine_stalled":
                    out += checks.stalled_machines(conn, now)
                elif kind == "plan_buildable":
                    out += checks.buildable_plans(conn, self.home)
                else:
                    out += self._opened(conn, now)
            except Exception as exc:  # noqa: BLE001 - one kind's failure (a busy DB) is its own
                self.problems.append(f"{kind}: {exc}")
                self.failed.add(kind)
                log.info(
                    "notice check failed", extra={"kind": kind, "why": str(exc)[:200]}
                )
        return out

    def _unknown(self, conn: sqlite3.Connection, key: str, now: float) -> bool:
        """A ``tel:`` subject whose OC entry has gone quiet, but not for long: unknown."""
        if not key.startswith("tel:") or not checks._has(conn, "tel_machine"):
            return False
        row = conn.execute(
            "SELECT last_seen FROM tel_machine WHERE id = ?", (key[4:],)
        ).fetchone()
        return row is not None and STALE_S < now - int(row[0]) <= UNKNOWN_GRACE_S

    def _opened(self, conn: sqlite3.Connection, now: float) -> list[checks.Finding]:
        from gregtec.db import quests as quest_db
        from gregtec.quests import guide
        from gregtec.vault.io import VaultIO

        try:
            found = guide.ranked(conn, VaultIO(self.home / "vault"), self.db)
        except quest_db.QuestError:
            return []
        return checks.opened_quests(conn, now, found, QUEST_WINDOW_S)

    # --- the tick

    def tick(self) -> list[Notice]:
        """One round: check, gate, deliver; returns the notices given."""
        with self._lock:
            conn = self._db()
            now = self.clock()
            found = self.check(conn, now)
            st = self._state(conn)
            current = {f.key: f for f in found}
            for gone in [k for k in st["seen"] if k not in current]:
                if st["kinds"].get(gone) in self.failed or self._unknown(
                    conn, gone, now
                ):
                    continue  # not checked, or not heard: unknown, not cleared
                del st["seen"][gone]
                st["said"].pop(gone, None)
                st["kinds"].pop(gone, None)
            by_kind: dict[str, list[checks.Finding]] = {}
            for f in found:
                st["seen"].setdefault(f.key, int(now))
                st["kinds"][f.key] = f.kind
                if f.key in st["said"] or now - st["seen"][f.key] < f.hold_s:
                    continue
                by_kind.setdefault(f.kind, []).append(f)
            given: list[Notice] = []
            for kind in sorted(by_kind, key=lambda k: (-PRIORITY[k], KINDS.index(k))):
                if not self._open(kind, st["last"], now):
                    continue
                title, say = _say(kind, by_kind[kind])
                notice = Notice(
                    kind, PRIORITY[kind], title, say, tuple(by_kind[kind]), int(now)
                )
                for f in by_kind[kind]:
                    st["said"][f.key] = int(now)
                st["last"][kind] = int(now)
                given.append(notice)
            before = self._holding
            self._holding = {
                k: (
                    before.get(
                        k, []
                    )  # its check failed: what held last tick still shows
                    if k in self.failed
                    else [f for f in found if f.kind == k and f.key in st["said"]]
                )
                for k in PROBLEMS
            }
            moved = _keys(before) != _keys(self._holding)
            self._save(conn, st, int(now))
        for notice in given:
            log.info(
                "notice", extra={"kind": notice.kind, "refs": ",".join(notice.refs)}
            )
            if self.deliver is not None:
                try:
                    self.deliver(notice)
                except Exception:  # a failed push must not stop the notices
                    log.exception("notice delivery failed")
        if moved and not given and self.changed is not None:
            try:
                self.changed()  # a notice's own push already refreshes the state
            except Exception:
                log.exception("notice change push failed")
        return given

    def _open(self, kind: str, last: Mapping[str, int], now: float) -> bool:
        """The kind's own cooldown is over and no higher-priority notice is in its cooldown."""
        if now - last.get(kind, -self.cooldown_s) < self.cooldown_s:
            return False
        return not any(
            PRIORITY[k] > PRIORITY[kind] and now - ts < self.cooldown_s
            for k, ts in last.items()
            if k in PRIORITY
        )

    def alerts(self) -> list[dict[str, Any]]:
        """The announced problems that still hold, as catalog alert cards (D160)."""
        from gregtec.cards import build as cards_build

        # No lock: the dashboard reads this on the event loop while a tick may be checking; the
        # tick replaces the whole mapping at once, so this reads one tick's or the next's.
        holding = self._holding
        out = []
        for kind in PROBLEMS:
            found = holding.get(kind) or []
            if not found:
                continue
            title, _ = _say(kind, found)
            ref = next(
                (r for f in found for r in f.refs if not r.startswith("tel:")), None
            )
            value = found[0].value if len(found) == 1 else None
            out.append(
                {
                    **cards_build.alert(
                        f"notice:{kind}",
                        "warn",
                        title,
                        "; ".join(f.detail for f in found),
                        value=value,
                        unit=found[0].unit if value is not None else None,
                        source="advisor",
                    ),
                    "ref": ref,
                }
            )
        return out
