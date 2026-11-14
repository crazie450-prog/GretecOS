"""Schematic I/O (WO-10.4, plan 7.10 "Schematic writer", D309-D311).

The writer's bytes are pinned by a golden file; write -> read round trips are checked as
properties over sizes, palettes of 1 to 255 and more block types, and 16-bit metas; the reader is
fed files laid out as worldedit-gtnh v0.0.10's ``SchematicWriter`` and Schematica 1.12.6-GTNH's
``SchematicAlpha`` write them (by a test encoder written here from their behaviour, independent
of the module under test), including world ids above 4095; the palette rule's refusals go through
:func:`schematic.write_shell` with a real catalog DB and a level.dat.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import struct
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gregtec.build import palette, schematic, worldreg
from gregtec.build.palette import Entry
from gregtec.db import access
from gregtec.db import blocks as block_db
from gregtec.ingest import nbt
from gregtec.ingest import palette as ingest

E = Entry

# --- an independent NBT encoder for the fakes -------------------------------------------------

type Field = tuple[str, int, Any]


def _s(text: str) -> bytes:
    b = text.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _payload(kind: int, v: Any) -> bytes:  # noqa: ANN401
    if kind == nbt.BYTE:
        return struct.pack(">b", v)
    if kind == nbt.SHORT:
        return struct.pack(">h", v)
    if kind == nbt.INT:
        return struct.pack(">i", v)
    if kind == nbt.STRING:
        return _s(v)
    if kind == nbt.BYTE_ARRAY:
        return struct.pack(">i", len(v)) + bytes(v)
    if kind == nbt.LIST:
        elem, items = v
        return struct.pack(">bi", elem, len(items)) + b"".join(
            _payload(elem, i) for i in items
        )
    if kind == nbt.COMPOUND:
        return b"".join(bytes((k,)) + _s(n) + _payload(k, x) for n, k, x in v) + b"\0"
    raise AssertionError(kind)


def nbt_file(fields: Sequence[Field], root: str = "Schematic") -> bytes:
    return gzip.compress(
        bytes((nbt.COMPOUND,)) + _s(root) + _payload(nbt.COMPOUND, fields)
    )


def _signed(v: int) -> int:
    return v - 0x10000 if v > 0x7FFF else v


def worldedit_copy(
    size: tuple[int, int, int],
    cells: Sequence[tuple[int, int]],
    mapping: dict[str, int] | None,
    offset: tuple[int, int, int] = (-3, 0, 1),
    tile_entities: Sequence[tuple[int, int, int]] = (),
) -> bytes:
    """A ``//copy`` + ``//schem save`` as worldedit-gtnh v0.0.10's SchematicWriter lays it out:
    the world's ids (low 8 bits in Blocks, bits 8-11 in AddBlocks and 12-15 in AddBlocks2, each
    ``(n >> 1) + 1`` bytes, even cell in the low nibble), Data the low meta byte, AddData the
    high byte once a meta is above 15, BlockMapping name -> world id as a short."""
    w, h, ln = size
    n = w * h * ln
    blocks, data = bytearray(n), bytearray(n)
    add = add2 = add_data = None
    for i, (bid, meta) in enumerate(cells):
        if bid > 255:
            add = add if add is not None else bytearray((n >> 1) + 1)
            nib = (bid >> 8) & 0xF
            add[i >> 1] = (
                (add[i >> 1] & 0xF0 | nib)
                if i % 2 == 0
                else (add[i >> 1] & 0xF | nib << 4)
            )
        if bid > 4095:
            add2 = add2 if add2 is not None else bytearray((n >> 1) + 1)
            nib = (bid >> 12) & 0xF
            add2[i >> 1] = (
                (add2[i >> 1] & 0xF0 | nib)
                if i % 2 == 0
                else (add2[i >> 1] & 0xF | nib << 4)
            )
        blocks[i] = bid & 0xFF
        data[i] = meta & 0xFF
        if meta > 15:
            add_data = add_data if add_data is not None else bytearray(n)
            add_data[i] = meta >> 8
    f: list[Field] = [
        ("Width", nbt.SHORT, w),
        ("Length", nbt.SHORT, ln),
        ("Height", nbt.SHORT, h),
        ("Materials", nbt.STRING, "Alpha"),
        ("WEOriginX", nbt.INT, 120),
        ("WEOriginY", nbt.INT, 64),
        ("WEOriginZ", nbt.INT, -40),
        ("WEOffsetX", nbt.INT, offset[0]),
        ("WEOffsetY", nbt.INT, offset[1]),
        ("WEOffsetZ", nbt.INT, offset[2]),
        ("Blocks", nbt.BYTE_ARRAY, blocks),
        ("Data", nbt.BYTE_ARRAY, data),
        (
            "TileEntities",
            nbt.LIST,
            (
                nbt.COMPOUND,
                [
                    [
                        ("id", nbt.STRING, "Chest"),
                        ("x", nbt.INT, x),
                        ("y", nbt.INT, y),
                        ("z", nbt.INT, z),
                    ]
                    for x, y, z in tile_entities
                ],
            ),
        ),
        ("Entities", nbt.LIST, (nbt.COMPOUND, [])),
    ]
    if add is not None:
        f.append(("AddBlocks", nbt.BYTE_ARRAY, add))
    if add2 is not None:
        f.append(("AddBlocks2", nbt.BYTE_ARRAY, add2))
    if add_data is not None:
        f.append(("AddData", nbt.BYTE_ARRAY, add_data))
    if mapping is not None:
        f.append(
            (
                "BlockMapping",
                nbt.COMPOUND,
                [(k, nbt.SHORT, _signed(v)) for k, v in mapping.items()],
            )
        )
        f.append(("ItemMapping", nbt.COMPOUND, []))
    return nbt_file(f)


def schematica_save(
    size: tuple[int, int, int],
    cells: Sequence[tuple[int, int]],
    mapping: dict[str, int] | None,
    extended: bool = False,
) -> bytes:
    """A Schematica-GTNH save as SchematicAlpha writes it: its classic format packs the bits above
    8 two cells a byte with the even cell in the **high** nibble (only when some id is above 255);
    its extended-id format writes AddBlocks as one byte per cell. SchematicaMapping, an Icon."""
    w, h, ln = size
    n = w * h * ln
    blocks = bytes(bid & 0xFF for bid, _ in cells)
    data = bytes(meta & 0xFF for _, meta in cells)
    extra = [bid >> 8 for bid, _ in cells]
    f: list[Field] = [
        ("Icon", nbt.COMPOUND, [("id", nbt.SHORT, 1), ("Count", nbt.BYTE, 1)]),
        ("Width", nbt.SHORT, w),
        ("Length", nbt.SHORT, ln),
        ("Height", nbt.SHORT, h),
        ("Materials", nbt.STRING, "Alpha"),
        ("Blocks", nbt.BYTE_ARRAY, blocks),
        ("Data", nbt.BYTE_ARRAY, data),
        ("Entities", nbt.LIST, (nbt.COMPOUND, [])),
        ("TileEntities", nbt.LIST, (nbt.COMPOUND, [])),
    ]
    if extended:
        f.append(("AddBlocks", nbt.BYTE_ARRAY, bytes(extra)))
    elif any(extra):
        nib = bytearray((n + 1) // 2)
        for i in range(0, n, 2):
            nib[i // 2] = (extra[i] << 4 | (extra[i + 1] if i + 1 < n else 0)) & 0xFF
        f.append(("AddBlocks", nbt.BYTE_ARRAY, bytes(nib)))
    if mapping is not None:
        f.append(
            (
                "SchematicaMapping",
                nbt.COMPOUND,
                [(k, nbt.SHORT, _signed(v)) for k, v in mapping.items()],
            )
        )
    return nbt_file(f)


# --- the golden file --------------------------------------------------------------------------

# 2x1x2: stone at (0, 0, 0), an MV casing at (1, 0, 1); offset (-1, 0, 1). Checked by hand against
# plan 7.10: root Schematic; Width 2, Height 1, Length 2 (shorts); Materials Alpha; Blocks
# 02 00 00 01 and Data 00 00 00 02 (cell (y*L + z)*W + x; ids by sorted name: casings 1,
# stone 2); WEOrigin 0 0 0; WEOffset -1 0 1; TileEntities and Entities empty compound lists;
# BlockMapping = SchematicaMapping = {minecraft:air: 0, casings: 1, stone: 2}; ItemMapping {}.
GOLDEN = bytes.fromhex(
    "0a0009536368656d617469630200055769647468000202000648656967687400010200064c656e677468"
    "00020800094d6174657269616c730005416c706861070006426c6f636b7300000004020000010700044461"
    "7461000000040000000203000957454f726967696e580000000003000957454f726967696e5900000000"
    "03000957454f726967696e5a0000000003000957454f666673657458ffffffff03000957454f66667365"
    "74590000000003000957454f66667365745a0000000109000c54696c65456e7469746965730a00000000"
    "090008456e7469746965730a000000000a000c426c6f636b4d617070696e6702000d6d696e6563726166"
    "743a616972000002001867726567746563683a67742e626c6f636b636173696e6773000102000f6d696e"
    "6563726166743a73746f6e650002000a0011536368656d61746963614d617070696e6702000d6d696e65"
    "63726166743a616972000002001867726567746563683a67742e626c6f636b636173696e677300010200"
    "0f6d696e6563726166743a73746f6e650002000a000b4974656d4d617070696e670000"
)


def golden_schematic() -> schematic.Schematic:
    return schematic.Schematic.from_blocks(
        2,
        1,
        2,
        {
            (0, 0, 0): E("minecraft:stone", 0),
            (1, 0, 1): E("gregtech:gt.blockcasings", 2),
        },
    )


def test_golden_bytes() -> None:
    s = golden_schematic()
    assert s.offset == (-1, 0, 1)  # default_offset: (-floor(W/2), 0, 1)
    assert schematic.to_nbt(s) == GOLDEN
    data = schematic.encode(s)
    assert data[:2] == b"\x1f\x8b" and data[4:8] == b"\0\0\0\0"  # gzip, no timestamp
    assert gzip.decompress(data) == GOLDEN
    assert schematic.encode(s) == data  # stable
    name, root = nbt.read(data)
    assert name == "Schematic"
    assert (
        "AddBlocks" not in root and "AddData" not in root and "AddBlocks2" not in root
    )
    assert (
        root["TileEntities"] == []
        and root["Entities"] == []
        and root["ItemMapping"] == {}
    )
    assert (
        root["BlockMapping"]
        == root["SchematicaMapping"]
        == {
            "minecraft:air": 0,
            "gregtech:gt.blockcasings": 1,
            "minecraft:stone": 2,
        }
    )


def test_add_data_only_above_meta_255_and_add_blocks_only_when_asked() -> None:
    s = schematic.Schematic.from_blocks(
        1, 1, 2, {(0, 0, 0): E("gregtech:gt.blockframes", 255)}
    )
    root = nbt.read(schematic.encode(s))[1]
    assert "AddData" not in root and root["Data"] == b"\xff\0"
    s = schematic.Schematic.from_blocks(
        1, 1, 2, {(0, 0, 1): E("gregtech:gt.blockframes", 0x1305)}
    )
    root = nbt.read(schematic.encode(s))[1]
    assert root["Data"] == b"\0\x05" and root["AddData"] == b"\0\x13"
    root = nbt.read(schematic.encode(s, add_blocks=True))[1]
    assert "AddBlocks" not in root  # 1 type: nothing above 255 to store


def test_more_than_255_types_need_a_split_or_add_blocks() -> None:
    s = many_types(300)
    with pytest.raises(schematic.SchematicError, match="split the shell"):
        schematic.to_nbt(s)
    root = nbt.read(schematic.encode(s, add_blocks=True))[1]
    add = root["AddBlocks"]
    assert len(add) == (len(s.cells) + 1) // 2
    # ids by sorted name: t:b000 is 1 ... t:b299 is 300 (0x12C): cell index 299
    i = s.index(*next(p for p, e in cells_of(s) if e.block == "t:b299"))
    assert root["Blocks"][i] == 0x2C
    nib = add[i >> 1] & 0xF if i % 2 == 0 else add[i >> 1] >> 4
    assert nib == 1  # WorldEdit's order: even cell in the low nibble
    assert schematic.read(schematic.encode(s, add_blocks=True)).order == "low-first"


def cells_of(s: schematic.Schematic) -> Iterator[tuple[tuple[int, int, int], Entry]]:
    for y in range(s.height):
        for z in range(s.length):
            for x in range(s.width):
                yield (x, y, z), s.at(x, y, z)


def many_types(k: int, width: int = 20) -> schematic.Schematic:
    names = [E(f"t:b{i:03d}", i % 16) for i in range(k)]
    length = -(-k // width)
    return schematic.Schematic.from_blocks(
        width, 1, length, {(i % width, 0, i // width): e for i, e in enumerate(names)}
    )


def test_model_refusals() -> None:
    with pytest.raises(schematic.SchematicError, match="outside 1..32767"):
        schematic.Schematic.from_cells(0, 1, 1, [])
    with pytest.raises(schematic.SchematicError, match="outside the"):
        schematic.Schematic.from_blocks(1, 1, 1, {(1, 0, 0): E("minecraft:stone", 0)})
    with pytest.raises(schematic.SchematicError, match="minecraft:air@0"):
        schematic.Schematic(
            1, 1, 1, (E("minecraft:stone", 0),), schematic.array("H", [0])
        )
    with pytest.raises(schematic.SchematicError, match="a meta"):
        schematic.Schematic.from_cells(1, 1, 1, [E("minecraft:stone", 70000)])


# --- write -> read round trips -----------------------------------------------------------------


@st.composite
def schematics(draw: st.DrawFn, max_types: int = 255) -> schematic.Schematic:
    w = draw(st.integers(1, 24))
    h = draw(st.integers(1, 6))
    ln = draw(st.integers(1, 24))
    n = w * h * ln
    k = draw(st.integers(1, min(n, max_types)))
    names = [f"mod{i % 7}:block_{i}" for i in range(k)]
    metas = st.one_of(
        st.integers(0, 15), st.integers(16, 255), st.integers(256, 0xFFFF)
    )
    states = [E(name, draw(metas)) for name in names]
    if draw(st.booleans()):  # a second meta of the same block: one id, two states
        states.append(E(names[0], (states[0].meta + 1) % 16))
    picks = draw(st.lists(st.integers(-1, len(states) - 1), min_size=n, max_size=n))
    cells = [schematic.AIR_STATE if p < 0 else states[p] for p in picks]
    cells[: len(names)] = states[: len(names)]  # every block type is used
    offset = draw(st.tuples(*[st.integers(-1000, 1000)] * 3))
    return schematic.Schematic.from_cells(w, h, ln, cells, offset)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(schematics())
def test_round_trip_is_identical(s: schematic.Schematic) -> None:
    c = schematic.read(schematic.encode(s))
    assert schematic.diff(s, c.schematic) == []
    assert c.schematic.offset == s.offset and c.origin == (0, 0, 0)
    assert c.writer == "worldedit" and c.order is None and c.notes == ()
    assert len(s.names()) <= 255


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(256, 700), st.integers(1, 3))
def test_round_trip_above_255_types_with_add_blocks(k: int, h: int) -> None:
    s = many_types(k, width=31)
    s = schematic.Schematic.from_cells(
        s.width, h, s.length, [e for _, e in cells_of(s)] * h, (5, -2, 7)
    )
    c = schematic.read(schematic.encode(s, add_blocks=True))
    assert schematic.diff(s, c.schematic) == []
    assert c.order == "low-first" and c.schematic.offset == (5, -2, 7)


# --- reading captures ---------------------------------------------------------------------------

MAPPING = {
    "minecraft:air": 0,
    "minecraft:stone": 1,
    "gregtech:gt.blockcasings": 5123,  # EndlessIDs: above 4095
    "gregtech:gt.blockcasings5": 300,
    "bartworks:BW_GlasBlocks": 30001,
    "gregtech:gt.blockframes": 65000,  # a negative short in the file
}


def capture_cells() -> list[tuple[int, int]]:
    return [
        (1, 0),
        (5123, 2),
        (0, 0),
        (300, 1),
        (30001, 0),
        (65000, 305),  # meta above 255: AddData
        (5123, 15),
        (0, 0),
        (1, 0),
        (300, 0),
        (65000, 20),  # meta 16..255: AddData 0
        (30001, 3),
    ]


def expected_capture() -> list[Entry]:
    names = {v: k for k, v in MAPPING.items()}
    return [
        schematic.AIR_STATE if bid == 0 else E(names[bid], meta)
        for bid, meta in capture_cells()
    ]


def test_reads_a_worldedit_copy_with_ids_above_4095() -> None:
    data = worldedit_copy(
        (3, 2, 2), capture_cells(), MAPPING, tile_entities=[(1, 0, 1)]
    )
    c = schematic.read(data)
    assert c.writer == "worldedit" and c.order == "low-first"
    assert [e for _, e in cells_of(c.schematic)] == expected_capture()
    assert c.schematic.offset == (-3, 0, 1) and c.origin == (120, 64, -40)
    assert c.tile_entities == ((1, 0, 1),)
    assert any("tile entities" in n for n in c.notes)


def test_paste_then_copy_gives_identical_blocks() -> None:
    """What worldedit-gtnh does with a GOS shell (D310): it remaps the compact ids to the world's
    by name, so a //copy of the paste holds the world's ids (here above 4095); read back, every
    cell is the same block state."""
    s = golden_schematic()
    world_ids = {
        "minecraft:air": 0,
        "minecraft:stone": 1,
        "gregtech:gt.blockcasings": 5123,
    }
    root = nbt.read(schematic.encode(s))[1]
    to_world = {v: world_ids[k] for k, v in root["BlockMapping"].items()}
    cells = [
        (to_world[b], d) for b, d in zip(root["Blocks"], root["Data"], strict=True)
    ]
    copy = worldedit_copy((s.width, s.height, s.length), cells, world_ids, s.offset)
    c = schematic.read(copy)
    assert schematic.diff(s, c.schematic) == [] and c.schematic.offset == s.offset


def test_reads_schematica_saves_in_both_formats() -> None:
    cells = [(bid, meta & 15) for bid, meta in capture_cells() if bid < 4096]
    mapping = {k: v for k, v in MAPPING.items() if v < 4096}
    names = {v: k for k, v in mapping.items()}
    want = [schematic.AIR_STATE if b == 0 else E(names[b], m) for b, m in cells]
    size = (len(cells), 1, 1)
    c = schematic.read(schematica_save(size, cells, mapping))
    assert c.writer == "schematica" and c.order == "high-first"
    assert [e for _, e in cells_of(c.schematic)] == want
    assert any("no WEOffset" in n for n in c.notes) and c.schematic.offset == (0, 0, 0)
    c = schematic.read(schematica_save(size, cells, mapping, extended=True))
    assert c.order == "byte" and [e for _, e in cells_of(c.schematic)] == want


def test_a_file_without_a_mapping_needs_the_source_world() -> None:
    data = worldedit_copy((3, 2, 2), capture_cells(), None)
    with pytest.raises(schematic.CaptureError, match="give the world it was copied in"):
        schematic.read(data)
    source = {v: k for k, v in MAPPING.items()}
    c = schematic.read(data, source_ids=source)
    assert [e for _, e in cells_of(c.schematic)] == expected_capture()
    assert any("source world" in n for n in c.notes)
    del source[30001]
    with pytest.raises(schematic.CaptureError, match="30001.*doesn't know them"):
        schematic.read(data, source_ids=source)


def _bare(cells: list[tuple[int, int]], add: bytes, **extra: Any) -> bytes:  # noqa: ANN401
    n = len(cells)
    f: list[Field] = [
        ("Width", nbt.SHORT, n),
        ("Height", nbt.SHORT, 1),
        ("Length", nbt.SHORT, 1),
        ("Materials", nbt.STRING, "Alpha"),
        ("Blocks", nbt.BYTE_ARRAY, bytes(b & 0xFF for b, _ in cells)),
        ("Data", nbt.BYTE_ARRAY, bytes(m for _, m in cells)),
        ("AddBlocks", nbt.BYTE_ARRAY, add),
        ("TileEntities", nbt.LIST, (nbt.COMPOUND, [])),
    ]
    for k, v in extra.items():
        f.append((k, nbt.COMPOUND, [(n, nbt.SHORT, i) for n, i in v.items()]))
    return nbt_file(f)


def test_nibble_order_detected_or_refused() -> None:
    # cell 0 is id 0x101, cell 1 id 0x001: WorldEdit packs 0x01; read high nibble first it is
    # cell 0 id 0x001 and cell 1 id 0x101. Both orders give ids {1, 257}.
    cells = [(0x101, 0), (0x001, 0), (0, 0)]
    add = bytes((0x01, 0x00))
    ids = {0: "minecraft:air", 1: "minecraft:stone", 0x101: "minecraft:glass"}
    with pytest.raises(schematic.CaptureError, match="both nibble orders"):
        schematic.read(_bare(cells, add), source_ids=ids)
    # the writer's tags decide when both fit
    mapping = {v: k for k, v in ids.items()}
    c = schematic.read(_bare(cells, add, BlockMapping=mapping))
    assert c.order == "low-first" and c.schematic.at(0, 0, 0).block == "minecraft:glass"
    assert any("both AddBlocks nibble orders fit" in n for n in c.notes)
    c = schematic.read(_bare(cells, add, SchematicaMapping=mapping))
    assert (
        c.order == "high-first" and c.schematic.at(1, 0, 0).block == "minecraft:glass"
    )
    # only one order fits: detected, even against the writer's tags
    only_high = {0: "minecraft:air", 1: "minecraft:stone", 0x200: "minecraft:glass"}
    cells = [(0x200, 0), (0x001, 0), (0, 0)]  # high first: byte 0x20
    c = schematic.read(
        _bare(
            cells, bytes((0x20, 0)), BlockMapping={v: k for k, v in only_high.items()}
        )
    )
    assert (
        c.order == "high-first" and c.schematic.at(0, 0, 0).block == "minecraft:glass"
    )
    assert any("detected in Schematica's" in n for n in c.notes)
    with pytest.raises(schematic.CaptureError, match="in either nibble order"):
        schematic.read(
            _bare(cells, bytes((0x20, 0))), source_ids={1: "minecraft:stone"}
        )
    # a writer's file whose ids fit neither order: refused by the ids left without a name
    with pytest.raises(schematic.CaptureError, match="ids 513 have no name"):
        schematic.read(
            _bare(cells, bytes((0x20, 0)), BlockMapping={"minecraft:stone": 1})
        )


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda f: [("x", nbt.INT, 1)], "Materials is"),
        (
            lambda f: [*f[:3], ("Materials", nbt.STRING, "Classic"), *f[4:]],
            "not 'Alpha'",
        ),
        (lambda f: [*f[:4], ("Blocks", nbt.BYTE_ARRAY, b"\0"), *f[5:]], "one per cell"),
        (lambda f: [("Width", nbt.SHORT, -2), *f[1:]], "Width is -2"),
        (
            lambda f: [*f, ("AddBlocks", nbt.BYTE_ARRAY, b"")],
            "AddBlocks has 0 bytes",
        ),
        (
            lambda f: [
                *f,
                (
                    "BlockMapping",
                    nbt.COMPOUND,
                    [
                        ("minecraft:stone", nbt.SHORT, 1),
                        ("minecraft:dirt", nbt.SHORT, 1),
                    ],
                ),
            ],
            "both 'minecraft:stone' and 'minecraft:dirt'",
        ),
        (
            lambda f: [*f, ("BlockMapping", nbt.COMPOUND, [("1", nbt.SHORT, 1)])],
            "not a registry name",
        ),
    ],
)
def test_bad_files_are_refused_with_a_reason(change: Any, match: str) -> None:  # noqa: ANN401
    fields: list[Field] = [
        ("Width", nbt.SHORT, 2),
        ("Height", nbt.SHORT, 1),
        ("Length", nbt.SHORT, 2),
        ("Materials", nbt.STRING, "Alpha"),
        ("Blocks", nbt.BYTE_ARRAY, b"\1\1\1\1"),
        ("Data", nbt.BYTE_ARRAY, b"\0\0\0\0"),
        ("TileEntities", nbt.LIST, (nbt.COMPOUND, [])),
    ]
    with pytest.raises(schematic.CaptureError, match=match):
        schematic.read(nbt_file(change(fields)), source_ids={1: "minecraft:stone"})
    with pytest.raises(schematic.CaptureError, match="not 'Schematic'"):
        schematic.read(nbt_file(fields, root="Other"))
    with pytest.raises(schematic.CaptureError, match="not a schematic"):
        schematic.read(b"\x1f\x8bnot gzip")


def test_diff_reports_size_and_cells() -> None:
    a = golden_schematic()
    b = schematic.Schematic.from_blocks(2, 1, 2, {(0, 0, 0): E("minecraft:stone", 1)})
    assert schematic.diff(a, b) == [
        "(0, 0, 0): minecraft:stone@0 vs minecraft:stone@1",
        "(1, 0, 1): gregtech:gt.blockcasings@2 vs minecraft:air@0",
    ]
    assert schematic.diff(a, b, limit=1)[-1] == "... 1 more cells differ"
    assert schematic.diff(a, many_types(3)) == ["size 2x1x2 vs 20x1x1"]


# --- the palette rule and splitting (write_shell) ----------------------------------------------


def _state(block: str, meta: int = 0, has_te: bool = False) -> dict[str, Any]:
    return {
        "block": block,
        "meta": meta,
        "item": {"item": block, "meta": meta},
        "name": block.split(":")[1],
        "unlocalized": "tile.x",
        "mod": block.split(":")[0],
        "has_te": has_te,
        "opaque": True,
        "full_cube": True,
        "normal_cube": True,
        "standard_render": True,
        "liquid": False,
        "falls": False,
        "light": 0,
        "light_opacity": 255,
        "tab": "buildingBlocks",
        "faces": ["stone"] * 6,
    }


CATALOG = [
    _state("minecraft:stone"),
    _state("gregtech:gt.blockcasings", 2),
    _state("gregtech:gt.blockcasings5", 0),
    _state("gregtech:gt.blockmachines", 0, has_te=True),
    _state("minecraft:chest", 0, has_te=True),
    _state("gregtech:gt.blockores", 20),
    _state("minecraft:wool", 3),
    *[_state(f"t:b{i:03d}", i % 16) for i in range(700)],
]
WORLD = sorted(
    {s["block"] for s in CATALOG if s["block"] != "minecraft:wool"} | {"minecraft:air"}
)


def level_dat(blocks: Sequence[str]) -> bytes:
    entries = [
        [("K", nbt.STRING, "\x01" + b), ("V", nbt.INT, 4096 + i)]
        for i, b in enumerate(blocks)
    ]
    entries.append([("K", nbt.STRING, "\x02minecraft:stick"), ("V", nbt.INT, 280)])
    return nbt_file(
        [
            ("Data", nbt.COMPOUND, [("LevelName", nbt.STRING, "Lab")]),
            ("FML", nbt.COMPOUND, [("ItemData", nbt.LIST, (nbt.COMPOUND, entries))]),
        ],
        root="",
    )


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    run = tmp_path / "run"
    run.mkdir()
    (run / ingest.FILE).write_text(
        "".join(json.dumps(s) + "\n" for s in CATALOG), encoding="utf-8"
    )
    (run / ingest.TEXTURES).write_text("", encoding="utf-8")
    c = access.connect(tmp_path / "gos.sqlite")
    c.execute(
        "INSERT INTO export_run(id, fingerprint, run_key) VALUES (1, '', 'run-a')"
    )
    block_db.write(c, 1, ingest.read(run))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def world(tmp_path: Path) -> worldreg.WorldRegistry:
    w = tmp_path / "saves" / "lab-shells"
    w.mkdir(parents=True)
    (w / "level.dat").write_bytes(level_dat(WORLD))
    return worldreg.read(w)


def test_write_shell_writes_one_file_up_to_255_types(
    conn: sqlite3.Connection, world: worldreg.WorldRegistry
) -> None:
    s = golden_schematic()
    out = schematic.write_shell(conn, world, s)
    assert out.how == "single" and len(out.parts) == 1
    assert gzip.decompress(out.parts[0].data) == GOLDEN
    assert out.report.ok and "no AddBlocks" in out.notes[0]
    assert any("without //rotate" in n for n in out.notes)
    k255 = many_types(255)
    assert schematic.write_shell(conn, world, k255).how == "single"


@pytest.mark.parametrize(
    ("state", "why"),
    [
        (E("gregtech:gt.blockmachines", 0), "never pastes machines"),
        (E("minecraft:chest", 0), "has a tile entity"),
        (E("minecraft:stone", 1), "not a placeable state"),
        (E("nomod:nothing", 0), "not in the block catalog"),
        (E("minecraft:wool", 3), "registry"),
        (E("gregtech:gt.blockores", 20), "worldedit-gtnh #30"),
    ],
)
def test_write_shell_refuses_the_palette_rule(
    conn: sqlite3.Connection, world: worldreg.WorldRegistry, state: Entry, why: str
) -> None:
    s = schematic.Schematic.from_blocks(
        2, 1, 1, {(0, 0, 0): E("minecraft:stone", 0), (1, 0, 0): state}
    )
    with pytest.raises(schematic.ShellRefused, match=why) as exc:
        schematic.write_shell(conn, world, s)
    assert any(line.startswith("REFUSED " + state.key) for line in exc.value.lines)
    assert any(line.startswith("ok      minecraft:stone@0") for line in exc.value.lines)


def test_write_shell_refuses_an_all_air_shell(
    conn: sqlite3.Connection, world: worldreg.WorldRegistry
) -> None:
    with pytest.raises(schematic.ShellRefused, match="the palette is empty"):
        schematic.write_shell(conn, world, schematic.Schematic.from_blocks(2, 2, 2, {}))


def _reassemble(s: schematic.Schematic, parts: Sequence[schematic.Part]) -> list[Entry]:
    cells: list[Entry | None] = [None] * len(s.cells)
    for p in parts:
        sub = schematic.read(p.data).schematic
        assert sub.offset == tuple(
            a + b for a, b in zip(s.offset, p.corner, strict=True)
        )
        assert len(sub.names()) <= 255
        cx, cy, cz = p.corner
        for (x, y, z), e in cells_of(sub):
            i = s.index(cx + x, cy + y, cz + z)
            assert cells[i] is None, "parts overlap"
            cells[i] = e
    assert None not in cells, "parts leave a gap"
    return [e for e in cells if e is not None]


@pytest.mark.parametrize(
    ("k", "width", "height"),
    [
        (300, 20, 1),
        (700, 31, 2),
        (300, 300, 1),
    ],  # rows; layers and rows; a row of columns
)
def test_write_shell_splits_above_255_types(
    conn: sqlite3.Connection,
    world: worldreg.WorldRegistry,
    k: int,
    width: int,
    height: int,
) -> None:
    base = many_types(k, width)
    s = schematic.Schematic.from_cells(
        base.width,
        height,
        base.length,
        [e for _, e in cells_of(base)] * height,
        (-4, 0, 1),
    )
    out = schematic.write_shell(conn, world, s)
    assert out.how == "split" and len(out.parts) > 1
    assert f"{k} block types > 255: split into {len(out.parts)} parts" in out.notes[0]
    assert _reassemble(s, out.parts) == [e for _, e in cells_of(s)]
    assert [p.name for p in out.parts] == [
        f"part-{i}" for i in range(1, len(out.parts) + 1)
    ]


def test_write_shell_add_blocks_on_request(
    conn: sqlite3.Connection, world: worldreg.WorldRegistry
) -> None:
    s = many_types(300)
    out = schematic.write_shell(conn, world, s, over="addblocks")
    assert out.how == "addblocks" and len(out.parts) == 1
    assert "Schematica-GTNH reads AddBlocks the other way round" in out.notes[0]
    c = schematic.read(out.parts[0].data)
    assert schematic.diff(s, c.schematic) == [] and c.order == "low-first"


def test_palette_check_lifts_the_255_cap_only_when_asked(
    conn: sqlite3.Connection, world: worldreg.WorldRegistry
) -> None:
    entries = sorted(many_types(300).used(), key=lambda e: e.key)
    assert not palette.check(conn, entries, world).ok
    assert palette.check(conn, entries, world, max_types=None).ok


def test_world_block_ids_name_a_capture_without_mappings(tmp_path: Path) -> None:
    w = tmp_path / "lab"
    w.mkdir()
    (w / "level.dat").write_bytes(level_dat(["minecraft:air", "minecraft:stone"]))
    ids = worldreg.read_block_ids(w)
    assert ids == {4096: "minecraft:air", 4097: "minecraft:stone"}  # items left out
    data = worldedit_copy((1, 1, 2), [(4097, 0), (4096, 0)], None)
    c = schematic.read(data, source_ids=ids)
    assert c.schematic.at(0, 0, 0) == E("minecraft:stone", 0)
    assert c.schematic.at(0, 0, 1) == schematic.AIR_STATE


def test_the_schematic_harness_inputs_are_current() -> None:
    """mod/harness/schematic/in/ is what tests/harness/schematic_inputs.py writes (compared as
    NBT: gzip's bytes may differ between zlib builds)."""
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "harness" / "schematic_inputs.py"
    spec = importlib.util.spec_from_file_location("schematic_inputs", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    committed = sorted(p.name for p in mod.OUT.glob("*.schematic"))
    assert committed == sorted(mod.files())
    for name, data in mod.files().items():
        assert gzip.decompress((mod.OUT / name).read_bytes()) == gzip.decompress(
            data
        ), name
    for s in mod.inputs().values():
        assert all(e.meta <= 15 for e in s.used())  # no EndlessIDs in the dev runtime


def test_sample_places_every_state_once() -> None:
    states = [E(f"t:b{i:03d}", 0) for i in range(10)]
    s = schematic.sample(states + states[:2])
    assert (s.width, s.height, s.length) == (4, 1, 3)
    assert set(s.used()) == set(states) and set(s.used().values()) == {1}
    with pytest.raises(schematic.SchematicError):
        schematic.sample([])


# --- gos schematic --------------------------------------------------------------------------


def _home(tmp_path: Path) -> tuple[Path, Path, Path]:
    """GOS_HOME with the fixture catalog, a Lab instance with world lab-shells, gos.toml."""
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    run = tmp_path / "run"
    run.mkdir()
    (run / ingest.FILE).write_text(
        "".join(json.dumps(s) + "\n" for s in CATALOG), encoding="utf-8"
    )
    (run / ingest.TEXTURES).write_text("", encoding="utf-8")
    c = access.connect(home / "data" / "gos.sqlite")
    try:
        c.execute(
            "INSERT INTO export_run(id, fingerprint, run_key) VALUES (1, '', 'run-a')"
        )
        block_db.write(c, 1, ingest.read(run))
    finally:
        c.close()
    instances = tmp_path / "instances"
    world = instances / "GTNH-Lab" / ".minecraft" / "saves" / "lab-shells"
    world.mkdir(parents=True)
    (instances / "GTNH-Lab" / "instance.cfg").write_text(
        "name=GTNH-Lab\n", encoding="utf-8"
    )
    (world / "level.dat").write_bytes(level_dat(WORLD))
    toml = tmp_path / "gos.toml"
    toml.write_text(
        "[paths]\n"
        f"gos_home = {json.dumps(home.as_posix())}\n"
        f"prism_instances = {json.dumps(instances.as_posix())}\n"
        'lab_world = "lab-shells"\n',
        encoding="utf-8",
    )
    return toml, home, world


def run_cli(argv: list[str]) -> tuple[int, str]:
    import argparse
    import io

    from gregtec.build import schematic_cli

    parser = argparse.ArgumentParser()
    schematic_cli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    code = schematic_cli.run(parser.parse_args(argv), out)
    return code, out.getvalue()


def test_cli_sample_read_and_compare(tmp_path: Path) -> None:
    toml, home, world = _home(tmp_path)
    pal = tmp_path / "shell.txt"
    pal.write_text(
        "minecraft:stone@0\ngregtech:gt.blockcasings@2\ngregtech:gt.blockcasings5@0\n",
        encoding="utf-8",
    )
    code, text = run_cli(["schematic", "sample", str(pal), "--config", str(toml)])
    assert code == 0, text
    made = home / "data" / "schematics" / "shells" / "gos_roundtrip.schematic"
    assert made.is_file() and f"wrote {made}" in text and "without //rotate" in text
    code, text = run_cli(["schematic", "read", str(made)])
    assert code == 0 and "2x1x2 (W x H x L), written by worldedit" in text
    assert "WEOffset (-1, 0, 1)" in text and "gregtech:gt.blockcasings@2" in text
    # a //copy of the paste, standing on the same block: the world's ids, above 4095
    s = schematic.read(made.read_bytes()).schematic
    ids = {"minecraft:air": 0, **{n: 4096 + i for i, n in enumerate(s.names())}}
    cells = [(ids[e.block], e.meta) for _, e in cells_of(s)]
    copy = tmp_path / "copy.schematic"
    copy.write_bytes(worldedit_copy((2, 1, 2), cells, ids, s.offset))
    code, text = run_cli(["schematic", "compare", str(made), str(copy)])
    assert code == 0 and "identical: 2x1x2, 3 block states" in text, text
    cells[0] = (0, 0)
    copy.write_bytes(worldedit_copy((2, 1, 2), cells, ids, (0, 0, 0)))
    code, text = run_cli(["schematic", "compare", str(made), str(copy)])
    assert code == 1 and "(0, 0, 0):" in text and "DIFFERENT" in text
    assert "(differ: did you //copy from the spot you pasted from?)" in text
    # without a mapping: the source world names the ids
    copy.write_bytes(worldedit_copy((1, 1, 1), [(4097, 0)], None))
    code, text = run_cli(["schematic", "read", str(copy)])
    assert code == 2 and "give the world it was copied in" in text
    code, text = run_cli(["schematic", "read", str(copy), "--source-world", str(world)])
    assert code == 0 and f"{WORLD[1]}@0" in text


def test_cli_sample_refuses_and_splits(tmp_path: Path) -> None:
    toml, _, world = _home(tmp_path)
    pal = tmp_path / "shell.txt"
    pal.write_text("minecraft:stone@0\nminecraft:chest@0\n", encoding="utf-8")
    out = tmp_path / "out" / "s.schematic"
    base = ["schematic", "sample", str(pal), "--config", str(toml), "--out", str(out)]
    code, text = run_cli(base)
    assert code == 1 and "REFUSED minecraft:chest@0" in text and not out.exists()
    pal.write_text(
        "".join(f"t:b{i:03d}@{i % 16}\n" for i in range(300)), encoding="utf-8"
    )
    code, text = run_cli(base)
    assert code == 0 and "split into 2 parts" in text, text
    assert sorted(p.name for p in out.parent.iterdir()) == [
        "s-part-1.schematic",
        "s-part-2.schematic",
    ]
    code, text = run_cli([*base, "--over", "addblocks", "--world-dir", str(world)])
    assert (
        code == 0 and "AddBlocks in WorldEdit's nibble order" in text and out.is_file()
    )
    code, text = run_cli(["schematic", "read", str(tmp_path / "nothing.schematic")])
    assert code == 2 and "Can't read nothing.schematic" in text
