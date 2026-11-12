"""The ages Clayton has played and the finished age's recap (D162, D81; WO-6.3, D265).

The dashboard's theme follows the tier the core reports (``effective_tier``: his override wins
over detection, D114): Stone and Steam are age 1, LV 2 ... UV 9, UHV and up 10 (D162). The core
notes in ``age_mark`` when it first saw each age (:func:`observe`, every 5 s with the
dashboard's tier and on each recap request). When the age rises, the age it leaves is closed
and its **recap** is computed here, never by a model, and kept:

- ``hours_played``: Play's session time inside the age (each session clipped to it), in hours
  to one decimal; ``sessions``: how many of Play's sessions it touched;
- ``machines_built``: Play's ``machine_built`` events inside the age (GregtecLink's listener,
  D260); every placement counts, a machine broken later included;
- ``best_net_eut``: the best net power OC telemetry measured inside the age, a power entry's
  ``eu_in - eu_out`` (``getEUInputAverage``/``getEUOutputAverage``, EU/t, WO-7.1) at one
  reading, cited ``db:tel_sample/<src>@<ts>``; samples are kept 7 days, so the hourly tick
  keeps the age's best so far in ``age_mark`` (:func:`update_best`). Before OC (no power
  entry read in the age) it is None: shown as "no readings", never estimated.

A drop (his override set lower) changes nothing here: only a rise closes an age, and an age is
recapped once. The dashboard shows the newest recap when a tier-up re-themes it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

STAGE_AGE = {
    "stone": 1,
    "steam": 1,
    "LV": 2,
    "MV": 3,
    "HV": 4,
    "EV": 5,
    "IV": 6,
    "LuV": 7,
    "ZPM": 8,
    "UV": 9,
}
LAST_AGE = 10  # UHV and up
TEL_KEEP_S = 7 * 86400


def age_of(stage: str | None) -> int:
    """The age of a stage (D162); an unknown stage is age 1, as the dashboard's ``ageOf``."""
    if stage is None:
        return 1
    if stage in STAGE_AGE:
        return STAGE_AGE[stage]
    return LAST_AGE if stage in ("UHV", "UEV", "UIV", "UMV", "UXV", "MAX") else 1


def _current(conn: sqlite3.Connection) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM age_mark WHERE end_ts IS NULL ORDER BY age DESC LIMIT 1"
    ).fetchone()
    return row


def observe(conn: sqlite3.Connection, stage: str | None, now: float) -> int | None:
    """Note the age ``stage`` is in; returns the age a rise just closed (its recap is kept),
    else None. One ``BEGIN IMMEDIATE`` transaction."""
    age, ts = age_of(stage), int(now)
    closed = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = _current(conn)
        if cur is not None and age > cur["age"]:
            best = scan_best(conn, cur["start_ts"], ts)
            kept = (cur["best_net_eut"], cur["best_ref"])
            if kept[0] is not None and (best[0] is None or kept[0] >= best[0]):
                best = kept
            recap = compute(conn, cur["age"], cur["stage"], cur["start_ts"], ts, best)
            conn.execute(
                "UPDATE age_mark SET end_ts = ?, best_net_eut = ?, best_ref = ?, recap = ? "
                "WHERE age = ?",
                (ts, best[0], best[1], json.dumps(recap), cur["age"]),
            )
            closed = int(cur["age"])
            cur = None
        if cur is None:
            have = conn.execute(
                "SELECT age FROM age_mark WHERE age = ?", (age,)
            ).fetchone()
            if have is None:
                conn.execute(
                    "INSERT INTO age_mark(age, stage, start_ts) VALUES (?, ?, ?)",
                    (age, stage or "stone", ts),
                )
            elif closed is not None or _current(conn) is None:
                # back to an age reached before (after an override drop and rise)
                conn.execute("UPDATE age_mark SET end_ts = NULL WHERE age = ?", (age,))
        elif age == cur["age"] and stage and stage != cur["stage"]:
            conn.execute("UPDATE age_mark SET stage = ? WHERE age = ?", (stage, age))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return closed


def scan_best(
    conn: sqlite3.Connection, start: int, end: int
) -> tuple[int | None, str | None]:
    """The best net EU/t one power entry's readings show in [start, end]: at each reading, its
    latest ``eu_in`` minus its latest ``eu_out`` (both read by then)."""
    rows = conn.execute(
        "SELECT src, key, value, ts FROM tel_sample WHERE key IN ('eu_in', 'eu_out') "
        "AND value IS NOT NULL AND ts BETWEEN ? AND ? ORDER BY src, ts, rowid",
        (max(start, end - TEL_KEEP_S - 3600), end),
    ).fetchall()
    best: tuple[int | None, str | None] = (None, None)
    latest: dict[tuple[str, str], float] = {}
    for r in rows:
        latest[(r["src"], r["key"])] = r["value"]
        inn, out = latest.get((r["src"], "eu_in")), latest.get((r["src"], "eu_out"))
        if inn is None or out is None:
            continue
        net = round(inn - out)
        if best[0] is None or net > best[0]:
            best = (net, f"db:tel_sample/{r['src']}@{r['ts']}")
    return best


def update_best(conn: sqlite3.Connection, now: float) -> None:
    """Keep the open age's best net power so far (samples last only 7 days)."""
    cur = _current(conn)
    if cur is None:
        return
    found = scan_best(conn, cur["start_ts"], int(now))
    if found[0] is not None and (
        cur["best_net_eut"] is None or found[0] > cur["best_net_eut"]
    ):
        conn.execute(
            "UPDATE age_mark SET best_net_eut = ?, best_ref = ? WHERE age = ?",
            (found[0], found[1], cur["age"]),
        )


def compute(
    conn: sqlite3.Connection,
    age: int,
    stage: str,
    start: int,
    end: int,
    best: tuple[int | None, str | None],
) -> dict[str, Any]:
    """The recap of the age played from ``start`` to ``end`` (see the module docstring)."""
    played, sessions = 0, 0
    for s in conn.execute(
        "SELECT start_ts, end_ts, last_ts FROM session WHERE instance = 'play' "
        "AND start_ts <= ? AND COALESCE(end_ts, last_ts, start_ts) >= ?",
        (end, start),
    ):
        stop = (
            s["end_ts"] if s["end_ts"] is not None else (s["last_ts"] or s["start_ts"])
        )
        span = min(stop, end) - max(s["start_ts"], start)
        if span > 0:
            played += span
            sessions += 1
    built = conn.execute(
        "SELECT COUNT(*) FROM event WHERE kind = 'machine_built' AND instance = 'play' "
        "AND ts BETWEEN ? AND ?",
        (start, end),
    ).fetchone()[0]
    return {
        "age": age,
        "stage": stage,
        "start_ts": start,
        "end_ts": end,
        "hours_played": round(played / 3600, 1),
        "sessions": sessions,
        "machines_built": int(built),
        "best_net_eut": best[0],
        "best_ref": best[1],
    }


def recaps(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every finished age's recap, newest first."""
    out = []
    for r in conn.execute(
        "SELECT recap FROM age_mark WHERE recap IS NOT NULL ORDER BY end_ts DESC, age DESC"
    ):
        try:
            out.append(json.loads(r["recap"]))
        except ValueError:
            continue
    return out
