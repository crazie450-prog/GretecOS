"""WO-7.1: OC telemetry into tel_machine / tel_sample, staleness, the locator, and the waypoint
writer (gregtec.bridge.telemetry, gregtec.bridge.waypoint; plan 7.9, Appendix D; D219, D220)."""

from __future__ import annotations

import copy
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.bridge import waypoint
from gregtec.bridge.actions import SETTLE_S, ActionDesk, ActionError
from gregtec.bridge.ingest import Bridge, Outcome
from gregtec.bridge.telemetry import HEARTBEAT_S, STALE_S, Telemetry
from gregtec.config import Config
from gregtec.store import Store

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "oc"
FIRST = json.loads((FIXTURES / "telemetry-first.json").read_text(encoding="utf-8"))
RESULTS = json.loads((FIXTURES / "telemetry-results.json").read_text(encoding="utf-8"))
EBF = "2e000000-0000-4000-8000-000000000008"


class Clock:
    def __init__(self) -> None:
        self.now = 2_000_000.0

    def __call__(self) -> float:
        return self.now


class Rig:
    def __init__(self, folder: Path) -> None:
        self.clock = Clock()
        self.store = Store(folder / "gos.sqlite")
        self.tel = Telemetry(self.store, clock=self.clock)
        self.desk = ActionDesk(self.store, locate=self.tel.locate, clock=self.clock)
        self.tel.results = self.desk.results
        self.bridge = Bridge(self.tel, self.desk)

    def send(self, doc: dict[str, Any], binding: str = "lab") -> Outcome:
        return self.bridge.receive(
            json.dumps(doc).encode(),
            transport="http",
            source="test",
            binding=binding,
            now=self.clock.now,
        )

    def samples(self, src: str | None = None) -> list[tuple[Any, ...]]:
        where, params = ("WHERE src = ?", (src,)) if src else ("", ())
        return [
            tuple(r)
            for r in self.store.rows(
                f"SELECT ts, src, key, value, text FROM tel_sample {where} ORDER BY rowid",
                params,
            )
        ]


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.store.close()


def msg(seq: int, **changes: object) -> dict[str, Any]:
    doc = copy.deepcopy(FIRST)
    doc["seq"] = seq
    doc.update(changes)
    return doc


def test_a_message_fills_the_tables(rig: Rig) -> None:
    assert rig.send(msg(1)).ok
    rows = {
        r["id"]: dict(r)
        for r in rig.store.rows("SELECT * FROM tel_machine ORDER BY id")
    }
    ebf = rows[f"lab/{EBF}"]
    assert (ebf["name"], ebf["x"], ebf["y"], ebf["z"]) == (
        "multimachine.blastfurnace",
        120,
        64,
        -35,
    )
    assert ebf["status"] == "running" and ebf["last_seen"] == int(rig.clock.now)
    assert (
        ebf["mte_id"] is None and ebf["dim"] is None
    )  # no OC method gives them (D214)
    assert {r["status"] for r in rows.values()} >= {"power", "running"}
    keys = {(s[1].split("/")[-1][:8], s[2]) for s in rig.samples()}
    assert ("2e000000", "progress") in keys and ("2e000000", "active") in keys
    assert ("4c000000", "eu_str") in keys  # the LSC's exact decimal, as text
    assert ("5e000000", "item:gregtech:gt.metaitem.01@11305") in keys
    lsc = [s for s in rig.samples() if s[2] == "eu_str"]
    assert lsc[0][3] is None and lsc[0][4] == "123456789012345678901234"


def test_samples_are_written_on_change_and_every_heartbeat(rig: Rig) -> None:
    src = f"lab/{EBF}"
    rig.send(msg(1))
    first = len(rig.samples(src))
    rig.clock.now += 5
    rig.send(msg(2))  # nothing changed
    assert len(rig.samples(src)) == first
    rig.clock.now += 5
    changed = msg(3)
    changed["machines"][0]["progress"] = 125
    rig.send(changed)
    new = rig.samples(src)[first:]
    assert [(s[2], s[3]) for s in new] == [("progress", 125.0)]
    rig.clock.now += HEARTBEAT_S
    rig.send(msg(4, machines=changed["machines"]))
    assert (
        len(rig.samples(src)) > first + 1
    )  # the heartbeat rewrites unchanged readings


def test_a_reading_the_agent_left_out_is_not_written(rig: Rig) -> None:
    doc = msg(1)
    del doc["machines"][0]["progress"]  # the call failed: left out, never estimated
    rig.send(doc)
    assert "progress" not in {s[2] for s in rig.samples(f"lab/{EBF}")}


def test_a_repeated_message_is_recorded_once(rig: Rig) -> None:
    rig.send(msg(1))
    before = len(rig.samples())
    out = rig.send(msg(1))
    assert out.ok and out.repeat
    assert len(rig.samples()) == before


