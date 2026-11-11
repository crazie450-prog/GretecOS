"""Routes as canonical plan JSON (WO-3.4) on the WO-3.3 fixture DB (tests/unit/planner/test_graph.py),
plus two made-up additions for this file: a second way to make the test gas (``mixer/7``, so a
non-primary node has an alternative to pin) and an addon mixer (``mte:106``, mod ``testaddon``)
for the side-by-side addon column (D12). Every item, recipe and machine is made up; no pack data.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import math
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import jsonschema
import pytest

from gregtec.db import access
from gregtec.packupdate import plans as pack_plans
from gregtec.planner import alternatives, chains, plan, plancli, sizing, solve
from gregtec.planner.gtmath.processing import EnergyHatch
from gregtec.planner.machines import Cited, MachineOption, Sizing
from gregtec.planner.request import Request, RequestError


def _graph_fixture() -> ModuleType:
    path = Path(__file__).with_name("test_graph.py")
    spec = importlib.util.spec_from_file_location("_wo33_graph_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tg = _graph_fixture()
WIDGET, SALT, GAS = tg.WIDGET, tg.SALT, tg.GAS
ADDON_MIXER = 106


def make_home(home: Path) -> Path:
    """The WO-3.3 fixture run plus mixer/7 (Dust B -> test gas) and an addon MV mixer."""
    recipes, machines = tg.recipes, tg.machines

    def more_recipes() -> list[dict[str, object]]:
        return [
            *recipes(),
            tg.gt("mixer", 7, 16, 50, ins=[tg.ref(4)], fout=[tg.fl("testgas", 500)]),
        ]

    def more_machines() -> list[dict[str, object]]:
        out = machines()
        addon = dict(out[0])
        addon.update(
            mte_id=ADDON_MIXER, meta=ADDON_MIXER, mod="testaddon", tier=2,
            name="Addon Mixer", meta_name="basicmachine.mixer.tier.02",
        )  # fmt: skip
        return [*out, addon]

    tg.recipes, tg.machines = more_recipes, more_machines
    try:
        return tg.make_home(home)
    finally:
        tg.recipes, tg.machines = recipes, machines


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_home(tmp_path_factory.mktemp("plan-home"))


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def design(
    conn: sqlite3.Connection, request: Request, exact: bool = False
) -> plan.Design:
    return plan.design(conn, request, tg.Base(), templates=chains.EMPTY, exact=exact)


def ref_of(conn: sqlite3.Connection, map_: str, idx: int) -> str:
    if (map_, idx) == ("mixer", 7):
        row = conn.execute(
            "SELECT r.ref FROM recipe AS r JOIN recipe_map AS m ON m.id = r.recipe_map_id"
            " WHERE m.unloc_name = 'gt.recipe.mixer' AND r.duration_ticks = 50"
        ).fetchone()
        return str(row[0])
    return str(tg.ref_of(conn, map_, idx))


SCHEMA = json.loads(plan.SCHEMA.read_text(encoding="utf-8"))


def valid(p: dict[str, object]) -> None:
    jsonschema.Draft202012Validator(SCHEMA).validate(p)


# --- routes, numbers, the JSON ------------------------------------------------------------


def test_three_routes_differ_in_a_primary_choice(conn: sqlite3.Connection) -> None:
    d = design(conn, Request(WIDGET, qty=2))
    assert len(d.plans) == 3
    choices = [
        tuple((c["node"], c["recipe"]) for c in p["route"]["choices"]) for p in d.plans
    ]
    assert len(set(choices)) == 3
    for a in choices:
        for b in choices:
            if a != b:
                assert set(a) != set(b)
    targets = [dict(c)[WIDGET] for c in choices]
    assert targets[0] == ref_of(conn, "chemicalreactor", 0) or len(set(targets)) == 3
    ids = [p["id"] for p in d.plans]
    for p in d.plans:
        valid(p)
        assert p["run"] == tg.RUN and p["gt"] == "5.09.54.265" and p["v"] == 1
        assert p["kind"] == "production" and p["tier_cap"] == "MV"
        assert sorted(p["alternatives"]) == sorted(i for i in ids if i != p["id"])
        assert p["route"]["all_run"] and p["route"]["gates"] == []
        assert p["metrics"] == {} and p["rank"] == {} and p["layout"] == {}
        assert p["request"] == Request(WIDGET, qty=2).as_dict()
    assert d.plans[0]["name"] == "Widget line · 2/s · MV"
    assert d.plans[1]["name"].endswith("· route 2")


def test_node_numbers_trace_to_recipes_and_machines(conn: sqlite3.Connection) -> None:
    d = design(conn, Request(WIDGET, qty=2))
    for p in d.plans:
        for n in p["nodes"]:
            assert n["recipe"].startswith("r:")
            m = n["machine"]
            assert m is not None and m["kind"] == "singleblock" and not m["addon"]
            cps = n["parallel"] * 20 / n["ticks_eff"]
            assert n["count"] == math.ceil(round(n["crafts_s"] / cps, 9))
            assert n["util"] == pytest.approx(n["crafts_s"] / (n["count"] * cps))
            assert n["eut_avg"] == pytest.approx(n["eut"] * n["crafts_s"] / cps)
            assert n["eut_peak"] == n["eut"] * n["count"]
            assert n["steady"] is None and n["badges"] == []
        cites = plan.cites(p)
        assert all(c.startswith(("r:", "mte:")) for c in cites)
        assert {n["recipe"] for n in p["nodes"]} <= set(cites)


def test_flows_balance(conn: sqlite3.Connection) -> None:
    """The acid loop route: every item's makers equal its users plus net exports."""
    d = design(conn, Request(WIDGET, qty=2))
    loop = next(
        p for p in d.plans if any(n["for"] == ["fluid:testacid"] for n in p["nodes"])
    )
    by_n = {n["n"]: n for n in loop["nodes"]}
    into = {(e["to"], e["item"]): e["rate_s"] for e in loop["edges"]}
    acid = sum(v for (to, item), v in into.items() if item == "fluid:testacid")
    maker = next(n for n in loop["nodes"] if n["for"] == ["fluid:testacid"])
    assert acid == pytest.approx(maker["crafts_s"] * 2000)
    assert all(e["from"] in by_n and e["to"] in by_n for e in loop["edges"])
    assert loop["target"]["rate_s"] == pytest.approx(2.0)


