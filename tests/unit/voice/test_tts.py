"""WO-5.3: text-to-speech with fake engines and a fake device (D203-D205)."""

from __future__ import annotations

import io
import json
import sys
import threading
import time
import types
import urllib.error
import urllib.request
import wave
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gregtec.llm import keys
from gregtec_voice import hooks, playback, settings, tts
from gregtec_voice.audio import AudioError, Device
from gregtec_voice.client import IDLE, SPEAKING, VoiceClient
from gregtec_voice.playback import FakeOutput, WavOutput
from gregtec_voice.ptt import KeyEvent
from gregtec_voice.settings import VoiceSettings
from gregtec_voice.tts import CPU, ELEVENLABS, GPU, KOKORO, PIPER, Choice

REAL_MODEL_FILES = tts.model_files  # conftest refuses it in every test


class FakeEngine:
    """Each chunk is ``seconds`` of a constant sample (its index), after ``delay`` s."""

    def __init__(
        self,
        name: str = PIPER,
        rate: int = 1000,
        *,
        seconds: float = 0.1,
        delay: float = 0.0,
        fail: bool = False,
    ) -> None:
        self.name = name
        self.label = f"fake {name}"
        self.rate = rate
        self.seconds = seconds
        self.delay = delay
        self.fail = fail
        self.said: list[str] = []

    def stream(self, text: str) -> Iterator[bytes]:
        if self.fail:
            raise tts.TtsError("broken")
        time.sleep(self.delay)
        self.said.append(text)
        value = len(self.said).to_bytes(2, "little")
        yield value * round(self.seconds * self.rate)


def loader_of(*engines: FakeEngine | Exception) -> tts.Loader:
    queue = list(engines)

    def load(choice: Choice, voice: VoiceSettings, note: Callable[[str], None]) -> Any:  # noqa: ANN401
        got = queue.pop(0)
        if isinstance(got, Exception):
            raise got
        return got

    return load


def speaker_with(
    *engines: FakeEngine | Exception,
    output: playback.Output | None = None,
    choices: list[Choice] | None = None,
) -> tts.Speaker:
    picks = choices or [Choice(PIPER), Choice(KOKORO)][: len(engines)]
    found = tts.Engines(picks, VoiceSettings(), loader_of(*engines))
    return tts.Speaker(
        found, output or FakeOutput(), lambda text: tts.speech_filter.spoken(text)
    )


class Done:
    def __init__(self) -> None:
        self.first = 0
        self.done = threading.Event()
        self.count = 0

    def on_first(self) -> None:
        self.first += 1

    def on_done(self) -> None:
        self.count += 1
        self.done.set()

    def wait(self) -> None:
        assert self.done.wait(10)


def say(speaker: tts.Speaker, text: str) -> Done:
    done = Done()
    speaker.speak(text, done.on_first, done.on_done)
    done.wait()
    time.sleep(0.02)  # a second on_done would show here
    return done


# --- chunks -----------------------------------------------------------------------------------


def test_chunks_are_sentences_and_the_first_is_cut_at_a_clause() -> None:
    text = "The furnace runs on high voltage today, then it cools down. Next one."
    assert tts.chunks(text) == [
        "The furnace runs on high voltage today,",
        "then it cools down.",
        "Next one.",
    ]


def test_the_first_chunk_needs_six_words_and_leaves_three() -> None:
    assert tts.chunks("Steel, then iron and gold.") == ["Steel, then iron and gold."]
    assert tts.chunks("One two three four five six, seven eight.") == [
        "One two three four five six, seven eight."
    ]
    assert tts.chunks("") == []


# --- choosing engines -------------------------------------------------------------------------


def test_choices_fall_back_to_the_cpu_then_the_local_engines() -> None:
    assert tts.choices(KOKORO, CPU) == [Choice(KOKORO), Choice(PIPER)]
    assert tts.choices(KOKORO, GPU) == [
        Choice(KOKORO, GPU),
        Choice(KOKORO),
        Choice(PIPER),
    ]
    assert tts.choices(PIPER, CPU) == [Choice(PIPER), Choice(KOKORO)]
    assert tts.choices(ELEVENLABS, GPU) == [
        Choice(ELEVENLABS),
        Choice(PIPER),
        Choice(KOKORO),
    ]


