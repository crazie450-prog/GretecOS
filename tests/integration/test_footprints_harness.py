"""WO-10.2 acceptance on a real harness DB: footprints for every structure row, and the coverage
report on the benchmark plans (plan 8 row 10.2, D305, D306).

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after the server harness (CI's mod job; locally
mod/README.md). The server run is collected and built into a DB in tmp_path (``gos export
collect`` + ``gos db build``), then a structure dump is loaded into it with ``gos structures
load``'s own loader, which links each controller to the harness DB's real ``machine`` rows:

- the dump in GOS_STRUCTURES_DATASET when it is set (a real ``gos structures extract`` output,
  checked against the GT pin), else the repo's self-authored fixture
  (tests/fixtures/gtnh-extractor). The extractor's dump is pack data and never goes in the repo.

Then:

- **Footprints for every structure the DB holds**: one per structure row and facing; each one's
  bounding box equals the row's ``size_x/y/z`` (north) or its quarter turn, its blocks are the
  row's ``blocks_json`` (registry name + meta, never a numeric id) and its hatch markers the
  row's ``hatches_json``.
- **The coverage report on the benchmark plans**: G3's lines (``test_g3_lines_harness.LINES``)
  are designed on the harness DB, and the report says for each multi their best routes use
  whether it has a footprint or needs a Lab capture, consistently with the library. ``-s``
  prints it.

Nothing is written outside tmp_path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.build import coverage
from gregtec.build import footprints as fp
from gregtec.db import access, build
from gregtec.db import structures as structure_db
from gregtec.ingest import collect
from gregtec.planner import plan
from gregtec.planner.request import DefaultBaseInputs, Request

REPO = Path(__file__).resolve().parents[2]
GAME = REPO / "mod" / "run" / "server"
FIXTURE = REPO / "tests" / "fixtures" / "gtnh-extractor"
DATASET = os.environ.get("GOS_STRUCTURES_DATASET")


def _g3() -> ModuleType:
    """G3's benchmark lines, from test_g3_lines_harness.py (imported by path, as pytest does)."""
    name = "_wo102_g3_lines"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("test_g3_lines_harness.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the server harness")
    if not (GAME / collect.EXPORTS).is_dir():
        pytest.fail(
            f"no server harness run in {GAME / collect.EXPORTS} (mod/README.md)"
        )
    home = tmp_path_factory.mktemp("footprints")
    collect.collect(GAME, home, 1, allow_harness=True)
    built = build.build(home, allow_harness=True)
    conn = access.connect(built.db)
    try:
        report = structure_db.load(conn, Path(DATASET) if DATASET else FIXTURE)
    finally:
        conn.close()
    print("\n" + "\n".join(report.lines()))
    return built.db


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def test_a_footprint_for_every_structure_row(conn: sqlite3.Connection) -> None:
    lib = fp.library(conn)
    rows = conn.execute(
        "SELECT registry_name, meta, variant, size_x, size_y, size_z, blocks_json,"
        " hatches_json FROM structure WHERE registry_name IS NOT NULL"
        " ORDER BY registry_name, meta, variant"
    ).fetchall()
    assert rows
    assert lib.skipped == ()
    assert len(lib.footprints) == len(rows)
    for f, r in zip(lib.footprints, rows, strict=True):
        assert (f.registry_name, f.meta, f.variant) == (r[0], r[1], r[2])
        size = (r[3], r[4], r[5])
        assert f.size == size, f.key
        blocks = {tuple(b["d"]): (b["block"], b["meta"]) for b in json.loads(r[6])}
        assert {c.d: (c.block, c.meta) for c in f.blocks} == blocks, f.key
        hatches = {tuple(h["d"]): tuple(h["kinds"]) for h in json.loads(r[7])}
        assert {c.d: c.hatches for c in f.hatch_cells} == hatches, f.key
        for facing in fp.FACINGS:
            turned = f.rotated(facing)
            quarter = facing in ("east", "west")
            assert turned.size == ((size[2], size[1], size[0]) if quarter else size)
            assert turned.materials() == f.materials()
            assert len(turned.hatch_cells) == len(f.hatch_cells)
            assert turned.rotated(fp.BUILT_FACING) == f
    keys = [f.key for f in lib.every_facing()]
    assert len(keys) == len(set(keys)) == 4 * len(rows)
    # every block is a registry name, never a numeric id
    assert all(
        ":" in c.block and not c.block.split(":")[0].isdigit()
        for f in lib.footprints
        for c in f.blocks
        if c.block
    )
    by_source = {s: sum(f.source == s for f in lib.footprints) for s in fp.SOURCES}
    print(f"\n{len(lib.footprints)} footprints (x4 facings): {by_source}")


@pytest.fixture(scope="module")
def benchmark_plans(db: Path) -> list[dict[str, Any]]:
    conn = access.connect(db, readonly=True)
    try:
        base = DefaultBaseInputs.from_db(conn)
        out = []
        for name, (key, tier) in _g3().LINES.items():
            d = plan.design(conn, Request(key, qty=1, tier_cap=tier), base)
            assert d.plans, name
            p = dict(d.plans[0])
            p.setdefault("id", f"g3-{len(out)}")
            out.append(p)
        return out
    finally:
        conn.close()


def test_the_coverage_report_on_the_benchmark_plans(
    conn: sqlite3.Connection, benchmark_plans: list[dict[str, Any]]
) -> None:
    cov = coverage.coverage(conn, benchmark_plans, with_base=False)
    wanted = set()
    for p in benchmark_plans:
        wanted |= set(coverage.plan_multis(p))
    assert wanted, "the benchmark lines use no multiblock"
    assert {n.mte for n in cov.needed} == wanted
    lib = fp.library(conn)
    for n in cov.needed:
        has = bool(lib.for_mte(n.mte))
        assert (n.status == coverage.FOOTPRINT) == has, n
        assert n.status in (coverage.FOOTPRINT, coverage.NEEDS)
        assert n.needed_by and all(b.startswith("plan:") for b in n.needed_by)
    multis = conn.execute(
        "SELECT count(*) FROM machine WHERE is_multiblock = 1"
    ).fetchone()[0]
    assert len(cov.catalog) == multis
    if DATASET:  # a real dump covers the base pack's controllers the plans use
        missing = [n for n in cov.needed if n.status == coverage.NEEDS]
        print(f"\nbenchmark multis without an extractor footprint: {missing}")
    print("\n" + "\n".join(coverage.render(cov)))