def test_staleness_is_marked_never_extrapolated(rig: Rig) -> None:
    rig.send(msg(1))
    rig.clock.now += STALE_S
    rig.tel.sweep()
    assert rig.tel.snapshot()["agents"][0]["stale"] is False
    rig.clock.now += 1
    rig.tel.sweep()
    snap = rig.tel.snapshot()
    assert snap["agents"][0]["stale"] is True
    ebf = next(m for m in snap["machines"] if m["id"] == f"lab/{EBF}")
    assert ebf["stale"] and ebf["status"] == "stale" and ebf["age_s"] == STALE_S + 1
    assert ebf["readings"]["progress"] == 120  # the last value, as it was: not moved on
    before = len(rig.samples())
    rig.clock.now += 600
    rig.tel.sweep()
    assert len(rig.samples()) == before  # a gap writes nothing
    rig.send(msg(2))
    assert (
        rig.store.rows("SELECT status FROM tel_machine WHERE id = ?", (f"lab/{EBF}",))[
            0
        ][0]
        == "running"
    )


def test_a_reading_the_newest_message_left_out_is_not_shown_as_current(
    rig: Rig, tmp_path: Path
) -> None:
    """Review M1: a failed call leaves a key out; its old value isn't shown with a fresh age."""
    rig.send(msg(1))
    ebf_id = f"lab/{EBF}"

    def readings(tel: Telemetry) -> dict[str, Any]:
        snap = tel.snapshot()
        ebf = next(m for m in snap["machines"] if m["id"] == ebf_id)
        assert ebf["age_s"] == 0 and not ebf["stale"]
        return ebf["readings"]

    assert readings(rig.tel)["active"] == 1.0
    for later in (10, 600):  # within and past the heartbeat
        rig.clock.now += later
        doc = msg(int(rig.clock.now))
        del doc["machines"][0]["active"], doc["machines"][0]["progress"]
        rig.send(doc)
        shown = readings(rig.tel)
        assert "active" not in shown and "progress" not in shown, later
        assert shown["progress_max"] == doc["machines"][0]["progress_max"]
    # After a restart (no memory of what the newest message carried), the heartbeat window
    # still leaves out what that message didn't carry.
    restarted = Telemetry(rig.store, clock=rig.clock)
    shown = readings(restarted)
    assert "active" not in shown and "progress" not in shown
    assert shown["progress_max"] == doc["machines"][0]["progress_max"]


def test_a_binding_has_at_most_so_many_computers(rig: Rig) -> None:
    """Review L5: a token can't fill the core with made-up OC computers."""
    from gregtec.bridge.telemetry import MAX_AGENTS_PER_BINDING

    base = FIRST["computer"]["address"]
    assert rig.send(msg(1)).ok
    for i in range(MAX_AGENTS_PER_BINDING - 1):
        addr = f"{i:08x}" + base[8:]
        assert rig.send(msg(1, computer=dict(FIRST["computer"], address=addr))).ok
    extra = msg(1, computer=dict(FIRST["computer"], address="f" * 8 + base[8:]))
    out = rig.send(extra)
    assert not out.ok and out.err == "internal"
    assert rig.send(msg(2, computer=dict(FIRST["computer"], address=base))).ok  # known
    assert rig.send(extra, binding="play").ok  # another binding has its own count


def test_the_locator_picks_the_computer_that_can_run_it(rig: Rig) -> None:
    with pytest.raises(ActionError, match="no fresh telemetry"):
        rig.tel.locate("lab", "say", {"text": "hi"})
    rig.send(msg(1))
    address = FIRST["computer"]["address"]
    assert rig.tel.locate("lab", "say", {"text": "hi"}) == address
    assert rig.tel.locate("lab", "craft", {}) == address
    assert rig.tel.locate("lab", "machine_pause", {"addr": EBF}) == address
    with pytest.raises(ActionError, match="reports the machine"):
        rig.tel.locate("lab", "machine_pause", {"addr": "9" * 8 + EBF[8:]})
    with pytest.raises(ActionError, match="no fresh telemetry"):
        rig.tel.locate("play", "say", {"text": "hi"})  # Lab's agent is not Play's
    other = msg(1, computer=dict(FIRST["computer"], address="7" * 8 + address[8:]))
    rig.send(other)
    with pytest.raises(ActionError, match="2 lab OC computers report a chat box"):
        rig.tel.locate("lab", "say", {"text": "hi"})
    rig.clock.now += STALE_S + 1
    with pytest.raises(ActionError, match="game may be paused"):
        rig.tel.locate("lab", "say", {"text": "hi"})


