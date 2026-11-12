"""The designer's outputs (WO-3.6) on the WO-3.4 fixture DB (tests/unit/planner/test_plan.py), and
footprints on a made-up multiblock with structure rows. Every item, recipe, machine and structure
is made up; no pack data.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PIL import Image

from gregtec.agents import saved_plans
from gregtec.agents.tools import plans as plan_tools
from gregtec.agents.tools.registry import Call, Env, ToolError
from gregtec.cards import validate
from gregtec.db import access
from gregtec.db import structures as structure_db
from gregtec.planner import plan, plancli
from gregtec.planner.graph import Port
from gregtec.planner.outputs import (
    FILES,
    bom,
    book,
    cards,
    diagram,
    footprint,
    gtnhflow,
    gtnhplanner,
    mermaid,
    png,
    store,
)
from gregtec.planner.request import Request
from gregtec.vault import deploy as vault_deploy
from gregtec.vault import schema as vault_schema
from gregtec.vault.io import VaultIO

REPO = Path(__file__).resolve().parents[3]


def _plan_fixture() -> ModuleType:
    path = Path(__file__).with_name("test_plan.py")
    spec = importlib.util.spec_from_file_location("_wo36_plan_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tp = _plan_fixture()
WIDGET = tp.WIDGET


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tp.make_home(tmp_path_factory.mktemp("outputs-home"))


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def plans(conn: sqlite3.Connection) -> tuple[dict[str, Any], ...]:
    return tp.design(conn, Request(WIDGET, qty=2, tier_cap=2)).plans


# --- BOM ------------------------------------------------------------------------------------


def test_bom_sums_equal_the_plan(plans: tuple[dict[str, Any], ...]) -> None:
    for p in plans:
        assert bom.check(p) == []
        table = list(csv.reader(io.StringIO(bom.tsv(p)), delimiter="\t"))
        assert table[0] == list(bom.COLUMNS)
        body, total = table[1:-1], table[-1]
        assert [r[0] for r in body] == [n["n"] for n in p["nodes"]]
        assert [r[-1] for r in body] == [n["recipe"] for n in p["nodes"]]
        assert total[0] == bom.TOTAL
        assert int(total[4]) == p["metrics"]["machines"]
        assert float(total[8]) == p["metrics"]["eut_avg"]
        assert int(total[9]) == p["metrics"]["eut_peak"]
        for row, n in zip(body, p["nodes"], strict=True):
            assert row[1] == n["machine"]["name"] and row[3] == n["machine"]["tier"]
            assert int(row[4]) == n["count"] and int(row[7]) == n["eut"]
            assert float(row[10]) == n["util"]


def test_bom_check_names_a_mismatch(plans: tuple[dict[str, Any], ...]) -> None:
    p = json.loads(plan.dumps(plans[0]))
    p["metrics"]["machines"] += 1
    assert bom.check(p) and "metrics.machines" in bom.check(p)[0]


def test_bom_cell_never_holds_a_tab() -> None:
    assert bom._cell("a\tb\nc") == "a b c" and bom._cell(None) == ""


# --- footprint ------------------------------------------------------------------------------


@pytest.fixture
def mb(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A DB with a made-up multiblock mte:900 (two structure variants) and mte:901 (none)."""
    c = access.connect(tmp_path / "gos.sqlite")
    c.execute(
        "INSERT INTO machine(id, mte_id, name_en, is_multiblock) VALUES"
        " (1, 900, 'Test Furnace', 1), (2, 901, 'Addon Multi', 1), (3, 101, 'Mixer', 0)"
    )
    c.executemany(
        "INSERT INTO structure(machine_id, variant, size_x, size_y, size_z, blocks_json, source)"
        " VALUES (?, ?, ?, ?, ?, '[]', 'gtnh-extractor')",
        [(1, "t1", 3, 4, 3), (1, "t2", 3, 5, 3)],
    )
    try:
        yield c
    finally:
        c.close()


def _node(n: str, mte: int, kind: str, count: int) -> dict[str, Any]:
    return {
        "n": n,
        "count": count,
        "machine": {"mte": mte, "kind": kind, "name": f"M{mte}"},
    }


