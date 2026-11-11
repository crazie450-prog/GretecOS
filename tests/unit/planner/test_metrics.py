"""Route metrics, the effort model and planner explanations (WO-3.5, plan §7.1 steps 7, 8 and
11, §7.2's effort model, D14, D17, D62, D196-D199) on the WO-3.4 fixture DB
(tests/unit/planner/test_plan.py) plus, for the effort model, an assembler that makes the
electrolyzer's machine item, and two crafting-table recipes (NEI's shaped handler) that make the
assembler and the mixer. Every item, recipe and machine is made up; no pack data.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from gregtec.db import access
from gregtec.planner import (
    chains,
    effort,
    explain,
    graph,
    metrics,
    plan,
    rank,
    validate,
)
from gregtec.planner.request import Request


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_wo35_{name}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tp = _load("test_plan")
tg = tp.tg
WIDGET, PLATE, DUST_B = tg.WIDGET, tg.PLATE, tg.DUST_B
MACHINE = "gregtech:gt.blockmachines"
ELECTROLYZER, MIXER, REACTOR, ASSEMBLER = 103, 101, 102, 107
ASSEMBLER_S = 200 / 20  # the fixture's assembler recipe: 200 ticks at LV, no overclock
SHAPED = "codechicken.nei.recipe.ShapedRecipeHandler"


def _stack(meta: int, item: str = tg.T) -> dict[str, object]:
    return {"Count": 1, "Damage": meta, "strId": item, "name": f"{item}@{meta}"}


def make_home(home: Path) -> Path:
    """test_plan's fixture plus the assembler (map, machine, recipe) and two shaped recipes."""
    recipes, machines, maps = tg.recipes, tg.machines, tg.MAPS

    def more_recipes() -> list[dict[str, object]]:
        return [
            *recipes(),
            tg.gt("assembler", 0, 30, 200, ins=[tg.ref(2, 4), tg.ref(4)],
                  outs=[tg.ref(ELECTROLYZER, 1, MACHINE)]),
        ]  # fmt: skip

    def more_machines() -> list[dict[str, object]]:
        out = machines()
        assembler = dict(out[0])
        assembler.update(
            mte_id=ASSEMBLER, meta=ASSEMBLER, name="Test assembler 1",
            meta_name="basicmachine.assembler.tier.01", recipe_maps=["gt.recipe.assembler"],
        )  # fmt: skip
        return [*out, assembler]

    shaped = [
        {"handler": SHAPED, "ingredients": [_stack(2)] * 8,
         "results": [_stack(ASSEMBLER, MACHINE)]},
        {"handler": SHAPED, "ingredients": [_stack(4)] * 8,
         "results": [_stack(MIXER, MACHINE)]},
    ]  # fmt: skip
    nei = {"nei/Shaped Crafting.jsonl": tg.jsonl(shaped)}
    tg.recipes, tg.machines, tg.MAPS = more_recipes, more_machines, (*maps, "assembler")
    try:
        return _with_nei(home, nei)
    finally:
        tg.recipes, tg.machines, tg.MAPS = recipes, machines, maps


def _with_nei(home: Path, nei: Mapping[str, bytes]) -> Path:
    # test_plan.make_home adds mixer/7 and the addon mixer around tg.make_home; do the same
    # with the NEI file passed through.
    original = tg.make_home
    tg.make_home = lambda h: original(h, nei)
    try:
        return Path(tp.make_home(home))
    finally:
        tg.make_home = original


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_home(tmp_path_factory.mktemp("metrics-home"))


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def design(
    conn: sqlite3.Connection,
    request: Request,
    owned: Mapping[int, int] | None = None,
    prefs: rank.Prefs = rank.BALANCED,
) -> plan.Design:
    return plan.design(
        conn,
        request,
        tg.Base(owned=dict(owned or {})),
        templates=chains.EMPTY,
        prefs=prefs,
    )


def with_mte(d: plan.Design, mte: int) -> dict[str, Any]:
    return next(
        p for p in d.plans if any(t["mte"] == mte for t in p["metrics"]["by_type"])
    )


def rows(p: Mapping[str, Any], part: str) -> dict[str, dict[str, Any]]:
    return {r["map"]: r for r in p["metrics"]["effort"]["rows"] if r["part"] == part}


# --- the effort model (§7.2, D17, D196) ------------------------------------------------------


