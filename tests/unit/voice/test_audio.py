"""WO-5.1, D181: 16 kHz mono capture, the 300 ms pre-roll, the post-roll and talk segments.

Linux runs everything against fake and WAV sources; the WASAPI source is opened only on
Windows, where CI has no audio device, so there it is checked as far as listing devices.
"""

from __future__ import annotations

import io
import logging
import math
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

from gregtec_voice import audio
from gregtec_voice.audio import (
    BLOCK_FRAMES,
    BYTES_PER_S,
    AudioError,
    AudioPipe,
    Block,
    Device,
    FakeSource,
    Recorder,
    Ring,
    Segment,
    WavSource,
)

BLOCK = BLOCK_FRAMES * 2  # bytes in 20 ms


def blocks(seconds: float, value: int = 1) -> list[bytes]:
    """20 ms blocks of a constant sample, so a segment's content shows where it came from."""
    sample = value.to_bytes(2, "little", signed=True)
    return [sample * BLOCK_FRAMES for _ in range(round(seconds / 0.02))]


def feed(rec: Recorder, chunks: list[bytes], t0: float) -> float:
    """Feed blocks 20 ms apart from ``t0``; returns the last block's end time."""
    t = t0
    for chunk in chunks:
        t += 0.02
        rec.feed(Block(chunk, t))
    return t


def test_ring_keeps_the_last_300_ms() -> None:
    ring = Ring(0.3)
    assert ring.capacity == 9600  # 4800 samples
    for chunk in blocks(0.5, 1) + blocks(0.2, 2):
        ring.push(chunk)
    kept = ring.snapshot()
    assert len(kept) == 9600
    assert kept[: BLOCK * 5] == b"".join(blocks(0.1, 1))  # 100 ms of the first sound
    assert kept[BLOCK * 5 :] == b"".join(blocks(0.2, 2))


def test_a_segment_starts_with_the_pre_roll_and_keeps_the_post_roll() -> None:
    closed: list[Segment] = []
    rec = Recorder(closed.append)
    t = feed(rec, blocks(1.0, 1), 0.0)  # room noise before the key
    assert rec.start("key", t)
    assert rec.recording == "key"
    t = feed(rec, blocks(1.0, 2), t)  # speech while the key is down
    rec.stop(t)  # the key comes up
    t = feed(rec, blocks(0.1, 3), t)  # still in flight: kept (post-roll 120 ms)
    assert not closed
    feed(rec, blocks(0.04, 4), t)  # the block that passes key up + 120 ms closes it
    assert len(closed) == 1 and rec.recording is None
    seg = closed[0]
    assert seg.trigger == "key" and seg.pre_roll_s == pytest.approx(0.3)
    assert seg.pcm[:9600] == b"".join(blocks(0.3, 1))  # 300 ms before the key went down
    assert seg.pcm[9600 : 9600 + BYTES_PER_S] == b"".join(blocks(1.0, 2))
    assert seg.seconds == pytest.approx(0.3 + 1.0 + 0.1 + 0.02)
    assert not seg.capped and seg.stopped == pytest.approx(2.0)


def test_cancel_drops_the_segment_and_start_needs_none_open() -> None:
    closed: list[Segment] = []
    rec = Recorder(closed.append)
    assert rec.start("key", 0.0)
    assert not rec.start("wake", 0.1)  # one segment at a time
    assert rec.cancel() and not rec.cancel()
    rec.stop(1.0)  # nothing open: nothing to stop
    feed(rec, blocks(0.5), 1.0)
    assert closed == []


def test_a_stopped_segment_closes_when_audio_stops_arriving() -> None:
    closed: list[Segment] = []
    rec = Recorder(closed.append)
    rec.start("key", 0.0)
    feed(rec, blocks(0.2), 0.0)
    rec.stop(0.2)
    rec.tick(0.2 + 0.12 + 0.4)  # within the stall allowance
    assert not closed
    rec.tick(0.2 + 0.12 + 0.6)
    assert len(closed) == 1


def test_the_length_cap_closes_a_segment_as_if_the_key_came_up() -> None:
    closed: list[Segment] = []
    rec = Recorder(closed.append, max_s=1.0)
    feed(rec, blocks(0.3), 0.0)
    rec.start("key", 0.3)
    feed(rec, blocks(1.5), 0.3)
    assert len(closed) == 1
    seg = closed[0]
    assert seg.capped and seg.stopped is None
    assert seg.seconds == pytest.approx(1.3)  # 300 ms pre-roll + the 1 s cap