def test_footprints_equal_structure(mb: sqlite3.Connection) -> None:
    fp = footprint.machine(mb, 900, "multiblock")
    assert fp["size"] == {
        "x": 3,
        "y": 4,
        "z": 3,
    }  # the smaller variant, as structure has it
    c = footprint.CLEARANCE_XZ
    assert fp["box"] == {"x": 3 + 2 * c, "y": 4 + footprint.CLEARANCE_Y, "z": 3 + 2 * c}
    assert fp["area"] == fp["box"]["x"] * fp["box"]["z"]
    single = footprint.machine(mb, 101, "singleblock")
    assert (
        single["size"] == {"x": 1, "y": 1, "z": 1}
        and single["source"] == "single block"
    )
    unknown = footprint.machine(mb, 901, "multiblock")
    assert unknown == {
        "mte": 901,
        "footprint": "unknown",
        "why": "mte:901 has no structure data",
    }


def test_layout_sums_boxes_times_counts(mb: sqlite3.Connection) -> None:
    plan_ = {
        "nodes": [
            _node("n1", 900, "multiblock", 2),
            _node("n2", 101, "singleblock", 5),
            _node("n3", 101, "singleblock", 1),
            _node("n4", 901, "multiblock", 1),
            {"n": "n5", "count": None, "machine": None},
        ]
    }
    lay = footprint.layout(mb, plan_)
    big = footprint.machine(mb, 900, "multiblock")
    small = footprint.machine(mb, 101, "singleblock")
    assert lay["area"] == 2 * big["area"] + 6 * small["area"]
    assert lay["volume"] == 2 * big["volume"] + 6 * small["volume"]
    assert lay["h"] == big["box"]["y"] and lay["unknown"] == ["mte:901"]
    assert [m["count"] for m in lay["machines"]] == [6, 2, 1]
    assert footprint.summary(lay) == {
        "area": lay["area"], "volume": lay["volume"], "h": lay["h"], "unknown": 1
    }  # fmt: skip


def test_footprint_finds_an_unlinked_controller_by_key(mb: sqlite3.Connection) -> None:
    structure_db.ensure_schema(mb)  # D123's registry_name and meta columns
    mb.execute(
        "INSERT INTO structure(machine_id, variant, size_x, size_y, size_z, registry_name, meta)"
        " VALUES (NULL, 't1', 5, 2, 7, 'gregtech:gt.blockmachines', 901)"
    )
    assert footprint.machine(mb, 901, "multiblock")["size"] == {"x": 5, "y": 2, "z": 7}


def test_the_plan_carries_its_layout(plans: tuple[dict[str, Any], ...]) -> None:
    for p in plans:
        tp.valid(p)
        assert p["layout"]["method"] == "bbox"
        assert p["metrics"]["footprint"] == footprint.summary(p["layout"])
        counts = sum(m["count"] for m in p["layout"]["machines"])
        assert counts == p["metrics"]["machines"]


# --- diagrams -------------------------------------------------------------------------------


def test_flow_boxes_and_links(
    conn: sqlite3.Connection, plans: tuple[dict[str, Any], ...]
) -> None:
    p = plans[0]
    b = book.read(conn, p)
    f = diagram.flow(p, b)
    ids = {x.id for x in f.boxes}
    assert {n["n"] for n in p["nodes"]} <= ids
    assert f"out:{p['target']['id']}" in ids
    for e in p["edges"]:
        assert any(
            (x.src, x.dst, x.item, x.rate_s)
            == (e["from"], e["to"], e["item"], e["rate_s"])
            for x in f.links
        )
    assert all(x.src in ids and x.dst in ids for x in f.links)
    target = next(x for x in f.boxes if x.kind == "target")
    assert target.rate_s == p["target"]["rate_s"]


def test_layers_cut_loops_and_keep_input_order() -> None:
    got = diagram.layers(
        ["a", "b", "c", "d"], [("a", "b"), ("b", "c"), ("c", "b"), ("a", "d")]
    )
    assert got == {"a": (0, 0), "b": (1, 0), "d": (1, 1), "c": (2, 0)}
    assert diagram.layers([], []) == {}


