"""OC telemetry into ``tel_machine`` and ``tel_sample`` (WO-7.1; plan 7.9, Appendix D; D219).

:class:`Telemetry` is the :class:`gregtec.bridge.ingest.Sink` both transports feed (M2's
``POST /oc/telemetry`` and WO-7.3's M1 mailbox). For each new message it:

- keeps the agent's newest message in memory (:class:`gregtec.bridge.ingest.Latest`), which the
  action desk asks which OC computer sees a machine, the AE2 network or a chat box;
- upserts one ``tel_machine`` row per machine and power entry, ``id`` = ``<binding>/<addr>``
  (the component address within the binding its transport vouches for, D217), with GT's meta
  name, ``x``/``y``/``z`` from ``getCoordinates``, ``last_seen`` (epoch seconds, when the core
  received it) and ``status``: ``running`` (``isMachineActive``), ``paused`` (work not
  allowed), ``idle``, ``power`` (a power entry) or ``stale``. ``mte_id`` and ``dim`` stay NULL:
  no OC method gives them (D214; the meta-name map is WO-7.4's);
- writes ``tel_sample`` rows (``ts``, ``src`` = the ``tel_machine`` id or ``<binding>/<computer
  address>`` for the computer's own readings, ``key``, ``value`` or ``text``) for each reading
  that **changed**, and again every :data:`HEARTBEAT_S` while it doesn't, so a gap in the
  samples longer than that is a gap in the data. Readings the agent left out (a failed call)
  are not written; nothing is ever estimated or carried forward;
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

Results = Callable[[AgentId, Mapping[str, Any]], None]


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
    ) -> None:
        self.store = store
        self.results = results
        self.clock = clock
        self.latest = Latest()
        self._lock = threading.Lock()
        self._written: dict[tuple[str, str], tuple[float | None, str | None, int]] = {}
        self._pruned = 0.0

    # --- the sink

    def ingest(self, msg: Received) -> str | None:
        now = int(msg.received_at)
        agent, doc = msg.agent, msg.doc
        machines: list[tuple[Any, ...]] = []
        samples: list[tuple[Any, ...]] = []
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
                        xyz[0],
                        xyz[1],
                        xyz[2],
                        now,
                        _status(entry, power),
                    )
                )
                for key in POWER_KEYS if power else MACHINE_KEYS:
                    if key in entry:
                        self._sample(samples, now, mid, key, _num(entry[key]), None)
                for key in POWER_TEXT_KEYS if power else ():
                    if key in entry:
                        self._sample(samples, now, mid, key, None, str(entry[key]))
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
                    "INSERT INTO tel_machine(id, name, x, y, z, last_seen, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                    "name = excluded.name, x = excluded.x, y = excluded.y, z = excluded.z, "
                    "last_seen = excluded.last_seen, status = excluded.status",
                    machines,
                )
                conn.executemany(
                    "INSERT INTO tel_sample(ts, src, key, value, text) VALUES (?, ?, ?, ?, ?)",
                    samples,
                )
            for ts, ssrc, key, value, text in samples:
                self._written[(ssrc, key)] = (value, text, ts)
        self.latest.ingest(msg)
        if self.results is not None:
            self.results(
                agent, doc
            )  # a failing desk refuses the message: it comes again
        return None

    def _sample(
        self,
        out: list[tuple[Any, ...]],
        now: int,
        src: str,
        key: str,
        value: float | None,
        text: str | None,
    ) -> None:
        if value is None and text is None:
            return
        last = self._written.get((src, key))
        if (
            last is not None
            and last[0] == value
            and last[1] == text
            and now - last[2] < HEARTBEAT_S
        ):
            return
        out.append((now, src, key, value, text))

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
                    "readings": self._readings(r["id"]),
                }
            )
        return {
            "ts": int(now),
            "stale_s": STALE_S,
            "agents": agents,
            "machines": machines,
        }

    def _readings(self, src: str) -> dict[str, Any]:
        rows = self.store.rows(
            "SELECT key, value, text, ts FROM tel_sample s WHERE src = ? AND ts = "
            "(SELECT MAX(ts) FROM tel_sample WHERE src = s.src AND key = s.key)",
            (src,),
        )
        return {
            r["key"]: r["text"] if r["text"] is not None else r["value"] for r in rows
        }
