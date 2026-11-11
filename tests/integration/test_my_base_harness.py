"""My base (WO-3.8) on a real harness DB: the dev runtime's machines and items.

As test_machines_harness.py, it runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the
server harness; locally: mod/README.md), and writes nothing outside tmp_path:

- the Electric Blast Furnace (mte:1000) and an item and a fluid of the real export are kept by
  their stable keys, and all of it, the tier override too, outlives a second ``gos db build``
  and a reopened connection (a restarted core);
- the planner's machine model follows My base: with ``require_owned`` a real EBF recipe gets
  the EBF only once it is ticked.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gregtec import store
from gregtec.base import state
from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import machines as m

REPO = Path(__file__).resolve().parents[2]
EBF = 1000
IRON = "minecraft:iron_ingot@0"
WATER = "fluid:water"
EBF_RECIPE = (
    "SELECT r.ref FROM recipe AS r JOIN recipe_map AS rm ON rm.id = r.recipe_map_id"
    " WHERE rm.unloc_name = 'gt.recipe.blastfurnace' AND r.enabled = 1"
    " AND coalesce(r.fake, 0) = 0 AND r.eut > 0 AND r.eut <= 120 ORDER BY r.ref LIMIT 1"
)


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    h = tmp_path_factory.mktemp("my-base-harness")
    collect.collect(game, h, 1, allow_harness=True)
    build.build(h, allow_harness=True)
    return h


def test_my_base_outlives_a_rebuild_on_real_data(home: Path) -> None:
    db = access.db_path(home)
    conn = store.connect(db)
    try:
        assert state.set_machine(conn, EBF, 2).name  # named from the real catalog
        assert state.set_stock(conn, IRON, 32).name
        assert state.set_stock(conn, WATER, 1000).kind == "fluid"
        state.set_tier(conn, "MV")
        before = state.snapshot(conn)
    finally:
        conn.close()
    build.build(home, allow_harness=True)
    conn = store.connect(db)
    try:
        assert state.snapshot(conn) == before
        base = state.MyBase.from_db(conn)
        assert base.tier_cap() == 2
        assert base.owned_machines() == {EBF: 2}
        assert base.stock() == {IRON: 32.0, WATER: 1000.0}
    finally:
        conn.close()


def test_the_machine_model_follows_my_base_on_real_data(home: Path) -> None:
    conn = store.connect(access.db_path(home))
    try:
        row = conn.execute(EBF_RECIPE).fetchone()
        assert row is not None, "the harness run has no EBF recipe at MV or below"

        def options() -> set[int]:
            player = m.Player(tier=2, owned=m.owned_machines(conn), require_owned=True)
            return {o.mte_id for o in m.select(conn, row[0], player).options}

        state.set_machine(conn, EBF, 0)
        assert EBF not in options()
        state.set_machine(conn, EBF, 1)
        assert EBF in options()
    finally:
        conn.close()
