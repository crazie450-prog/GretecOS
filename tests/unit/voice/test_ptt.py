"""WO-5.1, D180: the hook that only enqueues, its watchdog, the talk key (hold and toggle) and the
hotkeys. Linux runs the logic with a fake hook and fake key state; the Windows tests install the
real pynput hook and inject keys with SendInput (F24, which nothing binds)."""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import time
from collections.abc import Callable

import pytest

from gregtec_voice import ptt
from gregtec_voice.ptt import (
    CANCEL,
    START,
    STOP,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    FakeHook,
    FakeKeyState,
    Hotkeys,
    KeyEvent,
    KeyFilter,
    TalkKey,
    Watchdog,
)

F13, F14, F24 = 0x7C, 0x7D, 0x87
WM_CHAR = 0x0102


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_the_filter_queues_only_the_watched_keys() -> None:
    got: list[KeyEvent] = []
    clock = Clock()
    key_filter = KeyFilter({F13, F14}, got.append, clock=clock)
    key_filter(WM_KEYDOWN, 0x41)  # A: not watched, nothing at all happens
    key_filter(WM_KEYDOWN, F13)
    key_filter(WM_SYSKEYDOWN, F14)  # with Alt held
    key_filter(WM_CHAR, F13)  # not a press or release
    key_filter(WM_SYSKEYUP, F14)
    key_filter(WM_KEYUP, F13)
    assert [(e.vk, e.down, e.source) for e in got] == [
        (F13, True, "hook"),
        (F14, True, "hook"),
        (F14, False, "hook"),
        (F13, False, "hook"),
    ]
    assert key_filter.seen == {F13: False, F14: False}


def test_the_filter_measures_hook_lag_across_the_tick_wrap() -> None:
    got: list[KeyEvent] = []
    key_filter = KeyFilter({F13}, got.append, tick=lambda: 5)
    key_filter(
        WM_KEYDOWN, F13, 0xFFFF_FFFE
    )  # the event's tick just before GetTickCount wrapped
    assert got[0].lag_ms == 7


def test_hold_mode() -> None:
    talk = TalkKey("hold")
    assert talk.press(1.0) == START
    assert talk.press(1.1) is None  # auto-repeat
    assert talk.release(2.0) == STOP
    assert talk.release(2.1) is None  # a duplicate release
    assert talk.press(3.0) == START
    assert talk.release(3.2) == CANCEL  # a tap: barge-in only, nothing is sent


def test_toggle_mode() -> None:
    talk = TalkKey("toggle")
    assert talk.press(1.0) == START
    assert talk.press(1.05) is None  # auto-repeat
    assert talk.release(1.1) is None  # releases do nothing in toggle mode
    assert talk.press(4.0) == STOP  # the second press ends it, however short the first
    assert talk.release(4.1) is None
    assert talk.press(5.0) == START


def test_after_the_cap_a_toggle_press_only_ends_the_turn() -> None:
    talk = TalkKey("toggle")
    talk.press(1.0)
    talk.release(1.1)
    talk.capped(1.0)  # the 30 s cap sent the segment opened at 1.0
    assert talk.press(40.0) is None  # the press meant to end it starts nothing
    assert talk.release(40.1) is None
    assert talk.press(41.0) == START


def test_after_the_cap_a_hold_release_does_nothing() -> None:
    hold = TalkKey("hold")
    hold.press(1.0)
    hold.capped(1.0)
    assert hold.release(31.0) is None  # the cap already sent it
    assert hold.press(32.0) == START


def test_the_cap_of_an_older_segment_leaves_a_newer_press_alone() -> None:
    hold = TalkKey("hold")
    hold.press(1.0)
    assert hold.release(2.0) == STOP
    assert hold.press(2.05) == START  # pressed again during the first one's post-roll
    hold.capped(1.0)  # the first segment, not this one
    assert hold.release(3.0) == STOP


