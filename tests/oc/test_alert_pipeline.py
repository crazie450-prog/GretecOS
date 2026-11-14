"""WO-7.5 acceptance on a fake clock: alerts end to end, from the real OC agent's posts to the
dashboard (plan 8 WO-7.5; D46, D280, D281).

tests/oc/alert_pipe.lua runs oc/gos-agent.lua in fakeoc's world and sends every POST here over a
pipe, to the core's own ``POST /oc/telemetry`` (the FastAPI app in-process, an ``oc-lab`` token).
Virtual time drives everything: ``time.time`` is the agent's clock, and the core's watcher tick
(:meth:`Ops.tick`, alert rules included) runs once per virtual second as it does once per real
second in the core. The story:

- at 10 s the dashboard confirms a craft (D59), which AE2 finishes a few seconds later;
- at 20 s the LSC drops to 10 %, the EBF stalls and reports 2 maintenance problems;
- at 100 s Clayton presses Esc (the watchers report the pause, D258) and OC stops: the posts no
  longer reach the core; at 110 s the world recovers, unseen;
- at 160 s the game runs again: posts reach the core, while the watchers' flag lags 20 s more.

Checked: each of the four alerts fires once, as an AlertCard in ``/api/state`` (crafting done on
the Base view only), on ``/api/base/live``, as an ``alert`` push with its voice line, on the OC
board (power low, crafting done); while paused they are held stale, not cleared and not fired
again; after the unpause they clear on fresh readings only. Needs Lua 5.3 like the other agent
tests. The Lab check with the game unfocused and no menu open (D46) is a PC step (runbook
docs/runbooks/oc_agent.md).
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gregtec.api import dashboard
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.bridge import http as bridge_http
from gregtec.bridge import ops as bridge_ops
from gregtec.cards import validate
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec.watchers.feed import GameFeed

from .test_oc_agent import REPO, _lua53, _need

ADMIN = "admin-" + "d" * 40
BASE = 2_000_000.0
CRAFT_AT, PAUSE_AT, UNPAUSE_AT, FLAG_LAG = 10.0, 100.0, 160.0, 20.0
BARE_REPLY = (
    '{"v":1,"ok":true,"cmds":[]}'  # what the agent sees while nothing reaches the core
)
RULES = ("power_low", "maintenance", "machine_stalled", "crafting_done")


class VClock:
    def __init__(self) -> None:
        self.now = BASE

    def __call__(self) -> float:
        return self.now


class Sock:
    """A dashboard or voice socket that keeps what the core pushes."""

    trusted = True

    def __init__(self) -> None:
        self.got: list[dict[str, Any]] = []

    def put(self, text: str) -> None:
        self.got.append(json.loads(text))


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


@pytest.fixture
def world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Core, VClock]]:
    clock = VClock()
    monkeypatch.setattr(time, "time", clock)  # before anything keeps time.time
    monkeypatch.setattr(bridge_http, "RATE", (1_000_000, 1_000_000.0))
    cfg = Config(gos_home=tmp_path)
    store = Store(tmp_path / "data" / "gos.sqlite")
    ops = bridge_ops.build(cfg, store)
    # Default arguments kept the real time.time: every part of the bridge on the agent's clock.
    assert ops.live is not None and ops.displays is not None and ops.alerts is not None
    for part in (ops.telemetry, ops.desk, ops.live, ops.displays, ops.alerts):
        part.clock = clock
    core = Core(
        cfg=cfg,
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        oc=ops.bridge,
        ops=ops,
    )
    ops.desk.on_change = ops.changed(None)
    core.game = GameFeed(clock=clock)
    board = dashboard.board_of(core)
    board.clients = lambda cfg: ["lab"]  # the Lab client runs
    board.running = ["lab"]
    yield core, clock
    store.close()


def _admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN}"}


def test_alerts_from_a_fakeoc_post_to_the_dashboard(
    world: tuple[Core, VClock],
) -> None:
    core, clock = world
    lua = _need(_lua53(), "Lua 5.3")
    ops = core.ops
    assert ops is not None and ops.alerts is not None
    engine = ops.alerts
    events: list[Any] = []
    engine.sinks.append(events.append)
    sock = Sock()
    core.sockets.add(sock)  # type: ignore[arg-type]
    token = core.auth.issue_client_token("oc-lab")
    looks: dict[str, Any] = {}
    craft: dict[str, Any] = {}
    proc = subprocess.Popen(
        [lua, "tests/oc/alert_pipe.lua", token],
        cwd=REPO,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        with TestClient(create_app(core), base_url="http://127.0.0.1:8765") as client:
            while True:
                line = proc.stdout.readline()
                if not line:
                    err = proc.stderr.read() if proc.stderr else ""
                    pytest.fail(f"the agent side stopped: {err[-3000:]}")
                req = json.loads(line)
                if req["op"] == "done":
                    seen = req
                    break
                t = float(req["t"])
                while clock.now + 1 <= BASE + t:  # the core's watcher: once a second
                    clock.now += 1
                    ops.tick()
                clock.now = BASE + t
                paused = PAUSE_AT <= t < UNPAUSE_AT + FLAG_LAG
                core.game.receive(  # type: ignore[union-attr]
                    {
                        "instance": "lab",
                        "paused": {
                            "paused": paused,
                            "since": BASE + PAUSE_AT if paused else None,
                        },
                        "events": [],
                    }
                )
                if (
                    PAUSE_AT <= t < UNPAUSE_AT
                ):  # OC is stopped: nothing reaches the core
                    reply = BARE_REPLY
                else:
                    r = client.post(
                        "/oc/telemetry",
                        content=req["body"].encode("ascii"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {token}",
                        },
                    )
                    assert r.status_code == 200
                    reply = r.text
                if t >= CRAFT_AT and not craft:  # after a post: fresh
                    out = ops.desk.propose(
                        "craft",
                        {
                            "name": "gregtech:gt.metaitem.01",
                            "damage": 11305,
                            "amount": 64,
                        },
                        target="lab",
                    )
                    assert out.action is not None, out.text
                    assert ops.desk.confirm("dashboard", out.action.id).ok
                    craft["id"] = out.action.id
                for name, at in (("firing", 95.0), ("paused", 150.0), ("after", 235.0)):
                    if t >= at and name not in looks:
                        state = client.get("/api/state", headers=_admin()).json()
                        live = client.get("/api/base/live", headers=_admin()).json()
                        looks[name] = {"state": state, "live": live, "t": t}
                proc.stdin.write(json.dumps({"body": reply}) + "\n")
                proc.stdin.flush()
    finally:
        proc.kill()
        proc.wait(timeout=10)

    def at(e: Any) -> float:  # noqa: ANN401
        return e.ts - BASE

    fired = {e.alert.rule: at(e) for e in events if e.kind == "fired"}
    # Each rule fired once, after its hold on fresh readings, before the pause.
    assert sorted(e.alert.rule for e in events if e.kind == "fired") == sorted(RULES)
    assert CRAFT_AT < fired["crafting_done"] < CRAFT_AT + 30, fired
    assert 20 <= fired["maintenance"] < 20 + 15 + 10, fired  # the next sensor read
    assert 20 + 30 <= fired["power_low"] < 20 + 30 + 10, fired
    assert 20 + 60 <= fired["machine_stalled"] < 20 + 60 + 10, fired
    # Held, never cleared or fired again while the game was paused (D46, plan 7.9).
    stale = [e for e in events if e.kind in ("stale", "fresh")]
    assert [(e.kind, e.why) for e in stale] == [("stale", "paused"), ("fresh", None)]
    # Here the watchers' post rides with the agent's next exchange (a cycle, 5 s); in the core
    # it reaches the engine within its 1 s watcher tick (D258's path is about 2 s).
    assert PAUSE_AT <= at(stale[0]) < PAUSE_AT + 6
    assert UNPAUSE_AT <= at(stale[1]) < UNPAUSE_AT + 10  # OC's own posts, not the flag
    cleared = {e.alert.rule: at(e) for e in events if e.kind == "cleared"}
    assert set(cleared) == {"power_low", "maintenance", "machine_stalled"}, cleared
    for rule, when in cleared.items():
        # clear_s after the first fresh good reading; a Problems count comes with the sensor
        # lines, read every sensor_s (15 s here, rotating over the devices; D279 keeps the last)
        slack = 15 + 30 if rule == "maintenance" else 15
        assert UNPAUSE_AT + 30 <= when < UNPAUSE_AT + 30 + slack, (rule, when)

    # The dashboard while firing: AlertCards for the three warn/err ones, valid by the catalog.
    cards = {
        c["id"]: c
        for c in looks["firing"]["state"]["alerts"]
        if c["id"].startswith("oc-")
    }
    assert {c["title"].split(":")[0] for c in cards.values()} == {
        "Power low",
        "Maintenance",
        "Machine stalled",
    }
    for c in cards.values():
        assert not validate(c), c
        assert "tel:lab/" in c["body"]
    lab = looks["firing"]["live"]["bindings"][0]
    assert lab["held"] is None
    assert {a["rule"] for a in lab["alerts"]} == set(RULES)
    # While paused: held and marked stale, on the cards and the Base view.
    lab = looks["paused"]["live"]["bindings"][0]
    assert lab["held"] == "paused"
    assert {a["stale"] for a in lab["alerts"] if a["rule"] != "crafting_done"} == {
        "paused"
    }
    held = [c for c in looks["paused"]["state"]["alerts"] if c["id"].startswith("oc-")]
    assert len(held) == 3 and all(c["body"].startswith("Held: ") for c in held)
    # After: only crafting done is still shown (for done_show_s), nothing on the cards.
    assert not [
        c for c in looks["after"]["state"]["alerts"] if c["id"].startswith("oc-")
    ]
    assert [a["rule"] for a in looks["after"]["live"]["bindings"][0]["alerts"]] == [
        "crafting_done"
    ]

    # The voice line: one ``alert`` push per event, a ``say`` on each fired one.
    pushed = [m["data"] for m in sock.got if m.get("type") == "alert"]
    said = [p["say"] for p in pushed if p["event"] == "fired"]
    # (No export run or game DB here, so the names are GT's meta names and the item's key.)
    assert sorted(said) == sorted(
        [
            "Lab: crafting done, 64 gregtech:gt.metaitem.01@11305.",
            "Lab: blastfurnace needs maintenance.",
            "Lab: power low. supercapacitor at 10.0 percent.",
            "Lab: blastfurnace has stalled.",
        ]
    )
    assert len(pushed) == len(events)
    # The OC board showed both alert lines the problems don't already say.
    assert (
        seen.get("board_low") is not None
        and seen["board_low"] - fired["power_low"] <= 10
    )
    assert seen.get("board_done") is not None
    # The alert table: a row per fired alert (Appendix D).
    rows = core.store.rows("SELECT kind, state FROM alert ORDER BY id")
    assert sorted(r["kind"] for r in rows) == sorted(RULES)
    assert {r["kind"]: r["state"] for r in rows}["power_low"] == "cleared"
    # The craft itself ran once, confirmed by the dashboard (D59).
    done = ops.desk.get(craft["id"])
    assert done is not None and done.state == "done"