def test_engines_skip_one_that_cannot_start() -> None:
    piper = FakeEngine(PIPER)
    engines = tts.Engines(
        [Choice(KOKORO), Choice(PIPER)],
        VoiceSettings(),
        loader_of(tts.TtsError("no"), piper),
    )
    assert engines.current() is piper
    assert engines.current() is piper  # loaded once
    assert engines.skipped == ["kokoro on cpu: TtsError: no"]


def test_engines_move_on_after_a_failure_on_a_chunk() -> None:
    first, second = FakeEngine(KOKORO), FakeEngine(PIPER)
    engines = tts.Engines(
        [Choice(KOKORO), Choice(PIPER)], VoiceSettings(), loader_of(first, second)
    )
    assert engines.current() is first
    assert engines.failed(first, tts.TtsError("down")) is second
    assert engines.failed(second, tts.TtsError("down")) is None
    assert engines.current() is first  # the next utterance tries it again
    assert engines.skipped == []


def test_an_engine_is_dropped_only_after_failures_in_a_row() -> None:
    first, second = FakeEngine(KOKORO), FakeEngine(PIPER)
    engines = tts.Engines(
        [Choice(KOKORO, GPU), Choice(PIPER)], VoiceSettings(), loader_of(first, second)
    )
    assert engines.current() is first
    for _ in range(tts.STRIKES - 1):
        assert engines.failed(first, tts.TtsError("CUDA out of memory")) is second
    engines.worked(first)  # a chunk said: the count starts again
    for _ in range(tts.STRIKES - 1):
        engines.failed(first, tts.TtsError("CUDA out of memory"))
    assert engines.current() is first and engines.skipped == []
    assert engines.failed(first, tts.TtsError("CUDA out of memory")) is second
    assert engines.current() is second
    assert engines.skipped == [
        f"fake kokoro: {tts.STRIKES} failures in a row, last TtsError: CUDA out of memory"
    ]


def test_one_failed_chunk_hands_only_that_utterance_to_the_next() -> None:
    out = FakeOutput()
    flaky, good = FakeEngine(KOKORO), FakeEngine(PIPER, rate=2000)
    speaker = speaker_with(
        flaky, good, output=out, choices=[Choice(KOKORO), Choice(PIPER)]
    )
    flaky.fail = True
    say(speaker, "First sentence is here. Second one.")
    assert good.said == ["First sentence is here.", "Second one."]  # the rest, too
    flaky.fail = False
    say(speaker, "Hello there.")
    assert flaky.said == ["Hello there."] and speaker.last["engine"] == "fake kokoro"


def test_create_with_no_engine_is_unavailable_and_still_finishes_turns(
    tmp_path: Path,
) -> None:
    voice = VoiceSettings(gos_home=tmp_path)
    speaker = tts.create(voice, loader=loader_of(tts.TtsError("a"), tts.TtsError("b")))
    assert isinstance(speaker, tts.Unavailable)
    assert "a" in speaker.reason and "b" in speaker.reason
    done = Done()
    speaker.speak("Hello.", done.on_first, done.on_done)
    assert done.count == 1 and done.first == 0
    speaker.stop()


def test_create_uses_the_glossary_and_the_given_output(tmp_path: Path) -> None:
    engine = FakeEngine()
    out = FakeOutput()
    speaker = tts.create(
        VoiceSettings(gos_home=tmp_path), output=out, loader=loader_of(engine)
    )
    assert isinstance(speaker, tts.Speaker) and speaker.name == PIPER
    say(speaker, "Use the LuV EBF at 1920 EU/t (r:gt.recipe.x/3f9a0c2b1e7d).")
    assert engine.said == ["Use the L U V E B F at nineteen-twenty E U per tick."]


def test_the_hook_finds_this_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tts, "load_engine", loader_of(FakeEngine()))
    found = hooks.speaker(VoiceSettings(gos_home=tmp_path))
    assert isinstance(found, tts.Speaker)


# --- the speaker ------------------------------------------------------------------------------


