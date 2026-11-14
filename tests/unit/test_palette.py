"""WO-10.3: the palette reader, the block catalog tables, a world's registry from level.dat, the
palette rule, ``gos palette`` and the ``block_find`` tool, on fixtures (D307, D308)."""

from __future__ import annotations

import gzip
import json
import sqlite3
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.agents.tools import build_registry
from gregtec.agents.tools.registry import DEEP, FAST
from gregtec.agents.tools.tsv import parse as parse_tsv
from gregtec.build import cli as palette_cli
from gregtec.build import palette, worldreg
from gregtec.db import access
from gregtec.db import blocks as block_db
from gregtec.ingest import nbt
from gregtec.ingest import palette as ingest

FIXTURE_V5 = Path(__file__).resolve().parents[1] / "fixtures" / "gos-schema-v5.sql"


# --- fixtures ------------------------------------------------------------------------------


def state(block: str, meta: int = 0, **kw: Any) -> dict[str, Any]:  # noqa: ANN401
    line: dict[str, Any] = {
        "block": block,
        "meta": meta,
        "item": {"item": block, "meta": kw.pop("item_meta", meta)},
        "name": kw.pop("name", block.split(":")[1]),
        "unlocalized": "tile.x",
        "mod": block.split(":")[0],
        "has_te": kw.pop("has_te", False),
        "opaque": True,
        "full_cube": True,
        "normal_cube": True,
        "standard_render": True,
        "liquid": False,
        "falls": False,
        "light": 0,
        "light_opacity": 255,
        "tab": "buildingBlocks",
        "faces": kw.pop("faces", ["stone"] * 6),
    }
    line.update(kw)
    return line


STATES = [
    state("GoodGenerator:MAR_Casing", name="Magnetic Flux Casing"),
    state("bartworks:BW_GlasBlocks", 0, name="Borosilicate Glass Block", opaque=False),
    state(
        "gregtech:gt.blockcasings",
        1,
        name="LV Machine Casing",
        faces=["gregtech:iconsets/MACHINE_LV_SIDE"] * 6,
    ),
    state("gregtech:gt.blockcasings", 2, name="MV Machine Casing"),
    state("gregtech:gt.blockcasings5", 0, name="Cupronickel Coil Block"),
    state("gregtech:gt.blockmachines", 0, item_meta=1, name="Bronze Hull", has_te=True),
    state(
        "gregtech:gt.blockmachines",
        0,
        item_meta=1000,
        name="Electric Blast Furnace",
        has_te=True,
    ),
    state("gregtech:gt.blockores", 0, name="Ore", has_te=True, hidden=True),
    state("minecraft:chest", 0, name="Chest", has_te=True, opaque=False),
    state("minecraft:glowstone", 0, name="Glowstone", light=15),
    state("minecraft:sand", 0, name="Sand", falls=True),
    state("minecraft:stone", 0, name="Stone"),
    state("minecraft:wool", 20, name="Odd Wool"),
]
TEXTURES = [
    {
        "icon": "gregtech:iconsets/MACHINE_LV_SIDE",
        "path": "assets/gregtech/textures/blocks/iconsets/MACHINE_LV_SIDE.png",
        "sha256": "ab" * 32,
        "width": 16,
        "height": 16,
        "rgb": "#9d9d9d",
        "alpha": 255,
    },
    {
        "icon": "ic2:blockAlloy:0",
        "path": "assets/ic2/textures/blocks/blockAlloy:0.png",
        "error": "java.io.FileNotFoundException: ic2:textures/blocks/blockAlloy:0.png",
    },
    {
        "icon": "stone",
        "path": "assets/minecraft/textures/blocks/stone.png",
        "sha256": "cd" * 32,
        "width": 16,
        "height": 16,
        "rgb": "#7d7d7d",
        "alpha": 255,
    },
]


def write_run(
    folder: Path, states: list[dict[str, Any]], textures: list[dict[str, Any]]
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ingest.FILE).write_text(
        "".join(json.dumps(s) + "\n" for s in states), encoding="utf-8"
    )
    (folder / ingest.TEXTURES).write_text(
        "".join(json.dumps(t) + "\n" for t in textures), encoding="utf-8"
    )
    return folder


