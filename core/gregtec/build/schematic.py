"""Schematic I/O (WO-10.4, plan 7.10 "Schematic writer", D309-D311).

**The writer** makes the MCEdit ``.schematic`` that worldedit-gtnh (>= v0.0.9) pastes and
Schematica-GTNH can ghost, exactly per plan 7.10 [G§4.3]: a gzipped NBT root ``Schematic`` with
``Width/Height/Length`` (shorts), ``Materials`` ``"Alpha"``, ``Blocks`` and ``Data``, empty
``TileEntities`` and ``Entities`` lists, ``WEOriginX/Y/Z`` = 0 and ``WEOffsetX/Y/Z`` = the paste
offset (the shell's min corner relative to the player, :func:`default_offset`), and the block
types numbered 1..N (0 = air) with the same ids in ``BlockMapping`` and ``SchematicaMapping``
(``minecraft:air``: 0 in both) and an empty ``ItemMapping``. The mapping is what pastes the right
block: worldedit-gtnh looks every name up in the world it pastes in, so EndlessIDs' ids above
4095 need nothing in the file. No ``AddBlocks``; ``AddData`` only when a meta is above 255.

Above 255 block types (registry names; metas share one id) :func:`write_shell` either **splits**
the shell into parts of at most 255 types each (the default; every part keeps both readers
right) or writes ``AddBlocks`` in **WorldEdit's nibble order** (even cell in the low nibble), which
Schematica-GTNH reads the other way round, and says which in its notes. Output is pre-rotated: the
cells are written in world axes as they will stand (x east, z south) and are pasted without
``//rotate`` (GT tiles don't rotate, [E§5.3]).

:func:`write_shell` is the only way GOS writes a shell: it applies the palette rule
(:func:`gregtec.build.palette.check`, WO-10.3) to every block state the shell uses and refuses the
whole shell when any is refused: air or ``gregtech:gt.blockmachines`` named, not in the target
world's registry, not a placeable state in the block catalog, ``hasTileEntity`` set, or a meta
above 15 (worldedit-gtnh #30, V16; kept out of shells until it's resolved). :func:`encode` is the
format alone and carries 16-bit metas (``AddData``) for files that aren't shells.

**The reader** (:func:`read`) takes captures: worldedit-gtnh ``//copy`` + ``//schem save`` files
(the world's numeric ids, ``AddBlocks`` low nibble first, ``AddBlocks2`` for ids above 4095,
``AddData`` above meta 15, ``BlockMapping``) and Schematica-GTNH saves (``SchematicaMapping``;
``AddBlocks`` high nibble first, or one byte per cell in its extended-id format, or ``Add``), with
or without a mapping. Every numeric id becomes a registry name as the file is read, from the
file's mapping or, for a file without one, the ids of the world it was copied in
(:func:`gregtec.build.worldreg.read_block_ids`); a capture's blocks are keyed by name + meta, never
by id. An ``AddBlocks`` nibble order is taken from the writer's tags when the mapping fits it,
detected when only the other order fits, and otherwise refused with the reason.

Re-implemented from the format as worldedit-gtnh v0.0.10 (``SchematicReader``/``SchematicWriter``,
LGPL-3.0) and Schematica 1.12.6-GTNH (``SchematicAlpha``, MIT) read and write it; their source was
read to learn the behaviour, nothing is copied (D309).
"""

from __future__ import annotations

import gzip
import sqlite3
import struct
from array import array
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from gregtec.build import palette
from gregtec.build.palette import AIR, Entry
from gregtec.build.worldreg import WorldRegistry
from gregtec.ingest import nbt

ROOT = "Schematic"
MATERIALS = "Alpha"
MAX_SIDE = (
    32767  # Width/Height/Length are signed shorts (worldedit-gtnh loops `x < width`)
)
MAX_CELLS = 32 << 20  # Blocks + Data + AddData stay well under nbt.MAX_BYTES
MAX_COMPACT = 255  # block types numbered 1..255 in Blocks alone (plan 7.10)
MAX_ADDBLOCKS = 4095  # 12 bits with AddBlocks
MAX_META = 0xFFFF  # Data + AddData; EndlessIDs' 16-bit metadata
AIR_STATE = Entry(AIR, 0)