def test_speak_plays_every_chunk_in_order_and_calls_back_once() -> None:
    out = FakeOutput()
    engine = FakeEngine(seconds=0.05)
    speaker = speaker_with(engine, output=out)
    done = say(speaker, "First sentence is here. Second one. Third one.")  # D23: two
    assert done.first == 1 and done.count == 1
    assert engine.said == ["First sentence is here.", "Second one."]
    [played] = out.streams
    assert played.rate == 1000 and played.closed and not played.aborted
    values = {bytes(played.pcm[i : i + 2]) for i in range(0, len(played.pcm), 2)}
    assert values == {(1).to_bytes(2, "little"), (2).to_bytes(2, "little")}
    assert played.pcm.index((2).to_bytes(2, "little")) > 0  # chunk 1 first
    assert speaker.last["chunks"] == 2 and "first_audio_ms" in speaker.last


def test_audio_is_written_in_short_blocks() -> None:
    out = FakeOutput()
    say(speaker_with(FakeEngine(seconds=0.2), output=out), "One.")
    assert len(out.streams[0].writes) == 5  # 0.2 s in 40 ms blocks


def test_nothing_to_say_finishes_at_once() -> None:
    out = FakeOutput()
    speaker = speaker_with(FakeEngine(), output=out)
    done = Done()
    speaker.speak("(r:abc/123456789abc)", done.on_first, done.on_done)
    assert done.count == 1 and done.first == 0 and out.streams == []


def test_barge_in_stops_playback_within_a_block() -> None:
    out = FakeOutput(realtime=True)
    speaker = speaker_with(FakeEngine(seconds=3.0), output=out)
    done = Done()
    speaker.speak("A long answer.", done.on_first, done.on_done)
    deadline = time.monotonic() + 5
    while not done.first and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    speaker.stop()
    done.wait()
    assert time.monotonic() - started < 0.5
    [played] = out.streams
    assert played.aborted and played.seconds < 1.0
    assert speaker.last["stopped"] is True


def test_a_stop_while_synthesising_says_nothing() -> None:
    out = FakeOutput()
    speaker = speaker_with(FakeEngine(delay=0.3), output=out)
    done = Done()
    speaker.speak("Slow to make.", done.on_first, done.on_done)
    speaker.stop()
    done.wait()
    assert done.first == 0 and out.streams == []


def test_a_new_utterance_stops_the_last() -> None:
    out = FakeOutput(realtime=True)
    speaker = speaker_with(FakeEngine(seconds=2.0), output=out)
    first = Done()
    speaker.speak("First answer.", first.on_first, first.on_done)
    second = say(speaker, "Second answer.")
    first.wait()
    assert out.streams[0].aborted and second.count == 1


def test_a_failing_engine_hands_the_chunk_to_the_next() -> None:
    out = FakeOutput()
    broken, good = (
        FakeEngine(KOKORO, rate=1000, fail=True),
        FakeEngine(PIPER, rate=2000),
    )
    speaker = speaker_with(
        broken, good, output=out, choices=[Choice(KOKORO), Choice(PIPER)]
    )
    done = say(speaker, "Hello there.")
    assert done.first == 1 and good.said == ["Hello there."]
    assert [s.rate for s in out.streams] == [2000]
    assert speaker.last["engine"] == "fake piper"


def test_no_engine_left_ends_the_utterance_quietly() -> None:
    speaker = speaker_with(FakeEngine(fail=True), output=FakeOutput())
    done = say(speaker, "Hello.")
    assert done.first == 0 and speaker.last["error"] == "no text-to-speech engine"