def _jstring(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _named(kind: int, name: str, body: bytes) -> bytes:
    return bytes((kind,)) + _jstring(name) + body


def level_dat(blocks: list[str], items: list[str] = (), fml: bool = True) -> bytes:  # type: ignore[assignment]
    """A Forge 1.7.10 level.dat: ``Data`` plus ``FML.ItemData`` as ``{K, V}`` compounds, a
    block's K starting with \\x01 and an item's with \\x02 (ids are arbitrary here)."""
    entries = b""
    for i, name in enumerate(
        [f"\x01{b}" for b in blocks] + [f"\x02{i}" for i in items]
    ):
        entries += (
            _named(nbt.STRING, "K", _jstring(name))
            + _named(nbt.INT, "V", struct.pack(">i", 4096 + i))
            + b"\x00"
        )
        count = len(blocks) + len(items)
    item_data = struct.pack(">bi", nbt.COMPOUND, len(blocks) + len(items)) + entries
    fml_tag = _named(nbt.LIST, "ItemData", item_data) + b"\x00"
    data = _named(nbt.STRING, "LevelName", _jstring("Lab")) + b"\x00"
    body = _named(nbt.COMPOUND, "Data", data)
    if fml:
        body += _named(nbt.COMPOUND, "FML", fml_tag)
    del count
    return gzip.compress(_named(nbt.COMPOUND, "", body + b"\x00"))


WORLD_BLOCKS = [
    "GoodGenerator:MAR_Casing",
    "bartworks:BW_GlasBlocks",
    "gregtech:gt.blockcasings",
    "gregtech:gt.blockcasings5",
    "gregtech:gt.blockmachines",
    "gregtech:gt.blockores",
    "minecraft:air",
    "minecraft:chest",
    "minecraft:glowstone",
    "minecraft:sand",
    "minecraft:stone",
    "minecraft:wool",
]


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(tmp_path / "gos.sqlite")
    c.execute(
        "INSERT INTO export_run(id, fingerprint, run_key) VALUES (1, '', 'run-a')"
    )
    c.execute(
        "INSERT INTO export_run(id, fingerprint, run_key) VALUES (2, '', 'run-b')"
    )
    block_db.write(c, 1, ingest.read(write_run(tmp_path / "run", STATES, TEXTURES)))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def world(tmp_path: Path) -> worldreg.WorldRegistry:
    w = tmp_path / "saves" / "lab-shells"
    w.mkdir(parents=True)
    (w / "level.dat").write_bytes(level_dat(WORLD_BLOCKS, ["minecraft:stick"]))
    return worldreg.read(w)


# --- the reader ----------------------------------------------------------------------------


def test_reads_states_textures_and_block_errors(tmp_path: Path) -> None:
    lines = [*STATES, {"block": "broken:block", "error": "getSubItems: java.lang.NPE"}]
    data = ingest.read(write_run(tmp_path, lines, TEXTURES))
    assert len(data.states) == len(STATES)
    assert data.errors == (("broken:block", "getSubItems: java.lang.NPE"),)
    hull = next(s for s in data.states if s.item_meta == 1000)
    assert (hull.key, hull.has_te, hull.item) == (
        "gregtech:gt.blockmachines@0",
        True,
        "gregtech:gt.blockmachines",
    )
    assert next(s for s in data.states if s.block == "minecraft:sand").falls
    assert next(s for s in data.states if s.block == "gregtech:gt.blockores").hidden
    assert [t.rgb for t in data.textures] == ["#9d9d9d", None, "#7d7d7d"]
    assert data.textures[1].error is not None


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"id": 4096}, "numeric id key 'id'"),
        (
            {"item": {"item": "minecraft:stone", "meta": 0, "item_id": 1}},
            "numeric id key",
        ),
        ({"faces": ["stone"] * 5}, "faces is not a list of 6"),
        ({"has_te": "no"}, "has_te is not a boolean"),
        ({"meta": "0"}, "meta is not an integer"),
    ],
)
def test_refuses_lines_that_are_not_gregteclinks_shape(
    tmp_path: Path, change: dict[str, Any], why: str
) -> None:
    write_run(tmp_path, [state("minecraft:stone") | change], [])
    with pytest.raises(ingest.FormatError, match=why):
        ingest.read(tmp_path)


