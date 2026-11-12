"""Ore intel (WO-6.6): nearest veins, his position, the tools, the card, the Ores view's routes
and a vein's waypoint through the confirm flow, on WO-1.6's self-authored ore fixture.

The fixture (``test_ores.py``: made-up mixes and a made-up Lab world, never pack data) is loaded
with ``gos ores load``; his position is a ``level.dat`` written here as Minecraft 1.7.10 writes
it. Every expected vein below is worked out by hand from the fixture (see ``EXPECTED``). The
waypoint is written only into the temporary Lab instance; the Play path runs with a fake
``mark`` and the real writer's refusal, so nothing here can reach a real instance.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sqlite3
import struct
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from gregtec import cards, config
from gregtec.agents import prefetch
from gregtec.agents.fast_lane import NOT_CHECKED, FastLane
from gregtec.agents.tools import Registry, build_registry
from gregtec.api.security import Principal
from gregtec.bridge import waypoint
from gregtec.bridge.actions import SETTLE_S, ActionDesk, Outcome
from gregtec.bridge.ops import Ops
from gregtec.cards import build as card_build
from gregtec.db import access
from gregtec.ingest import nbt
from gregtec.llm.fake import ScriptedClient, Turn
from gregtec.ores import intel, view
from gregtec.store import Store

HERE = Path(__file__).resolve().parent


def _fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_wo66_ores", HERE / "test_ores.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


F = _fixture()

# His position: x 100.7, z 20.2 in the Overworld (blocks 100, 20).
POS = (100.7, 64.0, 20.2, 0)
# Ferrite by hand (D142 (c)): live first, then the best layer, then nearest.
#   vein:0/10/-5   centre (168, -72), ferrite mix (primary): dx 68, dz -92 -> 114
#   vein:0/-2/4    centre (-24, 72),  ferrite mix (primary): dx -124, dz 52 -> 134
#   vein:0/4/1     centre (72, 24),   copperish mix (Ferrite only sporadic): dx -28, dz 4 -> 28
#   vein:0/1/1     centre (24, 24),   ferrite mix, depleted (his client cache): dx -76 -> 76
# The nearest vein (28 blocks) ranks third: the material is only sporadic there.
EXPECTED = [
    ("vein:0/10/-5", 168, -72, 114, "primary", False),
    ("vein:0/-2/4", -24, 72, 134, "primary", False),
    ("vein:0/4/1", 72, 24, 28, "sporadic", False),
    ("vein:0/1/1", 24, 24, 76, "primary", True),
]


def _named(kind: int, name: str, body: bytes) -> bytes:
    return bytes((kind,)) + F.jstring(name) + body


def level_dat(x: float, y: float, z: float, dim: int, compressed: bool = True) -> bytes:
    """A single-player level.dat: ``Data.Player`` with ``Pos`` (3 doubles) and ``Dimension``."""
    pos = struct.pack(">bi", nbt.DOUBLE, 3) + struct.pack(">3d", x, y, z)
    player = (
        _named(nbt.LIST, "Pos", pos)
        + _named(nbt.INT, "Dimension", struct.pack(">i", dim))
        + b"\x00"
    )
    data = (
        _named(nbt.STRING, "LevelName", F.jstring(F.WORLD))
        + _named(nbt.COMPOUND, "Player", player)
        + b"\x00"
    )
    raw = _named(nbt.COMPOUND, "", _named(nbt.COMPOUND, "Data", data) + b"\x00")
    return gzip.compress(raw) if compressed else raw


class Lab:
    """A Lab instance with the fixture world loaded into GOS_HOME's DB."""

    def __init__(self, tmp: Path) -> None:
        self.game, self.toml, self.home = F.prism(tmp)
        self.world = F.write_world(self.game)
        self.place(*POS)
        code, out = F.load(self.toml)
        assert code == 0, out
        self.cfg = config.load(self.toml)
        self.db = access.db_path(self.home)
        # JourneyMap has mapped this world once (its data folder exists).
        self.jm = self.game / "journeymap" / "data" / "sp" / F.WORLD
        self.jm.mkdir(parents=True)

    def place(self, x: float, y: float, z: float, dim: int) -> None:
        (self.world / "level.dat").write_bytes(level_dat(x, y, z, dim))

    def conn(self) -> sqlite3.Connection:
        return access.connect(self.db, readonly=True)


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    return Lab(tmp_path)


