"""Paused-state detection (V29, plan 5.6 AFK, WO-6.1, D258).

What the game actually writes, from Minecraft 1.7.10's ``IntegratedServer.tick`` [B§2.2, B§6.1]:

- **Pausing.** The tick that first sees the game paused (the Esc menu, or any screen that pauses,
  in single-player and not opened to LAN) logs ``Saving and pausing game...`` on the Server
  thread, saves every player and world (``Saving chunks for level '<world>'/<dim>`` lines,
  level.dat, BetterQuesting's progress, the stats), then stops ticking. Nothing else runs on the
  server while it is paused: no autosave, no chat, no OpenComputers [F§3].
- **Unpausing writes nothing at once.** Vanilla logs no line when the menu closes. The first
  signs are the server's own: any Server-thread log line that isn't part of that save (a login,
  a command, a warning) or, at the latest, the next autosave 900 ticks (45 s) later, which writes
  level.dat again (GTNH ships Hodgepodge's ``autoSaveInterval`` = 900) [B§6.1].

So the tracker says **paused** from the pause line on, and **not paused** at the first
Server-thread line of latest.log after the pause save (read more than :data:`LINE_GRACE_S` after
the pause line; mods' lines in the FML log don't count, since they log from their save handlers),
or when level.dat is written again more than
:data:`SAVE_GRACE_S` after the pause line (the pause's own save lands within a few seconds, through
the threaded IO queue), or when the world closes (``Stopping server``: then unknown). Before the
first pause line it says ``None`` (unknown), as the dashboard's ``game.paused`` did before WO-6.1.
A pause line while it already thinks the game is paused (an unpause it missed) restarts
``since``. The clock is injected, so tests run without waiting.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

SAVE_GRACE_S = (
    10.0  # level.dat writes this soon after the pause line are the pause's own save
)


LINE_GRACE_S = (
    3.0  # Server-thread lines read this soon after the pause line: its own save
)


@dataclass(frozen=True)
class Change:
    paused: bool | None
    since: float  # wall clock of the change
    why: str


class PauseTracker:
    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.paused: bool | None = None
        self.since: float | None = None

    def _set(self, paused: bool | None, why: str) -> Change | None:
        now = self.clock()
        if paused == self.paused and not (paused and why == "pause line"):
            return None
        self.paused, self.since = paused, now
        return Change(paused, now, why)

    def pause_line(self) -> Change | None:
        return self._set(True, "pause line")

    def server_line(self) -> Change | None:
        """A Server-thread line that isn't part of a save: the server is ticking."""
        if self.paused is not True or self.since is None:
            return None
        if self.clock() - self.since < LINE_GRACE_S:
            return None  # read with the pause line: the pause's own save
        return self._set(False, "the server logged again")

    def world_saved(self) -> Change | None:
        """level.dat was written (an autosave, a pause save or a quit save)."""
        if self.paused is not True or self.since is None:
            return None
        if self.clock() - self.since < SAVE_GRACE_S:
            return None  # the pause's own save
        return self._set(False, "an autosave ran")

    def world_closed(self) -> Change | None:
        return self._set(None, "the world closed")
