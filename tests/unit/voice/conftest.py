"""Fakes for the voice client's tests (WO-5.1): a manual clock, a core, a speaker, and a real
core served on a free loopback port for the tests that need real HTTP and WebSockets."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

import pytest
import uvicorn

from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.tools.catalog import default_registry
from gregtec.api.app import MAX_MESSAGE, Core, create_app
from gregtec.api.security import Auth
from gregtec.api.server import bind
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec_voice import stt
from gregtec_voice.client import CoreError, VoiceClient

ADMIN = "admin-" + "v" * 40


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


@dataclass
class FakeCore:
    """The core as the controller sees it: records everything, answers from ``answer``."""

    answer: Callable[[str], dict[str, Any]] = lambda text: {
        "text": f"You said {text}.",
        "lane": "fast",
        "intent": "general",
        "ttft_ms": 900,
        "checked": True,
    }
    fail: CoreError | None = None
    hold: threading.Event | None = None  # set: ask() waits for it (a slow answer)
    asked: list[str] = field(default_factory=list)
    prewarms: int = 0
    uis: list[str] = field(default_factory=list)
    states: list[tuple[str, str | None]] = field(default_factory=list)

    def ask(self, text: str) -> dict[str, Any]:
        self.asked.append(text)
        if self.hold is not None:
            self.hold.wait(5)
        if self.fail is not None:
            raise self.fail
        return self.answer(text)

    def prewarm(self) -> None:
        self.prewarms += 1

    def ui(self, action: str) -> None:
        self.uis.append(action)

    def state(self, state: str, trigger: str | None) -> None:
        self.states.append((state, trigger))


@dataclass
class FakeSpeaker:
    """Says things at once (``auto``), or holds them until :meth:`finish` or :meth:`stop`."""

    auto: bool = True
    name: str = "fake"
    said: list[str] = field(default_factory=list)
    stopped: int = 0
    _open: list[tuple[Callable[[], None], Callable[[], None]]] = field(
        default_factory=list
    )

    def speak(
        self,
        text: str,
        on_first_audio: Callable[[], None],
        on_done: Callable[[], None],
    ) -> None:
        self.said.append(text)
        if self.auto:
            on_first_audio()
            on_done()
        else:
            self._open.append((on_first_audio, on_done))

    def start_audio(self) -> None:
        self._open[-1][0]()

    def finish(self) -> None:
        _, done = self._open.pop()
        done()

    def stop(self) -> None:
        self.stopped += 1
        while self._open:
            self.finish()


@dataclass
class FakeTranscriber:
    text: str = "how do I make steel"
    name: str = "fake"
    seen: list[float] = field(default_factory=list)  # each segment's length in seconds

    def transcribe(self, segment: Any) -> str:  # noqa: ANN401 - a Segment
        self.seen.append(segment.seconds)
        return self.text


def pump(voice: VoiceClient, until: Callable[[], bool], timeout: float = 5.0) -> None:
    """Handle the controller's events (its worker threads post some) until ``until()``."""
    deadline = time.monotonic() + timeout
    while not until():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the voice client")
        try:
            voice.handle(voice.events.get(timeout=0.05))
        except queue.Empty:
            pass


@dataclass
class LiveCore:
    """A real core on a loopback port; :meth:`stop` and :meth:`start` restart it there."""

    def __init__(self, core: Core, sock: Any) -> None:  # noqa: ANN401 - a socket
        self.core = core
        self.sock = sock
        self.port: int = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.server, self.thread = serve(core, sock)

    def admin(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {ADMIN}"}

    def client_token(self, name: str = "voice") -> str:
        return self.core.auth.issue_client_token(name)

    def push(self, message: dict[str, Any]) -> None:
        """Broadcast from the core's loop, as its own threads do."""
        loop = self.core.loop
        assert loop is not None
        loop.call_soon_threadsafe(lambda: self.core.broadcast(message))

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
            self.thread.join(timeout=10)
            self.sock.close()
            self.server = None

    def start(self) -> None:
        """Serve the same core on the same port again (a supervisor restart)."""
        deadline = time.monotonic() + 10
        while True:
            try:
                self.sock = bind(self.port)
                break
            except OSError:  # the old socket's port isn't free yet
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        self.server, self.thread = serve(self.core, self.sock)


def serve(core: Core, sock: Any) -> tuple[uvicorn.Server, threading.Thread]:  # noqa: ANN401
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(core),
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
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the test core didn't start")
        time.sleep(0.02)
    return server, thread


@pytest.fixture
def live_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[LiveCore]:
    """A real core (FastAPI + uvicorn) on 127.0.0.1:<free port>, no API key, no DB yet."""
    monkeypatch.setattr(keys, "has_key", lambda name: False)
    sock = bind(0)
    store = Store(tmp_path / "data" / "gos.sqlite")
    deep = HandoffRecorder()
    core = Core(
        cfg=Config(gos_home=tmp_path, core_port=sock.getsockname()[1]),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        asker=Asker(default_registry(tmp_path, deep=deep), deep, None),
    )
    live = LiveCore(core, sock)
    try:
        yield live
    finally:
        live.stop()
        store.close()


# The test modules can't import this file (pytest's importlib mode): they get these as fixtures.


@pytest.fixture(autouse=True)
def no_speech_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """No unit test may fetch a speech model (WO-5.2): where the stt group is installed, an
    engine that got this far would download hundreds of MB. The slow tests that use real
    models live in tests/integration."""

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("a unit test tried to fetch a speech model")

    monkeypatch.setattr(stt, "whisper_files", refuse)
    monkeypatch.setattr(stt, "parakeet_files", refuse)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def core() -> FakeCore:
    return FakeCore()


@pytest.fixture
def speaker() -> FakeSpeaker:
    return FakeSpeaker()


@pytest.fixture
def transcriber() -> FakeTranscriber:
    return FakeTranscriber()


@pytest.fixture
def run_events() -> Callable[[VoiceClient, Callable[[], bool]], None]:
    return pump
