"""The power designer's objective modes (WO-4.2, D233-D235) on the WO-3.3 fixture DB
(tests/unit/planner/test_graph.py) plus, for this file only: a fuel map with two fuels (the test
gas and the test acid), a single-block generator burning it, a second way to make the test gas
(``mixer/7``: Dust B, made from any test log), a Tree Growth Simulator page for a test sapling
that grows Oak Test Logs, and the generator row the export's power pass would write. Every item,
recipe, machine and number is made up; no pack data.

The fixture's lines: the test gas from water through its loop (acid from gas and water, gas from
acid: net one litre of gas per litre of water), so a generator on test gas closes on water
alone; or from Dust B, which needs logs: closed only with the tree farm.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import shutil
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.db import access
from gregtec.planner import chains, farms, power, powercli, rank, solve, sustain
from gregtec.planner.request import RequestError


def _graph_fixture() -> ModuleType:
    path = Path(__file__).with_name("test_graph.py")
    spec = importlib.util.spec_from_file_location("_wo42_graph_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tg = _graph_fixture()
GAS, ACID, WATER = tg.GAS, tg.ACID, tg.WATER
LOG_A = tg.LOG_A
SAPLING = f"{tg.T}@30"
GENERATOR = 110
TREE_FARM = 111
FREE = ("fluid:water",)


def make_home(home: Path) -> Path:
    recipes, machines, items, maps = tg.recipes, tg.machines, tg.items, tg.MAPS

    def more_recipes() -> list[dict[str, object]]:
        return [
            *recipes(),
            tg.gt("mixer", 7, 16, 50, ins=[tg.ref(4)], fout=[tg.fl("testgas", 500)]),
            tg.gt("dieselgeneratorfuel", 0, 0, 0, fin=[tg.fl("testgas", 1)], special=40),
            tg.gt("dieselgeneratorfuel", 1, 0, 0, fin=[tg.fl("testacid", 1)], special=10),
            tg.gt("treefarm", 0, 0, 100, fake=True, outs=[tg.ref(8, 5), tg.ref(30, 5)]),
        ]  # fmt: skip

    def more_machines() -> list[dict[str, object]]:
        out = machines()
        base = dict(out[0])
        gen = dict(base)
        gen.update(
            mte_id=GENERATOR, meta=GENERATOR, tier=1, name="Test Combustion Generator",
            meta_name="basicgenerator.diesel.tier.01",
            recipe_maps=["gt.recipe.dieselgeneratorfuel"],
            **{"class": "gregtech.common.tileentities.generators.MTEDieselGenerator"},
        )  # fmt: skip
        farm = dict(base)
        farm.update(
            mte_id=TREE_FARM, meta=TREE_FARM, tier=None, multiblock=True,
            name="Tree Growth Simulator", meta_name="multimachine.treefarm",
            recipe_maps=["gt.recipe.treefarm"],
            **{"class": "gregtech.common.tileentities.machines.multi.MTETreeFarm"},
        )  # fmt: skip
        return [*out, gen, farm]

    def more_items() -> list[dict[str, object]]:
        return [
            *items(),
            {"item": tg.T, "meta": 30, "unlocalized": "t.30", "name": "Test Sapling",
             "ore": []},
        ]  # fmt: skip

    tg.recipes, tg.machines, tg.items = more_recipes, more_machines, more_items
    tg.MAPS = (*maps, "dieselgeneratorfuel", "treefarm")
    try:
        db = tg.make_home(home)
    finally:
        tg.recipes, tg.machines, tg.items, tg.MAPS = recipes, machines, items, maps
    c = sqlite3.connect(db)
    try:
        run = int(c.execute("SELECT MAX(id) FROM export_run").fetchone()[0])
        c.execute(
            "INSERT INTO generator VALUES (?,?,?,?,?,?,?,?)",
            (run, GENERATOR, "gregtech.common.tileentities.generators.MTEDieselGenerator",
             1, 100, 32, 1, "gt.recipe.dieselgeneratorfuel"),
        )  # fmt: skip
        c.commit()
    finally:
        c.close()
    return db


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_home(tmp_path_factory.mktemp("power-home"))


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def no_farms(tmp_path: Path) -> Path:
    path = tmp_path / "farms.tsv"
    path.write_text("\t".join(farms.HEADER) + "\n", encoding="utf-8")
    return path


def design(
    conn: sqlite3.Connection, farms_path: Path, **kw: object
) -> power.PowerDesign:
    kw.setdefault("free", FREE)
    kw.setdefault("tier_cap", 2)
    effort = bool(kw.pop("with_effort", False))
    return power.design(
        conn,
        power.PowerRequest(**kw),
        tg.Base(),
        templates=chains.EMPTY,
        with_effort=effort,
        farms_path=farms_path,
    )


# --- candidates ------------------------------------------------------------------------------


def test_candidates_read_the_catalog_and_prune_by_eu_per_fuel(
    conn: sqlite3.Connection,
) -> None:
    gens, notes = power.candidates(conn, 1, power.PowerRequest(free=FREE), 2)
    assert {g.fuel for g in gens} == {GAS, ACID}
    gas = next(g for g in gens if g.fuel == GAS)
    # 32 EU/t x 20 / (40 EU/L x 100 %) = 16 L/s; cited by the generator and the fuel row.
    assert gas.kind == "single_block" and gas.eut == 32
    assert gas.fuel_per_s == pytest.approx(16.0)
    assert gas.cites[0] == f"mte:{GENERATOR}" and gas.fuel_ref.startswith("r:")
    assert gas.eu_per_fuel == pytest.approx(40.0)
    assert any("turbine" in n for n in notes)  # no rotors in this DB
    # The top 1 per generator: the denser fuel.
    assert [g.fuel for g in power.prune(gens, 1)] == [GAS]
    # self_sustaining: only reachable fuels, the lowest level first.
    assert [g.fuel for g in power.prune(gens, 1, {ACID: 0, GAS: 1})] == [ACID]
    assert power.prune(gens, 4, {}) == []
    # Filters.
    only, _ = power.candidates(conn, 1, power.PowerRequest(free=FREE, fuels=(ACID,)), 2)
    assert [g.fuel for g in only] == [ACID]
    none, _ = power.candidates(
        conn, 1, power.PowerRequest(free=FREE, kinds=("gas_turbine",)), 2
    )
    assert none == []
    low, _ = power.candidates(conn, 1, power.PowerRequest(free=FREE), 0)
    assert low == []  # the generator is LV


def test_reachable_levels(conn: sqlite3.Connection) -> None:
    lv = power.reachable(conn, 1, 2, {WATER})
    assert lv[WATER] == 0
    assert GAS not in lv  # the loop needs gas or acid to start
    lv = power.reachable(conn, 1, 2, {WATER, GAS})
    assert lv[ACID] == 1
    lv = power.reachable(conn, 1, 2, {LOG_A})
    assert lv["ore:logTest"] == 0  # a slot through its member, same level
    assert lv[tg.DUST_B] == 1 and lv[GAS] == 2


# --- modes ------------------------------------------------------------------------------------


def test_best_returns_ranked_options_with_printed_weights(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    d = design(conn, no_farms, mode="best")
    assert 2 <= len(d.options) <= 3
    assert d.weights.startswith("mode best") and "net EU/t 1" in d.weights
    assert [p["rank"]["position"] for p in d.options] == list(
        range(1, len(d.options) + 1)
    )
    scores = [p["rank"]["score"] for p in d.options]
    assert scores == sorted(scores, reverse=True)
    ids = [p["id"] for p in d.options]
    assert len(set(ids)) == len(ids)
    for p in d.options:
        pw = p["power"]
        assert p["kind"] == "power" and p["gt"] == "5.09.54.265"
        assert pw["net_eut"] > 0
        assert pw["net_eut"] == pytest.approx(
            pw["gross_eut"] - pw["chain_eut"], abs=1e-6
        )
        assert sorted(p["alternatives"]) == sorted(i for i in ids if i != p["id"])
        assert p["metrics"]["net_eut"] == pw["net_eut"]
        gen = next(n for n in p["nodes"] if n["kind"] == "generator")
        assert gen["machine"]["mte"] == GENERATOR and gen["count"] == 1
        assert "bootstrap" not in p
    # Options differ in the generator, the fuel or a primary choice of the chain.
    keys = {
        (
            p["power"]["generator"]["id"],
            tuple(c["recipe"] for c in p["route"]["choices"]),
        )
        for p in d.options
    }
    assert len(keys) == len(d.options)
    # The same request gives the same order (D235: ranking is rank.py's, deterministic).
    again = design(conn, no_farms, mode="best")
    assert [p["id"] for p in again.options] == ids


def test_max_output_within_the_scale_bound(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    one = design(conn, no_farms, mode="max_output")
    nets = [p["power"]["net_eut"] for p in one.options]
    assert nets == sorted(nets, reverse=True)
    two = design(conn, no_farms, mode="max_output", scale="generators", bound=2)
    top = two.options[0]
    assert top["power"]["units"] == 2
    gen = next(n for n in top["nodes"] if n["kind"] == "generator")
    assert gen["count"] == 2 and top["power"]["gross_eut"] == 64
    assert top["power"]["net_eut"] == pytest.approx(2 * nets[0], rel=1e-6)
    capped = design(
        conn, no_farms, mode="max_output", scale="eut_cap", bound=nets[0] * 1.5
    )
    assert all(p["power"]["units"] == 1 for p in capped.options)
    tiny = design(conn, no_farms, mode="max_output", scale="eut_cap", bound=0.001)
    assert tiny.options == () and any("over the cap" in w for _, w in tiny.rejected)


def test_output_per_effort_ranks_by_net_per_effort_hour(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    d = design(conn, no_farms, mode="output_per_effort", with_effort=True)
    assert d.options
    for p in d.options:
        assert p["rank"]["mode"] == "output_per_effort"
        assert "eff_h" in p["metrics"] and "effort" in p["metrics"]


def test_self_sustaining_closes_on_water_and_proves_it(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    d = design(conn, no_farms, mode="self_sustaining", fuels=(GAS,))
    assert d.options
    for p in d.options:
        proof = p["closed"]
        assert proof["ok"] and proof["imports"] == []
        assert [b["id"] for b in p["boundary"]["in"]] == [WATER]
        assert proof["net_eut"] > 0
        kit = p["bootstrap"]
        assert kit["starter_fuel"][0]["id"] == GAS and kit["starter_fuel"][0]["qty"] > 0
        # The test gas comes from its loop on water: the chain's recipes run.
        assert any(n["recipe"].startswith("r:") for n in p["nodes"])


def test_self_sustaining_with_a_farm(
    conn: sqlite3.Connection, no_farms: Path, tmp_path: Path
) -> None:
    # Pin the gas to Dust B (logs): closed only with the tree farm growing Oak Test Logs.
    fl, _ = farms.load(conn, 1, no_farms)
    assert [f.id for f in fl] and fl[0].machine == TREE_FARM
    farm = fl[0]
    assert farm.outputs == ((LOG_A, 50.0),) and farm.catalysts == ((SAPLING, 1.0),)
    assert farm.eu_per_op == farm.eut * 100 + 32
    chain = power._chain(
        conn, GAS, power.PowerRequest(mode="self_sustaining", free=FREE), tg.Base(),
        2, 1, chains.EMPTY, pin=_mixer7(conn),
    )  # fmt: skip
    gens, _ = power.candidates(conn, 1, power.PowerRequest(free=FREE), 2)
    gen = next(g for g in gens if g.fuel == GAS)
    req = power.PowerRequest(mode="self_sustaining", free=FREE)
    net = power.network(conn, chain, gen, 1.0, req, fl)
    assert LOG_A in net.banned and WATER not in net.banned
    flow = solve.routes(net, 1)[0]
    p = power.build_plan(
        conn,
        "x",
        req,
        power.Option(gen, chain, 1.0, flow, net),
        {f.id: f for f in fl},
        1,
    )
    p["run"] = tg.RUN
    proof = sustain.validate(conn, p, FREE, {f.id: f for f in fl})
    assert proof.ok, proof
    kinds = {n.get("kind") for n in p["nodes"]}
    assert {"farm", "member", "generator"} <= kinds
    kit = sustain.bootstrap(conn, p, {f.id: f for f in fl})
    assert kit["seeds_saplings"] == [{"id": SAPLING, "qty": 1}]
    assert kit["tools"] == [{"tool": "buzzsaw", "qty": 1}]
    # Without the farm the same chain can't close: logs are banned imports.
    with pytest.raises(solve.SolveError):
        solve.routes(power.network(conn, chain, gen, 1.0, req, ()), 1)


def _mixer7(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT r.ref FROM recipe AS r JOIN recipe_map AS m ON m.id = r.recipe_map_id"
        " WHERE m.unloc_name = 'gt.recipe.mixer' AND r.duration_ticks = 50"
    ).fetchone()
    return str(row[0])


def test_self_sustaining_closes_on_air_with_the_tree_farm(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    # Without water the gas loop can't run, but the gas from Dust B from farmed logs can.
    d = design(conn, no_farms, mode="self_sustaining", free=("fluid:air",))
    assert d.options
    for p in d.options:
        assert p["closed"]["ok"] and p["boundary"]["in"] == []
        assert any(n["kind"] == "farm" for n in p["nodes"])
        assert p["bootstrap"]["seeds_saplings"] == [{"id": SAPLING, "qty": 1}]


def test_self_sustaining_rejects_lines_that_import(
    conn: sqlite3.Connection, no_farms: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The acid needs water (its loop, or gas and water): with only air free, the closure
    # doesn't reach it, so no candidate is left.
    with pytest.raises(power.PowerError, match="no generator and fuel"):
        design(
            conn, no_farms, mode="self_sustaining", free=("fluid:air",), fuels=(ACID,)
        )
    # Past the pre-filter, the MILP's ban finds no route and the validator, on the line
    # solved without the ban, names what it would import.
    monkeypatch.setattr(power, "reachable", lambda *a: {ACID: 0})
    d = design(
        conn, no_farms, mode="self_sustaining", free=("fluid:air",), fuels=(ACID,)
    )
    assert d.options == () and d.rejected
    assert all("not closed" in why for _, why in d.rejected)
    assert any(WATER in why for _, why in d.rejected)
    assert any("only 0 option" in n for n in d.notes)


# --- the request ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"mode": "fastest"}, "mode"),
        ({"scale": "huge"}, "scale"),
        ({"scale": "unit", "bound": 2}, "no bound"),
        ({"scale": "generators"}, "needs a bound"),
        ({"scale": "generators", "bound": 1.5}, "whole number"),
        ({"free": ("water",)}, "free"),
        ({"kinds": ("nuclear",)}, "kinds"),
        ({"fuels": ("gas",)}, "fuels"),
        ({"rotor": ("Steel", "giant")}, "rotor size"),
        ({"candidates": 0}, "candidates"),
        ({"options": 4}, "options"),
        ({"tier_cap": "XYZ"}, "tier"),
    ],
)
def test_request_validation(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(RequestError, match=match):
        power.PowerRequest(**kwargs)


def test_request_normalises_and_digests() -> None:
    a = power.PowerRequest(
        tier_cap="HV", free=("fluid:water", "sunlight", "fluid:water")
    )
    assert a.tier_cap == 3 and a.free == ("fluid:water", "sunlight")
    b = power.PowerRequest(tier_cap=3, free=("sunlight", "fluid:water"))
    assert a.digest() == b.digest() and a.as_dict()["v"] == power.VERSION


def test_units_per_scale_bound() -> None:
    def units(
        scale: str, bound: float | None, net: float, up: float
    ) -> tuple[float | None, str]:
        return power._units(power.PowerRequest(scale=scale, bound=bound), net, up)

    assert units("unit", None, 10, 5) == (1.0, "")
    assert units("generators", 3, 10, 5) == (3.0, "")
    assert units("eut_cap", 25, 10, 5) == (2.0, "")
    assert units("eut_cap", 5, 10, 5)[0] is None
    assert units("eut_cap", 5, -1, 5)[0] is None
    assert units("raw_budget", 12, 10, 5) == (2.0, "")
    assert units("raw_budget", 1, 10, 5)[0] is None
    n, why = units("raw_budget", 1, 10, 0)
    assert n == 1.0 and why == ""  # design() sizes it like the best bounded line


def test_prefs_come_from_the_vault_or_the_template(tmp_path: Path) -> None:
    assert power.read_prefs(None).name == "balanced"
    (tmp_path / "me").mkdir()
    (tmp_path / "me" / "prefs.md").write_text(
        "weights: balanced\nw_net_eut: 2\nw_upkeep: 0\n", encoding="utf-8"
    )
    p = power.read_prefs(tmp_path)
    assert p.power_weight("net_eut") == 2 and p.power_weight("upkeep") == 0
    assert p.name == "custom"
    assert power.read_prefs(tmp_path / "missing").name == "balanced"
    assert power.TEMPLATE_PREFS.is_file()


# --- the CLI ----------------------------------------------------------------------------------


def run_cli(home: Path, *argv: str) -> tuple[int, str]:
    cfg = home / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")
    powercli.add_parsers(sub)
    args = p.parse_args(["power", "--config", str(cfg), *argv])
    buf = io.StringIO()
    code = powercli.run(args, buf)
    return code, buf.getvalue()


def test_cli_design(db: Path) -> None:
    home = db.parent.parent
    code, text = run_cli(
        home, "design", "self_sustaining", "--tier", "MV", "--free", "fluid:water",
        "--fuel", GAS, "--no-effort",
    )  # fmt: skip
    assert code == 0, text
    assert text.startswith("mode self_sustaining")
    assert "closed: no import outside the free set" in text
    assert "bootstrap kit:" in text and "(starter fuel)" in text
    code, text = run_cli(
        home, "design", "best", "--tier", "MV", "--free", "fluid:water", "--no-effort",
        "--json",
    )  # fmt: skip
    assert code == 0
    plans = [json.loads(line) for line in text.splitlines()]
    assert 2 <= len(plans) <= 3 and all(p["kind"] == "power" for p in plans)
    code, text = run_cli(home, "design", "best", "--scale", "generators", "--no-effort")
    assert code == 2 and "bound" in text
    code, text = run_cli(
        home, "design", "self_sustaining", "--tier", "MV", "--free", "fluid:air",
        "--fuel", ACID, "--no-effort",
    )  # fmt: skip
    assert code == 2 and "no generator and fuel" in text


# --- ranking ----------------------------------------------------------------------------------


def _plan(pid: str, net: float, eff: float, upkeep: float = 0.0) -> dict[str, Any]:
    return {
        "id": pid,
        "metrics": {
            "net_eut": net, "eff_h": eff, "upkeep": upkeep, "scarcity": 0.0,
            "complexity": {"score": 3}, "new_types": [], "tier": "MV",
        },
    }  # fmt: skip


def test_order_power_by_mode() -> None:
    plans = [_plan("a", 100, 10, 5), _plan("b", 80, 2), _plan("c", 120, 0, 50)]
    best = [p["id"] for p in rank.order_power([dict(p) for p in plans], "best")]
    # (net - effort - upkeep - complexity - scarcity) / 5, each over the largest:
    # a (0.833 - 1 - 0.1 - 1), b (0.667 - 0.2 - 0 - 1), c (1 - 0 - 1 - 1).
    assert best == ["b", "c", "a"]
    top = [p["id"] for p in rank.order_power([dict(p) for p in plans], "max_output")]
    assert top == ["c", "a", "b"]
    per = rank.order_power([dict(p) for p in plans], "output_per_effort")
    assert [p["id"] for p in per] == [
        "b",
        "a",
        "c",
    ]  # c: effort 0, after those with one
    assert (
        per[0]["rank"]["eut_per_effort_h"] == 40
        and per[2]["rank"]["eut_per_effort_h"] is None
    )
    with pytest.raises(ValueError):
        rank.order_power(plans, "fastest")
    assert "net EU/t 1" in rank.describe_power(rank.BALANCED, "best")


def test_farms_tsv(tmp_path: Path, conn: sqlite3.Connection) -> None:
    path = tmp_path / "farms.tsv"
    head = "\t".join(farms.HEADER)
    path.write_text(
        f"# a comment\n{head}\n"
        f"crop1\tTest crop\tmte:5\t20\t8\t\t{tg.DUST_B}*2\t{SAPLING}*1\tcurated\tLab\n"
        f"tree\tDup\t\t5\t8\t\t{LOG_A}*3\t\tcurated\tclashes with the DB\n",
        encoding="utf-8",
    )
    rows = farms.curated(path)
    assert [f.id for f in rows] == ["farm:crop1", "farm:tree"]
    crop = rows[0]
    assert crop.source == "curated" and crop.cite == "farms.tsv:3" and crop.machine == 5
    assert crop.eu_per_op == 8 * 20 * 20
    loaded, notes = farms.load(conn, 1, path)
    assert "farm:crop1" in {f.id for f in loaded}
    assert "farm:tree" not in {f.id for f in loaded} and "DB farm map" in notes[0]
    for bad, match in [
        ("id\tname\n", "header"),
        (f"{head}\nx\ty\t\t1\t1\t\t{LOG_A}*1\t\tdb\tn\n", "curated"),
        (f"{head}\nx\ty\t\t1\t1\t\t\t\tcurated\tn\n", "gives"),
        (f"{head}\nx\ty\t\t1\t1\t\tlog*1\t\tcurated\tn\n", "stable key"),
        (f"{head}\nx\ty\t\t0\t1\t\t{LOG_A}*1\t\tcurated\tn\n", "seconds"),
        (f"{head}\nx\ty\t7\t1\t1\t\t{LOG_A}*1\t\tcurated\tn\n", "mte"),
        (f"{head}\nx\ty\t\t1\t1\t\t{LOG_A}*z\t\tcurated\tn\n", "number"),
        (f"{head}\nx\ty\n", "cells"),
    ]:
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(farms.FarmError, match=match):
            farms.curated(path)
    assert farms.curated(farms.FARMS) == []  # ships empty until curated (D233)


# --- review fixes (WO-4.2 review: HIGH-1, verdict 11, LOW-2) ------------------------------------


def _writable_copy(db: Path, tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "copy.db"
    shutil.copyfile(db, path)
    return sqlite3.connect(path)


def test_single_block_fuel_is_gts_truncated_eu_per_litre(
    db: Path, tmp_path: Path
) -> None:
    # A 95 % generator: GT's getFuelValue truncates per litre (acid: 10 x 95 / 100 = 9 EU/L,
    # never 9.5), so the line plans 640 / 9 L/s, not 640 / 9.5.
    c = _writable_copy(db, tmp_path)
    try:
        c.execute("UPDATE generator SET efficiency = 95")
        gens, _ = power.candidates(c, 1, power.PowerRequest(free=FREE), 2)
        by_fuel = {g.fuel: g for g in gens}
        assert by_fuel[ACID].fuel_per_s == pytest.approx(640 / 9)
        assert by_fuel[ACID].eu_per_fuel == pytest.approx(9.0)
        assert by_fuel[GAS].fuel_per_s == pytest.approx(640 / 38)
        # The validator derives the same rate on its own.
        g = by_fuel[ACID]
        node = {"recipe": g.id, "spec": dict(g.spec)}
        p, why = sustain.gen_ports(c, node)
        assert why is None and p is not None
        assert p.used == {ACID: pytest.approx(640 / 9)}
        # 10 x 9 / 100 = 0 EU/L: GT burns nothing for it, so no candidate.
        c.execute("UPDATE generator SET efficiency = 9")
        gens, notes = power.candidates(c, 1, power.PowerRequest(free=FREE), 2)
        assert {g.fuel for g in gens} == {GAS}  # 40 x 9 / 100 = 3
        assert any("0 EU per unit" in n for n in notes)
    finally:
        c.close()


def test_the_most_efficient_single_block_for_sustain_and_effort(
    db: Path, tmp_path: Path
) -> None:
    # An MV copy of the generator at 85 %: best and max_output take the highest tier;
    # self_sustaining and output_per_effort also keep the 100 % LV one (verdict 11).
    c = _writable_copy(db, tmp_path)
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(machine)")][1:]
        row = c.execute(
            f"SELECT {', '.join(cols)} FROM machine WHERE mte_id = ?", (GENERATOR,)
        ).fetchone()
        vals = dict(zip(cols, row, strict=True)) | {"mte_id": 112, "tier": 2}
        marks = ", ".join("?" * len(cols))
        c.execute(
            f"INSERT INTO machine ({', '.join(cols)}) VALUES ({marks})",
            [vals[k] for k in cols],
        )
        new_id = c.execute("SELECT id FROM machine WHERE mte_id = 112").fetchone()[0]
        c.execute(
            "INSERT INTO machine_map SELECT ?, mm.recipe_map_id, mm.priority FROM"
            " machine_map AS mm JOIN machine AS m ON m.id = mm.machine_id"
            " WHERE m.mte_id = ?",
            (new_id, GENERATOR),
        )
        c.execute(
            "INSERT INTO generator SELECT run_id, 112, class_name, 2, 85, 128, 1,"
            " recipe_map FROM generator WHERE mte_id = ?",
            (GENERATOR,),
        )
        for mode, want in (
            ("best", {112}),
            ("max_output", {112}),
            ("self_sustaining", {GENERATOR, 112}),
            ("output_per_effort", {GENERATOR, 112}),
        ):
            gens, _ = power.candidates(
                c, 1, power.PowerRequest(mode=mode, free=FREE), 2
            )
            assert {g.machines[0].mte for g in gens} == want, mode
    finally:
        c.close()


def test_raw_budget_sizes_a_closed_line_like_the_best_open_one(
    conn: sqlite3.Connection, no_farms: Path
) -> None:
    # The two lines that import take 115.2 and 230.4 per hour: a budget of 1000 gives 8 and 4
    # units. The closed line (water only) gets 8, never 1 (LOW-2).
    d = design(conn, no_farms, mode="max_output", scale="raw_budget", bound=1000)
    closed = [p for p in d.options if [b["id"] for b in p["boundary"]["in"]] == [WATER]]
    assert [p["power"]["units"] for p in closed] == [8]
    assert any("as many as the best line" in w for w in closed[0]["warnings"])
    assert d.options[0]["power"]["units"] == 8


def test_two_routes_that_differ_only_at_zero_flow_are_one_line() -> None:
    """Review M2: a route forced off a choice that runs at zero flow has another plan id but
    the same generator, fuel, recipes and rates; it is kept once, the first one."""
    from types import SimpleNamespace

    def opt(crafts: tuple[tuple[str, float], ...]) -> Any:  # noqa: ANN401
        return SimpleNamespace(
            gen=SimpleNamespace(id="g1", fuel="fluid:x"),
            units=1.0,
            flow=SimpleNamespace(crafts=crafts),
        )

    a = opt((("r:a", 0.5), ("r:b", 0.0)))
    b = opt((("r:a", 0.5), ("r:c", 0.0)))  # the other choice, unused
    c = opt((("r:a", 0.25), ("r:c", 0.25)))  # really another route
    assert power.signature(a) == power.signature(b) != power.signature(c)
    plans = [{"id": "pw-1"}, {"id": "pw-2"}, {"id": "pw-3"}, {"id": "pw-1"}]
    sigs = {
        "pw-1": power.signature(a),
        "pw-2": power.signature(b),
        "pw-3": power.signature(c),
    }
    assert [p["id"] for p in power.distinct(plans, sigs)] == ["pw-1", "pw-3"]
