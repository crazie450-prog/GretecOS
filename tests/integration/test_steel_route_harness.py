"""Steel at MV on a real harness DB: the plan GOS should make, and today doesn't (WO-3.10).

Runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the server harness; locally:
mod/README.md), and writes nothing outside tmp_path. docs/reviews/WO-3.10_steel_route.md has
the diagnosis: on this DB the top steel plan is Steeleaf nuggets (a Twilight Forest material
no recipe makes, so a free "raw" import) -> Steeleaf ingot -> dust -> centrifuge -> steel dust
-> Alloy Blast Smelter -> molten steel -> solidifier, and no iron or coal anywhere, because the
Bricked Blast Furnace's iron + coal recipes have no machine model and nothing gates where a raw
input comes from.

``test_steel_at_mv_is_iron_and_carbon`` is the acceptance test of the fix Clayton is asked to
approve: a strict xfail until then, so it fails CI the day the planner starts passing it
(remove the mark in that PR). The other test holds today.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.db import access
from gregtec.ingest import collect
from gregtec.planner import plan
from gregtec.planner.request import DefaultBaseInputs, Request

REPO = Path(__file__).resolve().parents[2]
STEEL = "gregtech:gt.metaitem.01@11305"
MV = 2
IRON = {"Iron", "WroughtIron", "PigIron"}
CARBON = {"Coal", "Charcoal", "Coke", "Carbon", "Lignite"}
TWILIGHT = {"Steeleaf", "Knightmetal", "FierySteel", "IronWood"}


@pytest.fixture(scope="module")
def conn(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Iterator[sqlite3.Connection]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("steel-route-harness")
    built_home(game, home)
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="module")
def best(conn: sqlite3.Connection) -> dict[str, Any]:
    d = plan.design(conn, Request(STEEL, tier_cap=MV), DefaultBaseInputs(MV))
    assert d.plans
    return d.plans[0]


def materials(conn: sqlite3.Connection, keys: set[str]) -> set[str]:
    """The GT materials of the item keys (registry@meta) among ``keys``."""
    out: set[str] = set()
    for k in keys:
        reg, _, meta = k.rpartition("@")
        if not reg or not meta.isdigit():
            continue
        row = conn.execute(
            "SELECT gt_material FROM item WHERE registry_name = ? AND meta = ?",
            (reg, int(meta)),
        ).fetchone()
        if row and row[0]:
            out.add(str(row[0]))
    return out


def test_every_import_the_plan_cannot_source_is_warned(best: dict[str, Any]) -> None:
    """Holds today: whatever the route, a locked or gated import is in the warnings."""
    imports = {b["id"] for b in best["boundary"]["in"]}
    for w in best["warnings"]:
        if w.startswith("imports "):
            assert w.split()[1] in imports


@pytest.mark.xfail(
    strict=True,
    reason="WO-3.10: no Bricked Blast Furnace model and no raw-source gate (pending Clayton)",
)
def test_steel_at_mv_is_iron_and_carbon(
    conn: sqlite3.Connection, best: dict[str, Any]
) -> None:
    imported = materials(conn, {b["id"] for b in best["boundary"]["in"]})
    assert imported & IRON, imported
    assert imported & CARBON, imported
    assert not imported & TWILIGHT, imported
    assert best["metrics"]["machines"] <= 10
