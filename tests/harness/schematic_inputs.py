"""The shells the schematic harness pastes with worldedit-gtnh (WO-10.4, D310): ``-PharnessSchematic``.

``uv run python tests/harness/schematic_inputs.py`` writes them, through GOS's own writer
(:func:`gregtec.build.schematic.encode`), to ``mod/harness/schematic/in/``, which the harness's
``prepareHarness`` copies into the dev server. They are committed (a few hundred bytes each);
``tests/unit/test_schematic.py`` checks they are current. Every block is a plain block of the dev
runtime (vanilla, GT 5.09.54.265 and its merged addons) at a meta its item places, none above 15
(the dev runtime has no EndlessIDs, so 4-bit metas and ids below 4096).
"""

from __future__ import annotations

import sys
from pathlib import Path

from gregtec.build import schematic
from gregtec.build.palette import Entry

OUT = Path(__file__).resolve().parents[2] / "mod" / "harness" / "schematic" / "in"

STATES = (
    Entry("minecraft:stone", 0),
    Entry("minecraft:glass", 0),
    Entry("minecraft:wool", 14),
    Entry("minecraft:stonebrick", 3),
    Entry("minecraft:quartz_block", 2),
    Entry("gregtech:gt.blockcasings", 2),
    Entry("gregtech:gt.blockcasings5", 0),
    Entry("bartworks:BW_GlasBlocks", 0),
    Entry("GoodGenerator:MAR_Casing", 0),
)


def inputs() -> dict[str, schematic.Schematic]:
    """name -> shell: ``shell`` (5x3x4, every state and air, the default paste offset) and
    ``offset`` (3x2x2, asymmetric, an offset up, east and north of the player)."""
    w, h, ln = 5, 3, 4
    k = len(STATES) + 2  # two of every k cells are air
    cells = [
        schematic.AIR_STATE
        if (v := (x * 7 + y * 3 + z * 5) % k) >= len(STATES)
        else STATES[v]
        for y in range(h)
        for z in range(ln)
        for x in range(w)
    ]
    shell = schematic.Schematic.from_cells(w, h, ln, cells)
    stone, glass = STATES[0], STATES[1]
    offset = schematic.Schematic.from_blocks(
        3,
        2,
        2,
        {(0, 0, 0): stone, (2, 0, 0): glass, (1, 1, 1): stone, (2, 1, 1): glass},
        offset=(2, 1, -5),
    )
    return {"shell": shell, "offset": offset}


def files() -> dict[str, bytes]:
    return {f"{name}.schematic": schematic.encode(s) for name, s in inputs().items()}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in files().items():
        (OUT / name).write_bytes(data)
        print(f"wrote {OUT / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
