"""WO-6.3: the finished age's recap, computed in code when a tier-up re-themes the dashboard
(D162, D265): hours played, machines built, best net power."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec import store
from gregtec.events import ages
from gregtec.events import store as events

T0 = 1_762_000_000
H = 3600
_n = 0


def ev(kind: str, ts: float, **data: Any) -> dict[str, Any]:  # noqa: ANN401
    global _n
    _n += 1
    return {"id": f"a{_n}", "kind": kind, "ts": ts, "source": "x", "data": data}


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = store.connect(tmp_path / "gos.sqlite")
    yield c
    c.close()


def session(conn: sqlite3.Connection, instance: str, start: int, end: int, built: int = 0) -> None:
    items = [ev("location", start, via="login", x=0, y=0, z=0)]
    items += [ev("location", t, x=t % 97, y=64, z=0, dim=0) for t in range(start + 600, end, 600)]
    items += [ev("machine_built", start + 5 + i, mte_id=1000, ref="mte:1000", dim=0, x=i, y=1, z=1) for i in range(built)]
    items.append(ev("world_closed", end, via="log"))
    events.record(conn, instance, items)


def tel(conn: sqlite3.Connection, src: str, ts: int, key: str, value: float) -> None:
    conn.execute(
        "INSERT INTO tel_sample(ts, src, key, value, text) VALUES (?, ?, ?, ?, NULL)",
        (ts, src, key, value),
    )


def test_ages_follow_d162() -> None:
    assert [ages.age_of(s) for s in ("stone", "steam", "LV", "MV", "UV", "UHV", "UXV")] == [
        1, 1, 2, 3, 9, 10, 10,
    ]
    assert ages.age_of(None) == 1 and ages.age_of("nonsense") == 1


def test_a_rise_closes_the_age_with_its_recap(conn: sqlite3.Connection) -> None:
    assert ages.observe(conn, "steam", T0) is None
    session(conn, "play", T0 + H, T0 + 3 * H, built=3)  # 2 h, 3 machines
    session(conn, "lab", T0 + 4 * H, T0 + 9 * H, built=5)  # Lab never counts
    session(conn, "play", T0 + 10 * H, T0 + 11 * H + 1800, built=1)  # 1.5 h
    tel(conn, "lsc", T0 + 2 * H, "eu_in", 512)
    tel(conn, "lsc", T0 + 2 * H, "eu_out", 100)
    tel(conn, "lsc", T0 + 2 * H + 60, "eu_out", 300)  # net 212 now: 412 stays the best
    tel(conn, "gen", T0 + 2 * H + 90, "eu_in", 900)  # no eu_out read yet: no net
    assert ages.observe(conn, "steam", T0 + 12 * H) is None
    assert ages.observe(conn, "LV", T0 + 12 * H) == 1
    (recap,) = ages.recaps(conn)
    assert recap == {
        "age": 1,
        "stage": "steam",
        "start_ts": T0,
        "end_ts": T0 + 12 * H,
        "hours_played": 3.5,
        "sessions": 2,
        "machines_built": 4,
        "best_net_eut": 412,
        "best_ref": f"db:tel_sample/lsc@{T0 + 2 * H}",
    }
    assert ages.observe(conn, "LV", T0 + 13 * H) is None  # once


def test_no_power_readings_is_none_never_an_estimate(conn: sqlite3.Connection) -> None:
    ages.observe(conn, "LV", T0)
    session(conn, "play", T0 + 10, T0 + 1000)
    ages.observe(conn, "MV", T0 + 2000)
    (recap,) = ages.recaps(conn)
    assert recap["best_net_eut"] is None and recap["best_ref"] is None
    assert recap["hours_played"] == 0.3 and recap["machines_built"] == 0


def test_a_drop_changes_nothing_and_the_best_outlives_the_samples(
    conn: sqlite3.Connection,
) -> None:
    ages.observe(conn, "MV", T0)
    tel(conn, "lsc", T0 + 100, "eu_in", 2000)
    tel(conn, "lsc", T0 + 100, "eu_out", 500)
    ages.update_best(conn, T0 + H)
    conn.execute("DELETE FROM tel_sample")  # pruned after 7 days
    assert ages.observe(conn, "LV", T0 + 2 * H) is None  # his override, set lower
    assert ages.observe(conn, "HV", T0 + 30 * 86400) == 3
    (recap,) = ages.recaps(conn)
    assert recap["best_net_eut"] == 1500 and recap["stage"] == "MV"


def test_a_session_that_spans_the_tier_up_is_split(conn: sqlite3.Connection) -> None:
    ages.observe(conn, "LV", T0)
    session(conn, "play", T0 + 100, T0 + 100 + 2 * H)
    assert ages.observe(conn, "MV", T0 + 100 + H) == 2  # an hour into the session
    assert ages.recaps(conn)[0]["hours_played"] == 1.0