def test_build_effort_counts_each_component_recipe(conn: sqlite3.Connection) -> None:
    p = with_mte(design(conn, Request(WIDGET, qty=2)), ELECTROLYZER)
    m = p["metrics"]
    n = next(t["count"] for t in m["by_type"] if t["mte"] == ELECTROLYZER)
    comps = m["effort"]["components"]
    assert {"id": f"{MACHINE}@{ELECTROLYZER}", "qty": n, "for": f"mte:{ELECTROLYZER}",
            "part": "machine"} in comps  # fmt: skip
    build = rows(p, "build")
    a = build["gt.recipe.assembler"]
    assert a["crafts"] == n and a["machines"] == 1 and a["mte"] == ASSEMBLER
    assert a["h"] == pytest.approx(n * ASSEMBLER_S / 3600, rel=1e-6)
    # 4 plates per electrolyzer from the mixer (1 Dust A each), whole crafts.
    assert build["gt.recipe.mixer"]["crafts"] >= 4 * n
    assert m["build_h"] == pytest.approx(
        math.fsum(r["h"] for r in build.values()), rel=1e-6
    )
    # No owned machines listed: every map counts as one machine he has, nothing to unlock.
    assert m["unlock_h"] == 0 and m["effort"]["unlocks"] == []
    assert m["eff_h"] == pytest.approx(m["build_h"] + m["unlock_h"], rel=1e-6)
    assert any("unlock_h is 0" in note for note in m["effort"]["notes"])


def test_effort_is_spread_over_owned_machines(conn: sqlite3.Connection) -> None:
    owned = {ELECTROLYZER: 1, MIXER: 2, REACTOR: 1, ASSEMBLER: 3}
    p = with_mte(design(conn, Request(WIDGET, qty=2), owned), ELECTROLYZER)
    n = next(t["count"] for t in p["metrics"]["by_type"] if t["mte"] == ELECTROLYZER)
    build = rows(p, "build")
    assert build["gt.recipe.assembler"]["machines"] == 3
    assert build["gt.recipe.assembler"]["h"] == pytest.approx(
        n * ASSEMBLER_S / 3 / 3600, rel=1e-6
    )
    assert build["gt.recipe.mixer"]["machines"] == 2
    assert p["metrics"]["effort"]["unlocks"] == [] and p["metrics"]["unlock_h"] == 0


def test_unlocks_walk_missing_maps_once_with_crafting_steps(
    conn: sqlite3.Connection,
) -> None:
    craft = rank.parse_prefs("craft_s: 5\n")
    p = with_mte(
        design(conn, Request(WIDGET, qty=2), {ELECTROLYZER: 1, REACTOR: 1}, craft),
        ELECTROLYZER,
    )
    m = p["metrics"]
    # The assembler (to make electrolyzers) and the mixer (plates) are unlocked, each once,
    # though the assembler's own plates need the mixer too (de-duplicated).
    assert sorted(m["effort"]["unlocks"]) == [f"mte:{MIXER}", f"mte:{ASSEMBLER}"]
    unlock = rows(p, "unlock")
    hand = unlock[effort.HAND]
    assert hand["crafts"] == 2 and hand["mte"] is None  # one shaped craft per machine
    assert hand["h"] == pytest.approx(2 * 5 / 3600, rel=1e-6)
    assert m["effort"]["craft_s"] == 5
    assert m["unlock_h"] == pytest.approx(
        math.fsum(r["h"] for r in unlock.values()), rel=1e-6
    )
    assert m["eff_h"] == pytest.approx(m["build_h"] + m["unlock_h"], rel=1e-6)


def test_steps_counts_whole_crafts() -> None:
    w = effort.Walk()
    w.add(effort.Walk({"m": 2}, {"m": 3.0}, {"m": 7}, ["x"]))
    w.add(
        effort.Walk(
            {"m": 1, effort.HAND: 4}, {"m": 1.0, effort.HAND: 8.0}, {"m": 9}, ["x"]
        )
    )
    assert w.crafts == {"m": 3, effort.HAND: 4} and w.seconds == {
        "m": 4.0,
        effort.HAND: 8.0,
    }
    assert w.machine == {"m": 7} and w.missing == ["x"]


# --- route metrics (§7.1 step 7) -------------------------------------------------------------