def test_twelve_identical_designs(conn: sqlite3.Connection) -> None:
    first = [plan.dumps(p) for p in design(conn, Request(WIDGET, qty=2)).plans]
    for _ in range(11):
        assert [
            plan.dumps(p) for p in design(conn, Request(WIDGET, qty=2)).plans
        ] == first


def test_canonical_text(conn: sqlite3.Connection) -> None:
    p = design(conn, Request(WIDGET, routes=1)).plans[0]
    text = plan.dumps(p)
    assert json.loads(text) == p and " " not in text.split('"name"')[0]
    assert plan.dumps(json.loads(text)) == text


# --- pins and alternatives (D61) ----------------------------------------------------------


def test_pin_on_the_target_fixes_the_choice(conn: sqlite3.Connection) -> None:
    electro = ref_of(conn, "electrolyzer", 0)
    d = design(conn, alternatives.pin(Request(WIDGET), WIDGET, electro))
    assert len(d.plans) == 1  # the only primary choice with options is pinned
    p = d.plans[0]
    assert p["pins"] == [{"node": WIDGET, "recipe": electro}]
    assert [n["recipe"] for n in p["nodes"]] == [electro]
    assert p["nodes"][0]["pinned"] and p["nodes"][0]["primary"]
    valid(p)


def test_pinning_a_non_primary_node_changes_only_its_branch(
    conn: sqlite3.Connection,
) -> None:
    acid_route = alternatives.pin(
        Request(WIDGET), WIDGET, ref_of(conn, "chemicalreactor", 0)
    )
    before = design(conn, acid_route)
    assert GAS not in before.graph.primary
    options = alternatives.node_alternatives(before.graph, GAS)
    other = next(a["recipe"] for a in options if a["status"] != "chosen")
    after = design(conn, alternatives.pin(acid_route, GAS, other))
    branch = alternatives.branch(before.graph, GAS) | alternatives.branch(
        after.graph, GAS
    )
    old = {n["recipe"]: n for n in before.plans[0]["nodes"]}
    new = {n["recipe"]: n for n in after.plans[0]["nodes"]}
    changed = set(old) ^ set(new)
    assert changed, "the pin changed nothing"
    for ref in changed:
        assert set((old.get(ref) or new[ref])["for"]) <= branch
    assert other in new and new[other]["pinned"]
    assert {"node": GAS, "recipe": other} in after.plans[0]["pins"]


def test_alternatives_list_and_show_all(conn: sqlite3.Connection) -> None:
    g = design(conn, Request(WIDGET)).graph
    shown = alternatives.node_alternatives(g, WIDGET)
    everything = alternatives.node_alternatives(g, WIDGET, show_all=True)
    assert {a["status"] for a in shown} <= {"chosen", "alternative", "pruned"}
    assert len(everything) > len(shown)
    assert any(a["status"] == "rejected" and a["reason"] for a in everything)
    with pytest.raises(KeyError):
        alternatives.node_alternatives(g, "test:item@999")


