"""WO-7.4 acceptance on a fake clock: the real agent's displays fed by the real core, over both
transports (plan 8 WO-7.4 "Readable in game; updates <= 10 s"; D277-D279).

tests/oc/display_pipe.lua runs oc/gos-agent.lua (with oc/display.lua) in fakeoc's world and sends
every exchange here over a pipe: over M2 to the core's own ``POST /oc/telemetry`` (the FastAPI app
in-process, an ``oc-lab`` token), over M1 through the core's own :class:`Mailbox` on a real temp
folder. On the agent's virtual clock the EBF stalls at 33.3 s and runs again at 61.7 s, and at
the first message after 45 s the dashboard confirms a pause of it (D59: by id). Checked: each
change is on the board (and over M2 on the HUD) within 10 s; the board is 50 x 16 and nothing
is drawn past its edge; the confirmation and the result are said once each on the chat box; the
pause ran; the HUD keeps its widget budget. Needs Lua 5.3 like the other agent tests.
"""

from __future__ import annotations

import json
import os
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
from gregtec.bridge.mailbox import Mailbox, Place
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

from .test_oc_agent import REPO, _lua53, _need

DRIVE = "0f000000-0000-4000-8000-000000000003"
ADMIN = "admin-" + "c" * 40
PAUSE_AFTER = 45.0


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


@pytest.fixture
def core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Core]:
    # The agent's virtual minutes pass in real milliseconds: lift the per-token rate for this.
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


def _mailbox(tmp_path: Path, core: Core) -> tuple[Mailbox, Path]:
    instance = tmp_path / "GTNH-Lab"
    folder = instance.joinpath(
        ".minecraft",
        "saves",
        "lab-ops",
        "opencomputers",
        DRIVE,
        "home",
        "gos",
        "mailbox",
    )
    folder.mkdir(parents=True)
    assert core.ops is not None
    place = Place("lab", instance, "lab-ops", DRIVE, "home/gos/mailbox")
    return Mailbox(place, core.ops.bridge, sleep=lambda s: None), folder


def _check_reply(text: str) -> None:
    assert len(text.encode("ascii")) <= REPLY_MAX
    doc = json.loads(text)
    assert not list(validators()["reply"].iter_errors(doc)), doc


def _drive(transport: str, core: Core, tmp_path: Path) -> dict[str, Any]:
    lua = _need(_lua53(), "Lua 5.3")
    assert core.ops is not None
    desk = core.ops.desk
    token = core.auth.issue_client_token("oc-lab")
    box, folder = _mailbox(tmp_path, core) if transport == "mailbox" else (None, None)
    paused: dict[str, Any] = {}
    proc = subprocess.Popen(
        [lua, "tests/oc/display_pipe.lua", transport, token],
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
                    req["paused_action"] = paused
                    return req
                if req["t"] >= PAUSE_AFTER and not paused:
                    # The approval card's button, as the dashboard presses it (by id, D59).
                    doc = json.loads(req["body"])
                    ebf = next(
                        m["addr"]
                        for m in doc["machines"]
                        if m["name"] == "multimachine.blastfurnace"
                    )
                    out = desk.propose("machine_pause", {"addr": ebf}, target="lab")
                    assert out.action is not None, out.text
                    confirmed = desk.confirm("dashboard", out.action.id)
                    assert confirmed.ok, confirmed.text
                    paused.update(id=out.action.id, at=req["t"])
                if transport == "http":
                    r = client.post(
                        "/oc/telemetry",
                        content=req["body"].encode("ascii"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {token}",
                        },
                    )
                    assert r.status_code == 200
                    reply: str | None = r.text
                else:
                    assert box is not None and folder is not None
                    (folder / "outbox.tmp").write_bytes(req["body"].encode("ascii"))
                    os.replace(folder / "outbox.tmp", folder / "outbox.json")
                    box.poll()
                    inbox = folder / "inbox.json"
                    reply = inbox.read_text("ascii") if inbox.exists() else None
                    if reply is not None:
                        inbox.unlink()  # OC took it
                if reply is not None:
                    _check_reply(reply)
                proc.stdin.write(json.dumps({"body": reply}) + "\n")
                proc.stdin.flush()
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.parametrize("transport", ["http", "mailbox"])
def test_changes_reach_the_displays_within_10_s(
    transport: str, core: Core, tmp_path: Path
) -> None:
    seen = _drive(transport, core, tmp_path)
    assert (seen["width"], seen["height"]) == (50, 16)
    assert seen["overflow"] == 0
    assert all(len(r) == 50 for r in seen["rows"])
    stall, back = seen["stall_at"], seen["back_at"]
    assert 0 <= seen["board_stalled"] - stall <= 10, seen
    assert 0 <= seen["board_back"] - back <= 10, seen
    if transport == "http":
        assert 0 <= seen["hud_stalled"] - stall <= 10, seen
        assert seen["energy"] == 0.4  # 4 text widgets
    else:
        assert seen.get("hud_stalled") is None and seen["energy"] == 0  # M2 only
    # The confirmed pause: run once, and said once each (confirmed, then done) within 10 s.
    assert seen["paused"] is True
    texts = [s["text"] for s in seen["said"]]
    assert [t.split(":")[1].strip() for t in texts] == ["Confirmed", "Done"], texts
    assert all("Pause machine" in t for t in texts)
    asked = seen["paused_action"]["at"]
    assert seen["said"][0]["t"] - asked <= 10 and seen["said"][1]["t"] - asked <= 10
    assert core.ops is not None
    done = core.ops.desk.get(seen["paused_action"]["id"])
    assert done is not None and done.state == "done"
    assert any("GREGTEC OS" in r for r in seen["rows"])
