"""The planner's process caches (WO-3.9, D352) on the server harness DB: the G3 benchmark lines
and the G4 power lines designed with the cache off, then on (cold, then warm), give byte-equal
plans, the same rank order and the same cited ids; a ``gos db build`` of the same run drops
what was kept, and the designs after it are equal again.

Runs only with GOS_REQUIRE_HARNESS_RUN=1 after the server harness (CI's mod job; locally
mod/README.md). Writes nothing outside tmp_path.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import cache, graph, plan, power, solve
from gregtec.planner.request import DefaultBaseInputs, Request

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, alias: str) -> ModuleType:
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, Path(__file__).with_name(name))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


g3 = _load("test_g3_lines_harness.py", "_wo39_g3_lines")
g4 = _load("test_g4_lines_harness.py", "_wo39_g4_lines")


@pytest.fixture(scope="module")
def home(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    h = tmp_path_factory.mktemp("design-cache")
    built_home(game, h)
    return h


def _production(conn: sqlite3.Connection) -> list[str]:
    out = []
    for name, (key, tier) in g3.LINES.items():
        try:
            d = plan.design(
                conn, Request(key, qty=1.0, tier_cap=tier), DefaultBaseInputs(tier)
            )
        except (graph.GraphError, LookupError, solve.SolveError) as exc:
            out.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        out.append(f"{name}:\n" + "\n".join(plan.dumps(p) for p in d.plans))
    return out


def _power(conn: sqlite3.Connection) -> list[str]:
    out = []
    for name, ln in g4.LINES.items():
        req = power.PowerRequest(
            mode=ln.mode, tier_cap=ln.tier, kinds=ln.kinds, fuels=ln.fuels
        )
        try:
            d = power.design(conn, req, DefaultBaseInputs(ln.tier))
        except power.PowerError as exc:
            out.append(f"{name}: {exc}")
            continue
        out.append(
            f"{name}:\n"
            + "\n".join(power.dumps(p) for p in d.options)
            + f"\n{d.rejected!r}\n{d.notes!r}"
        )
    return out


def test_g3_and_g4_designs_are_byte_equal_with_the_cache_off_and_on(
    home: Path,
) -> None:
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        with cache.disabled():
            off = _production(conn) + _power(conn)
        cold = _production(conn) + _power(conn)
        warm = _production(conn) + _power(conn)
    finally:
        conn.close()
    assert sum(":\n" in x for x in off) >= 8  # most lines design on the dev runtime
    for a, b, c in zip(off, cold, warm, strict=True):
        assert a == b, a.splitlines()[0]
        assert a == c, a.splitlines()[0]
    assert cache.stats.effort_hits > 0 and cache.stats.reader_hits > 0


def test_a_rebuild_drops_the_cache(home: Path) -> None:
    key, tier = g3.LINES["steel (EBF)"]
    req = Request(key, qty=1.0, tier_cap=tier)

    def steel() -> str:
        conn = access.connect(access.db_path(home), readonly=True)
        try:
            d = plan.design(conn, req, DefaultBaseInputs(tier))
            return "\n".join(plan.dumps(p) for p in d.plans)
        finally:
            conn.close()

    first = steel()
    assert cache.sizes()["effort"] > 0
    build.build(home, allow_harness=True)  # the same run again: a new generation
    misses = cache.stats.effort_misses
    assert steel() == first
    assert cache.stats.dropped == 1 and cache.stats.effort_misses > misses
