"""WO-5.1 on a DB built from a real GregtecLink harness run: a talk-key question goes through the
voice client's real core connection (HTTP and WebSocket) to a real core whose fast lane (the fake
model, D153) answers from the harness DB; the answer is spoken and its card reaches a dashboard.

The talk key, microphone and speech engines are the Linux stand-ins (a fake hook and source, a
fixed transcript for STT, WO-5.2, and a recording speaker for TTS, WO-5.3). As the other harness
tests: only with GOS_REQUIRE_HARNESS_RUN=1 after the client harness; nothing is written outside
tmp_path.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
import websockets.sync.client

from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.api.app import MAX_MESSAGE, Core, create_app
from gregtec.api.security import Auth
from gregtec.api.server import bind
from gregtec.config import Config
from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.llm import keys
from gregtec.llm.fake import EchoClient
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec_voice import audio
from gregtec_voice.audio import AudioPipe, FakeSource
from gregtec_voice.client import CoreClient, Online, Process, Pushed, VoiceClient
from gregtec_voice.hooks import FixedTranscriber
from gregtec_voice.ptt import FakeHook, FakeKeyState, KeyFilter, Watchdog
from gregtec_voice.settings import VoiceSettings

REPO = Path(__file__).resolve().parents[2]
ADMIN = "admin-" + "h" * 40
STEEL = "gregtech:gt.metaitem.01@11305"  # q01 of tests/evals/fast_lane_questions.tsv
F13 = 0x7C


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("voice-harness")
    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    return access.db_path(home)


class Speaker:
    name = "recording"

    def __init__(self) -> None:
        self.said: list[str] = []

    def speak(
        self, text: str, on_first_audio: Callable[[], None], on_done: Callable[[], None]
    ) -> None:
        self.said.append(text)
        on_first_audio()
        on_done()

    def stop(self) -> None:
        pass


@pytest.fixture
def core(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Core]:
    """The real core over the harness DB, with the fake model, on a free loopback port."""
    monkeypatch.setattr(keys, "has_key", lambda name: False)
    sock = bind(0)
    store = Store(tmp_path / "core.sqlite")
    deep = HandoffRecorder()
    reg = build_registry(
        db, Glossary.find(None), deep=deep
    )  # vault-template's glossary
    c = Core(
        cfg=Config(gos_home=tmp_path, core_port=sock.getsockname()[1]),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        asker=Asker(reg, deep, FastLane(EchoClient(), reg)),
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(c),
            log_config=None,
            access_log=False,
            ws="websockets-sansio",
            ws_max_size=MAX_MESSAGE,
            lifespan="on",
            timeout_graceful_shutdown=1,
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
        yield c
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        store.close()


def test_a_spoken_question_is_answered_from_the_harness_db(core: Core) -> None:
    port = core.cfg.core_port
    speaker = Speaker()
    voice: VoiceClient
    client = CoreClient(
        port,
        core.auth.issue_client_token("voice"),
        on_push=lambda message: voice.post(Pushed(message)),
        on_online=lambda online: voice.post(Online(online, time.monotonic())),
    )
    voice = VoiceClient(
        VoiceSettings(),
        client,
        transcriber=FixedTranscriber("How do I make steel?"),
        speaker=speaker,
    )
    source = FakeSource()
    key_filter = KeyFilter(voice.watched, voice.post)
    hook, state = FakeHook(key_filter), FakeKeyState()
    process = Process(
        voice,
        AudioPipe(source, voice.recorder),
        hook,
        watchdog=Watchdog(hook, state, key_filter),
        core=client,
    )
    stop = threading.Event()
    runner = threading.Thread(target=process.run, args=(stop,))
    pushed: list[dict[str, Any]] = []
    with websockets.sync.client.connect(
        f"ws://127.0.0.1:{port}/ws",
        additional_headers={"Authorization": f"Bearer {ADMIN}"},
        proxy=None,
    ) as dashboard:
        runner.start()
        try:
            deadline = time.monotonic() + 10
            while not (hook.alive() and source._on_block is not None and client.online):
                assert time.monotonic() < deadline
                time.sleep(0.02)
            pcm = audio.tone(0.02)
            state.down.add(F13)
            hook.press(F13)
            for _ in range(40):  # 0.4 s of "speech" while the key is held
                source.push(pcm)
                time.sleep(0.01)
            state.down.discard(F13)
            hook.release(F13)
            deadline = time.monotonic() + 30
            while not speaker.said:
                assert time.monotonic() < deadline, "nothing was said"
                source.push(pcm)  # the post-roll's blocks
                time.sleep(0.01)
            deadline = time.monotonic() + 10
            while not any(p["type"] == "ask.reply" for p in pushed):
                assert time.monotonic() < deadline, "no ask.reply push"
                pushed.append(json.loads(dashboard.recv(timeout=10)))
        finally:
            stop.set()
            runner.join(15)
    reply = next(p["data"] for p in pushed if p["type"] == "ask.reply")
    assert reply["by"] == "voice" and reply["request"] == "How do I make steel?"
    assert reply["lane"] == "fast" and reply["checked"], reply["problems"]
    assert reply["target"]["key"] == STEEL
    assert reply["card"]["tool"] == "recipe_find" and reply["refs"]
    # the spoken answer names the resolved item first (plan 4.4) and is the checked text
    assert speaker.said == [reply["text"]]
    assert (
        speaker.said[0].lower().startswith(reply["target"]["name"].lower().split()[0])
    )
    states = [p["data"]["state"] for p in pushed if p["type"] == "voice.state"]
    assert {"listening", "thinking"} <= set(states)
    assert not runner.is_alive()