def test_a_hotkey_dropping_the_segment_resets_the_key() -> None:
    talk = TalkKey("toggle")
    talk.press(1.0)
    talk.dropped()  # the cancel hotkey dropped what was being recorded
    talk.release(1.1)
    assert talk.press(2.0) == START
    capped = TalkKey("toggle")
    capped.press(1.0)
    capped.capped(1.0)
    capped.release(31.0)
    capped.dropped()  # a fresh start after cancel: the next press talks
    assert capped.press(32.0) == START


def test_talk_mode_must_be_known() -> None:
    with pytest.raises(ValueError, match="hold or toggle"):
        TalkKey("push")


def test_hotkeys_act_on_the_press_only() -> None:
    hot = Hotkeys({F14: "confirm"})
    assert hot.event(F14, True) == "confirm"
    assert hot.event(F14, True) is None  # auto-repeat
    assert hot.event(F14, False) is None
    assert hot.event(F14, True) == "confirm"
    assert hot.event(F13, True) is None


# --- the watchdog ---------------------------------------------------------------------------


def make_watch() -> tuple[Watchdog, FakeHook, FakeKeyState, list[KeyEvent], Clock]:
    got: list[KeyEvent] = []
    clock = Clock()
    key_filter = KeyFilter({F13, F14}, got.append, clock=clock)
    hook = FakeHook(key_filter)
    state = FakeKeyState()
    hook.install()
    return Watchdog(hook, state, key_filter, clock=clock), hook, state, got, clock


def press(hook: FakeHook, state: FakeKeyState, vk: int) -> None:
    """A key going down: Windows' key state, and the hook (which sees nothing when dead)."""
    state.down.add(vk)
    state.pressed.add(vk)
    hook.press(vk)


def release(hook: FakeHook, state: FakeKeyState, vk: int) -> None:
    state.down.discard(vk)
    hook.release(vk)


def test_a_healthy_hook_is_left_alone() -> None:
    dog, hook, state, got, clock = make_watch()
    press(hook, state, F13)  # the hook sees it as it happens
    for _ in range(10):
        clock.now += 0.05
        dog.check()
    release(hook, state, F13)
    press(hook, state, F14)  # a tap between two polls, which the hook saw
    release(hook, state, F14)
    for _ in range(3):
        clock.now += 0.05
        dog.check()
    assert dog.reinstalls == 0 and len(got) == 4


def test_a_press_the_hook_missed_is_passed_on_and_the_hook_reinstalled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dog, hook, state, got, clock = make_watch()
    hook.dead = True  # Windows removed the hook after a timeout: it sees nothing now
    press(hook, state, F13)
    first = clock.now
    dog.check()
    assert got == [] and dog.reinstalls == 0  # one poll: the hook may only be late
    clock.now += 0.05
    dog.check()
    assert dog.reinstalls == 1 and hook.installed == 2 and not hook.dead
    assert [(e.vk, e.down, e.source) for e in got] == [(F13, True, "watchdog")]
    assert got[0].t == first  # when the watchdog first saw it, not 50 ms later
    assert [getattr(r, "why", None) for r in caplog.records] == ["it missed F13 down"]
    # the new hook sees the release itself
    release(hook, state, F13)
    clock.now += 1
    dog.check()
    assert [(e.down, e.source) for e in got] == [(True, "watchdog"), (False, "hook")]


def test_a_missed_release_is_passed_on_too() -> None:
    dog, hook, state, got, clock = make_watch()
    press(hook, state, F14)
    hook.dead = True
    release(hook, state, F14)  # lost
    dog.check()
    clock.now += 0.05
    dog.check()
    assert [(e.vk, e.down, e.source) for e in got] == [
        (F14, True, "hook"),
        (F14, False, "watchdog"),
    ]


def test_a_late_hook_is_not_a_miss() -> None:
    """The hook's report can come after Windows' key state (a hook that ran late): one poll
    that disagrees isn't a miss when the next one agrees."""
    dog, hook, state, got, clock = make_watch()
    state.down.add(F13)  # Windows has it; the hook hasn't run yet
    dog.check()
    hook.press(F13)  # now it has
    clock.now += 0.05
    dog.check()
    assert dog.reinstalls == 0 and [(e.down, e.source) for e in got] == [(True, "hook")]


