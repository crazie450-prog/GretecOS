"""The live base: OC telemetry read for people (WO-7.4; plan 7.9, 7.14 Base view; D219).

:class:`Live` turns each binding's newest OC messages (:class:`gregtec.bridge.telemetry.Telemetry`
keeps them) into what the dashboard's Base view and the in-game displays show: power, machines,
problems, AE2 crafting and how old each reading is. It computes nothing a reading didn't give:
a percentage is the stored EU over the capacity the same reading carried, a net EU/t is the
averages GT reported added up, and a reading the agent left out stays out. Everything carries its
``tel:<binding>/<address>`` citation (D219) and its age; once an agent hasn't been heard for
:data:`~gregtec.bridge.telemetry.STALE_S` its readings are **stale** (shown as such, never
extrapolated: a paused game pauses OC, plan 7.9).

- **Machine status** (from the readings of its newest message): ``paused`` (work not allowed,
  and the desk's latest finished pause or resume for that address is a confirmed pause: GOS
  paused it), ``stopped`` (work not allowed and GOS didn't pause it: GT disables work when a
  multiblock shuts itself down, on power loss or maintenance, and a soft mallet does too),
  ``running`` (``isMachineActive``), ``stalled`` (it has a job, work is allowed, and it isn't
  running: no power, no input, a full output), ``idle``, ``unknown`` (the agent left the
  readings out), or ``stale``.
- **Problems** (the ``problems`` list, worst first, stopped and stalled machines before others):
  a Problems count above 0 or an LSC's bad maintenance from its sensor lines
  (:mod:`gregtec.bridge.sensors`; lines come every ``sensor_s``, so the last parsed count is kept
  with its own age), a stopped or stalled machine, an agent gone stale (its age is in
  ``age_s``, not in the text, so the displays' ``rev`` holds still), and the agent's own errors. WO-7.5's alert rules (thresholds,
  notifications) build on this; nothing here alerts anyone.
- Names: GT's meta name through the export (:class:`gregtec.bridge.names.MetaNames`); items by
  registry name and damage with no NBT from the game DB. All of it is game data: shown, never
  followed (plan 0.1 principle 8).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from gregtec.bridge import sensors
from gregtec.bridge.ingest import Received
from gregtec.bridge.names import MetaNames
from gregtec.bridge.telemetry import STALE_S, Telemetry

LEVELS = ("bad", "warn", "info")
MAX_PROBLEMS = 32
MAY_STILL_RUN = "may still run"

ItemNamer = Callable[[str, int], str | None]
PausedBy = Callable[
    [str], set[str]
]  # binding -> addresses GOS paused (ActionDesk.paused)
MACHINE_FIRST = ("stopped", "stalled")  # these lead the problems among their level


@dataclass(frozen=True)
class SensorSeen:
    reading: sensors.Reading
    at: float  # when the core received the lines (epoch s)


def _num(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def pct(stored: object, capacity: object) -> float | None:
    """``stored / capacity`` as a percentage with one decimal (ints or exact decimal strings)."""
    try:
        s = int(stored) if isinstance(stored, str) else stored
        c = int(capacity) if isinstance(capacity, str) else capacity
    except ValueError:
        return None
    if (
        not isinstance(s, int | float)
        or not isinstance(c, int | float)
        or isinstance(s, bool)
        or isinstance(c, bool)
        or c <= 0
        or s < 0
    ):
        return None
    if isinstance(s, int) and isinstance(c, int):
        return min(1000, s * 1000 // c) / 10  # exact for BigInteger-sized strings
    return round(min(100.0, 100.0 * s / c), 1)


def machine_status(entry: Mapping[str, Any], gos_paused: bool = False) -> str:
    if entry.get("allowed") is False:
        return "paused" if gos_paused else "stopped"
    if entry.get("active") is True:
        return "running"
    if entry.get("work") is True and entry.get("allowed") is True:
        return "stalled"
    if "active" in entry:
        return "idle"
    return "unknown"


def pretty_meta(meta: str | None) -> str:
    """A GT meta name readable when the DB doesn't know it (``multimachine.blastfurnace``)."""
    if not meta:
        return "unnamed machine"
    return meta.rsplit(".", 1)[-1] if meta.startswith("multimachine.") else meta


