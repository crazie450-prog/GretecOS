"""WO-5.6 (D207): the TTS voice follows the persona, and the plan 8 acceptance "switch by voice
in <= 1 s", timed from the end of speech to the persona's reply with a fake STT transcript
through the real router and persona packs (the real microphone check is a PC step)."""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.personas import Personas
from gregtec.agents.tools.catalog import default_registry
from gregtec_voice import tts
from gregtec_voice.audio import Block
from gregtec_voice.client import IDLE, VoiceClient
from gregtec_voice.playback import FakeOutput
from gregtec_voice.ptt import KeyEvent
from gregtec_voice.settings import VoiceSettings
from gregtec_voice.tts import KOKORO, Choice

F13 = 0x7C
BLOCK = bytes(640)


class VoicedEngine:
    """A Kokoro-like fake: knows a few voices, says which one spoke each chunk."""

    name = KOKORO
    rate = 1000

    def __init__(self, voice: str = "am_michael", log: list[str] | None = None) -> None:
        self.voice = voice
        self.label = f"fake kokoro {voice}"
        self.log = log if log is not None else []
        self.made: list[str] = []

    def with_voice(self, voice: str) -> VoicedEngine:
        if voice not in ("am_michael", "am_fenrir", "bf_emma"):
            raise tts.TtsError(f"no voice {voice!r}")
        self.made.append(voice)
        return VoicedEngine(voice, self.log)

    def stream(self, text: str) -> Iterator[bytes]:
        self.log.append(self.voice)
        yield bytes(20)


def speaker_of(engine: VoicedEngine) -> tts.Speaker:
    engines = tts.Engines([Choice(KOKORO)], VoiceSettings(), lambda c, v, n: engine)
    return tts.Speaker(engines, FakeOutput(), lambda text: text)


def say(speaker: tts.Speaker, text: str) -> None:
    done = threading.Event()
    speaker.speak(text, lambda: None, done.set)
    assert done.wait(10)


def test_the_speaker_uses_the_personas_voice_for_its_engine() -> None:
    engine = VoicedEngine()
    speaker = speaker_of(engine)
    say(speaker, "Hello.")
    speaker.use_voice("kokoro:bf_emma, eleven:21m00Tcm4TlvDq8ikWAM")
    say(speaker, "Noted.")
    say(speaker, "Again.")
    speaker.use_voice("eleven:abc, piper:joe")  # no kokoro entry: the configured voice
    say(speaker, "Back.")
    assert engine.log == ["am_michael", "bf_emma", "bf_emma", "am_michael"]
    assert engine.made == ["bf_emma"]  # made once, then reused
    speaker.use_voice("kokoro:am_fenrir")
    speaker.use_voice("kokoro:bf_emma")
    say(speaker, "Once more.")
    assert len(speaker._voiced) == 1  # one per engine, however many switches


def test_a_voice_the_engine_lacks_falls_back_to_its_own() -> None:
    engine = VoicedEngine()
    speaker = speaker_of(engine)
    speaker.use_voice("kokoro:zz_nobody")
    say(speaker, "Hello.")
    speaker.use_voice("nonsense, :x, kokoro:")
    say(speaker, "Hello.")
    assert engine.log == ["am_michael", "am_michael"]


class FakeKokoro:
    voices = ("am_michael", "bf_emma")

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def create(self, text: str, voice: str, speed: float, lang: str) -> tuple[Any, int]:
        self.calls.append((voice, lang))
        np = pytest.importorskip("numpy")
        return np.zeros(10, dtype=np.float32), 24000


