"""WO-5.4: the wake engine's steps with a fake detector and VAD (D185, D186)."""

from __future__ import annotations

import bz2
import hashlib
import io
import logging
import tarfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from gregtec_voice import wake
from gregtec_voice.audio import BLOCK_FRAMES, SAMPLE_BYTES, Block
from gregtec_voice.settings import VoiceSettings
from gregtec_voice.wake import (
    CANCEL_WINDOW_S,
    END_SILENCE_S,
    ONSET_S,
    VERIFY_TIMEOUT_S,
    WakeError,
    WakeWord,
    match_score,
    matches,
    strip_phrase,
    word_score,
)

SPEECH = b"\x01\x10" * BLOCK_FRAMES  # the fake VAD hears any non-zero block as speech
QUIET = bytes(BLOCK_FRAMES * SAMPLE_BYTES)
PHRASE = "hey archivist"


@dataclass
class FakeDetector:
    """Fires on the blocks whose number (from 1) is in ``at``."""

    at: set[int] = field(default_factory=set)
    name: str = "fake"
    blocks: int = 0
    resets: int = 0

    def accept(self, pcm: bytes) -> bool:
        self.blocks += 1
        return self.blocks in self.at

    def reset(self) -> None:
        self.resets += 1


class FakeVad:
    def is_speech(self, pcm: bytes) -> bool:
        return any(pcm)


@dataclass
class Bus:
    events: list[tuple[str, float | bool | None]] = field(default_factory=list)

    def wake(self, t: float) -> None:
        self.events.append(("wake", t))

    def end_of_speech(self, t: float) -> None:
        self.events.append(("end", t))

    def cancel(self) -> None:
        self.events.append(("cancel", None))

    def speech(self, active: bool, t: float) -> None:
        self.events.append(("speech", active))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events if k != "speech"]


@dataclass
class Chime:
    played: int = 0

    def play(self) -> None:
        self.played += 1


class Feeder:
    """Feeds 20 ms blocks with capture times from 100.0."""

    def __init__(self, engine: WakeWord) -> None:
        self.engine = engine
        self.t = 100.0

    def blocks(self, pcm: bytes, seconds: float) -> None:
        for _ in range(round(seconds / 0.02)):
            self.t = round(self.t + 0.02, 6)
            self.engine.feed(Block(pcm, self.t))


def build(
    *,
    at: set[int],
    heard: str | None = PHRASE,
    verify: bool = True,
    run: Callable[[Callable[[], None]], None] | None = None,
) -> tuple[WakeWord, Bus, Chime, FakeDetector, list[bytes]]:
    bus, chime, detector = Bus(), Chime(), FakeDetector(at)
    seen: list[bytes] = []

    def verifier(pcm: bytes) -> str:
        seen.append(pcm)
        if heard is None:
            raise RuntimeError("engine down")
        return heard

    engine = WakeWord(
        detector,
        FakeVad(),
        bus,
        phrase=PHRASE,
        verifier=verifier if verify else None,
        chime=chime,
        run=run or (lambda job: job()),
    )
    return engine, bus, chime, detector, seen


def wake_records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        {k: getattr(r, k) for k in ("outcome", "score", "question_s", "end_ms")}
        for r in caplog.records
        if r.getMessage() == "wake"
    ]


def test_a_wake_a_question_and_its_end(caplog: pytest.LogCaptureFixture) -> None:
    """Stage 1 opens the segment at once; stage 2 hears the phrase; the chime; the question;
    0.6 s of silence ends it at the end of the last speech (PM14's start point)."""
    caplog.set_level(logging.INFO, logger="gregtec_voice.wake")
    engine, bus, chime, detector, seen = build(at={40})
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.8)  # the phrase; the detector fires on block 40 (t=100.8)
    assert bus.kinds() == ["wake"] and bus.events[1] == ("wake", 100.8)
    assert (
        len(seen) == 1 and len(seen[0]) == 0.8 * 32000
    )  # the ring: what was heard so far
    assert chime.played == 0  # the verdict is taken with the next block
    feed.blocks(QUIET, 0.2)
    assert chime.played == 1 and engine.state == "verified"
    feed.blocks(SPEECH, 1.0)  # the question
    feed.blocks(QUIET, END_SILENCE_S - 0.02)
    assert bus.kinds() == ["wake"]
    feed.blocks(QUIET, 0.02)
    assert bus.kinds() == ["wake", "end"]
    assert bus.events[-1] == ("end", pytest.approx(102.0))  # the last speech, not now
    assert engine.state == "idle" and detector.resets == 1
    assert wake_records(caplog) == [
        {"outcome": "asked", "score": 1.0, "question_s": 1.0, "end_ms": 600}
    ]
    assert (engine.wakes, engine.chimes) == (1, 1)


