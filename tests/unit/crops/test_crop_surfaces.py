"""The crop guide's surfaces (WO-6.7): what Clayton has and follows (gregtec.crops.state), the
facts loader over a game DB, the Crops view's routes, the agent tools, "next crop step" and
``gos crops``. The DB is the made-up world (tests/fixtures/crops/cropworld.py)."""

from __future__ import annotations

import io
import sqlite3
from argparse import Namespace
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from gregtec import config, store
from gregtec.agents import ask, router
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.tsv import parse
from gregtec.api.security import Principal
from gregtec.crops import cli as cropcli
from gregtec.crops import facts as crop_facts
from gregtec.crops import planner, state, view
from gregtec.db import access


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = store.connect(db)
    yield c
    c.close()


# --- state ---------------------------------------------------------------------------------


def test_have_and_target_round_trip_and_refuse_bad_input(
    conn: sqlite3.Connection,
) -> None:
    known = crop_facts.load(conn).data.crops
    assert state.have(conn) == {} and state.target(conn) is None
    state.set_have(conn, "crop:cropsnh:reed", [10, 4, 31], known)
    assert state.have(conn) == {"cropsnh:reed": (10, 4, 31)}
    for bad in ([0, 1, 1], [1, 1], [1, 1, 32], ["1", 1, 1], [True, 1, 1]):
        with pytest.raises(state.CropStateError):
            state.set_have(conn, "cropsnh:reed", bad, known)
    with pytest.raises(state.CropStateError, match="no crop"):
        state.set_have(conn, "cropsnh:nope", [1, 1, 1], known)
    state.remove_have(conn, "cropsnh:reed")
    assert state.have(conn) == {}
    t = state.set_target(conn, "cropsnh:ironFern", [31, 31, 20], known)
    assert t == state.Target("cropsnh:ironFern", (31, 31, 20))
    assert state.set_target(conn, None) is None and state.target(conn) is None


def test_a_hand_edited_row_that_doesnt_read_is_left_out(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        "INSERT INTO kv(key, value, upd) VALUES (?, ?, 0)",
        (state.KV_HAVE, '{"cropsnh:reed": [1, 1, 1], "cropsnh:wheat": "x"}'),
    )
    assert state.have(conn) == {"cropsnh:reed": (1, 1, 1)}


def test_my_base_stock_keys_drop_the_nbt_hash(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO my_base_stock(key, qty, upd) VALUES ('minecraft:stone@0', 4, 0),"
        " ('a:b@1#0123abcd', 1, 0)"
    )
    assert state.stock_keys(conn) == {"minecraft:stone@0", "a:b@1"}


# --- the facts loader ------------------------------------------------------------------------


def test_facts_load_the_breeder_recipe_and_machines(
    db: Path, world: ModuleType
) -> None:
    c = access.connect(db, readonly=True)
    try:
        f = crop_facts.load(c)
    finally:
        c.close()
    assert f.run_key == world.RUN_KEY and set(f.data.crops) == set(world.data().crops)
    assert f.breeder == [world.BREEDER]
    assert f.recipe_refs == {world.BREEDER_REF}
    assert f.machine("crop_breeder", 2) == crop_facts.Machine(
        28026, "Advanced Crop Breeder", 2
    )
    assert (
        f.machine("crop_breeder", 9).tier == 3 and f.machine("crop_manager", 0) is None
    )
    assert f.names["minecraft:stone@0"] == "Stone"


def test_no_crop_data(tmp_path: Path) -> None:
    c = access.connect(tmp_path / "empty.sqlite")
    try:
        with pytest.raises(crop_facts.NoCropData):
            crop_facts.load(c)
    finally:
        c.close()


# --- the Crops view's routes ----------------------------------------------------------------


def app_for(db: Path) -> TestClient:
    from gregtec.api.app import _json_body

    app = FastAPI()
    who = Principal("device", "phone")

    def principal(request: Request) -> Principal:
        return who

    home = db.parent.parent
    core = SimpleNamespace(
        cfg=SimpleNamespace(gos_home=home), store=SimpleNamespace(path=db)
    )
    view.add_routes(app, core, principal, principal, _json_body)  # type: ignore[arg-type]
    return TestClient(app)