class Live:
    """The live base for every binding, from :class:`Telemetry`'s newest messages."""

    def __init__(
        self,
        telemetry: Telemetry,
        *,
        names: MetaNames | None = None,
        item_name: ItemNamer | None = None,
        clock: Callable[[], float] | None = None,
        paused_by: PausedBy | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.paused_by = paused_by
        self.names = names
        self.item_name = item_name
        self.clock = clock or telemetry.clock
        self._lock = threading.Lock()
        self._sensors: dict[str, SensorSeen] = {}

    # --- fed by the telemetry sink, once per new message

    def observe(self, msg: Received) -> None:
        """Keep the parsed sensor lines each new message carried (they come every sensor_s)."""
        binding = msg.agent.binding
        with self._lock:
            for e in [*(msg.doc.get("power") or []), *(msg.doc.get("machines") or [])]:
                lines = e.get("sensor")
                if isinstance(lines, list):
                    key = f"{binding}/{e['addr']}"
                    self._sensors[key] = SensorSeen(
                        sensors.read(lines), msg.received_at
                    )
            while len(self._sensors) > 1024:  # bounded: 16 computers x 96 entries
                self._sensors.pop(next(iter(self._sensors)))

    # --- reading

    def _name(self, meta: str | None) -> tuple[str, int | None]:
        named = self.names.get(meta) if self.names is not None else None
        if named is not None and named.name:
            return named.name, named.mte_id
        return pretty_meta(meta), None

    def _item(self, name: str, damage: int) -> str:
        found = self.item_name(name, damage) if self.item_name is not None else None
        return found or f"{name}@{damage}"

    def binding(self, binding: str) -> dict[str, Any]:
        """Everything one binding's OC computers report (see the module docstring)."""
        now = self.clock()
        agents: list[dict[str, Any]] = []
        power: list[dict[str, Any]] = []
        machines: list[dict[str, Any]] = []
        problems: list[dict[str, Any]] = []
        cpus = busy = 0
        has_ae2 = False
        crafts: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        with self._lock:
            seen = dict(self._sensors)
        gos_paused = self.paused_by(binding) if self.paused_by is not None else set()
        for agent in self.telemetry.latest.agents():
            if agent.binding != binding:
                continue
            msg = self.telemetry.latest.latest(agent)
            if msg is None:
                continue
            doc = msg.doc
            age = max(0, int(now - msg.received_at))
            stale = age > STALE_S
            acite = f"tel:{agent}"
            agents.append(
                {
                    "address": agent.address,
                    "cite": acite,
                    "transport": msg.transport,
                    "age_s": age,
                    "stale": stale,
                    "tick_ms": doc.get("tick_ms"),
                }
            )
            if stale:
                problems.append(
                    {
                        "level": "warn",
                        "kind": "stale",
                        "cite": acite,
                        "text": f"OC computer {agent.address[:8]} is silent "
                        "(the game may be paused); its readings are stale",
                        "age_s": age,
                    }
                )
            for err in doc.get("errors") or []:
                problems.append(
                    {
                        "level": "info",
                        "kind": "agent_error",
                        "cite": acite,
                        "text": f"OC agent ({err.get('where')}): {err.get('msg')}"[
                            :200
                        ],
                    }
                )
            for e in doc.get("power") or []:
                mid = f"{binding}/{e['addr']}"
                name, mte = self._name(e.get("name"))
                row: dict[str, Any] = {
                    "id": mid,
                    "cite": f"tel:{mid}",
                    "name": name,
                    "meta": e.get("name"),
                    "mte_id": mte,
                    "kind": e.get("kind"),
                    "age_s": age,
                    "stale": stale,
                    "eu_in": _num(e.get("eu_in")),
                    "eu_out": _num(e.get("eu_out")),
                }
                if e.get("kind") == "storage":
                    row["eu_str"], row["eu_max_str"] = (
                        e.get("eu_str"),
                        e.get("eu_max_str"),
                    )
                    row["pct"] = pct(e.get("eu_str"), e.get("eu_max_str"))
                else:
                    row["eu"], row["eu_max"] = _num(e.get("eu")), _num(e.get("eu_max"))
                    row["pct"] = pct(e.get("eu"), e.get("eu_max"))
                s = seen.get(mid)
                if s is not None and s.reading.maintenance_ok is False:
                    problems.append(
                        {
                            "level": "bad",
                            "kind": "maintenance",
                            "cite": f"tel:{mid}",
                            "text": f"{name}: needs maintenance",
                            "age_s": max(0, int(now - s.at)),
                        }
                    )
                power.append(row)
            for e in doc.get("machines") or []:
                mid = f"{binding}/{e['addr']}"
                name, mte = self._name(e.get("name"))
                status = (
                    "stale" if stale else machine_status(e, e["addr"] in gos_paused)
                )
                s = seen.get(mid)
                row = {
                    "id": mid,
                    "cite": f"tel:{mid}",
                    "name": name,
                    "meta": e.get("name"),
                    "mte_id": mte,
                    "xyz": e.get("xyz"),
                    "status": status,
                    "progress": _num(e.get("progress")),
                    "progress_max": _num(e.get("progress_max")),
                    "problems": s.reading.problems if s is not None else None,
                    "problems_age_s": max(0, int(now - s.at))
                    if s is not None
                    else None,
                    "age_s": age,
                    "stale": stale,
                }
                machines.append(row)
                if status == "stopped":
                    problems.append(
                        {
                            "level": "warn",
                            "kind": "stopped",
                            "cite": f"tel:{mid}",
                            "text": f"{name}: stopped (work disabled: power loss, "
                            "maintenance or by hand)",
                        }
                    )
                if status == "stalled":
                    problems.append(
                        {
                            "level": "warn",
                            "kind": "stalled",
                            "cite": f"tel:{mid}",
                            "text": f"{name}: stalled (has a job, not running)",
                        }
                    )
                if s is not None and s.reading.problems:
                    n = s.reading.problems
                    problems.append(
                        {
                            "level": "bad",
                            "kind": "maintenance",
                            "cite": f"tel:{mid}",
                            "text": f"{name}: {n} maintenance problem{'s' if n != 1 else ''}",
                            "age_s": max(0, int(now - s.at)),
                        }
                    )
            ae2 = doc.get("ae2")
            if isinstance(ae2, dict):
                has_ae2 = True
                listed = ae2.get("cpus") or []
                cpus += len(listed)
                busy += sum(1 for c in listed if c.get("busy"))
                for it in ae2.get("items") or []:
                    items.append(
                        {
                            "key": f"{it['name']}@{it['damage']}",
                            "name": self._item(it["name"], it["damage"]),
                            "size": it.get("size"),
                            "craftable": it.get("craftable"),
                            "cite": f"tel:{binding}/{ae2['addr']}",
                        }
                    )
            for c in doc.get("crafts") or []:
                state = str(c.get("state"))
                err = c.get("err")
                if state == "failed" and isinstance(err, str) and MAY_STILL_RUN in err:
                    state = "check_ae2"
                crafts.append(
                    {
                        "id": c.get("id"),
                        "name": self._item(
                            str(c.get("name")), int(c.get("damage") or 0)
                        ),
                        "amount": c.get("amount"),
                        "state": state,
                        "err": err,
                        "cite": acite,
                        "stale": stale,
                    }
                )
        # Stopped and stalled machines first among their level, the worst level first (7.14).
        problems.sort(
            key=lambda p: (LEVELS.index(p["level"]), p["kind"] not in MACHINE_FIRST)
        )
        order = {
            "stopped": 0,
            "stalled": 1,
            "paused": 2,
            "unknown": 3,
            "stale": 4,
            "idle": 5,
            "running": 6,
        }
        machines.sort(
            key=lambda m: (-(m["problems"] or 0), order.get(m["status"], 9), m["name"])
        )
        stores = [p for p in power if p["kind"] == "storage"] or power
        ins = [p["eu_in"] for p in stores if p["eu_in"] is not None and not p["stale"]]
        outs = [
            p["eu_out"] for p in stores if p["eu_out"] is not None and not p["stale"]
        ]
        net = sum(ins) - sum(outs) if ins and outs and len(ins) == len(outs) else None
        if not agents:
            status = "not_set_up"
        elif all(a["stale"] for a in agents):
            status = "stale"
        else:
            status = "connected"
        return {
            "binding": binding,
            "status": status,
            "age_s": min((a["age_s"] for a in agents), default=None),
            "agents": agents,
            "power": power,
            "net_eu_t": net,
            "machines": machines,
            "problems": problems[:MAX_PROBLEMS],
            "ae2": {"cpus": cpus, "busy": busy, "crafts": crafts, "items": items}
            if has_ae2
            else None,
        }

    def snapshot(self) -> dict[str, Any]:
        """Every binding that has reported (Play first), for ``GET /api/base/live``."""
        found = {a.binding for a in self.telemetry.latest.agents()}
        order = sorted(found, key=lambda b: (b != "play", b))
        return {
            "ts": int(self.clock()),
            "stale_s": STALE_S,
            "bindings": [self.binding(b) for b in order],
        }