@pytest.fixture
def conn(lab: Lab) -> Iterator[sqlite3.Connection]:
    c = lab.conn()
    yield c
    c.close()


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class Desk:
    """The action desk over its own store, writing waypoints with the core's real writer."""

    def __init__(
        self, folder: Path, cfg: config.Config, fake_play: bool = False
    ) -> None:
        self.clock = Clock()
        self.store = Store(folder / "actions.sqlite")
        self.marked: list[tuple[str, Mapping[str, Any]]] = []
        self.fake_play = fake_play
        self.cfg = cfg
        self.desk = ActionDesk(self.store, mark=self.mark, clock=self.clock)

    def mark(self, target: str, args: Mapping[str, Any]) -> str:
        self.marked.append((target, dict(args)))
        if target == "play" and self.fake_play:
            return "fake: Play is never written here"
        # The real writer, as gos-dev: Play is refused (D75), the Lab written.
        return waypoint.write(waypoint.place(self.cfg, target, as_dev=True), args)

    def confirm(self) -> Outcome:
        self.clock.now += SETTLE_S
        return self.desk.confirm("dashboard")


@pytest.fixture
def desk(tmp_path: Path, lab: Lab) -> Iterator[Desk]:
    d = Desk(tmp_path, lab.cfg)
    yield d
    d.store.close()


# --- his position ----------------------------------------------------------------------------


def test_his_position_is_read_from_the_veins_world(
    lab: Lab, conn: sqlite3.Connection
) -> None:
    pos = intel.position(conn)
    assert (pos.x, pos.y, pos.z, pos.dim, pos.world) == (100, 64, 20, 0, F.WORLD)
    assert pos.age_s(pos.saved + 30) == 30
    lab.place(
        -0.5, 70.0, -16.1, -1
    )  # negative positions floor, as Minecraft's blocks do
    pos = intel.position(conn)
    assert (pos.x, pos.z, pos.dim) == (-1, -17, -1)
    (lab.world / "level.dat").write_bytes(level_dat(1.0, 2.0, 3.0, 0, compressed=False))
    assert intel.position(conn).x == 1


@pytest.mark.parametrize(
    ("data", "why"),
    [
        (None, "has no level.dat"),
        (b"\x1f\x8bnot gzip", "can't read the world's level.dat"),
        (F.nbt_file({"Data": {"LevelName": "x"}}), "has no player"),
        (
            F.nbt_file({"Data": {"Player": {"Pos": [1, 2], "Dimension": 0}}}),
            "no position",
        ),
    ],
)
def test_a_position_that_cant_be_read_says_why(
    lab: Lab, conn: sqlite3.Connection, data: bytes | None, why: str
) -> None:
    path = lab.world / "level.dat"
    if data is None:
        path.unlink()
    else:
        path.write_bytes(data)
    with pytest.raises(intel.OreIntelError, match=why):
        intel.position(conn)
    pos, said = intel.from_player(conn)
    assert pos is None and said is not None and why in said