def test_a_device_failure_ends_the_utterance(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        name = "broken"

        def open(self, rate: int) -> playback.Stream:
            raise AudioError("headset gone")

    speaker = speaker_with(FakeEngine(), output=Broken())
    done = say(speaker, "Hello.")
    assert done.count == 1 and "headset gone" in speaker.last["error"]


# --- engines ----------------------------------------------------------------------------------


class Response(io.BytesIO):
    def __init__(self, data: bytes, pieces: list[int]) -> None:
        super().__init__(data)
        self.pieces = pieces

    def read(self, size: int | None = -1) -> bytes:
        return super().read(self.pieces.pop(0) if self.pieces else size)


def test_elevenlabs_request_and_streamed_audio() -> None:
    seen: list[urllib.request.Request] = []
    audio = bytes(range(10))

    def opener(request: urllib.request.Request, timeout: float) -> Response:
        seen.append(request)
        assert timeout == tts.HTTP_TIMEOUT_S
        return Response(audio, [3, 4, 3])

    engine = tts.ElevenLabsEngine("secret-key", "", opener=opener)
    pieces = list(engine.stream("Hello there."))
    assert b"".join(pieces) == audio and all(len(p) % 2 == 0 for p in pieces)
    [request] = seen
    assert request.full_url == (
        f"https://api.elevenlabs.io/v1/text-to-speech/{tts.ELEVENLABS_VOICE}/stream"
        "?output_format=pcm_24000"
    )
    assert request.get_header("Xi-api-key") == "secret-key"
    body = json.loads(request.data)  # type: ignore[arg-type]
    assert body == {"text": "Hello there.", "model_id": "eleven_flash_v2_5"}
    assert engine.rate == 24000 and "secret" not in engine.label


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (urllib.error.HTTPError("u", 401, "no", {}, None), "HTTP 401"),  # type: ignore[arg-type]
        (urllib.error.URLError("down"), "unreachable"),
        (TimeoutError(), "unreachable"),
    ],
)
def test_elevenlabs_failures_never_show_the_key(error: Exception, said: str) -> None:
    def opener(request: urllib.request.Request, timeout: float) -> Response:
        raise error

    engine = tts.ElevenLabsEngine("secret-key", opener=opener)
    with pytest.raises(tts.TtsError, match=said) as caught:
        list(engine.stream("Hi."))
    assert "secret" not in str(caught.value)


def test_the_elevenlabs_key_comes_from_the_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", missing)
    with pytest.raises(tts.TtsError, match="gos key set elevenlabs"):
        tts.ElevenLabsEngine.load("")
    monkeypatch.setattr(keys, "get_key", lambda name: f"{name}-value")
    assert tts.ElevenLabsEngine.load("myvoice").voice == "myvoice"
    assert "elevenlabs" in keys.NAMES


PIPER_CONFIG = {
    "audio": {"sample_rate": 22050},
    "espeak": {"voice": "en-us"},
    "inference": {"noise_scale": 0.5, "length_scale": 1.0, "noise_w": 0.3},
    "phoneme_id_map": {
        "_": [0],
        "^": [1],
        "$": [2],
        " ": [3],
        "a": [4],
        "ɪ": [5],
        "aɪ": [6],
        "ˈ": [7],
        "h": [8],
        ".": [9],
        "̃": [10],
    },
    "vowel_clusters": [["a", "ɪ"]],
}


def test_piper_symbols_and_ids_follow_the_voice_config() -> None:
    engine = tts.PiperEngine(None, PIPER_CONFIG, lambda text: text, "piper test")
    assert engine.rate == 22050 and engine.scales == [0.5, 1.0, 0.3]
    assert engine.symbols("hˈaɪ") == ["h", "ˈ", "aɪ"]
    assert engine.symbols("ã") == ["a", "̃"]  # decomposed (NFD)
    # start, pad, then each known symbol and a pad; "z" is unknown and skipped; end
    assert engine.ids("haɪz.") == [1, 0, 8, 0, 6, 0, 9, 0, 2]


def test_piper_runs_the_model_with_ids_lengths_and_scales() -> None:
    np = pytest.importorskip("numpy")
    calls: list[dict[str, Any]] = []

    class Model:
        def run(self, outputs: object, feeds: dict[str, Any]) -> list[Any]:
            calls.append(feeds)
            return [np.array([[[[0.5, -1.5]]]], dtype=np.float32)]

    engine = tts.PiperEngine(Model(), PIPER_CONFIG, lambda text: "ha", "piper test")
    assert b"".join(engine.stream("Ha.")) == np.array([16383, -32767], "<i2").tobytes()
    [feeds] = calls
    assert feeds["input"].tolist() == [[1, 0, 8, 0, 4, 0, 2]]
    assert feeds["input_lengths"].tolist() == [7]
    assert feeds["scales"].tolist() == pytest.approx([0.5, 1.0, 0.3])


