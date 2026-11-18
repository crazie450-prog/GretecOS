"""WO-11.2: the cost watch (D334) on fake ``llm_usage`` rows: the heads-up, the day cap, the task
cap, the caps card, and each sent once through the alert router, a restart included."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from gregtec import config
from gregtec.alerts import policy
from gregtec.alerts.router import AlertRouter
from gregtec.llm.prices import Usage
from gregtec.llm.usage import UsageMeter
from gregtec.ops.costs import CostWatch
from gregtec.presence import FakeIdle
from gregtec.store import Store

# Noon, local time, on the 15th: well inside one day and one credit cycle (cycle_day 1).
NOON = datetime(2026, 11, 15, 12, 0).astimezone().timestamp()


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "gos.sqlite")
    yield s
    s.close()


def spend(store: Store, usd: float, ts: float, task: str | None = None) -> None:
    UsageMeter(store, 100.0, (80, 100)).record(
        "deep", "claude-sonnet-5-5", Usage(), usd=usd, task=task, ts=ts
    )


def watch(store: Store, clock: Clock, **kw: object) -> CostWatch:
    args: dict[str, object] = {
        "monthly_usd": 100.0,
        "alerts_pct": (80, 100),
        "notice_pct": (50,),
        "task_max_usd": 2.0,
        "day_max_usd": 10.0,
        "clock": clock,
    }
    args.update(kw)
    return CostWatch(store, **args)  # type: ignore[arg-type]


@dataclass
class FakeTask:
    """The deep lane's task as the cap alert reads it (gregtec.agents.deep.DeepTask)."""

    id: str
    task: str
    status: str = "running"
    spent_usd: float = 0.0
    cap_usd: float = 2.0
    approvals: int = 0


def keys(w: CostWatch) -> dict[str, str]:
    return {a["key"]: a["level"] for a in w.alerts()}


def test_nothing_under_every_line(store: Store) -> None:
    clock = Clock(NOON)
    spend(store, 3.0, NOON - 86400 * 3)
    spend(store, 1.5, NOON - 60, task="t1")
    w = watch(store, clock)
    assert w.alerts() == []
    caps = w.caps()
    assert caps["day"]["spent_usd"] == 1.5 and not caps["day"]["reached"]
    assert caps["day"]["pct"] == 15.0
    assert caps["task"] == {
        "cap_usd": 2.0,
        "reached": 0,
        "top": [{"task": "t1", "usd": 1.5, "calls": 1, "reached": False}],
    }
    assert caps["notice_pct"] == [50] and caps["crossed_notice"] == []


def test_the_heads_up_at_50_percent_then_the_credit_alert_takes_over(
    store: Store,
) -> None:
    clock = Clock(NOON)
    spend(store, 52.0, NOON - 86400 * 5)  # an earlier day: not today's cap
    w = watch(store, clock)
    [alert] = w.alerts()
    assert alert["key"] == "cost:notice:50" and alert["level"] == "info"
    assert alert["occ"] == "2026-11-01"
    assert "52% of this cycle's included $100.00 used ($52.00" in alert["text"]
    assert w.caps()["crossed_notice"] == [50]
    spend(store, 30.0, NOON - 86400 * 4)  # 82%: D48's own alert speaks now
    assert keys(w) == {}


def test_the_day_cap_counts_only_today(store: Store) -> None:
    clock = Clock(NOON)
    spend(store, 9.0, NOON - 13 * 3600)  # yesterday, 23:00
    spend(store, 6.0, NOON - 3600)
    spend(store, 4.5, NOON - 60)
    w = watch(store, clock)
    assert keys(w) == {"cost:day": "warn"}
    [day] = w.alerts()
    assert day["occ"] == datetime.fromtimestamp(NOON).strftime("%Y-%m-%d")
    assert "$10.50 in 2 calls, past the $10.00 day cap" in day["text"]
    assert w.caps()["day"]["reached"]
    clock.t = NOON + 86400  # the next day starts at zero
    assert keys(w) == {}


def test_the_caps_card_lists_todays_biggest_tasks(store: Store) -> None:
    clock = Clock(NOON)
    spend(store, 1.25, NOON - 120, task="design-7")
    spend(store, 0.75, NOON - 60, task="design-7")
    spend(store, 0.5, NOON - 60, task="ask-1")
    w = watch(store, clock)
    assert keys(w) == {}  # the meter alone never raises the task alert (the lane does)
    caps = w.caps()["task"]
    assert caps["reached"] == 1
    assert [t["task"] for t in caps["top"]] == ["design-7", "ask-1"]


