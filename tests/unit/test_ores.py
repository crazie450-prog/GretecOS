"""Ore mixes, VisualProspecting's caches and ``gos ores`` (WO-1.6) on self-authored fixtures.

The VP caches are written here in VisualProspecting 1.5.42's v3 layout, as its
``DimensionCache.saveToNbt`` and Minecraft's ``CompressedStreamTools.writeCompressed`` write
them (gzip, a root compound named "", keys ``version``, ``dim``, ``ores`` with a palette,
``fluids``), and the world id as ``WorldIdHandler`` saves it (``data/visualprospecting.dat``,
``{data: {wId}}``). The ore mixes are ``gt_ore_mixes.json`` in GregtecLink's shape (D126). Every
mix, vein, world and player is made up here: never pack or game data. A real VP cache written
by VP in the dev runtime was read the same way (D127); it is not committed.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import shutil
import sqlite3
import struct
from collections.abc import Iterable
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from gregtec import cli, config
from gregtec.db import access
from gregtec.db import ores as ore_db
from gregtec.gamestate import safe_open
from gregtec.ingest import collect, nbt, ore_mixes, vp

CLAYTON = "11111111-2222-4333-8444-555555555555"
FRIEND = "66666666-7777-4888-9999-aaaaaaaaaaaa"
WORLD = "lab-ores"
WID = f"{WORLD}_0f9e8d7c-6b5a-4938-8271-605f4e3d2c1b"
RUN = "20261106T120000Z"

# --- an NBT writer, as Minecraft 1.7.10 writes (big-endian, Java's modified UTF-8) ---------


class Tag:
    """A value with an explicit NBT type, for the types plain Python can't tell apart."""

    def __init__(self, kind: int, value: object) -> None:
        self.kind, self.value = kind, value


def byte(v: int) -> Tag:
    return Tag(nbt.BYTE, v)


def ints(v: Iterable[int]) -> Tag:
    return Tag(nbt.INT_ARRAY, list(v))


def jstring(s: str) -> bytes:
    """Java's DataOutput.writeUTF: modified UTF-8 (NUL as C0 80, surrogates one by one)."""
    out = bytearray()
    for unit in struct.unpack(
        f"<{len(s.encode('utf-16-le')) // 2}H", s.encode("utf-16-le")
    ):
        if 0 < unit < 0x80:
            out.append(unit)
        elif unit < 0x800:
            out += bytes((0xC0 | unit >> 6, 0x80 | unit & 0x3F))
        else:
            out += bytes(
                (0xE0 | unit >> 12, 0x80 | unit >> 6 & 0x3F, 0x80 | unit & 0x3F)
            )
    return struct.pack(">H", len(out)) + bytes(out)


def kind_of(v: object) -> int:
    if isinstance(v, Tag):
        return v.kind
    if isinstance(v, dict):
        return nbt.COMPOUND
    if isinstance(v, str):
        return nbt.STRING
    if isinstance(v, bytes):
        return nbt.BYTE_ARRAY
    if isinstance(v, list):
        return nbt.LIST
    if isinstance(v, int):
        return nbt.INT
    raise TypeError(v)


def payload(v: object) -> bytes:
    kind = kind_of(v)
    raw = v.value if isinstance(v, Tag) else v
    if kind == nbt.BYTE:
        return struct.pack(">b", raw)
    if kind == nbt.INT:
        return struct.pack(">i", raw)
    if kind == nbt.STRING:
        assert isinstance(raw, str)
        return jstring(raw)
    if kind == nbt.BYTE_ARRAY:
        assert isinstance(raw, bytes)
        return struct.pack(">i", len(raw)) + raw
    if kind == nbt.INT_ARRAY:
        assert isinstance(raw, list)
        return struct.pack(f">i{len(raw)}i", len(raw), *raw)
    if kind == nbt.LIST:
        assert isinstance(raw, list)
        inner = kind_of(raw[0]) if raw else nbt.END
        return struct.pack(">bi", inner, len(raw)) + b"".join(payload(x) for x in raw)
    if kind == nbt.COMPOUND:
        assert isinstance(raw, dict)
        return (
            b"".join(
                bytes((kind_of(x),)) + jstring(k) + payload(x) for k, x in raw.items()
            )
            + b"\x00"
        )
    raise TypeError(kind)


def nbt_file(root: dict[str, Any], compressed: bool = True) -> bytes:
    data = bytes((nbt.COMPOUND,)) + jstring("") + payload(root)
    return gzip.compress(data) if compressed else data


Entry = tuple[int, int, str, bool, int]  # cx, cz, mix, depleted, source byte


def vp_dim(dim: int, veins: Iterable[Entry], version: int | None = 3) -> bytes:
    """One DIM<n>.dat as VP 1.5.42's saveToNbt writes it (palette in first-use order)."""
    veins = list(veins)
    palette: list[str] = []
    for _, _, mix, _, _ in veins:
        if mix not in palette:
            palette.append(mix)
    ores: dict[str, Any] = {}
    if veins:
        ores = {
            "palette": palette,
            "chunkX": ints(v[0] for v in veins),
            "chunkZ": ints(v[1] for v in veins),
            "veinTypeIndex": ints(palette.index(v[2]) for v in veins),
            "depleted": bytes(int(v[3]) for v in veins),
            "source": bytes(v[4] for v in veins),
        }
    root: dict[str, Any] = {"dim": dim, "ores": ores, "fluids": {}}
    if version is not None:
        root = {"version": version, **root}
    return nbt_file(root)


def world_id_file(wid: str) -> bytes:
    return nbt_file({"data": {"wId": wid}})


GEN, RESCAN, API = 1, 2, 3  # VeinSource bytes

# --- an export run's ore mixes, in GregtecLink's shape -------------------------------------


