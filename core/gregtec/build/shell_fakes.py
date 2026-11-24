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

In site mode (D422; the brief's facts say ``"mode": "site"``) the building comes first: plain,
ornate and sloppy draw a roofed hall in a corner of SITE whose air holds :data:`ROOM` times
NEED (walls, roof and the ornament on the hall's own volume, a door three wide in its west
wall), and GOS fits the machines into it. Two more show the fit's refusals:

- ``fake-cramped``: its first hall holds only :data:`CRAMPED_ROOM` of NEED (the fit refuses
  it for floor or volume); the repair is the plain hall (refused, repaired, accepted).
- ``fake-tower``: enough air, but on floor three blocks wide (a spine with three-wide wings,
  a comb): no machine wider than that fits, so it ends refused, every round.

In layout mode those two answer as fake-plain.

A follow-up is answered with GOS's own reviser (:mod:`gregtec.build.shell_revise`) on the current
operations. They are the bake-off's hands-on tour and CI's models; what they draw says nothing
about any real model.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gregtec.build import shell_brief, shell_revise
from gregtec.llm.designers import Reply
from gregtec.llm.prices import Usage

PLAIN, ORNATE, SLOPPY = "fake-plain", "fake-ornate", "fake-sloppy"
NAMES = (PLAIN, ORNATE, SLOPPY)
CRAMPED, TOWER = "fake-cramped", "fake-tower"  # site mode's fit refusals (D422)
ALL = (*NAMES, CRAMPED, TOWER)
ROOM = (
    shell_brief.ROOM
)  # a site hall's air, in NEED.required (D422 §2.8: 1.5; see ROOM)
CRAMPED_ROOM = 0.7
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


def site_script(hall: Mapping[str, Any], top: int, line_type: str) -> str:
    """fake-ornate's ornament on a site hall: the same script, its walls the hall's."""
    head = f"LINE = {line_type!r}\nHALL = {json.dumps(dict(hall))}\nTOP = {top}\n"
    return head + ORNAMENT.replace(
        'lo, hi = WALLS["lo"], WALLS["hi"]', 'lo, hi = HALL["lo"], HALL["hi"]'
    ).replace("WALL_TOP", "TOP").replace("ROOF_Y", "(TOP + 1)")


def _line(user: str, key: str) -> Any:  # noqa: ANN401 - the line's JSON
    for line in user.splitlines():
        if line.startswith(key):
            return json.loads(line[len(key) :])
    raise ValueError(f"the brief has no {key!r} line")


def _clears(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"op": "clear", "lo": d["lo"], "hi": d["hi"]} for d in facts["doorways"]]


def site(facts: Mapping[str, Any]) -> bool:
    return facts.get("mode") == "site"


def hall(facts: Mapping[str, Any], room: float = ROOM) -> tuple[list[int], list[int]]:
    """A site hall's walls (lo, hi; y 0 up to the eaves, the roof one above): in SITE's west
    corner, its air ``room`` times NEED.required up to cap_height and its floor ``room`` times
    NEED.floor_required (a tall cap would otherwise make a floor the keep-outs don't fit on), at
    least the widest keep-out across, centred in z."""
    lo, hi, need = facts["site"]["lo"], facts["site"]["hi"], facts["need"]
    cap = int(need["cap_height"])
    sx, sz = hi[0] - lo[0] + 1, hi[2] - lo[2] + 1
    # inner floor, cap high
    area = math.ceil(room * max(need["required"] / cap, need.get("floor_required", 0)))
    wide = max(need["widest"][0], need["widest"][2])
    ix = min(max(math.ceil(math.sqrt(area)), wide), sx - 2)
    iz = min(max(math.ceil(area / ix), wide), sz - 2)
    if ix * iz < area:  # SITE is too narrow for a square: as long as it can be
        ix = min(math.ceil(area / iz), sx - 2)
    z0 = lo[2] + (sz - iz - 2) // 2
    return [lo[0], lo[1], z0], [lo[0] + ix + 1, lo[1] + cap - 1, z0 + iz + 1]


def _door(
    facts: Mapping[str, Any], lo: Sequence[int], hi: Sequence[int]
) -> dict[str, Any]:
    """A clear three wide in the hall's west wall, door_height high."""
    mid = (lo[2] + hi[2]) // 2
    top = lo[1] + int(facts["need"]["door_height"]) - 1
    return {"op": "clear", "lo": [lo[0], lo[1], mid - 1], "hi": [lo[0], top, mid + 1]}


def site_plain(facts: Mapping[str, Any], room: float = ROOM) -> list[dict[str, Any]]:
    """fake-plain on a site: the hall, windows, pillars and a flat roof, then its door."""
    roles = {p["role"] for p in facts["palette"]}
    lo, hi = hall(facts, room)
    top = hi[1]
    vol = {"lo": lo, "hi": hi}
    ops: list[dict[str, Any]] = [{"op": "walls", "role": "wall", "y": [0, top], **vol}]
    if "window" in roles and top >= 3:
        ops.append(
            {"op": "windows", "role": "window", "y": [1, 2], "every": 6, "width": 2}
            | vol
        )
    if "pillar" in roles:
        ops.append({"op": "pillars", "role": "pillar", "y": [0, top], "every": 8} | vol)
    roof = {"lo": [lo[0], top + 1, lo[2]], "hi": [hi[0], top + 1, hi[2]]}
    ops.append({"op": "roof", "role": "roof", "style": "flat", **roof})
    return [*ops, _door(facts, lo, hi)]


