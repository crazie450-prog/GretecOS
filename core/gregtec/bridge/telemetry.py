"""OC telemetry into ``tel_machine`` and ``tel_sample`` (WO-7.1; plan 7.9, Appendix D; D219).

:class:`Telemetry` is the :class:`gregtec.bridge.ingest.Sink` both transports feed (M2's
``POST /oc/telemetry`` and WO-7.3's M1 mailbox). For each new message it:

- keeps the agent's newest message in memory (:class:`gregtec.bridge.ingest.Latest`), which the
  action desk asks which OC computer sees a machine, the AE2 network or a chat box;
- upserts one ``tel_machine`` row per machine and power entry, ``id`` = ``<binding>/<addr>``
  (the component address within the binding its transport vouches for, D217), with GT's meta
  name, ``x``/``y``/``z`` from ``getCoordinates``, ``last_seen`` (epoch seconds, when the core
  received it) and ``status``: ``running`` (``isMachineActive``), ``paused`` (work not allowed, by
  whoever: the live base tells GOS's pause from GT's own shutdown, D279), ``idle``, ``power`` (a power entry) or ``stale``. ``mte_id`` is the export's
  meta-name map's (WO-7.4, :mod:`gregtec.bridge.names`; NULL for a name it can't resolve to one
  machine) and ``dim`` stays NULL: no OC method gives it (D214);
- writes ``tel_sample`` rows (``ts``, ``src`` = the ``tel_machine`` id or ``<binding>/<computer
  address>`` for the computer's own readings, ``key``, ``value`` or ``text``) for each reading
  that **changed**, and again every :data:`HEARTBEAT_S` while it doesn't, so a gap in the
  samples longer than that is a gap in the data. Readings the agent left out (a failed call)
  are not written; nothing is ever estimated or carried forward: a machine's ``readings`` are
  only those its newest message carried (:meth:`Telemetry.snapshot`);
- refuses a message from a new OC computer once its binding has
  :data:`MAX_AGENTS_PER_BINDING` (``internal``), so a token can't fill the core with made-up
  computers;
- hands ``results`` and ``crafts`` to the action desk (only actions sent to that binding's
  computer can be finished by it).

**Staleness:** a reading is as old as the message it came in. :meth:`Telemetry.sweep` (every
second, from the core) marks ``tel_machine`` rows ``stale`` once nothing has been heard of them
for :data:`STALE_S`; :meth:`Telemetry.snapshot` gives every agent and machine its ``age_s`` and
``stale`` flag. A paused game pauses OC, so its readings go stale; they are never extrapolated.
Samples older than :data:`KEEP_S` are pruned once an hour.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from gregtec.bridge.actions import ActionError
from gregtec.bridge.ingest import AgentId, Latest, Received
from gregtec.store import Store

log = logging.getLogger("gregtec.bridge.telemetry")

STALE_S = 30  # three cycles at OC's slowest (10 s): nothing heard this long is stale
HEARTBEAT_S = 60  # an unchanged reading is written again this often
KEEP_S = 7 * 86400  # samples kept
PRUNE_EVERY_S = 3600
MACHINE_KEYS = ("active", "allowed", "work", "progress", "progress_max", "eu", "eu_max")
POWER_KEYS = ("eu", "eu_max", "eu_in", "eu_out")
POWER_TEXT_KEYS = ("eu_str", "eu_max_str")
COMPUTER_KEYS = ("mem_free", "mem_total", "energy", "energy_max")
MAX_AGENTS_PER_BINDING = (
    16  # OC computers one binding may have (until the core restarts)
)

Results = Callable[[AgentId, Mapping[str, Any]], None]
Resolve = Callable[[str | None], int | None]  # GT meta name -> mte_id (WO-7.4)
Observer = Callable[[Received], None]


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    return None


def _status(entry: Mapping[str, Any], power: bool) -> str:
    if power:
        return "power"
    if entry.get("allowed") is False:
        return "paused"
    if entry.get("active") is True:
        return "running"
    if "active" in entry:
        return "idle"
    return "unknown"


class Telemetry:
    """The OC ingest's sink (D217's :class:`~gregtec.bridge.ingest.Sink`): tables, latest, results."""

    def __init__(
        self,
        store: Store,
        *,
        results: Results | None = None,
        clock: Callable[[], float] = time.time,
        resolve: Resolve | None = None,
    ) -> None:
        self.store = store
        self.results = results
        self.clock = clock
        self.resolve = resolve
        # Each new message after it is recorded (WO-7.4's live base keeps sensor readings).
        self.observers: list[Observer] = []
        self.latest = Latest()
        self._lock = threading.Lock()
        self._written: dict[tuple[str, str], tuple[float | None, str | None, int]] = {}
        # Per machine or power entry: (last_seen, the reading keys its newest message carried).
        self._carried: dict[str, tuple[int, frozenset[str]]] = {}
        self._pruned = 0.0

    # --- the sink

    def ingest(self, msg: Received) -> str | None:
        now = int(msg.received_at)
        agent, doc = msg.agent, msg.doc
        machines: list[tuple[Any, ...]] = []
        samples: list[tuple[Any, ...]] = []
        carried: dict[str, frozenset[str]] = {}
        known = self.latest.agents()
        if (
            agent not in known
            and sum(1 for a in known if a.binding == agent.binding)
            >= MAX_AGENTS_PER_BINDING
        ):  # the Bridge answers "internal"; nothing is recorded
            raise ValueError(
                f"the {agent.binding} binding has {MAX_AGENTS_PER_BINDING} OC computers already"
            )
        with self._lock:  # marks are kept only once the rows are committed
            src = str(agent)
            computer = doc.get("computer") or {}
            for key in COMPUTER_KEYS:
                self._sample(samples, now, src, key, _num(computer.get(key)), None)
            if "tick_ms" in doc:
                self._sample(samples, now, src, "tick_ms", _num(doc["tick_ms"]), None)
            for entry, power in [(e, True) for e in doc.get("power") or []] + [
                (e, False) for e in doc.get("machines") or []
            ]:
                mid = f"{agent.binding}/{entry['addr']}"
                xyz = entry.get("xyz") or [None, None, None]
                machines.append(
                    (
                        mid,
                        entry.get("name"),
                        self._mte(entry.get("name")),
                        xyz[0],
                        xyz[1],
                        xyz[2],
                        now,
                        _status(entry, power),
                    )
                )
                keys: set[str] = set()
                for key in POWER_KEYS if power else MACHINE_KEYS:
                    if key in entry and self._sample(
                        samples, now, mid, key, _num(entry[key]), None
                    ):
                        keys.add(key)
                for key in POWER_TEXT_KEYS if power else ():
                    if key in entry and self._sample(
                        samples, now, mid, key, None, str(entry[key])
                    ):
                        keys.add(key)
                carried[mid] = frozenset(keys)
            ae2 = doc.get("ae2")
            if isinstance(ae2, dict):
                asrc = f"{agent.binding}/{ae2['addr']}"
                cpus = ae2.get("cpus") or []
                self._sample(samples, now, asrc, "cpus", float(len(cpus)), None)
                busy = sum(1 for c in cpus if c.get("busy"))
                self._sample(samples, now, asrc, "cpus_busy", float(busy), None)
                for item in ae2.get("items") or []:
                    key = f"item:{item['name']}@{item['damage']}"
                    self._sample(samples, now, asrc, key, _num(item.get("size")), None)
            with self.store.transaction() as conn:
                conn.executemany(
                    "INSERT INTO tel_machine(id, name, mte_id, x, y, z, last_seen, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                    "name = excluded.name, mte_id = excluded.mte_id, x = excluded.x, "
                    "y = excluded.y, z = excluded.z, "
                    "last_seen = excluded.last_seen, status = excluded.status",
                    machines,
                )
                conn.executemany(
                    "INSERT INTO tel_sample(ts, src, key, value, text) VALUES (?, ?, ?, ?, ?)",
                    samples,
                )
            for ts, ssrc, key, value, text in samples:
                self._written[(ssrc, key)] = (value, text, ts)
            for mid, keys in carried.items():
                self._carried[mid] = (now, keys)
        self.latest.ingest(msg)
        for observe in self.observers:
            try:
                observe(msg)
            except Exception:  # a display's failure never refuses a recorded message
                log.exception("OC message observer failed")
        if self.results is not None:
            self.results(
                agent, doc
            )  # a failing desk refuses the message: it comes again
        return None

    def _mte(self, name: object) -> int | None:
        if self.resolve is None or not isinstance(name, str):
            return None
        try:
            return self.resolve(name)
        except Exception:
            log.exception("meta name not resolved")
            return None

    def _sample(
        self,
        out: list[tuple[Any, ...]],
        now: int,
        src: str,
        key: str,
        value: float | None,
        text: str | None,
    ) -> bool:
        """Add the reading to ``out`` if it changed or is due again; False: no reading."""
        if value is None and text is None:
            return False
        last = self._written.get((src, key))
        if (
            last is not None
            and last[0] == value
            and last[1] == text
            and now - last[2] < HEARTBEAT_S
        ):
            return True
        out.append((now, src, key, value, text))
        return True

    # --- staleness

    def sweep(self) -> None:
        """Mark machines nothing was heard of for :data:`STALE_S` stale; prune old samples."""
        now = self.clock()
        # Each a quick write: while another writer (a DB build, a backup) holds the DB, the
        # sweep waits for the next second instead of stalling the core's watcher.
        try:
            self.store.try_write(
                "UPDATE tel_machine SET status = 'stale' WHERE last_seen < ? AND "
                "COALESCE(status, '') != 'stale'",
                (int(now) - STALE_S,),
            )
            if now - self._pruned >= PRUNE_EVERY_S:
                self.store.try_write(
                    "DELETE FROM tel_sample WHERE ts < ?", (int(now) - KEEP_S,)
                )
                self._pruned = now
        except sqlite3.OperationalError as exc:
            log.info("telemetry sweep skipped", extra={"why": str(exc)[:100]})
        # Forget the write marks of readings no message has carried for a while.
        with self._lock:
            cut = int(now) - 2 * HEARTBEAT_S
            for k in [k for k, v in self._written.items() if v[2] < cut]:
                del self._written[k]

    def fresh(self, binding: str) -> list[Received]:
        """The newest message of each agent of ``binding`` heard within :data:`STALE_S`."""
        now = self.clock()
        out = []
        for agent in self.latest.agents():
            msg = self.latest.latest(agent)
            if (
                agent.binding == binding
                and msg is not None
                and now - msg.received_at <= STALE_S
            ):
                out.append(msg)
        return out

    # --- the action desk's locator

    def locate(self, binding: str, kind: str, args: Mapping[str, Any]) -> str:
        """The OC computer of ``binding`` that can run ``kind`` (D220), or ActionError."""
        fresh = self.fresh(binding)
        if not fresh:
            raise ActionError(
                f"no fresh telemetry from the {binding} OC computers (none heard in the last "
                f"{STALE_S} s: the game may be paused or the agent stopped)"
            )
        if kind in ("machine_pause", "machine_resume"):
            found = [
                m
                for m in fresh
                if any(e.get("addr") == args["addr"] for e in m.doc["machines"])
            ]
            what = f"the machine {args['addr']}"
        elif kind == "craft":
            found = [m for m in fresh if isinstance(m.doc.get("ae2"), dict)]
            what = "an AE2 network"
        elif kind in ("crop_start", "crop_stop"):
            found = [m for m in fresh if isinstance(m.doc.get("crops"), dict)]
            what = "a breeding station (a crops table in gos-agent.cfg)"
        else:  # say
            found = [
                m
                for m in fresh
                if (m.doc.get("components") or {}).get("chat_box", 0) > 0
            ]
            what = "a chat box"
        if not found:
            raise ActionError(f"no {binding} OC computer reports {what}")
        if len(found) > 1:
            raise ActionError(
                f"{len(found)} {binding} OC computers report {what}; GOS won't guess which"
            )
        return found[0].agent.address

    # --- reading

    def snapshot(self, binding: str | None = None) -> dict[str, Any]:
        """Agents, machines and power as last heard, each with ``age_s`` and ``stale``."""
        now = self.clock()
        agents = []
        for agent in self.latest.agents():
            msg = self.latest.latest(agent)
            if msg is None or (binding is not None and agent.binding != binding):
                continue
            age = max(0, int(now - msg.received_at))
            doc = msg.doc
            agents.append(
                {
                    "binding": agent.binding,
                    "address": agent.address,
                    "transport": msg.transport,
                    "boot": msg.boot,
                    "seq": msg.seq,
                    "last_seen": int(msg.received_at),
                    "age_s": age,
                    "stale": age > STALE_S,
                    "tick_ms": doc.get("tick_ms"),
                    "machines": len(doc.get("machines") or []),
                    "power": len(doc.get("power") or []),
                    "ae2": isinstance(doc.get("ae2"), dict),
                    "errors": [
                        {
                            "where": e.get("where"),
                            "msg": e.get("msg"),
                            "count": e.get("count"),
                        }
                        for e in doc.get("errors") or []
                    ],
                }
            )
        where, params = "", ()
        if binding is not None:
            where, params = "WHERE id LIKE ?", (f"{binding}/%",)
        rows = self.store.rows(
            f"SELECT id, name, x, y, z, last_seen, status FROM tel_machine {where} ORDER BY id",
            params,
        )
        machines = []
        for r in rows:
            age = max(0, int(now - (r["last_seen"] or 0)))
            machines.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "xyz": [r["x"], r["y"], r["z"]],
                    "last_seen": r["last_seen"],
                    "age_s": age,
                    "stale": age > STALE_S,
                    "status": r["status"],
                    "readings": self._readings(r["id"], r["last_seen"]),
                }
            )
        return {
            "ts": int(now),
            "stale_s": STALE_S,
            "agents": agents,
            "machines": machines,
        }

    def _readings(self, src: str, last_seen: int | None) -> dict[str, Any]:
        """The readings the machine's newest message carried, as they were then (so its
        ``age_s`` is theirs). A reading that message left out (a failed call) isn't shown: its
        last value is old data, never a current one. After a restart, until the machine's next
        message, they are the keys with a sample within :data:`HEARTBEAT_S` of ``last_seen``
        (the heartbeat writes a reading still carried at least that often)."""
        if last_seen is None:
            return {}
        rows = self.store.rows(
            "SELECT key, value, text, ts FROM tel_sample s WHERE src = ? AND ts > ? AND ts = "
            "(SELECT MAX(ts) FROM tel_sample WHERE src = s.src AND key = s.key)",
            (src, int(last_seen) - HEARTBEAT_S),
        )
        carried = self._carried.get(src)
        keys = carried[1] if carried is not None and carried[0] == last_seen else None
        return {
            r["key"]: r["text"] if r["text"] is not None else r["value"]
            for r in rows
            if keys is None or r["key"] in keys
        }