def test_machines_new_types_and_eu(conn: sqlite3.Connection) -> None:
    d = design(conn, Request(WIDGET, qty=2), {MIXER: 1})
    for p in d.plans:
        m = p["metrics"]
        nodes = [n for n in p["nodes"] if n["machine"]]
        assert m["machines"] == sum(n["count"] for n in nodes)
        assert sum(t["count"] for t in m["by_type"]) == m["machines"]
        mtes = sorted({n["machine"]["mte"] for n in nodes})
        assert m["new_types"] == [f"mte:{x}" for x in mtes if x != MIXER]
        assert [t["new"] for t in m["by_type"]] == [
            t["mte"] != MIXER for t in m["by_type"]
        ]
        assert m["eut_avg"] == pytest.approx(math.fsum(n["eut_avg"] for n in nodes))
        assert m["eut_peak"] == sum(n["eut_peak"] for n in nodes)
        for e in m["eu"]:
            at = [n for n in nodes if n["machine"]["tier"] == e["tier"]]
            assert e["amps"] == sum(n["count"] * n["amps"] for n in at)
        assert m["footprint"] is None
        assert m["tier"] in {"LV", "MV"}


def test_raw_inputs_byproducts_per_hour_and_scarcity(conn: sqlite3.Connection) -> None:
    p = with_mte(design(conn, Request(WIDGET, qty=2)), ELECTROLYZER)
    m = p["metrics"]
    rates = {b["id"]: b["rate_s"] for b in p["boundary"]["in"]}
    for r in m["raw_in_h"]:
        assert r["per_h"] == pytest.approx(rates[r["id"]] * 3600)
    assert WIDGET not in {r["id"] for r in m["raw_in_h"] + m["byproducts_h"]}
    # The repo table weighs no fixture material; a table that weighs Testium does.
    assert m["scarcity"] == 0 and m["scarce"] == []
    ore = next(r for r in m["raw_in_h"] if r["id"] == tg.ORE_A)
    table = {
        "material:Testium": metrics.Weight("material:Testium", 0.5, "test", "test")
    }
    mats = metrics.materials(conn, [ore["id"]])
    assert mats == {tg.ORE_A: ("Testium", 1.0)}
    score, scarce = metrics.scarcity(m["raw_in_h"], table, mats)
    assert score == pytest.approx(ore["per_h"] * 0.5)
    assert scarce[0]["by"] == "material:Testium"


def test_scarcity_prefers_an_explicit_key_and_reads_fluids_per_1000_l() -> None:
    w = metrics.Weight
    table = {
        "test:item@5": w("test:item@5", 1.0, "t", "t"),
        "material:Testium": w("material:Testium", 0.5, "t", "t"),
        "fluid:testacid": w("fluid:testacid", 0.2, "t", "t"),
    }
    mats = {"test:item@5": ("Testium", 2.0), "test:item@3": ("Testium", 0.25)}
    ins = [
        {"id": "test:item@5", "per_h": 10.0},
        {"id": "test:item@3", "per_h": 8.0},
        {"id": "fluid:testacid", "per_h": 5000.0},
        {"id": "test:item@9", "per_h": 99.0},
    ]
    score, scarce = metrics.scarcity(ins, table, mats)
    assert [s["score"] for s in scarce] == [10.0, 1.0, 1.0]
    assert score == 12.0


def test_scarcity_table_loads_and_is_checked(tmp_path: Path) -> None:
    table = metrics.load_scarcity()
    assert table["material:Naquadria"].weight == 1
    assert all(w.source and w.rationale and 0 <= w.weight <= 1 for w in table.values())
    bad = [
        ("key\tweight\n", "header"),
        ("key\tweight\tsource\trationale\nx\t1\ts\tr\n", "stable key"),
        ("key\tweight\tsource\trationale\nmaterial:A\t2\ts\tr\n", "between 0 and 1"),
        ("key\tweight\tsource\trationale\nmaterial:A\tx\ts\tr\n", "not a number"),
        ("key\tweight\tsource\trationale\nmaterial:A\t1\t\tr\n", "non-empty"),
        (
            "key\tweight\tsource\trationale\nmaterial:A\t1\ts\tr\nmaterial:A\t1\ts\tr\n",
            "twice",
        ),
    ]
    for text, why in bad:
        path = tmp_path / "s.tsv"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match=why):
            metrics.load_scarcity(path)


