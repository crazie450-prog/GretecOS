"""Fake shell designers: free stand-ins for a model in the bake-off (WO-10.11, D374).

Each is a :class:`gregtec.llm.designers.Designer` that answers from the brief's own text, as a
model would (it reads the ``facts:`` line, the request words and, for a revise, the ``current
operations:`` line), with no network, no key and no cost:

- ``fake-plain``: walls, a few low windows, corner pillars, a flat roof, lights; every
  doorway cleared.
- ``fake-ornate`` (buttresses that stand out into the relief zone, D386): a plinth, an eaves band, windows, pillars, a gable roof where the span
  allows it (in its own volume, two blocks under the top, when the headroom allows; else a
  parapet), lights, and a freeform build script (WO-10.12) for what the
  vocabulary can't draw: buttresses at the corners, round chimney stacks over a smelting line,
  a domed cupola over any other; on a tight block limit the script leaves the ornament out.
- ``fake-sloppy``: its first design puts a chest in the roof (the gate refuses a block with an
  inventory) and its first revision is chat instead of JSON; each is right on the next round,
  so a run shows what a repair round looks like.

A follow-up is answered with GOS's own reviser (:mod:`gregtec.build.shell_revise`) on the current
operations. They are the bake-off's hands-on tour and CI's models; what they draw says nothing
about any real model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from gregtec.build import shell_brief, shell_revise
from gregtec.llm.designers import Reply
from gregtec.llm.prices import Usage

PLAIN, ORNATE, SLOPPY = "fake-plain", "fake-ornate", "fake-sloppy"
NAMES = (PLAIN, ORNATE, SLOPPY)
PROVIDER = "fake"
_REQUEST = re.compile(r"<request>\n(.*?)\n</request>", re.DOTALL)
_REPAIR = "Your previous answer was:"
_LINE_TYPE = re.compile(r"Line type: (\w+)\.")
# fake-ornate's build script: it reads the build's facts and the line type it is given
ORNAMENT = """import math
lo, hi = WALLS["lo"], WALLS["hi"]
if MAX_BLOCKS < 4 * (2 * (hi[0] - lo[0] + hi[2] - lo[2]) * (WALL_TOP + 1)):
    print("tight block limit: no ornament")
else:
    for x in (lo[0], hi[0]):
        for z in (lo[2], hi[2]):
            fill((x, 0, z), (x, WALL_TOP + 1, z), "pillar")
    d = min(2, RELIEF)  # buttresses that stand out from the long walls, every 4 blocks
    for x in range(lo[0] + 4, hi[0] - 1, 4):
        for z0, z1 in ((lo[2] - d, lo[2] - 1), (hi[2] + 1, hi[2] + d)):
            if d:
                fill((x, 0, z0), (x, WALL_TOP - 1, z1), "pillar")
    if LINE == "smelting":
        for cx in (lo[0] + 3, hi[0] - 3):
            cz = lo[2] + 3
            for y in range(ROOF_Y, hi[1] + 1):
                for k in range(0, 360, 30):
                    a = math.radians(k)
                    set_block(cx + 1.5 * math.cos(a), y, cz + 1.5 * math.sin(a), "wall")
    else:
        cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
        base = max(ROOF_Y, hi[1] - 2)  # on the ridge, under the top
        for j in range(0, 360, 15):  # a drum of pillars
            b = math.radians(j)
            set_block(cx + 2.5 * math.cos(b), base - 1, cz + 2.5 * math.sin(b), "pillar")
        for k in range(0, 91, 10):  # and a dome on it
            for j in range(0, 360, 15):
                a, b = math.radians(k), math.radians(j)
                set_block(cx + 2.5 * math.cos(a) * math.cos(b), base + 2 * math.sin(a),
                          cz + 2.5 * math.cos(a) * math.sin(b), "roof")
