"""The rehearsal's voice client (WO-11.3): the real :class:`gregtec_voice.client.Process` (the
controller, the recorder, the core's HTTP and WebSocket client, the key filter and watchdog) with
the Linux stand-ins of its Windows parts: a fake microphone that delivers a tone every 20 ms, a
fake keyboard hook, a transcriber that "hears" the text the driver asked for, a speaker that
writes what it says, and the wake engine replaced by the trigger bus it drives (``wake`` and
``end_of_speech``), since openWakeWord's accuracy is a PC measurement (plan 10, WO-5.4).

Driven through :class:`rfakes.Mailbox` ``voice``: ``{"op": "talk", "text": ...}`` holds the talk
key (F13) for 0.4 s; ``{"op": "wake", "text": ...}`` fires the wake word, speaks for 0.4 s and
ends the utterance. Every spoken line and every core push is appended to ``voice/out.jsonl``.
The token is the keyring's ``token-voice`` (``gos token new voice``), as ``gos voice run``.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import rfakes

from gregtec import config
from gregtec.llm import keys
from gregtec_voice import audio
from gregtec_voice.audio import AudioPipe, FakeSource, Segment
from gregtec_voice.client import CoreClient, Online, Process, Pushed, VoiceClient
from gregtec_voice.ptt import FakeHook, FakeKeyState, KeyFilter, Watchdog
from gregtec_voice.settings import VoiceSettings

F13 = 0x7C  # the default talk key
EXIT_SETUP = 2


class Heard:
    """The transcriber: each segment "says" the text of the newest command."""

    name = "rehearsal"

    def __init__(self) -> None:
        self.text = ""

    def transcribe(self, segment: Segment) -> str:
        return self.text


class Speaker:
    name = "recording"

    def __init__(self, record: Callable[[dict[str, Any]], None]) -> None:
        self.record = record

    def speak(
        self, text: str, on_first_audio: Callable[[], None], on_done: Callable[[], None]
    ) -> None:
        self.record({"said": text, "t": time.time()})
        on_first_audio()
        on_done()

    def stop(self) -> None:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_voice.py")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--supervised", action="store_true")
    args = parser.parse_args(argv)
    cfg = config.load(args.config)
    try:
        token = keys.get_key("token-voice")
    except keys.MissingKeyError:
        print("no voice token", file=sys.stderr)
        return EXIT_SETUP
    box = rfakes.Mailbox(rfakes.control_dir(), "voice")
    lock = threading.Lock()

    def record(data: dict[str, Any]) -> None:
        with lock:
            box.record(data)

    heard = Heard()
    voice: VoiceClient
    core = CoreClient(
        cfg.core_port,
        token,
        on_push=lambda m: (
            record({"push": m.get("type"), "t": time.time()}),
            voice.post(Pushed(m)),
        ),
        on_online=lambda online: voice.post(Online(online, time.monotonic())),
    )
    voice = VoiceClient(
        VoiceSettings(), core, transcriber=heard, speaker=Speaker(record)
    )
    source = FakeSource()
    key_filter = KeyFilter(voice.watched, voice.post)
    hook, state = FakeHook(key_filter), FakeKeyState()
    process = Process(
        voice,
        AudioPipe(source, voice.recorder),
        hook,
        watchdog=Watchdog(hook, state, key_filter),
        core=core,
    )
    stop = threading.Event()
    if args.supervised:
        threading.Thread(target=_stop_on_stdin, args=(stop,), daemon=True).start()
    pcm = audio.tone(0.02)

    def mic() -> None:  # a live microphone: one 20 ms block every 20 ms
        while not stop.is_set():
            source.push(pcm)
            time.sleep(0.02)

    def commands() -> None:
        while not stop.is_set():
            for cmd in box.commands():
                heard.text = str(cmd.get("text") or "")
                record({"command": cmd.get("op"), "text": heard.text, "t": time.time()})
                if cmd.get("op") == "talk":
                    state.down.add(F13)
                    hook.press(F13)
                    time.sleep(0.4)
                    state.down.discard(F13)
                    hook.release(F13)
                elif cmd.get("op") == "wake":
                    voice.wake(time.monotonic())
                    time.sleep(0.4)
                    voice.end_of_speech(time.monotonic())
            time.sleep(0.1)

    threading.Thread(target=mic, daemon=True).start()
    threading.Thread(target=commands, daemon=True).start()
    record({"started": True, "t": time.time()})
    process.run(stop)
    return 0


def _stop_on_stdin(stop: threading.Event) -> None:
    try:
        for line in sys.stdin.buffer:
            if line.strip() == b"stop":
                break
    except (OSError, ValueError, AttributeError):
        pass
    stop.set()


if __name__ == "__main__":
    sys.exit(main())
