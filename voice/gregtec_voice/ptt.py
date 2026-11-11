"""The talk key and the global hotkeys: a low-level hook that only enqueues, and its watchdog
(plan 8 WO-5.1, D180).

**The hook** (:class:`WindowsHook`) is pynput's ``WH_KEYBOARD_LL`` listener, on its own thread
with its own message loop, raised to ``THREAD_PRIORITY_HIGHEST`` so a PC kept busy by GTNH doesn't
starve it into the hook timeout. Its event filter is :class:`KeyFilter`, which is all the hook
does: for the four watched keys it notes the key's state and queues a :class:`KeyEvent`, for
every other key it does nothing; either way it returns at once and the key goes on to the game as
usual (nothing is suppressed, so the keys must be ones GTNH and Discord don't bind, QA:V8).
pynput's own key translation never runs. Windows calls a low-level hook for every key press on
the desktop, so it works with GTNH focused in fullscreen and borderless alike [D§2.5].

**The watchdog** (:class:`Watchdog`): Windows silently removes a low-level hook that doesn't
return within its timeout (1 s on Windows 10 1709+) [D§2.5], and nothing tells the program. A
working hook always reports a key before Windows updates the key's state (``GetAsyncKeyState``,
which no hook can lose), so every 50 ms the watchdog compares the two: a watched key whose state
differs from the hook's last report on two polls in a row was missed (one poll alone can catch a
hook that is only late). The watchdog then re-installs the hook and passes the missed press or
release on, time-stamped at the first poll that saw it, so it still counts. A whole press between
two polls (a tap, or a macro key's instant one) is caught, as a best effort, by
``GetAsyncKeyState``'s "pressed since the last call" bit when the hook counted no press by the
next poll: Windows calls that bit unreliable (any program reading the key clears it), so such a
tap can still go unseen. A hook thread that died is re-installed the same way. Re-installs are at
least 1 s apart. Polling a key's state is cheap and, unlike a heartbeat of injected keys, never
counts as input (the away timer, D76).

**The talk key** (:class:`TalkKey`), D180: *hold* talks while the key is down; a press shorter
than 0.3 s only stops speech (barge-in) and sends nothing. *Toggle* starts on one press and
ends on the next; after the 30 s cap sent a toggle segment, the next press only ends the turn.
Auto-repeat and duplicate events are ignored. **Hotkeys** (:class:`Hotkeys`) act on the press
only.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from gregtec_voice.keys import BY_VK

log = logging.getLogger(__name__)

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
DOWN_MESSAGES = frozenset({WM_KEYDOWN, WM_SYSKEYDOWN})
UP_MESSAGES = frozenset({WM_KEYUP, WM_SYSKEYUP})
WATCH_EVERY_S = 0.05
MISSES = (
    2  # polls in a row on which a key's state and the hook's report disagree: a miss
)
REINSTALL_GAP_S = 1.0
THREAD_PRIORITY_HIGHEST = 2
THREAD_SET_INFORMATION = 0x0020
MIN_HOLD_S = 0.3  # hold mode: a shorter press only stops speech
HOLD, TOGGLE = "hold", "toggle"
START, STOP, CANCEL = "start", "stop", "cancel"


@dataclass(frozen=True)
class KeyEvent:
    vk: int
    down: bool
    t: float  # monotonic, when the hook (or the watchdog) saw it
    source: str = "hook"  # "hook", or "watchdog" for a transition the hook missed
    lag_ms: int | None = None  # Windows: from the key event's own timestamp to the hook


def key_label(vk: int) -> str:
    key = BY_VK.get(vk)
    return key.label if key else f"VK 0x{vk:02X}"


class KeyFilter:
    """What the hook runs for each key event, on the hook thread: only notes and queues."""

    def __init__(
        self,
        watched: Iterable[int],
        put: Callable[[KeyEvent], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        tick: Callable[[], int] | None = None,
    ) -> None:
        self.watched = frozenset(watched)
        self.put = put
        self.clock = clock
        self.tick = tick  # Windows: GetTickCount, to compare with the event's own time
        self.seen: dict[
            int, bool
        ] = {}  # the last state the hook reported per watched key
        self.presses: dict[
            int, int
        ] = {}  # key-down messages the hook saw per watched key

    def __call__(self, msg: int, vk: int, event_ms: int | None = None) -> None:
        if vk not in self.watched:
            return
        if msg in DOWN_MESSAGES:
            down = True
        elif msg in UP_MESSAGES:
            down = False
        else:
            return
        lag = None
        if self.tick is not None and event_ms is not None:
            lag = (
                self.tick() - event_ms
            ) & 0xFFFF_FFFF  # GetTickCount wraps at 2^32 ms
        if down:
            self.presses[vk] = self.presses.get(vk, 0) + 1
        self.seen[vk] = down
        self.put(KeyEvent(vk, down, self.clock(), "hook", lag))


class Hook(Protocol):
    def install(self) -> None: ...

    def uninstall(self) -> None: ...

    def alive(self) -> bool: ...


class KeyState(Protocol):
    """A key's physical state, read without any hook."""

    def read(self, vk: int) -> tuple[bool, bool]:
        """(down now, pressed since the last read)."""
        ...


