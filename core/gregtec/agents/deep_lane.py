"""The deep lane's door (plan 4.4): where the router and the fast lane's ``handoff`` send a task.

:class:`gregtec.agents.deep.DeepLaneService` (WO-2.4, D154) is the real :class:`DeepLane`:
a warm ``ClaudeSDKClient`` session per task. :class:`HandoffRecorder` only records each handoff
(in memory, the newest :data:`KEEP`, and as a log line); ``gos eval fast-lane`` and the tests use
it, so they never start a deep-lane session (D152).

A handoff carries two texts. ``request`` is what Clayton said, word for word: the deep lane's
only instruction. ``task`` is the router's copy of it, or the fast lane model's summary (with
items resolved), which tool results the fast lane read may have steered: the deep lane quotes
it as data, a hint about the request, never as instructions of its own.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

# The handoff tool lives with the tools (the catalog imports it), re-exported here: importing
# this module first no longer loops back into it through the catalog (QA 5-9).
from gregtec.agents.tools.handoff import HANDOFF_SCHEMA, HANDOFF_TOOL, TASK_MAX, handoff

__all__ = ["HANDOFF_SCHEMA", "HANDOFF_TOOL", "TASK_MAX", "handoff"]  # and the classes below

log = logging.getLogger(__name__)

KEEP = 50
NOT_YET = "recorded only: no deep lane runs in this process"


@dataclass(frozen=True)
class Handoff:
    id: str
    ts: int
    task: str  # the fast lane model's summary (data), or the request itself (router)
    request: str  # what Clayton asked, as the router got it: the only instruction
    source: str  # router or fast_lane
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "task": self.task,
            "request": self.request,
            "source": self.source,
            "status": self.status,
        }


class DeepLane(Protocol):
    def handoff(self, task: str, request: str, source: str) -> Handoff: ...


class HandoffRecorder:
    """The stub deep lane: records handoffs, runs nothing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recent: deque[Handoff] = deque(maxlen=KEEP)

    def handoff(self, task: str, request: str, source: str) -> Handoff:
        item = Handoff(
            f"h-{secrets.token_hex(4)}",
            int(time.time()),
            task[:TASK_MAX],
            request[:TASK_MAX],
            source,
            NOT_YET,
        )
        with self._lock:
            self._recent.append(item)
        # The id and size only: request text can hold anything Clayton said.
        log.info(
            "deep-lane handoff recorded",
            extra={"handoff": item.id, "source": source, "chars": len(item.task)},
        )
        return item

    def recent(self) -> list[Handoff]:
        with self._lock:
            return list(self._recent)