def test_the_task_alert_follows_the_deep_lanes_pause(store: Store) -> None:
    clock = Clock(NOON)
    t = FakeTask("dl-1", "design-7")
    w = watch(store, clock, tasks=lambda: [t, FakeTask("dl-2", "ask-1")])
    assert keys(w) == {}
    t.status, t.spent_usd = "needs_approval", 2.01  # paused at its cap (D156)
    [a] = w.alerts()
    assert (a["key"], a["occ"], a["level"]) == ("cost:task:dl-1", "0", "warn")
    assert "design-7 (dl-1) has used $2.01, its $2.00 cap" in a["text"]
    t.status, t.approvals, t.cap_usd = "running", 1, 4.0  # approved: the alert clears
    assert keys(w) == {}
    t.status, t.spent_usd = "needs_approval", 4.0  # the second cap is a new occurrence
    [a] = w.alerts()
    assert a["occ"] == "1" and "$4.00 cap" in a["text"]
    t.status = "done"
    assert keys(w) == {}


def test_config_reads_and_checks_the_new_keys(tmp_path: Path) -> None:
    f = tmp_path / "gos.toml"
    f.write_text("[budget]\nday_max_usd = 4.5\nnotice_pct = [25, 50]\n", "utf-8")
    cfg = config.load(f)
    assert (cfg.day_max_usd, cfg.notice_pct) == (4.5, (25, 50))
    f.write_text("[budget]\nday_max_usd = 0\nnotice_pct = [100]\n", "utf-8")
    cfg = config.load(f)
    assert (cfg.day_max_usd, cfg.notice_pct) == (10.0, (50,))  # defaults kept
    assert any("day_max_usd" in e for e in cfg.service_errors)
    assert any("notice_pct" in e for e in cfg.service_errors)
    s = Store(tmp_path / "x.sqlite")
    try:
        assert CostWatch.of(s, cfg).day_max_usd == 10.0
    finally:
        s.close()


def router_for(store: Store, clock: Clock, w: CostWatch) -> AlertRouter:
    r = AlertRouter(store, policy.NotifyPolicy(), idle=FakeIdle(clock), clock=clock)
    r.costs = w.alerts
    r.discord_up = lambda: True
    return r


def dms(store: Store) -> list[str]:
    return [r["text"] for r in store.rows("SELECT text FROM alert_dm ORDER BY id")]


def test_each_cost_alert_is_dmed_once_even_across_a_restart(store: Store) -> None:
    clock = Clock(NOON)
    spend(store, 55.0, NOON - 86400 * 3)
    spend(store, 2.5, NOON - 60, task="design-9")
    spend(store, 8.0, NOON - 30)
    t = FakeTask("dl-9", "design-9", "needs_approval", 2.5)
    w = watch(store, clock, tasks=lambda: [t])
    r = router_for(store, clock, w)
    for _ in range(3):
        clock.t += 1
        r.tick()
    sent = dms(store)
    assert len(sent) == 3, sent  # present at the PC: direct alerts still DM (D334)
    assert any(m.startswith("[info] API credits: 6") for m in sent)
    assert any(m.startswith("[warn] Today's API spend is $10.50") for m in sent)
    assert any(m.startswith("[warn] Task design-9 (dl-9) has used $2.50") for m in sent)
    r = router_for(store, clock, w)  # the core restarted
    for _ in range(3):
        clock.t += 1
        r.tick()
    assert len(dms(store)) == 3
    clock.t = NOON + 86400  # tomorrow: a new day's cap is a new occurrence
    spend(store, 11.0, clock.t - 10)
    clock.t += 1
    r.tick()
    assert len(dms(store)) == 4
    t.status, t.approvals = "running", 1  # answered: the alert closes
    clock.t += 1
    r.tick()
    t.status, t.cap_usd = "needs_approval", 4.0  # paused again at 2x: DMed again
    clock.t += 1
    r.tick()
    assert len(dms(store)) == 5
    assert dms(store)[-1].startswith("[warn] Task design-9 (dl-9)")


def test_cost_alerts_are_direct_and_never_spoken() -> None:
    ctx = policy.Context(away=False, quiet_hours=False)
    p = policy.NotifyPolicy()
    assert policy.decide("discord", "cost", "info", ctx, p) == policy.SEND
    assert policy.decide("voice", "cost", "warn", ctx, p) != policy.SEND
    quiet = policy.Context(away=True, quiet_hours=True)
    assert policy.decide("discord", "cost", "warn", quiet, p) == "quiet hours"