def site_ornate(
    facts: Mapping[str, Any], line_type: str = "general"
) -> list[dict[str, Any]]:
    """fake-ornate on a site: the hall with a plinth, an eaves band, tall windows, pillars, a
    gable roof in its own volume where it fits, and the ornament script round the hall."""
    roles = [p["role"] for p in facts["palette"]]
    lo, hi = hall(facts)
    top, ceiling = hi[1], facts["site"]["hi"][1]
    vol = {"lo": lo, "hi": hi}
    ops: list[dict[str, Any]] = [{"op": "walls", "role": "wall", "y": [0, top], **vol}]
    if "trim" in roles:
        ops.append({"op": "walls", "role": "trim", "y": [0, 0], **vol})
    if "accent" in roles and top >= 3:
        ops.append({"op": "walls", "role": "accent", "y": [top, top], **vol})
    if "window" in roles and top >= 3:
        ops.append(
            {"op": "windows", "role": "window", "y": [1, top - 1], "every": 4}
            | {"width": 2, **vol}
        )
    if "pillar" in roles:
        ops.append({"op": "pillars", "role": "pillar", "y": [0, top], "every": 4} | vol)
    short = min(hi[0] - lo[0], hi[2] - lo[2]) + 1
    roof: dict[str, Any] = {"op": "roof", "role": "roof"}
    if short <= 33 and ceiling - 2 > top + 1:
        roof |= {"style": "gable", "lo": [lo[0], top + 1, lo[2]]}
        roof["hi"] = [hi[0], ceiling - 2, hi[2]]
    else:
        roof |= {"style": "flat", "lo": [lo[0], top + 1, lo[2]]}
        roof["hi"] = [hi[0], top + 1, hi[2]]
    if roles.count("roof") > 1:
        roof["edge"] = "roof"
        roof["edge_n"] = roles.count("roof") - 1
    ops.append(roof)
    the_hall = {"lo": lo, "hi": [hi[0], ceiling, hi[2]]}
    ops.append({"op": "script", "script": site_script(the_hall, top, line_type)})
    return [*ops, _door(facts, lo, hi)]


def site_tower(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """fake-tower: a comb over the whole SITE, every floor three blocks wide: a spine along
    the west wall and wings east from it, cap_height high, one roof over all, a door in the
    spine's west wall."""
    lo, hi, need = facts["site"]["lo"], facts["site"]["hi"], facts["need"]
    top = lo[1] + int(need["cap_height"]) - 1
    ops: list[dict[str, Any]] = [
        {
            "op": "walls",
            "role": "wall",
            "y": [0, top],
            "lo": lo,
            "hi": [*hi[:1], top, hi[2]],
        },
        {
            "op": "fill",
            "role": "roof",
            "lo": [lo[0], top + 1, lo[2]],
            "hi": [hi[0], top + 1, hi[2]],
        },
    ]
    for z in range(lo[2] + 4, hi[2], 4):  # walls between the wings, east of the spine
        ops.append(
            {
                "op": "fill",
                "role": "wall",
                "lo": [lo[0] + 4, 0, z],
                "hi": [hi[0] - 1, top, z],
            }
        )
    return [*ops, _door(facts, lo, [hi[0], top, hi[2]])]


def plain(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    if site(facts):
        return site_plain(facts)
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
    if site(facts):
        return site_ornate(facts, line_type)
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
    if site(facts):  # as a saved site build's record says (D422)
        rec["fit"] = {"mode": "site"}
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
                text = json.dumps(
                    {"ops": [*plain(facts), chest]}
                    | ({"entrance": None, "zones": []} if site(facts) else {})
                )
        else:
            if revise:
                ops = revised(user, facts)
            elif site(facts) and self.model == TOWER:
                ops = site_tower(facts)
            elif site(facts) and self.model == CRAMPED and not repair:
                ops = site_plain(facts, CRAMPED_ROOM)
            else:
                m = _LINE_TYPE.search(user)
                line = m.group(1) if m else "general"
                ops = ornate(facts, line) if self.model == ORNATE else plain(facts)
            if not site(facts):
                text = shell_brief.answer(ops)
            elif revise:  # the current door and zones, sent again to keep them
                text = shell_brief.answer(
                    ops,
                    _line(user, "current entrance: "),
                    _line(user, "current zones: "),
                )
            else:
                text = shell_brief.answer(ops, None, [])
        usage = Usage(input=(len(system) + len(user)) // 4, output=len(text) // 4 + 1)
        return Reply(text, usage)
