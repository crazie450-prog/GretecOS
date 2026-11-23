"""WO-10.5 acceptance on a real harness DB: two benchmark lines laid out (plan 8 row 10.5, D312).

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after the server harness (CI's mod job; locally
mod/README.md). The server run is collected and built into a DB in tmp_path (``gos export
collect`` + ``gos db build``), and a structure dump is loaded into it with ``gos structures
load``'s own loader, as WO-10.2's harness test does:

- the dump in GOS_STRUCTURES_DATASET when it is set (a real ``gos structures extract`` output),
  else the repo's self-authored fixture (tests/fixtures/gtnh-extractor).

The two lines: G3's steel line (``test_g3_lines_harness.LINES["steel (EBF)"]``, at MV; its
best route is the Bricked Blast Furnace's since WO-3.10 F4, a primitive structure) and G4's
benzene gas-turbine line (``test_g4_lines_harness.LINES["benzene"]``, EV), each
designed by its own designer on the harness DB, laid out alone and together. Each layout holds
every acceptance property (the unit tests' ``check``: no two keep-outs overlap, nor a keep-out
and a corridor or chase; everything inside the bounding volume; every machine's front and a cell
of each of its hatch kinds reached from the entrance by flood fill; the JSON schema), and the
same plans give the same layout.

With a real dump every multi (and primitive structure) the lines use has its own footprint,
and none is left unplaced. Without one (CI: the extractor's dump is pack data and never in the
repo), each benchmark multi or primitive structure gets a **stand-in**: the fixture's made-up
3x3x3 shape copied under its MTE id in the tmp DB only, so the placement, corridors and reach
are still checked end to end on the real plans' machines and
counts. The stand-in is test data, never a size GOS uses. ``-s`` prints the layouts.
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

from gregtec.build import coverage, layout
from gregtec.build import footprints as fp
from gregtec.build.layout_cli import render
from gregtec.db import access, build
from gregtec.db import structures as structure_db
from gregtec.ingest import collect
from gregtec.planner import plan, power
from gregtec.planner.request import DefaultBaseInputs, Request

REPO = Path(__file__).resolve().parents[2]
GAME = REPO / "mod" / "run" / "server"
FIXTURE = REPO / "tests" / "fixtures" / "gtnh-extractor"
DATASET = os.environ.get("GOS_STRUCTURES_DATASET")
STAND_IN = 9001  # the fixture's made-up multi whose shape stands in without a real dump


def _module(name: str, path: Path) -> ModuleType:
    """A test module by path (pytest imports test files by path, not by name)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).parent
unit = _module("_wo105_unit_layout", HERE.parent / "unit" / "test_build_layout.py")


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the server harness")
    if not (GAME / collect.EXPORTS).is_dir():
        pytest.fail(
            f"no server harness run in {GAME / collect.EXPORTS} (mod/README.md)"
        )
    home = tmp_path_factory.mktemp("layout")
    collect.collect(GAME, home, 1, allow_harness=True)
    built = build.build(home, allow_harness=True)
    conn = access.connect(built.db)
    try:
        structure_db.load(conn, Path(DATASET) if DATASET else FIXTURE)
    finally:
        conn.close()
    return built.db


@pytest.fixture(scope="module")
def lines(db: Path) -> list[dict[str, Any]]:
    """The two benchmark lines' best routes, as their designers make them."""
    g3 = _module("_wo105_g3", HERE / "test_g3_lines_harness.py")
    g4 = _module("_wo105_g4", HERE / "test_g4_lines_harness.py")
    conn = access.connect(db, readonly=True)
    try:
        key, tier = g3.LINES["steel (EBF)"]
        d = plan.design(
            conn, Request(key, qty=1, tier_cap=tier), DefaultBaseInputs.from_db(conn)
        )
        assert d.plans, "the steel line didn't design"
        steel = dict(d.plans[0])
        steel.setdefault("id", "g3-steel")
        ln = g4.LINES["benzene"]
        req = power.PowerRequest(
            mode=ln.mode, tier_cap=ln.tier, kinds=ln.kinds, fuels=ln.fuels
        )
        pd = power.design(conn, req, DefaultBaseInputs(ln.tier))
        assert pd.options, (pd.notes, pd.rejected)
        benzene = dict(pd.options[0])
    finally:
        conn.close()
    return [steel, benzene]


