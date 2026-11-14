"""WO-6.5 x WO-7.5 (D267, D281; PM 2026-11-14): one stalled machine is ONE card on the dashboard
and ONE spoken line, not one from the advisor's notices and another from the alert engine.

The core runs both (its OC ops with the alert engine, and the notices with D71's defaults) on a
virtual clock; OC reports the Lab's EBF with a job and not running for 15 minutes, past the
notices' cooldown. Everything the core pushes on ``/ws`` goes to the voice client, and the
dashboard's ``/api/state`` gives the cards.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gregtec.advisor.notices import Notices
from gregtec.api import dashboard
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.bridge import ops as bridge_ops
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec_voice.client import Pushed, VoiceClient
from gregtec_voice.settings import VoiceSettings

REPO = Path(__file__).resolve().parents[3]
ADMIN = "admin-" + "s" * 40
BASE = 2_000_000_000.0


def _scenarios() -> ModuleType:
    name = "advisor_scenarios"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO / "tests" / "evals" / "advisor_scenarios.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class VClock:
    def __init__(self) -> None:
        self.now = BASE

    def __call__(self) -> float:
        return self.now


class Sock:
    trusted = True

    def __init__(self) -> None:
        self.got: list[dict[str, Any]] = []

    def put(self, text: str) -> None:
        self.got.append(json.loads(text))


def test_one_stalled_machine_is_one_card_and_one_spoken_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    core: Any,  # noqa: ANN401 - the voice conftest's FakeCore
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
) -> None:
    S = _scenarios()
    monkeypatch.setattr(keys, "has_key", lambda name: False)
    sc = S.build(tmp_path, "early", now=BASE)  # no problem of its own
    vclock = VClock()
    monkeypatch.setattr(time, "time", vclock)
    cfg = Config(gos_home=tmp_path)
    store = Store(sc.db)
    ops = bridge_ops.build(cfg, store)
    assert ops.live is not None and ops.displays is not None and ops.alerts is not None
    for part in (ops.telemetry, ops.desk, ops.live, ops.displays, ops.alerts):
        part.clock = vclock
    notices = Notices(sc.db, clock=vclock)  # D71's defaults: machine_stalled among them
    gos = Core(
        cfg=cfg,
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        oc=ops.bridge,
        ops=ops,
        notices=notices,
    )
    ops.desk.on_change = ops.changed(None)
    board = dashboard.board_of(gos)
    board.clients = lambda cfg: ["lab"]
    board.running = ["lab"]
    board.refresh_data = lambda: None  # type: ignore[method-assign]
    sock = Sock()
    gos.sockets.add(sock)  # type: ignore[arg-type]
    try:
        with TestClient(create_app(gos), base_url="http://127.0.0.1:8765") as client:
            for seq, t in enumerate(range(0, 900, 5), 1):
                vclock.now = BASE + t
                doc = S.oc_message(seq, active=False, eu_in=9, eu_out=9)
                out = ops.bridge.receive(
                    json.dumps(doc).encode(),
                    transport="http",
                    source="fixture",
                    binding="lab",
                    now=vclock.now,
                )
                assert out.ok, out
                ops.tick()
                if t % 10 == 0:
                    notices.tick()
            client.get("/api/state", headers={"Authorization": f"Bearer {ADMIN}"})
            for _ in range(50):  # the pushes are queued on the core's loop
                if any(m["type"] == "alert" for m in sock.got):
                    break
                time.sleep(0.02)
            state = client.get(
                "/api/state", headers={"Authorization": f"Bearer {ADMIN}"}
            ).json()
    finally:
        notices.close()
        store.close()

    ebf = f"tel:lab/{S.EBF_ADDR}"
    stall_cards = [
        a
        for a in state["alerts"]
        if "stall" in a["title"].lower() or ebf in json.dumps(a)
    ]
    assert [a["title"] for a in stall_cards] == [
        "Machine stalled: Electric Blast Furnace"
    ]
    assert [m for m in sock.got if m["type"] == "notice"] == []

    voice = VoiceClient(
        VoiceSettings(), core, transcriber=transcriber, speaker=speaker, clock=clock
    )
    for message in sock.got:
        voice.handle(Pushed(message))
        voice.drain()
    assert [line for line in speaker.said if "stall" in line] == [
        "Lab: Electric Blast Furnace has stalled."
    ]
