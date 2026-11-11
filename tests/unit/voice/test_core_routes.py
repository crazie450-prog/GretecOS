"""WO-5.1, D179: the core's routes for the voice client: ``POST /api/prewarm`` (and
``Asker.prewarm``), ``POST /api/ui``, ``POST /api/voice/state`` and the ``ask.reply`` push, with
their Host/auth rules (WO-2.1). Starlette's TestClient, no network."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from gregtec.agents import ask
from gregtec.agents.ask import PREWARM_FRESH_S, Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.tools.catalog import default_registry
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

ADMIN = "admin-" + "w" * 40
BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/ws"


class Lane:
    """A fast lane whose pre-warm is counted (the real one is FastLane.prewarm, D152)."""

    def __init__(self) -> None:
        self.prewarms = 0
        self.fail = False

    def prewarm(self, task: str = "prewarm") -> None:
        self.prewarms += 1
        if self.fail:
            request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.APIConnectionError(request=request)


class Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


@pytest.fixture
def core(tmp_path: Path) -> Iterator[Core]:
    store = Store(tmp_path / "data" / "gos.sqlite")
    deep = HandoffRecorder()
    c = Core(
        cfg=Config(gos_home=tmp_path),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        asker=Asker(default_registry(tmp_path, deep=deep), deep, None),
    )
    yield c
    store.close()


@pytest.fixture
def client(core: Core) -> Iterator[TestClient]:
    with TestClient(create_app(core), base_url=BASE) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def messages(ws: WebSocketTestSession, n: int) -> list[dict[str, Any]]:
    return [json.loads(ws.receive_text()) for _ in range(n)]


def until(ws: WebSocketTestSession, kind: str) -> dict[str, Any]:
    """The next message of ``kind`` (health pushes may come first)."""
    for _ in range(20):
        message = json.loads(ws.receive_text())
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind} message")


# --- Asker.prewarm --------------------------------------------------------------------------


def test_prewarm_skips_a_cache_that_is_still_warm(tmp_path: Path) -> None:
    clock, lane = Clock(), Lane()
    deep = HandoffRecorder()
    asker = Asker(default_registry(tmp_path, deep=deep), deep, lane, clock=clock)  # type: ignore[arg-type]
    assert asker.prewarm() == ask.WARMED and lane.prewarms == 1
    clock.now += PREWARM_FRESH_S - 1  # the 5-minute cache is still warm: no call
    assert asker.prewarm() == ask.WARM and lane.prewarms == 1
    clock.now += 2
    assert asker.prewarm() == ask.WARMED and lane.prewarms == 2
    clock.now += PREWARM_FRESH_S
    lane.fail = True
    assert asker.prewarm() == ask.FAILED
    lane.fail = False
    assert asker.prewarm() == ask.WARMED  # a failed one doesn't count as warm


def test_prewarm_without_a_lane_and_while_one_runs(tmp_path: Path) -> None:
    deep = HandoffRecorder()
    asker = Asker(default_registry(tmp_path, deep=deep), deep, None)
    assert asker.prewarm() == ask.NO_LANE  # no key stored
    lane = Lane()
    busy = Asker(default_registry(tmp_path, deep=deep), deep, lane)  # type: ignore[arg-type]
    busy._warming = True
    assert busy.prewarm() == ask.BUSY and lane.prewarms == 0


# --- the routes -----------------------------------------------------------------------------


def test_prewarm_route(client: TestClient, core: Core) -> None:
    voice = bearer(core.auth.issue_client_token("voice"))
    assert client.post("/api/prewarm").status_code == 401
    got = client.post("/api/prewarm", headers=voice)
    assert got.status_code == 200 and got.json() == {"status": "offline"}
    assert (
        client.post(
            "/api/prewarm", headers={**voice, "Host": "evil.example"}
        ).status_code
        == 403
    )
    core.asker = None
    assert client.post("/api/prewarm", headers=voice).status_code == 503


def test_ui_actions_reach_the_dashboards_only(client: TestClient, core: Core) -> None:
    voice = bearer(core.auth.issue_client_token("voice"))
    discord = bearer(core.auth.issue_client_token("discord"))
    with (
        client.websocket_connect(WS, headers=bearer(ADMIN)) as dashboard,
        client.websocket_connect(WS, headers=discord) as other,
    ):
        messages(dashboard, 3)  # hello, health, pair.pending
        messages(other, 2)  # hello, health
        got = client.post("/api/ui", json={"action": "next_card"}, headers=voice)
        assert got.status_code == 200 and got.json() == {"action": "next_card"}
        assert until(dashboard, "ui") == {
            "type": "ui",
            "action": "next_card",
            "by": "voice",
        }
        other.send_text(json.dumps({"type": "ping"}))
        assert until(other, "pong") == {"type": "pong"}  # the ui push never came first
    for bad in ({"action": "open_bom"}, {"action": 3}, {}):
        assert client.post("/api/ui", json=bad, headers=voice).status_code == 400
    assert client.post("/api/ui", json={"action": "next_card"}).status_code == 401


def test_only_the_voice_client_reports_the_voice_state(
    client: TestClient, core: Core
) -> None:
    voice = bearer(core.auth.issue_client_token("voice"))
    discord = bearer(core.auth.issue_client_token("discord"))
    body = {"state": "listening", "trigger": "key"}
    assert (
        client.post("/api/voice/state", json=body, headers=discord).status_code == 403
    )
    got = client.post("/api/voice/state", json=body, headers=voice)
    assert got.status_code == 200 and got.json()["state"] == "listening"
    assert (
        client.post("/api/voice/state", json=body, headers=bearer(ADMIN)).status_code
        == 200
    )
    for bad in (
        {"state": "dreaming"},
        {"state": "idle", "trigger": "mouse"},
        {"trigger": "key"},
    ):
        assert (
            client.post("/api/voice/state", json=bad, headers=voice).status_code == 400
        )


def test_the_voice_state_is_pushed_kept_and_off_when_the_client_goes(
    client: TestClient, core: Core
) -> None:
    token = core.auth.issue_client_token("voice")
    with client.websocket_connect(WS, headers=bearer(ADMIN)) as dashboard:
        messages(dashboard, 3)
        with client.websocket_connect(WS, headers=bearer(token)) as voice_ws:
            messages(voice_ws, 2)
            client.post(
                "/api/voice/state",
                json={"state": "speaking", "trigger": None},
                headers=bearer(token),
            )
            pushed = until(dashboard, "voice.state")["data"]
            assert (pushed["state"], pushed["trigger"]) == ("speaking", None)
            # a dashboard that connects now gets the state at once
            with client.websocket_connect(WS, headers=bearer(ADMIN)) as late:
                first = messages(late, 4)
                assert first[3]["type"] == "voice.state"
                assert first[3]["data"]["state"] == "speaking"
        # the voice client's socket closed: the voice strip shows it off
        assert until(dashboard, "voice.state")["data"]["state"] == "off"
    assert core.voice is not None and core.voice["state"] == "off"


def test_every_answer_is_pushed_to_the_dashboards(
    client: TestClient, core: Core
) -> None:
    token = core.auth.issue_client_token("voice")
    with (
        client.websocket_connect(WS, headers=bearer(ADMIN)) as dashboard,
        client.websocket_connect(WS, headers=bearer(token)) as voice_ws,
    ):
        messages(dashboard, 3)
        messages(voice_ws, 2)
        before = time.time()
        reply = client.post(
            "/api/ask", json={"text": "what's my tier"}, headers=bearer(token)
        )
        assert reply.status_code == 200
        pushed = until(dashboard, "ask.reply")["data"]
        assert pushed["by"] == "voice" and pushed["request"] == "what's my tier"
        assert pushed["text"] == reply.json()["text"]
        assert pushed["lane"] == "instant" and pushed["intent"] == "tier"
        assert pushed["card"] is not None and pushed["card"] == reply.json()["card"]
        assert pushed["pending"] is None  # only confirm and cancel say whether one was
        # when the question came, so a dashboard keeps the newest question's card
        assert before - 1 <= pushed["asked_ts"] <= time.time() + 1
        assert "asked_ts" not in reply.json()
        voice_ws.send_text(json.dumps({"type": "ping"}))
        assert until(voice_ws, "pong") == {
            "type": "pong"
        }  # clients don't get answers pushed


def test_a_confirm_or_cancel_with_nothing_waiting_isnt_pushed(
    client: TestClient, core: Core
) -> None:
    """The cancel hotkey, pressed to silence GOS, changed nothing: no card-less "cancel" answer
    replaces the card on the dashboards (D179)."""
    voice = bearer(core.auth.issue_client_token("voice"))
    with client.websocket_connect(WS, headers=bearer(ADMIN)) as dashboard:
        messages(dashboard, 3)
        for command in ("cancel", "confirm"):
            reply = client.post("/api/ask", json={"text": command}, headers=voice)
            assert reply.status_code == 200
            assert reply.json()["text"] == ask.NOTHING_PENDING
            # the voice client still learns that nothing was waiting: cancel stays quiet
            assert reply.json()["pending"] is False
        client.post("/api/ask", json={"text": "what's my tier"}, headers=voice)
        pushed = until(dashboard, "ask.reply")["data"]
        assert pushed["request"] == "what's my tier"  # the first answer pushed
    # nor kept for Chat (WO-2.6's board, D160)
    answers = client.get("/api/answers", headers=bearer(ADMIN)).json()
    assert [a["request"] for a in answers] == ["what's my tier"]