def test_unpin(conn: sqlite3.Connection) -> None:
    r = alternatives.pin(Request(WIDGET), WIDGET, ref_of(conn, "electrolyzer", 0))
    assert alternatives.unpin(r, WIDGET).pins == ()
    with pytest.raises(RequestError):
        alternatives.unpin(Request(WIDGET), WIDGET)


# --- batch mode, exact counts, the addon column --------------------------------------------


def test_batch_mode(conn: sqlite3.Connection) -> None:
    d = design(conn, Request(SALT, mode="batch", qty=64, within_s=600))
    p = d.plans[0]
    valid(p)
    t = p["target"]
    assert t["qty"] == 64 and t["window_s"] == 600
    assert t["rate_s"] == pytest.approx(64 / 600)
    salt = next(n for n in p["nodes"] if n["for"] == [SALT])
    assert salt["crafts"] == pytest.approx(64.0)  # one salt per craft
    for n in p["nodes"]:
        assert n["crafts"] == pytest.approx(n["crafts_s"] * 600)
    assert 0 < t["eta_s"] <= 600
    hour = design(conn, Request(SALT, mode="batch", qty=64)).plans[0]
    assert hour["target"]["window_s"] == 3600  # D14: qty per hour by default


def test_exact_counts_never_exceed_the_ceiling(conn: sqlite3.Connection) -> None:
    ceil = design(conn, Request(WIDGET, qty=2.5))
    exact = design(conn, Request(WIDGET, qty=2.5), exact=True)
    for a, b in zip(ceil.plans, exact.plans, strict=True):
        assert b["route"]["exact"] and not a["route"]["exact"]
        for na, nb in zip(a["nodes"], b["nodes"], strict=True):
            assert nb["count"] <= na["count"]
            cps = nb["parallel"] * 20 / nb["ticks_eff"]
            assert nb["count"] * cps >= nb["crafts_s"] - 1e-9


def test_addon_column(conn: sqlite3.Connection) -> None:
    d = design(conn, Request(WIDGET, machines="base+addons", qty=2))
    columns = [p["route"]["column"] for p in d.plans]
    assert columns[-1] == "addon" and columns.count("addon") == 1
    addon = d.plans[-1]
    valid(addon)
    assert addon["machines"] == "base+addons" and addon["name"].endswith("· addons")
    used = [
        n["machine"] for n in addon["nodes"] if n["machine"] and n["machine"]["addon"]
    ]
    assert used and all(
        m["mte"] == ADDON_MIXER and m["confidence"] == 1.0 for m in used
    )
    for p in d.plans[:-1]:
        assert not any(n["machine"]["addon"] for n in p["nodes"] if n["machine"])


# --- sizing ---------------------------------------------------------------------------------


def _sizing(state: str, duration: int, eut: int = 30) -> Sizing:
    def c(v: float) -> Cited:
        return Cited(v, ("r:x/000000000000",))

    return Sizing(state, c(eut), c(duration), c(1), c(0), c(20 / duration), c(1))


def _option(**kw: object) -> MachineOption:
    base: dict[str, object] = {
        "recipe": "r:x/000000000000", "mte_id": 1, "name": "m", "kind": "singleblock",
        "mod": "gregtech", "addon": False, "tier": 1, "hatches": (), "coil_tier": None,
        "min_coil_tier": None, "batch_mode": False, "batch_supported": False,
        "requires": (), "owned": False, "badges": (), "notes": (), "facts": (),
        "sizing": _sizing("fixed", 100),
    }  # fmt: skip
    base.update(kw)
    return MachineOption(**base)  # type: ignore[arg-type]


def test_pick_order() -> None:
    single_lv = _option(mte_id=1, tier=1)
    single_hv = _option(mte_id=2, tier=3)
    multi = _option(mte_id=3, kind="multiblock", tier=3, hatches=(EnergyHatch(3),))
    badged = _option(mte_id=4, tier=4, badges=("flagged",))
    addon = _option(mte_id=5, tier=1, addon=True)
    steam = _option(mte_id=6, kind="steam", tier=0)
    opts = (single_lv, single_hv, multi, badged, addon, steam)
    assert sizing.pick(opts) is single_hv  # unbadged single block, highest tier
    assert sizing.pick(opts, addon=True) is addon
    assert sizing.pick((multi, steam)) is multi
    assert sizing.pick((badged,)) is badged  # kept, with its badge (D142 (a))
    assert sizing.pick((addon,)) is None


