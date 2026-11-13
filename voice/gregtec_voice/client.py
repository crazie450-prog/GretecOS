"""The voice client (plan 4.2, 4.5; D179): trigger -> segment -> transcript -> core -> speech.

**One controller thread owns the state.** The key hook, the watchdog, the audio worker, the
speaker, the core's WebSocket and the worker threads only post events to its queue
(:meth:`VoiceClient.post`), so nothing waits on anything slow: a model call never holds up a key.

**A turn**: the talk key goes down (or the wake engine fires): speech stops (barge-in), the
fast lane's prompt cache is warmed (``POST /api/prewarm``, D152) and a segment opens with its
pre-roll. The key comes up: after the post-roll the segment closes and a worker transcribes it
(WO-5.2's engine, one segment at a time). A transcript with words in it is sent (``POST
/api/ask``, D152) and the reply's text is spoken (WO-5.3's engine) while its card goes to the
dashboards (the core pushes ``ask.reply``). A question, once sent, supersedes the one still in
flight: that one's late reply isn't spoken, nor an answer still waiting to be said. Until then
nothing is superseded: an answer that comes while Clayton talks, or while what he said is
transcribed, waits, and is said when it turns out he said nothing (a tap that only stopped
speech, an empty transcript). Each turn's timings go to ``logs/voice.log`` as a ``turn`` record
(never its text): ``gos voice bench`` reads them (PM14).

**Hotkeys**: *confirm* and *cancel* are the router's instant commands (D59, D150), sent beside
the question in flight, which they don't supersede; both first drop a segment being recorded and
stop speech. *Cancel* also drops the questions on their way (their answers aren't said), and its
own reply is said only when an action was waiting for it (the core's ``pending``): pressed to
silence GOS, it never makes GOS talk. *Next card* is ``POST /api/ui {"action": "next_card"}``,
which the core pushes to the dashboards.

**The screen key** (WO-8.2, D288) is a question about the screen: it drops a segment being
recorded, stops speech and supersedes the question in flight, as a new question does; a worker
grabs **one** frame (:mod:`gregtec_voice.screen`) and sends it (``POST /api/screen``), and the
reply's text is said. Nothing else ever captures: no other key, no wake, no push, no timer.
With no capture set up the key says so. A frame that can't be had says why.

**The core**: the client's state (idle, listening, thinking, speaking) goes to the core
(``POST /api/voice/state``) for the dashboard's voice strip and orb, again after a reconnect.
The WebSocket brings ``deep.task`` pushes: a task this client handed off (by its id, or by its
request text, which the deep lane keeps verbatim, D152, made while the ask was on its way) is
spoken when it is done (its first sentence, the deep lane's spoken result), when it waits at its
spending cap, and when it fails. When the core's WebSocket has been down for 15 s, the client
says "Core offline." once per outage (plan 4.2); a supervisor restart (about 2 s, D115) stays
quiet. An ask that gets no answer says it at once while the WebSocket is down too (the outage's
one), else "The core couldn't answer that." A token the core refuses is said as that, not as an
outage.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from gregtec import control
from gregtec_voice.audio import AudioError, AudioPipe, Recorder, Segment
from gregtec_voice.hooks import Speaker, Transcriber, WakeEngine
from gregtec_voice.ptt import (
    CANCEL,
    START,
    STOP,
    Hook,
    Hotkeys,
    KeyEvent,
    TalkKey,
    Watchdog,
)
from gregtec_voice.screen import Capture, CaptureError, Frame
from gregtec_voice.settings import KEY, WAKE, VoiceSettings
from gregtec_voice.wake import strip_phrase

log = logging.getLogger(__name__)

VOICE = "voice"  # this client's name; its token is gregtec-os/token-voice (D117)
IDLE, LISTENING, THINKING, SPEAKING, OFF = (
    "idle",
    "listening",
    "thinking",
    "speaking",
    "off",
)
CANCEL_HOTKEY, NEXT_CARD, SCREEN = "cancel", "next_card", "screen"
CORE_OFFLINE = "Core offline."
CORE_ERROR = "The core couldn't answer that."
CORE_REFUSED = "The core refused the voice client's token."
STT_ERROR = "Speech-to-text failed."
MIC_LOST = "Microphone lost."
DEEP_CAP = (
    "The deep-lane task stopped at its spending cap. Approve it on the dashboard."
)
DEEP_FAILED = "The deep-lane task failed. The dashboard has the error."
SCREEN_OFF = "Screen reading isn't set up in the voice client."
NO_FRAME = "I couldn't see the game: {why}."
OFFLINE_SAY_S = 15.0  # the core unreachable this long: say so, once per outage
ASK_TIMEOUT_S = 60.0  # the fast lane gives up well within this (D152: 15 s, 1 retry)
PREWARM_TIMEOUT_S = 30.0
POST_TIMEOUT_S = 5.0
WS_BACKOFF_S = (1.0, 2.0, 4.0, 8.0)
SPEECH_QUEUE = 3  # utterances waiting to be said; older ones are dropped
MINE_KEEP = 20  # recent asks remembered to recognise this client's deep-lane tasks
TICK_S = 0.1


class CoreError(Exception):
    """The core didn't answer (``offline``), refused the token (``refused``) or answered with
    an error."""

    def __init__(self, message: str, *, offline: bool, refused: bool = False) -> None:
        super().__init__(message)
        self.offline = offline
        self.refused = refused


class CoreApi(Protocol):
    """What the controller needs from the core (:class:`CoreClient`; tests use a fake)."""

    def ask(self, text: str) -> dict[str, Any]: ...

    def screen(self, frame: Frame) -> dict[str, Any]: ...

    def prewarm(self) -> None: ...

    def ui(self, action: str) -> None: ...

    def state(self, state: str, trigger: str | None) -> None: ...


# --- events for the controller ----------------------------------------------------------------


@dataclass(frozen=True)
class Closed:
    segment: Segment


@dataclass(frozen=True)
class Transcribed:
    turn: Turn
    text: str  # stripped; "" when nothing was said
    error: str | None = None  # the transcriber failed: its exception's type


@dataclass(frozen=True)
class Answered:
    turn: Turn
    reply: dict[str, Any] | None
    error: str | None = None
    offline: bool = False
    refused: bool = False
    said: str | None = (
        None  # an error's own words (a screen frame that couldn't be had)
    )


@dataclass(frozen=True)
class Pushed:
    message: dict[str, Any]


@dataclass(frozen=True)
class Online:
    online: bool
    t: float


@dataclass(frozen=True)
class Refused:
    """The core refused the voice client's token (its WebSocket said 401 or 403)."""

    t: float


