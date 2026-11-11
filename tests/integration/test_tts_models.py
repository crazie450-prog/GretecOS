"""WO-5.3's slow tests: the real text-to-speech engines with downloaded models (D203, D204).

Skipped unless the models are present; nothing is downloaded here:

- ``GOS_TTS_TEST_HOME``: a GOS_HOME whose ``models/kokoro`` and ``models/piper`` hold the pinned
  files (``gos voice tts-bench`` downloads them there), with the ``tts`` group installed;
- and, for the round trip through speech-to-text, its ``models/whisper`` holding faster-whisper
  ``small.en`` (``gos voice stt --model small.en`` downloads it), with the ``stt`` group.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gregtec_voice import tts, tts_bench
from gregtec_voice.playback import FakeOutput
from gregtec_voice.settings import VoiceSettings
from gregtec_voice.tts import CPU, KOKORO, PIPER, Choice

pytestmark = pytest.mark.slow

# A floor against regressions, not the acceptance (Clayton's listening check is): synthetic
# speech heard by a small CPU Whisper confuses letters such as V and B (D204's smoke run).
ROUND_TRIP_FLOOR = 0.6


@pytest.fixture(scope="module")
def home() -> Path:
    found = os.environ.get("GOS_TTS_TEST_HOME")
    if not found or not (Path(found) / "models" / "kokoro").is_dir():
        pytest.skip("GOS_TTS_TEST_HOME with models/kokoro and models/piper isn't set")
    pytest.importorskip("kokoro_onnx")
    return Path(found)


@pytest.mark.parametrize("engine", [KOKORO, PIPER])
def test_each_engine_speaks_through_the_speaker(home: Path, engine: str) -> None:
    np = pytest.importorskip("numpy")
    voice = VoiceSettings(gos_home=home, tts=engine)
    out = FakeOutput()
    result = tts_bench.bench_one(
        Choice(engine, CPU), voice, out, tts.lexicon(home), runs=2
    )
    assert result.problem == "" and len(result.first_ms) == 2, result
    played = out.streams[-1]
    samples = np.frombuffer(bytes(played.pcm), "<i2")
    assert played.seconds > 1.0 and np.abs(samples).max() > 3000


def test_the_round_trip_hears_most_check_phrases(home: Path) -> None:
    if not (home / "models" / "whisper").is_dir():
        pytest.skip("no models/whisper in GOS_TTS_TEST_HOME")
    pytest.importorskip("faster_whisper")
    from gregtec_voice import stt

    try:
        transcriber = stt.load("whisper-cpu", "small.en", home)
    except stt.SttError as exc:
        pytest.skip(f"small.en isn't downloaded: {exc}")
    engine = tts.KokoroEngine.load(home / "models", CPU, "", print)
    rows = tts_bench.check(
        engine, transcriber, tts.lexicon(home), tts_bench.load_phrases()
    )
    heard = sum(r.ok for r in rows)
    assert heard >= ROUND_TRIP_FLOOR * len(rows), [
        (r.phrase.id, r.transcript) for r in rows if not r.ok
    ]
