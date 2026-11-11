"""WO-5.4's slow tests: the real wake engines with downloaded models (D185-D187).

Skipped unless the models are present; nothing is downloaded here:

- ``GOS_WAKE_TEST_HOME``: a GOS_HOME whose ``models/kws`` holds the sherpa-onnx KWS model
  (``gos voice wake-eval`` downloads it there), with the ``wake`` and ``stt`` groups installed
  (the VAD is faster-whisper's copy of Silero);
- ``GOS_WAKE_TEST_KOKORO``: a folder with Kokoro's ``kokoro-v1.0.onnx`` and
  ``voices-v1.0.bin``, and kokoro-onnx installed, to speak the phrases (else only silence and
  noise are checked);
- ``GOS_WAKE_TEST_OWW``: a folder with openWakeWord's ``melspectrogram.onnx`` and
  ``embedding_model.onnx`` and the model ``hey_jarvis_v0.1.onnx`` (openWakeWord's release
  v0.5.1; CC BY-NC-SA, a test fixture only), to run the openWakeWord engine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from gregtec_voice import wake, wake_eval
from gregtec_voice.settings import VoiceSettings

pytestmark = pytest.mark.slow


def _folder(name: str) -> Path | None:
    found = os.environ.get(name)
    return Path(found) if found and Path(found).is_dir() else None


@pytest.fixture(scope="module")
def home() -> Path:
    found = _folder("GOS_WAKE_TEST_HOME")
    if found is None or not (found / "models" / "kws" / wake.KWS_MODEL).is_dir():
        pytest.skip("GOS_WAKE_TEST_HOME with models/kws isn't set (no download here)")
    pytest.importorskip("sherpa_onnx")
    pytest.importorskip("faster_whisper")
    return found


@pytest.fixture(scope="module")
def tts() -> wake_eval.KokoroTts:
    found = _folder("GOS_WAKE_TEST_KOKORO")
    if found is None:
        pytest.skip("GOS_WAKE_TEST_KOKORO isn't set")
    pytest.importorskip("kokoro_onnx")
    return wake_eval.KokoroTts(found)


def said(tts: wake_eval.KokoroTts, *parts: str) -> bytes:
    np = pytest.importorskip("numpy")
    pieces = [wake_eval.silence(0.6)]
    for text in parts:
        pieces += [tts.say(text, "am_michael", 1.0), wake_eval.silence(0.35)]
    pieces.append(wake_eval.silence(2.5))
    return wake_eval.pcm16(np.concatenate(pieces))


def run(engine: wake.Detector, pcm: bytes, verifier: Any = None) -> wake_eval.Bus:  # noqa: ANN401
    bus = wake_eval.Bus()
    word = wake.WakeWord(
        engine, wake.vad(), bus, phrase="hey archivist", verifier=verifier,
        run=lambda job: job(),
    )  # fmt: skip
    wake_eval.feed(word, pcm, 1000.0)
    return bus


def test_the_kws_engine_hears_nothing_in_noise(home: Path) -> None:
    np = pytest.importorskip("numpy")
    engine = wake.detector(VoiceSettings(gos_home=home))
    rng = np.random.default_rng(5)
    for kind in ("quiet", "game", "clicks", "hum"):
        noise = wake_eval.noise(kind, 20.0, rng) * 0.05
        assert run(engine, wake_eval.pcm16(noise)).wakes == []


def test_the_silero_vad_hears_no_speech_in_silence(home: Path) -> None:
    vad = wake.vad()
    assert not any(vad.is_speech(bytes(640)) for _ in range(100))


def test_the_kws_engine_wakes_on_the_phrase_and_ends_on_silence(
    home: Path, tts: wake_eval.KokoroTts
) -> None:
    engine = wake.detector(VoiceSettings(gos_home=home))
    bus = run(engine, said(tts, "Hey archivist,", "what uses tin?"))
    assert len(bus.wakes) == 1 and len(bus.ends) == 1 and bus.cancels == 0
    vad = wake.vad()
    speech = said(tts, "What does the blast furnace need?")
    assert any(vad.is_speech(speech[i : i + 640]) for i in range(0, len(speech), 640))


def test_the_second_stage_with_whisper(home: Path, tts: wake_eval.KokoroTts) -> None:
    """With a Whisper model in GOS_HOME (``GOS_WAKE_TEST_STT``, default base.en)."""
    from gregtec_voice import stt

    model = os.environ.get("GOS_WAKE_TEST_STT", "base.en")
    try:
        engine = stt.load("whisper-cpu", model, home, note=print)
    except stt.SttError as exc:
        pytest.skip(f"no Whisper {model} in {home}: {exc}")
    found = wake.detector(VoiceSettings(gos_home=home))
    bus = run(
        found, said(tts, "Hey archivist,", "what uses tin?"), wake.verifier(engine)
    )
    assert len(bus.wakes) == 1 and len(bus.ends) == 1

    class Once:
        """Stage 1 fires at the end of the first utterance, whatever was said."""

        name = "once"

        def __init__(self, at: int) -> None:
            self.at, self.blocks = at, 0

        def accept(self, pcm: bytes) -> bool:
            self.blocks += 1
            return self.blocks == self.at

        def reset(self) -> None:
            pass

    for text, kept in (
        ("Hey archivist,", True),
        ("The foreman villager is useless.", False),
        ("I need more cupronickel coils.", False),
    ):
        pcm = said(tts, text, "what uses tin?")
        spoken = len(said(tts, text)) - round(2.5 * 32000)  # to the end of the phrase
        bus = run(Once(spoken // 640), pcm, wake.verifier(engine))
        assert (len(bus.ends) == 1) is kept, text


def test_openwakeword_on_onnx(tts: wake_eval.KokoroTts) -> None:
    pytest.importorskip("onnxruntime")
    folder = _folder("GOS_WAKE_TEST_OWW")
    if folder is None or not (folder / "hey_jarvis_v0.1.onnx").is_file():
        pytest.skip("GOS_WAKE_TEST_OWW with hey_jarvis_v0.1.onnx isn't set")
    np = pytest.importorskip("numpy")
    engine = wake.OpenWakeWord(folder / "hey_jarvis_v0.1.onnx", folder)
    pcm = said(tts, "Hey Jarvis,", "what uses tin?")
    samples = np.frombuffer(pcm, "<i2").astype(np.float32)
    frames = len(samples) // wake.OWW_FRAME
    scores = [
        engine.score(samples[i * wake.OWW_FRAME : (i + 1) * wake.OWW_FRAME])
        for i in range(frames)
    ]
    assert max(scores) > 0.9
    engine.reset()
    quiet = wake_eval.pcm16(
        wake_eval.noise("game", 10.0, np.random.default_rng(2)) * 0.05
    )
    assert not any(engine.accept(quiet[i : i + 640]) for i in range(0, len(quiet), 640))