def test_the_view_payload_and_edits(db: Path) -> None:
    client = app_for(db)
    p = client.get("/api/crops").json()
    assert p["guide"] is None and p["target"] is None and p["error"] is None
    assert p["tier"] == 0 and p["tier_name"] == "Steam"
    assert [c["crop"] for c in p["crops"]][:2] == ["cropsnh:carrot", "cropsnh:ironFern"]
    p = client.get("/api/crops", params={"crop": "stone lily"}).json()
    assert p["guide"]["target"] == "cropsnh:stoneLily" and p["guide"]["next"] == 1
    assert client.get("/api/crops", params={"crop": "zzz"}).json()["error"] == (
        "no crop matches 'zzz'"
    )
    p = client.post("/api/crops/target", json={"crop": "cropsnh:reed"}).json()
    assert p["target"]["name"] == "Reed" and p["guide"]["target"] == "cropsnh:reed"
    p = client.post(
        "/api/crops/have", json={"crop": "cropsnh:carrot", "stats": [3, 2, 1]}
    ).json()
    assert p["have"] == [
        {
            "crop": "cropsnh:carrot",
            "ref": "crop:cropsnh:carrot",
            "name": "Carrot",
            "stats": [3, 2, 1],
        }
    ]
    assert [s["child"] for s in p["guide"]["steps"]] == [
        "cropsnh:wheat",
        "cropsnh:reed",
    ]
    assert (
        client.post(
            "/api/crops/have", json={"crop": "cropsnh:carrot", "stats": [0, 1, 1]}
        ).status_code
        == 400
    )
    assert client.post("/api/crops/target", json={}).status_code == 400
    p = client.delete("/api/crops/have", params={"crop": "cropsnh:carrot"}).json()
    assert p["have"] == []
    assert (
        client.post("/api/crops/target", json={"crop": None}).json()["target"] is None
    )
    assert client.get("/api/crops", params={"crop": "x" * 121}).status_code == 422


def test_a_crop_with_no_way_says_so(db: Path) -> None:
    p = app_for(db).get("/api/crops", params={"crop": "lonely"}).json()
    assert p["guide"] is None and p["error"].startswith("no way to Lonely")


def test_without_a_db_the_view_says_so(tmp_path: Path) -> None:
    db = tmp_path / "home" / "data" / "gos.sqlite"
    r = app_for(db).get("/api/crops")
    assert r.status_code == 404 and "gos db build" in r.json()["detail"]


# --- the tools, and "next crop step" -------------------------------------------------------


def test_crop_find_plan_and_step(db: Path, conn: sqlite3.Connection) -> None:
    reg = build_registry(db)
    out = reg.call("crop_find", {"text": "lily"}, lane="fast")
    rows = parse(out.text).rows
    assert [r["ref"] for r in rows] == ["crop:cropsnh:stoneLily"]
    assert rows[0]["sub_soil"] == "yes" and rows[0]["bred_from"] == "Reed + Carrot"
    out = reg.call("crop_plan", {}, lane="fast")
    assert out.is_error and "no target" in out.text
    out = reg.call("crop_plan", {"target": "Iron Fern"}, lane="fast")
    p = parse(out.text)
    assert [r["kind"] for r in p.rows] == [
        "obtain",
        "obtain",
        "sticks",
        "sticks",
        "breeder",
    ]
    assert p.rows[0]["state"] == "next" and p.head["next"] == "1"
    assert "crop:cropsnh:carrot" in p.rows[3]["parents"]
    out = reg.call("crop_step", {"target": "Iron Fern", "n": 5}, lane="fast")
    p = parse(out.text)
    assert p.head["step"] == "5 of 5"
    parts = [r["part"] for r in p.rows]
    assert parts.count("requirement") >= 3 and "number" in parts and "warning" in parts
    num = next(r for r in p.rows if r["part"] == "number")
    assert num["refs"].split()[0] == "mte:28026" and "50%" in num["text"]
    out = reg.call("crop_step", {"target": "Iron Fern", "n": 9}, lane="fast")
    assert out.is_error and "has 5 steps" in out.text
    state.set_target(conn, "cropsnh:stoneLily")
    p = parse(reg.call("crop_step", {}, lane="fast").text)
    assert p.head["step"] == "1 of 4" and p.head["title"].startswith("Plant Carrot")


