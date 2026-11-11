"""Where speech is played: the output device (WO-5.3, D56, D205).

- **Windows** (:class:`WasapiOutput`): PortAudio's WASAPI host API in shared mode through
  ``sounddevice``, mono int16 at the engine's own rate with WASAPI's converter on
  (``auto_convert``), so Windows resamples to the headset's format. The device is picked by
  name: ``[voice] output_endpoint`` is a part of a device's name ("Chat": the Arctis Nova
  Elite's Chat endpoint, D56, so the ChatMix dial balances GOS against the game [D§2.6]); ""
  is Windows' default output device. Of several matches, one named exactly so wins. A name
  that matches no device, or several and none exactly, falls back to
  the default device with a warning (plan 0.1 principle 9: GOS still talks), and
  ``gos voice devices`` shows which device is used and why.
- **Linux** (tests, the build container, the bench): :class:`FakeOutput` takes the audio and
  keeps it (its writes and their times) instead of playing it; :class:`WavOutput` also writes
  each utterance to a WAV file for listening.

A stream is written in :data:`BLOCK_S` blocks by one thread, which checks between blocks
whether to stop: barge-in stops speech within one block plus the device's own buffer, and the
stream is aborted (what is buffered is dropped), never drained.
"""

from __future__ import annotations

import logging
import sys
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from gregtec_voice.audio import AudioError, Device, _sounddevice, _wasapi

log = logging.getLogger(__name__)

SAMPLE_BYTES = 2  # int16 mono
BLOCK_S = 0.04
DEFAULT_ENDPOINT = "Chat"  # D56: the Arctis Nova Elite's Chat endpoint (Appendix C)


class Stream(Protocol):
    def write(self, pcm: bytes) -> None:
        """Play ``pcm`` (int16 mono at the stream's rate); returns once it is queued."""
        ...

    def close(self, abort: bool = False) -> None:
        """Let the rest play out, or (``abort``) drop it now."""
        ...


class Output(Protocol):
    name: str

    def open(self, rate: int) -> Stream: ...


# --- Windows: WASAPI --------------------------------------------------------------------------


def playback_devices() -> list[Device]:
    """The WASAPI output devices (Windows), the default one marked."""
    sd = _sounddevice()
    index, api = _wasapi(sd)
    return [
        Device(i, str(d["name"]), i == api["default_output_device"])
        for i, d in enumerate(sd.query_devices())
        if d["hostapi"] == index and d["max_output_channels"] > 0
    ]


@dataclass(frozen=True)
class Pick:
    device: Device
    why: str  # "" when the configured name picked it, else why the default is used


def pick_device(name: str, devices: Sequence[Device]) -> Pick:
    """The device ``[voice] output_endpoint`` picks: the one whose name contains ``name``
    (any case); of several, the one whose whole name is ``name``; else the default device and
    why (D205). Raises :class:`AudioError` when there is no output device at all."""
    if not devices:
        raise AudioError("Windows lists no output device (is the headset connected?)")
    default = next((d for d in devices if d.default), devices[0])
    if not name:
        return Pick(default, "")
    hits = [d for d in devices if name.casefold() in d.name.casefold()]
    if len(hits) == 1:
        return Pick(hits[0], "")
    exact = [d for d in hits if d.name.casefold() == name.casefold()]
    if len(exact) == 1:  # SteelSeries Sonar can add a second "...Chat" device
        return Pick(exact[0], "")
    if not hits:
        why = f"no output device's name contains {name!r}"
    else:
        why = f"{name!r} is in {len(hits)} device names ({'; '.join(d.name for d in hits)})"
    return Pick(default, f"{why}: using Windows' default output device")


class _WasapiStream:
    def __init__(self, stream: Any) -> None:  # noqa: ANN401 - a sounddevice.RawOutputStream
        self.stream = stream

    def write(self, pcm: bytes) -> None:
        self.stream.write(pcm)

    def close(self, abort: bool = False) -> None:
        try:
            if abort:
                self.stream.abort()
            else:
                self.stream.stop()  # plays out what is buffered
        finally:
            self.stream.close()


