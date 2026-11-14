"""Is Clayton at the PC? Input idle time for alert routing (WO-9.2, plan 7.12, D46, D76).

D76: he is **away** when the PC has had no keyboard or mouse input for :data:`AWAY_S` (10
minutes). The core's alert router (:mod:`gregtec.alerts.router`) and the Discord bot's
"GOS offline" watch (:mod:`gregtec.discordbot.alerts`) each ask an :class:`IdleSource`.

- :class:`WindowsIdle` (Windows only): ``GetLastInputInfo``, the session's last keyboard or
  mouse input, against ``GetTickCount``. Both are milliseconds since boot in 32 bits, so the
  difference is taken modulo 2**32 (it wraps every 49.7 days). It reads the input of the session
  the process runs in: GOS's processes run in Clayton's own logon session (the supervisor starts
  at his login, D116), windowless or not (VERIFY V35).
- :class:`NoIdle` everywhere else, and when Windows can't say: ``idle_s()`` is None, which the
  router reads as **not away**. So an unknown presence never sends an away-only DM (it errs on
  the quiet side); voice and the dashboard carry the alert instead.
- :class:`FakeIdle` for tests: the last input is set by hand on a fake clock.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger("gregtec.presence")

AWAY_S = 600.0  # D76: no keyboard or mouse input for 10 minutes
_WRAP = 1 << 32


class IdleSource(Protocol):
    def idle_s(self) -> float | None:
        """Seconds since the last keyboard or mouse input, or None when unknown."""
        ...


def is_away(idle: float | None, away_s: float = AWAY_S) -> bool:
    """D76's "away": no input for ``away_s``. Unknown (None) is not away."""
    return idle is not None and idle >= away_s


class NoIdle:
    """No input-idle source here (not Windows): always unknown."""

    def idle_s(self) -> float | None:
        return None


class FakeIdle:
    """Input idle on a fake clock: :meth:`touch` is a key press or a mouse move."""

    def __init__(self, clock: Callable[[], float], last: float | None = None) -> None:
        self.clock = clock
        self.last = clock() if last is None else last
        self.known = True

    def touch(self, at: float | None = None) -> None:
        self.last = self.clock() if at is None else at

    def idle_s(self) -> float | None:
        if not self.known:
            return None
        return max(0.0, self.clock() - self.last)


def idle_ms(tick_now: int, last_input: int) -> int:
    """Milliseconds between two 32-bit tick counts, across one wrap of the counter."""
    return (tick_now - last_input) % _WRAP


class WindowsIdle:
    """``GetLastInputInfo`` (user32) against ``GetTickCount`` (kernel32). Windows only."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        self._info = LastInputInfo()
        self._info.cbSize = ctypes.sizeof(LastInputInfo)
        windll = ctypes.windll  # type: ignore[attr-defined]
        self._get_last = windll.user32.GetLastInputInfo
        self._get_last.argtypes = [ctypes.POINTER(LastInputInfo)]
        self._get_last.restype = wintypes.BOOL
        self._tick = windll.kernel32.GetTickCount
        self._tick.restype = wintypes.DWORD
        self._ref = ctypes.byref(self._info)
        self._warned = False

    def idle_s(self) -> float | None:
        if not self._get_last(self._ref):
            if not self._warned:
                self._warned = True
                log.warning("GetLastInputInfo failed: presence unknown (not away)")
            return None
        return idle_ms(int(self._tick()), int(self._info.dwTime)) / 1000.0


def system_idle() -> IdleSource:
    """This PC's input-idle source: :class:`WindowsIdle` on Windows, else :class:`NoIdle`."""
    if sys.platform == "win32":
        try:
            return WindowsIdle()
        except (OSError, AttributeError) as exc:
            log.warning("no input-idle source", extra={"why": str(exc)[:200]})
    return NoIdle()
