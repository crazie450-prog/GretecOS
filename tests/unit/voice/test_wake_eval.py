"""WO-5.4: the wake-phrase harness (D187) and ``gos voice wake-eval`` / ``capture`` with fake
engines; the synthetic-audio helpers where numpy is installed (the stt group)."""

from __future__ import annotations

import csv
import io
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from gregtec_voice import cli, wake, wake_eval
from gregtec_voice.audio import SAMPLE_RATE, Block
from gregtec_voice.wake_eval import Clip, Score

SPEECH = b"\x01\x10" * 320
QUIET = bytes(640)


@dataclass
class MarkDetector:
    """Fires on any block that starts with the marker bytes (b"WW")."""

    name: str = "fake"
    resets: int = 0

    def accept(self, pcm: bytes) -> bool:
        return pcm[:2] == b"WW"

    def reset(self) -> None:
        self.resets += 1


class FakeVad:
    def is_speech(self, pcm: bytes) -> bool:
        return any(pcm)


MARK = b"WW" + SPEECH[2:]


def attempt(question_s: float = 1.0) -> bytes:
    """The phrase (0.4 s, ending in the marker), a pause, a question, then silence."""
    return (
        SPEECH * 19
        + MARK
        + QUIET * 10
        + SPEECH * round(question_s / 0.02)
        + QUIET * 100
    )


def test_the_candidate_set_and_the_chatter_load() -> None:
    candidates = wake_eval.load_phrases()
    phrases = [c.phrase for c in candidates]
    assert "hey archivist" in phrases and len(phrases) >= 6
    assert all("greg" not in p for p in phrases)  # PM08
    assert all(c.near for c in candidates)  # every phrase has near misses
    lines = wake_eval.load_lines()
    assert len(lines) >= 60 and not any(x.startswith("#") for x in lines)
    assert sum("GregTech" in x or "Gregtec" in x for x in lines) >= 3
    assert len(wake_eval.load_questions()) == 50


def test_a_bad_candidate_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "p.tsv"
    path.write_text("phrase\tnote\tnear\nhey gregtech\tno\t\n", encoding="utf-8")
    with pytest.raises(
        wake_eval.EvalError, match='line 2: the phrase must not contain "GregTech"'
    ):
        wake_eval.load_phrases(path)


def test_score_counts_hits_misses_and_false_wakes() -> None:
    positives = [
        Clip(attempt(), "a1", 0.4),
        Clip(attempt(), "a2", 0.4),
        Clip(SPEECH * 30 + QUIET * 100, "missed", 0.4),  # no marker: stage 1 misses
    ]
    negatives = [
        Clip(SPEECH * 50 + QUIET * 50, "chatter: fine"),
        Clip(MARK + SPEECH * 20 + QUIET * 100, "chatter: a look-alike"),
        Clip(QUIET * 3000, "noise: quiet"),
    ]
    heard = iter(["hey archivist", "hey archivist", "hey artist"])
    result = wake_eval.score(
        "hey archivist",
        "fake",
        MarkDetector(),
        FakeVad,
        positives,
        negatives,
        lambda pcm: next(heard),
    )
    assert (result.attempts, result.detected, result.hits) == (3, 2, 2)
    assert result.misses == ["missed"]
    assert result.latency_ms == [0, 0]  # the marker block ends the phrase
    assert (result.false_detected, result.false_wakes) == (1, 0)  # stage 2 dropped it
    assert result.negative_s == pytest.approx(0.02 * 3221)
    assert result.offenders == []


def test_stage_one_only_counts_every_detection_as_a_false_wake() -> None:
    negatives = [Clip(MARK + SPEECH * 20 + QUIET * 100, "chatter: look-alike")] * 3
    result = wake_eval.score(
        "hey archivist", "fake", MarkDetector(), FakeVad, [], negatives, None
    )
    assert result.false_wakes == 3 and result.offenders == ["chatter: look-alike"] * 3
    assert result.per_hour == pytest.approx(3 / (3 * 121 * 0.02 / 3600))
    assert not result.met  # no attempts


def test_met_follows_plan_8s_targets() -> None:
    s = Score("p", "e", attempts=40, hits=38, negative_s=7200, false_wakes=2)
    assert s.met
    assert not Score("p", "e", attempts=40, hits=37, negative_s=7200).met
    assert not Score("p", "e", attempts=40, hits=40, negative_s=7200, false_wakes=3).met


