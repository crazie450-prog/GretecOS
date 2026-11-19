"""The fast lane's ``handoff`` tool (WO-2.3, D152): a task to the deep lane.

It reaches the deep lane through ``call.env.deep`` (a :class:`gregtec.agents.deep_lane.DeepLane`)
and so never imports :mod:`gregtec.agents.deep_lane`, which re-exports these names.
"""

from __future__ import annotations

from typing import Any

from gregtec.agents.tools.registry import FAST, Call, Tool, ToolError
from gregtec.agents.tools.tsv import Result

TASK_MAX = 2000

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
