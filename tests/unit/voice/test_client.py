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

from gregtec_voice import tts
from gregtec_voice.audio import Block
from gregtec_voice.client import (
    CORE_ERROR,
    CORE_OFFLINE,
    CORE_REFUSED,
    DEEP_CAP,
    DEEP_FAILED,
    IDLE,
    LISTENING,
    OFF,
    SPEAKING,
    STT_ERROR,
    THINKING,
    CoreError,
    Online,
    Pushed,
    Refused,
    Transcribed,
    Turn,
    VoiceClient,
    first_sentence,
)
from gregtec_voice.lexicon import Lexicon
from gregtec_voice.ptt import KeyEvent
from gregtec_voice.settings import VoiceSettings

F13, F14, F15, F16, F17 = 0x7C, 0x7D, 0x7E, 0x7F, 0x80
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
    # a speaker that can't count (this fake) leaves the spoken length out
    assert (turn["spoken_sentences"], turn["spoken_words"], turn["spoken_cut"]) == (
        None,
        None,
        None,
    )


def test_a_turn_logs_how_long_its_answer_is_heard(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """WO-5.7 review M2: D23 on the live model's answers, as counts (never the text)."""
    caplog.set_level(logging.INFO)
    speaker.heard = tts.prepare(Lexicon()).said
    voice = make()
    voice._set_state(IDLE)
    audio_for(voice, clock, 0.5)
    key(voice, clock, F13, True)
    audio_for(voice, clock, 1.0)
    key(voice, clock, F13, False)
    audio_for(voice, clock, 0.2)
    run_events(voice, lambda: voice.state == (IDLE, None))
    (turn,) = turns(caplog)
    # "You said how do I make steel." (the fake core's reply)
    assert (turn["spoken_sentences"], turn["spoken_words"], turn["spoken_cut"]) == (
        1,
        7,
        False,
    )


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


NOTHING_PENDING = {"text": "Nothing is waiting for confirmation.", "pending": False}


def test_hotkeys(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    core.answer = lambda text: NOTHING_PENDING
    voice = make()
    key(voice, clock, F14, True)  # confirm: the router's instant command (D59)
    key(voice, clock, F14, False)
    run_events(voice, lambda: len(speaker.said) == 1)
    voice.drain()
    assert core.asked == ["confirm"]
    assert speaker.said == ["Nothing is waiting for confirmation."]
    key(voice, clock, F13, True)  # talking...
    audio_for(voice, clock, 0.5)
    key(voice, clock, F15, True)  # cancel: drops the recording, then tells the core
    assert voice.recorder.recording is None
    key(voice, clock, F13, False)  # the talk key's release does nothing now
    run_events(voice, lambda: len(turns(caplog)) == 2)
    assert core.asked == ["confirm", "cancel"]
    # nothing was waiting for cancel: pressed to silence GOS, it says nothing either
    assert len(speaker.said) == 1 and voice.state == (IDLE, None)
    key(voice, clock, F16, True)  # next card: a dashboard action, nothing is asked
    key(voice, clock, F16, True)  # auto-repeat
    assert core.uis == ["next_card"] and len(core.asked) == 2


def test_cancel_says_what_it_cancelled_when_an_action_was_waiting(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    core.answer = lambda text: {"text": "Cancelled the crafting job.", "pending": True}
    voice = make()
    key(voice, clock, F15, True)
    run_events(voice, lambda: bool(speaker.said))
    assert speaker.said == ["Cancelled the crafting job."]


def test_confirm_doesnt_supersede_the_question_in_flight(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    gate = threading.Event()

    def answer(text: str) -> dict[str, Any]:
        if text == "confirm":
            return NOTHING_PENDING
        gate.wait(5)  # the question's answer takes a while
        return {"text": f"You said {text}.", "lane": "fast"}

    core.answer = answer
    voice = make()
    talk(voice, clock)
    run_events(voice, lambda: len(core.asked) == 1)
    key(voice, clock, F14, True)  # confirm while the question is on its way
    run_events(voice, lambda: len(speaker.said) == 1)
    voice.drain()  # the speaker's callbacks
    assert voice.state[0] == THINKING  # still waiting for the question's answer
    gate.set()
    run_events(voice, lambda: len(speaker.said) == 2)
    assert speaker.said == [
        "Nothing is waiting for confirmation.",
        "You said how do I make steel.",
    ]


def test_cancel_drops_the_question_on_its_way(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    gate = threading.Event()

    def answer(text: str) -> dict[str, Any]:
        if text == "cancel":
            return NOTHING_PENDING
        gate.wait(5)
        return {"text": f"You said {text}.", "lane": "fast"}

    core.answer = answer
    voice = make()
    talk(voice, clock)
    run_events(voice, lambda: len(core.asked) == 1)
    key(voice, clock, F15, True)
    gate.set()
    run_events(voice, lambda: len(turns(caplog)) == 2)
    assert speaker.said == []
    assert sorted(t["outcome"] for t in turns(caplog)) == ["answered", "cancelled"]
    assert voice.state == (IDLE, None)


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
    ("offline", "refused", "socket_down", "said", "later"),
    [
        # the WebSocket is down too: an outage, and that was its one "Core offline."
        (True, False, True, CORE_OFFLINE, []),
        # the WebSocket is up: the core is there (a timeout), no outage to announce
        (True, False, False, CORE_ERROR, []),
        # the core answered with an error; the outage is still announced after 15 s
        (False, False, True, CORE_ERROR, [CORE_OFFLINE]),
        # 401/403: the token, not an outage
        (False, True, False, CORE_REFUSED, []),
    ],
)
def test_a_failed_ask_is_said(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    run_events: Pump,
    offline: bool,
    refused: bool,
    socket_down: bool,
    said: str,
    later: list[str],
) -> None:
    core.fail = CoreError("POST /api/ask: no", offline=offline, refused=refused)
    voice = make()
    if socket_down:
        voice.handle(Online(False, clock()))
    talk(voice, clock)
    run_events(voice, lambda: bool(speaker.said))
    assert speaker.said == [said]
    voice.drain()
    voice.tick(clock.advance(20.0))
    voice.drain()
    assert speaker.said == [said, *later]


def test_a_refused_token_is_said_instead_of_core_offline(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
) -> None:
    voice = make()
    voice.handle(Refused(clock()))
    voice.handle(Online(False, clock()))
    voice.tick(clock.advance(30.0))
    voice.drain()
    assert speaker.said == [CORE_REFUSED]  # the core is there: no "Core offline."
    voice.handle(Online(True, clock()))  # a new token, a new client: connected
    voice.handle(Online(False, clock()))  # a real outage later is said again
    voice.tick(clock.advance(15.0))
    assert speaker.said == [CORE_REFUSED, CORE_OFFLINE]


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


def test_a_wake_turn_is_asked_without_the_phrases_tail(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    """D185: the wake segment's pre-roll holds the end of the phrase, which speech-to-text may
    write out; a talk-key turn's text is sent as it is."""
    voice = make()
    transcriber.text = "vist, how do I make steel"
    voice.wake(clock())
    voice.drain()
    audio_for(voice, clock, 1.0)
    voice.end_of_speech(clock())
    voice.drain()
    audio_for(voice, clock, 0.2)
    run_events(voice, lambda: len(core.asked) == 1)
    assert core.asked == ["how do I make steel"]
    transcriber.text = "Archivist, what does vist mean"
    talk(voice, clock)
    run_events(voice, lambda: len(core.asked) == 2)
    assert core.asked[1] == "Archivist, what does vist mean"


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
    assert voice.watched == {F14, F15, F16, F17}
    key(voice, clock, F13, True)
    assert voice.recorder.recording is None
    key(voice, clock, F16, True)
    assert core.uis == ["next_card"]
    assert make().watched == {F13, F14, F15, F16, F17}


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


# --- review fixes: superseding only once sent (M1), key edge cases (L8), deep-lane matching (L7)


@pytest.mark.parametrize("answer_while", ["held", "transcribing"])
def test_an_empty_transcript_keeps_the_answer_on_its_way(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
    caplog: pytest.LogCaptureFixture,
    answer_while: str,
) -> None:
    """A press held past 0.3 s with nothing said (a slow barge-in, a false wake, STT hearing
    nothing) sends nothing, so it supersedes nothing: the first answer is still said."""
    caplog.set_level(logging.INFO)
    voice = make()
    core.hold = threading.Event()
    talk(voice, clock)
    run_events(voice, lambda: len(core.asked) == 1)
    transcriber.text = ""
    key(voice, clock, F13, True)
    audio_for(voice, clock, 0.5)
    if answer_while == "held":
        core.hold.set()  # the answer comes while the key is down: it waits
        run_events(voice, lambda: bool(voice._queued))
        assert speaker.said == []
    key(voice, clock, F13, False)
    audio_for(voice, clock, 0.2)
    if answer_while == "transcribing":
        core.hold.set()
    run_events(voice, lambda: bool(speaker.said))
    assert speaker.said == ["You said how do I make steel."] and len(core.asked) == 1
    voice.drain()
    assert sorted(t["outcome"] for t in turns(caplog)) == ["answered", "nothing said"]


def test_speech_waits_while_a_segment_is_transcribed(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    """Until the transcript says whether there was a question, what would be said waits."""
    gate = threading.Event()
    real = transcriber.transcribe

    def slow(segment: Any) -> str:  # noqa: ANN401
        gate.wait(5)
        return str(real(segment))

    transcriber.transcribe = slow
    voice = make()
    talk(voice, clock)
    run_events(voice, lambda: bool(voice._transcribing))
    voice.say("A notice.")
    assert speaker.said == [] and voice.state[0] == THINKING
    gate.set()
    run_events(voice, lambda: len(speaker.said) == 2)
    assert speaker.said == ["A notice.", "You said how do I make steel."]


def test_a_failed_transcription_is_said_and_supersedes_nothing(
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

    def broken(segment: Any) -> str:  # noqa: ANN401
        raise RuntimeError("the model file is missing")

    transcriber.transcribe = broken
    talk(voice, clock)
    run_events(voice, lambda: bool(speaker.said))
    assert speaker.said == [STT_ERROR]
    core.hold.set()
    run_events(voice, lambda: len(speaker.said) == 2)
    assert speaker.said[1] == "You said how do I make steel."
    voice.drain()
    assert sorted(t["outcome"] for t in turns(caplog)) == ["answered", "error"]


def test_segments_are_transcribed_one_at_a_time(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    gate = threading.Event()
    running: list[int] = []
    most = [0]
    lock = threading.Lock()

    def slow(segment: Any) -> str:  # noqa: ANN401
        with lock:
            running.append(1)
            most[0] = max(most[0], len(running))
        gate.wait(5)
        time.sleep(0.05)
        with lock:
            running.pop()
        return "how do I make steel"

    transcriber.transcribe = slow
    voice = make()
    talk(voice, clock)
    talk(voice, clock)  # a second segment while the first is still transcribed
    run_events(voice, lambda: len(voice._transcribing) == 2)
    gate.set()
    run_events(voice, lambda: not voice._transcribing)
    assert most[0] == 1  # the transcriber never ran twice at once (hooks' contract)


def test_an_older_transcript_after_a_newer_question_is_not_sent(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    run_events: Pump,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    voice = make()
    older, newer = Turn(1, "key", clock()), Turn(2, "key", clock())
    voice.turns = 2
    voice._transcribing = {1: older, 2: newer}
    voice.handle(Transcribed(newer, "what uses steel"))
    voice.handle(Transcribed(older, "how do I make steel"))  # its STT finished later
    run_events(voice, lambda: len(turns(caplog)) == 2)
    assert core.asked == ["what uses steel"]
    assert {t["turn"]: t["outcome"] for t in turns(caplog)}[1] == "superseded"


def test_a_quick_repress_in_hold_mode_is_a_second_question(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    """Pressed again within the post-roll: the first segment keeps its post-roll, the second
    starts at once (its pre-roll), and both are sent."""
    voice = make()
    audio_for(voice, clock, 0.5)  # the room before the key
    key(voice, clock, F13, True)
    audio_for(voice, clock, 1.0)
    key(voice, clock, F13, False)
    audio_for(voice, clock, 0.04)  # within the first segment's 120 ms post-roll
    key(voice, clock, F13, True)
    assert voice.recorder.recording == "key" and voice.state == (LISTENING, "key")
    audio_for(voice, clock, 1.0)
    key(voice, clock, F13, False)
    audio_for(voice, clock, 0.2)
    run_events(voice, lambda: len(core.asked) == 2)
    first, second = transcriber.seen
    assert first == pytest.approx(0.3 + 1.0 + 0.14, abs=0.03)  # the full post-roll
    assert second == pytest.approx(0.3 + 1.0 + 0.14, abs=0.03)


def test_cancel_drops_a_segment_still_finishing_its_post_roll(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    core.answer = lambda text: NOTHING_PENDING
    voice = make()
    key(voice, clock, F13, True)
    audio_for(voice, clock, 1.0)
    key(voice, clock, F13, False)
    key(voice, clock, F13, True)  # pressed again: the first one finishes its post-roll
    key(voice, clock, F15, True)  # cancel both
    audio_for(voice, clock, 0.2)
    run_events(voice, lambda: core.asked == ["cancel"] and len(turns(caplog)) == 2)
    assert transcriber.seen == []  # nothing went through speech-to-text
    assert sorted(t["outcome"] for t in turns(caplog)) == ["answered", "cancelled"]


def test_toggle_after_the_cap_the_next_press_only_ends_the_turn(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    voice = make(talk_mode="toggle")
    key(voice, clock, F13, True)
    key(voice, clock, F13, False)
    audio_for(voice, clock, 31.0)  # still talking: the 30 s cap sends the segment
    run_events(voice, lambda: len(core.asked) == 1)
    key(voice, clock, F13, True)  # meant to end the turn the cap already ended
    key(voice, clock, F13, False)
    assert voice.recorder.recording is None
    key(voice, clock, F13, True)  # the next press starts a new question
    assert voice.recorder.recording == "key"


def test_a_deep_task_is_matched_by_text_only_within_the_ask_and_once(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    run_events: Pump,
) -> None:
    core.answer = lambda text: {"text": "Handed over.", "handoff": {"source": "x"}}
    transcriber.text = "compare the steel lines"
    voice = make()
    talk(voice, clock)
    run_events(voice, lambda: len(speaker.said) == 1)
    voice.drain()
    request, now = "compare the steel lines", int(time.time())
    # the same words asked an hour later from Discord: not this client's
    handle(voice, task("h-7", "done", request=request, created=now + 3600, text="No."))
    # the task this client's ask made, then a second task with the same words
    handle(voice, task("h-8", "done", request=request, created=now, text="Yes. More."))
    handle(voice, task("h-9", "done", request=request, created=now, text="Again."))
    assert speaker.said[1:] == ["Yes."]


def test_barge_in_logs_the_answers_it_drops(
    make: Callable[..., VoiceClient],
    clock: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    speaker.auto = False
    voice = make()
    said, waiting = Turn(1, "key", clock()), Turn(2, "key", clock())
    said.outcome = waiting.outcome = "answered"
    voice.say("The first answer.", said)
    voice.say("Waiting to be said.", waiting)
    key(voice, clock, F13, True)  # barge-in
    assert sorted(t["turn"] for t in turns(caplog)) == [1, 2]
    assert {t["outcome"] for t in turns(caplog)} == {"stopped"}


def alert(event: str, say: str | None) -> Pushed:
    return Pushed({"type": "alert", "data": {"event": event, "say": say}})


def test_a_telemetry_alert_that_fired_is_said_once(
    make: Callable[..., VoiceClient],
    speaker: Any,  # noqa: ANN401
) -> None:
    # WO-7.5: the core pushes every alert event; only a fired one has a line to say.
    voice = make()
    line = "Lab: power low. Lapotronic Supercapacitor at 12.3 percent."
    handle(voice, alert("fired", line))
    handle(voice, alert("cleared", None))
    handle(voice, alert("stale", None))
    handle(voice, Pushed({"type": "alert", "data": "not an event"}))
    handle(voice, alert("fired", "x" * 1000))
    assert speaker.said == [line, "x" * 300]