def test_report_and_tsv(tmp_path: Path) -> None:
    s = Score(
        "hey archivist",
        "sherpa-onnx",
        attempts=40,
        detected=39,
        hits=38,
        latency_ms=[200, 300, 250],
        misses=["bm_george x1.3 game 6 dB", "af_heart x1 quiet 60 dB"],
        negative_s=1800,
        false_detected=4,
        false_wakes=1,
        offenders=["am_adam: hey, archive this"],
    )
    out = io.StringIO()
    wake_eval.report([s], out, second_stage=True)
    text = out.getvalue()
    assert "hey archivist" in text and "38/40" in text and "250 ms" in text
    assert "false wake, 'hey archivist': am_adam: hey, archive this" in text
    assert "missed, 'hey archivist': bm_george x1.3 game 6 dB" in text
    path = tmp_path / "r" / "wake.tsv"
    wake_eval.write_tsv([s], path, second_stage=True)
    with open(path, encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f, delimiter="\t"))
    assert row["hits"] == "38" and row["false_per_hour"] == "2.0" and row["met"] == "0"
    assert row["latency_p50_ms"] == "250"


# --- the CLI --------------------------------------------------------------------------------


def setup(tmp_path: Path, voice: str = "") -> Path:
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n[voice]\n{voice}\n',
        encoding="utf-8",
    )
    return toml


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(list(argv), out), out.getvalue()


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    phrases: list[str] = []

    def detector(settings: Any, *, phrase: str | None = None, note: Any = None) -> Any:  # noqa: ANN401
        phrases.append(phrase or settings.wake_phrase)
        return MarkDetector()

    monkeypatch.setattr(wake, "detector", detector)
    monkeypatch.setattr(wake, "vad", FakeVad)
    return phrases


def test_wake_eval_on_recordings_meets_the_targets(
    tmp_path: Path, fake_engine: list[str]
) -> None:
    audio = tmp_path / "rec"
    for i in range(40):
        write_wav(audio / "wake" / f"w{i:02}.wav", attempt() if i else SPEECH * 50)
    write_wav(audio / "play" / "evening.wav", SPEECH * 3000 + MARK + QUIET * 200)
    toml = setup(tmp_path, 'wake_phrase = "okay tessera"')
    out_tsv = tmp_path / "wake.tsv"
    code, text = run(
        "wake-eval", "--config", str(toml), "--audio", str(audio), "--no-verify",
        "--out", str(out_tsv),
    )  # fmt: skip
    assert fake_engine == ["okay tessera"]
    assert "39/40" in text and "missed, 'okay tessera': w00.wav" in text
    # one false wake in 64 s of play is far over 1 an hour
    assert "false wake, 'okay tessera': evening.wav" in text
    assert code == cli.EXIT_PROBLEM and out_tsv.is_file()
    write_wav(audio / "play" / "evening.wav", SPEECH * 3000 + QUIET * 200)
    code, _ = run(
        "wake-eval", "--config", str(toml), "--audio", str(audio), "--no-verify",
        "--out", str(out_tsv),
    )  # fmt: skip
    assert code == 0


def test_wake_eval_needs_a_source_and_its_folders(tmp_path: Path) -> None:
    toml = setup(tmp_path)
    with pytest.raises(SystemExit):
        run("wake-eval", "--config", str(toml))
    code, text = run("wake-eval", "--config", str(toml), "--audio", str(tmp_path))
    assert code == cli.EXIT_SETUP and "No folder" in text
    code, text = run(
        "wake-eval", "--config", str(toml), "--audio", str(tmp_path), "--phrase", "greg"
    )
    assert code == cli.EXIT_SETUP and "--phrase must not contain" in text
    code, text = run(
        "wake-eval",
        "--config",
        str(toml),
        "--kokoro",
        str(tmp_path),
        "--threshold",
        "2",
    )
    assert code == cli.EXIT_SETUP and "--threshold" in text


def test_wake_eval_says_when_the_engine_cant_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        raise wake.WakeError("sherpa-onnx isn't installed here: uv sync --group wake")

    monkeypatch.setattr(wake, "vad", FakeVad)
    monkeypatch.setattr(wake, "detector", missing)
    audio = tmp_path / "rec"
    write_wav(audio / "wake" / "w.wav", attempt())
    write_wav(audio / "play" / "p.wav", QUIET)
    code, text = run(
        "wake-eval",
        "--config",
        str(setup(tmp_path)),
        "--audio",
        str(audio),
        "--no-verify",
    )
    assert code == cli.EXIT_SETUP and "uv sync --group wake" in text