type Pos = tuple[int, int, int]
type Over = Literal["split", "addblocks"]
type Order = Literal["low-first", "high-first", "byte"]


class SchematicError(ValueError):
    """A schematic that can't be made or written as asked (shown as is)."""


# --- the model -------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Schematic:
    """A box of block states. ``cells[(y * length + z) * width + x]`` indexes ``states``, whose
    first entry is air; ``offset`` is WEOffset, the min corner relative to the player at paste."""

    width: int  # x
    height: int  # y
    length: int  # z
    states: tuple[Entry, ...]
    cells: array[int]
    offset: Pos = (0, 0, 0)

    def __post_init__(self) -> None:
        for side, n in (
            ("width", self.width),
            ("height", self.height),
            ("length", self.length),
        ):
            if not 1 <= n <= MAX_SIDE:
                raise SchematicError(f"{side} {n} is outside 1..{MAX_SIDE}")
        n = self.width * self.height * self.length
        if n > MAX_CELLS:
            raise SchematicError(f"{n:,} cells: at most {MAX_CELLS:,}")
        if len(self.cells) != n:
            raise SchematicError(f"{len(self.cells):,} cells for a {self.size} box")
        if not self.states or self.states[0] != AIR_STATE:
            raise SchematicError("the first state must be minecraft:air@0")
        if len(set(self.states)) != len(self.states):
            raise SchematicError("a block state is listed twice")
        if any(s.block == AIR for s in self.states[1:]):
            raise SchematicError("air is only the first state")
        if any(not 0 <= s.meta <= MAX_META for s in self.states):
            raise SchematicError(f"a meta is outside 0..{MAX_META}")
        if n and max(self.cells) >= len(self.states):
            raise SchematicError("a cell names a state that isn't listed")

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}x{self.length}"

    def index(self, x: int, y: int, z: int) -> int:
        return (y * self.length + z) * self.width + x

    def at(self, x: int, y: int, z: int) -> Entry:
        return self.states[self.cells[self.index(x, y, z)]]

    def used(self) -> Counter[Entry]:
        """How many cells hold each block state (air left out)."""
        counts = Counter(self.cells)
        return Counter({self.states[i]: c for i, c in counts.items() if i and c})

    def names(self) -> list[str]:
        """The registry names the cells use (air left out), sorted: ids 1..N in the file."""
        return sorted({s.block for s in self.used()})

    @classmethod
    def from_cells(
        cls,
        width: int,
        height: int,
        length: int,
        cells: Sequence[Entry],
        offset: Pos | None = None,
    ) -> Schematic:
        """A schematic from one state per cell (in ``index`` order); any air state is air.
        ``offset`` defaults to :func:`default_offset`."""
        used = sorted({c for c in cells if c.block != AIR}, key=_sort_key)
        states = (AIR_STATE, *used)
        number = {s: i for i, s in enumerate(states)}
        packed = array("H", (0 if c.block == AIR else number[c] for c in cells))
        return cls(
            width,
            height,
            length,
            states,
            packed,
            default_offset(width) if offset is None else offset,
        )

    @classmethod
    def from_blocks(
        cls,
        width: int,
        height: int,
        length: int,
        blocks: Mapping[Pos, Entry],
        offset: Pos | None = None,
    ) -> Schematic:
        """A schematic of air with ``blocks`` placed at their ``(x, y, z)``."""
        cells = [AIR_STATE] * (width * height * length)
        for (x, y, z), state in blocks.items():
            if not (0 <= x < width and 0 <= y < height and 0 <= z < length):
                raise SchematicError(
                    f"({x}, {y}, {z}) is outside the {width}x{height}x{length} box"
                )
            cells[(y * length + z) * width + x] = state
        return cls.from_cells(width, height, length, cells, offset)