def test_refuses_a_state_listed_twice_and_a_bad_colour(tmp_path: Path) -> None:
    write_run(tmp_path, [state("minecraft:stone"), state("minecraft:stone")], [])
    with pytest.raises(ingest.FormatError, match="listed twice"):
        ingest.read(tmp_path)
    write_run(tmp_path, [], [{"icon": "stone", "path": "x", "rgb": "grey"}])
    with pytest.raises(ingest.FormatError, match="not #rrggbb"):
        ingest.read(tmp_path)


def test_status_is_absent_for_older_runs() -> None:
    assert ingest.status({"parts": {"palette": "ok"}}) == "ok"
    assert ingest.status({"parts": {"nei": "ok"}}) == "absent"
    assert ingest.status({}) == "absent"


# --- the tables ----------------------------------------------------------------------------


def test_catalog_tables_merge_variants_per_state(conn: sqlite3.Connection) -> None:
    assert block_db.catalog_run(conn) == (1, "run-a")
    assert block_db.counts(conn, 1) == {
        "states": 12,  # gt.blockmachines@0 has two variants, one state
        "blocks": 11,
        "tile_entity_states": 3,
        "mods": 4,
    }
    hull = block_db.state(conn, 1, "gregtech:gt.blockmachines", 0)
    assert hull is not None and hull.has_te
    assert block_db.metas(conn, 1, "gregtech:gt.blockcasings") == [1, 2]
    assert block_db.state(conn, 1, "gregtech:gt.blockcasings", 3) is None
    # a later run's catalog is the one GOS reads; deleting a run deletes its rows
    block_db.write(conn, 2, ingest.Palette((), (), ()))
    assert block_db.catalog_run(conn) == (1, "run-a")
    block_db.delete(conn, [1])
    assert block_db.catalog_run(conn) is None
    assert conn.execute("SELECT count(*) FROM block_texture").fetchone()[0] == 0


def test_find_filters_and_orders_by_key(conn: sqlite3.Connection) -> None:
    rows = block_db.find(conn, 1, "casing", plain=True)
    assert [r["block"] for r in rows] == [
        "GoodGenerator:MAR_Casing@0",
        "gregtech:gt.blockcasings@1",
        "gregtech:gt.blockcasings@2",
        "gregtech:gt.blockcasings5@0",  # the coils: "casing" is in the registry name
    ]
    assert rows[1]["side_rgb"] == "#9d9d9d"
    assert [r["block"] for r in block_db.find(conn, 1, "", light_min=1)] == [
        "minecraft:glowstone@0"
    ]
    plain = {r["block"] for r in block_db.find(conn, 1, "", plain=True, limit=100)}
    assert (
        "minecraft:chest@0" not in plain and "gregtech:gt.blockmachines@0" not in plain
    )
    every = {r["block"] for r in block_db.find(conn, 1, "", hidden=True, limit=100)}
    assert "gregtech:gt.blockores@0" in every
    assert "gregtech:gt.blockores@0" not in {
        r["block"] for r in block_db.find(conn, 1, "")
    }
    # LIKE wildcards in the words are literal
    assert block_db.find(conn, 1, "%") == []
    assert [r["block"] for r in block_db.find(conn, 1, "stone", mod="MINECRAFT")] == [
        "minecraft:glowstone@0",
        "minecraft:stone@0",
    ]
    assert [r["block"] for r in block_db.find(conn, 1, "stone glow")] == [
        "minecraft:glowstone@0"
    ]


