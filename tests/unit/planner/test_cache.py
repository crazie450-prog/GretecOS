"""The planner's process caches (WO-3.9, D352) on the WO-3.4 and WO-4.2 fixture DBs (made-up
items): the DB generation and what changes it, the keys (no answer reused where an input
differs), invalidation by a rebuild, the bounds, and the differential check that designs with
the caches on give byte-equal plans, rankings and citations to designs with them off."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import shutil
import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.db import access, build
from gregtec.planner import (
    cache,
    chains,
    effort,
    graph,
    machines,
    plan,
    power,
    progress,
)
from gregtec.planner.request import Constraints, Request


def _fixture(name: str, alias: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tp = _fixture("test_plan.py", "_wo39c_plan_fixture")
tpw = _fixture("test_power.py", "_wo39c_power_fixture")
tg = tp.tg


@pytest.fixture(scope="module")
def plan_src(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return Path(tp.make_home(tmp_path_factory.mktemp("cache-plan-home")))


@pytest.fixture(scope="module")
def power_src(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return Path(tpw.make_home(tmp_path_factory.mktemp("cache-power-home")))


def _copy(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    a = access.connect(src, readonly=True)
    b = sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()
    return dst


@pytest.fixture
def db(plan_src: Path, tmp_path: Path) -> Path:
    return _copy(plan_src, tmp_path / "plan.sqlite")


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db)
    try:
        yield c
    finally:
        c.close()


def design(
    c: sqlite3.Connection,
    req: Request,
    base: object | None = None,
    templates: chains.Templates = chains.EMPTY,
) -> str:
    d = plan.design(c, req, base or tg.Base(), templates=templates)  # type: ignore[arg-type]
    return "\n".join(plan.dumps(p) for p in d.plans)


REQS = [
    Request(tp.WIDGET, qty=2, tier_cap="MV"),
    Request(tp.WIDGET, qty=2, tier_cap="LV"),
    Request(tp.WIDGET, mode="batch", qty=64, tier_cap="MV"),
    Request(tp.GAS, qty=1, tier_cap="MV", routes=2),
    Request(tp.WIDGET, qty=1, tier_cap="MV", machines="base+addons"),
]


# --- the generation ------------------------------------------------------------------------------


def test_a_db_without_a_file_is_never_cached() -> None:
    c = sqlite3.connect(":memory:")
    try:
        assert cache.scope(c) is None
    finally:
        c.close()


def test_off_means_no_scope(conn: sqlite3.Connection) -> None:
    with cache.disabled():
        assert cache.scope(conn) is None
    assert cache.scope(conn) is not None


def test_what_changes_the_generation(conn: sqlite3.Connection, db: Path) -> None:
    path = cache.db_file(conn)
    assert path is not None and Path(path) == db.resolve()
    first = cache.generation(conn, path)
    # Writes the planner doesn't read: the same generation (events, plans, other kv rows).
    conn.execute("INSERT INTO kv(key, value, upd) VALUES ('tier.override', 'mv', 1)")
    conn.execute("INSERT INTO plan(id, name, created) VALUES ('pl-0000000009', 'x', 1)")
    assert cache.generation(conn, path) == first
    # A build's stamp, an export run, a structure: a new generation each.
    conn.execute("UPDATE kv SET value = 'b2' WHERE key = 'db.build'")
    second = cache.generation(conn, path)
    assert second != first
    conn.execute(
        "INSERT INTO export_run(created_at, fingerprint, run_key) VALUES ('t', '', 'r2')"
    )
    third = cache.generation(conn, path)
    assert third != second
    conn.execute("INSERT INTO structure(machine_id, variant) VALUES (1, 'x')")
    assert cache.generation(conn, path) != third


def test_gos_db_build_stamps_every_build(plan_src: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    shutil.copytree(plan_src.parent.parent, home)  # GOS_HOME: data/exports and the DB
    c = access.connect(access.db_path(home), readonly=True)
    try:
        before = c.execute("SELECT value FROM kv WHERE key = 'db.build'").fetchone()[0]
    finally:
        c.close()
    build.build(home)  # the same run again
    c = access.connect(access.db_path(home), readonly=True)
    try:
        after = c.execute("SELECT value FROM kv WHERE key = 'db.build'").fetchone()[0]
    finally:
        c.close()
    assert int(after) == int(before) + 1  # a counter, not a random stamp (D352)


# --- the differential check ----------------------------------------------------------------------


def test_designs_are_byte_equal_with_the_cache_off_and_on(
    conn: sqlite3.Connection,
) -> None:
    with cache.disabled():
        off = [design(conn, r) for r in REQS]
    on_cold = [design(conn, r) for r in REQS]
    on_warm = [design(conn, r) for r in reversed(REQS)][::-1]
    assert on_cold == off and on_warm == off
    assert cache.stats.effort_hits > 0 and cache.stats.reader_hits > 0


def test_power_designs_are_byte_equal_with_the_cache_off_and_on(
    power_src: Path, tmp_path: Path
) -> None:
    path = _copy(power_src, tmp_path / "power.sqlite")
    farms = tmp_path / "farms.tsv"
    farms.write_text("\t".join(tpw.farms.HEADER) + "\n", encoding="utf-8")
    c = access.connect(path, readonly=True)

    def run(mode: str) -> str:
        d = power.design(
            c,
            power.PowerRequest(mode=mode, free=tpw.FREE, tier_cap=2),
            tg.Base(),
            templates=chains.EMPTY,
            with_effort=True,
            farms_path=farms,
        )
        return "\n".join(power.dumps(p) for p in d.options) + repr(d.rejected)

    modes = ["best", "max_output", "output_per_effort", "self_sustaining"]
    try:
        with cache.disabled():
            off = [run(m) for m in modes]
        on = [run(m) for m in modes]
        again = [run(m) for m in modes]
    finally:
        c.close()
    assert on == off and again == off
    assert cache.stats.effort_hits > 0


def test_every_input_of_a_component_solve_is_in_its_key(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A design whose tier, machines, constraints, My base or templates differ never reuses
    another's component solves: each equals the same design with the cache off."""
    (tmp_path / "test.tsv").write_text(
        "\t".join(chains.HEADER)
        + f"\n{tp.GAS}\t{tg.ref_of(conn, 'electrolyzer', 1)}\t\tthe gas this way\n",
        encoding="utf-8",
    )
    tpl = chains.load(tmp_path)
    assert tpl.rows
    req = Request(tp.WIDGET, qty=2, tier_cap="MV")
    variants: list[tuple[Request, object, chains.Templates]] = [
        (req, tg.Base(), chains.EMPTY),
        (Request(tp.WIDGET, qty=2, tier_cap="LV"), tg.Base(), chains.EMPTY),
        (
            Request(tp.WIDGET, qty=2, tier_cap="MV", machines="base+addons"),
            tg.Base(),
            chains.EMPTY,
        ),
        (
            Request(tp.WIDGET, qty=2, tier_cap="MV", boundary=(tp.GAS,)),
            tg.Base(),
            chains.EMPTY,
        ),
        (req, tg.Base(stocked={tp.SALT: 64.0}), chains.EMPTY),
        (req, tg.Base(lines={tp.GAS: 1.0}), chains.EMPTY),
        (req, tg.Base(owned={101: 1, 102: 2}), chains.EMPTY),  # its mixer and reactor
        (req, tg.Base(), tpl),
    ]
    with cache.disabled():
        off = [design(conn, r, b, t) for r, b, t in variants]
    on = [design(conn, r, b, t) for r, b, t in variants]
    assert on == off
    assert cache.stats.effort_misses > 0


