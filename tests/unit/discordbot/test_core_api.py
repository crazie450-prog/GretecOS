"""WO-9.1: the bot's HTTP calls (``HttpCore``) against a real core (FastAPI + uvicorn on a free
loopback port) with the ``discord`` client token: design jobs, plan files, alerts, the journal,
errors as :class:`CoreError`, and never through a proxy."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.tools.catalog import default_registry
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.api.server import bind
from gregtec.config import Config
from gregtec.discordbot.core_api import CoreError, HttpCore
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.remote.jobs import JobQueue
from gregtec.store import Store

ADMIN = "admin-" + "b" * 40


def designed(kind: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    return {"ids": ["pl-00000001"], "problems": [], "routes": []}


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Core, int]]:
    monkeypatch.setattr(keys, "has_key", lambda name: False)
    # A proxy nobody listens on: a call that used it would fail (the token stays local).
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    sock = bind(0)
    port = sock.getsockname()[1]
    store = Store(tmp_path / "data" / "gos.sqlite")
    deep = HandoffRecorder()
    core = Core(
        cfg=Config(gos_home=tmp_path, core_port=port),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        asker=Asker(default_registry(tmp_path, deep=deep), deep, None),
    )
    core.jobs = JobQueue(store, designed)
    lines: list[str] = []
    core.journal = lines.extend
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(core), log_config=None, access_log=False, lifespan="on"
        )
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline and thread.is_alive(), "core didn't start"
        time.sleep(0.02)
    try:
        yield core, port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        store.close()


def test_the_bot_s_calls_work_against_a_real_core(live: tuple[Core, int]) -> None:
    core, port = live
    api = HttpCore(port, core.auth.issue_client_token("discord"))
    out = api.submit_job(
        "plan", {"target": "steel ingot", "rate": "2/s"}, "1300000000000000001"
    )
    job = out["job"]
    assert out["created"] is True and job["state"] == "queued"
    assert api.undelivered() == []
    assert core.jobs is not None and core.jobs.run_one() is not None
    (ready,) = api.undelivered()
    assert ready["id"] == job["id"] and ready["result"]["ids"] == ["pl-00000001"]
    assert api.delivered(job["id"])["delivered"] is not None
    assert api.undelivered() == []

    plans = core.cfg.gos_home / "data" / "plans" / "pl-00000001"
    plans.mkdir(parents=True)
    (plans / "flow.png").write_bytes(b"\x89PNG fake")
    (plans / "bom.tsv").write_text("item\tcount\n", encoding="utf-8")
    assert api.export("pl-00000001", "png") == b"\x89PNG fake"
    assert api.export("pl-00000001", "bom") == b"item\tcount\n"
    with pytest.raises(CoreError) as missing:
        api.export("pl-00000002", "png")
    assert missing.value.status == 404

    assert api.alerts() == {"mode": "on", "until": None}
    assert api.set_alerts("quiet", 2.0)["mode"] == "quiet"
    api.journal("status", "status asked")
    assert api.ai()["ai"]["online"] is False  # no key in this test


def test_errors_carry_the_core_s_status_and_reason(live: tuple[Core, int]) -> None:
    core, port = live
    api = HttpCore(port, core.auth.issue_client_token("discord"))
    with pytest.raises(CoreError) as bad:
        api.submit_job("plan", {"target": ""}, "k")
    assert bad.value.status == 400 and bad.value.detail == "name a target item or fluid"
    with pytest.raises(CoreError) as no_bridge:
        api.actions()
    assert no_bridge.value.status == 503 and not no_bridge.value.offline
    with pytest.raises(CoreError) as refused:
        HttpCore(port, "not-a-token").alerts()
    assert refused.value.status == 401
    with pytest.raises(CoreError) as down:
        HttpCore(1, "x").ledger()  # nobody on port 1
    assert down.value.offline
