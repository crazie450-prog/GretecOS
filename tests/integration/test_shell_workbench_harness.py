"""WO-10.10 on real harness data: revise a shell through ``build_revise`` on a DB holding the real
block catalog and the extractor's structure rows.

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after the server and client harnesses (CI's mod job;
locally mod/README.md). WO-10.7's harness test (``test_shell_harness``) gives the benchmark
lines' layout (the server run's DB, the structures from GOS_STRUCTURES_DATASET when it is set, a
real ``gos structures extract`` dump, else the repo's fixture with stand-ins) and the client run's
block catalog. Here the client run is built into a GOS_HOME of its own and the same structure dump
is loaded into that DB. Only with GOS_STRUCTURES_DATASET set (a real dump, Clayton's PC step)
are those rows as large as the real ones (over ``MAX_VALUE_BYTES``, the tools' read guard's
limit, which is then asserted); CI's mod job loads the fixture's small stand-ins, so there this
test proves revise on the real block catalog, not on a 1.2 MB row. What proves that a revision
reads no structure row at all (only the version's own files and the catalog, D367) is the unit
test ``test_revise_reads_no_structure_row`` (a sqlite authorizer denies every read of
``structure``).

On that home: the steel line's procedural foundry shell is saved and started as a design, then
``build_revise`` (deep lane, the architect's tool) revises it from words (GOS's reviser) and from
operations (the architect's path), each a new version through the gate; undo makes v1
current; the 3D viewer's data and compare read the real catalog's names. A design with a chest
from the real catalog is refused and writes nothing (skipped, not passed, when the catalog has no
chest). Nothing is written outside tmp_path.
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

from gregtec.agents.tools import build_registry
from gregtec.agents.tools.registry import DEEP, MAX_VALUE_BYTES
from gregtec.build import layout, shell, shell_store
from gregtec.build import shell_versions as versions
from gregtec.build import themes as theme_lib
from gregtec.db import access, build
from gregtec.db import structures as structure_db
from gregtec.ingest import collect
from gregtec.vault.io import VaultIO

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "mod" / "run" / "client"
HERE = Path(__file__).parent
TEMPLATE = REPO / "vault-template"


def _module(name: str, path: Path) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


harness = _module("_wo107_shell_harness", HERE / "test_shell_harness.py")
laid = harness.laid
units = harness.units

db = harness.db
lines = harness.lines
conn = harness.conn


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The client run built into a GOS_HOME, with the structure dump loaded into its DB."""
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if not (CLIENT / collect.EXPORTS).is_dir():
        pytest.fail(
            f"no client harness run in {CLIENT / collect.EXPORTS} (mod/README.md)"
        )
    h = tmp_path_factory.mktemp("workbench") / "home"
    collect.collect(CLIENT, h, 1, allow_harness=True)
    built = build.build(h, allow_harness=True)
    assert built.loaded.palette_status == "ok", built.loaded.palette_error
    c = access.connect(built.db)
    try:
        structure_db.load(c, Path(laid.DATASET) if laid.DATASET else laid.FIXTURE)
        biggest = c.execute(
            "SELECT max(length(blocks_json)) FROM structure"
        ).fetchone()[0]
    finally:
        c.close()
    print(f"\nthe largest structure row: {biggest} bytes")
    if laid.DATASET:  # a real dump: its rows are what the read guard refuses
        assert biggest > MAX_VALUE_BYTES, biggest
    import shutil

    shutil.copytree(TEMPLATE, h / "vault")
    return h