def _sort_key(s: Entry) -> tuple[str, int]:
    return (s.block, s.meta)


def default_offset(width: int) -> Pos:
    """Plan 7.10's paste offset: centred in x, from the player's feet, starting one block south
    of the player: ``(-floor(W/2), 0, 1)`` (**VERIFY** in game, runbook schematic_roundtrip.md)."""
    return (-(width // 2), 0, 1)


def diff(a: Schematic, b: Schematic, limit: int = 20) -> list[str]:
    """Where two schematics' blocks differ (size, then each cell as registry@meta), at most
    ``limit`` lines; empty when every cell holds the same block state. Offsets aren't compared."""
    if (a.width, a.height, a.length) != (b.width, b.height, b.length):
        return [f"size {a.size} vs {b.size}"]
    out: list[str] = []
    differing = 0
    for y in range(a.height):
        for z in range(a.length):
            for x in range(a.width):
                i = a.index(x, y, z)
                sa, sb = a.states[a.cells[i]], b.states[b.cells[i]]
                if sa != sb:
                    differing += 1
                    if len(out) < limit:
                        out.append(f"({x}, {y}, {z}): {sa.key} vs {sb.key}")
    if differing > len(out):
        out.append(f"... {differing - len(out):,} more cells differ")
    return out


# --- NBT out (the few tags a schematic needs) --------------------------------------------------


def _mutf8(text: str) -> bytes:
    """Java's modified UTF-8 (``DataOutput.writeUTF``): NUL as C0 80, astral characters as two
    3-byte surrogates."""
    raw = text.encode("utf-16-be", "surrogatepass")
    units = struct.unpack(f">{len(raw) // 2}H", raw)
    out = bytearray()
    for u in units:
        if 0 < u < 0x80:
            out.append(u)
        elif u < 0x800:
            out += bytes((0xC0 | u >> 6, 0x80 | u & 0x3F))
        else:
            out += bytes((0xE0 | u >> 12, 0x80 | u >> 6 & 0x3F, 0x80 | u & 0x3F))
    if len(out) > 0xFFFF:
        raise SchematicError("a string longer than NBT allows")
    return struct.pack(">H", len(out)) + bytes(out)


def _tag(kind: int, name: str, payload: bytes) -> bytes:
    return bytes((kind,)) + _mutf8(name) + payload


def _short(name: str, v: int) -> bytes:
    return _tag(nbt.SHORT, name, struct.pack(">h", v))


def _int(name: str, v: int) -> bytes:
    return _tag(nbt.INT, name, struct.pack(">i", v))


def _bytes(name: str, v: bytes) -> bytes:
    return _tag(nbt.BYTE_ARRAY, name, struct.pack(">i", len(v)) + v)


def _empty_list(name: str) -> bytes:
    # worldedit-gtnh writes ListTag(CompoundTag.class, []): element type compound, length 0
    return _tag(nbt.LIST, name, struct.pack(">bi", nbt.COMPOUND, 0))


def _mapping(name: str, ids: Mapping[str, int]) -> bytes:
    body = b"".join(_short(k, v) for k, v in sorted(ids.items(), key=lambda kv: kv[1]))
    return _tag(nbt.COMPOUND, name, body + b"\x00")


# --- the writer ------------------------------------------------------------------------------


def to_nbt(s: Schematic, add_blocks: bool = False) -> bytes:
    """The schematic as uncompressed NBT (plan 7.10's layout, tags in a fixed order so the bytes
    are stable). Block type ``i`` of :meth:`Schematic.names` is id ``i + 1``. ``add_blocks``
    allows up to 4,095 types, the bits above 8 in ``AddBlocks`` in WorldEdit's nibble order."""
    names = s.names()
    limit = MAX_ADDBLOCKS if add_blocks else MAX_COMPACT
    if len(names) > limit:
        raise SchematicError(
            f"{len(names)} block types: at most {limit} "
            + ("with AddBlocks" if add_blocks else "without AddBlocks: split the shell")
        )
    ids = {name: i for i, name in enumerate(names, 1)}
    state_id = [0] + [ids.get(st.block, 0) for st in s.states[1:]]
    metas = [st.meta for st in s.states]
    n = len(s.cells)
    blocks = bytearray(n)
    data = bytearray(n)
    high_meta = any(metas[i] > 255 for i in set(s.cells))
    add_data = bytearray(n) if high_meta else None
    need_add = len(names) > MAX_COMPACT
    add = bytearray((n + 1) // 2) if need_add else None
    for i, c in enumerate(s.cells):
        bid = state_id[c]
        blocks[i] = bid & 0xFF
        data[i] = metas[c] & 0xFF
        if add_data is not None:
            add_data[i] = metas[c] >> 8
        if add is not None and bid > 0xFF:
            nib = (bid >> 8) & 0xF
            add[i >> 1] |= (
                nib if i & 1 == 0 else nib << 4
            )  # even cell: low nibble (WorldEdit)
    mapping = {AIR: 0, **ids}
    body = (
        _short("Width", s.width)
        + _short("Height", s.height)
        + _short("Length", s.length)
        + _tag(nbt.STRING, "Materials", _mutf8(MATERIALS))
        + _bytes("Blocks", bytes(blocks))
        + _bytes("Data", bytes(data))
        + (_bytes("AddBlocks", bytes(add)) if add is not None else b"")
        + (_bytes("AddData", bytes(add_data)) if add_data is not None else b"")
        + _int("WEOriginX", 0)
        + _int("WEOriginY", 0)
        + _int("WEOriginZ", 0)
        + _int("WEOffsetX", s.offset[0])
        + _int("WEOffsetY", s.offset[1])
        + _int("WEOffsetZ", s.offset[2])
        + _empty_list("TileEntities")
        + _empty_list("Entities")
        + _mapping("BlockMapping", mapping)
        + _mapping("SchematicaMapping", mapping)
        + _mapping("ItemMapping", {})
    )
    return _tag(nbt.COMPOUND, ROOT, body + b"\x00")


def encode(s: Schematic, add_blocks: bool = False) -> bytes:
    """:func:`to_nbt` gzipped, as worldedit-gtnh and Schematica read it (no timestamp, so the same
    schematic gives the same file)."""
    return gzip.compress(to_nbt(s, add_blocks), mtime=0)


@dataclass(frozen=True)
class Part:
    name: str  # "" for a whole shell, else part-1, part-2, ...
    schematic: Schematic
    data: bytes  # the .schematic file
    corner: Pos  # where the part's min corner sits in the shell


@dataclass(frozen=True)
class Written:
    parts: tuple[Part, ...]
    how: Literal["single", "split", "addblocks"]
    notes: tuple[str, ...]
    report: palette.Report


class ShellRefused(SchematicError):
    """The palette rule refused the shell; ``lines`` is the palette report."""

    def __init__(self, lines: Sequence[str]) -> None:
        super().__init__(
            "the shell is refused by the palette rule:\n" + "\n".join(lines)
        )
        self.lines = tuple(lines)


def write_shell(
    conn: sqlite3.Connection,
    world: WorldRegistry,
    s: Schematic,
    over: Over = "split",
) -> Written:
    """A shell's ``.schematic`` file(s) after the palette rule (plan 7.10): every block state the
    shell uses is checked against the newest block catalog in ``conn`` and ``world``'s registry,
    and any refusal refuses the shell (:class:`ShellRefused`). Above 255 block types ``over``
    says how: ``split`` into parts of at most 255 types (each pasted from the same spot) or
    ``addblocks`` (WorldEdit's nibble order; Schematica misreads it)."""
    used = sorted(s.used(), key=_sort_key)
    report = palette.check(conn, used, world, max_types=None)
    if not report.ok:
        raise ShellRefused(palette.render(report))
    names = s.names()
    notes = [
        "pre-rotated: paste as is, without //rotate (plan 7.10)",
        f"WEOffset {s.offset}: the shell's min corner relative to you at //paste",
    ]
    if len(names) <= MAX_COMPACT:
        notes.insert(
            0,
            f"{len(names)} block types: ids 1..{len(names)} in BlockMapping and "
            "SchematicaMapping, no AddBlocks",
        )
        return Written(
            (Part("", s, encode(s), (0, 0, 0)),), "single", tuple(notes), report
        )
    if over == "addblocks":
        if len(names) > MAX_ADDBLOCKS:
            raise SchematicError(
                f"{len(names)} block types: AddBlocks holds at most {MAX_ADDBLOCKS}; split the shell"
            )
        notes.insert(
            0,
            f"{len(names)} block types > {MAX_COMPACT}: AddBlocks in WorldEdit's nibble order "
            "(even cell in the low nibble). Schematica-GTNH reads AddBlocks the other way round, "
            "so don't ghost this file with Schematica",
        )
        return Written(
            (Part("", s, encode(s, add_blocks=True), (0, 0, 0)),),
            "addblocks",
            tuple(notes),
            report,
        )
    boxes = _split(s, (0, 0, 0, s.width, s.height, s.length), 0)
    parts = []
    for k, (x0, y0, z0, x1, y1, z1) in enumerate(boxes, 1):
        sub = _sub(s, x0, y0, z0, x1, y1, z1)
        parts.append(Part(f"part-{k}", sub, encode(sub), (x0, y0, z0)))
    notes.insert(
        0,
        f"{len(names)} block types > {MAX_COMPACT}: split into {len(parts)} parts of at most "
        f"{MAX_COMPACT} types (by layer, then row, then column); paste every part standing on "
        "the same block, since each part's WEOffset includes its corner",
    )
    return Written(tuple(parts), "split", tuple(notes), report)


type Box = tuple[
    int, int, int, int, int, int
]  # x0, y0, z0, x1, y1, z1 (ends exclusive)


def _box_names(s: Schematic, box: Box) -> set[str]:
    x0, y0, z0, x1, y1, z1 = box
    out: set[str] = set()
    states, cells = s.states, s.cells
    for y in range(y0, y1):
        for z in range(z0, z1):
            base = (y * s.length + z) * s.width
            out.update(states[c].block for c in cells[base + x0 : base + x1])
    out.discard(AIR)
    return out


def _slab(box: Box, axis: int, lo: int, hi: int) -> Box:
    b = list(box)
    b[axis], b[axis + 3] = lo, hi
    return (b[0], b[1], b[2], b[3], b[4], b[5])


_AXES = (1, 2, 0)  # y (layers), then z (rows), then x (columns)


def _split(s: Schematic, box: Box, depth: int) -> list[Box]:
    """``box`` cut into boxes of at most 255 block types: greedy runs of whole layers; a layer
    with more is cut into rows, a row into columns (a single cell is one type)."""
    if len(_box_names(s, box)) <= MAX_COMPACT:
        return [box]
    axis = _AXES[depth]
    lo, hi = box[axis], box[axis + 3]
    out: list[Box] = []
    start, run = lo, set[str]()
    for v in range(lo, hi):
        names = _box_names(s, _slab(box, axis, v, v + 1))
        if len(names) > MAX_COMPACT:
            if start < v:
                out.append(_slab(box, axis, start, v))
            out.extend(_split(s, _slab(box, axis, v, v + 1), depth + 1))
            start, run = v + 1, set()
            continue
        if len(run | names) > MAX_COMPACT:
            out.append(_slab(box, axis, start, v))
            start, run = v, set()
        run |= names
    if start < hi:
        out.append(_slab(box, axis, start, hi))
    return out


def _sub(
    s: Schematic, x0: int, y0: int, z0: int, x1: int, y1: int, z1: int
) -> Schematic:
    cells = [
        s.at(x, y, z)
        for y in range(y0, y1)
        for z in range(z0, z1)
        for x in range(x0, x1)
    ]
    ox, oy, oz = s.offset
    return Schematic.from_cells(
        x1 - x0, y1 - y0, z1 - z0, cells, (ox + x0, oy + y0, oz + z0)
    )


# --- the reader ------------------------------------------------------------------------------


class CaptureError(ValueError):
    """Why a schematic file can't be read (shown as is)."""


@dataclass(frozen=True)
class Capture:
    schematic: Schematic  # its offset is the file's WEOffset (0, 0, 0 when it has none)
    writer: Literal["worldedit", "schematica", "unknown"]  # from the file's tags
    order: Order | None  # how the ids above 255 were stored; None: no AddBlocks
    origin: Pos  # WEOrigin
    tile_entities: tuple[
        Pos, ...
    ]  # positions of the file's tile entities (not read further)
    entities: int
    notes: tuple[str, ...]


def read(data: bytes, source_ids: Mapping[int, str] | None = None) -> Capture:
    """A ``.schematic`` file (gzip or plain), every cell as registry name + meta. ``source_ids``
    (the block ids of the world it was copied in, :func:`worldreg.read_block_ids`) names the ids
    the file's own mapping doesn't."""
    try:
        name, root = nbt.read(data)
    except ValueError as exc:
        raise CaptureError(f"not a schematic (NBT): {exc}") from None
    if name != ROOT:
        raise CaptureError(f"the root tag is {name[:40]!r}, not {ROOT!r}")
    if root.get("Materials") != MATERIALS:
        raise CaptureError(
            f"Materials is {str(root.get('Materials'))[:40]!r}, not {MATERIALS!r}"
        )
    w, h, ln = (_side(root, k) for k in ("Width", "Height", "Length"))
    n = w * h * ln
    if n > MAX_CELLS:
        raise CaptureError(f"{n:,} cells: at most {MAX_CELLS:,}")
    blocks = _array(root, "Blocks", n)
    meta_lo = _array(root, "Data", n)
    add_data = _array(root, "AddData", n) if "AddData" in root else None
    notes: list[str] = []
    writer: Literal["worldedit", "schematica", "unknown"]
    if any(k in root for k in ("WEOffsetX", "WEOriginX", "BlockMapping", "AddBlocks2")):
        writer = "worldedit"
    elif "SchematicaMapping" in root or "Icon" in root or "Add" in root:
        writer = "schematica"
    else:
        writer = "unknown"
    mapping = _mappings(root)
    known = set(mapping) | set(source_ids or ())
    ids, order, more = _ids(root, blocks, n, writer, known)
    notes.extend(more)
    names: dict[int, str] = {}
    unnamed: list[int] = []
    for bid in sorted(set(ids)):
        nm = mapping.get(bid)
        if nm is None and source_ids is not None:
            nm = source_ids.get(bid)
        if nm is None and bid == 0:
            nm = AIR
        if nm is None:
            unnamed.append(bid)
        else:
            names[bid] = nm
    if unnamed:
        shown = ", ".join(str(i) for i in unnamed[:10]) + (
            ", ..." if len(unnamed) > 10 else ""
        )
        raise CaptureError(
            f"ids {shown} have no name: "
            + (
                "the file's mapping doesn't list them"
                if mapping
                else "the file has no BlockMapping or SchematicaMapping"
            )
            + (
                " and the source world doesn't know them"
                if source_ids is not None
                else "; give the world it was copied in (its level.dat names the ids)"
            )
        )
    if not mapping:
        notes.append(
            "no mapping in the file: ids named from the source world's level.dat"
        )
    cells: list[Entry] = []
    entry_of: dict[tuple[int, int], Entry] = {}
    for i in range(n):
        meta = meta_lo[i] | ((add_data[i] << 8) if add_data is not None else 0)
        key = (ids[i], meta)
        e = entry_of.get(key)
        if e is None:
            e = AIR_STATE if names[ids[i]] == AIR else Entry(names[ids[i]], meta)
            entry_of[key] = e
        cells.append(e)
    origin = _triple(root, "WEOrigin")
    offset = _triple(root, "WEOffset")
    if offset is None:
        notes.append("no WEOffset: offset (0, 0, 0)")
    tes = root.get("TileEntities")
    if tes is None:
        notes.append(
            "no TileEntities list (worldedit-gtnh refuses to load such a file)"
        )
    tile_entities = tuple(_te_pos(t) for t in tes or [] if isinstance(t, dict))
    if tile_entities:
        notes.append(
            f"{len(tile_entities)} tile entities in the file (a capture, never a shell)"
        )
    entities = root.get("Entities")
    s = Schematic.from_cells(w, h, ln, cells, offset or (0, 0, 0))
    return Capture(
        s,
        writer,
        order,
        origin or (0, 0, 0),
        tile_entities,
        len(entities) if isinstance(entities, list) else 0,
        tuple(notes),
    )


def _side(root: Mapping[str, object], key: str) -> int:
    v = root.get(key)
    if not isinstance(v, int) or not 1 <= v <= MAX_SIDE:
        raise CaptureError(f"{key} is {v!r}, not 1..{MAX_SIDE}")
    return v


def _array(root: Mapping[str, object], key: str, n: int) -> bytes:
    v = root.get(key)
    if not isinstance(v, bytes) or len(v) != n:
        size = len(v) if isinstance(v, bytes) else "missing"
        raise CaptureError(f"{key} has {size} bytes, not one per cell ({n:,})")
    return v


def _triple(root: Mapping[str, object], prefix: str) -> Pos | None:
    vals = [root.get(prefix + a) for a in "XYZ"]
    if all(isinstance(v, int) for v in vals):
        return (vals[0], vals[1], vals[2])  # type: ignore[return-value]
    return None


def _te_pos(t: Mapping[str, object]) -> Pos:
    vals = [t.get(a) for a in "xyz"]
    return tuple(v if isinstance(v, int) else 0 for v in vals)  # type: ignore[return-value]


def _mappings(root: Mapping[str, object]) -> dict[int, str]:
    """``BlockMapping`` and ``SchematicaMapping`` as id -> name (ids as unsigned shorts)."""
    out: dict[int, str] = {}
    for key in ("BlockMapping", "SchematicaMapping"):
        m = root.get(key)
        if m is None:
            continue
        if not isinstance(m, dict):
            raise CaptureError(f"{key} is not a compound")
        for name, sid in m.items():
            if not isinstance(sid, int):
                raise CaptureError(f"{key} gives {name[:80]!r} no id")
            try:
                palette.parse_entry(f"{name}@0")
            except palette.PaletteError:
                raise CaptureError(
                    f"{key} has {name[:80]!r}, which is not a registry name"
                ) from None
            bid = sid & 0xFFFF
            if out.get(bid, name) != name:
                raise CaptureError(
                    f"id {bid} is both {out[bid]!r} and {name!r} in the file's mappings"
                )
            out[bid] = name
    return out


def _nibbles(add: bytes, n: int, low_first: bool, shift: int) -> list[int]:
    out = [0] * n
    for i in range(n):
        b = add[i >> 1]
        low = i & 1 == 0 if low_first else i & 1 == 1
        out[i] = ((b & 0xF) if low else (b >> 4)) << shift
    return out


def _ids(
    root: Mapping[str, object],
    blocks: bytes,
    n: int,
    writer: str,
    known: set[int],
) -> tuple[list[int], Order | None, list[str]]:
    """Each cell's numeric id from ``Blocks`` and the add arrays, and how they were stored."""
    base = list(blocks)
    notes: list[str] = []
    if "Add" in root:  # Schematica's own name: one byte per cell
        add = _array(root, "Add", n)
        return [b | add[i] << 8 for i, b in enumerate(base)], "byte", notes
    if "AddBlocks" not in root:
        if "AddBlocks2" in root:
            raise CaptureError("AddBlocks2 without AddBlocks")
        return base, None, notes
    add = root["AddBlocks"]
    if not isinstance(add, bytes):
        raise CaptureError("AddBlocks is not a byte array")
    if len(add) == n and (n > 2 or writer == "schematica") and "AddBlocks2" not in root:
        # Schematica-GTNH's extended-id format writes a whole byte per cell
        notes.append(
            "AddBlocks with one byte per cell (Schematica's extended-id format)"
        )
        return [b | add[i] << 8 for i, b in enumerate(base)], "byte", notes
    if len(add) < (n + 1) // 2:
        raise CaptureError(
            f"AddBlocks has {len(add)} bytes: a nibble per cell needs {(n + 1) // 2}"
        )
    low = [b | a for b, a in zip(base, _nibbles(add, n, True, 8), strict=True)]
    if "AddBlocks2" in root:
        # only worldedit-gtnh writes it: bits 12-15, even cell in the low nibble like AddBlocks
        add2 = root["AddBlocks2"]
        if not isinstance(add2, bytes) or len(add2) < (n + 1) // 2:
            raise CaptureError("AddBlocks2 is shorter than a nibble per cell")
        low = [b | a for b, a in zip(low, _nibbles(add2, n, True, 12), strict=True)]
        return low, "low-first", notes
    high = [b | a for b, a in zip(base, _nibbles(add, n, False, 8), strict=True)]
    if low == high:
        return low, "low-first", notes
    fits = {"low-first": set(low) <= known, "high-first": set(high) <= known}
    preferred: Order | None = (
        "low-first"
        if writer == "worldedit"
        else "high-first"
        if writer == "schematica"
        else None
    )
    who = {"low-first": "WorldEdit's", "high-first": "Schematica's"}
    order: Order
    if preferred is not None and fits[preferred]:
        order = preferred
        if all(fits.values()):
            notes.append(
                f"both AddBlocks nibble orders fit the mapping; took the writer's ({who[order]})"
            )
    elif fits["low-first"] != fits["high-first"]:
        order = "low-first" if fits["low-first"] else "high-first"
        notes.append(
            f"AddBlocks detected in {who[order]} nibble order"
            + (f" although the file looks written by {writer}" if preferred else "")
        )
    elif all(fits.values()):
        raise CaptureError(
            "AddBlocks fits the ids in both nibble orders and the file doesn't say who "
            "wrote it: refused rather than guess"
        )
    elif preferred is not None:
        order = (
            preferred  # neither fits: the ids left unnamed are refused by name below
        )
    else:
        raise CaptureError(
            "AddBlocks gives ids that "
            + (
                "nothing names"
                if not known
                else "the mapping or source world doesn't name"
            )
            + " in either nibble order (WorldEdit's low nibble first or Schematica's high "
            "nibble first) and the file doesn't say who wrote it: refused rather than guess"
        )
    return (low if order == "low-first" else high), order, notes


def summary(c: Capture, top: int = 40) -> list[str]:
    """A capture as lines: size, writer, offset, notes, then the block states by count."""
    s = c.schematic
    used = s.used()
    out = [
        f"{s.size} (W x H x L), written by {c.writer}"
        + (f", AddBlocks {c.order}" if c.order else ""),
        f"WEOffset {s.offset}, WEOrigin {c.origin}",
        (
            f"{len(s.names())} block types, {len(used)} block states, "
            f"{sum(used.values()):,} non-air cells"
        ),
    ]
    out.extend(f"note: {note}" for note in c.notes)
    for state, count in sorted(used.items(), key=lambda kv: (-kv[1], _sort_key(kv[0])))[
        :top
    ]:
        out.append(f"{count:>8,}  {state.key}")
    if len(used) > top:
        out.append(f"... {len(used) - top} more states")
    return out


def sample(states: Iterable[Entry], offset: Pos | None = None) -> Schematic:
    """A test shell for the Lab round trip: every state once, in the order given, on a single
    layer (a near-square grid; the cells left over are air)."""
    listed = list(dict.fromkeys(states))
    if not listed:
        raise SchematicError("no block states to place")
    side = 1
    while side * side < len(listed):
        side += 1
    rows = -(-len(listed) // side)
    blocks = {(i % side, 0, i // side): st for i, st in enumerate(listed)}
    return Schematic.from_blocks(side, 1, rows, blocks, offset)