def test_the_whole_loop_through_the_bridge(rig: Rig) -> None:
    rig.send(msg(1))
    out = rig.desk.propose("say", {"text": "hello"}, target="lab")
    assert out.action is not None
    # Unconfirmed: the reply carries nothing.
    accepted = rig.send(msg(2))
    assert rig.bridge.reply(accepted)[0]["cmds"] == []
    rig.clock.now += SETTLE_S  # a plain "confirm" takes a proposal read back first
    rig.desk.confirm("voice")
    accepted = rig.send(msg(3))
    doc, ids = rig.bridge.reply(accepted)
    assert ids == [out.action.id]
    assert doc["cmds"] == [
        {
            "id": out.action.id,
            "kind": "say",
            "args": {"text": "hello"},
            "confirmed_via": "voice",
        }
    ]
    # Play's binding with Lab's computer address gets nothing (D217).
    assert rig.bridge.reply(rig.send(msg(1), binding="play"))[1] == []
    result = msg(
        4,
        results=[
            {"id": out.action.id, "kind": "say", "ok": True, "state": "done", "at": 2.4}
        ],
    )
    rig.send(result)
    assert rig.desk.get(out.action.id).state == "done"  # type: ignore[union-attr]


def test_the_contract_fixture_with_results_is_taken(rig: Rig) -> None:
    out = rig.send(RESULTS)
    assert out.ok  # results for ids the core never sent are ignored, not refused


# --- waypoints ------------------------------------------------------------------------------


def world(tmp_path: Path, game: str = "minecraft") -> tuple[Config, Path]:
    cfg = Config(
        gos_home=tmp_path / "home",
        prism_instances=tmp_path / "inst",
        lab_world="lab-ops",
    )
    sp = (
        tmp_path / "inst" / "GTNH-Lab" / game / "journeymap" / "data" / "sp" / "lab-ops"
    )
    sp.mkdir(parents=True)
    return cfg, sp


WP = {"name": "Base", "x": 100, "y": 64, "z": -200, "dim": 0}


def test_a_waypoint_file(tmp_path: Path) -> None:
    cfg, sp = world(tmp_path)
    where = waypoint.place(cfg, "lab", as_dev=False)
    assert waypoint.write(where, WP) == (
        f"wrote waypoint Base_100,64,-200.json; {waypoint.SHOWS_WHEN}"
    )
    data = json.loads(
        (sp / "waypoints" / "Base_100,64,-200.json").read_text(encoding="utf-8")
    )
    assert data == {
        "id": "Base_100,64,-200",
        "name": "Base",
        "icon": "waypoint-normal.png",
        "x": 100,
        "y": 64,
        "z": -200,
        "r": 0,
        "g": 200,
        "b": 255,
        "enable": True,
        "type": "Normal",
        "origin": "JourneyMap",  # Waypoint.Origin's name (V23, D271)
        "dimensions": [0],
    }
    assert (
        waypoint.write(where, WP)
        == f"waypoint Base_100,64,-200.json is already there; {waypoint.SHOWS_WHEN}"
    )
    assert [p.name for p in (sp / "waypoints").iterdir()] == ["Base_100,64,-200.json"]


def test_a_nether_waypoint_is_stored_at_overworld_scale(tmp_path: Path) -> None:
    name, doc = waypoint.document(dict(WP, dim=-1, x=10, z=-3))
    assert (doc["x"], doc["z"], doc["dimensions"]) == (80, -24, [-1])
    assert name == "Base_80,64,-24.json"


def test_waypoint_refusals(tmp_path: Path) -> None:
    cfg, _ = world(tmp_path, ".minecraft")
    with pytest.raises(
        waypoint.WaypointError, match="Play's JourneyMap folder is never written"
    ):
        waypoint.place(cfg, "play", as_dev=True)
    with pytest.raises(waypoint.WaypointError, match="no \\[paths\\] world"):
        waypoint.place(cfg, "play", as_dev=False)
    missing = waypoint.Place(cfg.instance("lab"), "other-world")
    with pytest.raises(waypoint.WaypointError, match="no data for the world"):
        waypoint.write(missing, WP)
    with pytest.raises(waypoint.WaypointError, match="waypoint name"):
        waypoint.document(dict(WP, name="a/b"))


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)
def test_a_waypoint_never_goes_through_a_link(tmp_path: Path) -> None:
    cfg, sp = world(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, sp / "waypoints")
    with pytest.raises(waypoint.WaypointError, match="link or junction"):
        waypoint.write(waypoint.place(cfg, "lab", as_dev=False), WP)
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)
def test_no_folder_is_made_through_a_linked_world(tmp_path: Path) -> None:
    """Review L3: the world folder itself a link: nothing is created at its target."""
    cfg, sp = world(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sp.rmdir()
    os.symlink(outside, sp)
    with pytest.raises(waypoint.WaypointError, match="link or junction"):
        waypoint.write(waypoint.place(cfg, "lab", as_dev=False), WP)
    assert list(outside.iterdir()) == []
