"""The cost watch: caps and heads-up alerts from the ``llm_usage`` meter (WO-11.2; plan 4.4
"Billing", 10 "Ops"; D16, D47, D48, D118, D156, D334).

The D48 credit alerts (80% and 100% of the cycle's included credits, with their buttons and the
no-AI pause) stay :class:`gregtec.agents.no_ai.CreditWatch`'s. This adds, read from the same meter:

- **heads-ups** at ``[budget] notice_pct`` (default ``[50]``) of the cycle's included credits:
  ``info``, no buttons, nothing paused; key ``cost:notice:<pct>``, once per cycle;
- **the day cap** ``[budget] day_max_usd``: today's spend (local midnight to midnight) reaching
  it is a ``warn`` alert, key ``cost:day``, once per day. It only tells Clayton; nothing is paused
  or downgraded (D16);
- **the task cap** ``[budget] task_max_usd``: a task whose metered spend today reached it is a
  ``warn`` alert, key ``cost:task:<task>``, once per task. The deep lane already paused that
  task and asked (D156); the alert carries the question to Discord while he is away.

:meth:`CostWatch.alerts` is the router's ``costs`` source (kind ``cost``, routed like the credit
alert: dashboard, and a DM whatever his presence, D334); :meth:`CostWatch.caps` is the Costs
view's "Caps" card (``GET /api/usage`` ``caps``). Every number is the meter's (D118).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from gregtec.config import Config
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

TOP_TASKS = 5


@dataclass(frozen=True)
class CostWatch:
    store: Store
    monthly_usd: float
    alerts_pct: tuple[int, ...]
    notice_pct: tuple[int, ...]
    task_max_usd: float
    day_max_usd: float
    cycle_day: int = 1
    clock: Callable[[], float] = time.time

    @classmethod
    def of(cls, store: Store, cfg: Config) -> CostWatch:
        return cls(
            store,
            cfg.monthly_included_usd,
            cfg.credit_alerts_pct,
            cfg.notice_pct,
            cfg.task_max_usd,
            cfg.day_max_usd,
            cfg.cycle_day,
        )

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock()).astimezone()

    def _today(self) -> tuple[datetime, int, int]:
        local = self._now().replace(tzinfo=None)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        # A naive datetime's astimezone() reads it as local time: a DST day is 23 or 25 h.
        start = midnight.astimezone()
        end = (midnight + timedelta(days=1)).astimezone()
        return start, int(start.timestamp()), int(end.timestamp())

    def caps(self) -> dict[str, Any]:
        """The Costs view's caps card: today against the day cap, today's biggest tasks
        against the task cap, the cycle against the heads-ups."""
        start, lo, hi = self._today()
        rows = self.store.rows(
            "SELECT COALESCE(SUM(usd), 0) AS usd, COUNT(*) AS calls FROM llm_usage "
            "WHERE ts >= ? AND ts < ?",
            (lo, hi),
        )
        spent = round(float(rows[0]["usd"]), 6)
        tasks = self.store.rows(
            "SELECT task, SUM(usd) AS usd, COUNT(*) AS calls FROM llm_usage "
            "WHERE ts >= ? AND ts < ? AND task IS NOT NULL AND task != '' "
            "GROUP BY task ORDER BY usd DESC, task LIMIT ?",
            (lo, hi, TOP_TASKS),
        )
        reached = self.store.rows(
            "SELECT COUNT(*) AS n FROM (SELECT task FROM llm_usage WHERE ts >= ? AND ts < ? "
            "AND task IS NOT NULL AND task != '' GROUP BY task HAVING SUM(usd) >= ?)",
            (lo, hi, self.task_max_usd),
        )
        cycle = self._meter().cycle(self._now())
        return {
            "day": {
                "start": start.isoformat(),
                "spent_usd": spent,
                "calls": int(rows[0]["calls"]),
                "cap_usd": self.day_max_usd,
                "pct": round(100.0 * spent / self.day_max_usd, 2),
                "reached": spent >= self.day_max_usd,
            },
            "task": {
                "cap_usd": self.task_max_usd,
                "reached": int(reached[0]["n"]),
                "top": [
                    {
                        "task": str(r["task"]),
                        "usd": round(float(r["usd"]), 6),
                        "calls": int(r["calls"]),
                        "reached": float(r["usd"]) >= self.task_max_usd,
                    }
                    for r in tasks
                ],
            },
            "notice_pct": list(self.notice_pct),
            "crossed_notice": [p for p in self.notice_pct if cycle["pct"] >= p],
        }

    def _meter(self) -> UsageMeter:
        return UsageMeter(self.store, self.monthly_usd, self.alerts_pct, self.cycle_day)

    def alerts(self) -> list[dict[str, Any]]:
        """The active cost alerts: ``key``, ``occ``, ``level``, ``title`` and ``text``."""
        now = self._now()
        cycle = self._meter().cycle(now)
        out: list[dict[str, Any]] = []
        occ = str(cycle["cycle_start"])[:10]
        crossed = [p for p in self.notice_pct if cycle["pct"] >= p]
        if crossed and not cycle["crossed_pct"]:  # past a credit alert, that one speaks
            pct = max(crossed)
            out.append(
                {
                    "key": f"cost:notice:{pct}",
                    "occ": occ,
                    "level": "info",
                    "title": f"API spend at {pct}% of this cycle's credits",
                    "text": (
                        f"API credits: {cycle['pct']:.0f}% of this cycle's included "
                        f"${cycle['budget_usd']:.2f} used (${cycle['spent_usd']:.2f}; GOS's "
                        "own meter, D118). A heads-up only: nothing changes."
                    ),
                }
            )
        caps = self.caps()
        day = caps["day"]
        if day["reached"]:
            out.append(
                {
                    "key": "cost:day",
                    "occ": day["start"][:10],
                    "level": "warn",
                    "title": "Today's API spend reached the day cap",
                    "text": (
                        f"Today's API spend is ${day['spent_usd']:.2f} in {day['calls']} "
                        f"calls, past the ${day['cap_usd']:.2f} day cap ([budget] "
                        "day_max_usd; GOS's own meter, D118). Nothing was paused; the "
                        "Costs view shows where it went."
                    ),
                }
            )
        for task in caps["task"]["top"]:
            if task["reached"]:
                out.append(
                    {
                        "key": f"cost:task:{task['task']}",
                        "occ": task["task"],
                        "level": "warn",
                        "title": "A task reached its cap",
                        "text": (
                            f"Task {task['task'][:40]} has used ${task['usd']:.2f}, its "
                            f"${self.task_max_usd:.2f} cap ([budget] task_max_usd): it "
                            "paused and waits for your answer on the dashboard (D16)."
                        ),
                    }
                )
        return out