def test_complexity_counts_nodes_fluids_and_loops(conn: sqlite3.Connection) -> None:
    d = design(conn, Request(WIDGET, qty=2))
    for p in d.plans:
        c = p["metrics"]["complexity"]
        assert c["nodes"] == len(p["nodes"])
        assert c["score"] == c["nodes"] + c["fluids"] + c["loops"]

    # A loop: acid from gas and water, gas back from acid (made-up recipe nodes).
    def node(ref: str, ins: list[str], outs: list[str]) -> graph.RecipeNode:
        return graph.RecipeNode(
            ref, "gt.recipe.test", "gt", 30, 20, 1,
            tuple(graph.Port(k, 1000) for k in ins), tuple(graph.Port(k, 1000) for k in outs),
            (1,), True, (), (),
        )  # fmt: skip

    loop = {
        "r:a": node("r:a", [tg.GAS, tg.WATER], [tg.ACID]),
        "r:b": node("r:b", [tg.ACID], [tg.GAS]),
    }
    g = SimpleNamespace(recipe=loop.__getitem__)
    fake = {
        "nodes": [{"recipe": r} for r in loop],
        "edges": [{"item": tg.GAS}, {"item": tg.ACID}],
        "boundary": {"in": [{"id": tg.WATER}], "out": []},
        "target": {"id": WIDGET},
    }
    assert metrics.complexity(g, fake) == {  # type: ignore[arg-type]
        "nodes": 2, "fluids": 3, "loops": 1, "score": 6,
    }  # fmt: skip


def test_batch_totals_are_rate_times_window(conn: sqlite3.Connection) -> None:
    request = Request(WIDGET, mode="batch", qty=64, within_s=1800)
    for p in design(conn, request).plans:
        b = p["metrics"]["batch"]
        assert b["qty"] == 64 and b["window_s"] == 1800
        assert b["eta_s"] == p["target"]["eta_s"] and b["eta_h"] == pytest.approx(
            b["eta_s"] / 3600
        )
        rates = {x["id"]: x["rate_s"] for x in p["boundary"]["in"]}
        for x in b["inputs"]:
            assert x["qty"] == pytest.approx(rates[x["id"]] * 1800, rel=1e-3)
        assert b["crafts"] == pytest.approx(
            sum(n["crafts_s"] for n in p["nodes"]) * 1800, rel=1e-3
        )
        assert b["eu"] == pytest.approx(
            sum(n["eut_avg"] or 0 for n in p["nodes"]) * 20 * 1800, rel=1e-3
        )


def test_rate_mode_has_no_batch(conn: sqlite3.Connection) -> None:
    assert all("batch" not in p["metrics"] for p in design(conn, Request(WIDGET)).plans)


# --- ranking in the design (§7.1 step 8) ------------------------------------------------------


def test_design_ranks_routes_and_names_them_by_place(conn: sqlite3.Connection) -> None:
    d = design(conn, Request(WIDGET, qty=2))
    scores = [p["rank"]["score"] for p in d.plans]
    assert scores == sorted(scores, reverse=True)
    assert [p["rank"]["position"] for p in d.plans] == [1, 2, 3]
    assert d.plans[0]["name"] == "Widget line · 2/s · MV"
    assert d.plans[2]["name"] == "Widget line · 2/s · MV · route 3"
    for p in d.plans:
        tp.valid(p)
        assert p["rank"]["weights"] == "balanced"


def test_same_request_same_order_and_bytes(conn: sqlite3.Connection) -> None:
    first = [plan.dumps(p) for p in design(conn, Request(WIDGET, qty=2)).plans]
    for _ in range(3):
        assert [
            plan.dumps(p) for p in design(conn, Request(WIDGET, qty=2)).plans
        ] == first


@pytest.mark.parametrize("target", [WIDGET, PLATE])
def test_addon_column_shows_machine_fact_confidence(
    conn: sqlite3.Connection, target: str
) -> None:
    """§8 row 3.5's addon criterion on the fixture: the dev runtime has no addon machines."""
    d = design(conn, Request(target, machines="base+addons"))
    addon = d.plans[-1]
    assert addon["rank"]["column"] == "addon"
    assert [p["rank"]["column"] for p in d.plans].count("addon") == 1
    types = [t for t in addon["metrics"]["by_type"] if t["addon"]]
    assert types and all(0 < t["confidence"] <= 1 for t in types)
    doc = explain.facts(d.plans, target, rank.BALANCED)
    text = explain.template(doc)
    assert "Addon column" in text and "confidence" in text