def test_a_version_5_db_gets_the_block_tables(tmp_path: Path) -> None:
    path = tmp_path / "gos.sqlite"
    c = sqlite3.connect(path)
    try:
        c.executescript(FIXTURE_V5.read_text(encoding="utf-8"))
        c.execute("INSERT INTO item(registry_name, meta) VALUES ('minecraft:stone', 0)")
        c.commit()
        assert c.execute("PRAGMA user_version").fetchone() == (5,)
    finally:
        c.close()
    conn = access.connect(path)
    try:
        assert (
            conn.execute("PRAGMA user_version").fetchone()[0] == access.SCHEMA_VERSION
        )
        assert conn.execute("SELECT count(*) FROM block_state").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM item").fetchone()[0] == 1
    finally:
        conn.close()


# --- the world's registry ------------------------------------------------------------------


def test_world_registry_keeps_names_never_ids(world: worldreg.WorldRegistry) -> None:
    assert world.world == "lab-shells" and world.source == "level.dat"
    assert "gregtech:gt.blockcasings" in world.blocks
    assert world.items == frozenset({"minecraft:stick"})
    assert all(isinstance(b, str) for b in world.blocks)


def test_world_registry_reads_level_dat_old_during_a_save(tmp_path: Path) -> None:
    (tmp_path / "level.dat_old").write_bytes(level_dat(["minecraft:stone"]))
    reg = worldreg.read(tmp_path)
    assert reg.source == "level.dat_old" and reg.blocks == frozenset(
        {"minecraft:stone"}
    )


def test_world_registry_refusals(tmp_path: Path) -> None:
    with pytest.raises(worldreg.WorldRegistryError, match="no level.dat"):
        worldreg.read(tmp_path)
    (tmp_path / "level.dat").write_bytes(level_dat(["minecraft:stone"], fml=False))
    with pytest.raises(worldreg.WorldRegistryError, match="no FML data"):
        worldreg.read(tmp_path)
    (tmp_path / "level.dat").write_bytes(b"\x1f\x8b not gzip")
    with pytest.raises(worldreg.WorldRegistryError, match="can't read"):
        worldreg.read(tmp_path)
    with pytest.raises(
        worldreg.WorldRegistryError, match="without a block or item marker"
    ):
        worldreg.parse({"FML": {"ItemData": [{"K": "minecraft:stone", "V": 1}]}}, "w")
    with pytest.raises(worldreg.WorldRegistryError, match="lists no blocks"):
        worldreg.parse(
            {"FML": {"ItemData": [{"K": "\x02minecraft:stick", "V": 1}]}}, "w"
        )


# --- the palette rule ----------------------------------------------------------------------


def test_parse_palette_files() -> None:
    text = "# a shell\ngregtech:gt.blockcasings@2\tthe walls\n\nminecraft:stone@0 # floor\n"
    assert [e.key for e in palette.parse(text)] == [
        "gregtech:gt.blockcasings@2",
        "minecraft:stone@0",
    ]
    for bad in (
        "1:0",
        "4096@0",
        "minecraft:stone",
        "minecraft:stone@x",
        "minecraft:stone@40000",
    ):
        with pytest.raises(palette.PaletteError, match="registry name>@<meta>"):
            palette.parse_entry(bad)
    with pytest.raises(palette.PaletteError, match="line 2"):
        palette.parse("minecraft:stone@0\n42\n")


def verdicts(report: palette.Report) -> dict[str, tuple[str, ...]]:
    return {v.entry.key: v.refused for v in report.verdicts}


