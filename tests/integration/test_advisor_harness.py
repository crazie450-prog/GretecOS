"""The advisor and its buildable-from-stock notice (WO-6.5) over a real harness recipe DB.

The quest pack (tests/evals/quest_pack.py: a self-authored book) is loaded into the DB that
``gos export collect`` + ``gos db build`` make from the server harness's newest run (GT 5.09.54.265),
and an accepted plan is built from the real export's machines: the Basic Macerator (a single
block, found by its English name, never a numeric id assumed) and the Electric Blast Furnace (a
multiblock, by its name). It checks that "buildable from stock" counts the real machine items
by registry name + meta, that a multiblock is buildable only when every block of its smallest
real structure variant is in stock (or never, when the DB has no structure for it), and that the
advisor puts the buildable plan first and the notice fires.

Runs only with GOS_REQUIRE_HARNESS_RUN=1 after ``cd mod && ./gradlew runServer -Pharness``
(mod/README.md); ``GOS_SERVER_RUN`` names another server run folder. Nothing is written outside
tmp_path.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from gregtec import store as core_store
from gregtec.advisor import checks, notices
from gregtec.agents.tools.catalog import default_registry
from gregtec.agents.tools.tsv import parse
from gregtec.db import build
from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]


def _scenarios() -> ModuleType:
    name = "advisor_scenarios"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO / "tests" / "evals" / "advisor_scenarios.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


S = _scenarios()


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the server harness")
    game = Path(os.environ.get("GOS_SERVER_RUN") or REPO / "mod" / "run" / "server")
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")

    def export(h: Path) -> None:
        collect.collect(game, h, 1, allow_harness=True)
        build.build(h, allow_harness=True)

    h = tmp_path_factory.mktemp("advisor") / "home"
    S.pack().build(h, export=export)
    core_store.connect(h / "data" / "gos.sqlite").close()
    yield h


def mte(db: Path, name: str) -> tuple[int, bool]:
    """The GT machine named ``name`` in the export (its English name, the lowest mte id)."""
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT mte_id, is_multiblock FROM machine WHERE name_en = ?"
            " AND mod_id = 'gregtech' ORDER BY mte_id LIMIT 1",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"{name} isn't in the harness export"
    return int(row[0]), bool(row[1])


def node(mte_id: int, kind: str, count: int) -> dict[str, object]:
    return {
        "n": f"n{mte_id}",
        "machine": {"mte": mte_id, "kind": kind, "tier": "LV"},
        "count": count,
    }


def test_a_plan_of_real_machines_is_buildable_from_their_items(home: Path) -> None:
    db = home / "data" / "gos.sqlite"
    mac, multi = mte(db, "Basic Macerator")
    assert not multi
    plan = S.plan_json("pl-real-mac", "Real macerators", nodes=[node(mac, "single", 3)])
    S.save_plan(home, plan, "accepted")
    conn = core_store.connect(db)
    try:
        total, skipped = checks.needs(conn, plan) or ({}, -1)
        assert skipped == 0 and list(total.values()) == [3]
        (key,) = total
        assert key.startswith("gregtech:gt.blockmachines@")  # registry name + meta
        assert checks.buildable_plans(conn, home) == []
        S.stock(db, key, 3, time.time())
        assert [f.key for f in checks.buildable_plans(conn, home)] == [
            "plan:pl-real-mac"
        ]
    finally:
        conn.close()
    text = default_registry(home).call("advisor_next", {}, lane="deep").text
    first = parse(text).rows[0]
    assert (first["kind"], first["refs"]) == ("build", "plan:pl-real-mac")
    n = notices.Notices(db, enabled=("plan_buildable",))
    try:
        assert [x.kind for x in n.tick()] == ["plan_buildable"]
    finally:
        n.close()


def test_a_real_multiblock_needs_its_whole_structure(home: Path) -> None:
    db = home / "data" / "gos.sqlite"
    ebf, multi = mte(db, "Electric Blast Furnace")
    assert multi
    plan = S.plan_json("pl-real-ebf", "Real EBF", nodes=[node(ebf, "multiblock", 1)])
    S.save_plan(home, plan, "accepted")
    conn = core_store.connect(db)
    try:
        total, skipped = checks.needs(conn, plan) or ({}, -1)
        for key, qty in total.items():
            S.stock(db, key, qty, time.time())
        found = {f.key for f in checks.buildable_plans(conn, home)}
        if skipped:  # no structure data for it in this DB: never called buildable
            assert "plan:pl-real-ebf" not in found
            return
        assert len(total) > 1  # the controller and its casings and coils
        assert "plan:pl-real-ebf" in found
        short = min(k for k, q in total.items() if q > 1)
        S.stock(db, short, total[short] - 1, time.time())
        assert "plan:pl-real-ebf" not in {
            f.key for f in checks.buildable_plans(conn, home)
        }
    finally:
        conn.close()