class WasapiOutput:
    """Windows: the output device ``endpoint`` names (see :func:`pick_device`), looked up
    again for each utterance in PortAudio's device list. That list is made when PortAudio
    starts and is refreshed only when capture restarts (``audio._rescan``), so a headset that
    comes back is found once capture has restarted (it does when the same headset's microphone
    went away) or the client restarts; until then speech plays on the default device or, with
    none, fails and is logged. A write to a stream that capture's restart closed fails the
    utterance (logged), never the client."""

    def __init__(self, endpoint: str = DEFAULT_ENDPOINT) -> None:
        self.endpoint = endpoint
        self.name = endpoint or "default"
        self._warned = ""

    def open(self, rate: int) -> Stream:
        sd = _sounddevice()
        pick = pick_device(self.endpoint, playback_devices())
        if pick.why and pick.why != self._warned:
            self._warned = pick.why
            log.warning("speech output", extra={"problem": pick.why})
        try:
            stream = sd.RawOutputStream(
                samplerate=rate,
                device=pick.device.index,
                channels=1,
                dtype="int16",
                latency="low",
                extra_settings=sd.WasapiSettings(auto_convert=True),
            )
            stream.start()
        except Exception as exc:  # noqa: BLE001 - PortAudioError: say which device
            raise AudioError(f"can't play on {pick.device.name}: {exc}") from None
        return _WasapiStream(stream)


# --- Linux and tests --------------------------------------------------------------------------


@dataclass
class Played:
    """One stream's audio as :class:`FakeOutput` kept it."""

    rate: int
    pcm: bytearray = field(default_factory=bytearray)
    writes: list[float] = field(default_factory=list)  # clock() at each write
    aborted: bool = False
    closed: bool = False

    @property
    def seconds(self) -> float:
        return len(self.pcm) / (SAMPLE_BYTES * self.rate)


class _FakeStream:
    def __init__(self, owner: FakeOutput, played: Played) -> None:
        self.owner = owner
        self.played = played

    def write(self, pcm: bytes) -> None:
        self.played.writes.append(self.owner.clock())
        self.played.pcm += pcm
        if self.owner.realtime:
            self.owner.sleep(len(pcm) / (SAMPLE_BYTES * self.played.rate))

    def close(self, abort: bool = False) -> None:
        self.played.aborted = abort
        self.played.closed = True
        self.owner.closed(self.played)


class FakeOutput:
    """No sound device (Linux, tests, ``gos voice tts-bench``): keeps what would be played.
    ``realtime`` makes each write take as long as its audio, as a device's would."""

    def __init__(
        self,
        *,
        realtime: bool = False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        name: str = "fake (no sound device)",
    ) -> None:
        self.realtime = realtime
        self.clock = clock
        self.sleep = sleep
        self.name = name
        self.streams: list[Played] = []

    def open(self, rate: int) -> Stream:
        played = Played(rate)
        self.streams.append(played)
        return _FakeStream(self, played)

    def closed(self, played: Played) -> None:
        """A stream was closed (:class:`WavOutput` writes it out)."""


class WavOutput(FakeOutput):
    """:class:`FakeOutput` that also writes each stream to ``folder/NNN.wav``, to listen to."""

    def __init__(self, folder: Path, **kwargs: Any) -> None:  # noqa: ANN401 - FakeOutput's
        super().__init__(**kwargs)
        self.folder = folder

    def closed(self, played: Played) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{len(self.streams):03d}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(SAMPLE_BYTES)
            w.setframerate(played.rate)
            w.writeframes(bytes(played.pcm))


def default_output(endpoint: str) -> Output:
    """Windows: the WASAPI device ``endpoint`` names; elsewhere a :class:`FakeOutput`."""
    if sys.platform == "win32":
        return WasapiOutput(endpoint)
    return FakeOutput(realtime=True)