def test_a_look_alike_is_dropped_silently_by_the_second_stage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="gregtec_voice.wake")
    engine, bus, chime, _, _ = build(at={10}, heard="hey artist, nice build")
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.2)
    feed.blocks(SPEECH, 0.02)
    assert bus.kinds() == ["wake", "cancel"] and chime.played == 0
    assert wake_records(caplog)[0]["outcome"] == "rejected"
    assert (engine.wakes, engine.chimes) == (1, 0)


def test_no_question_in_the_cancel_window_drops_the_wake(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="gregtec_voice.wake")
    engine, bus, chime, _, _ = build(at={10})
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.2)
    feed.blocks(QUIET, 0.02)  # the chime, with this block
    assert chime.played == 1
    feed.blocks(QUIET, CANCEL_WINDOW_S - 0.04)
    assert bus.kinds() == ["wake"]
    feed.blocks(QUIET, 0.04)
    assert bus.kinds() == ["wake", "cancel"]
    assert wake_records(caplog)[0]["outcome"] == "no question"


def test_the_phrases_own_tail_isnt_a_question() -> None:
    """Speech that goes on from the detection for under 0.5 s is the phrase ending (the VAD
    lingers), not a question."""
    engine, bus, _, _, _ = build(at={10})
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.2)
    feed.blocks(SPEECH, 0.46)
    feed.blocks(QUIET, CANCEL_WINDOW_S)
    assert bus.kinds() == ["wake", "cancel"]


def test_an_early_detection_and_a_pause_for_the_chime_keep_the_question(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stage 1 fires 3 blocks before the phrase ends; Clayton waits 1 s (for the chime), then
    asks: the question is the segment, not the phrase's tail (review finding 2)."""
    caplog.set_level(logging.INFO, logger="gregtec_voice.wake")
    engine, bus, chime, _, _ = build(at={37})
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.8)  # the phrase: blocks 1-40, the detection at block 37
    feed.blocks(QUIET, 1.0)  # the pause; the chime with the first quiet block
    assert chime.played == 1 and bus.kinds() == ["wake"]
    feed.blocks(SPEECH, 1.0)  # the question, from t=101.82
    feed.blocks(QUIET, END_SILENCE_S)
    assert bus.kinds() == ["wake", "end"]
    assert bus.events[-1] == ("end", pytest.approx(102.8))
    assert wake_records(caplog)[0]["question_s"] == 1.0


def test_the_cancel_window_counts_to_the_questions_first_onset() -> None:
    """A question whose onset comes right at the end of the 1.5 s window is kept, though its
    first ONSET_S of speech runs past the window (plan 8: 1.5 s to start the question)."""
    engine, bus, _, _, _ = build(at={10})
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.2)
    feed.blocks(QUIET, CANCEL_WINDOW_S)  # the chime comes with the first quiet block
    feed.blocks(SPEECH, ONSET_S)
    feed.blocks(QUIET, END_SILENCE_S)
    assert bus.kinds() == ["wake", "end"]