def test_a_read_during_a_save_waits_then_reads_the_save_before(
    lab: Lab, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Minecraft 1.7.10 saves by renaming level.dat to level.dat_old, then level.dat_new to
    level.dat: a read in that gap waits once, then reads level.dat_old (review L1)."""
    monkeypatch.setattr(intel, "SAVE_WAIT_S", 0.0)
    level = lab.world / "level.dat"
    old = lab.world / "level.dat_old"
    saves: list[float] = []

    def save_lands(_: float) -> None:  # the save finishes while the read waits
        saves.append(_)
        old.rename(level)

    level.rename(old)
    monkeypatch.setattr(intel.time, "sleep", save_lands)
    assert intel.position(conn).x == 100 and len(saves) == 1
    level.rename(old)  # this time the gap outlasts the wait
    monkeypatch.setattr(intel.time, "sleep", lambda _: None)
    pos = intel.position(conn)
    assert (pos.x, pos.z) == (100, 20) and pos.saved == old.stat().st_mtime


def test_his_position_is_cited_by_world_and_save() -> None:
    pos = intel.Position(1, 2, 3, 0, "My World (2)", 1762876800.7)
    assert pos.ref == "pos:My_World_2@1762876800"
    assert intel.Position(1, 2, 3, 0, "Üï", 5.0).ref == "pos:world@5"


def test_with_no_veins_loaded_there_is_no_world(tmp_path: Path) -> None:
    db = tmp_path / "gos.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT)")
    with pytest.raises(intel.OreIntelError, match="gos ores load"):
        intel.position(c)
    c.close()


# --- nearest veins ---------------------------------------------------------------------------


def test_nearest_veins_match_the_hand_derived_order(conn: sqlite3.Connection) -> None:
    veins = intel.find(conn, "Ferrite", 0, (100, 20))
    got = [
        (v.ref, v.found.x, v.found.z, v.found.distance, v.layer, v.found.depleted)
        for v in veins
    ]
    assert got == EXPECTED
    assert veins[0].ores == ("Ferrite", "Magnetitoid", "Ferrite", "Copperish")
    assert (veins[0].cx, veins[0].cz) == (10, -5)
    # The display name finds the same veins, in any case.
    assert [v.ref for v in intel.find(conn, "ferrite", 0, (100, 20))] == [
        e[0] for e in EXPECTED
    ]


def test_other_dimensions_and_materials(conn: sqlite3.Connection) -> None:
    # The Nether: quartz is only in the quartzish mix; Tinite is secondary in copperish and
    # sporadic in quartzish, so copperish's vein ranks first wherever he stands.
    nether = intel.find(conn, "Nether Quartzish", -1, (0, 0))
    assert [(v.ref, v.layer, v.found.distance) for v in nether] == [
        ("vein:-1/1/1", "primary", 34)  # centre (24, 24): sqrt(1152) = 33.9
    ]
    tin = intel.find(conn, "Tinite", -1, (24, 24))
    assert [(v.ref, v.layer) for v in tin] == [
        ("vein:-1/-5/7", "secondary"),
        ("vein:-1/1/1", "sporadic"),
    ]
    # Twilight Forest heights come from the mix's override there.
    tf = intel.find(conn, "Ferrite", 7)
    assert [(v.ref, v.found.ymin, v.found.ymax, v.found.distance) for v in tf] == [
        ("vein:7/1/-2", 15, 40, None)
    ]
    assert intel.find(conn, "Unobtainium") == []
    assert intel.materials(conn) == [
        "Copperish",
        "Ferrite",
        "Lunite",
        "Magnetitoid",
        "Nether Quartzish",
        "Tinite",
    ]


def test_vein_refs_round_trip() -> None:
    assert intel.parse_ref("vein:-1/-5/7") == (-1, -5, 7)
    assert intel.vein_ref(0, 10, -5) == "vein:0/10/-5"
    for bad in (
        "vein:0/1",
        "vein:x/1/2",
        "r:0/1/2",
        "vein:0/1/2/3",
        "vein:0/1/99999999",
    ):
        with pytest.raises(ValueError):
            intel.parse_ref(bad)


def test_the_card_keeps_the_order_and_passes_the_catalog(
    conn: sqlite3.Connection,
) -> None:
    c = intel.card("Ferrite", intel.find(conn, "Ferrite", 0, (100, 20)), ["a note"])
    assert cards.validate(c) == []
    assert [r["ref"] for r in c["rows"]] == [e[0] for e in EXPECTED]
    assert c["rows"][3]["depleted"] is True and c["title"] == "Nearest Ferrite veins"


# --- the tools -------------------------------------------------------------------------------


def registry(lab: Lab, desk: Desk | None = None) -> Registry:
    ops = None
    if desk is not None:
        ops = Ops(
            bridge=SimpleNamespace(),  # type: ignore[arg-type]
            telemetry=SimpleNamespace(),  # type: ignore[arg-type]
            desk=desk.desk,
            cfg=lab.cfg,
        )
    return build_registry(lab.db, ops=ops)


def test_ore_find_measures_from_his_position_and_cites_every_vein(lab: Lab) -> None:
    out = registry(lab).call("ore_find", {"material": "Ferrite"})
    assert not out.is_error, out.text
    lines = out.text.splitlines()
    assert "material: Ferrite" in lines
    assert any(
        line.startswith(
            f"from: his position at the last save of {F.WORLD}: x 100, y 64, z 20"
        )
        for line in lines
    )
    assert "dim: 0 (Overworld)" in lines
    header = lines.index(
        "ref\tmix\tores\tdim\tx\tz\ty_min\ty_max\tdistance\tlayer\tknown\tdepleted"
    )
    rows = [line.split("\t") for line in lines[header + 1 : header + 5]]
    assert [(r[0], r[4], r[5], r[8]) for r in rows] == [
        (ref, str(x), str(z), str(d)) for ref, x, z, d, _, _ in EXPECTED
    ]
    assert rows[0][9] == "primary,between" and rows[2][9] == "sporadic"
    assert rows[3][11] == "1" and rows[3][10] == "1"  # depleted, known
    card = card_build.from_tool("ore_find", out.text, False, lab.db)
    assert cards.validate(card) == []
    assert [r["ref"] for r in card["rows"]] == [e[0] for e in EXPECTED]
    assert [r["distance"] for r in card["rows"]] == [e[3] for e in EXPECTED]


def test_ore_find_from_a_given_point_another_dimension_or_none(lab: Lab) -> None:
    reg = registry(lab)
    out = reg.call("ore_find", {"material": "Ferrite", "dim": 0, "x": 24, "z": 24})
    assert "from: x 24, z 24 in dim 0 (Overworld)" in out.text
    out = reg.call("ore_find", {"material": "Ferrite", "dim": 7})
    assert "no distances: he is in another dimension" in out.text
    out = reg.call("ore_find", {"material": "Ferrite", "near": "none"})
    assert "from:" not in out.text and "rows: 1-5 of" not in out.text
    bad = reg.call("ore_find", {"material": "Ferrite", "x": 1})
    assert bad.is_error and "both x and z" in bad.text
    bad = reg.call("ore_find", {"material": "Ferrite", "x": 1, "z": 1})
    assert bad.is_error and "give dim too" in bad.text
    none = reg.call("ore_find", {"material": "Unobtainium"})
    assert "no loaded vein holds 'Unobtainium'" in none.text
    (lab.world / "level.dat").unlink()
    out = reg.call("ore_find", {"material": "Ferrite"})
    assert "no distances: the world 'lab-ores' has no level.dat" in out.text


def test_player_pos(lab: Lab) -> None:
    out = registry(lab).call("player_pos", {})
    assert not out.is_error, out.text
    lines = out.text.splitlines()
    for line in ("world: lab-ores", "x: 100", "y: 64", "z: 20", "dim: 0 (Overworld)"):
        assert line in lines
    with lab.conn() as c:
        ref = intel.position(c).ref
    assert f"ref: {ref}" in lines and ref.startswith("pos:lab-ores@")
    (lab.world / "level.dat").unlink()
    assert registry(lab).call("player_pos", {}).is_error


def test_the_fast_lane_says_where_he_is_from_player_pos(lab: Lab) -> None:
    """The skill's "Where am I?" row in the fast lane: an answer built from player_pos passes
    the lane's number check only with its pos: citation (review M1)."""
    with lab.conn() as c:
        ref = intel.position(c).ref
    said = "You were at x 100, y 64, z 20 in the Overworld at the last save"
    call = Turn(tools=[{"name": "player_pos", "input": {}}])
    client = ScriptedClient(turns=[call, Turn(f"{said} ({ref}).")])
    out = FastLane(client, registry(lab)).answer("where am I?", prefetch.Prefetch())  # type: ignore[arg-type]
    assert out.checked and out.tool_calls == ["player_pos"], out.problems
    assert out.text == f"{said} ({ref})."
    uncited = ScriptedClient(turns=[call, Turn(f"{said}."), Turn(f"{said}.")])
    out = FastLane(uncited, registry(lab)).answer("where am I?", prefetch.Prefetch())  # type: ignore[arg-type]
    assert not out.checked and out.text == NOT_CHECKED


def test_ore_waypoint_only_proposes_and_clayton_confirms(lab: Lab, desk: Desk) -> None:
    reg = registry(lab, desk)
    assert "ore_waypoint" in {s["name"] for s in reg.specs("deep")}
    assert "ore_waypoint" not in {s["name"] for s in reg.specs("fast")}
    out = reg.call("ore_waypoint", {"vein": "vein:0/10/-5"})
    assert not out.is_error, out.text
    say = 'Add waypoint "Ferrite vein" at 168, 25, -72 in dimension 0 in Lab. Say confirm.'
    assert f"say: {say}" in out.text.splitlines()
    assert desk.marked == [] and not any((lab.jm / "waypoints").glob("*.json"))
    done = desk.confirm()
    assert done.ok, done.text
    assert desk.marked == [
        ("lab", {"name": "Ferrite vein", "x": 168, "y": 25, "z": -72, "dim": 0})
    ]
    doc = json.loads(
        (lab.jm / "waypoints" / "Ferrite vein_168,25,-72.json").read_text(
            encoding="utf-8"
        )
    )
    assert (doc["x"], doc["y"], doc["z"], doc["dimensions"], doc["origin"]) == (
        168,
        25,
        -72,
        [0],
        "JourneyMap",
    )
    bad = reg.call("ore_waypoint", {"vein": "vein:0/99/99"})
    assert bad.is_error and "no loaded vein is vein:0/99/99" in bad.text
    assert (
        registry(lab).call("ore_waypoint", {"vein": "vein:0/10/-5"}).is_error
    )  # no desk


# --- waypoints -------------------------------------------------------------------------------


def test_waypoint_names_and_heights() -> None:
    assert intel.waypoint_name("Ferrite") == "Ferrite vein"
    assert (
        intel.waypoint_name("Nether Quartz (Certus) / Mix")
        == "Nether Quartz Certus Mix vein"
    )
    assert intel.waypoint_name("A very long mix name that goes on") == (
        "A very long mix name that goes"
    )
    assert intel.waypoint_name("Thirty characters of mix name!") == (
        "Thirty characters of mix name"
    )
    assert intel.waypoint_name("x" * 40) == "x" * 32
    assert intel.waypoint_name("§§§") == "GOS vein"
    assert intel.waypoint_name("-.'Lunite") == "Lunite vein"
    for name in ("Ferrite", "§a", "x" * 80, "Ünïcode"):
        assert waypoint.NAME.fullmatch(intel.waypoint_name(name))


def test_a_nether_vein_waypoint_is_at_overworld_scale(lab: Lab, desk: Desk) -> None:
    c = lab.conn()
    try:
        out = intel.propose_waypoint(
            lab.cfg, desk.desk, c, "vein:-1/-5/7", source="dashboard"
        )
    finally:
        c.close()
    assert out.action is not None and out.action.args == {
        "name": "Copperish vein",
        "x": -72,  # centre (-72, 120) in the Nether; copperish heights 5-60 -> y 32
        "y": 32,
        "z": 120,
        "dim": -1,
    }
    assert desk.confirm().ok
    doc = json.loads(
        (lab.jm / "waypoints" / "Copperish vein_-576,32,960.json").read_text(
            encoding="utf-8"
        )
    )
    assert (doc["x"], doc["z"], doc["dimensions"]) == (-576, 960, [-1])


def test_play_veins_mark_play_only_through_the_confirmed_path(
    tmp_path: Path, lab: Lab
) -> None:
    # Veins loaded from Play's configured world: the proposal targets Play. The writer is a
    # fake here, and the real one refuses Play as gos-dev (D75); nothing reaches a real Play.
    play = tmp_path / "instances" / "GTNH-Play"
    (play / ".minecraft" / "saves" / "survival").mkdir(parents=True)
    (play / "instance.cfg").write_text("name=GTNH-Play\n", encoding="utf-8")
    toml = lab.toml.read_text(encoding="utf-8") + 'world = "survival"\n'
    lab.toml.write_text(toml, encoding="utf-8")
    cfg = config.load(lab.toml)
    with sqlite3.connect(lab.db) as c:
        src = json.loads(
            c.execute("SELECT value FROM kv WHERE key='ores.veins'").fetchone()[0]
        )
        src["world"] = str(play / ".minecraft" / "saves" / "survival")
        c.execute("UPDATE kv SET value=? WHERE key='ores.veins'", (json.dumps(src),))
    c.close()
    (tmp_path / "fake").mkdir()
    fake = Desk(tmp_path / "fake", cfg, fake_play=True)
    conn = lab.conn()
    try:
        out = intel.propose_waypoint(
            cfg, fake.desk, conn, "vein:0/10/-5", source="dashboard"
        )
        assert out.action is not None and out.action.target == "play"
        assert fake.confirm().ok and fake.marked[0][0] == "play"
        (tmp_path / "real").mkdir()
        real = Desk(tmp_path / "real", cfg)
        intel.propose_waypoint(cfg, real.desk, conn, "vein:0/10/-5", source="dashboard")
        refused = real.confirm()
        assert not refused.ok and "never written" in refused.text
        assert not (play / ".minecraft" / "journeymap").exists()
        real.store.close()
    finally:
        conn.close()
        fake.store.close()


def test_a_world_gos_toml_doesnt_name_is_never_marked(lab: Lab, desk: Desk) -> None:
    lab.toml.write_text(
        lab.toml.read_text(encoding="utf-8").replace(
            f'lab_world = "{F.WORLD}"', 'lab_world = "other"'
        ),
        encoding="utf-8",
    )
    cfg = config.load(lab.toml)
    c = lab.conn()
    try:
        with pytest.raises(intel.OreIntelError, match="neither"):
            intel.propose_waypoint(cfg, desk.desk, c, "vein:0/10/-5", source="agent")
    finally:
        c.close()
    assert desk.desk.pending() is None


# --- the Ores view's routes ------------------------------------------------------------------


def app_for(lab: Lab, desk: Desk | None) -> TestClient:
    from gregtec.api.app import _json_body

    app = FastAPI()
    who = Principal("device", "phone")

    def principal(request: Request) -> Principal:
        return who

    core = SimpleNamespace(
        cfg=lab.cfg,
        ops=None if desk is None else SimpleNamespace(desk=desk.desk),
    )
    view.add_routes(app, core, principal, _json_body)  # type: ignore[arg-type]
    return TestClient(app)


def test_the_view_payload(lab: Lab, desk: Desk) -> None:
    client = app_for(lab, desk)
    p = client.get("/api/ores").json()
    assert p["card"] is None and p["material"] is None
    assert p["source"]["world"] == F.WORLD and p["source"]["instance"] == "lab"
    assert p["source"]["veins"] == 8
    assert p["position"] == {
        "x": 100,
        "y": 64,
        "z": 20,
        "dim": 0,
        "dim_name": "Overworld",
        "world": F.WORLD,
        "age_s": p["position"]["age_s"],
    }
    assert [d["dim"] for d in p["dims"]] == [-1, 0, 7] and p["dims"][1]["veins"] == 5
    assert (
        "Ferrite" in p["materials"] and p["can_mark"] is True and p["mark_note"] is None
    )
    p = client.get("/api/ores", params={"material": "Ferrite"}).json()
    assert cards.validate(p["card"]) == [] and p["dim"] == 0
    assert [(r["ref"], r["distance"]) for r in p["card"]["rows"]] == [
        (e[0], e[3]) for e in EXPECTED
    ]
    p = client.get("/api/ores", params={"material": "Ferrite", "dim": 7}).json()
    assert p["card"]["notes"][0] == "no distances: he is in another dimension"
    assert client.get("/api/ores", params={"dim": 5000}).status_code == 422
    assert client.get("/api/ores", params={"material": "x" * 101}).status_code == 422


def test_the_view_proposes_a_waypoint_the_core_builds(lab: Lab, desk: Desk) -> None:
    client = app_for(lab, desk)
    r = client.post("/api/ores/waypoint", json={"vein": "vein:0/-2/4"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["action"]["state"] == "proposed"
        and body["action"]["source"] == "dashboard"
    )
    assert body["text"].startswith('Add waypoint "Ferrite vein" at -24, 25, 72')
    assert desk.marked == []  # nothing is written before his confirm
    # The browser's numbers are never used: only the vein id is read.
    r = client.post("/api/ores/waypoint", json={"vein": "vein:0/-2/4", "x": 9, "y": 9})
    assert r.json()["action"]["args"]["x"] == -24
    assert client.post("/api/ores/waypoint", json={"vein": "nope"}).status_code == 409
    assert client.post("/api/ores/waypoint", json={"vein": 3}).status_code == 400
    assert client.post("/api/ores/waypoint", content=b"x").status_code == 415
    no_desk = app_for(lab, None)
    assert no_desk.get("/api/ores").json()["can_mark"] is False
    assert (
        no_desk.post("/api/ores/waypoint", json={"vein": "vein:0/-2/4"}).status_code
        == 503
    )


def test_without_a_db_the_view_says_so(tmp_path: Path) -> None:
    empty = SimpleNamespace(gos_home=tmp_path / "empty")
    with pytest.raises(HTTPException) as exc:
        view.read(empty, None, None, False)  # type: ignore[arg-type]
    assert exc.value.status_code == 404 and "gos ores load" in str(exc.value.detail)
