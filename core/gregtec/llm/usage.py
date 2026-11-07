"""The ``llm_usage`` meter (plan 4.4 Billing, D47, D48): every Claude call's tokens and cost.

The lanes (WO-2.3, WO-2.4) call :meth:`UsageMeter.record` after each call. The fast lane passes
its token counts and the cost is priced from ``prices.PRICES``; the deep lane passes the SDK's own
``total_cost_usd`` as ``usd``. A model with no price and no ``usd`` is refused, so the month's
total never silently leaves a call out.

:meth:`UsageMeter.cycle` sums the current credit cycle against ``[budget] monthly_included_usd``
and says which of ``credit_alerts_pct`` it has crossed. Acting on a crossing (the D48 DMs and
no-AI mode) is WO-2.8. A cycle starts at local midnight on ``[budget] cycle_day`` (D118).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gregtec.llm.prices import PRICES, Usage, estimate_cost
from gregtec.store import Store


class UnpricedModelError(ValueError):
    def __init__(self, model: str) -> None:
        super().__init__(
            f"no price for {model!r} in gregtec.llm.prices.PRICES; add it, or pass usd"
        )


def cycle_bounds(now: datetime, day: int) -> tuple[datetime, datetime]:
    """The credit cycle holding ``now``: [start, end), each at local midnight on ``day``."""
    local = now.astimezone().replace(tzinfo=None)
    start = local.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if local < start:
        start = _add_months(start, -1)
    # A naive datetime's astimezone() reads it as local time, with that date's UTC offset.
    return start.astimezone(), _add_months(start, 1).astimezone()


def _add_months(when: datetime, months: int) -> datetime:
    index = when.year * 12 + when.month - 1 + months
    return when.replace(year=index // 12, month=index % 12 + 1)


@dataclass(frozen=True)
class UsageMeter:
    store: Store
    monthly_usd: float
    alerts_pct: tuple[int, ...]
    cycle_day: int = 1

    def record(
        self,
        lane: str,
        model: str,
        usage: Usage,
        *,
        usd: float | None = None,
        task: str | None = None,
        ts: float | None = None,
        ttft_ms: int | None = None,
    ) -> float:
        """Add one call to ``llm_usage``. Returns its cost in USD. ``ttft_ms``: a streamed
        call's milliseconds from sending it to its first text token (None: no text, or not
        streamed)."""
        if not lane:
            raise ValueError("lane must be a non-empty string")
        if usd is None:
            if model not in PRICES:
                raise UnpricedModelError(model)
            usd = estimate_cost(model, usage)
        if usd < 0:
            raise ValueError("usd must not be negative")
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO llm_usage(ts, lane, model, in_tok, out_tok, cache_read, "
                "cache_write, usd, task, ttft_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(time.time() if ts is None else ts),
                    lane,
                    model,
                    usage.input,
                    usage.output,
                    usage.cache_read,
                    usage.cache_write,
                    usd,
                    task,
                    ttft_ms,
                ),
            )
        return usd

    def cycle(self, now: datetime | None = None) -> dict[str, Any]:
        """Spend in the current credit cycle, in total and by lane and model."""
        start, end = cycle_bounds(now or datetime.now().astimezone(), self.cycle_day)
        window = (int(start.timestamp()), int(end.timestamp()))

        def grouped(column: str) -> list[dict[str, Any]]:
            rows = self.store.rows(
                f"SELECT {column} AS k, COUNT(*) AS calls, SUM(usd) AS usd, "
                "SUM(in_tok) AS in_tok, SUM(out_tok) AS out_tok, "
                "SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write "
                "FROM llm_usage WHERE ts >= ? AND ts < ? GROUP BY k ORDER BY usd DESC, k",
                window,
            )
            return [
                {
                    column: r["k"],
                    "calls": r["calls"],
                    "usd": round(r["usd"] or 0.0, 6),
                    "in_tok": r["in_tok"] or 0,
                    "out_tok": r["out_tok"] or 0,
                    "cache_read": r["cache_read"] or 0,
                    "cache_write": r["cache_write"] or 0,
                }
                for r in rows
            ]

        by_lane = grouped("lane")
        spent = round(sum(row["usd"] for row in by_lane), 6)
        pct = round(100.0 * spent / self.monthly_usd, 2)
        return {
            "cycle_start": start.isoformat(),
            "cycle_end": end.isoformat(),
            "budget_usd": self.monthly_usd,
            "spent_usd": spent,
            "pct": pct,
            "alerts_pct": list(self.alerts_pct),
            "crossed_pct": [a for a in self.alerts_pct if pct >= a],
            "calls": sum(row["calls"] for row in by_lane),
            "by_lane": by_lane,
            "by_model": grouped("model"),
        }