def test_kokoro_must_be_installed_with_its_files(tmp_path: Path) -> None:
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        with pytest.raises(wake_eval.EvalError, match="kokoro-onnx isn't installed"):
            wake_eval.KokoroTts(tmp_path)
    else:
        with pytest.raises(wake_eval.EvalError, match="lacks kokoro-v1.0.onnx"):
            wake_eval.KokoroTts(tmp_path)


def test_capture_needs_windows_and_a_new_file(tmp_path: Path) -> None:
    toml = setup(tmp_path)
    if cli.sys.platform != "win32":
        code, text = run("capture", "--config", str(toml), str(tmp_path / "x.wav"))
        assert code == cli.EXIT_SETUP and "Windows" in text


def test_the_capture_sink_writes_a_16k_wav(tmp_path: Path) -> None:
    sink = cli._WavSink(tmp_path / "out" / "play.wav")
    for i in range(50):
        sink.feed(Block(SPEECH, float(i)))
    sink.close()
    with wave.open(str(tmp_path / "out" / "play.wav"), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 16000)
    assert sink.seconds == pytest.approx(1.0)


# --- synthetic audio (numpy) ------------------------------------------------------------------


@dataclass
class BeepTts:
    """A "voice" of tones: each text is a beep as long as its words."""

    said: list[tuple[str, str, float]] = field(default_factory=list)

    def say(self, text: str, voice: str, speed: float) -> Any:  # noqa: ANN401
        np = pytest.importorskip("numpy")
        self.said.append((text, voice, speed))
        n = round(0.25 * len(text.split()) / speed * SAMPLE_RATE)
        return (0.3 * np.sin(np.arange(n) * 0.05)).astype(np.float32)


def test_synthetic_attempts_and_chatter() -> None:
    np = pytest.importorskip("numpy")
    tts = BeepTts()
    negatives, babble = wake_eval.chatter(
        ["one two", "three four five"], tts, noise_minutes=0.1
    )
    assert [c.label for c in negatives] == [
        "af_heart: one two",
        "am_michael: three four five",
        "noise: game",
    ]
    assert negatives[2].seconds == pytest.approx(6.0)
    spoken = sum(round(0.25 * len(t.split()) / v * SAMPLE_RATE) for t, _, v in tts.said)
    assert len(babble) == spoken  # the clean speech, for babble
    clips = wake_eval.attempts("hey archivist", tts, ["what uses tin"], babble=babble)
    assert len(clips) == wake_eval.ATTEMPTS
    assert {t for t, _, _ in tts.said[2:]} == {"Hey archivist,", "what uses tin"}
    assert {v for _, v, _ in tts.said[2:]} == set(wake_eval.VOICES)
    first = clips[0]
    assert first.phrase_end == pytest.approx(wake_eval.LEAD_S + 0.5)
    assert first.label == "af_heart x1.0 quiet 60 dB"
    x = np.frombuffer(first.pcm, "<i2")
    assert np.abs(x).max() > 1000


@pytest.mark.parametrize("kind", ["pink", "hum", "clicks", "game", "quiet"])
def test_noise_has_unit_rms(kind: str) -> None:
    np = pytest.importorskip("numpy")
    x = wake_eval.noise(kind, 1.0, np.random.default_rng(1))
    assert len(x) == SAMPLE_RATE and wake_eval.rms(x) == pytest.approx(1.0, rel=1e-3)


def test_mix_sets_the_snr_against_the_loud_part() -> None:
    np = pytest.importorskip("numpy")
    speech = np.concatenate([np.zeros(16000), 0.5 * np.ones(16000)]).astype(np.float32)
    bg = np.ones(8000, np.float32)
    mixed = wake_eval.mix(speech, bg, 20.0)
    assert mixed[:16000] == pytest.approx(np.full(16000, 0.05), abs=1e-6)


def test_resample_keeps_a_tone() -> None:
    np = pytest.importorskip("numpy")
    t = np.arange(24000) / 24000
    y = wake_eval.resample(np.sin(2 * np.pi * 440 * t).astype(np.float32), 24000, 16000)
    assert len(y) == 16000
    peak = np.argmax(np.abs(np.fft.rfft(y)))
    assert peak == 440