def ore(
    material: str, name: str, meta: int, item: str = "gregtech:gt.blockores2"
) -> dict[str, Any]:
    return {
        "material": material,
        "name": name,
        "class": "gregtech.api.enums.Materials",
        "stone": "Stone",
        "ore": {"item": item, "meta": meta, "n": 1},
    }


def layer(
    name: str,
    localized: str,
    dims: list[str],
    ores: tuple[dict[str, Any], ...],
    y: tuple[int, int] = (10, 40),
    heights: dict[str, list[int]] | None = None,
) -> dict[str, Any]:
    o: dict[str, Any] = {
        "name": name,
        "localized": localized,
        "min_y": y[0],
        "max_y": y[1],
        "weight": 40,
        "density": 3,
        "size": 24,
        "dims": sorted(dims),
        "ores": dict(zip(ore_mixes.LAYERS, ores, strict=True)),
    }
    if heights:
        o["dim_heights"] = heights
    return o


FERRITE = ore("Ferrite", "Ferrite", 901)
MAGNETITE = ore("Magnetitoid", "Magnetitoid", 902)
COPPERISH = ore("Copperish", "Copperish", 903)
TINITE = ore("Tinite", "Tinite", 904)
QUARTZ = ore("NetherQuartzish", "Nether Quartzish", 905)
LUNITE = ore("Lunite", "Lunite", 906, "bartworks:bw.blockores.01")
LAYERS = [
    layer(
        "ore.mix.ferrite",
        "Ferrite",
        ["Overworld", "Twilight Forest"],
        (FERRITE, MAGNETITE, FERRITE, COPPERISH),
        heights={"Twilight Forest": [15, 40]},
    ),
    layer(
        "ore.mix.copperish",
        "Copperish",
        ["Overworld", "Nether"],
        (COPPERISH, TINITE, COPPERISH, FERRITE),
        y=(5, 60),
    ),
    layer(
        "ore.mix.quartzish",
        "Nether Quartzish",
        ["Nether"],
        (QUARTZ, QUARTZ, QUARTZ, TINITE),
    ),
    layer("ore.mix.lunite", "Lunite", ["moon"], (LUNITE, LUNITE, TINITE, TINITE)),
]
DIMENSIONS = [
    {"dim": -1, "provider": "net.minecraft.world.WorldProviderHell", "name": "Nether"},
    {
        "dim": 0,
        "provider": "net.minecraft.world.WorldProviderSurface",
        "name": "Overworld",
    },
    {"dim": 1, "provider": "net.minecraft.world.WorldProviderEnd", "name": "The End"},
    {
        "dim": 7,
        "provider": "twilightforest.world.WorldProviderTwilightForest",
        "name": "Twilight Forest",
    },
]


def ore_mix_bytes(layers: list[dict[str, Any]] | None = None) -> bytes:
    doc = {"layers": LAYERS if layers is None else layers, "dimensions": DIMENSIONS}
    return (json.dumps(doc, indent=2) + "\n").encode("utf-8")


def make_run(
    home: Path, run_id: str = RUN, data: bytes | None = None, **manifest: object
) -> Path:
    """A collected run holding gt_ore_mixes.json, as `gos export collect` leaves it."""
    data = ore_mix_bytes() if data is None else data
    run = home / collect.DATA_EXPORTS / run_id
    run.mkdir(parents=True)
    (run / ore_mixes.FILE).write_bytes(data)
    m: dict[str, Any] = {
        "format": collect.FORMAT,
        "run_id": run_id,
        "created_utc": "2026-11-06T12:00:00Z",
        "complete": True,
        "mode": "autoexport",
        "files": {
            ore_mixes.FILE: {
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "lines": data.count(b"\n"),
            }
        },
        "counts": {"ore_mixes": 4, "ore_mix_errors": 0, "dimensions": 4},
        "parts": {"gt_dump": "ok", "machines": "ok", "ore_mixes": "ok", "refs": "ok"},
    }
    m.update(manifest)
    (run / collect.MANIFEST).write_text(json.dumps(m), encoding="utf-8")
    return run


# --- a Lab instance with a prospected world ------------------------------------------------

OVERWORLD = [
    (1, 1, "ore.mix.ferrite", False, GEN),
    (4, 1, "ore.mix.copperish", False, GEN),
    (-2, 4, "ore.mix.ferrite", False, GEN),
    (10, -5, "ore.mix.ferrite", False, GEN),
    (40, 40, "ore.mix.retired", False, GEN),  # a mix this export doesn't have
]
NETHER = [
    (1, 1, "ore.mix.quartzish", False, GEN),
    (-5, 7, "ore.mix.copperish", False, GEN),
]
TWILIGHT = [(1, -2, "ore.mix.ferrite", False, GEN)]
# What Clayton prospected: two Overworld veins, one marked depleted, and the Nether's quartz.
CLIENT_OW = [
    (1, 1, "ore.mix.ferrite", True, GEN),
    (4, 1, "ore.mix.copperish", False, GEN),
]
CLIENT_NETHER = [(1, 1, "ore.mix.quartzish", False, GEN)]


def write_world(
    game: Path,
    world: str = WORLD,
    wid: str | None = WID,
    server: dict[int, list[Entry]] | None = None,
    clients: dict[str, dict[int, list[Entry]]] | None = None,
) -> Path:
    w = game / "saves" / world
    (w / "data").mkdir(parents=True)
    (w / "level.dat").write_bytes(nbt_file({"Data": {"LevelName": world}}))
    if wid is not None:
        (w / vp.WORLD_ID_FILE).write_bytes(world_id_file(wid))
    folder = wid or f"{world}_00000000-0000-4000-8000-000000000000"
    if server is None:
        server = {0: OVERWORLD, -1: NETHER, 7: TWILIGHT}
    if clients is None:
        clients = {CLAYTON: {0: CLIENT_OW, -1: CLIENT_NETHER}}
    caches = [(game / vp.VP_DIR / vp.SERVER_DIR / folder, server)]
    caches += [
        (game / vp.VP_DIR / vp.CLIENT_DIR / who / folder, dims)
        for who, dims in clients.items()
    ]
    for path, dims in caches:
        path.mkdir(parents=True)
        for dim, veins in dims.items():
            (path / f"DIM{dim}.dat").write_bytes(vp_dim(dim, veins))
    return w


