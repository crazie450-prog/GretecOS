"""When the curator runs (plan 6.5, 6.6) [QA:A9]: the core calls :meth:`Maintenance.tick` once a
minute (``gregtec.api.app``) and :meth:`Maintenance.session_end` when it stops.

- **Batches of agent writes** are committed to the vault's git every 10 minutes and at session
  end (plan 6.5), so any change can be undone. Until WO-2.4 has sessions, the core's own stop
  is the session end; WO-2.4 calls ``session_end`` at the end of each deep-lane session too.
- **Idle maintenance** (:mod:`gregtec.vault.curator`) runs once a day (local date, recorded in
  ``kv`` as ``maintenance.last_day``), at the first minute when the game is closed and there
  has been no keyboard or mouse input for ``[maintenance] idle_minutes`` (30), never at a fixed
  hour. **The game is closed** when no GTNH client runs (Play, Export or Lab: Prism puts the
  instance folder on the client's Java command line, :func:`gregtec.prism.running_clients`), no
  launch is starting (Play's pre-launch mark) and no export holds the instance lock. If the
  processes can't be listed, the game counts as running. **Input**: Windows' ``GetLastInputInfo``
  for this session (:func:`seconds_since_input`); elsewhere it is unknown, which never counts
  as idle. Both are injectable, for tests.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from gregtec import config, locks, playhooks, prism
from gregtec.store import Store
from gregtec.vault.curator import FAILED, Curator, JobResult, commit_agent_writes

log = logging.getLogger(__name__)

COMMIT_EVERY_S = 600.0  # plan 6.5: batches every 10 minutes
TICK_S = 60.0
KV_LAST_DAY = "maintenance.last_day"
KV_LAST_RUN = "maintenance.last_run"


def seconds_since_input() -> float | None:
    """Seconds since this Windows session's last keyboard or mouse input, or None when it can't
    be told (not Windows, or the call failed)."""
    if sys.platform != "win32":
        return None

    class LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]

    info = LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    kernel32.GetTickCount.restype = ctypes.c_uint32
    now = kernel32.GetTickCount()
    # Both are 32-bit millisecond tick counts that wrap after 49.7 days.
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


def game_running(cfg: config.Config) -> bool:
    """Whether a GTNH client runs or is starting, or an export holds the lock. Raises OSError
    when the processes can't be listed."""
    if locks.held(cfg.gos_home) is not None:
        return True
    if playhooks.launching(cfg.gos_home) is not None:
        return True
    return bool(prism.running_clients(cfg))


@dataclass
class Maintenance:
    curator: Curator
    store: Store
    idle_s: Callable[[], float | None] = seconds_since_input
    game: Callable[[], bool] = lambda: True
    idle_minutes: int = 30
    now: Callable[[], dt.datetime] = dt.datetime.now
    monotonic: Callable[[], float] = time.monotonic
    commit_every_s: float = COMMIT_EVERY_S
    last_commit: float | None = None
    last_wait: str = "not checked yet"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- the idle rule

    def waiting_for(self) -> str | None:
        """Why idle maintenance can't run now, or None when it may."""
        idle = self.idle_s()
        if idle is None:
            return "keyboard and mouse input can't be read here"
        if idle < self.idle_minutes * 60:
            return f"input {idle / 60:.0f} min ago (waits for {self.idle_minutes} min)"
        try:
            if self.game():
                return "the game is running"
        except OSError as exc:
            return f"can't tell whether the game runs ({exc})"
        return None

    # --- kv

    def _kv(self, key: str) -> str | None:
        rows = self.store.rows("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def _set_kv(self, key: str, value: str) -> None:
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO "
                "UPDATE SET value = excluded.value, upd = excluded.upd",
                (key, value, int(time.time())),
            )

    def last_run(self) -> dict[str, Any] | None:
        raw = self._kv(KV_LAST_RUN)
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    # --- the jobs

    def commit(self, why: str) -> str | None:
        """Commit the agents' writes now; errors are logged (the next batch tries again)."""
        try:
            sha = commit_agent_writes(self.curator.vault, f"GOS: {why}")
        except (RuntimeError, OSError) as exc:
            log.warning("vault commit failed", extra={"why": why, "error": str(exc)})
            return None
        if sha:
            log.info("vault commit", extra={"why": why, "commit": sha})
        return sha

    def run_daily(self, today: dt.date) -> list[JobResult]:
        results = self.curator.run(today)
        record = {
            "day": today.isoformat(),
            "ts": int(time.time()),
            "jobs": [r.__dict__ for r in results],
        }
        self._set_kv(KV_LAST_DAY, today.isoformat())
        self._set_kv(KV_LAST_RUN, json.dumps(record))
        return results

    def tick(self) -> list[JobResult] | None:
        """One minute's check: the 10-minute commit batch, then the daily run if it is due and
        allowed. Returns the daily run's results when it ran."""
        with self._lock:
            mono = self.monotonic()
            if self.last_commit is None:
                self.last_commit = mono
            elif mono - self.last_commit >= self.commit_every_s:
                self.last_commit = mono
                if (self.curator.vault / ".git").is_dir():
                    self.commit("agent writes")
            today = self.now().date()
            if self._kv(KV_LAST_DAY) == today.isoformat():
                self.last_wait = f"done for {today}"
                return None
            why = self.waiting_for()
            if why is not None:
                self.last_wait = why
                return None
            self.last_wait = f"running for {today}"
            results = self.run_daily(today)
            self.last_wait = f"done for {today}"
            return results

    def session_end(self) -> str | None:
        """Commit the session's writes (plan 6.5: at session end)."""
        with self._lock:
            if not (self.curator.vault / ".git").is_dir():
                return None
            return self.commit("session end")

    def health(self) -> tuple[str, str]:
        """(status, detail) for the dashboard's health strip."""
        last = self.last_run()
        if last is None:
            return "ok", f"not run yet; {self.last_wait}"
        failed = [j["job"] for j in last.get("jobs", []) if j.get("status") == FAILED]
        detail = f"last run {last.get('day')}; {self.last_wait}"
        if failed:
            return "warn", f"{detail}; failed: {', '.join(failed)}"
        return "ok", detail