@dataclass(frozen=True)
class Wake:
    kind: str  # start, end, cancel or speech
    t: float


@dataclass(frozen=True)
class Spoken:
    utterance: int
    kind: str  # first_audio or done
    t: float


@dataclass
class Turn:
    id: int
    trigger: str  # key, wake, confirm, cancel or screen
    started: float  # key down, wake, or the hotkey press
    stopped: float | None = None  # key up or end of speech (PM14's start point)
    closed: float | None = None
    text_at: float | None = None
    replied: float | None = None
    first_audio: float | None = None
    seconds: float | None = None
    stt_ms: int | None = None
    lane: str | None = None
    intent: str | None = None
    core_ttft_ms: int | None = None
    checked: bool | None = None
    outcome: str = ""
    said: tuple[int, int, bool] | None = (
        None  # D23 as heard: sentences, words, cut (WO-5.7)
    )

    def record(self) -> dict[str, Any]:
        """The turn's timings in ms and the length of its answer as heard, counts only (no
        text: gos-dev reads the logs, D82)."""

        def ms(a: float | None, b: float | None) -> int | None:
            return None if a is None or b is None else round((b - a) * 1000)

        return {
            "turn": self.id,
            "trigger": self.trigger,
            "outcome": self.outcome,
            "lane": self.lane,
            "intent": self.intent,
            "checked": self.checked,
            "audio_s": None if self.seconds is None else round(self.seconds, 2),
            "held_ms": ms(self.started, self.stopped),
            "close_ms": ms(self.stopped, self.closed),
            "stt_ms": self.stt_ms,
            "core_ms": ms(self.text_at, self.replied),
            "core_ttft_ms": self.core_ttft_ms,
            "speak_ms": ms(self.replied, self.first_audio),
            "total_ms": ms(self.stopped, self.first_audio),
            "spoken_sentences": None if self.said is None else self.said[0],
            "spoken_words": None if self.said is None else self.said[1],
            "spoken_cut": None if self.said is None else self.said[2],
        }


@dataclass
class _Utterance:
    id: int
    text: str
    turn: Turn | None


def first_sentence(text: str) -> str:
    """The deep lane's spoken result: its answer's first sentence (deep_lane.md)."""
    text = text.strip()
    end = re.search(r"(?<=[.!?])\s|\n", text)
    return (text[: end.start()] if end else text).strip()


def _normalise(text: str) -> str:
    return " ".join(text.split())  # as gregtec.agents.ask does before it routes