def test_segment_wav_and_level() -> None:
    seg = Segment(audio.tone(0.5, amplitude=16384), "key", 0.0, 0.5, 0.6, 0.3)
    with wave.open(io.BytesIO(seg.wav()), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert w.getnframes() == 8000
    assert seg.peak_dbfs() == pytest.approx(-6.0, abs=0.1)
    assert math.isinf(Segment(bytes(320), "key", 0, 0, 0, 0).peak_dbfs())


# --- the pipe -------------------------------------------------------------------------------


class Sink:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.got: list[Block] = []

    def feed(self, block: Block) -> None:
        self.got.append(block)
        if self.fail:
            raise RuntimeError("engine broke")


def wait_for(cond: object, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():  # type: ignore[operator]
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_the_pipe_feeds_the_recorder_then_each_sink(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rec = Recorder(lambda seg: None)
    good, bad = Sink(), Sink(fail=True)
    source = FakeSource()
    pipe = AudioPipe(source, rec, [bad, good])
    pipe.start()
    try:
        for chunk in blocks(0.2):
            source.push(chunk)
        wait_for(lambda: pipe.blocks == 10 and len(good.got) == 10)
        assert pipe.last_block is not None
        assert len(rec._ring.snapshot()) == 10 * BLOCK
        # a failing sink is logged once and never stops the others
        assert [r.message for r in caplog.records].count("an audio sink failed") == 1
    finally:
        pipe.stop()
    assert source._on_block is None


def test_a_full_queue_drops_blocks_instead_of_blocking_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audio, "QUEUE_BLOCKS", 3)
    release = threading.Event()

    class Slow(Sink):
        def feed(self, block: Block) -> None:
            release.wait(5)

    source = FakeSource()
    pipe = AudioPipe(source, Recorder(lambda seg: None), [Slow()])
    pipe.start()
    try:
        started = time.monotonic()
        for chunk in blocks(0.4):  # 20 blocks into a queue of 3 behind a stuck sink
            source.push(chunk)
        assert time.monotonic() - started < 1.0  # the capture thread never waited
        assert pipe.dropped >= 15
    finally:
        release.set()
        pipe.stop()


def test_restart_source_reopens_the_microphone() -> None:
    source = FakeSource()
    pipe = AudioPipe(source, Recorder(lambda seg: None))
    pipe.start()
    try:
        pipe.restart_source()
        assert source.started == 2
    finally:
        pipe.stop()


def write_wav(path: Path, pcm: bytes, rate: int = 16000, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return path


def test_a_wav_file_stands_in_for_the_microphone(tmp_path: Path) -> None:
    pcm = audio.tone(0.5)
    got: list[Block] = []
    source = WavSource(write_wav(tmp_path / "say.wav", pcm), realtime=False)
    source.start(got.append)
    wait_for(lambda: len(got) == 25)  # 0.5 s, then the file is done
    source.stop()
    assert b"".join(b.pcm for b in got) == pcm
    assert all(len(b.pcm) == BLOCK for b in got)


def test_a_realtime_wav_paces_blocks_and_then_gives_silence(tmp_path: Path) -> None:
    got: list[Block] = []
    source = WavSource(write_wav(tmp_path / "say.wav", audio.tone(0.1)), realtime=True)
    source.start(got.append)
    time.sleep(0.4)
    source.stop()
    assert 10 <= len(got) <= 30  # about 20 blocks in 0.4 s
    assert got[-1].pcm == bytes(BLOCK)  # silence after the file


def test_a_wav_in_another_format_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="16 kHz mono"):
        WavSource(write_wav(tmp_path / "cd.wav", bytes(4410), rate=44100))
    with pytest.raises(AudioError, match="16 kHz mono"):
        WavSource(write_wav(tmp_path / "st.wav", bytes(640), channels=2))


# --- devices --------------------------------------------------------------------------------

DEVICES = [
    Device(3, "Microphone (Arctis Nova Elite Chat)", default=False),
    Device(4, "Microphone (Realtek Audio)", default=True),
    Device(7, "Line In (Realtek Audio)", default=False),
]


def test_find_device_picks_the_default_or_by_name() -> None:
    assert audio.find_device("", DEVICES).index == 4
    assert audio.find_device("arctis", DEVICES).index == 3
    with pytest.raises(AudioError, match="matches 2 capture devices"):
        audio.find_device("realtek", DEVICES)
    with pytest.raises(AudioError, match="no capture device matches 'yeti'"):
        audio.find_device("yeti", DEVICES)
    with pytest.raises(AudioError, match="no default capture device"):
        audio.find_device("", [d for d in DEVICES if not d.default])
    with pytest.raises(AudioError, match="lists no capture device"):
        audio.find_device("", [])


@pytest.mark.skipif(sys.platform == "win32", reason="Linux: there is no WASAPI")
def test_wasapi_is_windows_only() -> None:
    with pytest.raises(AudioError, match="Windows only"):
        audio.capture_devices()
    with pytest.raises(AudioError, match="Windows only"):
        audio.WasapiSource().start(lambda block: None)


@pytest.mark.skipif(sys.platform != "win32", reason="WASAPI is Windows only")
def test_wasapi_devices_list_on_windows(caplog: pytest.LogCaptureFixture) -> None:
    """CI's Windows runner has no audio device; listing must still work (or say why)."""
    caplog.set_level(logging.INFO)
    try:
        devices = audio.capture_devices()
    except AudioError as exc:  # no audio service on the runner
        assert "WASAPI" in str(exc) or "PortAudio" in str(exc)
        return
    assert all(isinstance(d, Device) for d in devices)
    assert sum(d.default for d in devices) <= 1
