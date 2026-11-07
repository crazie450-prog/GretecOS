"""WO-5.1: the voice process end to end with the Linux stand-ins: a fake microphone, a fake hook
and key state, the real controller, recorder, pipe and watchdog threads, and a fake core."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from gregtec_voice import audio
from gregtec_voice.audio import AudioPipe, Block, FakeSource
from gregtec_voice.client import Process, VoiceClient
from gregtec_voice.ptt import FakeHook, FakeKeyState, KeyFilter, Watchdog
from gregtec_voice.settings import VoiceSettings

F13 = 0x7C


def wait(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


class Wake:
    """A wake engine stand-in: gets the blocks, is closed with the process."""

    name = "fake-wake"

    def __init__(self) -> None:
        self.blocks = 0
        self.closed = False

    def feed(self, block: Block) -> None:
        self.blocks += 1

    def close(self) -> None:
        self.closed = True


def test_the_process_runs_a_turn_with_its_threads(
    core: Any,  # noqa: ANN401 - the conftest FakeCore
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
) -> None:
    voice = VoiceClient(VoiceSettings(), core, transcriber=transcriber, speaker=speaker)
    source = FakeSource()
    wake = Wake()
    pipe = AudioPipe(source, voice.recorder, [wake])
    key_filter = KeyFilter(voice.watched, voice.post)
    hook = FakeHook(key_filter)
    state = FakeKeyState()
    dog = Watchdog(hook, state, key_filter)
    process = Process(voice, pipe, hook, watchdog=dog, wake=wake)
    stop = threading.Event()
    runner = threading.Thread(target=process.run, args=(stop,))
    runner.start()
    try:
        wait(lambda: hook.alive() and source._on_block is not None)
        pcm = audio.tone(0.02)
        for _ in range(20):
            source.push(pcm)
        state.down.add(F13)
        hook.press(F13)  # the hook thread's view: only a queued event
        wait(lambda: voice.recorder.recording == "key")
        for _ in range(
            40
        ):  # held for at least 0.4 s (a shorter press only stops speech)
            source.push(pcm)
            time.sleep(0.01)
        state.down.discard(F13)
        hook.release(F13)
        # the post-roll needs blocks captured after the key came up
        deadline = time.monotonic() + 5
        while not speaker.said:
            assert time.monotonic() < deadline
            source.push(pcm)
            time.sleep(0.01)
        assert core.asked == ["how do I make steel"]
        assert dog.reinstalls == 0 and wake.blocks > 60
    finally:
        stop.set()
        runner.join(10)
    assert not runner.is_alive()
    assert not hook.alive() and source._on_block is None and wake.closed
    assert core.states[-1] == ("off", None)


def test_a_silent_microphone_is_reopened(
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
) -> None:
    voice = VoiceClient(VoiceSettings(), core, transcriber=transcriber, speaker=speaker)
    source = FakeSource()
    pipe = AudioPipe(source, voice.recorder)
    now = [100.0]
    process = Process(
        voice, pipe, FakeHook(KeyFilter(set(), voice.post)), clock=lambda: now[0]
    )
    pipe.start()
    try:
        process.started = 100.0
        process._tick(102.0)
        assert source.started == 1  # 2 s of silence from the device: not yet
        process._tick(103.5)
        assert source.started == 2  # re-opened
        process._tick(110.0)
        assert source.started == 2  # at most every 10 s
        process._tick(114.0)
        assert source.started == 3
        pipe.last_block = 113.9  # audio flows again
        process._tick(125.0)
        assert source.started == 4  # 11 s without a block since
        pipe.last_block = 124.9
        process._tick(126.0)
        assert source.started == 4
    finally:
        pipe.stop()
