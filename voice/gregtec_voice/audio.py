"""Microphone capture, the pre-roll and talk segments (plan 8 WO-5.1, D181).

Audio is 16 kHz mono 16-bit PCM (little-endian bytes) in 20 ms blocks, everywhere in the client.

- **Capture** (:class:`WasapiSource`, Windows): PortAudio's WASAPI host API in shared mode
  through ``sounddevice``, opened at 16 kHz mono with WASAPI's own converter on
  (``auto_convert``), so Windows resamples and down-mixes whatever the headset delivers. On Linux
  (tests, dev) :class:`WavSource` and :class:`FakeSource` stand in: no audio device is needed.
- **The capture callback only queues** (:class:`AudioPipe`): one worker thread takes each block,
  feeds the :class:`Recorder` and then the sinks (the wake engine, WO-5.4). A full queue
  (5 s behind) drops blocks and counts them; the capture thread never waits.
- **Pre-roll** (:class:`Recorder`): the last 300 ms are always kept, so a segment starts that long
  before the talk key went down and the first syllable isn't clipped.
- **Post-roll**: a segment keeps the blocks captured up to 120 ms after the key came up, the
  audio still on its way through WASAPI and the queue when the key event arrives. A segment that
  sees no audio for 0.5 s past that point is closed anyway (a device that stopped). A new segment
  that starts meanwhile (the key pressed again at once) doesn't cut it short: both take the
  blocks until the older one's post-roll is over.
- **Length cap**: 30 s (Whisper's window), then the segment closes as if the key came up.
- **Re-opening** (a headset that went away): PortAudio lists devices once, when it starts, so
  :class:`WasapiSource` starts it again before it re-opens, and a headset that came back is found.
"""

from __future__ import annotations

import array
import io
import logging
import math
import queue
import sys
import threading
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
SAMPLE_BYTES = 2  # int16, mono
BYTES_PER_S = SAMPLE_RATE * SAMPLE_BYTES
BLOCK_FRAMES = 320  # 20 ms
BLOCK_S = BLOCK_FRAMES / SAMPLE_RATE
PRE_ROLL_S = 0.3  # plan 8 WO-5.1
POST_ROLL_S = 0.12  # D181: audio in flight when the key comes up
STALL_S = 0.5  # no block this long past the post-roll: close the segment anyway
MAX_SEGMENT_S = 30.0
QUEUE_BLOCKS = 250  # 5 s between the capture callback and the worker
# WASAPI shared mode for capture and playback alike: never exclusive, so Discord and every
# other app keep the headset (WO-5.5, D247).
WASAPI_EXCLUSIVE = False


@dataclass(frozen=True)
class Block:
    pcm: bytes
    t_end: float  # time.monotonic() when its last sample was captured (as delivered)


@dataclass(frozen=True)
class Segment:
    """One recorded utterance: the pre-roll, then everything to the post-roll's end."""

    pcm: bytes
    trigger: str  # "key" or "wake"
    started: float  # monotonic: key down (or wake)
    stopped: (
        float | None
    )  # key up (or end of speech); None when the length cap closed it
    closed: float  # monotonic: when the recorder closed it
    pre_roll_s: float  # how much audio before ``started`` it holds

    @property
    def seconds(self) -> float:
        return len(self.pcm) / BYTES_PER_S

    @property
    def capped(self) -> bool:
        return self.stopped is None

    def samples(self) -> array.array[int]:
        found = array.array("h", self.pcm)
        if sys.byteorder == "big":
            found.byteswap()
        return found

    def peak_dbfs(self) -> float:
        """The loudest sample relative to full scale (-inf for silence)."""
        peak = max((abs(s) for s in self.samples()), default=0)
        return 20 * math.log10(peak / 32768) if peak else -math.inf

    def wav(self) -> bytes:
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(SAMPLE_BYTES)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(self.pcm)
        return out.getvalue()


class Ring:
    """The last ``seconds`` of audio (the pre-roll)."""

    def __init__(self, seconds: float) -> None:
        self.capacity = round(seconds * SAMPLE_RATE) * SAMPLE_BYTES
        self._buf = bytearray()

    def push(self, pcm: bytes) -> None:
        self._buf += pcm
        if len(self._buf) > self.capacity:
            del self._buf[: len(self._buf) - self.capacity]

    def snapshot(self) -> bytes:
        return bytes(self._buf)


