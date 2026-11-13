"""WO-7.6 acceptance on a fake clock: the real agent's breeding station driven by the real core
(plan 8 WO-7.6: breeds a 3-step target from starter seeds unattended and stocks 4, never uses
the last seed of a parent, stops cleanly mid-run; unconfirmed start and stop never run).

tests/oc/station_pipe.lua runs oc/gos-agent.lua with oc/crop/station.lua in fakeoc's world (a
chest of starter seeds, an MV Crop Breeder whose runs fail as injected, three Seed Generators,
one transposer) and sends every exchange here, to the core's own ``POST /oc/telemetry`` (the
FastAPI app in-process, an ``oc-lab`` token). The plan is gregtec.crops.station's for the
made-up world in tests/fixtures/crops/stationworld.py (the same crops and labels), proposed and
confirmed through the core's action desk on the agent's virtual clock:

- a start proposed and never confirmed expires; nothing moves while it waits;
- a stop proposed while nothing runs, unconfirmed, never reaches the agent;
- the start proposed again and confirmed by the dashboard's button runs the plan to the end
  (or, in the stop run, a confirmed stop mid-run winds it down).

Needs Lua 5.3 like the other agent tests.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.bridge import http as bridge_http
from gregtec.bridge import ops as bridge_ops
from gregtec.bridge.ingest import REPLY_MAX, validators
from gregtec.config import Config
from gregtec.crops import rules, station
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

from .test_oc_agent import REPO, _lua53, _need

ADMIN = "admin-" + "c" * 40
BASE = 2_000_000_000.0  # the desk's clock: this plus the agent's virtual seconds
FIRST_AT, SECOND_AT = (
    10.0,
    100.0,
)  # the unconfirmed start, then the one Clayton confirms


def _world() -> Any:
    path = REPO / "tests" / "fixtures" / "crops" / "stationworld.py"
    spec = importlib.util.spec_from_file_location("_wo76_pipe_world", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


@pytest.fixture
def core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Core]:
    monkeypatch.setattr(bridge_http, "RATE", (1_000_000, 1_000_000.0))
    cfg = Config(gos_home=tmp_path)
    store = Store(tmp_path / "data" / "gos.sqlite")
    ops = bridge_ops.build(cfg, store)
    c = Core(
        cfg=cfg,
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        oc=ops.bridge,
        ops=ops,
    )
    ops.desk.on_change = ops.changed(None)
    yield c
    store.close()


def _drive(core: Core, stop_at: float | None) -> dict[str, Any]:
    lua = _need(_lua53(), "Lua 5.3")
    assert core.ops is not None
    desk = core.ops.desk
    vt = [0.0]
    desk.clock = lambda: BASE + vt[0]
    w = _world()
    plan = station.build(w.facts(), w.HAVE, "cropsnh:tomato", tier=rules.MV)
    token = core.auth.issue_client_token("oc-lab")
    log: dict[str, Any] = {"cmds": [], "states": []}
    proc = subprocess.Popen(
        [lua, "tests/oc/station_pipe.lua", token, "4000"],
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
                    req["log"] = log
                    return req
                vt[0] = req["t"]
                doc = json.loads(req["body"])
                log["states"].append((req["t"], doc.get("crops", {}).get("state")))
                if req["t"] >= FIRST_AT and "first" not in log:
                    out = station.propose_start(desk, plan, target="lab", source="test")
                    assert out.action is not None
                    log["first"] = out.action.id  # never confirmed: it expires
                if req["t"] >= FIRST_AT + 70 and "idle_stop" not in log:
                    out = station.propose_stop(desk, target="lab", source="test")
                    assert out.action is not None
                    log["idle_stop"] = out.action.id  # cancelled, never confirmed
                    assert desk.cancel("voice", out.action.id).ok
                if req["t"] >= SECOND_AT and "second" not in log:
                    out = station.propose_start(desk, plan, target="lab", source="test")
                    assert out.action is not None
                    assert desk.confirm(
                        "dashboard", out.action.id
                    ).ok  # the button, by id
                    log["second"], log["confirmed_at"] = out.action.id, req["t"]
                if stop_at is not None and req["t"] >= stop_at and "stop" not in log:
                    out = station.propose_stop(desk, target="lab", source="test")
                    assert out.action is not None
                    assert desk.confirm("dashboard", out.action.id).ok
                    log["stop"], log["stop_at"] = out.action.id, req["t"]
                r = client.post(
                    "/oc/telemetry",
                    content=req["body"].encode("ascii"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                )
                assert r.status_code == 200
                assert len(r.text.encode("ascii")) <= REPLY_MAX
                reply = json.loads(r.text)
                assert not list(validators()["reply"].iter_errors(reply)), reply
                log["cmds"] += [(req["t"], c["kind"], c["id"]) for c in reply["cmds"]]
                proc.stdin.write(json.dumps({"body": r.text}) + "\n")
                proc.stdin.flush()
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _never_the_last_seed(seen: dict[str, Any]) -> None:
    assert seen["loads"], "the breeder was loaded"
    for load in seen["loads"]:
        assert load["n"] == 1 and load["left"] >= 1, load


def test_unattended_from_starters_to_4_stocked(core: Core) -> None:
    seen = _drive(core, stop_at=None)
    log = seen["log"]
    assert core.ops is not None
    desk = core.ops.desk
    # unconfirmed: the first start expired, the idle stop was cancelled; neither reached OC
    assert desk.get(log["first"]).state == "expired"  # type: ignore[union-attr]
    assert desk.get(log["idle_stop"]).state == "cancelled"  # type: ignore[union-attr]
    sent = [c for c in log["cmds"]]
    assert [c[2] for c in sent] == [log["second"]], sent
    assert seen["first_move"] >= log["confirmed_at"], "nothing moved before the confirm"
    assert all(
        s in (None, "idle") for t, s in log["states"] if t <= log["confirmed_at"]
    )
    # the run: 3 steps from starter seeds, failures retried, 4 stocked
    assert seen["phase"] == "done", (seen["note"], seen["stock"], seen["runs"])
    assert seen["tomato"] >= 4
    assert seen["runs"] >= 7  # the injected failures were retried
    _never_the_last_seed(seen)
    assert (
        not seen["inside"]["breeder"]["slots"]
        and not seen["inside"]["breeder"]["running"]
    )
    start = desk.get(log["second"])
    assert start is not None and start.state == "done"
    rows = station.status(core.ops.telemetry, "lab")
    assert rows and rows[0]["state"] == "done"
    assert rows[0]["stock"]["Tomato Seeds"] >= 4
    assert (
        rows[0]["plan"]
        == station.build(
            _world().facts(), _world().HAVE, "cropsnh:tomato", tier=rules.MV
        ).id
    )


def test_a_confirmed_stop_mid_run_leaves_nothing_in_transit(core: Core) -> None:
    seen = _drive(core, stop_at=160.0)
    log = seen["log"]
    assert seen["phase"] == "stopped", seen
    assert core.ops is not None
    assert core.ops.desk.get(log["stop"]).state == "done"  # type: ignore[union-attr]
    assert (
        not seen["inside"]["breeder"]["slots"]
        and not seen["inside"]["breeder"]["running"]
    )
    for i in (1, 2, 3):
        g = seen["inside"][f"gen{i}"]
        assert not g["running"]
        # a copier keeps only its one parked seed (and its catalyst), in its first input
        seeds = [s for s in g["slots"] if s["label"].endswith(" Seeds")]
        assert all(s["slot"] == 6 and s["size"] == 1 for s in seeds), g
        assert not any(s["slot"] == 8 for s in g["slots"]), "no copy left in an output"
    _never_the_last_seed(seen)
    rows = station.status(core.ops.telemetry, "lab")
    assert rows and rows[0]["state"] == "stopped"