def test_the_palette_rule(
    conn: sqlite3.Connection, world: worldreg.WorldRegistry
) -> None:
    entries = palette.parse(
        "gregtech:gt.blockcasings@2\ngregtech:gt.blockcasings5@0\nbartworks:BW_GlasBlocks@0\nGoodGenerator:MAR_Casing@0\nminecraft:sand@0\ngregtech:gt.blockmachines@0\nminecraft:chest@0\nminecraft:air@0\ngregtech:gt.blockcasings@3\nnotamod:fancy_brick@0\nminecraft:wool@20\nminecraft:stone@0\nminecraft:stone@0"
    )
    report = palette.check(conn, entries, world)
    got = verdicts(report)
    assert report.run_key == "run-a" and report.world == "lab-shells"
    for ok in (
        "gregtech:gt.blockcasings@2",
        "gregtech:gt.blockcasings5@0",
        "bartworks:BW_GlasBlocks@0",
        "GoodGenerator:MAR_Casing@0",
        "minecraft:sand@0",
        "minecraft:stone@0",
    ):
        assert got[ok] == (), ok
    assert "never pastes machines" in got["gregtech:gt.blockmachines@0"][0]
    assert any("tile entity" in r for r in got["minecraft:chest@0"])
    assert any("air is the schematic's 0" in r for r in got["minecraft:air@0"])
    assert any(
        "meta 3 is not a placeable state" in r
        for r in got["gregtech:gt.blockcasings@3"]
    )
    assert any("worldedit-gtnh #28" in r for r in got["gregtech:gt.blockcasings@3"])
    unknown = got["notamod:fancy_brick@0"]
    assert any("level.dat FML ItemData" in r for r in unknown)
    assert any("not in the block catalog" in r for r in unknown)
    assert any("#30" in r for r in got["minecraft:wool@20"])
    warn = {v.entry.key: v.warnings for v in report.verdicts}
    assert warn["minecraft:sand@0"] == ("falls (gravity): needs support under it",)
    assert report.verdicts[-1].warnings == ("listed twice",)
    assert not report.ok
    assert [e.key for e in report.accepted][:4] == [
        "gregtech:gt.blockcasings@2",
        "gregtech:gt.blockcasings5@0",
        "bartworks:BW_GlasBlocks@0",
        "GoodGenerator:MAR_Casing@0",
    ]
    lines = palette.render(report)
    assert lines[0].startswith("Palette check against the world 'lab-shells'")
    assert lines[-1] == "7 of 13 entries accepted; the palette is refused"