@pytest.fixture
def started(
    conn: sqlite3.Connection, lines: list[dict[str, Any]], home: Path
) -> Iterator[tuple[str, str]]:
    """The steel line's procedural foundry shell, saved in ``home`` and started as a design."""
    lay = layout.plan_layout(conn, [lines[0]])
    cat = access.connect(access.db_path(home), readonly=True)
    try:
        t = units.foundry()
        sw = shell.swatches_of(t)
        shell.check_palette(cat, sw)
        spec = shell.spec_from_layout(lay, sw, theme=t, line_type="smelting")
        ops = shell.procedural(spec)
        out = shell.design(cat, spec, ops, "procedural")
        assert out.status == "accepted", out.reasons
        brief = theme_lib.Brief(
            theme_lib.THEME_MODE, "smelting", "", t, "", None, "chosen"
        )
        b = shell.build_id(spec, ops)
        made = shell_store.Made(spec, tuple(ops), out, brief, b, None)
        if not (shell_store.folder(home, b) / "design.json").is_file():
            shell_store.save(home, made, lay)
    finally:
        cat.close()
    d = versions.start(
        home, b, asked="a foundry shell around the steel line", by="procedural"
    )
    yield d["id"], b
    versions.make_current(home, d["id"], b, "the next test starts from v1")


def test_build_revise_on_real_structure_rows_and_the_real_catalog(
    home: Path, started: tuple[str, str]
) -> None:
    ds, v1 = started
    reg = build_registry(access.db_path(home), vault=VaultIO(home / "vault"))
    before = {p.name for p in (home / shell_store.ROOT).iterdir()}
    # GOS's reviser, through the tool and its read guard
    out = reg.call(
        "build_revise", {"words": "make the roof steeper, no lights"}, lane=DEEP
    )
    assert not out.is_error, out.text
    d = versions.get(home, ds)
    v2 = d["current"]
    assert v2 != v1 and d["versions"][-1]["parent"] == v1
    # the architect's path: the current ops with the windows gone
    rec = shell_store.load(home, v2)
    ops = [o for o in rec["ops"] if o["op"] != "windows"]
    out = reg.call(
        "build_revise",
        {"words": "no windows at all", "design_ops": json.dumps(ops)},
        lane=DEEP,
    )
    assert not out.is_error, out.text
    v3 = versions.get(home, ds)["current"]
    assert versions.get(home, ds)["versions"][-1]["by"] == "architect"
    after = {p.name for p in (home / shell_store.ROOT).iterdir()}
    assert after - before == {v2, v3}
    # undo, compare and the viewer, on the real catalog's names
    _, cur = versions.undo(home, ds)
    assert cur["build"] == v2
    cmp = versions.compare(home, v1, v3)
    assert cmp["materials"] and all("@" in m["block"] for m in cmp["materials"])
    c = access.connect(access.db_path(home), readonly=True)
    try:
        view = versions.viewer(c, home, v3)
    finally:
        c.close()
    assert len(view["cells"]) == shell_store.load(home, v3)["blocks"]
    assert any(p["name"] for p in view["palette"]), "the real catalog names the blocks"
    print(f"\nrevised {v1} -> {v2} -> {v3}: {len(view['cells'])} blocks")


def test_a_chest_from_the_real_catalog_is_refused_and_writes_nothing(
    home: Path, started: tuple[str, str]
) -> None:
    ds, v1 = started
    cat = access.connect(access.db_path(home), readonly=True)
    try:
        chest = cat.execute(
            "SELECT registry_name, meta FROM block_state WHERE registry_name = 'minecraft:chest'"
            " LIMIT 1"
        ).fetchone()
    finally:
        cat.close()
    if chest is None:
        pytest.skip("the client run's catalog has no minecraft:chest")
    reg = build_registry(access.db_path(home), vault=VaultIO(home / "vault"))
    design_file = (home / versions.ROOT / f"{ds}.json").read_bytes()
    before = {p.name for p in (home / shell_store.ROOT).iterdir()}
    bad = [
        *shell_store.load(home, v1)["ops"],
        {
            "op": "fill",
            "block": f"{chest[0]}@{chest[1]}",
            "lo": [-1, 1, -1],
            "hi": [-1, 1, -1],
        },
    ]
    out = reg.call(
        "build_revise",
        {"words": "a chest", "design_ops": json.dumps(bad)},
        lane=DEEP,
    )
    assert out.is_error and "nothing was written" in out.text, out.text
    assert versions.get(home, ds)["current"] == v1
    assert (home / versions.ROOT / f"{ds}.json").read_bytes() == design_file
    assert {p.name for p in (home / shell_store.ROOT).iterdir()} == before