def test_quick_taps_on_a_silently_removed_hook_are_caught() -> None:
    """The review's case: five 150 ms taps of a hotkey with the hook removed but its thread
    alive. The first is passed on within two polls and the hook re-installed; the new hook
    sees the rest."""
    dog, hook, state, got, clock = make_watch()
    hook.dead = True
    step, t = 0.01, 0.0
    for _ in range(5):
        for down in (True, False):
            if down:
                press(hook, state, F14)
            else:
                release(hook, state, F14)
            for _ in range(15):  # 150 ms down, then 150 ms up
                t += step
                clock.now += step
                if round(t / step) % 5 == 0:  # a poll every 50 ms
                    dog.check()
    presses = [e for e in got if e.down]
    assert len(presses) == 5 and dog.reinstalls == 1
    assert presses[0].source == "watchdog" and presses[1].source == "hook"


def test_a_tap_between_two_polls_is_caught_by_the_pressed_bit() -> None:
    """Shorter than a poll (a macro key's instant tap): only GetAsyncKeyState's "pressed since
    the last call" bit shows it. Confirmed on the next poll, it is passed on as a tap."""
    dog, hook, state, got, clock = make_watch()
    dog.check()  # the first poll reads (and clears) each key's bit
    hook.dead = True
    press(hook, state, F14)
    release(hook, state, F14)
    clock.now += 0.05
    dog.check()
    assert got == [] and dog.reinstalls == 0  # the hook may only be late: one more poll
    clock.now += 0.05
    dog.check()
    assert [(e.vk, e.down, e.source) for e in got] == [
        (F14, True, "watchdog"),
        (F14, False, "watchdog"),
    ]
    assert dog.reinstalls == 1 and not hook.dead


def test_a_tap_the_hook_counted_late_is_not_a_miss() -> None:
    dog, hook, state, got, clock = make_watch()
    dog.check()
    state.pressed.add(F14)  # Windows saw a tap; the hook's report comes after the poll
    clock.now += 0.05
    dog.check()
    hook.press(F14)
    hook.release(F14)
    clock.now += 0.05
    dog.check()
    assert dog.reinstalls == 0 and [e.source for e in got] == ["hook", "hook"]


def test_a_press_the_hook_counts_during_a_poll_is_not_a_tap() -> None:
    """GetAsyncKeyState releases the GIL, so the hook thread can count a press while the
    watchdog reads that key's state: the hook saw it, so it is no missed tap, and a talk key
    still held isn't cancelled by a watchdog down and up."""
    dog, hook, _state, got, clock = make_watch()
    dog.check()

    class Racing(FakeKeyState):
        def read(self, vk: int) -> tuple[bool, bool]:
            found = super().read(vk)  # Windows hasn't updated the key's state yet
            if vk == F13 and not hook.key_filter.presses.get(F13):
                press(hook, self, F13)  # the hook counts it before read() returns
            return found

    dog.key_state = racing = Racing()
    for _ in range(4):  # the key stays held
        clock.now += 0.05
        dog.check()
    assert racing.is_down(F13) and dog.reinstalls == 0
    assert [(e.vk, e.down, e.source) for e in got] == [(F13, True, "hook")]


def test_the_first_poll_ignores_a_pressed_bit_from_before_the_watchdog() -> None:
    dog, _hook, state, got, clock = make_watch()
    state.pressed.add(F13)  # pressed before the client started
    dog.check()
    clock.now += 0.05
    dog.check()
    assert got == [] and dog.reinstalls == 0


def test_a_stopped_hook_thread_is_reinstalled_at_most_once_a_second() -> None:
    dog, hook, _state, _got, clock = make_watch()
    hook.uninstall()
    dog.check()
    assert dog.reinstalls == 1 and hook.alive()
    hook.uninstall()
    clock.now += 0.5
    dog.check()
    assert dog.reinstalls == 1  # too soon
    clock.now += 0.6
    dog.check()
    assert dog.reinstalls == 2


