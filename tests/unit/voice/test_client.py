"""WO-5.1, D179-D181: the voice client's controller with fakes for the core, STT and TTS:
talk-key turns in hold and toggle mode, barge-in, hotkeys, the wake-word hook point, deep-lane
results, "core offline" and the turn timings."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from gregtec_voice.audio import Block
from gregtec_voice.client import (
    CORE_ERROR,
    CORE_OFFLINE,
    DEEP_CAP,
    DEEP_FAILED,
    IDLE,
    LISTENING,
    OFF,
    SPEAKING,
    THINKING,
    CoreError,
    Online,
    Pushed,
    Turn,
    VoiceClient,
    first_sentence,
)
from gregtec_voice.ptt import KeyEvent
from gregtec_voice.settings import VoiceSettings

F13, F14, F15, F16 = 0x7C, 0x7D, 0x7E, 0x7F
BLOCK = bytes(640)

Pump = Callable[[VoiceClient, Callable[[], bool]], None]


@pytest.fixture
def make(
    clock: Any,  # noqa: ANN401 - the conftest Clock
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
) -> Callable[..., VoiceClient]:
    def build(**kw: Any) -> VoiceClient:  # noqa: ANN401
        return VoiceClient(
            VoiceSettings(**kw),
            core,
            transcriber=transcriber,
            speaker=speaker,
            clock=clock,
        )

    return build


def audio_for(voice: VoiceClient, clock: Any, seconds: float) -> None:  # noqa: ANN401
    """Feed ``seconds`` of 20 ms blocks as the audio worker would, moving the clock."""
    for _ in range(round(seconds / 0.02)):
        voice.recorder.feed(Block(BLOCK, clock.advance(0.02)))


def key(voice: VoiceClient, clock: Any, vk: int, down: bool) -> None:  # noqa: ANN401
    voice.handle(KeyEvent(vk, down, clock()))


def talk(voice: VoiceClient, clock: Any, held_s: float = 1.0) -> None:  # noqa: ANN401
    """A whole talk-key press: down, speech, up, the post-roll."""
    key(voice, clock, F13, True)
    audio_for(voice, clock, held_s)
    key(voice, clock, F13, False)
    audio_for(voice, clock, 0.2)


def turns(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        {k: getattr(r, k) for k in Turn(0, "", 0).record()}
        for r in caplog.records
        if r.getMessage() == "turn"
    ]


def test_a_talk_key_turn(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    voice = make()
    voice._set_state(IDLE)
    audio_for(voice, clock, 0.5)  # the room before the key
    key(voice, clock, F13, True)
    assert voice.state == (LISTENING, "key") and core.prewarms == 1
    audio_for(voice, clock, 1.0)
    key(voice, clock, F13, False)
    audio_for(voice, clock, 0.2)
    run_events(voice, lambda: bool(speaker.said))
    assert core.asked == ["how do I make steel"]
    assert speaker.said == ["You said how do I make steel."]
    run_events(voice, lambda: voice.state == (IDLE, None))
    assert [s for s, _ in core.states] == [IDLE, LISTENING, THINKING, SPEAKING, IDLE]
    # 300 ms of pre-roll, 1 s held, up to the block past the 120 ms post-roll
    assert transcriber.seen[0] == pytest.approx(0.3 + 1.0 + 0.14, abs=0.03)
    (turn,) = turns(caplog)
    assert turn["outcome"] == "answered" and turn["trigger"] == "key"
    assert (turn["lane"], turn["core_ttft_ms"], turn["checked"]) == ("fast", 900, True)
    assert turn["held_ms"] == 1000 and turn["audio_s"] == pytest.approx(1.44, abs=0.03)
    assert turn["total_ms"] is not None and turn["total_ms"] >= turn["close_ms"]
    assert not any("steel" in r.getMessage() for r in caplog.records)  # no text in logs


def test_a_short_tap_only_stops_speech(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
) -> None:
    speaker.auto = False
    voice = make()
    voice.say("A long answer that keeps going.")
    assert voice.state[0] == SPEAKING
    key(voice, clock, F13, True)  # barge-in: speech stops at once
    assert speaker.stopped == 1
    audio_for(voice, clock, 0.1)
    key(voice, clock, F13, False)  # a 0.1 s tap: nothing is sent
    audio_for(voice, clock, 0.3)
    voice.drain()
    assert core.asked == [] and voice.recorder.recording is None
    assert voice.state == (IDLE, None)


def test_toggle_mode(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    voice = make(talk_mode="toggle")
    key(voice, clock, F13, True)
    key(voice, clock, F13, False)
    audio_for(voice, clock, 2.0)  # still recording: releases don't end it
    assert voice.recorder.recording == "key"
    key(voice, clock, F13, True)
    key(voice, clock, F13, False)
    audio_for(voice, clock, 0.2)
    run_events(voice, lambda: bool(speaker.said))
    assert core.asked == ["how do I make steel"]


def test_a_new_turn_supersedes_one_in_flight(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    voice = make()
    core.hold = threading.Event()
    talk(voice, clock)
    run_events(voice, lambda: len(core.asked) == 1)
    transcriber.text = "what uses steel"
    talk(voice, clock)  # asked again before the first answer came
    run_events(voice, lambda: len(core.asked) == 2)
    core.hold.set()
    run_events(voice, lambda: bool(speaker.said) and len(turns(caplog)) == 2)
    assert speaker.said == ["You said what uses steel."]
    assert sorted(t["outcome"] for t in turns(caplog)) == ["answered", "superseded"]


def test_a_tap_while_an_answer_is_pending_keeps_it(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    voice = make()
    core.hold = threading.Event()
    talk(voice, clock)
    run_events(voice, lambda: len(core.asked) == 1)
    talk(voice, clock, held_s=0.1)  # a tap: barge-in only, nothing sent
    core.hold.set()
    run_events(voice, lambda: bool(speaker.said))
    assert speaker.said == ["You said how do I make steel."] and len(core.asked) == 1


def test_an_answer_waiting_while_a_new_question_is_recorded_is_dropped_when_it_is_sent(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    voice = make()
    core.hold = threading.Event()
    talk(voice, clock)
    run_events(voice, lambda: len(core.asked) == 1)
    key(voice, clock, F13, True)  # asking again while the first answer is on its way
    core.hold.set()
    run_events(voice, lambda: bool(turns(caplog)) or bool(voice._queued))
    voice.say("A notice.")  # not an answer: it survives the new question
    assert speaker.said == []  # the first answer waits: Clayton is talking
    transcriber.text = "what uses steel"
    audio_for(voice, clock, 1.0)
    key(voice, clock, F13, False)
    audio_for(voice, clock, 0.2)
    run_events(voice, lambda: len(speaker.said) == 2)
    assert speaker.said == ["A notice.", "You said what uses steel."]
    voice.drain()  # the speaker's callbacks: the turn is logged when it has been said
    assert sorted(t["outcome"] for t in turns(caplog)) == ["answered", "superseded"]


def test_hotkeys(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    core.answer = lambda text: {"text": "Nothing is waiting for confirmation."}
    voice = make()
    key(voice, clock, F14, True)  # confirm: the router's instant command (D59)
    key(voice, clock, F14, False)
    run_events(voice, lambda: len(speaker.said) == 1)
    voice.drain()
    assert core.asked == ["confirm"]
    key(voice, clock, F13, True)  # talking...
    audio_for(voice, clock, 0.5)
    key(voice, clock, F15, True)  # cancel: drops the recording, then tells the core
    assert voice.recorder.recording is None
    key(voice, clock, F13, False)  # the talk key's release does nothing now
    run_events(voice, lambda: len(speaker.said) == 2)
    assert core.asked == ["confirm", "cancel"]
    key(voice, clock, F16, True)  # next card: a dashboard action, nothing is asked
    key(voice, clock, F16, True)  # auto-repeat
    assert core.uis == ["next_card"] and len(core.asked) == 2


def test_nothing_said_asks_nothing(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    transcriber.text = "  "
    voice = make()
    talk(voice, clock)
    run_events(voice, lambda: bool(turns(caplog)))
    assert core.asked == [] and turns(caplog)[0]["outcome"] == "nothing said"
    assert voice.state == (IDLE, None)


@pytest.mark.parametrize(
    ("offline", "said"), [(True, CORE_OFFLINE), (False, CORE_ERROR)]
)
def test_a_failed_ask_is_said(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
    offline: bool,
    said: str,
) -> None:
    core.fail = CoreError("POST /api/ask: no answer", offline=offline)
    voice = make()
    talk(voice, clock)
    run_events(voice, lambda: bool(speaker.said))
    assert speaker.said == [said]


def test_an_unreachable_core_is_said_once_per_outage(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
) -> None:
    voice = make()
    voice.handle(Online(False, clock()))
    voice.tick(clock.advance(14.0))  # a supervisor restart takes about 2 s: stay quiet
    assert speaker.said == []
    voice.tick(clock.advance(1.0))
    voice.tick(clock.advance(30.0))
    voice.drain()  # the speaker's callbacks
    assert speaker.said == [CORE_OFFLINE]
    voice.handle(Online(True, clock()))
    voice.handle(Online(False, clock()))
    voice.tick(clock.advance(15.0))
    assert speaker.said == [CORE_OFFLINE, CORE_OFFLINE]


def task(task_id: str, status: str, **kw: Any) -> Pushed:  # noqa: ANN401
    return Pushed(
        {"type": "deep.task", "data": {"id": task_id, "status": status, **kw}}
    )


def handle(voice: VoiceClient, event: object) -> None:
    """Handle ``event``, then what it set off (the fake speaker's callbacks)."""
    voice.handle(event)
    voice.drain()


def test_deep_lane_results_are_spoken_for_this_clients_tasks(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    handed = {"text": "That's one for the deep lane; I've handed it over."}
    core.answer = lambda text: {**handed, "lane": "deep", "handoff": {"id": "h-1"}}
    voice = make()
    talk(voice, clock)
    run_events(voice, lambda: len(speaker.said) == 1)
    voice.drain()  # the speaker's callbacks: done speaking
    # not this client's task
    handle(voice, task("h-9", "done", text="Someone else's answer."))
    handle(voice, task("h-1", "running"))
    handle(voice, task("h-1", "done", text="Two routes fit at HV. The rows: r:x/1 ..."))
    assert speaker.said[1:] == ["Two routes fit at HV."]
    # the fast lane's handoff tool gives no id: the task is known by its request text
    core.answer = lambda text: {
        **handed,
        "lane": "fast",
        "handoff": {"source": "fast_lane"},
    }
    transcriber.text = "compare  the steel   lines"
    talk(voice, clock)
    run_events(voice, lambda: len(speaker.said) == 3)
    voice.drain()
    created = int(time.time())
    request = "compare the steel lines"
    handle(voice, task("h-2", "queued", request=request, created=created))
    handle(voice, task("h-2", "needs_approval", request=request, approvals=0))
    # the same pause again: said once
    handle(voice, task("h-2", "needs_approval", request=request, approvals=0))
    handle(voice, task("h-2", "running", request=request, approvals=1))
    # approved, then paused again: said again
    handle(voice, task("h-2", "needs_approval", request=request, approvals=1))
    handle(voice, task("h-2", "failed", request=request, error="x"))
    handle(voice, task("h-2", "failed", request=request))  # forgotten once final
    assert speaker.said[3:] == [DEEP_CAP, DEEP_CAP, DEEP_FAILED]


def test_speech_waits_while_listening(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    voice = make()
    key(voice, clock, F13, True)
    audio_for(voice, clock, 0.5)
    voice.say("A notice.")  # arrives while Clayton talks
    assert speaker.said == []
    key(voice, clock, F13, False)
    audio_for(voice, clock, 0.2)
    run_events(voice, lambda: len(speaker.said) == 2)
    # the notice is said once the key is up, while the question is on its way
    assert speaker.said == ["A notice.", "You said how do I make steel."]


def test_the_wake_hook_point(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    """WO-5.4 drives the client through wake / end_of_speech / cancel / speech."""
    voice = make()
    voice.wake(clock())
    voice.drain()
    assert voice.state == (LISTENING, "wake") and core.prewarms == 1
    key(voice, clock, F13, True)  # the other trigger can't take an open segment
    audio_for(voice, clock, 1.0)
    key(voice, clock, F13, False)
    assert voice.recorder.recording == "wake"
    voice.end_of_speech(clock())
    voice.drain()
    audio_for(voice, clock, 0.2)
    run_events(voice, lambda: len(speaker.said) == 1)
    assert len(core.asked) == 1
    voice.wake(clock())
    voice.cancel()  # the cancel window, or a failed second-stage check
    voice.drain()
    audio_for(voice, clock, 1.0)
    assert voice.recorder.recording is None and len(core.asked) == 1
    speaker.auto = False
    voice.say("Something long.")
    voice.speech(True, clock())  # Clayton talks over it: barge-in by voice
    voice.speech(False, clock())
    voice.drain()
    assert speaker.stopped == 1 and voice.state == (IDLE, None)


def test_without_the_wake_trigger_a_wake_does_nothing(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
) -> None:
    voice = make(primary_trigger="key", backup_trigger="none")
    voice.wake(clock())
    voice.drain()
    assert voice.recorder.recording is None


def test_with_the_talk_key_off_the_hotkeys_still_work(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
) -> None:
    voice = make(primary_trigger="wake", backup_trigger="none")
    assert voice.watched == {F14, F15, F16}
    key(voice, clock, F13, True)
    assert voice.recorder.recording is None
    key(voice, clock, F16, True)
    assert core.uis == ["next_card"]
    assert make().watched == {F13, F14, F15, F16}


def test_the_run_loop_reports_idle_then_off(
    make: Callable[..., VoiceClient],
    core: Any,  # noqa: ANN401
) -> None:
    voice = make()
    stop = threading.Event()
    ticks: list[float] = []
    thread = threading.Thread(
        target=voice.run, args=(stop,), kwargs={"on_tick": ticks.append}
    )
    thread.start()
    voice.post(object())  # an unknown event is ignored
    while not ticks:
        pass
    stop.set()
    thread.join(5)
    assert core.states[0] == (IDLE, None) and core.states[-1] == (OFF, None)


def test_first_sentence() -> None:
    assert first_sentence("Two routes fit. Then 1.5 A of rows.") == "Two routes fit."
    assert first_sentence("Use 1.5 A cable!\nDetails...") == "Use 1.5 A cable!"
    assert first_sentence("  No end  ") == "No end"
