"""WO-5.1, D179: the voice client's connection to a real core (FastAPI + uvicorn on a free
loopback port): asks, the voice state and UI actions, WebSocket pushes, reconnecting after a
core restart, a refused token, and never using an HTTP proxy."""

from __future__ import annotations

import json
import logging
import queue
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import websockets.sync.client

from gregtec.agents.ask import NOTHING_PENDING
from gregtec.api.server import bind
from gregtec_voice.client import CoreClient, CoreError

Live = Any  # the conftest's LiveCore


def wait(cond: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


@contextmanager
def dashboard(live: Live) -> Iterator[Any]:
    """A trusted WebSocket (admin), as a dashboard's: it gets ui and voice.state pushes."""
    with websockets.sync.client.connect(
        f"ws://127.0.0.1:{live.port}/ws",
        additional_headers=live.admin(),
        proxy=None,
    ) as ws:
        yield ws


def next_of(ws: Any, kind: str) -> dict[str, Any]:  # noqa: ANN401 - a ClientConnection
    for _ in range(30):
        message = json.loads(ws.recv(timeout=5))
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind} push")


@pytest.fixture
def no_proxy_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy nobody listens on: anything that used it would fail (the token stays local)."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)


def test_the_client_talks_to_a_real_core(live_core: Live, no_proxy_used: None) -> None:
    pushes: queue.Queue[dict[str, Any]] = queue.Queue()
    online: list[bool] = []
    client = CoreClient(
        live_core.port,
        live_core.client_token(),
        on_push=pushes.put,
        on_online=online.append,
    )
    with dashboard(live_core) as ws:
        client.start()
        try:
            wait(lambda: online == [True])
            assert client.ask("confirm")["text"] == NOTHING_PENDING
            client.state("listening", "key")
            data = next_of(ws, "voice.state")["data"]
            assert (data["state"], data["trigger"]) == ("listening", "key")
            client.ui("next_card")
            assert next_of(ws, "ui") == {
                "type": "ui",
                "action": "next_card",
                "by": "voice",
            }
            client.prewarm()  # no key stored here: the core says so, the client logs it
            wait(lambda: not client._prewarming.locked())
            live_core.push(
                {"type": "deep.task", "data": {"id": "h-1", "status": "done"}}
            )
            got = pushes.get(timeout=5)
            while got["type"] != "deep.task":
                got = pushes.get(timeout=5)
            assert got["data"]["id"] == "h-1"
        finally:
            client.stop()
        assert next_of(ws, "voice.state")["data"]["state"] == "off"
    assert live_core.core.voice["state"] == "off"


def test_ask_errors_say_whether_the_core_answered(live_core: Live) -> None:
    client = CoreClient(
        live_core.port, live_core.client_token(), on_push=print, on_online=print
    )
    with pytest.raises(CoreError) as refused:
        client.ask("x" * 1001)  # the core refuses it (400): it is there
    assert not refused.value.offline
    sock = bind(0)
    nobody = sock.getsockname()[1]
    sock.close()
    gone = CoreClient(nobody, "t" * 43, on_push=print, on_online=print)
    with pytest.raises(CoreError) as offline:
        gone.ask("confirm")
    assert offline.value.offline


def test_the_client_reconnects_after_a_core_restart(live_core: Live) -> None:
    online: list[bool] = []
    client = CoreClient(
        live_core.port,
        live_core.client_token(),
        on_push=lambda m: None,
        on_online=online.append,
    )
    client.start()
    try:
        wait(lambda: online == [True])
        client.state("speaking", None)
        wait(lambda: (live_core.core.voice or {}).get("state") == "speaking")
        live_core.stop()  # the core went down (a crash, gos stop)
        wait(lambda: online == [True, False])
        with pytest.raises(CoreError):
            client.ask("confirm")
        live_core.core.voice = None  # a new core knows nothing yet
        live_core.start()  # the supervisor restarted it
        wait(lambda: online == [True, False, True], timeout=15)
        # after a reconnect the client tells the new core its state again
        wait(lambda: (live_core.core.voice or {}).get("state") == "speaking")
        assert client.ask("confirm")["text"] == NOTHING_PENDING
    finally:
        client.stop()


def test_a_refused_token_is_logged_once(
    live_core: Live, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    online: list[bool] = []
    client = CoreClient(
        live_core.port, "wrong" * 9, on_push=print, on_online=online.append
    )
    client.start()
    try:
        wait(lambda: online == [False])
        time.sleep(1.5)  # a retry after the first back-off
    finally:
        client.stop(final_state=False)
    refused = [
        r
        for r in caplog.records
        if "refused the voice client's token" in r.getMessage()
    ]
    assert len(refused) == 1 and refused[0].status == 403
