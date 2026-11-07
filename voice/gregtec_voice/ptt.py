"""The talk key and the global hotkeys: a low-level hook that only enqueues, and its watchdog
(plan 8 WO-5.1, D180).

**The hook** (:class:`WindowsHook`) is pynput's ``WH_KEYBOARD_LL`` listener, on its own thread
with its own message loop. Its event filter is :class:`KeyFilter`, which is all the hook does:
for the four watched keys it notes the key's state and queues a :class:`KeyEvent`, for every
other key it does nothing; either way it returns at once and the key goes on to the game as
usual (nothing is suppressed, so the keys must be ones GTNH and Discord don't bind, QA:V8).
pynput's own key translation never runs. Windows calls a low-level hook for every key press on
the desktop, so it works with GTNH focused in fullscreen and borderless alike [D§2.5].

**The watchdog** (:class:`Watchdog`): Windows silently removes a low-level hook that doesn't
return within its timeout (1 s on Windows 10 1709+) [D§2.5], and nothing tells the program. So
every 100 ms the watchdog compares each watched key's physical state (``GetAsyncKeyState``,
which no hook can lose) with the last state the hook reported. A difference that lasts 300 ms
means the hook missed a press or a release: the watchdog re-installs the hook and passes the
missed transition on, so the press still counts, 0.3 s late. A hook thread that died is
re-installed the same way. Re-installs are at least 1 s apart. Polling a key's state is cheap
and, unlike a heartbeat of injected keys, never counts as input (the away timer, D76).

**The talk key** (:class:`TalkKey`), D180: *hold* talks while the key is down; a press shorter
than 0.3 s only stops speech (barge-in) and sends nothing. *Toggle* starts on one press and
ends on the next. Auto-repeat and duplicate events are ignored. **Hotkeys** (:class:`Hotkeys`)
act on the press only.
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
WATCH_EVERY_S = 0.1
MISMATCH_S = (
    0.3  # the hook and the key's physical state disagree this long: it missed one
)
REINSTALL_GAP_S = 1.0
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
        self.seen[vk] = down
        self.put(KeyEvent(vk, down, self.clock(), "hook", lag))


class Hook(Protocol):
    def install(self) -> None: ...

    def uninstall(self) -> None: ...

    def alive(self) -> bool: ...


class KeyState(Protocol):
    """A key's physical state, read without any hook."""

    def is_down(self, vk: int) -> bool: ...


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
        self._listener = listener

    def uninstall(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()
            listener.join(timeout=2.0)

    def alive(self) -> bool:
        return self._listener is not None and self._listener.is_alive()


class AsyncKeyState:
    """Windows' ``GetAsyncKeyState``: whether a key is down right now."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("GetAsyncKeyState is Windows only")
        user32 = ctypes.WinDLL("user32")
        self._get = user32.GetAsyncKeyState
        self._get.argtypes = [ctypes.c_int]
        self._get.restype = ctypes.c_short

    def is_down(self, vk: int) -> bool:
        return bool(self._get(vk) & 0x8000)


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
        mismatch_s: float = MISMATCH_S,
    ) -> None:
        self.hook = hook
        self.key_state = key_state
        self.key_filter = key_filter
        self.clock = clock
        self.mismatch_s = mismatch_s
        self.reinstalls = 0
        self._since: dict[int, float] = {}
        self._last_reinstall = -REINSTALL_GAP_S
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> None:
        """One pass: re-install a dead hook; pass on a transition the hook missed."""
        now = self.clock()
        if not self.hook.alive():
            self._reinstall(now, "its thread stopped")
            return
        for vk in sorted(self.key_filter.watched):
            physical = self.key_state.is_down(vk)
            if physical == self.key_filter.seen.get(vk, False):
                self._since.pop(vk, None)
                continue
            first = self._since.setdefault(vk, now)
            if now - first < self.mismatch_s:
                continue
            self._since.pop(vk, None)
            self._reinstall(
                now, f"it missed {key_label(vk)} {'down' if physical else 'up'}"
            )
            self.key_filter.seen[vk] = physical
            self.key_filter.put(KeyEvent(vk, physical, now, "watchdog"))

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
        self.open_since: float | None = None  # a segment this key opened

    def press(self, t: float) -> str | None:
        if self.down:
            return None  # auto-repeat, or the watchdog's copy of a press
        self.down = True
        if self.mode == HOLD or self.open_since is None:
            self.open_since = t
            return START
        self.open_since = None
        return STOP

    def release(self, t: float) -> str | None:
        if not self.down:
            return None
        self.down = False
        if self.mode == TOGGLE or self.open_since is None:
            return None
        held, self.open_since = t - self.open_since, None
        return STOP if held >= self.min_hold_s else CANCEL

    def closed(self) -> None:
        """The segment ended some other way (the length cap, the cancel hotkey)."""
        self.open_since = None


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
