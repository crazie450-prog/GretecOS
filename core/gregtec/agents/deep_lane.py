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

from gregtec.agents.tools.registry import FAST, Call, Tool, ToolError
from gregtec.agents.tools.tsv import Result

log = logging.getLogger(__name__)

KEEP = 50
TASK_MAX = 2000
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


HANDOFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": "the task for the deep lane, in one or two sentences, with the "
            "items and keys already resolved",
            "minLength": 1,
            "maxLength": TASK_MAX,
        }
    },
    "required": ["task"],
    "additionalProperties": False,
}


def handoff(call: Call, args: dict[str, Any], offset: int) -> Result:
    deep = call.env.deep
    if deep is None:
        raise ToolError("the deep lane isn't connected here; answer what you can")
    if not call.request:
        # Only a lane answering one of Clayton's requests can hand off: the request it passes
        # is the deep lane's instruction, so a call without one has nothing to hand over.
        raise ToolError("handoff runs only for a request Clayton made")
    item = deep.handoff(args["task"], call.request, "fast_lane")
    return Result(
        head=[("handoff", item.id), ("status", item.status)],
        notes=["tell Clayton in one sentence that the deep lane has the task"],
    )


HANDOFF_TOOL = Tool(
    "handoff",
    "Hand a task to the deep lane: designing or planning a line, comparing options, advice "
    "on what to do next, or research over several steps. Then say in one sentence that it is "
    "handed off; don't attempt it yourself.",
    HANDOFF_SCHEMA,
    handoff,
    read_only=False,
    lanes=frozenset({FAST}),
    needs_db=False,
    gate="the deep lane's own tool gate, path guard and cost cap (D154, D156)",
)
