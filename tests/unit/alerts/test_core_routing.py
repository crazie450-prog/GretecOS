"""WO-9.2 in the core (D298-D300): the alert engine's sink and the notices feed the router; the
voice line goes only to the voice client's sockets (and the dashboards' pushes carry none); the
paused game comes from the watchers' feed; the outbox routes are the bot's and gos's only.
Starlette's TestClient, no network."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gregtec.alerts.engine import Alert, AlertEvent
from gregtec.alerts.router import AlertRouter
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth, Principal
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.presence import FakeIdle
from gregtec.store import Store
from gregtec.watchers.feed import GameFeed

ADMIN = "admin-" + "r" * 40
BASE = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


@dataclass(eq=False)
class Sock:
    who: Principal
    got: list[dict[str, Any]] = field(default_factory=list)

    @property
    def trusted(self) -> bool:
        return self.who.role in ("admin", "device")

    def put(self, text: str) -> None:
        self.got.append(json.loads(text))


@pytest.fixture
def idle() -> FakeIdle:
    return FakeIdle(time.time)


@pytest.fixture
def core(tmp_path: Path, idle: FakeIdle) -> Iterator[Core]:
    store = Store(tmp_path / "data" / "gos.sqlite")
    c = Core(
        cfg=Config(gos_home=tmp_path),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        router=AlertRouter(store, idle=idle),
    )
    yield c
    store.close()


@pytest.fixture
def client(core: Core) -> Iterator[TestClient]:
    with TestClient(create_app(core), base_url=BASE) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def stalled(at: float) -> AlertEvent:
    a = Alert(
        key="machine_stalled:lab/42",
        rule="machine_stalled",
        binding="lab",
        subject="lab/42",
        level="warn",
        title="Machine stalled: EBF",
        body="Lab: has a job and work allowed, but isn't running. tel:lab/42",
        say="Lab: EBF has stalled.",
        cite="tel:lab/42",
        fired_at=at,
    )
    return AlertEvent("fired", "lab", at, a, say=a.say)


def wait_for(cond: Any, s: float = 3.0) -> None:  # noqa: ANN401 - a callable
    end = time.monotonic() + s
    while time.monotonic() < end and not cond():
        time.sleep(0.02)


def test_the_voice_line_goes_to_the_voice_client_only(
    core: Core, client: TestClient
) -> None:
    voice = Sock(Principal("client", "voice"))
    dash = Sock(Principal("device", "phone", "d1"))
    core.sockets |= {voice, dash}  # type: ignore[arg-type]
    core.alert_changed(stalled(time.time()))
    assert core.router is not None
    core.router.tick()
    wait_for(lambda: any(m.get("data", {}).get("routed") for m in voice.got))
    spoken = [
        m["data"]["say"] for m in voice.got if m["type"] == "alert" and m["data"]["say"]
    ]
    assert spoken == ["Lab: EBF has stalled."]
    wait_for(lambda: any(m["type"] == "alert" for m in dash.got))
    assert all(m["data"]["say"] is None for m in dash.got if m["type"] == "alert")
    core.router.tick()
    time.sleep(0.1)
    spoken = [m for m in voice.got if m["type"] == "alert" and m["data"]["say"]]
    assert len(spoken) == 1


def test_away_it_becomes_a_dm_in_the_outbox(
    core: Core, client: TestClient, idle: FakeIdle
) -> None:
    idle.touch(time.time() - 3600)
    core.alert_changed(stalled(time.time()))
    assert core.router is not None
    core.router.tick()  # no bot has read the outbox: nothing is queued for Discord (M1)
    assert core.store.rows("SELECT id FROM alert_dm") == []
    assert core.router.view()["active"][0]["held"]["discord"] == "no Discord bot"
    admin = client.get("/api/alerts/outbox", headers=bearer(ADMIN))
    assert admin.json()["dms"] == []
    core.router.tick()  # an admin read is not a bot
    assert core.store.rows("SELECT id FROM alert_dm") == []
    bot = bearer(core.auth.issue_client_token("discord"))
    assert client.get("/api/alerts/outbox", headers=bot).json()["dms"] == []
    core.router.tick()  # the bot read it: now it is queued
    got = client.get("/api/alerts/outbox", headers=bot)
    assert got.status_code == 200, got.text
    (dm,) = got.json()["dms"]
    assert "Machine stalled: EBF" in dm["text"] and "tel:lab/42" in dm["text"]
    assert got.json()["alerts"] == {"mode": "on", "until": None}
    done = client.post(f"/api/alerts/outbox/{dm['id']}/delivered", headers=bot)
    assert done.status_code == 200
    again = client.post(f"/api/alerts/outbox/{dm['id']}/delivered", headers=bot)
    assert again.status_code == 409
    assert client.get("/api/alerts/outbox", headers=bot).json()["dms"] == []


def test_the_paused_game_comes_from_the_watchers_feed(
    core: Core, client: TestClient, idle: FakeIdle
) -> None:
    core.game = GameFeed()
    since = time.time() - 30
    core.game.receive(
        {"instance": "lab", "paused": {"paused": True, "since": since}, "events": []}
    )
    idle.touch(time.time() - 3600)
    assert core.router is not None
    core.router.bot_seen()
    core.router.tick()
    rows = core.store.rows("SELECT text FROM alert_dm")
    assert len(rows) == 1 and "Lab is paused" in rows[0]["text"]


def test_who_may_use_the_alert_routes(core: Core, client: TestClient) -> None:
    voice = bearer(core.auth.issue_client_token("voice"))
    bot = bearer(core.auth.issue_client_token("discord"))
    admin = bearer(ADMIN)
    assert client.get("/api/alerts/outbox").status_code == 401
    assert client.get("/api/alerts/outbox", headers=voice).status_code == 403
    assert client.get("/api/alerts/outbox", headers=admin).status_code == 200
    assert client.get("/api/alerts/routed", headers=bot).status_code == 403
    view = client.get("/api/alerts/routed", headers=admin).json()
    assert set(view) == {"context", "active"}
    assert view["context"]["presence"] in ("away", "present")  # the fixture's FakeIdle
    assert client.post("/api/alerts/test", headers=bot).status_code == 403
    assert client.post("/api/alerts/test", headers=admin).status_code == 200
    assert core.router is not None
    client.get("/api/alerts/outbox", headers=bot)
    core.router.tick()
    (dm,) = client.get("/api/alerts/outbox", headers=bot).json()["dms"]
    assert "Test alert" in dm["text"]


def test_without_a_router_the_engines_push_speaks_as_before(tmp_path: Path) -> None:
    store = Store(tmp_path / "gos.sqlite")
    c = Core(
        cfg=Config(gos_home=tmp_path),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )
    voice = Sock(Principal("client", "voice"))
    c.sockets.add(voice)  # type: ignore[arg-type]
    with TestClient(create_app(c), base_url=BASE):
        c.alert_changed(stalled(time.time()))
        wait_for(lambda: bool(voice.got))
    assert voice.got[0]["data"]["say"] == "Lab: EBF has stalled."
    store.close()


def test_gos_alerts_says_when_the_core_is_not_running(tmp_path: Path) -> None:
    import argparse
    import io

    from gregtec.alerts import cli

    toml = tmp_path / "gos.toml"
    home = tmp_path.as_posix()
    toml.write_text(f'[paths]\ngos_home = "{home}"\n', encoding="utf-8")
    out = io.StringIO()
    for command in ("routed", "test"):
        args = argparse.Namespace(alerts_command=command, config=toml)
        assert cli.run(args, out) == 1
    assert "isn't running" in out.getvalue()