"""


def _line(user: str, key: str) -> Any:  # noqa: ANN401 - the line's JSON
    for line in user.splitlines():
        if line.startswith(key):
            return json.loads(line[len(key) :])
    raise ValueError(f"the brief has no {key!r} line")


def _clears(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"op": "clear", "lo": d["lo"], "hi": d["hi"]} for d in facts["doorways"]]


def plain(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    roles = {p["role"] for p in facts["palette"]}
    top = facts["wall_top"]
    ops: list[dict[str, Any]] = [{"op": "walls", "role": "wall", "y": [0, top]}]
    if "window" in roles and top >= 2:
        ops.append(
            {"op": "windows", "role": "window", "y": [1, 2], "every": 6, "width": 2}
        )
    if "pillar" in roles:
        ops.append({"op": "pillars", "role": "pillar", "y": [0, top], "every": 8})
    ops.append({"op": "roof", "role": "roof", "style": "flat"})
    if "light" in roles:
        ops.append({"op": "lights", "role": "light", "every": 6})
    return [*ops, *_clears(facts)]


def ornate(
    facts: Mapping[str, Any], line_type: str = "general"
) -> list[dict[str, Any]]:
    roles = [p["role"] for p in facts["palette"]]
    top = facts["wall_top"]
    walls = facts.get("walls") or facts["bounds"]  # the wall line (D386)
    lo, hi = walls["lo"], walls["hi"]
    ops: list[dict[str, Any]] = [{"op": "walls", "role": "wall", "y": [0, top]}]
    if "trim" in roles:
        ops.append({"op": "walls", "role": "trim", "y": [0, 0]})
    if "accent" in roles and top >= 3:
        ops.append({"op": "walls", "role": "accent", "y": [top, top]})
    if "window" in roles and top >= 2:
        ops.append(
            {
                "op": "windows",
                "role": "window",
                "y": [1, max(1, top - 1)],
                "every": 4,
                "width": 2,
            }
        )
    if "pillar" in roles:
        ops.append({"op": "pillars", "role": "pillar", "y": [0, top], "every": 4})
    short = min(hi[0] - lo[0], hi[2] - lo[2]) + 1
    roof: dict[str, Any] = {"op": "roof", "role": "roof"}
    if short <= 33:
        roof["style"] = "gable"
        # a low gable in its own volume, so the ornament stands above it
        if facts["headroom"] >= 4:
            roof["lo"] = [lo[0], facts["roof_y"], lo[2]]
            roof["hi"] = [hi[0], hi[1] - 2, hi[2]]
    else:
        roof["style"] = "flat"
        if facts["headroom"] >= 2:
            roof["parapet"] = 1
            roof["parapet_role"] = "wall"
    if roles.count("roof") > 1:
        roof["edge"] = "roof"
        roof["edge_n"] = roles.count("roof") - 1
    ops.append(roof)
    if "light" in roles:
        ops.append({"op": "lights", "role": "light", "every": 4})
    line = f"LINE = {line_type!r}\n"
    ops.append({"op": "script", "script": line + ORNAMENT})
    return [*ops, *_clears(facts)]


def revised(user: str, facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The current operations with the request applied by GOS's reviser (unchanged when it
    can't act on the words: the gate then says nothing changed)."""
    current = _line(user, "current operations: ")
    script = _line(user, "current script: ")
    if script:  # the script runs first (shell_brief.parse)
        current = [{"op": "script", "script": script}, *current]
    m = _REQUEST.search(user)
    words = m.group(1) if m else ""
    rec = {**facts, "ops": current}
    got = shell_revise.revise(rec, words)
    return [dict(o) for o in (got.ops if got.ok else current)]


@dataclass
class FakeDesigner:
    model: str
    provider: str = PROVIDER
    calls: int = 0

    def complete(
        self, system: str, user: str, schema: Mapping[str, Any], max_output_tokens: int
    ) -> Reply:
        self.calls += 1
        facts = _line(user, "facts: ")
        revise = user.startswith("Revise the current design")
        repair = _REPAIR in user
        if self.model == SLOPPY and not repair:
            if revise:
                text = (
                    "Sure! I made the change you asked for. Let me know what you think."
                )
            else:
                top = facts["bounds"]["hi"][1]
                x, z = facts["interior"]["lo"][0], facts["interior"]["lo"][2]
                chest = {
                    "op": "fill",
                    "block": "minecraft:chest@0",
                    "lo": [x, top, z],
                    "hi": [x, top, z],
                }
                text = json.dumps({"ops": [*plain(facts), chest]})
        else:
            if revise:
                ops = revised(user, facts)
            else:
                m = _LINE_TYPE.search(user)
                line = m.group(1) if m else "general"
                ops = ornate(facts, line) if self.model == ORNATE else plain(facts)
            text = shell_brief.answer(ops)
        usage = Usage(input=(len(system) + len(user)) // 4, output=len(text) // 4 + 1)
        return Reply(text, usage)