def prism(tmp_path: Path, lab_world: str | None = WORLD) -> tuple[Path, Path, Path]:
    """(the Lab's game folder, gos.toml, GOS_HOME) with a collected run in GOS_HOME."""
    instances = tmp_path / "instances"
    lab = instances / "GTNH-Lab"
    game = lab / ".minecraft"
    (game / "saves").mkdir(parents=True)
    (lab / "instance.cfg").write_text("name=GTNH-Lab\n", encoding="utf-8")
    home = tmp_path / "home"
    make_run(home)
    toml = tmp_path / "gos.toml"
    lines = [
        "[paths]",
        f'gos_home = "{home.as_posix()}"',
        f'prism_instances = "{instances.as_posix()}"',
    ]
    if lab_world:
        lines.append(f'lab_world = "{lab_world}"')
    toml.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return game, toml, home


def run(*args: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def db(home: Path) -> sqlite3.Connection:
    return access.connect(access.db_path(home))


@pytest.fixture
def lab(tmp_path: Path) -> tuple[Path, Path, Path]:
    game, toml, home = prism(tmp_path)
    write_world(game)
    return game, toml, home


# --- NBT -----------------------------------------------------------------------------------


def test_nbt_reads_what_minecraft_writes() -> None:
    root = {
        "b": byte(-3),
        "i": -70000,
        "s": "Gregtec\x00OS \U0001f527",  # NUL and a character past U+FFFF: modified UTF-8
        "ba": b"\x00\x01\xff",
        "ia": ints([1, -2, 2**31 - 1]),
        "l": ["a", "b"],
        "c": {"inner": [{"x": 1}, {"x": 2}]},
        "e": [],
    }
    want = {
        "b": -3,
        "i": -70000,
        "s": "Gregtec\x00OS \U0001f527",
        "ba": b"\x00\x01\xff",
        "ia": [1, -2, 2**31 - 1],
        "l": ["a", "b"],
        "c": {"inner": [{"x": 1}, {"x": 2}]},
        "e": [],
    }
    for compressed in (True, False):
        assert nbt.read(nbt_file(root, compressed)) == ("", want)


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (nbt_file({"a": 1})[:-5], "gzip data ends early"),
        (nbt_file({"a": 1}, compressed=False)[:-2], "truncated"),
        (b"\x08\x00\x00", "not a compound"),
        (b"\x0a\x00\x00\x0c\x00\x01x\x00", "unknown NBT tag type 12"),
        (b"\x0a\x00\x00\x07\x00\x01x\xff\xff\xff\xff\x00", "negative length"),
        (b"\x1f\x8bnot gzip at all", "bad gzip data"),
    ],
)
def test_nbt_refuses_bad_data(data: bytes, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        nbt.read(data)


def test_nbt_nested_too_deep_is_a_value_error() -> None:
    # 600 compounds deep: past MAX_DEPTH, and past Python's stack on some builds.
    deep = b"\x0a\x00\x00" + b"\x0a\x00\x01x" * 600 + b"\x00" * 601
    with pytest.raises(ValueError, match="nested too deep"):
        nbt.read(deep)


def test_nbt_refuses_a_gzip_bomb(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="once decompressed"):
        nbt.decompress(gzip.compress(b"\x00" * 5000), limit=4096)


# --- VisualProspecting's caches ------------------------------------------------------------


def test_a_v3_dimension_reads_as_vp_loads_it() -> None:
    entries = [
        (1, 1, "ore.mix.ferrite", False, GEN),
        (4, 1, "ore.mix.none", False, GEN),  # VP's sentinel: skipped
        (-2, 4, "ore.mix.copperish", True, RESCAN),
        (7, 7, "ore.mix.ferrite", False, 200),  # a source byte VP doesn't know: unknown
    ]
    version, dim, veins = vp.parse_dimension(vp_dim(-1, entries))
    assert (version, dim) == (3, -1)
    assert veins == [
        vp.Vein(-1, 1, 1, "ore.mix.ferrite", False, "generated"),
        vp.Vein(-1, -2, 4, "ore.mix.copperish", True, "rescan"),
        vp.Vein(-1, 7, 7, "ore.mix.ferrite", False, "unknown"),
    ]
    assert vp.parse_dimension(vp_dim(0, [])) == (3, 0, [])  # VP's empty ores compound


def test_an_index_outside_the_palette_is_skipped() -> None:
    root = {
        "version": 3,
        "dim": 0,
        "ores": {
            "palette": ["ore.mix.ferrite"],
            "chunkX": ints([1, 4]),
            "chunkZ": ints([1, 1]),
            "veinTypeIndex": ints([0, 5]),
            "depleted": b"\x00\x00",
            "source": b"\x01\x01",
        },
    }
    assert [v.cx for v in vp.parse_dimension(nbt_file(root))[2]] == [1]


@pytest.mark.parametrize(
    ("root", "error"),
    [
        ({"version": 3, "ores": {}}, "no int dim"),
        ({"version": 3, "dim": 0, "ores": {"palette": [], "chunkX": "x"}}, "chunkX"),
        (
            {
                "version": 3,
                "dim": 0,
                "ores": {
                    "palette": ["a"],
                    "chunkX": ints([1, 2]),
                    "chunkZ": ints([1]),
                    "veinTypeIndex": ints([0]),
                    "depleted": b"\x00",
                    "source": b"\x01",
                },
            },
            "differ in length",
        ),
    ],
)
def test_a_file_vp_couldnt_have_written_is_refused(
    root: dict[str, Any], error: str
) -> None:
    with pytest.raises(vp.FormatError, match=error):
        vp.parse_dimension(nbt_file(root))


def test_a_cache_collapses_duplicates_by_vps_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_open, "BACKOFF_S", 0)
    folder = tmp_path / "cache"
    folder.mkdir()
    # Two files for one dimension (VP reads every *.dat and goes by "dim"): a rescan, then
    # the generated entry, then an API entry it doesn't trust as much.
    (folder / "DIM0.dat").write_bytes(vp_dim(0, [(1, 1, "ore.mix.old", True, RESCAN)]))
    (folder / "DIM0-copy.dat").write_bytes(
        vp_dim(
            0,
            [
                (1, 1, "ore.mix.ferrite", False, GEN),
                (4, 4, "ore.mix.ferrite", False, GEN),
            ],
        )
    )
    (folder / "zz.dat").write_bytes(vp_dim(0, [(4, 4, "ore.mix.other", True, API)]))
    (folder / "DIM1.dat").write_bytes(
        vp_dim(1, [(1, 1, "ore.mix.x", False, GEN)], version=None)
    )
    (folder / "DIM2.dat").write_bytes(
        vp_dim(2, [(1, 1, "ore.mix.x", False, GEN)], version=4)
    )
    (folder / "DIM3.dat").write_bytes(b"\x1f\x8b\x08\x00cut off")
    (folder / "notes.txt").write_text("not a cache", encoding="utf-8")
    cache = vp.read_cache(folder)
    assert cache.veins == {
        # The generated entry replaced the rescan and kept its depleted flag.
        (0, 1, 1): vp.Vein(0, 1, 1, "ore.mix.ferrite", True, "generated"),
        # The API entry, trusted less, only added its depleted flag.
        (0, 4, 4): vp.Vein(0, 4, 4, "ore.mix.ferrite", True, "generated"),
    }
    assert cache.files == ["DIM0-copy.dat", "DIM0.dat", "zz.dat"]
    assert [p.split(":")[0] for p in cache.problems] == ["DIM1.dat", "DIM2.dat"]
    assert "no version (VP 1.4, GTNH 2.8)" in cache.problems[0]
    assert "format 4" in cache.problems[1]
    # A file VP could still be writing is kept apart: gos ores load refuses on it.
    assert len(cache.unreadable) == 1 and cache.unreadable[0].startswith(
        f"{folder / 'DIM3.dat'}: unreadable after 3 tries ("
    )


