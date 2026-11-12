"""The Designer's service (WO-3.7) on the WO-3.4 fixture DB (tests/unit/planner/test_plan.py): design
and save, the route comparison card, alternatives, pin and re-solve, accept (vault notes and the
ledger stub) and rename. Every item, recipe and machine is made up; no pack data.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.cards import validate as card_problems
from gregtec.db import access
from gregtec.planner import designer, ledger
from gregtec.planner.outputs import store
from gregtec.planner.request import Request
from gregtec.vault import deploy as vault_deploy
from gregtec.vault import schema as vault_schema

REPO = Path(__file__).resolve().parents[3]


def _plan_fixture() -> ModuleType:
    path = Path(__file__).with_name("test_plan.py")
    spec = importlib.util.spec_from_file_location("_wo37_plan_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tp = _plan_fixture()
WIDGET, GAS = tp.WIDGET, tp.GAS


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tp.make_home(tmp_path_factory.mktemp("designer-db"))


@pytest.fixture
def home(db: Path, tmp_path: Path) -> Path:
    """A GOS_HOME of its own: the fixture DB copied, the vault deployed from the template."""
    h = tmp_path / "home"
    (h / "data").mkdir(parents=True)
    src = access.connect(db, readonly=True)
    dst = sqlite3.connect(h / "data" / "gos.sqlite")
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    return h


def check_card(card: dict[str, Any]) -> None:
    assert card_problems(card) == []


def ref_of(home: Path, map_: str, idx: int) -> str:
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        return str(tp.ref_of(conn, map_, idx))
    finally:
        conn.close()


def status(home: Path, pid: str) -> str:
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        return str(
            conn.execute("SELECT status FROM plan WHERE id = ?", (pid,)).fetchone()[0]
        )
    finally:
        conn.close()


def note_ok(home: Path, rel: str) -> str:
    text = (home / "vault" / rel).read_text(encoding="utf-8")
    types = vault_schema.load_types(home / "vault" / "_gos" / "schema.md")
    assert vault_schema.check_text(rel, text, types) == []
    return text


# --- design and save ---------------------------------------------------------------------------


def test_design_saves_every_route_in_rank_order(home: Path) -> None:
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    assert len(d.ids) == 3 and d.problems == []
    assert [p["rank"]["position"] for p in d.plans] == [1, 2, 3]
    for p in d.plans:
        assert (home / store.json_rel(p["id"])).is_file()
        assert status(home, p["id"]) == "proposed"
        note_ok(home, f"plans/{p['id']}.md")
    assert designer.latest(home) == d.ids
    # The same request on the same DB: the same ids, in the same order.
    assert designer.design(home, Request(WIDGET, qty=2, tier_cap="MV")).ids == d.ids
    assert designer.resolve(home, d.ids[1]).ids == d.ids


def test_group_and_view(home: Path) -> None:
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    assert [p["id"] for p in designer.group(home, d.ids[2])] == d.ids
    v = designer.view(home, d.ids[0])
    assert [r["id"] for r in v["routes"]] == d.ids
    assert v["weights"].startswith("ranked by weights (balanced)")
    for r in v["routes"]:
        check_card(r["flow"])
        check_card(r["bom"])
        assert all(n["recipe"].startswith("r:") for n in r["nodes"])
        assert r["status"] == "proposed"
    card = v["comparison"]
    check_card(card)
    assert [r["id"] for r in card["routes"]] == d.ids
    # The routes differ in at least one primary choice, so each marks a difference.
    for route in card["routes"]:
        assert any(c["differs"] for c in route["choices"])
        assert route["ref"] == f"plan:{route['id']}"
    score = [
        next(m for m in r["metrics"] if m["key"] == "score") for r in card["routes"]
    ]
    assert score[0]["best"] and score[0]["better"] == "higher"
    assert score[0]["value"] >= score[1]["value"] >= score[2]["value"]


def test_comparison_marks_only_real_differences() -> None:
    def p(pid: str, recipes: list[tuple[str, str]], score: float) -> dict[str, Any]:
        return {
            "id": pid,
            "name": f"Widget line · 1/s · LV{'' if pid == 'pl-a' else ' · route 2'}",
            "nodes": [
                {"n": f"n{i}", "for": [item], "recipe": r, "machine": None}
                for i, (item, r) in enumerate(recipes, 1)
            ],
            "metrics": {"machines": 3, "new_types": [], "complexity": {"score": 2}},
            "rank": {"score": score, "position": 1 if pid == "pl-a" else 2},
        }

    from gregtec.planner.outputs.book import Book

    a = p("pl-a", [("t:x@0", "r:m/aaaaaaaaaaaa"), ("t:y@0", "r:m/bbbbbbbbbbbb")], 0.9)
    b = p("pl-b", [("t:x@0", "r:m/cccccccccccc"), ("t:y@0", "r:m/bbbbbbbbbbbb")], 0.5)
    card = designer.comparison_card([a, b], {"pl-a": Book(), "pl-b": Book()}, {}, "w")
    check_card(card)
    assert card["title"] == "Widget line · 1/s · LV"
    for route in card["routes"]:
        assert [c["differs"] for c in route["choices"]] == [True, False]
        machines = next(m for m in route["metrics"] if m["key"] == "machines")
        assert not machines["best"]  # every route ties: nothing is marked
    assert [
        next(m for m in r["metrics"] if m["key"] == "score")["best"]
        for r in card["routes"]
    ] == [True, False]
    one = designer.comparison_card([a], {"pl-a": Book()}, {"pl-a": "accepted"}, "w")
    assert one["routes"][0]["accepted"]
    assert not any(c["differs"] for c in one["routes"][0]["choices"])


def test_design_refusals(home: Path) -> None:
    with pytest.raises(designer.DesignerError, match="isn't a plan id"):
        designer.load(home, "../etc")
    with pytest.raises(designer.DesignerError, match="isn't saved"):
        designer.load(home, "pl-0000000000")
    empty = home.parent / "empty"
    empty.mkdir()
    with pytest.raises(designer.DesignerError, match="gos db build"):
        designer.design(empty, Request(WIDGET, tier_cap="MV"))


def test_resolve_target(home: Path) -> None:
    found = designer.resolve_target(home, WIDGET)
    assert found.key == WIDGET  # type: ignore[union-attr]
    with pytest.raises(designer.DesignerError, match="nothing in the DB"):
        designer.resolve_target(home, "no such thing at all zzz")


# --- alternatives and pins ---------------------------------------------------------------------


def test_alternatives_of_a_node(home: Path) -> None:
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    a = designer.alternatives(home, d.ids[0], WIDGET)
    assert a["node"] == WIDGET and a["pinned"] is None
    statuses = {x["status"] for x in a["alternatives"]}
    assert "chosen" in statuses and statuses <= {"chosen", "alternative", "pruned"}
    assert sum(1 for x in a["alternatives"] if x["used"]) == 1
    every = designer.alternatives(home, d.ids[0], WIDGET, show_all=True)
    assert len(every["alternatives"]) > len(a["alternatives"])
    with pytest.raises(designer.DesignerError, match="isn't a node"):
        designer.alternatives(home, d.ids[0], "test:item@999")


def test_pin_survives_reload_and_re_solve(home: Path) -> None:
    """D61: a non-primary node pinned from the Designer stays pinned in the saved JSON, when the
    plan is loaded again and when its request is solved again."""
    acid = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    first = acid.ids[0]
    reacting = designer.pin(home, first, WIDGET, ref_of(home, "chemicalreactor", 0))
    assert len(reacting.ids) >= 1
    gas_options = designer.alternatives(home, reacting.ids[0], GAS)
    other = next(
        x["recipe"] for x in gas_options["alternatives"] if x["status"] != "chosen"
    )
    pinned = designer.pin(home, reacting.ids[0], GAS, other)
    pid = pinned.ids[0]
    want = {"node": GAS, "recipe": other}
    # reload: the saved JSON and the view
    assert want in designer.load(home, pid)["pins"]
    view = designer.view(home, pid)
    assert want in view["routes"][0]["pins"]
    assert any(n["recipe"] == other and n["pinned"] for n in view["routes"][0]["nodes"])
    assert designer.alternatives(home, pid, GAS)["pinned"] == other
    assert designer.latest(home) == pinned.ids
    # re-solve: the same routes and the pin again
    again = designer.resolve(home, pid)
    assert again.ids == pinned.ids
    assert want in again.plans[0]["pins"]
    # unpin goes back to the design without it
    freed = designer.pin(home, pid, GAS, None)
    assert want not in freed.plans[0]["pins"]
    assert freed.ids == reacting.ids


def test_pin_refusals(home: Path) -> None:
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    with pytest.raises(designer.DesignerError, match="isn't an item"):
        designer.pin(home, d.ids[0], "n1", None)
    with pytest.raises(designer.DesignerError, match="isn't a recipe id"):
        designer.pin(home, d.ids[0], WIDGET, "r:bad")
    with pytest.raises(designer.DesignerError, match="no pin"):
        designer.pin(home, d.ids[0], WIDGET, None)
    with pytest.raises(designer.DesignerError, match="not a candidate|can't make"):
        designer.pin(home, d.ids[0], WIDGET, "r:gt.recipe.mixer/000000000000")


# --- accept, the ledger hook, rename -----------------------------------------------------------


def test_accept_writes_the_line_and_runs_the_ledger_stub(home: Path) -> None:
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    pid = d.ids[0]
    done = designer.accept(home, pid)
    assert done.status == "accepted" and done.problems == []
    assert status(home, pid) == "accepted"
    lid = designer.line_id(pid)
    assert done.line == f"base/lines/{lid}.md"
    line = note_ok(home, done.line)
    assert "st: accepted\n" in line and f"plan: plan:{pid}\n" in line
    assert f"tgt: {WIDGET}\n" in line
    assert f"id: {lid}\n" in line
    plan_note = note_ok(home, f"plans/{pid}.md")
    assert "st: accepted\n" in plan_note
    assert done.ledger["recorded"] is False
    assert done.ledger["note"] == ledger.PENDING
    assert done.ledger["draws"] == [
        {
            "tier": e["tier"],
            "eut_avg": float(e["eut_avg"]),
            "eut_peak": float(e["eut_peak"]),
            "amps": e["amps"],
        }
        for e in d.plans[0]["metrics"]["eu"]
    ]
    view = designer.view(home, pid)
    assert view["routes"][0]["status"] == "accepted"
    assert view["comparison"]["routes"][0]["accepted"]
    with pytest.raises(designer.DesignerError, match="is accepted"):
        designer.accept(home, pid)
    # A re-solve keeps the row's status.
    designer.resolve(home, pid)
    assert status(home, pid) == "accepted"


def test_accept_without_a_vault_is_a_problem(home: Path) -> None:
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    import shutil

    shutil.rmtree(home / "vault")
    done = designer.accept(home, d.ids[1])
    assert done.status == "accepted" and done.line is None
    assert done.problems == ["no vault: the notes weren't written"]


def test_rename(home: Path) -> None:
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    pid = d.ids[0]
    out = designer.rename(home, pid, "  Widget   works  ")
    assert out["name"] == "Widget works"
    assert designer.load(home, pid)["name"] == "Widget works"
    note = note_ok(home, f"plans/{pid}.md")
    assert "title: Widget works\n" in note
    bom = (home / "data" / "plans" / pid / "bom.tsv").read_text(encoding="utf-8")
    assert bom  # exports were written again with the plan
    # A re-solve keeps the human name; the id stays.
    again = designer.resolve(home, pid)
    assert again.plans[0]["name"] == "Widget works" and again.ids[0] == pid
    designer.accept(home, pid)
    designer.rename(home, pid, "Widget works 2")
    assert "note: Widget works 2\n" in note_ok(
        home, f"base/lines/{designer.line_id(pid)}.md"
    )
    for bad in ("", "   ", "x" * 81, "a [[link]]", "a|b", 5):
        with pytest.raises(designer.DesignerError):
            designer.rename(home, pid, bad)


def test_ledger_stub_reads_only_the_plan() -> None:
    p = {
        "id": "pl-1",
        "metrics": {
            "eu": [{"tier": "MV", "eut_avg": 64.5, "eut_peak": 120, "amps": 2}, {}]
        },
    }
    u = ledger.ledger_update(p, "accepted")
    assert u.as_dict() == {
        "plan": "plan:pl-1",
        "status": "accepted",
        "draws": [{"tier": "MV", "eut_avg": 64.5, "eut_peak": 120.0, "amps": 2}],
        "recorded": False,
        "note": ledger.PENDING,
    }
    assert ledger.ledger_update({"id": "pl-2"}, "accepted").draws == ()


def test_latest_ignores_bad_files(home: Path) -> None:
    assert designer.latest(home) == []
    path = home / designer.LATEST
    path.parent.mkdir(parents=True, exist_ok=True)
    for text in ("{", "[]", json.dumps({"ids": "x"}), json.dumps({"ids": ["../x", 3]})):
        path.write_text(text, encoding="utf-8")
        assert designer.latest(home) == []
