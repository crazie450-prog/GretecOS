"""The designer's outputs (WO-3.6) on a real harness DB: the dev runtime's recipes and machines.

As test_plan_harness.py, it runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the
server harness; locally: mod/README.md), and writes nothing outside tmp_path. §8 row 3.6:

- BOM sums equal the plan JSON (machines, average and peak EU/t) for three G3 lines;
- every plan's footprints come from ``structure`` (one block for a single block; a multiblock
  without a structure row is ``footprint: unknown``), and the layout sums them;
- saving writes the JSON, the five exports, the ``plan`` row and a vault note in the grammar;
- the gtnhplanner.com project names every node's recipe with its slots and links only to its
  own cards; the gtnh-flow YAML has one machine per sized node and one target.

The site import itself is dashboard/e2e/gtnhplanner-import.cjs (it needs the internet).
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import plan
from gregtec.planner.outputs import (
    FILES,
    bom,
    book,
    footprint,
    gtnhflow,
    gtnhplanner,
    store,
)
from gregtec.planner.request import DefaultBaseInputs, Request
from gregtec.vault import deploy as vault_deploy
from gregtec.vault import schema as vault_schema
from gregtec.vault.io import VaultIO

REPO = Path(__file__).resolve().parents[2]
LINES = {
    "gregtech:gt.metaitem.01@11305": 2,  # steel at MV
    "fluid:liquid_light_fuel": 2,  # light fuel at MV
    "gregtech:gt.metaitem.01@2085": 5,  # platinum dust at IV (the platline)
}


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    h = tmp_path_factory.mktemp("outputs-harness")
    collect.collect(game, h, 1, allow_harness=True)
    build.build(h, allow_harness=True)
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    return h


@pytest.fixture(scope="module")
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(home))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="module")
def plans(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, tier in LINES.items():
        req = Request(key, qty=1, tier_cap=tier)
        out += plan.design(conn, req, DefaultBaseInputs.from_db(conn)).plans
    return out


def test_bom_sums_equal_the_plan_json(plans: list[dict[str, Any]]) -> None:
    assert len(plans) >= 3
    for p in plans:
        assert bom.check(p) == [], p["id"]
        rows = list(csv.reader(io.StringIO(bom.tsv(p)), delimiter="\t"))
        assert len(rows) == len(p["nodes"]) + 2
        assert int(rows[-1][4]) == p["metrics"]["machines"]


def test_footprints_equal_structure(
    conn: sqlite3.Connection, plans: list[dict[str, Any]]
) -> None:
    for p in plans:
        lay = p["layout"]
        for m in lay["machines"]:
            if m["footprint"] == footprint.UNKNOWN:
                assert footprint.structure_size(conn, m["mte"]) is None
                continue
            found = footprint.structure_size(conn, m["mte"])
            if m["source"] == "structure":
                assert (
                    found is not None
                    and (m["size"]["x"], m["size"]["y"], m["size"]["z"]) == found[:3]
                )
            else:
                assert m["size"] == {"x": 1, "y": 1, "z": 1}
        known = [m for m in lay["machines"] if m["footprint"] != footprint.UNKNOWN]
        assert lay["area"] == sum(m["area"] * m["count"] for m in known)
        assert lay["volume"] == sum(m["volume"] * m["count"] for m in known)
        assert p["metrics"]["footprint"] == footprint.summary(lay)


def test_exports_are_well_formed(
    conn: sqlite3.Connection, plans: list[dict[str, Any]]
) -> None:
    for p in plans:
        b = book.read(conn, p)
        assert set(b.recipes) == {n["recipe"] for n in p["nodes"]}
        proj = gtnhplanner.project(p, b)
        ids = {r["id"] for r in proj["recipes"]}
        assert all(n["recipeId"] in ids for n in proj["nodes"])
        ends = {n["id"] for n in proj["nodes"]} | {s["id"] for s in proj["storages"]}
        assert all(e["source"] in ends and e["target"] in ends for e in proj["edges"])
        assert all(r["outputs"] for r in proj["recipes"])
        text = gtnhflow.text(p, b)
        sized = [n for n in p["nodes"] if n["machine"] and n["ticks_eff"]]
        assert text.count("\n- m: ") == len(sized)
        assert text.count("  target:") + text.count("  number:") == 1


def test_save_everything(
    home: Path, conn: sqlite3.Connection, plans: list[dict[str, Any]]
) -> None:
    vault = VaultIO(home / "vault")
    types = vault_schema.load_types(home / "vault" / "_gos" / "schema.md")
    for p in plans:
        saved = store.save(home, p, conn, book.read(conn, p), vault)
        assert saved.problems == [], saved.problems
        assert set(saved.files) == set(FILES)
        assert json.loads(saved.json_path.read_text(encoding="utf-8"))["id"] == p["id"]
        note = (home / "vault" / f"plans/{p['id']}.md").read_text(encoding="utf-8")
        assert vault_schema.check_text(f"plans/{p['id']}.md", note, types) == []
    rows = conn.execute(
        "SELECT count(*) FROM plan WHERE status = 'proposed'"
    ).fetchone()[0]
    assert rows == len({p["id"] for p in plans})