def test_a_partial_read_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # VP writes its caches in place, so a read can catch half a file: read_bytes tries again.
    monkeypatch.setattr(safe_open, "BACKOFF_S", 0)
    folder = tmp_path / "cache"
    folder.mkdir()
    path = folder / "DIM0.dat"
    full = vp_dim(0, OVERWORLD)
    path.write_bytes(full)
    reads: list[int] = []
    real = safe_open.safe_open

    def flaky(p: safe_open.StrPath) -> contextlib.AbstractContextManager[BinaryIO]:
        reads.append(1)
        path.write_bytes(full[: len(full) // 2] if len(reads) == 1 else full)
        return real(p)

    monkeypatch.setattr(safe_open, "safe_open", flaky)
    cache = vp.read_cache(folder)
    assert len(cache.veins) == len(OVERWORLD) and len(reads) == 2 and not cache.problems


def test_locate_finds_the_caches_by_the_worlds_id(tmp_path: Path) -> None:
    game = tmp_path / ".minecraft"
    w = write_world(game, clients={CLAYTON: {0: CLIENT_OW}})
    loc = vp.locate(w, game)
    assert loc.world_id == WID and loc.player == CLAYTON
    assert loc.server == game / "visualprospecting" / "server" / WID
    assert loc.client == game / "visualprospecting" / "client" / CLAYTON / WID
    assert loc.notes == ()


def test_a_renamed_world_still_finds_its_caches(tmp_path: Path) -> None:
    game = tmp_path / ".minecraft"
    write_world(game)
    (game / "saves" / WORLD).rename(game / "saves" / "lab-ores-copy")
    loc = vp.locate(game / "saves" / "lab-ores-copy", game)
    assert loc.world_id == WID and loc.server is not None


def test_without_the_world_id_file_the_only_named_folder_is_used(
    tmp_path: Path,
) -> None:
    game = tmp_path / ".minecraft"
    w = write_world(game, wid=None)
    loc = vp.locate(w, game)
    assert loc.world_id == f"{WORLD}_00000000-0000-4000-8000-000000000000"
    assert "is missing; using the only cache folder" in loc.notes[0]
    # "lab-ores_2" is another world: its caches never count as lab-ores's.
    (game / "visualprospecting" / "server" / f"{WORLD}_2_{CLAYTON}").mkdir()
    (game / "visualprospecting" / "server" / f"{WORLD}_backup").mkdir()
    assert (
        vp.locate(w, game).world_id == f"{WORLD}_00000000-0000-4000-8000-000000000000"
    )
    (game / "visualprospecting" / "server" / f"{WORLD}_{FRIEND}").mkdir()
    with pytest.raises(vp.VpError, match="several VP cache folders"):
        vp.locate(w, game)


def test_another_worlds_caches_are_never_guessed(tmp_path: Path) -> None:
    game = tmp_path / ".minecraft"
    write_world(game, world="New World_2", wid=f"New World_2_{CLAYTON}")
    w = game / "saves" / "New World"
    w.mkdir()
    loc = vp.locate(w, game)
    assert loc.world_id is None and loc.server is None
    with pytest.raises(vp.VpError, match="no data for it yet"):
        vp.locate(w, game, CLAYTON)


def test_a_world_vp_has_not_seen(tmp_path: Path) -> None:
    game = tmp_path / ".minecraft"
    w = game / "saves" / "fresh"
    w.mkdir(parents=True)
    loc = vp.locate(w, game)
    assert loc.world_id is None and loc.server is None and loc.client is None
    assert "no data for this world yet" in loc.notes[0]


def test_the_player_is_picked_like_quests_pick_him(tmp_path: Path) -> None:
    game = tmp_path / ".minecraft"
    w = write_world(game, clients={CLAYTON: {0: CLIENT_OW}, FRIEND: {0: []}})
    with pytest.raises(vp.VpError, match="several players"):
        vp.locate(w, game)
    assert vp.locate(w, game, FRIEND.upper()).player == FRIEND
    with pytest.raises(vp.VpError, match="has no client cache"):
        vp.locate(w, game, "99999999-2222-4333-8444-555555555555")
    other = tmp_path / "other"
    w2 = write_world(other, clients={})
    loc = vp.locate(w2, other)
    assert loc.player is None and "no client cache for this world yet" in loc.notes[-1]


def link_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("can't make symlinks here")


def test_links_are_refused(tmp_path: Path) -> None:
    game = tmp_path / ".minecraft"
    w = write_world(game)
    # A dimension file that is a link is skipped, never followed.
    server = game / vp.VP_DIR / vp.SERVER_DIR / WID
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "DIM5.dat").write_bytes(vp_dim(5, [(1, 1, "ore.mix.x", False, GEN)]))
    link_or_skip(server / "DIM5.dat", elsewhere / "DIM5.dat")
    cache = vp.read_cache(server)
    assert "DIM5.dat: not a regular file; skipped" in cache.problems
    assert all(dim != 5 for dim, _, _ in cache.veins)
    # A cache folder that is a link is refused.
    server.rename(elsewhere / WID)
    link_or_skip(server, elsewhere / WID)
    with pytest.raises(vp.VpError, match="is a link or junction"):
        vp.locate(w, game)
    # So is a world-id file that is a link.
    wid_file = w / vp.WORLD_ID_FILE
    wid_file.rename(elsewhere / "wid.dat")
    link_or_skip(wid_file, elsewhere / "wid.dat")
    with pytest.raises(vp.VpError, match="not a regular file"):
        vp.world_id(w)


@pytest.mark.parametrize("wid", ["", "..", "a/b", "a\\b", "C:x", "a\x00b", "a\nb"])
def test_a_bad_world_id_is_refused_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wid: str
) -> None:
    # A wId that can't name one folder is refused, not retried: the file read fine.
    game = tmp_path / ".minecraft"
    w = write_world(game)
    (w / vp.WORLD_ID_FILE).write_bytes(world_id_file(wid))
    reads: list[safe_open.StrPath] = []
    real = safe_open.safe_open

    def counted(p: safe_open.StrPath) -> contextlib.AbstractContextManager[BinaryIO]:
        reads.append(p)
        return real(p)

    monkeypatch.setattr(safe_open, "safe_open", counted)
    with pytest.raises(vp.VpError, match="no usable data.wId") as exc:
        vp.locate(w, game)
    assert "unreadable" not in str(exc.value) and len(reads) == 1


