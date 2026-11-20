"""The shell, layout, footprint and guide paths on a REAL ``gos structures extract`` dump.

Defect (QA, a real dump of 297 controllers and 455 variants): every ``build_request`` failed with
"database error: string or blob too big". The layout read every structure row, and Forge of the
Gods' t3 row holds 1,239,746 bytes of ``blocks_json``, over the tools' 1 MB value limit
(``registry.MAX_VALUE_BYTES``). CI and the G10 review had only stand-in structures. This file
runs on the real extractor output:

- GOS_REAL_STRUCTURES (CI's mod job extracts one after the harnesses; mod/README.md), else
  GOS_STRUCTURES_DATASET (a dump of your own, as the other harness tests take it). Without
  either it skips, unless GOS_REQUIRE_REAL_DUMP=1 (CI), when it fails.
- The client harness run (its export has the block catalog a shell's palette needs) is built
  into a home (the shared harness cache), the dump is loaded with ``gos structures load``'s own
  loader, and G3's steel line (at MV) is designed and its best route with a multiblock saved.
  Then: ``build_request`` through the registry, as the deep lane calls it (a preview and a
  saved shell); the layout on a connection with the tools' own guard and value limit, every
  multi placed; the footprint library, whole and for the line's multis only; the 3D guide API
  for the line's multis and for the dump's largest structure.

Nothing is written outside tmp_path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.agents.tools import build_registry, registry
from gregtec.agents.tools.registry import DEEP
from gregtec.build import footprints as fp
from gregtec.build import layout
from gregtec.db import access
from gregtec.db import structures as structure_db
from gregtec.ingest import collect
from gregtec.planner import plan
from gregtec.planner.request import DefaultBaseInputs, Request
from gregtec.vault.io import VaultIO

REPO = Path(__file__).resolve().parents[2]
GAME = REPO / "mod" / "run" / "client"  # its export has the block catalog shells need
TEMPLATE = REPO / "vault-template"
DUMP = os.environ.get("GOS_REAL_STRUCTURES") or os.environ.get("GOS_STRUCTURES_DATASET")
PLAN = "pl-real-steel"
ADMIN = "admin-" + "r" * 40


def _g3() -> ModuleType:
    name = "_realdump_g3_lines"
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
def home(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    if not DUMP:
        if os.environ.get("GOS_REQUIRE_REAL_DUMP") == "1":
            pytest.fail("GOS_REQUIRE_REAL_DUMP=1 but GOS_REAL_STRUCTURES is not set")
        pytest.skip("set GOS_REAL_STRUCTURES to a `gos structures extract` output")
    if not (GAME / collect.EXPORTS).is_dir():
        pytest.fail(
            f"no client harness run in {GAME / collect.EXPORTS} (mod/README.md)"
        )
    home = tmp_path_factory.mktemp("real-dump")
    built_home(GAME, home)
    shutil.copytree(TEMPLATE, home / "vault")
    conn = access.connect(access.db_path(home))
    try:
        report = structure_db.load(conn, Path(DUMP))
        print("\n" + "\n".join(report.lines()))
        key, tier = _g3().LINES["steel (EBF)"]
        d = plan.design(
            conn, Request(key, qty=1, tier_cap=tier), DefaultBaseInputs.from_db(conn)
        )
        assert d.plans, "the steel line didn't design"
    finally:
        conn.close()
    # the best route with a multiblock (on the client harness DB the best one is single blocks
    # and steps by hand): its footprints are what this file is about
    best = next((p for p in d.plans if _multis(dict(p))), None)
    assert best is not None, [p.get("name") for p in d.plans]
    steel = {**best, "id": PLAN}
    (home / "data" / "plans").mkdir(parents=True, exist_ok=True)
    (home / "data" / "plans" / f"{PLAN}.json").write_text(
        json.dumps(steel), encoding="utf-8"
    )
    return home


@pytest.fixture
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


def _steel(home: Path) -> dict[str, Any]:
    got: dict[str, Any] = json.loads(
        (home / "data" / "plans" / f"{PLAN}.json").read_text("utf-8")
    )
    return got


def _multis(p: dict[str, Any]) -> set[int]:
    return {
        int(n["machine"]["mte"])
        for n in p["nodes"]
        if isinstance(n.get("machine"), dict)
        and n["machine"].get("kind") == "multiblock"
    }


def test_the_dump_is_real_and_holds_a_row_over_the_tools_limit(
    conn: sqlite3.Connection,
) -> None:
    controllers, biggest = conn.execute(
        "SELECT count(DISTINCT registry_name || '@' || meta), max(octet_length(blocks_json))"
        " FROM structure WHERE source = 'gtnh-extractor'"
    ).fetchone()
    assert controllers >= 250  # a stand-in fixture has a handful
    assert biggest > registry.MAX_VALUE_BYTES  # the row that broke every build_request


def test_build_request_designs_a_shell_on_the_real_dump(home: Path) -> None:
    reg = build_registry(access.db_path(home), vault=VaultIO(home / "vault"))
    args = {"plans": PLAN, "theme": "th-foundry"}
    look = reg.call("build_request", {**args, "save": False}, lane=DEEP)
    assert not look.is_error, look.text
    assert "status: accepted" in look.text
    made = reg.call("build_request", args, lane=DEEP)
    assert not made.is_error, made.text
    assert "status: accepted" in made.text and "saved: data/build/shells/" in made.text


def test_the_layout_places_every_multi_under_the_tools_guard(home: Path) -> None:
    steel = _steel(home)
    c = access.connect(access.db_path(home), readonly=True)
    try:
        registry.guard(c, 60.0)  # the 1 MB value limit every tool but build_request has
        lay = layout.plan_layout(c, [steel])
    finally:
        c.close()
    assert lay["unplaced"] == []
    assert lay["placements"] and all(p["reach"]["ok"] for p in lay["placements"])
    placed = {p["mte"] for p in lay["placements"]}
    assert _multis(steel) <= placed


def test_footprints_whole_and_for_the_lines_multis(
    home: Path, conn: sqlite3.Connection
) -> None:
    lib = fp.library(conn)  # the CLI's and the dashboard's read: every row
    rows = conn.execute(
        "SELECT count(*) FROM structure WHERE registry_name IS NOT NULL"
    ).fetchone()[0]
    assert lib.skipped == () and len(lib.footprints) == rows
    multis = _multis(_steel(home))
    assert multis
    c = access.connect(access.db_path(home), readonly=True)
    try:
        registry.guard(c, 30.0)
        some = fp.library(c, multis)
    finally:
        c.close()
    assert {f.meta for f in some.footprints} == multis
    assert some.footprints == tuple(f for f in lib.footprints if f.meta in multis)


def test_the_guide_api_on_the_lines_multis_and_the_largest_structure(
    home: Path, conn: sqlite3.Connection
) -> None:
    from fastapi.testclient import TestClient

    from gregtec.api.app import Core, create_app
    from gregtec.api.security import Auth
    from gregtec.config import Config
    from gregtec.llm.usage import UsageMeter
    from gregtec.store import Store

    biggest = conn.execute(
        "SELECT registry_name, meta FROM structure ORDER BY octet_length(blocks_json) DESC"
        " LIMIT 1"
    ).fetchone()
    wanted = [f"{fp.GT_MACHINES}@{m}" for m in sorted(_multis(_steel(home)))]
    wanted.append(f"{biggest[0]}@{biggest[1]}")
    store = Store(access.db_path(home))
    core = Core(
        cfg=Config(gos_home=home),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            c.headers["Authorization"] = f"Bearer {ADMIN}"
            listed = c.get("/api/build/guides")
            assert listed.status_code == 200, listed.text
            assert len(listed.json()["guides"]) >= 250
            for controller in wanted:
                got = c.get("/api/build/guide", params={"controller": controller})
                assert got.status_code == 200, (controller, got.text)
                g = got.json()
                assert g["cells"] and g["palette"], controller
                for options in g["choices"].values():
                    assert all("name" in o for o in options)
    finally:
        store.close()