def _solve(
    conn: sqlite3.Connection,
    req: Request,
    base: object,
    templates: chains.Templates = chains.EMPTY,
    run_id: int | None = None,
    key: str = tp.WIDGET,
) -> str:
    """One component's own solve (``Effort.make``, what a route's effort sums), as JSON."""
    assert req.tier_cap is not None
    e = effort.Effort(
        conn,
        req,
        base,  # type: ignore[arg-type]
        req.tier_cap,
        templates=templates,
        run_id=run_id,
    )
    return json.dumps(dataclasses.asdict(e.make(key, 2)), sort_keys=True)


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The plan fixture DB with a second, newer export run without chemicalreactor/5 (Plate
    A to Widget, the Widget's own last step); the DB keeps both runs."""
    home = tmp_path_factory.mktemp("cache-two-runs")
    path = Path(tp.make_home(home))
    recipes, run = tg.recipes, tg.RUN

    def fewer() -> list[dict[str, object]]:
        return [
            x
            for x in recipes()
            if (x["map"], x["idx"]) != ("gt.recipe.chemicalreactor", 5)
        ]

    tg.recipes, tg.RUN = fewer, "20261112T120000Z"
    try:
        tp.make_home(home)
    finally:
        tg.recipes, tg.RUN = recipes, run
    return path


MV = Request(tp.WIDGET, qty=2, tier_cap="MV")
OWNED_ONLY = Request(
    tp.WIDGET, qty=2, tier_cap="MV", constraints=Constraints(require_owned=True)
)
SOLVE_INPUTS = [
    "tier",
    "machines",
    "boundary",
    "constraints",
    "owned",
    "stock",
    "lines",
    "templates",
    "raw_inputs",
    "run",
    "generation",
]


@pytest.mark.parametrize("what", SOLVE_INPUTS)
def test_each_input_of_a_component_solve_is_in_its_key(
    what: str,
    conn: sqlite3.Connection,
    two_runs: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For each input a cached component solve reads (Effort._shared_key, the request digest,
    the graph reader's run), a change that changes the component's own solve: the unchanged
    solve is a cache hit, and the changed one equals the same solve with the cache off. Taking
    the input out of the key makes the changed solve reuse the unchanged one's answer.
    My base's tier is in the key too but no solve reads it: the component request always
    carries the design's tier (effort.py Effort._request), which graph.player takes first."""
    c = conn
    before: Callable[[], str]
    after: Callable[[], str]
    if what == "tier":  # salt needs the MV electrolyzer
        before = lambda: _solve(c, MV, tg.Base(), key=tp.SALT)
        lv = Request(tp.WIDGET, qty=2, tier_cap="LV")
        after = lambda: _solve(c, lv, tg.Base(), key=tp.SALT)
    elif what == "machines":  # the addon mixer is faster
        before = lambda: _solve(c, MV, tg.Base())
        addons = Request(tp.WIDGET, qty=2, tier_cap="MV", machines="base+addons")
        after = lambda: _solve(c, addons, tg.Base())
    elif what == "boundary":
        before = lambda: _solve(c, MV, tg.Base())
        bound = Request(tp.WIDGET, qty=2, tier_cap="MV", boundary=(tg.PLATE,))
        after = lambda: _solve(c, bound, tg.Base())
    elif what == "constraints":
        before = lambda: _solve(c, MV, tg.Base(owned={101: 1}))
        after = lambda: _solve(c, OWNED_ONLY, tg.Base(owned={101: 1}))
    elif what == "owned":  # owned machines only: the mixer's route or the reactor's
        before = lambda: _solve(c, OWNED_ONLY, tg.Base(owned={101: 1}))
        after = lambda: _solve(c, OWNED_ONLY, tg.Base(owned={102: 1}))
    elif what == "stock":
        before = lambda: _solve(c, MV, tg.Base())
        after = lambda: _solve(c, MV, tg.Base(stocked={tg.PLATE: 1.0}))
    elif what == "lines":
        before = lambda: _solve(c, MV, tg.Base())
        after = lambda: _solve(c, MV, tg.Base(lines={tg.PLATE: 1.0}))
    elif what == "templates":  # the Widget from the mixer instead of the reactor
        (tmp_path / "test.tsv").write_text(
            "\t".join(chains.HEADER)
            + f"\n{tp.WIDGET}\t{tp.ref_of(c, 'mixer', 0)}\t\tthe widget this way\n",
            encoding="utf-8",
        )
        tpl = chains.load(tmp_path)
        before = lambda: _solve(c, MV, tg.Base())
        after = lambda: _solve(c, MV, tg.Base(), tpl)
    elif what == "raw_inputs":  # data/curated/raw_inputs.tsv, a repo file

        def after() -> str:
            with monkeypatch.context() as m:
                m.setattr(graph, "raw_inputs", lambda: (*raw(), tg.PLATE))
                return _solve(c, MV, tg.Base())

        raw = graph.raw_inputs
        before = lambda: _solve(c, MV, tg.Base())
    elif what == "run":  # an older run of the same DB (same generation)
        c = access.connect(two_runs, readonly=True)
        old = int(c.execute("SELECT min(id) FROM export_run").fetchone()[0])
        assert machines.current_run(c) != old
        before = lambda: _solve(c, MV, tg.Base(), run_id=old)
        after = lambda: _solve(c, MV, tg.Base())
    else:  # the DB generation: a rebuild that changed a recipe's time
        slow = tp.ref_of(c, "chemicalreactor", 5)

        def after() -> str:
            c.execute("UPDATE recipe SET duration_ticks = 80 WHERE ref = ?", (slow,))
            c.execute(
                "INSERT INTO kv(key, value, upd) VALUES ('db.build', 'rebuilt', 2)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
            return _solve(c, MV, tg.Base())

        before = lambda: _solve(c, MV, tg.Base())
    try:
        first = before()
        hits = cache.stats.effort_hits
        # The unchanged case is served by the cache.
        assert before() == first and cache.stats.effort_hits > hits
        on = after()
        with cache.disabled():
            off = after()
    finally:
        if c is not conn:
            c.close()
    assert off != first  # the input changes the component's own solve
    assert on == off


def test_solve_progress_never_counts_past_n(conn: sqlite3.Connection) -> None:
    """Review LOW-2: the route search explores past the n routes asked for, and its progress
    still reads k of n with k never past n."""
    seen: list[tuple[int | None, int | None]] = []

    def report(stage: str | None, k: int | None, n: int | None) -> None:
        if stage == "solve":
            seen.append((k, n))

    with progress.reporting(report):
        design(conn, Request(tp.WIDGET, qty=2, tier_cap="MV", routes=1))
    assert len(seen) > 1  # it searched past the one route asked for
    assert all(k is not None and n is not None and 1 <= k <= n for k, n in seen)
    assert seen[-1] == (1, 1)


def test_a_changed_quantity_in_my_base_reuses_the_solves(
    conn: sqlite3.Connection,
) -> None:
    """The component solves read only which stock, line outputs and owned machines are
    positive, so editing a quantity hits the cache and still equals a design with it off."""
    req = Request(tp.WIDGET, qty=2, tier_cap="MV")
    design(conn, req, tg.Base(stocked={tp.SALT: 64.0}, owned={101: 1}))
    misses = cache.stats.effort_misses
    edited = tg.Base(stocked={tp.SALT: 3.0}, owned={101: 4})
    on = design(conn, req, edited)
    assert cache.stats.effort_misses == misses and cache.stats.effort_hits > 0
    with cache.disabled():
        assert design(conn, req, edited) == on


def test_a_shared_key_resolves_the_same_whatever_was_read_first(
    conn: sqlite3.Connection,
) -> None:
    """Review LOW-6: two items whose NBT hashes share their first 8 characters have one key;
    the shared reader gives both ids, in id order, however an earlier read loaded them."""
    from gregtec.planner import graph, machines

    ids = [
        int(
            conn.execute(
                "INSERT INTO item(registry_name, meta, nbt_hash) VALUES ('t:twin', 0, ?)"
                " RETURNING id",
                (h,),
            ).fetchone()[0]
        )
        for h in ("abcdef01aaaa", "abcdef01bbbb")
    ]
    run = machines.current_run(conn)
    key = "t:twin@0#abcdef01"
    for first in ([ids[1]], [ids[0]], []):  # one twin read first (a recipe), or neither
        r = graph._Reader(conn, run)
        r.load_items(first)
        assert r.key_ids(key) == (sorted(ids), [])
        assert r.key_ids(key) == (sorted(ids), [])


# --- invalidation and bounds ---------------------------------------------------------------------


def test_a_rebuild_drops_what_the_cache_kept(conn: sqlite3.Connection) -> None:
    req = REQS[0]
    first = design(conn, req)
    assert cache.sizes()["effort"] > 0 and cache.sizes()["readers"] == 1
    conn.execute(
        "INSERT INTO kv(key, value, upd) VALUES ('db.build', 'rebuilt', 2)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )
    misses = cache.stats.effort_misses
    assert design(conn, req) == first
    assert cache.stats.dropped == 1
    assert cache.stats.effort_misses > misses  # solved again, not reused
    assert cache.sizes()["readers"] == 1  # the old generation's reader is gone


def test_two_db_files_never_share(db: Path, tmp_path: Path) -> None:
    other = _copy(db, tmp_path / "other.sqlite")
    for path in (db, other):
        c = access.connect(path, readonly=True)
        try:
            design(c, REQS[0])
        finally:
            c.close()
    assert cache.sizes()["readers"] == 2 and cache.stats.reader_hits > 0
    assert len({k[0] for k in cache._readers}) == 2


def test_the_caches_are_bounded(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache, "EFFORT_MAX", 2)
    monkeypatch.setattr(cache, "READER_MAX_RECIPES", -1)  # every reader is past it
    with cache.disabled():
        off = design(conn, REQS[0])
    assert design(conn, REQS[0]) == off
    assert cache.sizes()["effort"] <= 2
    assert cache.sizes()["readers"] == 0  # past its size: dropped after the build