def _stand_ins(db: Path, mtes: set[int]) -> list[int]:
    """Without a real dump: the fixture's shape copied under each multi's MTE id that has no
    footprint (tmp DB only). Returns the ids given one."""
    conn = access.connect(db)
    try:
        lib = fp.library(conn)
        linked = coverage._linked(conn, lib)
        cols = [str(r[1]) for r in conn.execute("PRAGMA table_info(structure)")]
        cols = [c for c in cols if c != "id"]
        given = []
        for mte in sorted(mtes):
            if lib.for_mte(mte) or linked.get(mte):
                continue
            rows = conn.execute(
                f"SELECT {', '.join(cols)} FROM structure WHERE registry_name = ? AND meta = ?",
                (fp.GT_MACHINES, STAND_IN),
            ).fetchall()
            for r in rows:
                values = dict(zip(cols, r, strict=True))
                values.update(
                    meta=mte, machine_id=None, name_en=f"stand-in for mte:{mte}"
                )
                blocks = json.loads(values["blocks_json"])
                for b in blocks:  # the controller cell becomes this multi's own
                    if b["block"] == fp.GT_MACHINES and b["meta"] == STAND_IN:
                        b["meta"] = mte
                values["blocks_json"] = json.dumps(blocks)
                marks = ", ".join("?" * len(cols))
                conn.execute(
                    f"INSERT INTO structure ({', '.join(cols)}) VALUES ({marks})",
                    [values[c] for c in cols],
                )
            given.append(mte)
        conn.commit()
        return given
    finally:
        conn.close()


@pytest.fixture(scope="module")
def conn(db: Path, lines: list[dict[str, Any]]) -> Iterator[sqlite3.Connection]:
    multis = {
        int(m["mte"])
        for p in lines
        for n in p["nodes"]
        for m in ([n["machine"]] if isinstance(n.get("machine"), dict) else [])
        if m.get("kind") in ("multiblock", "primitive") and m.get("mte") is not None
    } | {
        int(g["mte"])
        for p in lines
        for n in p["nodes"]
        if n.get("kind") == "generator"
        for g in n.get("machines", [])
    }
    given = [] if DATASET else _stand_ins(db, multis)
    print(f"\nbenchmark multis: {sorted(multis)}; stand-ins: {given}")
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def test_two_benchmark_lines_are_laid_out(
    conn: sqlite3.Connection, lines: list[dict[str, Any]]
) -> None:
    for chosen in ([lines[0]], [lines[1]], lines):
        lay = layout.plan_layout(conn, chosen)
        unit.check(lay)
        print("\n" + "\n".join(render(lay)))
        assert lay["unplaced"] == [], lay["unplaced"]
        # together, the MV steel line draws from the EV turbines through a transformer step
        # (D312); nothing else is warned about
        allowed = "a transformer step" if len(chosen) > 1 else None
        assert [w for w in lay["warnings"] if not allowed or allowed not in w] == []
        wanted = sum(
            int(g["count"]) if n.get("kind") == "generator" else int(n["count"])
            for p in chosen
            for n in p["nodes"]
            for g in (n.get("machines") or [n])
            if (n.get("machine") or {}).get("mte") is not None and n.get("count")
        )
        assert len(lay["placements"]) == wanted
        # a structure is placed: steel's best route is the Bricked Blast Furnace's (a
        # primitive structure, WO-3.10 F4), benzene's has the gas turbines
        assert any(p["kind"] in ("multiblock", "primitive") for p in lay["placements"])
        again = layout.plan_layout(conn, json.loads(json.dumps(chosen)))
        assert again == lay
        assert layout.card(lay)["type"] == "layout"
    both = layout.plan_layout(conn, lines)
    gens = [p for p in both["placements"] if p["role"] == "generator"]
    assert gens, "the benzene line placed no gas turbine"
    eu = [s for s in both["services"] if s["kind"] == "eu"]
    assert eu and all(s["to"] for s in eu)
    # a consumer tier with no generator of its own is fed from the turbines, flagged; one at
    # the turbines' tier from them directly; never silently from the base grid (e1)
    ids = {p["id"] for p in gens}
    for s in eu:
        assert set(s["from"]) == ids, s
        flagged = any(f"{s['item']}:" in w for w in both["warnings"])
        assert s["via_transformer"] is flagged
    print("eu services:", [(s["item"], s["via_transformer"]) for s in eu])
