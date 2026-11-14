"""The block catalog and the palette rule (WO-10.3, D307, D308) on a real client harness run.

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after the client harness (CI's mod job; locally
mod/README.md). The client run is collected and built into a DB in tmp_path, then:

- **The catalog includes addon blocks; TE blocks are flagged** (plan 8 row 10.3): states of GT,
  GT addons (bartworks, GoodGenerator) and other mods (vanilla, AE2, IC2) are in
  ``block_state``; every ``gregtech:gt.blockmachines`` variant and other tile-entity blocks have
  ``has_te``; GT's casings, coils and glass and bartworks' glass don't.
- **The level.dat check on the harness world.** The client harness's own world
  (``saves/gregteclink-autoexport``, written by the same game with EndlessIDs) gives the
  registry: every catalog block is in it; a shell palette of casings, a coil and glass passes;
  GT's machine block, a chest and a block no mod registered are refused.
- ``gos db build``'s report counts the catalog, and ``block_find`` lists it.

Nothing is written outside tmp_path; the harness world is only read.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.agents.tools import build_registry
from gregtec.agents.tools.registry import DEEP
from gregtec.agents.tools.tsv import parse as parse_tsv
from gregtec.build import palette, worldreg
from gregtec.db import access, build, report
from gregtec.db import blocks as block_db
from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]
GAME = REPO / "mod" / "run" / "client"
WORLD = GAME / "saves" / "gregteclink-autoexport"
TE = (
    "gregtech:gt.blockmachines",
    "minecraft:chest",
    "appliedenergistics2:tile.BlockDrive",
)
PLAIN = (
    "gregtech:gt.blockcasings",
    "gregtech:gt.blockcasings5",  # coils
    "gregtech:gt.blockglass1",
    "bartworks:BW_GlasBlocks",
    "GoodGenerator:MAR_Casing",
    "appliedenergistics2:tile.BlockSkyStone",
)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.Result:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if not (GAME / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {GAME / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("palette")
    collect.collect(GAME, home, 1, allow_harness=True)
    return build.build(home, allow_harness=True)


@pytest.fixture(scope="module")
def conn(built: build.Result) -> Iterator[sqlite3.Connection]:
    c = access.connect(built.db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def test_the_catalog_has_addon_blocks_and_flags_tile_entities(
    built: build.Result, conn: sqlite3.Connection
) -> None:
    assert built.loaded.palette_status == "ok", built.loaded.palette_error
    run = block_db.catalog_run(conn)
    assert run is not None and run[0] == built.run_id
    mods = {r[0] for r in conn.execute("SELECT DISTINCT mod_id FROM block_state")}
    assert {
        "gregtech",
        "bartworks",
        "GoodGenerator",
        "minecraft",
        "appliedenergistics2",
    } <= mods
    for block in TE:
        rows = conn.execute(
            "SELECT count(*), sum(has_te) FROM block_state WHERE registry_name = ?",
            (block,),
        ).fetchone()
        assert rows[0] > 0 and rows[0] == rows[1], block
    for block in PLAIN:
        rows = conn.execute(
            "SELECT count(*), sum(has_te) FROM block_state WHERE registry_name = ?",
            (block,),
        ).fetchone()
        assert rows[0] > 0 and rows[1] == 0, block
    # GT's machine block: one state (meta 0), one variant per machine item
    machines = conn.execute(
        "SELECT count(DISTINCT meta), count(*) FROM block_state"
        " WHERE registry_name = 'gregtech:gt.blockmachines'"
    ).fetchone()
    assert machines[0] == 1 and machines[1] > 1000
    # no row is keyed on a numeric id: every key is registry name + meta
    assert conn.execute(
        "SELECT count(*) FROM block_state WHERE registry_name NOT LIKE '%:%'"
    ).fetchone() == (0,)


def test_the_report_counts_the_catalog(built: build.Result) -> None:
    lines = report.palette_lines(built)
    assert lines[0].startswith("Block catalog: "), lines
    assert "with a tile entity (never in a shell)" in lines[0]


def test_every_catalog_block_is_in_the_harness_worlds_registry(
    conn: sqlite3.Connection,
) -> None:
    registry = worldreg.read(WORLD)
    assert registry.source == "level.dat"
    names = {
        r[0] for r in conn.execute("SELECT DISTINCT registry_name FROM block_state")
    }
    assert names <= registry.blocks
    assert "gregtech:gt.blockcasings" in registry.blocks


def test_the_palette_rule_on_the_harness_world(conn: sqlite3.Connection) -> None:
    registry = worldreg.read(WORLD)
    shell = palette.parse(
        "gregtech:gt.blockcasings@2\n"
        "gregtech:gt.blockcasings5@0\n"
        "gregtech:gt.blockglass1@0\n"
        "bartworks:BW_GlasBlocks@0\n"
        "appliedenergistics2:tile.BlockSkyStone@0\n"
        "minecraft:stone@0\n"
    )
    good = palette.check(conn, shell, registry)
    assert good.ok, palette.render(good)
    bad = palette.check(
        conn,
        palette.parse(
            "gregtech:gt.blockmachines@0\nminecraft:chest@0\nnotamod:fancy_brick@0\n"
        ),
        registry,
    )
    refused = {v.entry.key: v.refused for v in bad.verdicts}
    assert all(refused.values()), refused
    assert any("level.dat FML ItemData" in r for r in refused["notamod:fancy_brick@0"])
    assert any("tile entity" in r for r in refused["minecraft:chest@0"])


def test_block_find_on_the_harness_db(built: build.Result) -> None:
    reg = build_registry(built.db)
    out = reg.call("block_find", {"text": "glass", "mod": "bartworks"}, lane=DEEP)
    assert not out.is_error, out.text
    rows = parse_tsv(out.text).rows
    assert rows and all(r["te"] == "0" for r in rows)
    assert any(r["block"].startswith("bartworks:BW_GlasBlocks@") for r in rows)