def test_a_catalog_block_the_world_lacks_is_refused(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    w = tmp_path / "other"
    w.mkdir()
    (w / "level.dat").write_bytes(level_dat(["minecraft:stone"]))
    report = palette.check(
        conn,
        palette.parse("minecraft:stone@0\nbartworks:BW_GlasBlocks@0\n"),
        worldreg.read(w),
    )
    got = verdicts(report)
    assert got["minecraft:stone@0"] == ()
    assert got["bartworks:BW_GlasBlocks@0"] == (
        (
            "not in the world 'other''s registry (level.dat FML ItemData): that world has no such "
            "block"
        ),
    )


def test_whole_palette_problems(
    conn: sqlite3.Connection, world: worldreg.WorldRegistry
) -> None:
    assert palette.check(conn, [], world).problems == ("the palette is empty",)
    many = [palette.Entry("minecraft:stone", 0)] + [
        palette.Entry(f"minecraft:x{i}", 0) for i in range(palette.MAX_TYPES)
    ]
    report = palette.check(conn, many, world)
    assert report.problems[0].startswith("256 block types")
    good = palette.check(conn, [palette.Entry("minecraft:stone", 0)], world)
    assert good.ok


def test_no_catalog_is_an_error(tmp_path: Path, world: worldreg.WorldRegistry) -> None:
    c = access.connect(tmp_path / "empty.sqlite")
    try:
        with pytest.raises(palette.CatalogError, match="no block catalog"):
            palette.check(c, [palette.Entry("minecraft:stone", 0)], world)
    finally:
        c.close()


# --- gos palette ---------------------------------------------------------------------------


def _home(tmp_path: Path) -> tuple[Path, Path]:
    """GOS_HOME with the fixture catalog, a Lab instance with world lab-shells, gos.toml."""
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    c = access.connect(home / "data" / "gos.sqlite")
    try:
        c.execute(
            "INSERT INTO export_run(id, fingerprint, run_key) VALUES (1, '', 'run-a')"
        )
        block_db.write(c, 1, ingest.read(write_run(tmp_path / "run", STATES, TEXTURES)))
    finally:
        c.close()
    instances = tmp_path / "instances"
    game = instances / "GTNH-Lab" / ".minecraft"
    world = game / "saves" / "lab-shells"
    world.mkdir(parents=True)
    (instances / "GTNH-Lab" / "instance.cfg").write_text(
        "name=GTNH-Lab\n", encoding="utf-8"
    )
    (world / "level.dat").write_bytes(level_dat(WORLD_BLOCKS))
    toml = tmp_path / "gos.toml"
    toml.write_text(
        "[paths]\n"
        f"gos_home = {json.dumps(home.as_posix())}\n"
        f"prism_instances = {json.dumps(instances.as_posix())}\n"
        'lab_world = "lab-shells"\n',
        encoding="utf-8",
    )
    return toml, world


def run_cli(argv: list[str]) -> tuple[int, str]:
    import argparse
    import io

    parser = argparse.ArgumentParser()
    palette_cli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    code = palette_cli.run(parser.parse_args(argv), out)
    return code, out.getvalue()


def test_cli_check(tmp_path: Path) -> None:
    toml, world = _home(tmp_path)
    good = tmp_path / "good.txt"
    good.write_text("gregtech:gt.blockcasings@2\nminecraft:stone@0\n", encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("gregtech:gt.blockmachines@0\n", encoding="utf-8")
    code, text = run_cli(["palette", "check", str(good), "--config", str(toml)])
    assert code == 0, text
    assert "2 of 2 entries accepted" in text
    code, text = run_cli(
        ["palette", "check", str(bad), "--config", str(toml), "--world-dir", str(world)]
    )
    assert code == 1 and "REFUSED gregtech:gt.blockmachines@0" in text
    code, text = run_cli(
        ["palette", "check", str(good), "--config", str(toml), "--world", "missing"]
    )
    assert code == 2 and "Can't read the world's registry" in text
    junk = tmp_path / "junk.txt"
    junk.write_text("4096\n", encoding="utf-8")
    code, text = run_cli(["palette", "check", str(junk), "--config", str(toml)])
    assert code == 2 and "Can't read the palette" in text


def test_cli_blocks(tmp_path: Path) -> None:
    toml, _ = _home(tmp_path)
    code, text = run_cli(
        ["palette", "blocks", "glass", "--plain", "--config", str(toml)]
    )
    assert code == 0
    assert text.splitlines()[0] == "run: run-a"
    assert text.splitlines()[1].startswith(
        "bartworks:BW_GlasBlocks@0\tBorosilicate Glass Block"
    )


# --- the tool ------------------------------------------------------------------------------


def test_block_find_tool(conn: sqlite3.Connection, tmp_path: Path) -> None:
    reg = build_registry(tmp_path / "gos.sqlite")
    tool = reg.get("block_find")
    assert tool is not None and tool.read_only and tool.lanes == frozenset({DEEP})
    assert "block_find" not in {t.name for t in reg.tools(FAST)}
    out = reg.call("block_find", {"text": "casing"}, lane=DEEP)
    assert not out.is_error, out.text
    parsed = parse_tsv(out.text)
    assert parsed.head["run"] == "run-a"
    assert [r["block"] for r in parsed.rows] == [
        "GoodGenerator:MAR_Casing@0",
        "gregtech:gt.blockcasings@1",
        "gregtech:gt.blockcasings@2",
        "gregtech:gt.blockcasings5@0",  # the coils: "casing" is in the registry name
    ]
    out = reg.call("block_find", {"plain": False, "mod": "gregtech"}, lane=DEEP)
    rows = parse_tsv(out.text).rows
    te = {r["block"]: r["te"] for r in rows}
    assert (
        te["gregtech:gt.blockmachines@0"] == "1"
        and te["gregtech:gt.blockcasings@1"] == "0"
    )
    bad = reg.call("block_find", {"light_min": 16}, lane=DEEP)
    assert bad.is_error


def test_block_find_without_a_catalog(tmp_path: Path) -> None:
    access.connect(tmp_path / "gos.sqlite").close()
    out = build_registry(tmp_path / "gos.sqlite").call("block_find", {}, lane=DEEP)
    assert out.is_error and "no block catalog yet" in out.text