# --- explanations (§7.1 step 11, D199) -------------------------------------------------------


class FakeWriter:
    """A planner subagent stand-in: returns its replies in turn and keeps the prompts."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _ranked(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    d = design(conn, Request(WIDGET, qty=2))
    plans = list(d.plans)
    return plans, explain.facts(plans, "Widget", rank.BALANCED)


def test_facts_hold_only_codes_numbers_in_rank_order(conn: sqlite3.Connection) -> None:
    plans, doc = _ranked(conn)
    assert [r["id"] for r in doc["routes"]] == [p["id"] for p in plans]
    text = json.dumps(doc)
    assert 'nodes": [' not in text and "r:gt.recipe" not in text  # metrics only
    assert doc["weights"] == {"set": "balanced", **{m: 1.0 for m in rank.METRICS}}


def test_template_passes_the_validator(conn: sqlite3.Connection) -> None:
    for request in (Request(WIDGET, qty=2), Request(WIDGET, mode="batch", qty=64)):
        d = design(conn, request)
        doc = explain.facts(d.plans, "Widget", rank.BALANCED)
        text = explain.template(doc)
        result = validate.check(text, doc, explain.names(doc))
        assert result.ok, result.offending
        assert text.splitlines()[0].startswith("Ranked by code, weights (balanced)")


def test_template_with_injected_wrong_numbers_is_caught(
    conn: sqlite3.Connection,
) -> None:
    _, doc = _ranked(conn)
    text = explain.template(doc)
    m = doc["routes"][0]["metrics"]
    wrong = text.replace(
        f"{explain.fmt(m['machines'])} machines (", "999 machines (", 1
    )
    assert wrong != text
    assert validate.check(wrong, doc, explain.names(doc)).offending == ("999",)


def test_a_checked_model_text_is_kept(conn: sqlite3.Connection) -> None:
    plans, doc = _ranked(conn)
    m = doc["routes"][0]["metrics"]
    good = f"Route 1 is best: {m['machines']} machines and {m['nodes']} nodes."
    writer = FakeWriter(good)
    out = explain.explain(plans, "Widget", rank.BALANCED, writer)
    assert out == explain.Explanation(good, "model", (), 1)
    assert json.dumps(doc, sort_keys=True, separators=(",", ":")) in writer.prompts[0]
    assert "never reorder" in writer.prompts[0]


def test_a_rejected_text_is_retried_once_listing_the_numbers(
    conn: sqlite3.Connection,
) -> None:
    plans, doc = _ranked(conn)
    m = doc["routes"][0]["metrics"]
    good = f"It needs {m['machines']} machines."
    writer = FakeWriter("It needs 4321 machines and 77 hours.", good)
    out = explain.explain(plans, "Widget", rank.BALANCED, writer)
    assert out.source == "model-retry" and out.text == good and out.attempts == 2
    assert out.offending == (("4321", "77 hours"),)
    assert "4321, 77 hours" in writer.prompts[1] and "refused" in writer.prompts[1]


def test_two_rejections_fall_back_to_the_template(conn: sqlite3.Connection) -> None:
    plans, doc = _ranked(conn)
    writer = FakeWriter("It needs 4321 machines.", "Route 1 uses 5555 EU/t.")
    out = explain.explain(plans, "Widget", rank.BALANCED, writer)
    assert out.source == "template" and out.text == explain.template(doc)
    assert out.offending == (("4321",), ("5555",)) and out.attempts == 2
    assert explain.explain(plans, "Widget", rank.BALANCED, None).source == "template"


def test_an_empty_reply_is_rejected(conn: sqlite3.Connection) -> None:
    plans, _ = _ranked(conn)
    out = explain.explain(plans, "Widget", rank.BALANCED, FakeWriter("", "  "))
    assert out.source == "template" and out.offending == (("(empty)",), ("(empty)",))


@pytest.mark.parametrize(
    ("v", "text"),
    [(0, "0"), (12, "12"), (1234567, "1,234,567"), (2.0, "2"), (0.1674452, "0.1674"),
     (6.54321, "6.543"), (409071.959, "409,072"), (0.000123456, "0.0001235"), (12.5, "12.5")],
)  # fmt: skip
def test_fmt(v: float, text: str) -> None:
    assert explain.fmt(v) == text