def test_kokoro_and_elevenlabs_change_voice_without_reloading() -> None:
    pytest.importorskip("numpy")  # the tts group's (Kokoro's samples are numpy arrays)
    kokoro = FakeKokoro()
    engine = tts.KokoroEngine(kokoro, "am_michael", "kokoro cpu am_michael")
    emma = engine.with_voice("bf_emma")
    assert emma.kokoro is kokoro and emma.label == "kokoro cpu bf_emma"
    assert engine.with_voice("am_michael") is engine
    list(emma.stream("Noted."))
    assert kokoro.calls == [
        ("bf_emma", "en-gb")
    ]  # a British voice speaks British English
    with pytest.raises(tts.TtsError):
        engine.with_voice("af_nobody")
    eleven = tts.ElevenLabsEngine("secret-key", "", opener=lambda r, timeout: None)  # type: ignore[arg-type,return-value]
    other = eleven.with_voice("21m00Tcm4TlvDq8ikWAM")
    assert other.voice == "21m00Tcm4TlvDq8ikWAM" and other.model == eleven.model
    assert eleven.with_voice(eleven.voice) is eleven


# --- the acceptance: switch by voice in <= 1 s -------------------------------------------------


class Transcript:
    """A fake STT: whatever was said, the transcript is ``text``."""

    name = "fake"

    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, segment: Any) -> str:  # noqa: ANN401 - a Segment
        return self.text


class InProcessCore:
    """The core as the voice client sees it, answering through a real Asker (router, persona
    packs) with no model: a fast lane that would fail the test if it were called."""

    def __init__(self, asker: Asker) -> None:
        self.asker = asker

    def ask(self, text: str) -> dict[str, Any]:
        return self.asker.ask(text, via="voice").as_dict()

    def prewarm(self) -> None:
        pass

    def ui(self, action: str) -> None:
        pass

    def state(self, state: str, trigger: str | None) -> None:
        pass


class RecordingSpeaker:
    name = "fake"

    def __init__(self) -> None:
        self.said: list[tuple[str, str, float]] = []  # text, voice spec, when
        self.voice = ""

    def use_voice(self, spec: str) -> None:
        self.voice = spec

    def speak(
        self, text: str, on_first_audio: Callable[[], None], on_done: Callable[[], None]
    ) -> None:
        self.said.append((text, self.voice, time.perf_counter()))
        on_first_audio()
        on_done()

    def stop(self) -> None:
        pass


class NoModel:
    def answer(self, *args: object, **kwargs: object) -> Any:  # noqa: ANN401
        raise AssertionError("the persona command called the model")

    def prewarm(self, *args: object, **kwargs: object) -> Any:  # noqa: ANN401
        raise AssertionError("the persona command called the model")


@pytest.mark.parametrize(
    "said",
    ["persona ship AI", "Persona, Ship AI.", "Persona: ship AI!"],  # STT punctuates
)
def test_switch_by_voice_within_a_second(tmp_path: Path, said: str) -> None:
    deep = HandoffRecorder()
    found = Personas(None, state=tmp_path / "persona.json")
    asker = Asker(
        default_registry(tmp_path, deep=deep), deep, NoModel(), personas=found
    )  # type: ignore[arg-type]
    speaker = RecordingSpeaker()
    now = [1000.0]

    def clock() -> float:
        return now[0]

    voice = VoiceClient(
        VoiceSettings(),
        InProcessCore(asker),  # type: ignore[arg-type]
        transcriber=Transcript(said),  # type: ignore[arg-type]
        speaker=speaker,  # type: ignore[arg-type]
        clock=clock,
    )
    voice._set_state(IDLE)

    def audio(seconds: float) -> None:
        for _ in range(round(seconds / 0.02)):
            now[0] += 0.02
            voice.recorder.feed(Block(BLOCK, now[0]))

    audio(0.3)
    voice.handle(KeyEvent(F13, True, now[0]))
    audio(1.0)
    voice.handle(KeyEvent(F13, False, now[0]))
    released = time.perf_counter()
    audio(0.2)  # the post-roll closes the segment
    deadline = time.monotonic() + 5
    while not speaker.said:
        assert time.monotonic() < deadline, "no reply"
        with contextlib.suppress(queue.Empty):
            voice.handle(voice.events.get(timeout=0.05))
    text, spec, at = speaker.said[0]
    took = at - released
    assert took <= 1.0, f"the switch took {took:.3f} s"
    assert text.startswith("Ship AI here.")
    assert spec.startswith("kokoro:bf_emma")  # spoken in the new persona's voice (D207)
    assert found.active is not None and found.active.id == "ship-ai"