def test_a_blip_between_the_phrase_and_the_question_doesnt_start_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A game click (2 blocks) after the phrase is not the question's start: the question that
    follows it is the segment (final-check finding)."""
    caplog.set_level(logging.INFO, logger="gregtec_voice.wake")
    engine, bus, _, _, _ = build(at={40})
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.8)  # the phrase, the detection at its last block (t=100.8)
    feed.blocks(QUIET, 0.2)
    feed.blocks(SPEECH, 0.04)  # the blip
    feed.blocks(QUIET, 0.9)
    assert bus.kinds() == ["wake"]
    feed.blocks(SPEECH, 1.0)  # the question, from t=101.96
    feed.blocks(QUIET, END_SILENCE_S)
    assert bus.kinds() == ["wake", "end"]
    assert bus.events[-1] == ("end", pytest.approx(102.94))
    assert wake_records(caplog)[0]["question_s"] == 1.0


def test_a_blip_alone_in_the_window_is_no_question() -> None:
    """A blip inside the window and nothing after it: the window closes as for silence."""
    engine, bus, _, _, _ = build(at={10})
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.2)
    feed.blocks(QUIET, CANCEL_WINDOW_S - 0.04)
    feed.blocks(
        SPEECH, ONSET_S - 0.02
    )  # one block short of a start, across the window's end
    assert bus.kinds() == ["wake"]
    feed.blocks(QUIET, 0.02)
    assert bus.kinds() == ["wake", "cancel"]


def test_a_question_said_before_the_verdict_ends_once_it_passes() -> None:
    jobs: list[Callable[[], None]] = []
    engine, bus, chime, _, _ = build(at={10}, run=jobs.append)
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.2)
    feed.blocks(SPEECH, 1.0)
    feed.blocks(QUIET, 1.0)  # the question is over; stage 2 still runs
    assert bus.kinds() == ["wake"] and engine.state == "verifying"
    jobs.pop()()
    feed.blocks(QUIET, 0.02)
    assert chime.played == 1 and bus.kinds() == ["wake", "end"]
    assert bus.events[-1][1] == pytest.approx(101.2)


def test_a_second_stage_that_never_answers_times_out() -> None:
    engine, bus, _, _, _ = build(at={10}, run=lambda job: None)
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.2)
    feed.blocks(QUIET, VERIFY_TIMEOUT_S - 0.04)
    assert bus.kinds() == ["wake"]
    feed.blocks(QUIET, 0.04)
    assert bus.kinds() == ["wake", "cancel"]


def test_a_failing_second_stage_drops_the_wake(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="gregtec_voice.wake")
    engine, bus, _, _, _ = build(at={10}, heard=None)
    Feeder(engine).blocks(SPEECH, 0.24)
    assert bus.kinds() == ["wake", "cancel"]
    assert wake_records(caplog)[0]["outcome"] == "check failed"


def test_without_a_second_stage_the_chime_comes_at_once() -> None:
    engine, _, chime, _, seen = build(at={10}, verify=False)
    Feeder(engine).blocks(SPEECH, 0.2)
    assert chime.played == 1 and engine.state == "verified" and not seen


def test_detections_during_a_wake_and_just_after_it_are_ignored() -> None:
    # fires at 0.2 s, again during the wake, and right after it ends (the refractory 0.5 s)
    engine, bus, _, _, _ = build(at={10, 20, 116, 160})
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.2)  # wake at block 10
    feed.blocks(SPEECH, 0.5)  # block 20 fires: ignored (a wake is open)
    feed.blocks(QUIET, END_SILENCE_S)  # ends at block 65
    assert bus.kinds() == ["wake", "end"]
    feed.blocks(QUIET, 1.1)  # block 116, 1.02 s after the end: heard (refractory 0.5 s)
    assert bus.kinds() == ["wake", "end", "wake"]


def test_refractory_drops_a_detection_right_after_a_wake_ends() -> None:
    engine, bus, _, _, _ = build(at={10, 70})
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.2)
    feed.blocks(SPEECH, 0.5)
    feed.blocks(QUIET, END_SILENCE_S)  # ends at block 65
    feed.blocks(QUIET, 0.2)  # block 70 fires 0.1 s after: the phrase's echo, ignored
    assert bus.kinds() == ["wake", "end"]


def test_barge_in_by_voice() -> None:
    """D180: 0.3 s of speech is speech(True) (GOS stops if it speaks); 0.6 s of silence ends it.
    A keyboard click (one block) is no speech."""
    engine, bus, _, _, _ = build(at=set())
    feed = Feeder(engine)
    feed.blocks(SPEECH, 0.02)
    feed.blocks(QUIET, 0.1)
    feed.blocks(SPEECH, 0.28)
    assert bus.events == []
    feed.blocks(SPEECH, 0.02)
    assert bus.events == [("speech", True)]
    feed.blocks(QUIET, END_SILENCE_S)
    assert bus.events == [("speech", True), ("speech", False)]


def test_a_failing_chime_doesnt_drop_the_wake() -> None:
    class Broken:
        def play(self) -> None:
            raise OSError("no output device")

    bus = Bus()
    engine = WakeWord(FakeDetector({10}), FakeVad(), bus, phrase=PHRASE, chime=Broken())
    Feeder(engine).blocks(SPEECH, 0.2)
    assert engine.state == "verified" and bus.kinds() == ["wake"]


@pytest.mark.parametrize(
    ("text", "score"),
    [
        ("Hey, Archivist!", 1.0),
        ("and then hey archivist what does the EBF need", 1.0),
        ("Hey Archivists.", 0.96),
        ("hey artist", 0.76),
        ("archive this", 0.70),
        ("", 0.0),
    ],
)
def test_match_score(text: str, score: float) -> None:
    assert match_score(text, PHRASE) == pytest.approx(score, abs=0.01)


def test_the_second_stage_needs_more_than_a_near_miss() -> None:
    assert not matches("hey artist", PHRASE)
    assert matches("Hey, Archivists", PHRASE)
    assert matches("Hey Archivist, what uses tin?", PHRASE)
    assert not matches("okay the sierra", "okay tessera")
    assert matches("OK, Tessera", "okay tessera")
    # look-alikes whose words merge into the phrase's letters (review finding 3): the run
    # score passes, the longest word heard as one word doesn't
    for said in (
        "Hey, archive this.",
        "Hey, archive it",
        "hey archive is",
        "hey activist",
    ):
        assert word_score(said, PHRASE) < wake.WORD_MATCH, said
        assert not matches(said, PHRASE), said
    assert match_score("Hey, archive it", PHRASE) >= wake.MATCH
    assert not matches("okay tesseract", "okay tessera")  # 0.875 < 0.9
    assert matches("Hello, quarter master.", "hello quartermaster")  # one word, split
    assert not matches("hello quarter mister", "hello quartermaster")


@pytest.mark.parametrize(
    ("said", "question"),
    [
        ("vist, how do I make steel", "how do I make steel"),
        ("Archivist. What uses tin?", "What uses tin?"),
        ("Hey archivist, what uses tin?", "what uses tin?"),
        ("what uses tin?", "what uses tin?"),
        ("a steel ingot", "a steel ingot"),
        ("", ""),
    ],
)
def test_strip_phrase(said: str, question: str) -> None:
    assert strip_phrase(said, PHRASE) == question


def test_keyword_line() -> None:
    line = wake.keyword_line(["▁HE", "Y", "▁AR", "CH"], 0.25, 1.5)
    assert line == "▁HE Y ▁AR CH :1.5 #0.25 @WAKE"


def test_the_chime_is_short_and_quiet() -> None:
    pcm = wake.chime_pcm()
    assert 0.15 < len(pcm) / 32000 < 0.25
    samples = memoryview(pcm).cast("h")
    assert max(abs(s) for s in samples) <= 32767 * 10 ** (-18 / 20) + 1
    assert samples[0] == 0 and abs(samples[-1]) < 200


# --- create, models ------------------------------------------------------------------------


def test_create_without_its_engine_returns_none_and_says_why(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def missing(*args: object, **kwargs: object) -> object:
        raise WakeError("sherpa-onnx isn't installed here")

    monkeypatch.setattr(wake, "detector", missing)
    assert wake.create(VoiceSettings(), Bus()) is None
    record = next(
        r for r in caplog.records if r.getMessage() == "the wake word can't start"
    )
    assert "sherpa-onnx isn't installed" in record.error


def test_create_builds_the_engine_with_the_transcriber_as_second_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wake, "detector", lambda settings: FakeDetector({10}))
    monkeypatch.setattr(wake, "vad", FakeVad)

    @dataclass
    class Heard:
        name: str = "fake"
        segments: list[Any] = field(default_factory=list)

        def transcribe(self, segment: Any) -> str:  # noqa: ANN401
            self.segments.append(segment)
            return "Hey archivist"

    heard, bus = Heard(), Bus()
    engine = wake.create(VoiceSettings(), bus, heard)
    assert isinstance(engine, WakeWord) and isinstance(engine.chime, wake.OutputChime)
    engine.run = lambda job: job()
    engine.chime = Chime()
    Feeder(engine).blocks(SPEECH, 0.22)
    assert engine.state == "verified" and heard.segments[0].trigger == "wake"
    assert wake.create(VoiceSettings(), bus, None).verifier is None  # type: ignore[union-attr]


def test_the_second_stage_hears_without_the_glossary_prompt() -> None:
    @dataclass
    class Plain:
        name: str = "fake"

        def transcribe(self, segment: Any) -> str:  # noqa: ANN401
            raise AssertionError("the prompted path")

        def plain(self, pcm: bytes) -> str:
            return f"heard {len(pcm)}"

    assert wake.verifier(Plain())(b"1234") == "heard 4"


def test_a_missing_library_names_the_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_sherpa(*args: object, **kwargs: object) -> None:
        raise ImportError("No module named 'sherpa_onnx'", name="sherpa_onnx")

    monkeypatch.setattr(wake, "kws_files", lambda models, note: tmp_path)
    monkeypatch.setattr(wake, "SherpaKws", no_sherpa)
    with pytest.raises(WakeError, match="uv sync --group wake"):
        wake.detector(VoiceSettings(gos_home=tmp_path))


def test_openwakeword_needs_its_model_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("onnxruntime")
    pytest.importorskip("numpy")
    monkeypatch.setattr(wake, "oww_features", lambda models, note: tmp_path)
    settings = VoiceSettings(
        gos_home=tmp_path,
        wake_engine="openwakeword",
        wake_model=str(tmp_path / "missing.onnx"),
    )
    with pytest.raises(WakeError, match="doesn't exist"):
        wake.detector(settings)


def test_the_thresholds() -> None:
    assert wake.threshold(VoiceSettings()) == wake.DEFAULT_THRESHOLD["sherpa-onnx"]
    assert wake.threshold(VoiceSettings(wake_threshold=0.4)) == 0.4
    oww = VoiceSettings(wake_engine="openwakeword", wake_model="m.onnx")
    assert wake.threshold(oww) == 0.5


def _kws_archive() -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name in wake.KWS_FILES.values():
            data = name.encode()
            info = tarfile.TarInfo(f"{wake.KWS_MODEL}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return bz2.compress(raw.getvalue())


def test_the_kws_model_is_fetched_once_and_unpacked(tmp_path: Path) -> None:
    archive = _kws_archive()
    calls: list[str] = []

    def fetch(url: str, sha: str, dest: Path) -> None:
        calls.append(url)
        assert (url, sha) == (wake.KWS_URL, wake.KWS_SHA256)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive)

    notes: list[str] = []
    folder = wake.kws_files(tmp_path, notes.append, fetch)
    assert folder == tmp_path / "kws" / wake.KWS_MODEL
    assert (folder / wake.KWS_FILES["encoder"]).read_text() == wake.KWS_FILES["encoder"]
    assert not (tmp_path / "kws" / f"{wake.KWS_MODEL}.tar.bz2").exists()
    assert wake.kws_files(tmp_path, notes.append, fetch) == folder
    assert len(calls) == 1 and len(notes) == 1


def test_fetch_refuses_a_file_whose_hash_differs(tmp_path: Path) -> None:
    source = tmp_path / "model.bin"
    source.write_bytes(b"weights")
    url = source.resolve().as_uri()
    with pytest.raises(WakeError, match="SHA-256"):
        wake.fetch(url, "0" * 64, tmp_path / "out" / "model.bin")
    assert not list((tmp_path / "out").iterdir())
    wake.fetch(url, hashlib.sha256(b"weights").hexdigest(), tmp_path / "out" / "m.bin")
    assert (tmp_path / "out" / "m.bin").read_bytes() == b"weights"


def test_openwakewords_feature_models_are_fetched_once(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetch(url: str, sha: str, dest: Path) -> None:
        calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    folder = wake.oww_features(tmp_path, lambda m: None, fetch)
    wake.oww_features(tmp_path, lambda m: None, fetch)
    assert sorted(p.name for p in folder.iterdir()) == sorted(wake.OWW_FEATURES)
    assert len(calls) == 2