def raise_priority(native_id: int) -> bool:
    """Windows: run thread ``native_id`` at ``THREAD_PRIORITY_HIGHEST`` (the hook's thread, so a
    PC kept busy by GTNH doesn't make it miss the hook timeout). False if Windows refused."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenThread.restype = ctypes.c_void_p
    kernel32.OpenThread.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenThread(THREAD_SET_INFORMATION, False, native_id)
    if not handle:
        return False
    try:
        return bool(kernel32.SetThreadPriority(handle, THREAD_PRIORITY_HIGHEST))
    finally:
        kernel32.CloseHandle(handle)


class WindowsHook:
    """pynput's low-level keyboard listener (Windows) running :class:`KeyFilter`."""

    def __init__(self, key_filter: KeyFilter) -> None:
        self.key_filter = key_filter
        self._listener: Any = None

    def install(self) -> None:
        from pynput import keyboard  # Windows only (D180)

        key_filter = self.key_filter

        def event_filter(msg: int, data: Any) -> bool:  # noqa: ANN401 - KBDLLHOOKSTRUCT
            key_filter(msg, int(data.vkCode), int(data.time))
            return False  # no translation by pynput; the key still reaches the game

        listener = keyboard.Listener(win32_event_filter=event_filter)
        listener.start()
        listener.wait()
        if not raise_priority(listener.native_id):
            log.warning("can't raise the key hook thread's priority")
        self._listener = listener

    def uninstall(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()
            listener.join(timeout=2.0)

    def alive(self) -> bool:
        return self._listener is not None and self._listener.is_alive()


class AsyncKeyState:
    """Windows' ``GetAsyncKeyState``: whether a key is down right now, and whether it was
    pressed since the last call (a bit any program's call clears: a best effort only)."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("GetAsyncKeyState is Windows only")
        user32 = ctypes.WinDLL("user32")
        self._get = user32.GetAsyncKeyState
        self._get.argtypes = [ctypes.c_int]
        self._get.restype = ctypes.c_short

    def read(self, vk: int) -> tuple[bool, bool]:
        state = self._get(vk)
        return bool(state & 0x8000), bool(state & 0x0001)

    def is_down(self, vk: int) -> bool:
        return self.read(vk)[0]


def tick_count() -> Callable[[], int] | None:
    """Windows' ``GetTickCount`` (the clock of a hook event's ``time``); None elsewhere."""
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32")
    get = kernel32.GetTickCount
    get.restype = ctypes.c_uint32
    return lambda: int(get())


@dataclass
class FakeHook:
    """Tests (and Linux): :meth:`press` and :meth:`release` are keys going through the hook."""

    key_filter: KeyFilter
    installed: int = 0
    dead: bool = False  # True: installed but Windows removed it, so it sees nothing
    _alive: bool = field(default=False, repr=False)

    def install(self) -> None:
        self.installed += 1
        self._alive = True
        self.dead = False

    def uninstall(self) -> None:
        self._alive = False

    def alive(self) -> bool:
        return self._alive

    def press(self, vk: int) -> None:
        if self._alive and not self.dead:
            self.key_filter(WM_KEYDOWN, vk)

    def release(self, vk: int) -> None:
        if self._alive and not self.dead:
            self.key_filter(WM_KEYUP, vk)


@dataclass
class FakeKeyState:
    down: set[int] = field(default_factory=set)
    pressed: set[int] = field(default_factory=set)  # pressed since the last read

    def read(self, vk: int) -> tuple[bool, bool]:
        was = vk in self.pressed
        self.pressed.discard(vk)
        return vk in self.down, was

    def is_down(self, vk: int) -> bool:
        return vk in self.down


class Watchdog:
    """Re-installs the hook when it died or missed a watched key (see the module docstring)."""

    def __init__(
        self,
        hook: Hook,
        key_state: KeyState,
        key_filter: KeyFilter,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hook = hook
        self.key_state = key_state
        self.key_filter = key_filter
        self.clock = clock
        self.reinstalls = 0
        self._first: dict[int, float] = {}  # the first poll that disagreed, per key
        self._misses: dict[int, int] = {}  # polls in a row that disagreed
        self._counted: dict[int, int] = {}  # the hook's press count at the last poll
        self._tapped: dict[
            int, int
        ] = {}  # an uncounted press between polls: the count then
        self._last_reinstall = -REINSTALL_GAP_S
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> None:
        """One poll: re-install a dead hook; pass on a press or release the hook missed."""
        now = self.clock()
        if not self.hook.alive():
            self._reinstall(now, "its thread stopped")
            return
        for vk in sorted(self.key_filter.watched):
            # The count first: reading the key's state lets the hook thread run, and a press it
            # counts meanwhile (before Windows sets the "pressed" bit) must show as counted
            # since this poll at the next one, not as a tap it missed.
            presses = self.key_filter.presses.get(vk, 0)
            down, pressed = self.key_state.read(vk)
            counted, self._counted[vk] = self._counted.get(vk), presses
            tapped = self._tapped.pop(vk, None)
            if down != self.key_filter.seen.get(vk, False):
                first = self._first.setdefault(vk, now)
                self._misses[vk] = self._misses.get(vk, 0) + 1
                if self._misses[vk] < MISSES:
                    continue  # the hook may only be late
                del self._first[vk], self._misses[vk]
                self._reinstall(
                    now, f"it missed {key_label(vk)} {'down' if down else 'up'}"
                )
                self.key_filter.seen[vk] = down
                self.key_filter.put(KeyEvent(vk, down, first, "watchdog"))
                continue
            self._first.pop(vk, None)
            self._misses.pop(vk, None)
            if tapped is not None and presses == tapped:
                # a whole press between two polls, and the hook hasn't counted it since
                self._reinstall(now, f"it missed a tap of {key_label(vk)}")
                self.key_filter.put(KeyEvent(vk, True, now, "watchdog"))
                self.key_filter.put(KeyEvent(vk, False, now, "watchdog"))
            elif pressed and counted is not None and presses == counted:
                # a press the hook hasn't counted: the next poll tells a miss from a late hook
                self._tapped[vk] = presses

    def _reinstall(self, now: float, why: str) -> None:
        """At most once a second; a transition missed meanwhile is still passed on."""
        if now - self._last_reinstall < REINSTALL_GAP_S:
            return
        self._last_reinstall = now
        self.reinstalls += 1
        log.warning(
            "re-installing the key hook", extra={"why": why, "count": self.reinstalls}
        )
        self.hook.uninstall()
        try:
            self.hook.install()
        except Exception:  # try again on the next pass
            log.exception("the key hook didn't install")

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="gos-voice-watchdog", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(WATCH_EVERY_S):
            try:
                self.check()
            except Exception:  # the watchdog must keep running
                log.exception("the key watchdog failed")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


class TalkKey:
    """Talk-key transitions -> START, STOP (send the segment) or CANCEL (drop it), D180."""

    def __init__(self, mode: str, *, min_hold_s: float = MIN_HOLD_S) -> None:
        if mode not in (HOLD, TOGGLE):
            raise ValueError(f"talk mode must be {HOLD} or {TOGGLE}")
        self.mode = mode
        self.min_hold_s = min_hold_s
        self.down = False
        self.open_since: float | None = None  # when it opened the segment still open
        self._end_press = (
            False  # toggle: the cap sent the segment; the next press ends it
        )

    def press(self, t: float) -> str | None:
        if self.down:
            return None  # auto-repeat, or the watchdog's copy of a press
        self.down = True
        if self.mode == HOLD:
            self.open_since = t
            return START
        if self.open_since is not None:
            self.open_since = None
            return STOP
        if self._end_press:
            self._end_press = False
            return None  # meant to end the segment the 30 s cap already sent
        self.open_since = t
        return START

    def release(self, t: float) -> str | None:
        if not self.down:
            return None
        self.down = False
        if self.mode == TOGGLE or self.open_since is None:
            return None
        held, self.open_since = t - self.open_since, None
        return STOP if held >= self.min_hold_s else CANCEL

    def capped(self, started: float) -> None:
        """The 30 s cap sent the segment this key opened at ``started``. Hold: its release does
        nothing. Toggle: the next press only ends the turn, as Clayton meant it to."""
        if self.open_since != started:
            return  # a newer segment of this key, or one it didn't open
        self.open_since = None
        if self.mode == TOGGLE:
            self._end_press = True

    def dropped(self) -> None:
        """A hotkey dropped the segment being recorded: the key starts afresh."""
        self.open_since = None
        self._end_press = False


class Hotkeys:
    """Hotkey presses -> their names; repeats and releases do nothing."""

    def __init__(self, by_vk: dict[int, str]) -> None:
        self.by_vk = by_vk
        self._down: set[int] = set()

    def event(self, vk: int, down: bool) -> str | None:
        if vk not in self.by_vk:
            return None
        if not down:
            self._down.discard(vk)
            return None
        if vk in self._down:
            return None
        self._down.add(vk)
        return self.by_vk[vk]