def test_node_size_worst_and_steady() -> None:
    worst = _sizing("worst", 200)  # 0.1 crafts/s
    steady = _sizing("steady", 100)  # 0.2 crafts/s
    w = sizing.node_size(worst, 0.25)
    s = sizing.node_size(steady, 0.25)
    assert (w.count, s.count) == (3, 2)
    assert w.util == pytest.approx(0.25 / 0.3) and s.util == pytest.approx(0.25 / 0.4)
    assert w.eut_avg == pytest.approx(30 * 2.5) and w.eut_peak == 90
    assert sizing.node_size(worst, 0.3).count == 3  # no float noise past the ceiling
    assert sizing.node_size(worst, 0.0).count == 0
    assert sizing.node_size(worst, 0.25, count=4).util == pytest.approx(0.25 / 0.4)


def test_network_skips_catalysts_and_merges_outputs(conn: sqlite3.Connection) -> None:
    d = design(conn, Request(WIDGET))
    sized = {r.id: sizing.Sized(r.id, None) for r in d.graph.recipes}
    net = plan.network(d.graph, Request(WIDGET), sized, base_only=False)
    mixer = net.proc(ref_of(conn, "mixer", 0))
    assert all(k != "gregtech:gt.integrated_circuit@1" for k, _ in mixer.inputs)
    assert net.targets == ((WIDGET, 1.0),)
    assert any(k == WIDGET for k, _ in net.choices)


def test_unsolvable_target_is_refused() -> None:
    net = solve.Network(procs=(), targets=(("x", 1.0),))
    with pytest.raises(solve.SolveError):
        solve.routes(net)


# --- WO-1.9's affected-plan check reads the plan JSON ---------------------------------------


def test_pack_update_reads_the_plan(conn: sqlite3.Connection, tmp_path: Path) -> None:
    p = design(conn, Request(WIDGET, qty=2)).plans[1]
    path = tmp_path / f"{p['id']}.json"
    path.write_text(plan.dumps(p), encoding="utf-8")
    read = pack_plans.read_plan(path)
    assert (read.id, read.name, read.run, read.status) == (
        p["id"],
        p["name"],
        tg.RUN,
        "",
    )
    assert read.cites == plan.cites(p)
    assert any(c.startswith("mte:") for c in read.cites)
    with pytest.raises(ValueError, match="isn't a gos-plan/0 plan or a v1 plan"):
        pack_plans.parse(json.dumps({"v": 2, "nodes": []}).encode(), "x")


# --- gos plan ---------------------------------------------------------------------------------


def run_cli(home: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> tuple[int, str]:
    monkeypatch.setenv("GOS_HOME", str(home))
    parser = argparse.ArgumentParser()
    plancli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    return plancli.run(parser.parse_args(["plan", *argv]), out), out.getvalue()


def test_cli(db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = db.parent.parent
    code, text = run_cli(home, monkeypatch, "widget", "--tier", "MV", "--qty", "2/s")
    assert (
        code == 0
        and "Widget line · 2/s · MV" in text
        and "every machine runs: True" in text
    )
    code, text = run_cli(
        home, monkeypatch, WIDGET, "--tier", "MV", "--json", "--routes", "1"
    )
    lines = text.splitlines()
    assert code == 0 and len(lines) == 1
    valid(json.loads(lines[0]))
    code, text = run_cli(
        home, monkeypatch, WIDGET, "--tier", "2", "--batch", "64", "--within", "10min",
        "--out", str(tmp_path / "plans"),
    )  # fmt: skip
    assert code == 0
    written = sorted((tmp_path / "plans").glob("*.json"))
    assert (
        written
        and json.loads(written[0].read_text(encoding="utf-8"))["target"]["qty"] == 64
    )
    code, text = run_cli(
        home, monkeypatch, WIDGET, "--tier", "2", "--alternatives", WIDGET
    )
    assert code == 0 and text.startswith("chosen")
    code, text = run_cli(
        home, monkeypatch, WIDGET, "--tier", "2", "--alternatives", WIDGET, "--all"
    )
    assert "rejected" in text
    code, text = run_cli(home, monkeypatch, WIDGET, "--pin", "nonsense")
    assert code == 2 and "ITEM=RECIPE" in text
    code, text = run_cli(home, monkeypatch, "dust")
    assert code == 3
    request = tmp_path / "request.json"
    request.write_text(
        Request(WIDGET, tier_cap=2, routes=1).canonical(), encoding="utf-8"
    )
    code, text = run_cli(home, monkeypatch, "--request", str(request))
    assert code == 0 and text.count("[base]") == 1
    code, text = run_cli(home, monkeypatch)
    assert code == 2
