"""WO-10.2: the footprint library, its rotation, the Lab capture reader and the coverage report.

The structure rows come from WO-1.8's self-authored fixture (tests/fixtures/gtnh-extractor,
``fixture:*`` blocks) loaded by ``gos structures load``'s own loader. The capture is hand-made
(tests/fixtures/captures/addon_reactor.json, a made-up addon multi captured facing east): no game
data.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gregtec import cli
from gregtec.build import capture, coverage, footprints_cli
from gregtec.build import footprints as fp
from gregtec.db import access
from gregtec.db import structures as structure_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DATASET = FIXTURES / "gtnh-extractor"
CAPTURE = FIXTURES / "captures" / "addon_reactor.json"
GT = "gregtech:gt.blockmachines"

offsets = st.tuples(st.integers(-64, 64), st.integers(-64, 64), st.integers(-64, 64))
facings = st.sampled_from(fp.FACINGS)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(tmp_path / "gos.sqlite")
    structure_db.load(c, DATASET)
    try:
        yield c
    finally:
        c.close()


def _rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(
        conn.execute(
            "SELECT * FROM structure ORDER BY registry_name, meta, variant, source"
        )
    )


# --- rotation -------------------------------------------------------------------------------


def test_the_front_turns_with_the_facing() -> None:
    for facing in fp.FACINGS:
        assert fp.rotate(fp.FRONT["north"], facing) == fp.FRONT[facing]
    # a quarter turn clockwise seen from above: north -> east -> south -> west
    assert fp.rotate((1, 5, 0), "east") == (0, 5, 1)
    assert fp.rotate((1, 5, 0), "south") == (-1, 5, 0)
    assert fp.rotate((1, 5, 0), "west") == (0, 5, -1)


@given(offsets, facings)
def test_unrotate_undoes_rotate(d: fp.Offset, facing: fp.Facing) -> None:
    assert fp.unrotate(fp.rotate(d, facing), facing) == d
    assert fp.rotate(fp.unrotate(d, facing), facing) == d


@given(offsets)
def test_four_quarter_turns_are_the_identity(d: fp.Offset) -> None:
    east = d
    for _ in range(4):
        east = fp.rotate(east, "east")
    assert east == d
    assert fp.rotate(fp.rotate(d, "east"), "east") == fp.rotate(d, "south")
    assert fp.rotate(fp.rotate(d, "south"), "east") == fp.rotate(d, "west")


@given(offsets, facings)
def test_rotation_keeps_height_and_distance(d: fp.Offset, facing: fp.Facing) -> None:
    r = fp.rotate(d, facing)
    assert r[1] == d[1]
    assert r[0] ** 2 + r[2] ** 2 == d[0] ** 2 + d[2] ** 2


@given(facings, facings, st.sampled_from(["t1", "t2"]))
def test_a_rotated_footprint_is_the_same_structure(
    a: fp.Facing, b: fp.Facing, variant: str
) -> None:
    lib = _fixture_library()
    tower = lib.get(GT, 9003, variant)
    fa, fb = tower.rotated(a), tower.rotated(b)
    assert fa.rotated(b) == fb  # any path to a facing gives one footprint
    assert len(fa.cells) == len(tower.cells)
    assert fa.materials() == tower.materials()
    assert {c.hatches for c in fa.cells} == {c.hatches for c in tower.cells}
    # the controller stays at the origin; its front points along the facing
    assert any(c.d == (0, 0, 0) and c.block == GT and c.meta == 9003 for c in fa.cells)
    # a quarter turn swaps the box's width and depth, a half turn keeps them
    sx, sy, sz = tower.size
    expect = (sx, sy, sz) if a in ("north", "south") else (sz, sy, sx)
    assert fa.size == expect
    # the box spans the rotated cells
    assert fa.lo == tuple(min(c.d[i] for c in fa.cells) for i in range(3))
    assert fa.hi == tuple(max(c.d[i] for c in fa.cells) for i in range(3))
    assert fa.key.endswith(f"|{a}")


_LIB: list[fp.Library] = []


def _fixture_library() -> fp.Library:
    """One library from the fixture for the property tests (hypothesis reruns them often)."""
    if not _LIB:
        c = sqlite3.connect(":memory:", isolation_level=None)
        structure_db.load(c, DATASET)
        _LIB.append(fp.library(c))
        c.close()
    return _LIB[0]


def test_bad_facings_are_refused() -> None:
    with pytest.raises(fp.FootprintError, match="facing"):
        fp.parse_facing("up")
    assert fp.parse_facing(" East ") == "east"


# --- the library ----------------------------------------------------------------------------


def test_one_footprint_per_structure_row_with_its_box_and_hatches(
    conn: sqlite3.Connection,
) -> None:
    lib = fp.library(conn)
    rows = _rows(conn)
    assert len(lib.footprints) == len(rows) == 4 and not lib.skipped
    for f, r in zip(lib.footprints, rows, strict=True):
        assert (f.registry_name, f.meta, f.variant) == (
            r["registry_name"],
            r["meta"],
            r["variant"],
        )
        assert f.facing == "north"
        assert f.size == (r["size_x"], r["size_y"], r["size_z"])
        blocks = {
            tuple(b["d"]): (b["block"], b["meta"]) for b in json.loads(r["blocks_json"])
        }
        assert {c.d: (c.block, c.meta) for c in f.blocks} == blocks
        hatches = {
            tuple(h["d"]): tuple(h["kinds"]) for h in json.loads(r["hatches_json"])
        }
        assert {c.d: c.hatches for c in f.hatch_cells} == hatches
    assert len(list(lib.every_facing())) == 4 * len(rows)
    assert len({f.key for f in lib.every_facing()}) == 4 * len(rows)


def test_a_hatch_only_cell_has_no_block_and_widens_the_box(
    conn: sqlite3.Connection,
) -> None:
    furnace = fp.library(conn).get(GT, 9001)
    air = [c for c in furnace.cells if c.block is None]
    assert air and all(c.hatches for c in air)  # hatch-only cells: kinds, no block
    assert furnace.key == "gregtech:gt.blockmachines@9001|t1;coil=1|north"
    assert furnace.name == "Fixture Furnace"
    doc = furnace.to_json()
    assert doc["bbox"]["size"] == list(furnace.size)
    assert doc["choices"]["coil"] == [
        {"value": 1, "block": "fixture:coil@0"},
        {"value": 2, "block": "fixture:coil@1"},
        {"value": 3, "block": "fixture:coil@2"},
    ]
    assert all("hatches" in c for c in doc["cells"] if "block" not in c)


def test_a_tier_choice_swaps_the_channel_cells_only(conn: sqlite3.Connection) -> None:
    lib = fp.library(conn)
    built = lib.get(GT, 9001)
    hot = lib.get(GT, 9001, tiers={"coil": 3})
    assert hot.tiers == (("coil", 3),) and hot.key.endswith("t1;coil=3|north")
    coils = built.materials()["fixture:coil@0"]
    assert hot.materials().get("fixture:coil@2") == coils
    assert "fixture:coil@0" not in hot.materials()
    rest = {k: v for k, v in built.materials().items() if k != "fixture:coil@0"}
    assert {k: v for k, v in hot.materials().items() if k != "fixture:coil@2"} == rest
    assert hot.size == built.size
    with pytest.raises(fp.FootprintError, match="no coil tier 9"):
        lib.get(GT, 9001, tiers={"coil": 9})
    with pytest.raises(fp.FootprintError, match="no tier channel glass"):
        lib.get(GT, 9001, tiers={"glass": 1})


def test_an_ambiguous_tier_channel_is_refused_when_chosen() -> None:
    row = fp.Row(
        registry_name=GT,
        meta=1,
        variant="t1",
        size=(2, 1, 1),
        blocks=(
            ((0, 0, 0), GT, 1),
            ((1, 0, 0), "m:coil", 0),
            ((-1, 0, 0), "m:coil", 1),
        ),
        hatches=(),
        subs={
            "coil": (fp.Tier("coil", 1, "m:coil", 0), fp.Tier("coil", 2, "m:coil", 1))
        },
        caveats=(),
        name=None,
        class_name=None,
        source=fp.EXTRACTOR,
        machine_id=None,
    )
    assert fp.build(row).tiers == ()  # left as built, and out of the key
    with pytest.raises(fp.FootprintError, match="more than one coil"):
        fp.build(row, tiers={"coil": 2})


def test_variant_selection(conn: sqlite3.Connection) -> None:
    lib = fp.library(conn)
    assert lib.get(GT, 9003).variant == "t1"  # the smallest by volume
    assert lib.get(GT, 9003, "t2").size == (3, 4, 3)
    with pytest.raises(fp.FootprintError, match="no variant 't9'"):
        lib.get(GT, 9003, "t9")
    with pytest.raises(fp.FootprintError, match="no footprint for"):
        lib.get(GT, 1)
    assert [f.variant for f in lib.for_mte(9003)] == ["t1", "t2"]


def test_no_structure_columns_is_an_empty_library(tmp_path: Path) -> None:
    c = sqlite3.connect(tmp_path / "x.sqlite")
    c.execute("CREATE TABLE structure(machine_id INT, variant TEXT)")
    assert fp.library(c).footprints == ()
    c.close()


def test_unreadable_rows_and_numeric_ids_are_skipped(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE structure SET blocks_json = ? WHERE meta = 9002",
        (json.dumps([{"d": [0, 0, 0], "block": "4095:3", "meta": 0}]),),
    )
    lib = fp.library(conn)
    assert [s.controller for s in lib.skipped] == [f"{GT}@9002"]
    assert (
        "numeric id" in lib.skipped[0].reason
        or "registry name" in lib.skipped[0].reason
    )
    with pytest.raises(fp.FootprintError):
        fp.block_name("1234")


def test_lineage_and_family_from_the_class_name() -> None:
    base = "gregtech.common.tileentities.machines.multi."
    assert fp.lineage(base + "MTELargeTurbineSteamLegacy") == "legacy"
    assert fp.family(base + "MTELargeTurbineSteamLegacy") == "turbine"
    assert fp.lineage(base + "turbines.MTELargeTurbineSteam") == "current"
    assert fp.family(base + "MTEDieselEngineLegacy") == "lce"
    assert fp.family(base + "MTELargeCombustionEngine") == "lce"
    assert fp.family(base + "MTELargeBoilerBronzeLegacy") == "boiler"
    assert fp.family(base + "MTEElectricBlastFurnace") is None
    assert fp.lineage(None) is None


def test_parse_controller_and_tiers() -> None:
    assert fp.parse_controller("mte:1000") == (GT, 1000)
    assert fp.parse_controller("gregtech:gt.blockmachines@12") == (GT, 12)
    with pytest.raises(fp.FootprintError):
        fp.parse_controller("1000")
    assert fp.parse_tiers(["coil=3", "glass = 2"]) == {"coil": 3, "glass": 2}
    with pytest.raises(fp.FootprintError):
        fp.parse_tiers(["coil"])


# --- captures -------------------------------------------------------------------------------


def test_the_hand_made_capture_reads_turned_to_north() -> None:
    cap = capture.read(CAPTURE)
    assert cap.controller == "fixtureaddon:reactor_controller@0"
    assert cap.facing == "east" and cap.tool == "worldedit-copy"
    assert cap.variant == "t1;tier=2"
    f = fp.build(cap.row())
    assert f.source == fp.CAPTURE and f.size == (3, 3, 3)
    assert len(f.blocks) == 16  # 18 cells, less one air and one placed hatch
    cells = {c.d: c for c in f.cells}
    assert cells[(0, 0, 0)].key == "fixtureaddon:reactor_controller@0"
    assert cells[(1, 0, 2)].key == "fixtureaddon:casing@3"  # behind the controller, +z
    assert cells[(-1, 1, 0)].key == "fixtureaddon:glass@2"
    bus = cells[(-1, 0, 0)]
    assert bus.block is None and bus.hatches == ("Captured",)
    assert bus.placed == "gregtech:gt.blockmachines@70"
    assert cells[(1, 0, 1)].hatches == ("InputHatch", "OutputHatch")
    assert cells[(1, 0, 1)].key == "fixtureaddon:casing@3"
    assert cells[(0, 1, 1)].block is None and cells[(0, 1, 1)].hatches == ("Muffler",)
    assert cells[(0, 2, 1)].hatches == ("Maintenance",)  # hatch-only, over the top
    assert len(f.hatch_cells) == 4
    # facing east again, it stands where it was captured, relative to the controller
    east = f.rotated("east")
    doc = json.loads(CAPTURE.read_text(encoding="utf-8"))
    origin = next(c["p"] for c in doc["cells"] if c["block"] == cap.registry_name)
    world = {
        tuple(c["p"][i] - origin[i] for i in range(3)): (c["block"], c["meta"])
        for c in doc["cells"]
        if c["block"] not in ("minecraft:air", GT)
    }
    assert {c.d: (c.block, c.meta) for c in east.blocks} == world


def _doc(**change: object) -> bytes:
    doc = json.loads(CAPTURE.read_text(encoding="utf-8"))
    doc.update(change)
    return json.dumps(doc).encode("utf-8")


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"format": "schematic"}, "not a gos-capture"),
        ({"schema": 2}, "schema 2"),
        ({"facing": "up"}, "facing"),
        ({"tool": "printer"}, "tool"),
        ({"trigger_stack_size": 0}, "below 1"),
        ({"controller": {"block": "fixtureaddon:nothing", "meta": 0}}, "exactly one"),
        ({"controller": {"block": "1234", "meta": 0}}, "registry name"),
        ({"cells": [{"p": [0, 0, 0], "block": "2:0", "meta": 0}]}, "registry name"),
        ({"cells": [{"p": [0, 0], "block": "a:b", "meta": 0}]}, "offset"),
        ({"cells": [{"p": [0, 0, 0], "block": "a:b", "meta": 70000}]}, "meta"),
        ({"hatches": [{"p": [0, 0, 0], "kinds": []}]}, "no kinds"),
    ],
)
def test_bad_captures_are_refused(change: dict[str, Any], match: str) -> None:
    with pytest.raises(capture.CaptureError, match=match):
        capture.parse(_doc(**change))


def test_a_cell_listed_twice_and_non_json_are_refused() -> None:
    doc = json.loads(CAPTURE.read_text(encoding="utf-8"))
    doc["cells"].append(dict(doc["cells"][0]))
    with pytest.raises(capture.CaptureError, match="twice"):
        capture.parse(json.dumps(doc).encode("utf-8"))
    with pytest.raises(capture.CaptureError, match="JSON"):
        capture.parse(b"\x00not json")
    with pytest.raises(capture.CaptureError, match="can't read"):
        capture.read(CAPTURE.with_name("missing.json"))


def test_a_capture_loads_as_a_structure_row_and_replaces_its_earlier_self(
    conn: sqlite3.Connection,
) -> None:
    cap = capture.read(CAPTURE)
    first = capture.load(conn, cap, CAPTURE)
    again = capture.load(conn, cap, CAPTURE)
    assert not first.replaced and again.replaced
    rows = conn.execute(
        "SELECT source, variant, size_x, size_y, size_z, run_id FROM structure"
        " WHERE registry_name = 'fixtureaddon:reactor_controller'"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("lab-capture", "t1;tier=2", 3, 3, 3, again.run_id)
    ]
    run = conn.execute(
        "SELECT source, dataset FROM structure_run WHERE id = ?", (again.run_id,)
    ).fetchone()
    assert run[0] == "lab-capture" and "worldedit-copy" in run[1]
    lib = fp.library(conn)
    f = lib.get("fixtureaddon:reactor_controller", 0)
    assert f == fp.build(cap.row())
    assert len(lib.footprints) == 5
    # extractor rows reload without touching the capture
    structure_db.load(conn, DATASET)
    assert len(fp.library(conn).footprints) == 5


def test_a_capture_of_an_extractor_controller_is_refused(
    conn: sqlite3.Connection,
) -> None:
    cells = {(0, 0, 0): (GT, 9001), (0, 0, 1): ("fixture:casing", 0)}
    cap = capture.from_cells(cells, controller=(GT, 9001), facing="north")
    with pytest.raises(capture.CaptureError, match="already holds"):
        capture.load(conn, cap, "x.json")
    assert conn.execute("SELECT count(*) FROM structure_run").fetchone()[0] == 1


def test_a_capture_shadowed_by_a_later_extractor_dump_is_skipped(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        "UPDATE structure SET source = 'lab-capture' WHERE meta = 9002"
    )  # a capture loaded before ...
    conn.execute(
        "INSERT INTO structure(registry_name, meta, variant, size_x, size_y, size_z,"
        " blocks_json, hatches_json, subs_json, failures_json, source)"
        " SELECT registry_name, meta, variant, size_x, size_y, size_z, blocks_json,"
        " hatches_json, subs_json, failures_json, 'gtnh-extractor' FROM structure"
        " WHERE meta = 9002"
    )  # ... the extractor reached the controller
    lib = fp.library(conn)
    assert [f.source for f in lib.for_mte(9002)] == ["gtnh-extractor"]
    assert [s.source for s in lib.skipped] == ["lab-capture"]


# --- coverage -------------------------------------------------------------------------------


def _machines(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO machine(mte_id, name_en, is_multiblock) VALUES"
        " (9001, 'Fixture Furnace', 1), (9002, 'Fixture Freezer', 1),"
        " (9003, 'Fixture Tower', 1), (9100, 'Fixture Lathe', 0),"
        " (9200, 'Fixture Turbine', 1)"
    )


def _plan(pid: str, *mtes: tuple[int, str]) -> dict[str, Any]:
    return {
        "id": pid,
        "nodes": [
            {"machine": {"mte": m, "kind": kind, "name": f"m{m}"}, "count": 1}
            for m, kind in mtes
        ],
    }


def test_coverage_of_plans_base_and_catalog(conn: sqlite3.Connection) -> None:
    _machines(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS my_base_machine(mte_id INT, source TEXT, count INT, upd INT)"
    )
    conn.execute("INSERT INTO my_base_machine VALUES (9003, 'manual', 2, 0)")
    power = {
        "id": "pw-1",
        "nodes": [],
        "machines_used": [{"machine": {"mte": 9200, "kind": "multiblock"}, "count": 2}],
    }
    plans = [
        _plan("pl-a", (9001, "multiblock"), (9100, "single")),
        _plan("pl-b", (9001, "multiblock")),
        power,
    ]
    cov = coverage.coverage(conn, plans)
    by = {n.mte: n for n in cov.needed}
    assert sorted(by) == [9001, 9003, 9200]
    assert by[9001].status == "footprint" and by[9001].needed_by == [
        "plan:pl-a",
        "plan:pl-b",
    ]
    assert by[9003].needed_by == ["base"] and by[9003].variants == 2
    assert by[9200].status == "needs capture" and by[9200].name == "Fixture Turbine"
    assert cov.counts(cov.needed) == {"footprint": 2, "capture": 0, "needs capture": 1}
    assert cov.counts(cov.catalog) == {"footprint": 3, "capture": 0, "needs capture": 1}
    text = "\n".join(coverage.render(cov, all_catalog=True))
    assert "mte:9200\tFixture Turbine\tneeds capture" in text
    assert "3 accepted plan(s) read" in text
    assert json.loads(coverage.dumps(cov))["catalog_needs_capture"] == [9200]


def test_coverage_finds_a_capture_through_the_machine_link(
    conn: sqlite3.Connection,
) -> None:
    _machines(conn)
    cells = {(0, 0, 0): ("fixtureaddon:turbine", 0), (0, 1, 0): ("fixture:casing", 0)}
    cap = capture.from_cells(
        cells,
        controller=("fixtureaddon:turbine", 0),
        facing="north",
        class_name="fixtureaddon.MTEFixtureTurbineLegacy",
    )
    capture.load(conn, cap, "t.json")
    mid = conn.execute("SELECT id FROM machine WHERE mte_id = 9200").fetchone()[0]
    conn.execute(
        "UPDATE structure SET machine_id = ? WHERE source = 'lab-capture'", (mid,)
    )
    cov = coverage.coverage(conn, [_plan("pl-a", (9200, "multiblock"))])
    (need,) = cov.needed
    assert need.status == "capture" and need.lineage == "legacy"
    assert need.family == "turbine"
    assert "legacy turbine" in "\n".join(coverage.render(cov))


def test_accepted_plans_are_the_ledger_statuses(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    plans = tmp_path / "data" / "plans"
    plans.mkdir(parents=True)
    for pid, status in (
        ("pl-a", "accepted"),
        ("pl-b", "proposed"),
        ("pl-c", "running"),
        ("pl-d", "accepted"),
    ):
        conn.execute("INSERT INTO plan(id, status) VALUES (?, ?)", (pid, status))
        if pid != "pl-d":
            (plans / f"{pid}.json").write_text(
                json.dumps(_plan(pid, (9001, "multiblock"))), encoding="utf-8"
            )
    found, bad = coverage.accepted_plans(tmp_path, conn)
    assert [p["id"] for p in found] == ["pl-a", "pl-c"] and bad == ["pl-d"]


# --- the CLI --------------------------------------------------------------------------------


def _home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    db = access.db_path(home)
    db.parent.mkdir(parents=True)
    c = access.connect(db)
    structure_db.load(c, DATASET)
    _machines(c)
    c.close()
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f"[paths]\ngos_home = {json.dumps(home.as_posix())}\n", encoding="utf-8"
    )
    return home, toml


def _gos(toml: Path, *argv: str) -> tuple[int, str]:
    parser = argparse.ArgumentParser()
    footprints_cli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    code = footprints_cli.run(
        parser.parse_args(["footprints", *argv, "--config", str(toml)]), out
    )
    return code, out.getvalue()


def test_cli_list_show_coverage_capture(tmp_path: Path) -> None:
    _, toml = _home(tmp_path)
    code, out = _gos(toml, "list")
    assert code == 0 and "4 footprints (x4 facings)" in out
    assert "gregtech:gt.blockmachines@9003|t2|north\tFixture Tower\t3x4x3" in out
    code, out = _gos(toml, "show", "mte:9001", "--facing", "west", "--tier", "coil=2")
    doc = json.loads(out)
    assert code == 0 and doc["key"].endswith("t1;coil=2|west")
    assert doc["front"] == [-1, 0, 0]
    code, out = _gos(toml, "show", "mte:9001", "--tier", "coil=7")
    assert code == 1 and "no coil tier 7" in out
    code, out = _gos(toml, "capture", str(CAPTURE), "--dry-run")
    assert code == 0 and "3x3x3, 19 cells, 4 hatch cells" in out
    code, out = _gos(toml, "list")
    assert "4 footprints" in out  # the dry run wrote nothing
    code, out = _gos(toml, "capture", str(CAPTURE))
    assert (
        code == 0 and "loaded: fixtureaddon:reactor_controller@0|t1;tier=2|north" in out
    )
    code, out = _gos(toml, "list", "--json")
    assert len(out.splitlines()) == 5
    code, out = _gos(toml, "coverage", "--all")
    assert code == 0 and "0 accepted plan(s) read" in out
    assert (
        "machine catalog multis: 4 (3 footprint, 0 capture, 1 need a Lab capture)"
        in out
    )
    assert "mte:9200\tFixture Turbine\tneeds capture" in out
    code, out = _gos(toml, "coverage", "--json")
    assert json.loads(out)["catalog_needs_capture"] == [9200]
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    code, out = _gos(toml, "capture", str(bad))
    assert code == 1 and "Not a capture" in out


def test_cli_without_a_db(tmp_path: Path) -> None:
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f"[paths]\ngos_home = {json.dumps((tmp_path / 'empty').as_posix())}\n",
        encoding="utf-8",
    )
    code, out = _gos(toml, "list")
    assert code == 2 and "gos db build" in out
    code, out = _gos(toml, "capture", str(CAPTURE))
    assert code == 2 and "gos db build" in out


def test_the_gos_cli_has_the_command() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["footprints", "--help"])
    assert exc.value.code == 0


def _as_captured(
    f: fp.Footprint, at: fp.Offset = (10, 70, -5)
) -> dict[fp.Offset, tuple[str, int]]:
    """``f``'s cells as a Lab capture would hold them: world positions, hatch-only cells filled
    with a placed GT hatch."""
    cells: dict[fp.Offset, tuple[str, int]] = {}
    for c in f.cells:
        p = (at[0] + c.d[0], at[1] + c.d[1], at[2] + c.d[2])
        cells[p] = (c.block, int(c.meta or 0)) if c.block else (GT, 70)
    return cells


def test_compare_a_capture_with_its_footprint(conn: sqlite3.Connection) -> None:
    lib = fp.library(conn)
    hot = lib.get(GT, 9001, facing="south", tiers={"coil": 2})
    cells = _as_captured(hot)
    cap = capture.from_cells(cells, controller=(GT, 9001), facing="south")
    result = capture.compare(cap, lib)
    assert result.differences == ()
    assert result.footprint.tiers == (("coil", 2),)  # the tier the capture holds
    wrong = next(p for p, b in cells.items() if b == ("fixture:casing", 0))
    cells[wrong] = ("fixture:casing", 9)
    cells[(99, 99, 99)] = ("fixture:casing", 0)
    cap = capture.from_cells(cells, controller=(GT, 9001), facing="south")
    diffs = capture.compare(cap, lib).differences
    assert len(diffs) == 2
    assert any(
        "footprint fixture:casing@0, capture fixture:casing@9" in d for d in diffs
    )
    assert any("footprint air, capture fixture:casing@0" in d for d in diffs)
    tall = capture.from_cells(
        cells, controller=(GT, 9001), facing="south", trigger_stack_size=2
    )
    with pytest.raises(capture.CaptureError, match="no variant 't2'"):
        capture.compare(tall, lib)
    other = capture.from_cells(
        {(0, 0, 0): ("a:b", 0)}, controller=("a:b", 0), facing="north"
    )
    with pytest.raises(capture.CaptureError, match="no footprint"):
        capture.compare(other, lib)


def test_cli_compare(tmp_path: Path) -> None:
    _, toml = _home(tmp_path)
    tower = _fixture_library().get(GT, 9003, "t2", facing="east")
    doc: dict[str, Any] = {
        "format": "gos-capture",
        "schema": 1,
        "controller": {"block": GT, "meta": 9003},
        "facing": "east",
        "trigger_stack_size": 2,
        "tool": "guidenh-region-wand",
        "cells": [
            {"p": list(p), "block": b, "meta": m}
            for p, (b, m) in _as_captured(tower).items()
        ],
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, out = _gos(toml, "compare", str(path))
    assert code == 0 and "every cell matches" in out
    casing = next(c for c in doc["cells"] if c["block"] == "fixture:casing")
    casing["meta"] = 7
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, out = _gos(toml, "compare", str(path))
    assert code == 1 and "1 cell(s) differ" in out