def test_server_and_client_merge(tmp_path: Path) -> None:
    game = tmp_path / ".minecraft"
    clients = {
        CLAYTON: {
            0: [
                *CLIENT_OW,
                (-2, 4, "ore.mix.copperish", False, GEN),
                (7, 7, "ore.mix.ferrite", False, GEN),
            ]
        }
    }
    w = write_world(game, clients=clients)
    s, c = vp.read_all(vp.locate(w, game))
    merged, differ = vp.merge(s, c)
    by = {(m.vein.dim, m.vein.cx, m.vein.cz): m for m in merged}
    assert differ == 1  # (-2, 4): the client has another mix; the server's is kept
    assert by[(0, -2, 4)].vein.mix == "ore.mix.ferrite" and by[(0, -2, 4)].known
    assert (
        by[(0, 1, 1)].known
        and by[(0, 1, 1)].vein.depleted
        and by[(0, 1, 1)].cache == "server"
    )
    assert not by[(0, 10, -5)].known and not by[(0, 10, -5)].vein.depleted
    assert (
        by[(0, 7, 7)].cache == "client" and by[(0, 7, 7)].known
    )  # not in the server cache
    assert vp.counts(merged)[0] == (6, 4, 1)
    no_server = vp.merge(None, c)[0]
    assert {m.cache for m in no_server} == {"client"} and len(no_server) == 4


# --- ore mixes -----------------------------------------------------------------------------


def test_ore_mixes_parse() -> None:
    om = ore_mixes.parse(ore_mix_bytes())
    ferrite = om.mixes[0]
    assert (ferrite.id, ferrite.name, ferrite.ymin, ferrite.ymax) == (
        "ore.mix.ferrite",
        "Ferrite",
        10,
        40,
    )
    assert ferrite.heights == {"Twilight Forest": (15, 40)}
    assert [(o.layer, o.material, o.registry_name, o.meta) for o in ferrite.ores] == [
        ("primary", "Ferrite", "gregtech:gt.blockores2", 901),
        ("secondary", "Magnetitoid", "gregtech:gt.blockores2", 902),
        ("between", "Ferrite", "gregtech:gt.blockores2", 901),
        ("sporadic", "Copperish", "gregtech:gt.blockores2", 903),
    ]
    assert om.ids_by_name() == {
        "Nether": [-1],
        "Overworld": [0],
        "The End": [1],
        "Twilight Forest": [7],
    }
    assert om.problems == []