def test_next_crop_step_is_answered_in_code(db: Path, conn: sqlite3.Connection) -> None:
    for said in (
        "next crop step",
        "What's the next crop step?",
        "crop step",
        "what should i breed next",
    ):
        assert router.route(said).intent == router.CROP_STEP, said
    a = ask.Asker(build_registry(db), None, None)  # type: ignore[arg-type]
    reply = a.ask("next crop step")
    assert reply.lane == router.INSTANT and "no target" in reply.text
    state.set_target(conn, "cropsnh:reed")
    reply = a.ask("next crop step")
    assert reply.text.startswith(
        "Step 1 of 3: Plant Carrot to start Carrot. It plants the crop at 1/1/1"
    )
    assert reply.refs == ["crop:cropsnh:carrot"] and reply.card["type"] == "table"
    for crop in ("cropsnh:carrot", "cropsnh:wheat"):
        state.set_have(conn, crop, [1, 1, 1])
    reply = a.ask("next crop step")
    assert reply.text.startswith("Step 1 of 1: Breed Reed from Carrot + Wheat")
    assert "16.7%" in reply.text
    state.set_have(conn, "cropsnh:reed", [1, 1, 1])
    assert (
        a.ask("next crop step").text
        == "Every step to Reed (crop:cropsnh:reed) is done."
    )


# --- gos crops ------------------------------------------------------------------------------


def run(db: Path, monkeypatch: pytest.MonkeyPatch, **kw: object) -> tuple[int, str]:
    home = db.parent.parent
    monkeypatch.setattr(config, "load", lambda path: SimpleNamespace(gos_home=home))
    out = io.StringIO()
    args = Namespace(**{"config": None, "tier": None, "crop": None, **kw})
    return cropcli.run(args, out), out.getvalue()


def test_gos_crops(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out = run(db, monkeypatch, crops_command="guide", next=False)
    assert code == 1 and "No target" in out
    code, out = run(db, monkeypatch, crops_command="target", clear=False, crop="reed")
    assert code == 0 and out == "Following crop:cropsnh:reed\n"
    code, out = run(db, monkeypatch, crops_command="guide", next=True)
    assert code == 0 and "1. Plant Carrot to start Carrot (obtain, to do)" in out
    code, out = run(
        db,
        monkeypatch,
        crops_command="have",
        crop="carrot",
        stats=[5, 5, 5],
        remove=False,
    )
    assert code == 0
    code, out = run(
        db, monkeypatch, crops_command="have", crop=None, stats=[], remove=False
    )
    assert out == "crop:cropsnh:carrot\tCarrot\t5/5/5\n"
    code, out = run(
        db, monkeypatch, crops_command="guide", next=False, crop="iron fern"
    )
    assert code == 0 and "| . | Reed | ." in out and "stats plan:" in out
    code, out = run(
        db, monkeypatch, crops_command="check", crop=["reed", "iron fern", "weedy"]
    )
    assert code == 0 and out.count("0 problems") == 3
    code, out = run(db, monkeypatch, crops_command="guide", next=False, crop="nothing")
    assert code == 1 and "No crop matches 'nothing'" in out


def test_every_number_on_the_tool_rows_has_a_ref(db: Path) -> None:
    reg = build_registry(db)
    g = parse(reg.call("crop_plan", {"target": "Iron Fern"}, lane="fast").text)
    for row in g.rows:
        if any(ch.isdigit() for ch in row["numbers"]):
            assert row["refs"], row
    assert planner.CITE.match("crop:cropsnh:reed")