def test_a_hook_that_fails_to_install_is_tried_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dog, hook, _state, _got, clock = make_watch()
    calls: list[int] = []

    def broken() -> None:
        calls.append(1)
        raise OSError("SetWindowsHookEx failed")

    hook.install = broken  # type: ignore[method-assign]
    hook.uninstall()
    dog.check()
    clock.now += 1.1
    dog.check()
    assert len(calls) == 2 and "the key hook didn't install" in caplog.text


def test_the_watchdog_thread_runs_and_stops() -> None:
    dog, hook, _state, _got, _clock = make_watch()
    hook.uninstall()
    dog.start()
    try:
        deadline = time.monotonic() + 5
        while dog.reinstalls == 0:
            assert time.monotonic() < deadline
            time.sleep(0.02)
    finally:
        dog.stop()
    assert hook.alive()


# --- Windows: the real hook -----------------------------------------------------------------

windows = pytest.mark.skipif(sys.platform != "win32", reason="the hook is Windows only")
# SendInput types into whatever window has focus: on the gaming PC (gos-dev shares Clayton's
# desktop, D82) that could be Play. Injection runs only on GitHub's own Windows runners, or
# where someone opted in with GOS_TEST_SENDINPUT=1 on a PC with no game open.
injects = pytest.mark.skipif(
    not (
        sys.platform == "win32"
        and (
            (
                os.environ.get("GITHUB_ACTIONS") == "true"
                and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
            )
            or os.environ.get("GOS_TEST_SENDINPUT") == "1"
        )
    ),
    reason="injects keys: GitHub-hosted Windows runners, or GOS_TEST_SENDINPUT=1",
)


def send_key(vk: int, up: bool) -> int:
    """Inject one key event with SendInput; returns how many events Windows took."""
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MOUSEINPUT(ctypes.Structure):  # sizes the union as Windows expects
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]

    event = INPUT(type=1)  # INPUT_KEYBOARD
    event.u.ki = KEYBDINPUT(wVk=vk, dwFlags=0x0002 if up else 0)  # KEYEVENTF_KEYUP
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    return int(user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)))


def no_input() -> None:
    """SendInput was refused (no interactive desktop): skip, except on GitHub's Windows runners,
    which have one, so CI really exercises the hook."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.fail("SendInput was refused on the CI runner")
    pytest.skip("SendInput is blocked here (no interactive desktop)")


def take(got: queue.Queue[KeyEvent], n: int, timeout: float = 3.0) -> list[KeyEvent]:
    out = []
    deadline = time.monotonic() + timeout
    while len(out) < n and time.monotonic() < deadline:
        try:
            out.append(got.get(timeout=0.05))
        except queue.Empty:
            pass
    return out


def press_and_release(vk: int) -> None:
    if send_key(vk, up=False) != 1:
        no_input()
    time.sleep(0.05)
    send_key(vk, up=True)


def thread_priority(native_id: int) -> int:
    """GetThreadPriority of a thread of this process."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenThread.restype = ctypes.c_void_p
    kernel32.OpenThread.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.GetThreadPriority.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenThread(0x0040, False, native_id)  # THREAD_QUERY_INFORMATION
    assert handle
    try:
        return int(kernel32.GetThreadPriority(handle))
    finally:
        kernel32.CloseHandle(handle)


@windows
def test_the_real_hook_runs_at_high_priority() -> None:
    """Installs the real hook (it only watches F24 and injects nothing)."""
    hook = ptt.WindowsHook(KeyFilter({F24}, lambda event: None))
    hook.install()
    try:
        assert hook.alive()
        native_id = hook._listener.native_id
        assert thread_priority(native_id) == ptt.THREAD_PRIORITY_HIGHEST
    finally:
        hook.uninstall()