@pytest.mark.parametrize(
    ("doc", "error"),
    [
        (b"[]", "no layers"),
        (b"{not json", "not JSON"),
        (json.dumps({"layers": [{"name": "x"}]}).encode(), "dims"),
        (json.dumps({"layers": [LAYERS[0], LAYERS[0]]}).encode(), "listed twice"),
        (json.dumps({"layers": [], "dimensions": [{"name": "x"}]}).encode(), "int dim"),
    ],
)
def test_bad_ore_mixes_are_refused(doc: bytes, error: str) -> None:
    with pytest.raises(ore_mixes.FormatError, match=error):
        ore_mixes.parse(doc)


def test_a_layer_with_an_error_is_kept_and_reported() -> None:
    broken = {**LAYERS[0], "ores": {"primary": FERRITE}, "errors": ["secondary: boom"]}
    om = ore_mixes.parse(ore_mix_bytes([broken]))
    assert len(om.mixes[0].ores) == 1
    assert om.problems == [
        "ore.mix.ferrite: secondary: boom",
        "ore.mix.ferrite: ores missing (a layer)",
    ]


# --- the DB and the commands ---------------------------------------------------------------


def load(toml: Path, *more: str) -> tuple[int, str]:
    return run("ores", "load", "--config", str(toml), *more)


def test_load_and_list_two_dimensions(lab: tuple[Path, Path, Path]) -> None:
    # WO-1.6's acceptance in shape: veins listed for two dimensions of a Lab world.
    _, toml, home = lab
    code, out = load(toml)
    assert code == 0, out
    assert f"Ore mixes: 4 from export run {RUN}" in out
    assert f"VisualProspecting world id: {WID}" in out
    assert "dim -1 (Nether): 2 veins, 1 known, 0 depleted" in out
    assert "dim 0 (Overworld): 5 veins, 2 known, 1 depleted" in out
    assert "dim 7 (Twilight Forest): 1 veins, 0 known, 0 depleted" in out
    assert "veins of mixes this export doesn't have: ore.mix.retired (1)" in out
    code, out = run("ores", "list", "--config", str(toml))
    assert code == 0
    assert out.splitlines()[1:5] == [
        "dim\tname\tveins\tknown\tdepleted\tmixes",
        "-1\tNether\t2\t1\t0\t2",
        "0\tOverworld\t5\t2\t1\t3",
        "7\tTwilight Forest\t1\t0\t0\t1",
    ]
    code, out = run("ores", "list", "--dim", "0", "--config", str(toml))
    assert code == 0
    rows = out.splitlines()
    assert rows[2] == "x\tz\tmix\tores\ty\tknown\tdepleted\tsrc"
    assert rows[3:8] == [
        "-24\t72\tFerrite\tFerrite, Magnetitoid, Ferrite, Copperish\t10-40\t0\t0\tserver/generated",
        "24\t24\tFerrite\tFerrite, Magnetitoid, Ferrite, Copperish\t10-40\t1\t1\tserver/generated",
        "72\t24\tCopperish\tCopperish, Tinite, Copperish, Ferrite\t5-60\t1\t0\tserver/generated",
        "168\t-72\tFerrite\tFerrite, Magnetitoid, Ferrite, Copperish\t10-40\t0\t0\tserver/generated",
        "648\t648\tore.mix.retired\t\t\t0\t0\tserver/generated",
    ]
    code, out = run("ores", "list", "--dim", "-1", "--config", str(toml))
    assert (
        "Nether Quartzish\tNether Quartzish, Nether Quartzish, Nether Quartzish, Tinite"
        in out
    )
    assert "# 2 veins" in out
    # The Twilight Forest's own heights for the Ferrite mix.
    assert "\t15-40\t" in run("ores", "list", "--dim", "7", "--config", str(toml))[1]
    conn = db(home)
    try:
        src = json.loads(
            conn.execute("SELECT value FROM kv WHERE key = 'ores.veins'").fetchone()[0]
        )
        mixes = json.loads(
            conn.execute("SELECT value FROM kv WHERE key = 'ores.mixes'").fetchone()[0]
        )
        row = conn.execute(
            "SELECT dims, ores FROM ore_mix WHERE id = 'ore.mix.ferrite'"
        ).fetchone()
    finally:
        conn.close()
    assert src["world_id"] == WID and src["player"] == CLAYTON and src["veins"] == 8
    assert mixes["run"] == RUN and mixes["mixes"] == 4
    assert json.loads(row[0]) == {
        "Overworld": {"ids": [0]},
        "Twilight Forest": {"ids": [7], "ymax": 40, "ymin": 15},
    }
    assert json.loads(row[1])[1] == {
        "item": "gregtech:gt.blockores2",
        "layer": "secondary",
        "material": "Magnetitoid",
        "meta": 902,
        "name": "Magnetitoid",
    }


def test_ore_find(lab: tuple[Path, Path, Path]) -> None:
    _, toml, home = lab
    assert load(toml)[0] == 0
    conn = db(home)
    try:
        # Any case, GT name or display name; live veins first, then by dimension and chunk.
        found = ore_db.find(conn, "ferrite")
        assert [(f.dim, f.x, f.z, f.depleted) for f in found] == [
            (-1, -72, 120, False),  # Copperish's sporadic layer
            (0, -24, 72, False),
            (0, 72, 24, False),
            (0, 168, -72, False),
            (7, 24, -24, False),
            (0, 24, 24, True),
        ]
        assert found[0].layers == ("sporadic",) and found[1].layers == (
            "primary",
            "between",
        )
        assert ore_db.find(conn, "Nether quartzish")[0].mix == "Nether Quartzish"
        # Nearest first in one dimension, with the distance in blocks.
        near = ore_db.find(conn, "Ferrite", dim=0, near=(150, -60))
        assert [(f.x, f.z, f.distance) for f in near] == [
            (168, -72, 22),
            (72, 24, 115),
            (-24, 72, 218),
            (24, 24, 151),  # depleted: last
        ]
        assert (
            near[0].ymin == 10
            and near[0].ymax == 40
            and near[0].src == "server/generated"
        )
        assert not near[0].known and near[-1].known
        tf = ore_db.find(conn, "Ferrite", dim=7)
        assert (tf[0].ymin, tf[0].ymax, tf[0].distance) == (15, 40, None)
        assert ore_db.find(conn, "Lunite") == []  # a mix with no vein found
        assert ore_db.find(conn, "Unobtainium") == []
        assert len(ore_db.find(conn, "Ferrite", limit=2)) == 2
        with pytest.raises(ValueError, match="give dim too"):
            ore_db.find(conn, "Ferrite", near=(0, 0))
    finally:
        conn.close()


