"""WO-10.9: a Lab capture saved as a ``.schematic`` becomes a footprint (D350, D351).

The files are laid out as worldedit-gtnh v0.0.10's ``//schem save`` and Schematica-GTNH's save
write them, by test_schematic.py's own encoder (written from their behaviour, independent of the
reader), with GT's tile entities as ``BaseMetaTileEntity.writeToNBT`` saves them at the GT pin
5.09.54.265 (``id`` "BaseMetaTileEntity", ``mID`` an int, ``mFacing`` a short: ForgeDirection's
ordinal). The machine catalog is a hand-made ``machine`` table. Every capture is checked against
the one :func:`capture.from_cells` builds from the same cells, which is what the ``gos-capture``
JSON path builds too, so both paths give the same footprint.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sqlite3
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.build import capture, coverage, footprints_cli
from gregtec.build import footprints as fp
from gregtec.db import access
from gregtec.ingest import nbt


def _encoder() -> ModuleType:
    """test_schematic.py's encoder of worldedit-gtnh and Schematica saves (by path, as pytest)."""
    name = "_wo109_schematic_encoder"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("test_schematic.py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


_enc = _encoder()
worldedit_copy = _enc.worldedit_copy
schematica_save = _enc.schematica_save
type Field = tuple[str, int, Any]

GT = "gregtech:gt.blockmachines"
CASING = ("gregtech:gt.blockcasings", 11)
IMPL = "gregtech.api.metatileentity.implementations."
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
JSON_CAPTURE = FIXTURES / "captures" / "addon_reactor.json"

# the made-up addon multi: mte 7000 (in the catalog as a multiblock), three hatches
CONTROLLER = 7000
IN_BUS, OUT_BUS, ENERGY, ME_BUS, OTHER_MULTI = 7101, 7102, 7103, 7104, 7001
MACHINES = [
    (CONTROLLER, "Fixture Addon Reactor", "fixtureaddon.MTEFixtureReactor", 1),
    (OTHER_MULTI, "Fixture Addon Smelter", "fixtureaddon.MTEFixtureSmelter", 1),
    (IN_BUS, "Input Bus (LV)", IMPL + "MTEHatchInputBus", 0),
    (OUT_BUS, "Output Bus (LV)", IMPL + "MTEHatchOutputBus", 0),
    (ENERGY, "LV Energy Hatch", IMPL + "MTEHatchEnergy", 0),
    (
        ME_BUS,
        "ME Input Bus",
        "gregtech.common.tileentities.machines.MTEHatchInputBusME",
        0,
    ),
]
SIZE = (4, 2, 3)  # x 0 is an air column, as a //copy one wider than the multi
CONTROLLER_AT = (3, 0, 1)
HATCHES = {(1, 0, 0): IN_BUS, (1, 0, 2): OUT_BUS, (2, 1, 1): ENERGY}
EAST = 5  # ForgeDirection.EAST.ordinal()

# the JSON fixture's row as origin/main's capture.parse built it before WO-10.9
_FA = "fixtureaddon:"
PINNED_JSON_ROW = fp.Row(
    registry_name=_FA + "reactor_controller",
    meta=0,
    variant="t1;tier=2",
    size=(3, 3, 3),
    blocks=(
        ((-1, 0, 1), _FA + "casing", 3),
        ((-1, 0, 2), _FA + "casing", 3),
        ((-1, 1, 0), _FA + "glass", 2),
        ((-1, 1, 1), _FA + "glass", 2),
        ((-1, 1, 2), _FA + "glass", 2),
        ((0, 0, 0), _FA + "reactor_controller", 0),
        ((0, 0, 1), _FA + "casing", 3),
        ((0, 0, 2), _FA + "casing", 3),
        ((0, 1, 0), _FA + "glass", 2),
        ((0, 1, 2), _FA + "glass", 2),
        ((1, 0, 0), _FA + "casing", 3),
        ((1, 0, 1), _FA + "casing", 3),
        ((1, 0, 2), _FA + "casing", 3),
        ((1, 1, 0), _FA + "glass", 2),
        ((1, 1, 1), _FA + "glass", 2),
        ((1, 1, 2), _FA + "glass", 2),
    ),
    hatches=(
        ((-1, 0, 0), ("Captured",), "gregtech:gt.blockmachines@70"),
        ((0, 1, 1), ("Muffler",), None),
        ((0, 2, 1), ("Maintenance",), None),
        ((1, 0, 1), ("InputHatch", "OutputHatch"), None),
    ),
    subs={},
    caveats=(),
    name="Fixture Addon Reactor",
    class_name="fixtureaddon.tile.FixtureReactor",
    source="lab-capture",
    machine_id=None,
)

type Cells = dict[tuple[int, int, int], tuple[str, int]]
type Tile = tuple[tuple[int, int, int], str | None, int | None, int | None]


def multi(machines: Mapping[tuple[int, int, int], int] | None = None) -> Cells:
    """The multi's cells: casings around the controller and hatches (GT cells at world meta 0)."""
    held = {CONTROLLER_AT: CONTROLLER, **HATCHES} if machines is None else machines
    cells: Cells = {}
    for x in range(1, 4):
        for y in range(2):
            for z in range(3):
                cells[(x, y, z)] = (GT, 0) if (x, y, z) in held else CASING
    return cells


def gt_tiles(
    machines: Mapping[tuple[int, int, int], int] | None = None,
    facing: int | None = EAST,
) -> list[Tile]:
    held = {CONTROLLER_AT: CONTROLLER, **HATCHES} if machines is None else machines
    return [
        (p, "BaseMetaTileEntity", m, facing if p == CONTROLLER_AT else 3)
        for p, m in held.items()
    ]


def tile_fields(tiles: Sequence[Tile]) -> list[list[Field]]:
    out: list[list[Field]] = []
    for (x, y, z), te_id, mte, facing in tiles:
        f: list[Field] = [("x", nbt.INT, x), ("y", nbt.INT, y), ("z", nbt.INT, z)]
        if te_id is not None:
            f.insert(0, ("id", nbt.STRING, te_id))
        if mte is not None:
            f.append(("mID", nbt.INT, mte))
        if facing is not None:
            f.append(("mFacing", nbt.SHORT, facing))
        f.append(("mOwnerName", nbt.STRING, "Clayton"))  # dropped by the reader
        out.append(f)
    return out


def save(
    cells: Cells,
    tiles: Sequence[Tile],
    *,
    size: tuple[int, int, int] = SIZE,
    writer: str = "worldedit",
    first_id: int = 1,
    mapping: bool = True,
    fields: list[list[Field]] | None = None,
) -> bytes:
    """A save of ``cells`` (others air): world ids from ``first_id`` up, in the file's order;
    ``fields`` replaces the tile entities' tags :func:`tile_fields` makes of ``tiles``."""
    w, h, ln = size
    names = sorted({b for b, _ in cells.values()})
    ids = {n: first_id + i for i, n in enumerate(names)}
    flat = []
    for y in range(h):
        for z in range(ln):
            for x in range(w):
                b, m = cells.get((x, y, z), ("minecraft:air", 0))
                flat.append((0, 0) if b == "minecraft:air" else (ids[b], m))
    table = ({"minecraft:air": 0} | ids) if mapping else None
    te = tile_fields(tiles) if fields is None else fields
    if writer == "worldedit":
        return worldedit_copy(size, flat, table, tiles=te)
    return schematica_save(size, flat, table, tiles=te)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(tmp_path / "gos.sqlite")
    c.executemany(
        "INSERT INTO machine(mte_id, name_en, class_name, is_multiblock) VALUES (?,?,?,?)",
        MACHINES,
    )
    try:
        yield c
    finally:
        c.close()


def write(tmp_path: Path, data: bytes, name: str = "multi.schematic") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def expected(tool: str = "worldedit-copy") -> capture.Capture:
    """What the JSON path's :func:`capture.from_cells` builds from the same cells."""
    return capture.from_cells(
        multi(),
        controller_at=CONTROLLER_AT,
        facing="east",
        mte_ids={CONTROLLER_AT: CONTROLLER, **HATCHES},
        hatch_kinds={
            (1, 0, 0): ["InputBus"],
            (1, 0, 2): ["OutputBus"],
            (2, 1, 1): ["Energy"],
        },
        name="Fixture Addon Reactor",
        class_name="fixtureaddon.MTEFixtureReactor",
        tool=tool,
    )


# --- the .schematic path ----------------------------------------------------------------------


def test_a_worldedit_save_becomes_the_footprint_the_json_path_builds(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    path = write(tmp_path, save(multi(), gt_tiles()))
    cap = capture.read_any(path, db)
    assert cap == expected()
    assert cap.controller == f"{GT}@{CONTROLLER}" and cap.facing == "east"
    f = fp.build(cap.row())
    assert f.size == (3, 2, 3)  # the air column is dropped; turned to north
    assert len(f.cells) == 18
    kinds = sorted(k for c in f.hatch_cells for k in c.hatches)
    assert kinds == ["Energy", "InputBus", "OutputBus"]
    # each hatch is kept as placed, by MTE id (registry name + meta, never a numeric id)
    assert {c.placed for c in f.hatch_cells} == {
        f"{GT}@{IN_BUS}",
        f"{GT}@{OUT_BUS}",
        f"{GT}@{ENERGY}",
    }
    # facing east again, the cells stand where they were captured, relative to the controller
    east = fp.build(cap.row(), facing="east")
    got = {c.d for c in east.cells}
    want = {
        (x - 3, y, z - 1)
        for (x, y, z), (b, _) in multi().items()
        if b != "minecraft:air"
    }
    assert got == want


@pytest.mark.parametrize(
    ("writer", "first_id", "tool"),
    [
        ("worldedit", 300, "worldedit-copy"),  # AddBlocks, even cell in the low nibble
        ("worldedit", 4200, "worldedit-copy"),  # AddBlocks2 too (EndlessIDs)
        ("schematica", 300, "schematica-save"),  # even cell in the high nibble
    ],
)
def test_both_nibble_orders_read_the_same_capture(
    tmp_path: Path, db: sqlite3.Connection, writer: str, first_id: int, tool: str
) -> None:
    data = save(multi(), gt_tiles(), writer=writer, first_id=first_id)
    assert capture.read_any(write(tmp_path, data), db) == expected(tool)


def test_the_file_kind_is_its_content_not_its_extension(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    schem = write(tmp_path, save(multi(), gt_tiles()), "looks_like.json")
    assert capture.read_any(schem, db) == expected()
    js = write(tmp_path, JSON_CAPTURE.read_bytes(), "looks_like.schematic")
    assert capture.read_any(js, None) == capture.read(JSON_CAPTURE)
    assert capture.kind(b"\xef\xbb\xbf {") == "json"
    with pytest.raises(capture.CaptureError, match="neither a gos-capture"):
        capture.read_any(write(tmp_path, b"PK\x03\x04zip", "x.schematic"), db)


def test_the_json_path_is_unchanged(tmp_path: Path) -> None:
    """The fixture's capture, pinned literally (as the JSON path built it before WO-10.9), so a
    change to the shared path shows here as well as in test_build_footprints.py's older capture tests."""
    raw = JSON_CAPTURE.read_bytes()
    via_any = capture.read_any(JSON_CAPTURE, None)
    assert via_any == capture.read(JSON_CAPTURE) == capture.parse(raw)
    assert via_any.row() == PINNED_JSON_ROW
    assert via_any.tool == "worldedit-copy" and via_any.facing == "east"
    with pytest.raises(capture.CaptureError, match="are for a .schematic"):
        capture.read_any(JSON_CAPTURE, None, capture.SchematicOptions(facing="north"))


def test_the_controller_named_by_its_cell(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    two = {CONTROLLER_AT: CONTROLLER, (1, 1, 1): OTHER_MULTI, **HATCHES}
    path = write(tmp_path, save(multi(two), gt_tiles(two)))
    with pytest.raises(capture.CaptureError, match="2 tile entities are multiblock"):
        capture.read_any(path, db)
    opts = capture.SchematicOptions(controller_at=CONTROLLER_AT)
    cap = capture.read_any(path, db, opts)
    assert cap.meta == CONTROLLER
    # the other multi's controller is a placed GT machine like a hatch: kept, kind unknown
    assert ((capture.CAPTURED,), f"{GT}@{OTHER_MULTI}") in [
        (k, p) for _, k, p in cap.hatches
    ]
    # without a DB, --controller is enough (no names, no hatch kinds)
    bare = capture.read_any(path, None, opts)
    assert bare.meta == CONTROLLER and bare.name is None
    assert {k for _, k, _ in bare.hatches} == {(capture.CAPTURED,)}


def test_a_hatch_class_outside_hatch_element_stays_captured(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    held = {CONTROLLER_AT: CONTROLLER, (1, 0, 0): ME_BUS}
    cap = capture.read_any(write(tmp_path, save(multi(held), gt_tiles(held))), db)
    assert [(k, p) for _, k, p in cap.hatches] == [
        ((capture.CAPTURED,), f"{GT}@{ME_BUS}")
    ]


def test_facing_from_the_option_when_the_file_saved_none(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    path = write(tmp_path, save(multi(), gt_tiles(facing=None)))
    with pytest.raises(capture.CaptureError, match="saved no facing.*--facing"):
        capture.read_any(path, db)
    cap = capture.read_any(path, db, capture.SchematicOptions(facing="east"))
    assert cap == expected()
    up = write(tmp_path, save(multi(), gt_tiles(facing=1)), "up.schematic")
    with pytest.raises(capture.CaptureError, match=r"mFacing is 1 \(up or down\)"):
        capture.read_any(up, db)
    assert (
        capture.read_any(up, db, capture.SchematicOptions(facing="east")) == expected()
    )


def test_a_facing_that_disagrees_with_the_saved_one_is_refused(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    path = write(tmp_path, save(multi(), gt_tiles()))
    with pytest.raises(capture.CaptureError, match="saved facing is east"):
        capture.read_any(path, db, capture.SchematicOptions(facing="north"))


def test_trigger_and_channels_make_the_variant(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    path = write(tmp_path, save(multi(), gt_tiles()))
    opts = capture.SchematicOptions(trigger_stack_size=3, channels={"height": 2})
    assert capture.read_any(path, db, opts).variant == "t3;height=2"


# --- refusals (D351) ----------------------------------------------------------------------------


def _refused(
    tmp_path: Path,
    db: sqlite3.Connection | None,
    data: bytes,
    match: str,
    opts: capture.SchematicOptions | None = None,
) -> None:
    with pytest.raises(capture.CaptureError, match=match):
        capture.read_any(write(tmp_path, data), db, opts)


def test_no_controller_is_refused(tmp_path: Path, db: sqlite3.Connection) -> None:
    _refused(
        tmp_path,
        db,
        save(multi(HATCHES), gt_tiles(HATCHES)),
        "no tile entity is a multiblock controller.*--controller x,y,z",
    )


def test_without_a_db_the_controller_must_be_named(tmp_path: Path) -> None:
    _refused(tmp_path, None, save(multi(), gt_tiles()), "needs the DB's machine table")


def test_a_controller_cell_that_is_air_or_outside_is_refused(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    data = save(multi(), gt_tiles())
    air = capture.SchematicOptions(controller_at=(0, 0, 0))
    _refused(tmp_path, db, data, r"\[0, 0, 0\] is air", air)
    out = capture.SchematicOptions(controller_at=(9, 0, 0))
    _refused(tmp_path, db, data, "outside the", out)


def test_a_controller_cell_that_is_not_a_multi_controller_is_refused(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    data = save(multi(), gt_tiles())
    casing = capture.SchematicOptions(controller_at=(2, 0, 1), facing="east")
    _refused(
        tmp_path,
        db,
        data,
        r"\[2, 0, 1\] is gregtech:gt.blockcasings@11, not a GT",
        casing,
    )
    _refused(tmp_path, None, data, "not a GT machine", casing)  # without a DB too
    bus = capture.SchematicOptions(controller_at=(1, 0, 0))
    _refused(
        tmp_path,
        db,
        data,
        r"mID 7101 Input Bus \(LV\).*not a multiblock controller",
        bus,
    )
    # an mID the catalog doesn't hold is not refused: the cell is named, the DB can't say
    unknown = {CONTROLLER_AT: 7999, **HATCHES}
    named = capture.SchematicOptions(controller_at=CONTROLLER_AT)
    cap = capture.read_any(
        write(tmp_path, save(multi(unknown), gt_tiles(unknown))), db, named
    )
    assert cap.meta == 7999 and cap.name is None


@pytest.mark.parametrize(("rotation", "flip"), [(1, 0), (0, 1), (2, 1)])
def test_a_rotated_or_flipped_controller_is_refused(
    tmp_path: Path, db: sqlite3.Connection, rotation: int, flip: int
) -> None:
    fields = tile_fields(gt_tiles())
    for f in fields:
        if ("x", nbt.INT, CONTROLLER_AT[0]) in f and (
            "z",
            nbt.INT,
            CONTROLLER_AT[2],
        ) in f:
            f += [("eRotation", nbt.BYTE, rotation), ("eFlip", nbt.BYTE, flip)]
    _refused(
        tmp_path,
        db,
        save(multi(), [], fields=fields),
        rf"rotated or flipped \(eRotation {rotation}, eFlip {flip}\)",
    )


def test_an_unrotated_enhanced_controller_is_read(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    # MTEEnhancedMultiBlockBase saves eRotation 0 and eFlip 0, as the harness's EBF does
    fields = tile_fields(gt_tiles())
    for f in fields:
        f += [("eRotation", nbt.BYTE, 0), ("eFlip", nbt.BYTE, 0)]
    assert capture.read_any(write(tmp_path, save(multi(), [], fields=fields)), db) == (
        expected()
    )


def test_a_gt_tile_entity_without_its_coordinates_is_refused(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    fields = tile_fields(gt_tiles())
    fields[1] = [f for f in fields[1] if f[0] != "y"]
    _refused(
        tmp_path,
        db,
        save(multi(), [], fields=fields),
        "BaseMetaTileEntity tile entity .* has no integer x, y and z",
    )
    # another block's tile entity without them is left alone, as any non-GT tile entity
    chest = [("id", nbt.STRING, "Chest"), ("x", nbt.INT, 0)]
    ok = save(multi(), [], fields=[*tile_fields(gt_tiles()), chest])
    assert capture.read_any(write(tmp_path, ok), db) == expected()


def test_a_gt_cell_without_its_tile_entity_is_refused(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    tiles = [t for t in gt_tiles() if t[0] != (1, 0, 0)]  # the input bus lost its NBT
    _refused(tmp_path, db, save(multi(), tiles), r"\[1, 0, 0\] has no MTE id")
    no_id = [(p, i, None if p == (1, 0, 0) else m, f) for p, i, m, f in gt_tiles()]
    _refused(tmp_path, db, save(multi(), no_id), r"\[1, 0, 0\] has no MTE id")


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ("pipe", "GT pipe or cable"),
        ("chest", "has a Chest tile entity"),
        ("nameless", "has a nameless tile entity"),
        ("on_casing", "stands on gregtech:gt.blockcasings"),
        ("outside", "outside the"),
        ("twice", "two tile entities at"),
    ],
)
def test_tile_entities_that_disagree_with_the_blocks_are_refused(
    tmp_path: Path, db: sqlite3.Connection, change: str, match: str
) -> None:
    tiles = gt_tiles()
    bus = (1, 0, 0)
    if change == "pipe":
        tiles = [
            (p, "BaseMetaPipeEntity" if p == bus else i, m, f) for p, i, m, f in tiles
        ]
    elif change == "chest":
        tiles = [(p, "Chest" if p == bus else i, m, f) for p, i, m, f in tiles]
    elif change == "nameless":
        tiles = [(p, None if p == bus else i, m, f) for p, i, m, f in tiles]
    elif change == "on_casing":
        tiles = [*tiles, ((2, 0, 0), "BaseMetaTileEntity", IN_BUS, 3)]
    elif change == "outside":
        tiles = [*tiles, ((7, 0, 0), "Chest", None, None)]
    else:
        tiles = [*tiles, (bus, "BaseMetaTileEntity", IN_BUS, 3)]
    _refused(tmp_path, db, save(multi(), tiles), match)


def test_an_air_only_capture_is_refused(tmp_path: Path, db: sqlite3.Connection) -> None:
    _refused(tmp_path, db, save({}, []), "only air")


def test_a_capture_over_64_a_side_is_refused(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    # x 1..65 is 65 cells (turned to north, along z); x 1..64 is 64
    cells = multi() | {(65, 0, 1): CASING}
    data = save(cells, gt_tiles(), size=(66, 2, 3))
    _refused(tmp_path, db, data, "spans 3 x 2 x 65: at most 64 a side")
    ok = save(multi() | {(64, 0, 1): CASING}, gt_tiles(), size=(65, 2, 3))
    assert fp.build(capture.read_any(write(tmp_path, ok), db).row()).size == (3, 2, 64)


def test_unknown_blocks_are_refused(tmp_path: Path, db: sqlite3.Connection) -> None:
    # no BlockMapping: the world ids can't be named (EndlessIDs: never keyed on a number)
    _refused(
        tmp_path,
        db,
        save(multi(), gt_tiles(), mapping=False),
        "not a readable .schematic: ids .* have no name",
    )


def test_a_broken_schematic_is_refused(tmp_path: Path, db: sqlite3.Connection) -> None:
    _refused(tmp_path, db, b"\x1f\x8b\x08\x00garbage", "not a readable .schematic")


def test_the_file_is_read_not_written(tmp_path: Path, db: sqlite3.Connection) -> None:
    path = write(tmp_path, save(multi(), gt_tiles()))
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    capture.load(db, capture.read_any(path, db), path)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


# --- the CLI ------------------------------------------------------------------------------------


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    dbp = access.db_path(home)
    dbp.parent.mkdir(parents=True)
    c = access.connect(dbp)
    c.executemany(
        "INSERT INTO machine(mte_id, name_en, class_name, is_multiblock) VALUES (?,?,?,?)",
        MACHINES,
    )
    c.close()
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f"[paths]\ngos_home = {json.dumps(home.as_posix())}\n", encoding="utf-8"
    )
    return toml


def _gos(toml: Path, *argv: str) -> tuple[int, str]:
    parser = argparse.ArgumentParser()
    footprints_cli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    code = footprints_cli.run(
        parser.parse_args(["footprints", *argv, "--config", str(toml)]), out
    )
    return code, out.getvalue()


def test_cli_capture_of_a_schematic_then_coverage(tmp_path: Path) -> None:
    toml = _home(tmp_path)
    code, out = _gos(toml, "coverage", "--json")
    assert code == 0
    assert json.loads(out)["catalog_needs_capture"] == [CONTROLLER, OTHER_MULTI]
    path = write(tmp_path, save(multi(), gt_tiles()))
    code, out = _gos(toml, "capture", str(path), "--dry-run")
    assert code == 0 and "3x2x3, 18 cells, 3 hatch cells" in out, out
    assert "note: variant t1, no channels" in out  # forgetting --trigger is visible
    code, out = _gos(toml, "capture", str(path))
    assert code == 0 and f"loaded: {GT}@{CONTROLLER}|t1|north" in out, out
    code, out = _gos(toml, "coverage", "--all")
    assert code == 0, out
    assert (
        "machine catalog multis: 2 (0 footprint, 1 capture, 1 need a Lab capture)"
        in out
    )
    code, out = _gos(toml, "coverage", "--json")
    assert json.loads(out)["catalog_needs_capture"] == [OTHER_MULTI]
    # the same file through compare: the library now holds its own capture
    code, out = _gos(toml, "compare", str(path))
    assert code == 0, out


def test_cli_options_and_refusals(tmp_path: Path) -> None:
    toml = _home(tmp_path)
    path = write(tmp_path, save(multi(), gt_tiles(facing=None)))
    code, out = _gos(toml, "capture", str(path), "--dry-run")
    assert code == 1 and "Not a capture" in out and "--facing" in out
    code, out = _gos(
        toml,
        "capture",
        str(path),
        "--dry-run",
        "--facing",
        "east",
        "--controller",
        "3,0,1",
    )
    assert code == 0 and "3x2x3" in out, out
    code, out = _gos(toml, "capture", str(path), "--controller", "3,0")
    assert code == 1 and "is not x,y,z" in out
    code, out = _gos(toml, "capture", str(JSON_CAPTURE), "--facing", "east")
    assert code == 1 and "are for a .schematic" in out
    code, out = _gos(
        toml,
        "capture",
        str(path),
        "--trigger",
        "2",
        "--channel",
        "height=3",
        "--dry-run",
        "--facing",
        "east",
    )
    assert code == 0 and "t2;height=3" in out, out
    assert "note: variant t1" not in out


def test_coverage_counts_a_schematic_capture(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    path = write(tmp_path, save(multi(), gt_tiles()))
    capture.load(db, capture.read_any(path, db), path)
    cov = coverage.coverage(db, [], with_base=False)
    status = {n.mte: n.status for n in cov.catalog}
    assert status == {OTHER_MULTI: "needs capture", CONTROLLER: "capture"}