def test_mermaid(conn: sqlite3.Connection, plans: tuple[dict[str, Any], ...]) -> None:
    p = plans[0]
    text = mermaid.text(p, book.read(conn, p))
    lines = text.splitlines()
    assert lines[0] == "flowchart LR"
    for n in p["nodes"]:
        assert any(x.strip().startswith(f'{n["n"]}["') for x in lines)
    assert any("-->|" in x for x in lines)
    assert mermaid.esc('a "b" <c> | #d') == "a #quot;b#quot; #lt;c#gt; #124; #35;d"


def test_png(conn: sqlite3.Connection, plans: tuple[dict[str, Any], ...]) -> None:
    p = plans[0]
    data = png.render(p, book.read(conn, p))
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    img = Image.open(io.BytesIO(data))
    assert img.format == "PNG" and img.size[0] > 300 and img.size[1] > 80
    assert png.render(p, book.read(conn, p)) == data  # deterministic


def test_png_colours_are_the_design_systems() -> None:
    tokens = json.loads(
        (REPO / "docs/design/system/tokens.json").read_text(encoding="utf-8")
    )
    value = {t["name"]: t["value"] for t in tokens["color"]["tokens"]}
    for tier, colour in png.TIER.items():
        assert value[f"tier-{tier.lower()}"] == colour
    assert value["machine-bg"] == png.BG and value["machine-panel"] == png.PANEL
    assert value["machine-text"] == png.TEXT and value["tier-edge"] == png.TIER_EDGE


def test_cards_are_catalog_cards(
    conn: sqlite3.Connection, plans: tuple[dict[str, Any], ...]
) -> None:
    p = plans[0]
    flow_card = cards.flow_card(p, book.read(conn, p))
    assert validate(flow_card) == [] and flow_card["ref"] == f"plan:{p['id']}"
    nodes = {n["id"]: n for n in flow_card["nodes"]}
    for n in p["nodes"]:
        assert (
            nodes[n["n"]]["ref"] == n["recipe"] and nodes[n["n"]]["count"] == n["count"]
        )
    bom_card = cards.bom_card(p)
    assert validate(bom_card) == []
    assert sum(r["amount"] for r in bom_card["rows"]) == p["metrics"]["machines"]


# --- planner exports ------------------------------------------------------------------------


def test_gtnhplanner_project(
    conn: sqlite3.Connection, plans: tuple[dict[str, Any], ...]
) -> None:
    p = plans[0]
    proj = gtnhplanner.project(p, book.read(conn, p))
    assert proj["schemaVersion"] == 1 and proj["name"] == p["name"]
    assert proj["targetRate"]["amountPerSecond"] == p["target"]["rate_s"]
    assert [n["id"] for n in proj["nodes"]] == [n["n"] for n in p["nodes"]]
    assert [n["machineCount"] for n in proj["nodes"]] == [
        n["count"] for n in p["nodes"]
    ]
    recipes = {r["id"]: r for r in proj["recipes"]}
    for n in p["nodes"]:
        r = recipes[n["recipe"]]
        assert r["eut"] >= 0 and r["durationTicks"] >= 1 and r["machineType"]
        assert r["metadata"]["gos"]["count"] == n["count"]
        for s in (*r["inputs"], *r["outputs"]):
            assert s["amount"] > 0 and not s["id"].startswith("fluid:")
    ends = {n["id"] for n in proj["nodes"]} | {s["id"] for s in proj["storages"]}
    assert all(e["source"] in ends and e["target"] in ends for e in proj["edges"])
    assert any(s.get("drainMode") == "product" for s in proj["storages"])
    assert sum("targetOutput" in n for n in proj["nodes"]) >= 1
    assert gtnhplanner.resource_id("fluid:testgas") == "testgas"
    text = gtnhplanner.text(p, book.read(conn, p))
    assert json.loads(text) == proj and text == gtnhplanner.text(p, book.read(conn, p))


