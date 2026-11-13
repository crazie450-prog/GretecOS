"""GregtecLink's machine events, read by the game watchers (WO-6.2, plan 5.4, D70, D260).

The listener module (``mod/``, Play and Lab, never Export) appends one JSON object per line to
``<game>/gregteclink/events/machines.jsonl`` when a player places or breaks a GT machine
(contract: docs/contracts/gregteclink-events-v1.md). :class:`MachineEventWatch` tails that file
as the log readers tail theirs (``files.LogTail``: stat, open, seek, read, close through
``safe_open``; a rotation to ``machines.1.jsonl`` is a new file identity) and turns each valid
line into a ``machine_built`` or ``machine_removed`` event. Unlike a log, the file is read from
its start the first time (its lines are the base's history, and the core's position-keyed
apply makes a replay harmless), then from the saved offset.

Each line is checked field by field: anything else (a bad field, a numeric ``id`` key, a line
over 1 KB) is dropped and counted as a problem; nothing in it is ever an instruction. The machine
is keyed on its item: GT's machine block registry name + meta (the mte id), never a numeric id.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gregtec.watchers import files
from gregtec.watchers.events import Event

EVENTS_FILE = Path("gregteclink") / "events" / "machines.jsonl"
VERSION = 1
KINDS = ("machine_built", "machine_removed")
LINE_MAX = 1024
COORD_MAX = 30_000_000  # Minecraft's world border
MTE_MAX = 65535  # GT's MetaTileEntity ids are shorts in its item damage
REGISTRY = re.compile(r"^[A-Za-z0-9_.-]{1,64}:[A-Za-z0-9_.:/-]{1,120}$")
FIELDS = {
    "v",
    "kind",
    "boot",
    "seq",
    "ts_ms",
    "dim",
    "x",
    "y",
    "z",
    "mte_id",
    "registry",
    "meta",
}

Clock = Callable[[], float]


def _int(value: object, lo: int, hi: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        return None
    return value


def parse(line: str) -> dict[str, Any] | None:
    """One line of the events file as an event's data, or None when it isn't a valid v1 event."""
    if len(line) > LINE_MAX:
        return None
    try:
        o = json.loads(line)
    except ValueError:
        return None
    if not isinstance(o, dict) or set(o) != FIELDS:
        return None
    if o["v"] != VERSION or o["kind"] not in KINDS:
        return None
    ints = {
        "boot": _int(o["boot"], 0, 2**53),
        "seq": _int(o["seq"], 1, 2**53),
        "ts_ms": _int(o["ts_ms"], 0, 2**53),
        "dim": _int(o["dim"], -(2**31), 2**31 - 1),
        "x": _int(o["x"], -COORD_MAX, COORD_MAX),
        "y": _int(o["y"], 0, 255),
        "z": _int(o["z"], -COORD_MAX, COORD_MAX),
        "mte_id": _int(o["mte_id"], 1, MTE_MAX),
    }
    if any(v is None for v in ints.values()) or o["meta"] != o["mte_id"]:
        return None
    registry = o["registry"]
    if registry is not None and (
        not isinstance(registry, str) or not REGISTRY.match(registry)
    ):
        return None
    mte = ints["mte_id"]
    return {
        "kind": o["kind"],
        "mte_id": mte,
        "ref": f"mte:{mte}",
        "registry": registry,
        "meta": mte,
        "key": f"{registry}@{mte}" if registry else None,
        "dim": ints["dim"],
        "x": ints["x"],
        "y": ints["y"],
        "z": ints["z"],
        "boot": ints["boot"],
        "seq": ints["seq"],
        "logged_ms": ints["ts_ms"],
    }


class MachineEventWatch:
    """Tails the listener's events file: ``machine_built`` and ``machine_removed``."""

    source = "listener"

    def __init__(self, game: Path, clock: Clock, saved: dict[str, Any]) -> None:
        self.clock = clock
        path = game / EVENTS_FILE
        state = saved.get(str(path))
        self.tail = files.LogTail(
            path, state if isinstance(state, dict) else None, from_start=True
        )
        self.refused = 0
        self.problem: str | None = None

    def poll(self) -> list[Event]:
        now = self.clock()
        events = []
        for line in self.tail.poll():
            if not line.strip():
                continue
            data = parse(line)
            if data is None:
                self.refused += 1
                self.problem = (
                    f"{self.refused} malformed line(s) in {EVENTS_FILE.as_posix()}"
                )
                continue
            kind = data.pop("kind")
            # the same line read twice (a lost tail offset) is the same event id
            ident = f"m{data['boot']:x}-{data['seq']:x}"[:32]
            events.append(Event(kind, now, self.source, data, id=ident))
        return events