class VoiceClient:
    """The controller (see the module docstring). :meth:`handle` and :meth:`tick` run on one
    thread (:meth:`run`); tests call them directly."""

    def __init__(
        self,
        settings: VoiceSettings,
        core: CoreApi,
        *,
        transcriber: Transcriber,
        speaker: Speaker,
        observer: Callable[[object], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        screen: Capture | None = None,
    ) -> None:
        self.settings = settings
        self.screen = screen  # the screen key's one frame per press (WO-8.2); None: off
        # sees each event first, on this thread (gos voice test)
        self.observer = observer
        self.core = core
        self.transcriber = transcriber
        self.speaker = speaker
        self.clock = clock
        self.events: queue.Queue[object] = queue.Queue()
        self.recorder = Recorder(lambda seg: self.post(Closed(seg)), clock=clock)
        self.talk = TalkKey(settings.talk_mode)
        self.hotkeys = Hotkeys({k.vk: name for name, k in settings.hotkeys().items()})
        self.state: tuple[str, str | None] = (OFF, None)
        self.turns = 0
        # turns whose segment is open or still taking its post-roll, by the segment's start
        self._segments: dict[float, Turn] = {}
        self._transcribing: dict[int, Turn] = {}  # by turn id
        # the question whose answer is wanted: the newest sent, until it is said or cancelled
        self._turn: Turn | None = None
        # questions up to this turn id aren't sent any more (a newer one was, or cancel)
        self._floor = 0
        self._commands: dict[int, Turn] = {}  # confirm and cancel waiting for the core
        self._speaking: _Utterance | None = None
        self._queued: deque[_Utterance] = deque(maxlen=SPEECH_QUEUE)
        self._utterances = 0
        # (normalised text, wall clock) of recent questions sent; this thread only
        self._asked: deque[tuple[str, float]] = deque(maxlen=MINE_KEEP)
        # deep-lane task id -> how many of its approvals were announced
        self._mine: dict[str, int] = {}
        self._offline_since: float | None = None
        self._told_offline = False
        self._stt = threading.Lock()  # one segment at a time through the transcriber

    @property
    def watched(self) -> set[int]:
        """The virtual-key codes the hook passes on: the talk key (when enabled), the hotkeys."""
        vks = {k.vk for k in self.settings.hotkeys().values()}
        if self.settings.key_trigger:
            vks.add(self.settings.talk_key.vk)
        return vks

    def post(self, event: object) -> None:
        """Any thread: queue an event for the controller."""
        self.events.put(event)

    # The trigger bus a wake engine drives (gregtec_voice.hooks.TriggerBus, WO-5.4).
    def wake(self, t: float) -> None:
        self.post(Wake("start", t))

    def end_of_speech(self, t: float) -> None:
        self.post(Wake("end", t))

    def cancel(self) -> None:
        self.post(Wake("cancel", self.clock()))

    def speech(self, active: bool, t: float) -> None:
        if active:
            self.post(Wake("speech", t))

    # --- the loop

    def run(
        self, stop: threading.Event, on_tick: Callable[[float], None] | None = None
    ) -> None:
        self._set_state(IDLE)
        while not stop.is_set():
            try:
                event = self.events.get(timeout=TICK_S)
            except queue.Empty:
                event = None
            if event is not None:
                try:
                    self.handle(event)
                except Exception:  # one bad event mustn't stop the voice client
                    log.exception(
                        "voice event failed", extra={"event": type(event).__name__}
                    )
            now = self.clock()
            self.tick(now)
            if on_tick is not None:
                on_tick(now)
        self._barge_in()
        self.recorder.cancel()
        self._segments.clear()
        self._set_state(OFF)

    def drain(self) -> None:
        """Handle every queued event now (tests)."""
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            self.handle(event)

    def handle(self, event: object) -> None:
        if self.observer is not None:
            self.observer(event)
        if isinstance(event, KeyEvent):
            self._key(event)
        elif isinstance(event, Closed):
            self._closed(event.segment)
        elif isinstance(event, Transcribed):
            self._transcribed(event)
        elif isinstance(event, Answered):
            self._answered(event)
        elif isinstance(event, Spoken):
            self._spoken(event)
        elif isinstance(event, Wake):
            self._wake(event)
        elif isinstance(event, Pushed):
            self._pushed(event.message)
        elif isinstance(event, Online):
            self._online(event)
        elif isinstance(event, Refused):
            self._refused(event)

    def tick(self, now: float) -> None:
        self.recorder.tick(now)
        if (
            self._offline_since is not None
            and not self._told_offline
            and now - self._offline_since >= OFFLINE_SAY_S
        ):
            self._told_offline = True
            log.warning("the core is unreachable")
            self.say(CORE_OFFLINE)

    # --- triggers

    def _key(self, ev: KeyEvent) -> None:
        if ev.vk == self.settings.talk_key.vk:
            if not self.settings.key_trigger:
                return
            action = self.talk.press(ev.t) if ev.down else self.talk.release(ev.t)
            if action == START:
                self._start(KEY, ev.t)
            elif action == STOP:
                self._stop_segment(KEY, ev.t)
            elif action == CANCEL:
                self._cancel_segment(KEY)
            return
        name = self.hotkeys.event(ev.vk, ev.down)
        if name is not None:
            self._hotkey(name, ev.t)

    def _wake(self, ev: Wake) -> None:
        if ev.kind == "start" and self.settings.wake_trigger:
            self._start(WAKE, ev.t)
        elif ev.kind == "end":
            self._stop_segment(WAKE, ev.t)
        elif ev.kind == "cancel":
            self._cancel_segment(WAKE)
        elif ev.kind == "speech" and self._speaking is not None:
            log.info("barge-in by voice")
            self._barge_in()
            self._settle()

    def _start(self, trigger: str, t: float) -> None:
        self._barge_in()
        if not self.recorder.start(trigger, t):
            return  # a segment is being recorded: the other trigger's
        self.turns += 1
        self._segments[t] = Turn(self.turns, trigger, t)
        self.core.prewarm()
        self._set_state(LISTENING, trigger)

    def _stop_segment(self, trigger: str, t: float) -> None:
        if self.recorder.recording != trigger:
            return
        started = self.recorder.started
        turn = None if started is None else self._segments.get(started)
        self.recorder.stop(t)
        if turn is not None and turn.stopped is None:
            turn.stopped = t

    def _cancel_segment(self, trigger: str) -> None:
        if self.recorder.recording == trigger and self._drop_segment():
            self._settle()  # a question still in flight keeps its answer

    def _drop_segment(self) -> bool:
        """Drop the segment being recorded; its turn is forgotten (a tap isn't a turn)."""
        started = self.recorder.started
        if not self.recorder.cancel():
            return False
        if started is not None:
            self._segments.pop(started, None)
        return True

    def _hotkey(self, name: str, t: float) -> None:
        log.info("hotkey", extra={"hotkey": name})
        if name == NEXT_CARD:
            self.core.ui(NEXT_CARD)
            return
        if name == SCREEN:
            self._screen_key(t)
            return
        if self._drop_segment():  # confirm and cancel drop what was being recorded
            self.talk.dropped()
        self._barge_in()
        if name == CANCEL_HOTKEY:
            self._cancel_questions()
        self.turns += 1
        turn = Turn(self.turns, name, t, stopped=t, closed=t)
        self._commands[turn.id] = turn  # beside the question in flight: no superseding
        self._request(turn, name)

    def _cancel_questions(self) -> None:
        """The cancel hotkey: segments still closing or being transcribed aren't sent, and the
        question in flight isn't answered."""
        self._floor = self.turns
        for turn in [*self._segments.values(), *self._transcribing.values()]:
            turn.outcome = "cancelled"
        if self._turn is not None and self._turn.replied is None:
            self._turn.outcome = "cancelled"  # logged when its answer comes
        self._turn = None

    # --- turns

    def _closed(self, segment: Segment) -> None:
        if segment.capped and segment.trigger == KEY:
            self.talk.capped(segment.started)
        turn = self._segments.pop(segment.started, None)
        if turn is None:
            return  # dropped before it closed
        turn.closed = segment.closed
        turn.seconds = segment.seconds
        if segment.capped:
            turn.stopped = segment.closed
        if turn.id <= self._floor:  # cancelled while it took its post-roll
            self._log_turn(turn)
            self._settle()
            return
        self._transcribing[turn.id] = turn
        self._settle()
        threading.Thread(
            target=self._transcribe,
            args=(turn, segment),
            name="gos-voice-stt",
            daemon=True,
        ).start()

    def _transcribe(self, turn: Turn, segment: Segment) -> None:
        """A worker thread: speech-to-text, one segment at a time. Posts :class:`Transcribed`."""
        try:
            with self._stt:
                began = self.clock()
                said = self.transcriber.transcribe(segment)
                if turn.trigger == WAKE:  # the pre-roll holds the phrase's tail (D185)
                    said = strip_phrase(said, self.settings.wake_phrase)
                turn.stt_ms = round((self.clock() - began) * 1000)
        except Exception as exc:  # a failed engine must not kill the controller's turns
            log.exception("speech-to-text failed")
            self.post(Transcribed(turn, "", type(exc).__name__))
            return
        self.post(Transcribed(turn, said.strip()))

    def _transcribed(self, ev: Transcribed) -> None:
        turn = ev.turn
        self._transcribing.pop(turn.id, None)
        if turn.id <= self._floor:
            turn.outcome = turn.outcome or "superseded"  # a newer question went first
            self._log_turn(turn)
        elif ev.error is not None:
            turn.outcome = "error"
            self.say(STT_ERROR, turn)
        elif not ev.text:
            turn.outcome = "nothing said"
            self._log_turn(turn)
        else:
            self._ask(turn, ev.text)
            return
        self._settle()  # what waited for the transcript can be said now

    def _screen_key(self, t: float) -> None:
        """One press, one frame: a question about the screen (D288)."""
        if self._drop_segment():
            self.talk.dropped()
        self._barge_in()
        if self.screen is None:
            self.say(SCREEN_OFF)
            return
        self.turns += 1
        turn = Turn(self.turns, SCREEN, t, stopped=t, closed=t)
        self._supersede(turn)
        turn.text_at = self.clock()
        self._settle()
        threading.Thread(
            target=self._screen_core,
            args=(turn, self.screen),
            name="gos-voice-screen",
            daemon=True,
        ).start()

    def _screen_core(self, turn: Turn, capture: Capture) -> None:
        """A worker thread: grab the one frame, send it. Posts :class:`Answered`."""
        try:
            frame = capture.grab()
        except CaptureError as exc:
            self.post(Answered(turn, None, "no frame", said=NO_FRAME.format(why=exc)))
            return
        except Exception as exc:  # the controller's turns must go on
            log.exception("screen capture failed")
            self.post(Answered(turn, None, type(exc).__name__))
            return
        log.info(
            "screen frame",
            extra={"source": frame.source, "w": frame.width, "h": frame.height},
        )
        try:
            reply = self.core.screen(frame)
        except CoreError as exc:
            self.post(
                Answered(turn, None, str(exc), offline=exc.offline, refused=exc.refused)
            )
        except Exception as exc:
            log.exception("sending the frame failed")
            self.post(Answered(turn, None, type(exc).__name__))
        else:
            self.post(Answered(turn, reply))

    def _ask(self, turn: Turn, text: str) -> None:
        """Send a question: it supersedes the one in flight, and an answer still waiting to be
        said is dropped (notices and hotkey replies stay)."""
        self._supersede(turn)
        self._asked.append((_normalise(text), time.time()))
        self._request(turn, text)

    def _supersede(self, turn: Turn) -> None:
        """``turn`` is now the question whose answer is wanted."""
        self._floor = turn.id
        old = self._turn
        if old is not None and old.replied is None:
            old.outcome = "superseded"  # logged when its answer comes
        kept: deque[_Utterance] = deque(maxlen=SPEECH_QUEUE)
        for u in self._queued:
            if u.turn is not None and u.turn.trigger in (KEY, WAKE, SCREEN):
                u.turn.outcome = "superseded"
                self._log_turn(u.turn)
            else:
                kept.append(u)
        self._queued = kept
        self._turn = turn

    def _request(self, turn: Turn, text: str) -> None:
        """Ask the core on a worker thread, which posts :class:`Answered`."""
        turn.text_at = self.clock()
        self._settle()
        threading.Thread(
            target=self._ask_core,
            args=(turn, text),
            name="gos-voice-ask",
            daemon=True,
        ).start()

    def _ask_core(self, turn: Turn, text: str) -> None:
        try:
            reply = self.core.ask(text)
        except CoreError as exc:
            self.post(
                Answered(turn, None, str(exc), offline=exc.offline, refused=exc.refused)
            )
        except Exception as exc:  # the controller's turns must go on
            log.exception("asking the core failed")
            self.post(Answered(turn, None, type(exc).__name__))
        else:
            self.post(Answered(turn, reply))

    def _answered(self, ev: Answered) -> None:
        turn = ev.turn
        turn.replied = self.clock()
        command = self._commands.pop(turn.id, None) is not None
        if not command and turn is not self._turn:
            turn.outcome = turn.outcome or "superseded"
            self._log_turn(turn)
            self._settle()
            return
        quiet = turn.trigger == CANCEL_HOTKEY  # pressed to silence GOS
        if ev.error is not None:
            turn.outcome = (
                "refused" if ev.refused else "offline" if ev.offline else "error"
            )
            log.warning("the core didn't answer", extra={"error": ev.error[:200]})
            self.say("" if quiet else ev.said or self._failure(ev), turn)
            return
        reply = ev.reply or {}
        self._persona_voice(reply)
        turn.outcome = "answered"
        turn.lane, turn.intent = reply.get("lane"), reply.get("intent")
        turn.core_ttft_ms, turn.checked = reply.get("ttft_ms"), reply.get("checked")
        handoff = reply.get("handoff")
        if isinstance(handoff, dict) and isinstance(handoff.get("id"), str):
            self._mine.setdefault(handoff["id"], 0)
        if quiet and reply.get("pending") is False:
            # nothing was waiting for it: the press only stopped speech
            self.say("", turn)
            return
        self.say(str(reply.get("text") or ""), turn)

    def _persona_voice(self, reply: dict[str, Any]) -> None:
        """The reply's persona speaks in its own voice (WO-5.6, D207): a speaker that can
        change voice (``use_voice``) gets the persona's voice entries before the reply."""
        persona = reply.get("persona")
        use = getattr(self.speaker, "use_voice", None)
        if use is None or not isinstance(persona, dict):
            return
        voice = persona.get("voice")
        if isinstance(voice, str):
            use(voice)

    def _failure(self, ev: Answered) -> str:
        """What to say when the core didn't answer a request."""
        if ev.refused:
            return CORE_REFUSED
        if ev.offline and self._offline_since is not None:
            self._told_offline = True  # the outage's one "Core offline."
            return CORE_OFFLINE
        return CORE_ERROR

    # --- speech

    def say(self, text: str, turn: Turn | None = None) -> None:
        """Speak ``text`` now, or after what is being said, recorded or transcribed."""
        if not text:
            if turn is not None:
                self._finish(turn)
            self._settle()
            return
        self._utterances += 1
        utterance = _Utterance(self._utterances, text, turn)
        if self._speaking is not None or self._holding():
            self._queued.append(utterance)
            return
        self._speak(utterance)

    def _speak(self, u: _Utterance) -> None:
        self._speaking = u
        self._set_state(SPEAKING, self.state[1])
        self.speaker.speak(
            u.text,
            on_first_audio=lambda: self.post(Spoken(u.id, "first_audio", self.clock())),
            on_done=lambda: self.post(Spoken(u.id, "done", self.clock())),
        )
        heard = getattr(
            self.speaker, "heard", None
        )  # after speak: first audio waits for none
        if u.turn is not None and u.turn.said is None and callable(heard):
            said = heard(u.text)
            if said is not None:
                u.turn.said = (said.sentences, said.words, said.cut)

    def _spoken(self, ev: Spoken) -> None:
        u = self._speaking
        if u is None or u.id != ev.utterance:
            return  # a stopped utterance's late callback
        if ev.kind == "first_audio":
            if u.turn is not None and u.turn.first_audio is None:
                u.turn.first_audio = ev.t
            return
        self._speaking = None
        if u.turn is not None:
            self._finish(u.turn)
        self._settle()

    def _barge_in(self) -> None:
        """Stop speaking and forget what was waiting to be said."""
        dropped = list(self._queued)
        self._queued.clear()
        u, self._speaking = self._speaking, None
        if u is not None:
            self.speaker.stop()
            dropped.insert(0, u)
        for each in dropped:
            if each.turn is not None:
                if each.turn.outcome == "answered":
                    each.turn.outcome = "stopped"
                self._finish(each.turn)

    def _finish(self, turn: Turn) -> None:
        if turn is self._turn:
            self._turn = None
        self._log_turn(turn)

    def _log_turn(self, turn: Turn) -> None:
        log.info("turn", extra=turn.record())

    def _holding(self) -> bool:
        """Speech waits while Clayton talks, and while what he said is transcribed: a question
        in it would supersede an answer waiting to be said."""
        return self.recorder.recording is not None or bool(self._transcribing)

    def _busy(self) -> bool:
        """Something is on its way: a transcript, an answer, a hotkey's reply."""
        turn = self._turn
        waiting = turn is not None and turn.replied is None
        return waiting or bool(self._transcribing) or bool(self._commands)

    def _settle(self) -> None:
        """Say what waited, unless something holds it back; else show what goes on now."""
        if self._speaking is None and self._queued and not self._holding():
            self._speak(self._queued.popleft())
        elif self._speaking is not None:
            self._set_state(SPEAKING, self.state[1])
        elif self.recorder.recording is not None:
            self._set_state(LISTENING, self.recorder.recording)
        elif self._busy():
            self._set_state(THINKING, self.state[1])
        else:
            self._set_state(IDLE)

    def _set_state(self, state: str, trigger: str | None = None) -> None:
        if (state, trigger) != self.state:
            self.state = (state, trigger)
            self.core.state(state, trigger)

    # --- the core's pushes and reachability

    def _pushed(self, message: dict[str, Any]) -> None:
        data = message.get("data")
        if message.get("type") == "notice" and isinstance(data, dict):
            # a proactive notice (WO-6.5, D76): its spoken line, which holds no ids or numbers
            say = data.get("say")
            if isinstance(say, str) and say.strip():
                self.say(" ".join(say.split())[:300])
            return
        if message.get("type") != "deep.task" or not isinstance(
            message.get("data"), dict
        ):
            return
        task = message["data"]
        task_id, status = task.get("id"), task.get("status")
        if not isinstance(task_id, str):
            return
        if task_id not in self._mine and self._asked_for(task):
            self._mine[task_id] = 0
        if task_id not in self._mine:
            return
        if status == "done":
            del self._mine[task_id]
            self.say(first_sentence(str(task.get("text") or "")))
        elif status == "needs_approval":
            approvals = int(task.get("approvals") or 0)
            if self._mine[task_id] <= approvals:  # once per pause
                self._mine[task_id] = approvals + 1
                self.say(DEEP_CAP)
        elif status == "failed":
            del self._mine[task_id]
            self.say(DEEP_FAILED)
        elif status in ("stopped", "offline"):
            del self._mine[task_id]

    def _asked_for(self, task: dict[str, Any]) -> bool:
        """Whether a task's request is a question this client sent, made while that ask was
        on its way (a fast-lane handoff's reply has no id; the deep lane keeps Clayton's words
        verbatim, D152). A match is used up: the same words asked later elsewhere aren't."""
        request, created = task.get("request"), task.get("created")
        if not isinstance(request, str) or not isinstance(created, int | float):
            return False
        wanted = _normalise(request)
        for i, (text, asked) in enumerate(self._asked):
            # ``created`` is whole seconds: allow for the rounding and clock steps
            if text == wanted and asked - 2 <= created <= asked + ASK_TIMEOUT_S:
                del self._asked[i]
                return True
        return False

    def _online(self, ev: Online) -> None:
        if ev.online:
            if self._offline_since is not None:
                log.info("the core is reachable again")
            self._offline_since, self._told_offline = None, False
        elif self._offline_since is None:
            self._offline_since = ev.t

    def _refused(self, ev: Refused) -> None:
        """The core is there but refuses the token: say that, and no "Core offline." for this
        outage (the client logs how to fix it)."""
        self._told_offline = True
        self.say(CORE_REFUSED)


# --- the core connection ----------------------------------------------------------------------


@dataclass
class _Outbox:
    state: dict[str, Any] | None = None
    state_dirty: bool = False
    actions: list[str] = field(default_factory=list)


class CoreClient:
    """HTTP to the core (WO-2.1's bearer token) and its WebSocket for pushes, with reconnects.

    ``ask`` blocks (a turn worker calls it); ``prewarm`` runs on its own thread, at most one at a
    time; the voice state and UI actions go out in order from one sender thread, the state
    coalesced to the newest and sent again after every reconnect. Nothing goes through an HTTP
    proxy (the bearer token stays on loopback, as ``gregtec.control`` does). ``on_refused`` is
    called once each time the core starts refusing the token (401 or 403 on its WebSocket).
    """

    def __init__(
        self,
        port: int,
        token: str,
        *,
        on_push: Callable[[dict[str, Any]], None],
        on_online: Callable[[bool], None],
        on_refused: Callable[[], None] | None = None,
        call: Callable[..., dict[str, Any]] = control.call,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self.port = port
        self.token = token
        self.on_push = on_push
        self.on_online = on_online
        self.on_refused = on_refused
        self.call = call
        self.connect = connect
        self.online: bool | None = None
        self.url = f"ws://{control.LOOPBACK}:{port}/ws"
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._outbox = _Outbox()
        self._prewarming = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._refused = False

    def _post(
        self, path: str, body: dict[str, Any] | None, timeout: float
    ) -> dict[str, Any]:
        return self.call(
            self.port, "POST", path, token=self.token, body=body, timeout=timeout
        )

    def ask(self, text: str) -> dict[str, Any]:
        try:
            return self._post("/api/ask", {"text": text}, ASK_TIMEOUT_S)
        except control.ControlError as exc:
            raise CoreError(
                str(exc), offline=exc.status is None, refused=exc.status in (401, 403)
            ) from None

    def screen(self, frame: Frame) -> dict[str, Any]:
        """``POST /api/screen`` with the frame (WO-8.2)."""
        try:
            return self.call(
                self.port,
                "POST",
                f"/api/screen?source={frame.source}",
                token=self.token,
                raw=("image/png", frame.png),
                timeout=ASK_TIMEOUT_S,
            )
        except control.ControlError as exc:
            raise CoreError(
                str(exc), offline=exc.status is None, refused=exc.status in (401, 403)
            ) from None

    def prewarm(self) -> None:
        if not self._prewarming.acquire(blocking=False):
            return  # one is on its way

        def run() -> None:
            try:
                status = self._post("/api/prewarm", None, PREWARM_TIMEOUT_S).get(
                    "status"
                )
                log.debug("prewarm", extra={"status": status})
            except control.ControlError as exc:
                log.debug("prewarm failed", extra={"error": str(exc)[:200]})
            finally:
                self._prewarming.release()

        threading.Thread(target=run, name="gos-voice-prewarm", daemon=True).start()

    def ui(self, action: str) -> None:
        with self._cond:
            self._outbox.actions.append(action)
            self._cond.notify()

    def state(self, state: str, trigger: str | None) -> None:
        with self._cond:
            self._outbox.state = {"state": state, "trigger": trigger}
            self._outbox.state_dirty = True
            self._cond.notify()

    def start(self) -> None:
        self._stop.clear()
        for target, name in (
            (self._send_loop, "gos-voice-send"),
            (self._ws_loop, "gos-voice-ws"),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self, *, final_state: bool = True) -> None:
        """Stop both threads; first tell the core the voice client is off."""
        if final_state:
            try:
                self._post("/api/voice/state", {"state": OFF, "trigger": None}, 2.0)
            except control.ControlError:
                pass
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        for thread in self._threads:
            thread.join(timeout=5.0)
        self._threads.clear()

    def _send_loop(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                while not (
                    self._stop.is_set()
                    or self._outbox.state_dirty
                    or self._outbox.actions
                ):
                    self._cond.wait()
                if self._stop.is_set():
                    return
                state = self._outbox.state if self._outbox.state_dirty else None
                self._outbox.state_dirty = False
                actions, self._outbox.actions = self._outbox.actions, []
            try:
                if state is not None:
                    self._post("/api/voice/state", state, POST_TIMEOUT_S)
                for action in actions:
                    self._post("/api/ui", {"action": action}, POST_TIMEOUT_S)
            except control.ControlError as exc:
                # The state goes out again after the WebSocket reconnects; an action is dropped.
                log.debug("can't reach the core", extra={"error": str(exc)[:200]})

    def _set_online(self, online: bool) -> None:
        if online != self.online:
            self.online = online
            self.on_online(online)

    def _ws_loop(self) -> None:
        import websockets.exceptions
        import websockets.sync.client

        connect = self.connect or websockets.sync.client.connect
        tries = 0
        while not self._stop.is_set():
            try:
                with connect(
                    self.url,
                    additional_headers={"Authorization": f"Bearer {self.token}"},
                    proxy=None,
                    open_timeout=5,
                    close_timeout=2,
                ) as ws:
                    tries = 0
                    self._refused = False
                    self._set_online(True)
                    with (
                        self._cond
                    ):  # the core may have restarted: tell it the state again
                        self._outbox.state_dirty = self._outbox.state is not None
                        self._cond.notify()
                    self._receive(ws)
            except websockets.exceptions.InvalidStatus as exc:
                status = exc.response.status_code
                if status not in (401, 403):
                    log.debug("core WebSocket not accepted", extra={"status": status})
                elif not self._refused:
                    self._refused = True
                    log.error(
                        "the core refused the voice client's token: run gos token new voice",
                        extra={"status": status},
                    )
                    if self.on_refused is not None:
                        self.on_refused()
            except (
                OSError,
                websockets.exceptions.WebSocketException,
                TimeoutError,
            ) as exc:
                log.debug("core WebSocket closed", extra={"error": str(exc)[:200]})
            if self._stop.is_set():
                return
            self._set_online(False)
            self._stop.wait(WS_BACKOFF_S[min(tries, len(WS_BACKOFF_S) - 1)])
            tries += 1

    def _receive(self, ws: Any) -> None:  # noqa: ANN401 - a websockets ClientConnection
        while not self._stop.is_set():
            try:
                raw = ws.recv(timeout=0.5)
            except TimeoutError:
                continue
            if not isinstance(raw, str):
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if isinstance(message, dict):
                self.on_push(message)


# --- the process ------------------------------------------------------------------------------

AUDIO_STALL_S = 3.0  # no audio this long: re-open the microphone
AUDIO_RETRY_S = 10.0


class Process:
    """The voice process's parts, started and stopped in order (``gos voice run`` and
    ``gos voice test`` build it with the Windows parts, tests with fakes)."""

    def __init__(
        self,
        voice: VoiceClient,
        pipe: AudioPipe,
        hook: Hook,
        *,
        watchdog: Watchdog | None = None,
        core: CoreClient | None = None,
        wake: WakeEngine | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.voice = voice
        self.pipe = pipe
        self.hook = hook
        self.watchdog = watchdog
        self.core = core
        self.wake = wake
        self.clock = clock
        self.started = clock()
        self._retry_at = 0.0
        self._lost = False  # the microphone stopped delivering audio (said once)

    def run(self, stop: threading.Event) -> None:
        """Start everything, run the controller until ``stop`` is set, stop everything."""
        self.started = self.clock()
        self.pipe.start()  # AudioError: the microphone can't be opened
        try:
            self.hook.install()
            if self.watchdog is not None:
                self.watchdog.start()
            if self.core is not None:
                self.core.start()
            self.voice.run(stop, on_tick=self._tick)
        finally:
            if self.watchdog is not None:
                self.watchdog.stop()
            self.hook.uninstall()
            self.pipe.stop()
            if self.wake is not None:
                self.wake.close()
            if self.core is not None:
                self.core.stop()

    def _tick(self, now: float) -> None:
        """Re-open a microphone that stopped delivering audio (a headset that went away), and
        say "Microphone lost." once when it stops (the controller's thread: ``on_tick``)."""
        last = (
            self.pipe.last_block if self.pipe.last_block is not None else self.started
        )
        if now - last < AUDIO_STALL_S:
            if self._lost:
                self._lost = False
                log.info("the microphone delivers audio again")
            return
        if not self._lost:
            self._lost = True
            log.warning("no audio from the microphone: re-opening it every 10 s")
            self.voice.say(MIC_LOST)
        if now < self._retry_at:
            return
        self._retry_at = now + AUDIO_RETRY_S
        try:
            self.pipe.restart_source()
        except AudioError as exc:
            log.info("can't re-open the microphone yet", extra={"error": str(exc)})
