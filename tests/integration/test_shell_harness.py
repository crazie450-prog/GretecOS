"""WO-10.7 on real harness data: the procedural shell around the two benchmark layouts.

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after the server and client harnesses (CI's mod job;
locally mod/README.md). The benchmark lines and their layouts are WO-10.5's
(``test_layout_harness``: G3's steel line and G4's benzene line on the server run's DB, each
multi's footprint from the structure dump or the fixture's stand-in); the block catalog is the
client run's (WO-10.3's palette pass), so both starter themes' palettes are checked against real
registry names, metas and hasTileEntity flags.

For each layout (each line alone and both together) and each starter theme: the procedural shell
is accepted as designed (no repair), and this file's own geometry finds no cell in a keep-out,
corridor, chase or doorway, none outside the bounding volume, none with a tile entity or GT's
machine block, at most max_blocks; the shell's ``.schematic`` passes the palette rule against
the client run's world registry (from the catalog's names) and reads back cell for cell. Then
a design with GT's machine block and one with a chest from the real catalog are refused.

The two DB builds add about a minute to the mod job; the shells themselves take seconds.
Nothing is written outside tmp_path.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.build import layout, palette, schematic, shell
from gregtec.build import themes as theme_lib
from gregtec.build.worldreg import WorldRegistry
from gregtec.db import access, build
from gregtec.db import blocks as block_db
from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "mod" / "run" / "client"
HERE = Path(__file__).parent


def _module(name: str, path: Path) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


laid = _module("_wo107_layout_harness", HERE / "test_layout_harness.py")
units = _module("_wo107_shell_units", HERE.parent / "unit" / "test_shell.py")

# WO-10.5's module fixtures: the server run's DB, the benchmark lines, the structure stand-ins.
db = laid.db
lines = laid.lines
conn = laid.conn


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    """The client run's DB: its block catalog (WO-10.3's palette pass)."""
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if not (CLIENT / collect.EXPORTS).is_dir():
        pytest.fail(
            f"no client harness run in {CLIENT / collect.EXPORTS} (mod/README.md)"
        )
    home = tmp_path_factory.mktemp("shell-catalog")
    collect.collect(CLIENT, home, 1, allow_harness=True)
    built = build.build(home, allow_harness=True)
    assert built.loaded.palette_status == "ok", built.loaded.palette_error
    c = access.connect(built.db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def _themes() -> list[theme_lib.Theme]:
    return [units.foundry(), units.marble()]


def _world(cat: sqlite3.Connection) -> WorldRegistry:
    """The client run's world as GOS sees it: every block name in its catalog run."""
    found = block_db.catalog_run(cat)
    assert found is not None
    names = {
        r[0] for r in cat.execute("SELECT DISTINCT registry_name FROM block_state")
    }
    return WorldRegistry("harness", frozenset(names), frozenset(), "catalog")


def _tes(cat: sqlite3.Connection) -> set[palette.Entry]:
    rows = cat.execute(
        "SELECT registry_name, meta FROM block_state WHERE has_te = 1"
    ).fetchall()
    return {palette.Entry(r[0], r[1]) for r in rows}


def test_the_procedural_shell_on_both_benchmark_layouts(
    conn: sqlite3.Connection,
    lines: list[dict[str, Any]],
    catalog: sqlite3.Connection,
) -> None:
    world = _world(catalog)
    tes = _tes(catalog)
    assert tes, "the catalog flags no tile entity"
    for chosen in ([lines[0]], [lines[1]], lines):
        lay = layout.plan_layout(conn, chosen)
        assert lay["placements"] and lay["unplaced"] == []
        for t in _themes():
            sw = shell.swatches_of(t)
            shell.check_palette(catalog, sw)
            spec = shell.spec_from_layout(
                lay, sw, theme=t, line_type="smelting", max_blocks=shell.MAX_BLOCKS
            )
            out = shell.design(catalog, spec, shell.procedural(spec), "procedural")
            assert out.status == "accepted", (out.reasons, out.repairs)
            units.rules_hold(
                lay, out.cells, spec.allowed(), spec.max_blocks, spec.headroom
            )
            assert not set(out.cells.values()) & tes
            s = shell.to_schematic(spec, out.cells)
            written = schematic.write_shell(catalog, world, s)
            for part in written.parts:
                back = schematic.read(part.data).schematic
                assert schematic.diff(part.schematic, back) == []
            sx, sy, sz = spec.bounds.size
            print(
                f"\n{t.id} around {' + '.join(p['id'] for p in chosen)}: {len(out.cells)} "
                f"blocks, {sx} x {sz} x {sy}, {len(written.parts)} file(s)"
            )


def test_real_tile_entity_blocks_are_refused(
    conn: sqlite3.Connection,
    lines: list[dict[str, Any]],
    catalog: sqlite3.Connection,
) -> None:
    lay = layout.plan_layout(conn, [lines[0]])
    t = units.foundry()
    sw = shell.swatches_of(t)
    spec = shell.spec_from_layout(lay, sw, theme=t)
    good = shell.procedural(spec)
    machine = catalog.execute(
        "SELECT registry_name, meta FROM block_state WHERE registry_name = ? LIMIT 1",
        (palette.GT_MACHINES,),
    ).fetchone()
    chest = catalog.execute(
        "SELECT registry_name, meta FROM block_state WHERE registry_name = 'minecraft:chest' LIMIT 1"
    ).fetchone()
    for row, why in (
        (machine, "GOS never pastes machines"),
        (chest, "has a tile entity"),
    ):
        if row is None:
            continue
        bad = {
            "op": "fill",
            "block": f"{row[0]}@{row[1]}",
            "lo": [-1, 1, -1],
            "hi": [-1, 1, -1],
        }
        out = shell.design(catalog, spec, [*good, bad], "architect")
        assert out.status == "refused" and why in out.reasons[0], out.reasons