@dataclass
class _Open:
    trigger: str
    started: float
    pre_roll_s: float
    buf: bytearray
    stop_at: float | None = None  # keep blocks captured until then (key up + post-roll)
    stopped: float | None = None


class Recorder:
    """Keeps the pre-roll and the open segment. :meth:`feed` runs on the audio worker; the
    client calls :meth:`start`, :meth:`stop` and :meth:`cancel`. A closed segment goes to
    ``on_closed`` on whichever thread closed it. A stopped segment still taking its post-roll
    when a new one starts goes on taking it alongside the new one."""

    def __init__(
        self,
        on_closed: Callable[[Segment], None],
        *,
        pre_roll_s: float = PRE_ROLL_S,
        post_roll_s: float = POST_ROLL_S,
        max_s: float = MAX_SEGMENT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.on_closed = on_closed
        self.post_roll_s = post_roll_s
        self.max_bytes = round(max_s * SAMPLE_RATE) * SAMPLE_BYTES
        self.clock = clock
        self._lock = threading.Lock()
        self._ring = Ring(pre_roll_s)
        self._open: _Open | None = None
        self._closing: list[_Open] = []  # stopped, then replaced as the open one

    @property
    def recording(self) -> str | None:
        """The open segment's trigger, or None."""
        seg = self._open
        return None if seg is None else seg.trigger

    @property
    def started(self) -> float | None:
        """The open segment's start (key down or wake), or None."""
        seg = self._open
        return None if seg is None else seg.started

    def start(self, trigger: str, t: float) -> bool:
        """Open a segment with the pre-roll; False while one is being recorded. One that is
        stopped and still taking its post-roll goes on taking it alongside."""
        with self._lock:
            if self._open is not None:
                if self._open.stop_at is None:
                    return False
                self._closing.append(self._open)
            pre = self._ring.snapshot()
            self._open = _Open(trigger, t, len(pre) / BYTES_PER_S, bytearray(pre))
            return True

    def stop(self, t: float) -> None:
        """The key came up (or speech ended) at ``t``: close after the post-roll."""
        with self._lock:
            seg = self._open
            if seg is not None and seg.stop_at is None:
                seg.stopped, seg.stop_at = t, t + self.post_roll_s

    def cancel(self) -> bool:
        """Drop the open segment; False when none was open."""
        with self._lock:
            dropped, self._open = self._open, None
            return dropped is not None

    def _segments(self) -> list[_Open]:
        """Every segment taking blocks, oldest first (lock held)."""
        return self._closing + ([self._open] if self._open is not None else [])

    def feed(self, block: Block) -> None:
        closed = []
        with self._lock:
            self._ring.push(block.pcm)
            for seg in self._segments():
                seg.buf += block.pcm
                past_cut = seg.stop_at is not None and block.t_end >= seg.stop_at
                too_long = len(seg.buf) >= self.max_bytes + round(
                    seg.pre_roll_s * BYTES_PER_S
                )
                if past_cut or too_long:
                    closed.append(self._close(seg))
        for segment in closed:
            self.on_closed(segment)

    def tick(self, now: float) -> None:
        """Close a stopped segment whose audio stopped arriving (a device that went away)."""
        closed = []
        with self._lock:
            for seg in self._segments():
                if seg.stop_at is not None and now > seg.stop_at + STALL_S:
                    closed.append(self._close(seg))
        for segment in closed:
            self.on_closed(segment)

    def _close(self, seg: _Open) -> Segment:
        if seg is self._open:
            self._open = None
        else:
            self._closing = [s for s in self._closing if s is not seg]
        return Segment(
            bytes(seg.buf),
            seg.trigger,
            seg.started,
            seg.stopped,
            self.clock(),
            seg.pre_roll_s,
        )


class AudioSink(Protocol):
    """Gets every captured block on the audio worker thread (the wake engine, WO-5.4)."""

    def feed(self, block: Block) -> None: ...


class Source(Protocol):
    """A microphone: calls ``on_block`` with each 20 ms block until :meth:`stop`."""

    name: str

    def start(self, on_block: Callable[[Block], None]) -> None: ...

    def stop(self) -> None: ...


class AudioError(RuntimeError):
    """The microphone can't be opened (the message says which and why)."""


class AudioPipe:
    """Capture -> queue -> one worker: the recorder first, then each sink."""

    def __init__(
        self,
        source: Source,
        recorder: Recorder,
        sinks: Sequence[AudioSink] = (),
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = source
        self.recorder = recorder
        self.sinks = list(sinks)
        self.clock = clock
        self.dropped = 0  # blocks lost to a full queue
        self.blocks = 0
        self.last_block: float | None = (
            None  # monotonic, when the worker took the last one
        )
        self._queue: queue.Queue[Block | None] = queue.Queue(QUEUE_BLOCKS)
        self._thread: threading.Thread | None = None
        self._failed_sinks: set[int] = set()

    def _on_block(self, block: Block) -> None:  # the capture thread: queue only
        try:
            self._queue.put_nowait(block)
        except queue.Full:
            self.dropped += 1

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._work, name="gos-voice-audio", daemon=True
        )
        self._thread.start()
        try:
            self.source.start(self._on_block)
        except BaseException:
            self._finish()
            raise

    def restart_source(self) -> None:
        """Re-open the microphone (it stopped delivering audio, e.g. the headset went away)."""
        self.source.stop()
        self.source.start(self._on_block)

    def stop(self) -> None:
        self.source.stop()
        self._finish()

    def _finish(self) -> None:
        if self._thread is None:
            return
        try:
            self._queue.put(None, timeout=2.0)
        except queue.Full:
            pass
        self._thread.join(timeout=5.0)
        self._thread = None

    def _work(self) -> None:
        while (block := self._queue.get()) is not None:
            self.blocks += 1
            self.last_block = self.clock()
            try:
                self.recorder.feed(block)
            except Exception:  # the worker must keep running
                log.exception("the recorder failed on a block")
            for i, sink in enumerate(self.sinks):
                try:
                    sink.feed(block)
                except Exception:
                    if (
                        i not in self._failed_sinks
                    ):  # once per sink, not 50 times a second
                        self._failed_sinks.add(i)
                        log.exception(
                            "an audio sink failed", extra={"sink": type(sink).__name__}
                        )


# --- sources ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    default: bool


def _sounddevice() -> Any:  # noqa: ANN401 - the sounddevice module (Windows only)
    if sys.platform != "win32":
        raise AudioError("WASAPI capture runs on Windows only")
    try:
        import sounddevice  # Windows only (D181); starts PortAudio when imported
    except Exception as exc:  # noqa: BLE001 - OSError (no PortAudio), PortAudioError (no start)
        raise AudioError(f"PortAudio didn't start: {exc}") from None
    return sounddevice


def _rescan(sd: Any) -> None:  # noqa: ANN401 - sounddevice
    """Start PortAudio again, so its device list is current (it is made once, when PortAudio
    starts: a headset that went away and came back isn't in it). No stream may be open."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception as exc:  # noqa: BLE001 - PortAudioError, or a sounddevice without them
        raise AudioError(f"PortAudio didn't restart: {exc}") from None


def _wasapi(sd: Any) -> tuple[int, dict[str, Any]]:  # noqa: ANN401 - sounddevice
    for index, api in enumerate(sd.query_hostapis()):
        if api["name"] == "Windows WASAPI":
            return index, api
    raise AudioError("PortAudio has no WASAPI host API on this PC")


def capture_devices() -> list[Device]:
    """The WASAPI capture devices (Windows), the default one marked."""
    sd = _sounddevice()
    index, api = _wasapi(sd)
    return [
        Device(i, str(d["name"]), i == api["default_input_device"])
        for i, d in enumerate(sd.query_devices())
        if d["hostapi"] == index and d["max_input_channels"] > 0
    ]


def find_device(name: str, devices: Sequence[Device]) -> Device:
    """The device ``name`` picks: "" the default, else the one whose name contains it."""
    if not devices:
        raise AudioError("Windows lists no capture device (is the headset connected?)")
    if not name:
        default = [d for d in devices if d.default]
        if not default:
            raise AudioError(
                "Windows has no default capture device; set [voice] input_device"
            )
        return default[0]
    hits = [d for d in devices if name.casefold() in d.name.casefold()]
    if len(hits) == 1:
        return hits[0]
    names = "; ".join(d.name for d in devices)
    if not hits:
        raise AudioError(f"no capture device matches {name!r} (there are: {names})")
    raise AudioError(
        f"{name!r} matches {len(hits)} capture devices (there are: {names})"
    )


class WasapiSource:
    """Windows: shared-mode WASAPI capture at 16 kHz mono int16, converted by Windows."""

    def __init__(self, device: str = "") -> None:
        self.device = device
        self.name = device or "default"
        self.overflows = 0
        self._stream: Any = None
        self._starts = 0

    def start(self, on_block: Callable[[Block], None]) -> None:
        sd = _sounddevice()
        self._starts += 1
        if (
            self._starts > 1
        ):  # re-opening (the stream is closed): list the devices afresh
            _rescan(sd)
        found = find_device(self.device, capture_devices())
        self.name = found.name

        def callback(
            indata: bytes, frames: int, time_info: object, status: object
        ) -> None:
            if status:
                self.overflows += 1
            on_block(Block(bytes(indata), time.monotonic()))

        try:
            self._stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_FRAMES,
                device=found.index,
                channels=1,
                dtype="int16",
                latency="low",
                extra_settings=sd.WasapiSettings(
                    exclusive=WASAPI_EXCLUSIVE, auto_convert=True
                ),
                callback=callback,
            )
            self._stream.start()
        except sd.PortAudioError as exc:
            self._stream = None
            raise AudioError(
                f"can't open {found.name!r} through WASAPI: {exc}"
            ) from None

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()


class WavSource:
    """A 16 kHz mono 16-bit WAV as a microphone (tests, and Linux where there is no WASAPI).

    ``realtime``: one block every 20 ms, as a microphone, and silence after the file (a
    microphone in a quiet room); else as fast as the worker takes them, and nothing after it.
    """

    def __init__(self, path: Path, *, realtime: bool = True) -> None:
        with wave.open(str(path), "rb") as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (
                SAMPLE_RATE,
                1,
                SAMPLE_BYTES,
            ):
                raise AudioError(f"{path} isn't 16 kHz mono 16-bit PCM")
            self.pcm = w.readframes(w.getnframes())
        self.name = path.name
        self.realtime = realtime
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, on_block: Callable[[Block], None]) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(on_block,), name="gos-voice-wav", daemon=True
        )
        self._thread.start()

    def _run(self, on_block: Callable[[Block], None]) -> None:
        size = BLOCK_FRAMES * SAMPLE_BYTES
        silence = bytes(size)
        pos = 0
        due = time.monotonic()
        while not self._stop.is_set():
            if pos < len(self.pcm):
                chunk = self.pcm[pos : pos + size].ljust(size, b"\0")
                pos += size
            elif not self.realtime:
                return  # nothing more to give
            else:
                chunk = silence
            if self.realtime:
                due += BLOCK_S
                self._stop.wait(max(0.0, due - time.monotonic()))
            on_block(Block(chunk, time.monotonic()))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


@dataclass
class FakeSource:
    """Tests: :meth:`push` delivers blocks as the capture thread would."""

    name: str = "fake"
    started: int = 0
    _on_block: Callable[[Block], None] | None = field(default=None, repr=False)

    def start(self, on_block: Callable[[Block], None]) -> None:
        self.started += 1
        self._on_block = on_block

    def stop(self) -> None:
        self._on_block = None

    def push(self, pcm: bytes, t_end: float | None = None) -> None:
        if self._on_block is not None:
            self._on_block(Block(pcm, time.monotonic() if t_end is None else t_end))


def tone(seconds: float, *, amplitude: int = 8000, hz: float = 440.0) -> bytes:
    """A sine as PCM (tests and the WAV fixtures)."""
    n = round(seconds * SAMPLE_RATE)
    samples = array.array(
        "h",
        (
            round(amplitude * math.sin(2 * math.pi * hz * i / SAMPLE_RATE))
            for i in range(n)
        ),
    )
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()