def test_gtnhflow_yaml(
    conn: sqlite3.Connection, plans: tuple[dict[str, Any], ...]
) -> None:
    p = plans[0]
    text = gtnhflow.text(p, book.read(conn, p))
    blocks = [b for b in text.split("\n- ")[1:]]
    machines = [n for n in p["nodes"] if n["machine"]]
    assert len(blocks) == len(machines)
    for block, n in zip(blocks, machines, strict=True):
        keys = [
            line.split(":")[0].strip()
            for line in block.splitlines()
            if not line.startswith("    ")
        ]
        assert keys[:7] == ["m", "tier", "I", "O", "eut", "dur", "do_not_overclock"]
        dur = float(
            next(x for x in block.splitlines() if x.startswith("  dur: ")).split(": ")[
                1
            ]
        )
        # gtnh-flow's count is crafts/s x seconds per craft per machine: the plan's, before ceil
        assert n["crafts_s"] * dur == pytest.approx(n["count"] * n["util"], rel=1e-6)
    assert text.count("  target:") == 1
    assert gtnhflow._tier("ULV") == "lv" and gtnhflow._tier("MV") == "mv"


def test_gtnhflow_names_are_unique() -> None:
    rec = book.Recipe(
        "r:x/000000000000", "m", None, "gt", 1, 1, 1,
        (Port("a@1", 1),), (Port("b@1", 1), Port("c@1", 1)),
    )  # fmt: skip
    b = book.Book({"a@1": "Steel", "b@1": "steel", "c@1": "Slag"}, {rec.ref: rec})
    p = {"nodes": [{"recipe": rec.ref}], "target": {"id": "c@1"}}
    assert gtnhflow.ingredient_names(p, b) == {
        "a@1": "steel (a@1)", "b@1": "steel (b@1)", "c@1": "slag"
    }  # fmt: skip


# --- persistence, the tool, the CLI ---------------------------------------------------------


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


def test_save_writes_json_exports_row_and_note(
    home: Path, plans: tuple[dict[str, Any], ...]
) -> None:
    p = plans[0]
    rw = access.connect(access.db_path(home))
    try:
        saved = store.save(
            home, p, rw, book.read(rw, p), VaultIO(home / "vault"), now=1000
        )
        assert saved.problems == [] and saved.note == f"plans/{p['id']}.md"
        assert saved.json_path.read_text(encoding="utf-8") == plan.dumps(p)
        assert store.load(home, p["id"]) == json.loads(plan.dumps(p))
        assert set(saved.files) == set(FILES)
        assert all(
            path.is_file() and path.stat().st_size for path in saved.files.values()
        )
        row = rw.execute(
            "SELECT name, created, kind, target, objective, status, json_path, gt_version,"
            " metrics FROM plan WHERE id = ?",
            (p["id"],),
        ).fetchone()
        assert tuple(row)[:8] == (
            p["name"], 1000, "production", WIDGET, "rate", "proposed",
            f"data/plans/{p['id']}.json", p["gt"],
        )  # fmt: skip
        assert json.loads(row["metrics"])["machines"] == p["metrics"]["machines"]
        note = (home / "vault" / saved.note).read_text(encoding="utf-8")
        types = vault_schema.load_types(home / "vault" / "_gos" / "schema.md")
        assert vault_schema.check_text(saved.note, note, types) == []
        assert f"title: {p['name']}" in note and "st: proposed" in note
        assert "```mermaid\nflowchart LR" in note and len(note.splitlines()) <= 150
        # Saving again keeps the name and status Clayton set (WO-3.7 edits them).
        rw.execute(
            "UPDATE plan SET name = 'Mine', status = 'accepted' WHERE id = ?",
            (p["id"],),
        )
        store.save(home, p, rw, book.read(rw, p), VaultIO(home / "vault"), now=2000)
        assert tuple(
            rw.execute(
                "SELECT name, status, created FROM plan WHERE id = ?", (p["id"],)
            ).fetchone()
        ) == ("Mine", "accepted", 1000)
        assert "st: accepted" in (home / "vault" / saved.note).read_text(
            encoding="utf-8"
        )
        # The no-AI path reads the saved plan and its machine BOM.
    finally:
        rw.close()
    found, problems = saved_plans.load(access.db_path(home), home)
    assert problems == []
    mine = next(x for x in found if x.id == p["id"])
    assert mine.bom is not None
    assert sum(r.amount for r in mine.bom) == p["metrics"]["machines"]