@injects
def test_the_real_hook_sees_an_injected_key() -> None:
    got: queue.Queue[KeyEvent] = queue.Queue()
    tick = ptt.tick_count()
    key_filter = KeyFilter({F24}, got.put, tick=tick)
    hook = ptt.WindowsHook(key_filter)
    hook.install()
    try:
        assert hook.alive()
        press_and_release(F24)
        events = take(got, 2)
        assert [(e.vk, e.down) for e in events] == [(F24, True), (F24, False)]
        assert all(e.lag_ms is not None and e.lag_ms < 1000 for e in events)
    finally:
        hook.uninstall()
    assert not hook.alive()


@injects
def test_the_watchdog_catches_a_removed_hook_with_the_real_key_state() -> None:
    """The hook's thread gone: GetAsyncKeyState still sees the injected F24, so the watchdog
    re-installs the hook and passes the press on."""
    got: queue.Queue[KeyEvent] = queue.Queue()
    key_filter = KeyFilter({F24}, got.put)
    hook = ptt.WindowsHook(key_filter)
    hook.install()
    hook.uninstall()  # a dead hook
    state = ptt.AsyncKeyState()
    dog = Watchdog(hook, state, key_filter)
    try:
        if send_key(F24, up=False) != 1:
            no_input()
        assert wait(lambda: state.is_down(F24))
        dog.check()  # the thread is gone: re-installed, but it missed the press
        for _ in range(ptt.MISSES):  # the key is down and the hook says up
            time.sleep(ptt.WATCH_EVERY_S)
            dog.check()
        events = take(got, 1)
        assert [(e.vk, e.down, e.source) for e in events] == [(F24, True, "watchdog")]
        send_key(F24, up=True)
        events = take(got, 1)
        assert [(e.vk, e.down, e.source) for e in events] == [(F24, False, "hook")]
    finally:
        send_key(F24, up=True)
        hook.uninstall()


def hooked(hook: ptt.WindowsHook) -> bool:
    """pynput's listener has its hook (SetWindowsHookEx runs after ``wait()`` returns).
    pynput 1.8.2 (uv.lock) keeps each listener thread's ``SystemHook`` in ``_HOOKS``, before
    its handle is set."""
    from pynput._util.win32 import SystemHook

    listener = hook._listener
    return listener is not None and wait(
        lambda: (
            getattr(SystemHook._HOOKS.get(listener.ident), "_hook", None) is not None
        )
    )


def unhook(hook: ptt.WindowsHook) -> None:
    """Remove the real hook while its thread runs on, as Windows does silently with a hook that
    timed out."""
    from ctypes import wintypes

    from pynput._util.win32 import SystemHook

    assert hooked(hook)
    user32 = ctypes.WinDLL("user32")
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    assert user32.UnhookWindowsHookEx(SystemHook._HOOKS[hook._listener.ident]._hook)


@injects
def test_the_watchdog_catches_a_silently_removed_hook() -> None:
    """The hook removed but its thread alive (the review's case): a 150 ms tap of F24 is
    passed on within two polls and the hook re-installed; the new hook sees the next key."""
    got: queue.Queue[KeyEvent] = queue.Queue()
    key_filter = KeyFilter({F24}, got.put)
    hook = ptt.WindowsHook(key_filter)
    hook.install()
    state = ptt.AsyncKeyState()
    dog = Watchdog(hook, state, key_filter)
    try:
        unhook(hook)
        dog.check()
        if send_key(F24, up=False) != 1:
            no_input()
        assert wait(lambda: state.is_down(F24))
        for _ in range(ptt.MISSES):
            time.sleep(ptt.WATCH_EVERY_S)
            dog.check()
        assert dog.reinstalls == 1 and hooked(hook)  # the new hook is in place
        send_key(F24, up=True)
        events = take(got, 2)
        assert [(e.down, e.source) for e in events] == [
            (True, "watchdog"),
            (False, "hook"),
        ]
        assert dog.reinstalls == 1 and hook.alive()
    finally:
        send_key(F24, up=True)
        hook.uninstall()


def wait(cond: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True
