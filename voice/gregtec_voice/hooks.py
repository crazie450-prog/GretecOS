"""Where the speech engines plug into the voice client (D179): STT (WO-5.2), TTS (WO-5.3) and the
wake word (WO-5.4).

Each engine is a module of this package with a ``create`` function, found when the client
starts; until its WO lands the placeholder here runs instead:

| module                  | ``create(...)`` returns                 | placeholder            |
|-------------------------|-----------------------------------------|------------------------|
| ``gregtec_voice.stt``   | ``create(settings) -> Transcriber``     | :class:`NoTranscriber` |
| ``gregtec_voice.tts``   | ``create(settings) -> Speaker``         | :class:`LogSpeaker`    |
| ``gregtec_voice.wake``  | ``create(settings, bus) -> WakeEngine`` | none: talk key only    |

**The wake engine** gets every 16 kHz block (``feed``, on the audio worker thread) and drives
the client through the :class:`TriggerBus`: ``wake(t)`` opens a segment (the 300 ms pre-roll
included, as for the key), ``end_of_speech(t)`` closes it, ``cancel()`` drops it (the 1.5 s cancel
window, a failed second-stage check), and ``speech(True, t)`` while GOS speaks is barge-in by
voice: speech stops. Plan 4.5's "speaking stops speech" exists only once WO-5.4's VAD calls
``speech(True, t)`` (D180): that WO owns it. While a segment is being recorded, the other
trigger's start and end are ignored.

**The transcriber** runs on a worker thread, one segment at a time (the client holds a lock
around it); "" means nothing was said.
**The speaker** returns at once and reports through its two callbacks, from any thread:
``on_first_audio`` when sound starts (PM14's end point) and ``on_done`` when it has finished or
was stopped; :meth:`Speaker.stop` is barge-in. Text is what to say; the speech filter and the
pronunciation lexicon (plan 7.15) are the speaker's own business (WO-5.3).
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from collections.abc import Callable
from typing import Any, Protocol

from gregtec_voice.audio import Block, Segment
from gregtec_voice.settings import VoiceSettings

log = logging.getLogger(__name__)


class Transcriber(Protocol):
    name: str

    def transcribe(self, segment: Segment) -> str: ...


class Speaker(Protocol):
    name: str

    def speak(
        self,
        text: str,
        on_first_audio: Callable[[], None],
        on_done: Callable[[], None],
    ) -> None: ...

    def stop(self) -> None: ...


class TriggerBus(Protocol):
    def wake(self, t: float) -> None: ...

    def end_of_speech(self, t: float) -> None: ...

    def cancel(self) -> None: ...

    def speech(self, active: bool, t: float) -> None: ...


class WakeEngine(Protocol):
    name: str

    def feed(self, block: Block) -> None: ...

    def close(self) -> None: ...


class NoTranscriber:
    """No speech-to-text yet (WO-5.2): every segment is "nothing said"."""

    name = "none"

    def __init__(self) -> None:
        self._told = False

    def transcribe(self, segment: Segment) -> str:
        if not self._told:
            self._told = True
            log.warning(
                "no speech-to-text engine yet (WO-5.2): segments are recorded, not sent"
            )
        return ""


class FixedTranscriber:
    """Every segment "says" one fixed text: measures the rest of the path (``--transcript``)."""

    name = "fixed"

    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, segment: Segment) -> str:
        return self.text


class LogSpeaker:
    """No text-to-speech yet (WO-5.3): what would be said is printed when a terminal is
    attached; the log gets only its length (gos-dev reads logs, D82). There is no audio, so
    ``on_first_audio`` is never called: a turn's ``speak_ms`` and ``total_ms`` stay empty."""

    name = "log"

    def __init__(self, out: Callable[[str], None] | None = None) -> None:
        self.out = out
        if self.out is None and sys.stdout is not None and sys.stdout.isatty():
            self.out = print

    def speak(
        self,
        text: str,
        on_first_audio: Callable[[], None],
        on_done: Callable[[], None],
    ) -> None:
        log.info("would say", extra={"chars": len(text)})
        if self.out is not None:
            self.out(f"GOS: {text}")
        on_done()

    def stop(self) -> None:
        pass


def _engine(module: str) -> Callable[..., Any] | None:
    """``gregtec_voice.<module>``'s ``create``, or None while its WO hasn't landed."""
    name = f"gregtec_voice.{module}"
    if importlib.util.find_spec(name) is None:
        return None
    create = getattr(importlib.import_module(name), "create", None)
    return create if callable(create) else None


def transcriber(settings: VoiceSettings) -> Transcriber:
    create = _engine("stt")
    return NoTranscriber() if create is None else create(settings)


def speaker(settings: VoiceSettings) -> Speaker:
    create = _engine("tts")
    return LogSpeaker() if create is None else create(settings)


def wake_engine(settings: VoiceSettings, bus: TriggerBus) -> WakeEngine | None:
    create = _engine("wake")
    return None if create is None else create(settings, bus)