def test_the_server_cache_can_be_left_out(tmp_path: Path) -> None:
    game, toml, home = prism(tmp_path)
    write_world(game)
    toml.write_text(
        toml.read_text(encoding="utf-8") + "\n[policy]\nore_server_cache = false\n",
        encoding="utf-8",
    )
    code, out = load(toml)
    assert code == 0, out
    assert "server cache: not read ([policy] ore_server_cache = false)" in out
    conn = db(home)
    try:
        rows = conn.execute(
            "SELECT dim, known, src FROM vein ORDER BY dim, cx"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in rows] == [
        (-1, 1, "client/generated"),
        (0, 1, "client/generated"),
        (0, 1, "client/generated"),
    ]


def test_the_world_comes_from_lab_world_never_play_by_default(tmp_path: Path) -> None:
    game, toml, _ = prism(tmp_path, lab_world=None)
    write_world(game)
    code, out = load(toml)
    assert code == 1
    assert (
        "name the world: --world <folder in Lab's saves/> or [paths] lab_world" in out
    )
    assert load(toml, "--world", WORLD)[0] == 0
    code, out = load(toml, "--instance", "play", "--world", WORLD)
    assert code == 1 and "Play" in out  # there is no Play here; it is never the default
    code, out = load(toml, "--world", "../elsewhere")
    assert code == 1 and "not '../elsewhere'" in out


def test_output_never_breaks_a_windows_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows writes a pipe or a file in cp1252, which has no kana: a world named in Japanese.
    world = "ラボ"
    game, toml, _ = prism(tmp_path, lab_world=world)
    write_world(game, world=world, wid=f"{world}_0f9e8d7c-6b5a-4938-8271-605f4e3d2c1b")
    for command in (["load"], ["list"], ["list", "--dim", "0"]):
        raw = io.BytesIO()
        pipe = io.TextIOWrapper(raw, encoding="cp1252", newline="")
        monkeypatch.setattr("sys.stdout", pipe)
        assert cli.main(["ores", *command, "--config", str(toml)]) == 0
        pipe.flush()
        assert b"\\u30e9\\u30dc" in raw.getvalue()


def test_a_world_folder_anywhere(tmp_path: Path) -> None:
    _, toml, _ = prism(tmp_path)
    copy = tmp_path / "backup" / ".minecraft"
    w = write_world(copy)
    code, out = load(toml, "--world-dir", str(w))
    assert code == 0, out
    outside = tmp_path / "loose-world"
    write_world(tmp_path / "g2", world="x")
    (tmp_path / "g2" / "saves" / "x").rename(outside)
    code, out = load(toml, "--world-dir", str(outside))
    assert code == 1 and "--game-dir" in out
    code, out = load(
        toml, "--world-dir", str(outside), "--game-dir", str(tmp_path / "g2")
    )
    assert code == 0, out


def test_a_world_dir_in_play_needs_instance_play(tmp_path: Path) -> None:
    # A made-up Play instance in tmp_path: --world-dir never reads it by default.
    _, toml, _ = prism(tmp_path)
    play = tmp_path / "instances" / "GTNH-Play" / ".minecraft"
    w = write_world(play, world="survival")
    code, out = load(toml, "--world-dir", str(w))
    assert code == 1 and "is Play's: add --instance play" in out
    copy = tmp_path / "copy" / "saves" / "survival"
    copy.parent.mkdir(parents=True)
    (play / "saves" / "survival").rename(copy)
    code, out = load(toml, "--world-dir", str(copy), "--game-dir", str(play))
    assert code == 1 and "is Play's" in out  # Play's caches, through a copied world
    code, out = load(
        toml, "--world-dir", str(copy), "--game-dir", str(play), "--instance", "play"
    )
    assert code == 0 and out.startswith("Reading Play's world (read-only).")
    link = tmp_path / "linked-world"
    link_or_skip(link, copy)
    code, out = load(toml, "--world-dir", str(link), "--game-dir", str(play))
    assert code == 1 and "is a link or junction" in out


def test_game_dir_needs_world_dir(lab: tuple[Path, Path, Path]) -> None:
    _, toml, _ = lab
    with pytest.raises(SystemExit) as exc:
        cli.main(["ores", "load", "--game-dir", "x", "--config", str(toml)])
    assert exc.value.code == 2


def mix_count(home: Path) -> int:
    conn = db(home)
    try:
        return int(conn.execute("SELECT count(*) FROM ore_mix").fetchone()[0])
    finally:
        conn.close()


