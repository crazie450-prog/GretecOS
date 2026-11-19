"""WO-10.9 acceptance: a Lab capture saved as a ``.schematic`` becomes a footprint (G10 defect 1,
D350, D351).

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after the server harness and the schematic harness
(``./gradlew runServer -Pharness`` then ``-Pharness -PharnessSchematic``; CI's mod job). The
schematic harness built a GT multiblock as a player does (an EBF controller turned east in a
block of heat proof casings, with an input bus, an output bus and an energy hatch, each placed
as an item so GT wrote its tile entity), then ``//copy`` + ``//schem save`` through WorldEdit's
own classes, and wrote what it placed to ``gt_multi_capture.placed.json``. Here the server run
is built into a DB (``built_home``, D340), and through the ``gos footprints`` CLI:

- ``coverage`` says the EBF needs a Lab capture (this DB has no extractor dump, so no
  structure rows: the EBF stands in for an addon multi);
- ``capture`` reads the saved ``.schematic`` (its controller found by the DB's machine table, its
  facing from the controller's saved ``mFacing``) and loads it;
- the footprint's controller, hatch cells (by MTE id and HatchElement kind) and bounding box are
  what was placed;
- ``coverage`` then reports the EBF as ``capture``.

Nothing is written outside tmp_path; the harness output is read, never written.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gregtec.build import capture, footprints_cli
from gregtec.build import footprints as fp
from gregtec.db import access

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "mod" / "run" / "server"
OUT = SERVER / "gregteclink" / "schematic-harness" / "out"
RESULT = SERVER / "gregteclink" / "harness-result.json"
GT = "gregtech:gt.blockmachines"
KINDS = {
    "MTEHatchInputBus": "InputBus",
    "MTEHatchOutputBus": "OutputBus",
    "MTEHatchEnergy": "Energy",
}


@pytest.fixture(scope="module")
def placed() -> dict[str, Any]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    doc = OUT / "gt_multi_capture.placed.json"
    if not doc.is_file():
        pytest.fail(f"no schematic harness capture in {OUT} (mod/README.md)")
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    checks = {c["name"]: c for c in result["checks"]}
    for name in ("multi_placed", "multi_copied_with_tile_entities"):
        assert checks[name]["ok"] is True, checks[name]
    out: dict[str, Any] = json.loads(doc.read_text(encoding="utf-8"))
    return out


def _gos(toml: Path, *argv: str) -> tuple[int, str]:
    parser = argparse.ArgumentParser()
    footprints_cli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    code = footprints_cli.run(
        parser.parse_args(["footprints", *argv, "--config", str(toml)]), out
    )
    return code, out.getvalue()


def test_the_saved_schematic_becomes_the_placed_footprint(
    placed: dict[str, Any],
    built_home: Callable[[Path, Path], Path],
    tmp_path: Path,
) -> None:
    home = built_home(SERVER, tmp_path / "home")
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f"[paths]\ngos_home = {json.dumps(home.as_posix())}\n", encoding="utf-8"
    )
    machines = placed["machines"]
    (ctrl,) = [m for m in machines if m["controller"]]
    hatches = [m for m in machines if not m["controller"]]
    mte = int(ctrl["mte"])
    db = access.db_path(home)

    conn = access.connect(db, readonly=True)
    try:
        classes = {
            int(m): str(c).rsplit(".", 1)[-1]
            for m, c in conn.execute("SELECT mte_id, class_name FROM machine")
        }
        multi = conn.execute(
            "SELECT is_multiblock FROM machine WHERE mte_id = ?", (mte,)
        ).fetchone()
    finally:
        conn.close()
    assert multi is not None and tuple(multi) == (1,), (
        "the harness DB's machine table must list the controller"
    )

    code, out = _gos(toml, "coverage", "--json")
    assert code == 0, out
    assert mte in json.loads(out)["catalog_needs_capture"]

    schem = OUT / str(placed["file"])
    before = schem.read_bytes()
    code, out = _gos(toml, "capture", str(schem))
    print("\n" + out)
    assert code == 0, out
    assert f"loaded: {GT}@{mte}|t1|north" in out
    assert schem.read_bytes() == before  # read, never written

    conn = access.connect(db, readonly=True)
    try:
        lib = fp.library(conn)
        (row,) = conn.execute(
            "SELECT source, name_en, size_x, size_y, size_z FROM structure"
            " WHERE registry_name = ? AND meta = ?",
            (GT, mte),
        ).fetchall()
    finally:
        conn.close()
    row = tuple(row)
    assert row[0] == fp.CAPTURE and row[1]  # named from the machine table
    (north,) = lib.for_controller(GT, mte)
    f = lib.get(GT, mte, north.variant, facing=str(placed["facing"]))  # as it stood
    assert f.facing == "east"

    # every placed cell, relative to the controller, in the Lab's frame (it was captured east)
    cx, cy, cz = ctrl["p"]
    w, h, ln = (int(v) for v in str(placed["size"]).split("x"))
    want_cells = {
        (x - cx, y - cy, z - cz)
        for x in range(1, w)  # x 0 is the air column the copy took
        for y in range(h)
        for z in range(ln)
        if (x, y, z) != tuple(ctrl["p"])
    }
    got = {c.d for c in f.cells if c.d != (0, 0, 0)}
    assert got == want_cells
    want_hatches = {
        (m["p"][0] - cx, m["p"][1] - cy, m["p"][2] - cz): (
            (KINDS[classes[int(m["mte"])]],),
            f"{GT}@{m['mte']}",
        )
        for m in hatches
    }
    assert {c.d: (c.hatches, c.placed) for c in f.hatch_cells} == want_hatches
    casing = str(placed["casing"])
    assert {f"{c.block}@{c.meta}" for c in f.blocks if c.d != (0, 0, 0)} == {casing}
    # the bounding box: the casing block, four wide in x, two high, three deep (so the
    # turn to north visibly swaps x and z)
    assert sorted(f.size) == sorted((w - 1, h, ln))
    assert north.size == tuple(row[2:5]) == (ln, h, w - 1)  # turned to north

    code, out = _gos(toml, "coverage", "--json")
    assert code == 0, out
    doc = json.loads(out)
    assert mte not in doc["catalog_needs_capture"]
    code, out = _gos(toml, "coverage", "--all")
    assert code == 0 and "1 capture" in out, out

    # the controller is the one GT tile entity the DB calls a multiblock: no --controller needed
    conn = access.connect(db, readonly=True)
    try:
        again = capture.read_any(schem, conn)
    finally:
        conn.close()
    assert again.controller == f"{GT}@{mte}" and again.tool == "worldedit-copy"
