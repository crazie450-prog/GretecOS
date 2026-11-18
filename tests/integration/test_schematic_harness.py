"""Paste -> //copy -> identical blocks, with worldedit-gtnh v0.0.10 in a dev server (WO-10.4, D310).

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after ``./gradlew runServer -Pharness -PharnessSchematic``
(CI's mod job; mod/README.md). The harness pasted each GOS-written shell of
``mod/harness/schematic/in/`` with WorldEdit's own reader and paste, checked the world by registry
name + meta at the player + the file's WEOffset, and saved WorldEdit's copy of that box; here
GOS's reader reads each copy (world ids through its BlockMapping, AddBlocks in WorldEdit's order)
and every cell must hold the same block state as the input, with the same WEOffset.

The dev runtime has no EndlessIDs, so ids stay below 4096 and metas below 16 here; ids above 4095,
metas above 15 (worldedit-gtnh #30) and the paste offset in the real pack are the Lab round trip
(runbook ``schematic_roundtrip.md``, V16).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gregtec.build import schematic
from gregtec.ingest import nbt

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "mod" / "run" / "server"
IN = REPO / "mod" / "harness" / "schematic" / "in"
OUT = SERVER / "gregteclink" / "schematic-harness" / "out"
RESULT = SERVER / "gregteclink" / "harness-result.json"


@pytest.fixture(scope="module")
def ran() -> dict[str, object]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if not OUT.is_dir():
        pytest.fail(f"no schematic harness output in {OUT} (mod/README.md)")
    result: dict[str, object] = json.loads(RESULT.read_text(encoding="utf-8"))
    return result


def test_the_harness_passed(ran: dict[str, object]) -> None:
    names = {c["name"] for c in ran["checks"]}  # type: ignore[attr-defined, index]
    assert ran["pass"] is True, ran
    for f in IN.glob("*.schematic"):
        assert {f"pasted_at_offset_{f.name}", f"copied_{f.name}"} <= names


@pytest.mark.parametrize("name", ["shell.schematic", "offset.schematic"])
def test_copy_holds_the_pasted_blocks(ran: dict[str, object], name: str) -> None:
    pasted = schematic.read((IN / name).read_bytes())
    copied = schematic.read((OUT / name).read_bytes())
    assert copied.writer == "worldedit"
    assert schematic.diff(pasted.schematic, copied.schematic) == []
    assert (
        copied.schematic.offset == pasted.schematic.offset
    )  # copied from where it was pasted
    assert copied.tile_entities == () and copied.entities == 0
    # the copy carries the dev world's own ids: GOS's compact ids were remapped by name
    assert copied.schematic.names() == pasted.schematic.names()
    ids_in = nbt.read((IN / name).read_bytes())[1]["BlockMapping"]
    ids_out = nbt.read((OUT / name).read_bytes())[1]["BlockMapping"]
    assert any(ids_out[n] != ids_in[n] for n in pasted.schematic.names())
