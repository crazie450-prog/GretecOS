"""WO-5.1, D180: the hook that only enqueues, its watchdog, the talk key (hold and toggle) and the
hotkeys. Linux runs the logic with a fake hook and fake key state; the Windows tests install the
real pynput hook and inject keys with SendInput (F24, which nothing binds)."""

from __future__ import annotations

import ctypes
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


def test_a_segment_closed_elsewhere_resets_the_key() -> None:
    talk = TalkKey("toggle")
    talk.press(1.0)
    talk.release(1.1)
    talk.closed()  # the 30 s cap, or the cancel hotkey
    assert talk.press(40.0) == START
    hold = TalkKey("hold")
    hold.press(1.0)
    hold.closed()
    assert hold.release(31.0) is None  # the cap already sent it


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


def test_a_healthy_hook_is_left_alone() -> None:
    dog, hook, state, got, clock = make_watch()
    state.down.add(F13)
    hook.press(F13)  # the hook sees it as it happens
    for _ in range(10):
        clock.now += 0.1
        dog.check()
    assert dog.reinstalls == 0 and len(got) == 1


def test_a_press_the_hook_missed_is_passed_on_and_the_hook_reinstalled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dog, hook, state, got, clock = make_watch()
    hook.dead = True  # Windows removed the hook after a timeout: it sees nothing now
    state.down.add(F13)
    hook.press(F13)
    dog.check()
    clock.now += 0.2
    dog.check()
    assert got == [] and dog.reinstalls == 0  # under 300 ms: not yet
    clock.now += 0.15
    dog.check()
    assert dog.reinstalls == 1 and hook.installed == 2 and not hook.dead
    assert [(e.vk, e.down, e.source) for e in got] == [(F13, True, "watchdog")]
    assert [getattr(r, "why", None) for r in caplog.records] == ["it missed F13 down"]
    # the new hook sees the release itself
    state.down.discard(F13)
    hook.release(F13)
    clock.now += 1
    dog.check()
    assert [(e.down, e.source) for e in got] == [(True, "watchdog"), (False, "hook")]


def test_a_missed_release_is_passed_on_too() -> None:
    dog, hook, state, got, clock = make_watch()
    state.down.add(F14)
    hook.press(F14)
    hook.dead = True
    state.down.discard(F14)
    hook.release(F14)  # lost
    dog.check()
    clock.now += 0.31
    dog.check()
    assert [(e.vk, e.down, e.source) for e in got] == [
        (F14, True, "hook"),
        (F14, False, "watchdog"),
    ]


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
        pytest.skip("SendInput is blocked here (no interactive desktop)")
    time.sleep(0.05)
    send_key(vk, up=True)


@windows
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


@windows
def test_the_watchdog_catches_a_removed_hook_with_the_real_key_state() -> None:
    """The hook gone (as when Windows removes one that timed out): GetAsyncKeyState still sees
    the injected F24, so the watchdog re-installs the hook and passes the press on."""
    got: queue.Queue[KeyEvent] = queue.Queue()
    key_filter = KeyFilter({F24}, got.put)
    hook = ptt.WindowsHook(key_filter)
    hook.install()
    hook.uninstall()  # a dead hook
    state = ptt.AsyncKeyState()
    dog = Watchdog(hook, state, key_filter)
    try:
        if send_key(F24, up=False) != 1:
            pytest.skip("SendInput is blocked here (no interactive desktop)")
        assert wait(lambda: state.is_down(F24))
        dog.check()  # the thread is gone: re-installed, but it missed the press
        dog.check()  # the key is down and the hook says up: the clock starts
        time.sleep(ptt.MISMATCH_S + 0.05)
        dog.check()
        events = take(got, 1)
        assert [(e.vk, e.down, e.source) for e in events] == [(F24, True, "watchdog")]
        send_key(F24, up=True)
        events = take(got, 1)
        assert [(e.vk, e.down, e.source) for e in events] == [(F24, False, "hook")]
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