def test_save_without_a_vault_is_a_problem_not_a_failure(
    home: Path, plans: tuple[dict[str, Any], ...]
) -> None:
    p = plans[0]
    rw = access.connect(access.db_path(home))
    try:
        saved = store.save(home, p, rw, book.read(rw, p), VaultIO(home / "no-vault"))
        assert saved.note is None and "vault note wasn't written" in saved.problems[0]
        assert store.save(home, p, rw, book.read(rw, p)).problems
        assert saved.json_path.is_file()
    finally:
        rw.close()


def test_lifecycle(home: Path, plans: tuple[dict[str, Any], ...]) -> None:
    p = plans[0]
    rw = access.connect(access.db_path(home))
    try:
        store.save(home, p, rw, book.read(rw, p))
        for nxt in ("accepted", "building", "running", "retired"):
            store.set_status(rw, p["id"], nxt)
        with pytest.raises(ValueError, match="retired"):
            store.set_status(rw, p["id"], "running")
        with pytest.raises(ValueError, match="status must be"):
            store.set_status(rw, p["id"], "done")
        with pytest.raises(ValueError, match="no plan"):
            store.set_status(rw, "pl-nothere", "accepted")
    finally:
        rw.close()
    assert store.LIFECYCLE == ("proposed", "accepted", "building", "running", "retired")


def test_long_plans_leave_the_diagram_out_of_the_note(
    conn: sqlite3.Connection, plans: tuple[dict[str, Any], ...]
) -> None:
    p = json.loads(plan.dumps(plans[0]))
    b = book.read(conn, p)
    p["edges"] = p["edges"] * 200  # a diagram far past the note's cap
    body = store.note_body(p, b, "proposed")
    assert "```mermaid" not in body and "flow.mmd" in body


def test_plan_export_tool(home: Path, plans: tuple[dict[str, Any], ...]) -> None:
    p = plans[0]
    rw = access.connect(access.db_path(home))
    try:
        store.save(home, p, rw, book.read(rw, p))
    finally:
        rw.close()
    call = Call(None, Env(access.db_path(home)))
    got = plan_tools.plan_export(call, {"plan": f"plan:{p['id']}", "format": "bom"}, 0)
    assert got.columns[-1] == "cite" and len(got.rows) == len(p["nodes"]) + 1
    assert [r[-1] for r in got.rows[:-1]] == [n["recipe"] for n in p["nodes"]]
    assert got.rows[-1][0] == "total" and got.rows[-1][-1] == f"plan:{p['id']}"
    fp = plan_tools.plan_export(call, {"plan": p["id"], "format": "footprint"}, 0)
    assert dict(fp.head)["area"] == p["layout"]["area"]
    mm = plan_tools.plan_export(call, {"plan": p["id"], "format": "mermaid"}, 0)
    assert mm.rows[0] == ("flowchart LR",)
    path = plan_tools.plan_export(call, {"plan": p["id"], "format": "gtnhplanner"}, 0)
    assert dict(path.head)["path"].endswith("gtnhplanner.json") and not path.rows
    with pytest.raises(ToolError, match="plan id"):
        plan_tools.plan_export(call, {"plan": "../etc", "format": "bom"}, 0)
    with pytest.raises(ToolError, match="isn't saved"):
        plan_tools.plan_export(call, {"plan": "pl-0000000000", "format": "bom"}, 0)
    assert plan_tools.FILES == FILES


def test_cli_save(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOS_HOME", str(home))
    parser = argparse.ArgumentParser()
    plancli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    code = plancli.run(
        parser.parse_args(["plan", WIDGET, "--tier", "MV", "--qty", "2/s", "--save"]),
        out,
    )
    text = out.getvalue()
    assert code == 0 and text.count("saved pl-") >= 1
    saved = sorted((home / "data" / "plans").glob("pl-*.json"))
    assert saved and all(
        (home / "vault" / "plans" / f"{x.stem}.md").is_file() for x in saved
    )