def test_veins_load_without_ore_mixes(tmp_path: Path) -> None:
    # Before Export runs a GregtecLink from WO-1.6 on, or with no collected run at all, the
    # veins still load: they show their mix's GT name only.
    game, toml, home = prism(tmp_path)
    write_world(game)
    shutil.rmtree(home / collect.DATA_EXPORTS / RUN)
    code, out = load(toml)
    assert code == 0, out
    assert "Ore mixes: not loaded (no collected run in" in out
    assert "the DB has none yet, so veins show only their mix's GT name" in out
    assert "dim 0: 5 veins, 2 known, 1 depleted" in out
    assert "veins of mixes this export doesn't have" not in out
    code, out = run("ores", "list", "--dim", "0", "--config", str(toml))
    assert code == 0 and "24\t24\tore.mix.ferrite\t\t\t1\t1\tserver/generated" in out

    # A run that has mixes loads them; a newer one without keeps them.
    make_run(home)
    assert load(toml)[0] == 0 and mix_count(home) == 4
    make_run(home, "20261106T130000Z", parts={"gt_dump": "ok"})
    code, out = load(toml)
    assert code == 0, out
    assert (
        "Ore mixes: not loaded (run 20261106T130000Z has no ore mixes (parts.ore_mixes is"
        " missing); export with a GregtecLink from WO-1.6 on, collect, and load again);"
        " kept the 4 an earlier load left" in out
    )
    assert mix_count(home) == 4
    assert "veins of mixes this export doesn't have: ore.mix.retired (1)" in out
    conn = db(home)
    try:
        kv = dict(conn.execute("SELECT key, value FROM kv").fetchall())
    finally:
        conn.close()
    assert json.loads(kv["ores.mixes"])["run"] == RUN
    assert (
        "20261106T130000Z has no ore mixes" in json.loads(kv["ores.veins"])["no_mixes"]
    )


def test_veins_load_into_a_new_gos_home(tmp_path: Path) -> None:
    # gos-dev's first gos command: GOS_HOME has no data/ yet, so no lock folder either.
    game, toml, home = prism(tmp_path)
    write_world(game)
    shutil.rmtree(home)
    code, out = load(toml, "--run", RUN)
    assert code == 1 and "doesn't exist: run `gos export collect` first" in out
    assert not home.exists()
    code, out = load(toml)
    assert code == 0, out
    assert "Ore mixes: not loaded (" in out and "doesn't exist" in out
    assert "dim 0: 5 veins, 2 known, 1 depleted" in out
    assert not (home / "data" / "locks").exists()


def test_a_named_run_must_have_ore_mixes(tmp_path: Path) -> None:
    game, toml, home = prism(tmp_path)
    write_world(game)
    make_run(home, "20261106T130000Z", parts={"gt_dump": "ok"})
    code, out = load(toml, "--run", "20261106T130000Z")
    assert code == 1 and "has no ore mixes (parts.ore_mixes is missing)" in out
    assert load(toml, "--run", RUN)[0] == 0
    code, out = load(toml, "--run", "20261106T999999Z")
    assert code == 1 and "no such collected run" in out
    make_run(
        home,
        "20261106T140000Z",
        parts={"ore_mixes": "failed"},
        part_errors={"ore_mixes": "java.lang.NullPointerException"},
    )
    code, out = load(toml, "--run", "20261106T140000Z")
    assert code == 1 and "failed: java.lang.NullPointerException" in out
    code, out = load(toml)  # the newest: noted, veins loaded
    assert code == 0 and "failed: java.lang.NullPointerException" in out


def test_a_tampered_ore_mix_file_is_refused(tmp_path: Path) -> None:
    game, toml, home = prism(tmp_path)
    write_world(game)
    tampered = make_run(home, "20261106T150000Z")
    (tampered / ore_mixes.FILE).write_bytes(ore_mix_bytes()[:-2] + b"]\n")
    code, out = load(toml)
    assert code == 1 and "doesn't match the manifest" in out
    (tampered / ore_mixes.FILE).write_bytes(ore_mix_bytes() + b"\n")
    code, out = load(toml)
    assert code == 1 and "is not a file of the listed size; collect it again" in out
    assert not access.db_path(home).exists()


def test_a_failed_load_changes_nothing(
    lab: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_open, "BACKOFF_S", 0)
    game, toml, home = lab
    assert load(toml)[0] == 0

    def snapshot() -> list[list[tuple[Any, ...]]]:
        conn = db(home)
        try:
            return [
                [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2, 3")]
                for t in ("ore_mix", "vein", "kv")
            ]
        finally:
            conn.close()

    before = snapshot()
    world = game / "saves" / WORLD
    wid_file = world / vp.WORLD_ID_FILE
    good = wid_file.read_bytes()
    wid_file.write_bytes(b"\x1f\x8bcut")
    code, out = load(toml)
    assert (
        code == 1
        and out.startswith("Not loaded:")
        and "unreadable after 3 tries" in out
    )
    assert snapshot() == before

    # A dimension file VP is saving (in place) whenever GOS reads it: the load is refused
    # rather than drop that dimension's veins until the next load.
    wid_file.write_bytes(good)
    dim0 = game / vp.VP_DIR / vp.SERVER_DIR / WID / "DIM0.dat"
    full = dim0.read_bytes()
    dim0.write_bytes(full[:20])
    code, out = load(toml)
    assert code == 1 and out.startswith(f"Not loaded: {dim0}: unreadable after 3 tries")
    assert "may have been saving it: try again in a moment" in out
    assert snapshot() == before
    dim0.write_bytes(full)
    code, out = load(toml)
    assert code == 0 and "dim 0 (Overworld): 5 veins" in out


def test_list_before_any_load(tmp_path: Path) -> None:
    _, toml, _ = prism(tmp_path)
    code, out = run("ores", "list", "--config", str(toml))
    assert code == 1 and "run `gos ores load` first" in out


def test_config_keys() -> None:
    assert (
        config.Config().lab_world is None and config.Config().ore_server_cache is True
    )


def test_a_bad_policy_value_is_refused(tmp_path: Path) -> None:
    toml = tmp_path / "gos.toml"
    toml.write_text('[policy]\nore_server_cache = "yes"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match="ore_server_cache"):
        config.load(toml)