def test_engines_say_why_when_kokoro_onnx_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tts.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(tts.TtsError, match="uv sync --group tts"):
        tts.KokoroEngine.load(Path("."), CPU, "", print)
    with pytest.raises(tts.TtsError, match="uv sync --group tts"):
        tts.PiperEngine.load(Path("."), CPU, "", print)


def test_providers_cpu_and_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tts.providers(CPU) == ["CPUExecutionProvider"]
    fake = types.SimpleNamespace(
        get_available_providers=lambda: ["CPUExecutionProvider"]
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    with pytest.raises(tts.TtsError, match="no GPU provider"):
        tts.providers(GPU)
    preloaded: list[bool] = []
    fake.preload_dlls = lambda: preloaded.append(True)  # type: ignore[attr-defined]
    fake.get_available_providers = lambda: [
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ]
    assert tts.providers(GPU) == ["DmlExecutionProvider", "CPUExecutionProvider"]
    assert preloaded == [True]


def test_model_files_fetches_only_what_is_missing(tmp_path: Path) -> None:
    (tmp_path / "a.onnx").write_bytes(b"x")
    fetched: list[tuple[str, str, Path]] = []
    notes: list[str] = []

    def fetch(url: str, sha: str, dest: Path) -> None:
        fetched.append((url, sha, dest))

    files = {"a.onnx": "1" * 64, "b.bin": "2" * 64}
    REAL_MODEL_FILES(tmp_path, files, "https://host/x/", notes.append, fetch)
    assert fetched == [("https://host/x/b.bin", "2" * 64, tmp_path / "b.bin")]
    assert notes and "b.bin" in notes[0]


def test_pinned_models_are_https_with_sha256() -> None:
    assert tts.KOKORO_URL.startswith("https://") and tts.PIPER_URL.startswith(
        "https://"
    )
    hashes = [*tts.KOKORO_FILES.values()]
    for _, onnx, config in tts.PIPER_VOICES.values():
        hashes += [onnx, config]
    assert all(len(h) == 64 and int(h, 16) >= 0 for h in hashes)
    assert tts.PIPER_VOICE in tts.PIPER_VOICES


def test_pcm16_clips() -> None:
    np = pytest.importorskip("numpy")
    got = tts.pcm16(np.array([0.0, 1.0, -1.0, 2.0], dtype=np.float32))
    assert np.frombuffer(got, "<i2").tolist() == [0, 32767, -32767, 32767]


# --- the output device ------------------------------------------------------------------------

DEVICES = [
    Device(3, "Speakers (Realtek)", True),
    Device(5, "Headset Chat (Arctis Nova Elite)", False),
    Device(6, "Headset Game (Arctis Nova Elite)", False),
]


def test_the_chat_endpoint_is_picked_by_name() -> None:
    assert playback.pick_device("Chat", DEVICES) == playback.Pick(DEVICES[1], "")
    assert playback.pick_device("", DEVICES).device == DEVICES[0]


def test_no_match_or_two_matches_fall_back_to_the_default() -> None:
    none = playback.pick_device("Discord", DEVICES)
    assert none.device == DEVICES[0] and "no output device" in none.why
    two = playback.pick_device("Arctis", DEVICES)
    assert two.device == DEVICES[0] and "2 device names" in two.why
    with pytest.raises(AudioError):
        playback.pick_device("Chat", [])


def test_of_several_matches_the_exact_name_wins() -> None:
    devices = [
        Device(0, "Speakers (Realtek)", True),
        Device(1, "Chat (Arctis Nova Elite)", False),
        Device(
            2,
            "SteelSeries Sonar - Chat (SteelSeries Sonar Virtual Audio Device)",
            False,
        ),
    ]
    exact = playback.pick_device("chat (arctis nova elite)", devices)
    assert exact == playback.Pick(devices[1], "")
    assert "2 device names" in playback.pick_device("Chat", devices).why


def test_wav_output_writes_each_stream(tmp_path: Path) -> None:
    out = WavOutput(tmp_path)
    stream = out.open(22050)
    stream.write(b"\x01\x00" * 100)
    stream.close()
    with wave.open(str(tmp_path / "001.wav")) as w:
        assert (w.getframerate(), w.getnframes(), w.getnchannels()) == (22050, 100, 1)


def test_the_default_output_off_windows_is_fake() -> None:
    if sys.platform == "win32":
        assert isinstance(playback.default_output("Chat"), playback.WasapiOutput)
    else:
        assert isinstance(playback.default_output("Chat"), FakeOutput)


# --- settings ---------------------------------------------------------------------------------


def test_tts_settings_defaults_follow_appendix_c() -> None:
    found = settings.parse({})
    assert (found.tts, found.tts_device, found.tts_voice, found.output_endpoint) == (
        "kokoro",
        "cpu",
        "",
        "Chat",
    )


def test_tts_settings_parse_and_refuse() -> None:
    found = settings.parse(
        {
            "tts": "piper",
            "tts_device": "gpu",
            "tts_voice": " bm_george ",
            "output_endpoint": "",
        }
    )
    assert (found.tts, found.tts_device, found.tts_voice, found.output_endpoint) == (
        "piper",
        "gpu",
        "bm_george",
        "",
    )
    with pytest.raises(settings.SettingsError) as caught:
        settings.parse(
            {
                "tts": "espeak",
                "tts_device": "npu",
                "tts_voice": "a b",
                "output_endpoint": 3,
            }
        )
    message = str(caught.value)
    for key in (
        "tts must",
        "tts_device must",
        "tts_voice must",
        "output_endpoint must",
    ):
        assert key in message


def test_a_fallback_engine_uses_its_own_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def kokoro(models: Path, device: str, voice: str, note: object) -> FakeEngine:
        seen.append((KOKORO, voice))
        return FakeEngine(KOKORO)

    def piper(models: Path, device: str, voice: str, note: object) -> FakeEngine:
        seen.append((PIPER, voice))
        return FakeEngine(PIPER)

    monkeypatch.setattr(tts.KokoroEngine, "load", kokoro)
    monkeypatch.setattr(tts.PiperEngine, "load", piper)
    voice = replace(VoiceSettings(), tts=KOKORO, tts_voice="bm_george")
    tts.load_engine(Choice(KOKORO), voice, print)
    tts.load_engine(Choice(PIPER), voice, print)
    assert seen == [(KOKORO, "bm_george"), (PIPER, "")]


# --- with the voice client (WO-5.1's hooks) ---------------------------------------------------


def wait_for(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_the_voice_client_speaks_and_its_talk_key_barges_in(
    clock: Any,  # noqa: ANN401 - the conftest Clock
    core: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Callable[[VoiceClient, Callable[[], bool]], None],
) -> None:
    out = FakeOutput(realtime=True)
    engine = FakeEngine(seconds=3.0)
    voice = VoiceClient(
        VoiceSettings(),
        core,
        transcriber=transcriber,
        speaker=speaker_with(engine, output=out),
        clock=clock,
    )
    voice.say("Steel Ingot (r:gt.recipe.x/3f9a0c2b1e7d) takes 25 s.")
    assert voice.state[0] == SPEAKING
    wait_for(lambda: bool(out.streams and out.streams[0].writes))
    assert engine.said == ["Steel Ingot takes twenty-five seconds."]
    voice.handle(KeyEvent(0x7C, True, clock()))  # F13 down: barge-in (D179)
    wait_for(lambda: out.streams[0].closed, 1.0)
    assert out.streams[0].aborted and out.streams[0].seconds < 1.5
    voice.handle(KeyEvent(0x7C, False, clock()))


def test_the_voice_client_hears_when_speech_ends(
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Callable[[VoiceClient, Callable[[], bool]], None],
) -> None:
    voice = VoiceClient(
        VoiceSettings(),
        core,
        transcriber=transcriber,
        speaker=speaker_with(FakeEngine(seconds=0.05), output=FakeOutput()),
        clock=clock,
    )
    voice._set_state(IDLE)
    voice.say("Short answer.")
    run_events(voice, lambda: voice.state == (IDLE, None))
