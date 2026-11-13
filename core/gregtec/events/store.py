"""The event store and sessions (WO-6.3, plan 7.5, D263, D264).

Every well-formed event the game watchers post (``POST /api/game/events``) is kept in the core
DB's ``event`` table (Appendix D, plus ``uid``, ``source`` and ``instance``), keyed by the
watchers' own event id (``uid``), so a retried post or a re-read events file adds nothing.
``machine_census`` is not kept: it is My base's state of the saved chunks, not something that
happened (:mod:`gregtec.base.seen` keeps what it says). Columns: ``ts`` epoch seconds (when the
watchers saw it), ``dim``/``x``/``y``/``z`` when the event says where, ``ref`` its citation
(``q:`` for a quest, ``mte:`` for a machine), ``payload`` the event's data as JSON. Game text in
a payload is data, never instructions (plan 0.1 principle 8).

**Sessions** (plan 7.5), per instance (``play``, ``lab``), worked out as the events are stored,
in time order within a post:

- a session **starts** at the login line (``location`` with ``via: login``); a login while one is
  open ends that one (``relogin``). Any other activity with no open session starts one too
  (``resume``: back from 15 minutes idle, or watchers or a core that started mid-game), unless
  it comes within :data:`AFTER_END_S` of a session that ended at a quit or a crash: then it is
  that session's tail (save files read after the quit line) and joins it;
- it **ends** at the quit line (``world_closed``, end ``quit``), at a crash (``crash``) or after
  :data:`IDLE_S` (15 minutes) with no activity (``idle``; it ends at its last activity, and the
  core's tick closes it when no event comes at all, :func:`close_idle`);
- **activity** is what the player does: every kind but the census, ``error``, ``export_stale``
  and ``world_closed``; a pause or unpause counts (he was there).

Events that belong to no session (a stray error with the game closed) keep ``session_id`` NULL.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

IDLE_S = 15 * 60  # plan 7.5: a session ends after 15 minutes idle
AFTER_END_S = 120  # save files read this soon after a quit or crash still belong to it
NOT_STORED = frozenset({"machine_census"})
NOT_ACTIVITY = frozenset({"machine_census", "error", "export_stale", "world_closed"})
INSTANCES = ("play", "lab")
PAYLOAD_MAX = 16 << 10


@dataclass
class Recorded:
    """What one post changed: events stored (and already there), sessions opened and closed."""

    stored: int = 0
    duplicate: int = 0
    opened: list[int] = field(default_factory=list)
    closed: list[int] = field(default_factory=list)


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and abs(value) < 1e9:
        return round(value)
    return None


def _ref(kind: str, data: Mapping[str, Any]) -> str | None:
    for key in ("quest", "ref"):
        v = data.get(key)
        if isinstance(v, str) and v[:2] in ("q:", "mt") and len(v) <= 100:
            return v
    return None


def is_login(kind: str, data: Mapping[str, Any]) -> bool:
    return kind == "location" and data.get("via") == "login"


def opens(kind: str, data: Mapping[str, Any]) -> bool:
    """Whether an event with no open session starts one: activity, never a crash report or the
    paused state going unknown (the world closed)."""
    if kind in NOT_ACTIVITY or kind == "crash":
        return False
    return not (kind == "game_paused" and data.get("paused") is None)


def _open(conn: sqlite3.Connection, instance: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM session WHERE instance = ? AND end_ts IS NULL ORDER BY id DESC LIMIT 1",
        (instance,),
    ).fetchone()
    return row


def _last_closed(conn: sqlite3.Connection, instance: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM session WHERE instance = ? AND end_ts IS NOT NULL "
        "ORDER BY end_ts DESC, id DESC LIMIT 1",
        (instance,),
    ).fetchone()
    return row


def _close(
    conn: sqlite3.Connection, sid: int, end_ts: int, why: str, out: Recorded
) -> None:
    conn.execute(
        "UPDATE session SET end_ts = ?, end_why = ? WHERE id = ? AND end_ts IS NULL",
        (end_ts, why, sid),
    )
    out.closed.append(sid)


def _start(
    conn: sqlite3.Connection,
    instance: str,
    world: str | None,
    ts: int,
    why: str,
    out: Recorded,
) -> int:
    cur = conn.execute(
        "INSERT INTO session(start_ts, end_ts, world, journal_path, instance, start_why, "
        "last_ts, journal_upto, journal_ts, journal_lines, journal_done) "
        "VALUES (?, NULL, ?, NULL, ?, ?, ?, 0, NULL, 0, 0)",
        (ts, world, instance, why, ts),
    )
    sid = cur.lastrowid
    assert sid is not None
    out.opened.append(sid)
    return sid


def _session_for(
    conn: sqlite3.Connection,
    instance: str,
    world: str | None,
    kind: str,
    data: Mapping[str, Any],
    ts: int,
    out: Recorded,
) -> int | None:
    """The session an event at ``ts`` belongs to, opening and closing sessions on the way."""
    cur = _open(conn, instance)
    if cur is not None and cur["last_ts"] is not None and ts - cur["last_ts"] > IDLE_S:
        _close(conn, cur["id"], cur["last_ts"], "idle", out)
        cur = None
    if is_login(kind, data):
        if cur is not None:
            _close(conn, cur["id"], max(cur["last_ts"] or ts, ts), "relogin", out)
        sid = _start(conn, instance, world, ts, "login", out)
        return sid
    if cur is not None:
        if kind == "world_closed":
            _close(conn, cur["id"], ts, "quit", out)
        elif kind == "crash":
            _close(conn, cur["id"], ts, "crash", out)
            conn.execute("UPDATE session SET last_ts = ? WHERE id = ?", (ts, cur["id"]))
        elif kind not in NOT_ACTIVITY:
            conn.execute(
                "UPDATE session SET last_ts = MAX(COALESCE(last_ts, 0), ?) WHERE id = ?",
                (ts, cur["id"]),
            )
        return int(cur["id"])
    last = _last_closed(conn, instance)
    if (
        last is not None
        and last["end_why"] in ("quit", "crash")
        and 0 <= ts - last["end_ts"] <= AFTER_END_S
    ):
        return int(last["id"])  # the quit's own save, read a moment later
    if not opens(kind, data):
        return None
    return _start(conn, instance, world, ts, "resume", out)


def record(
    conn: sqlite3.Connection,
    instance: str | None,
    events: Iterable[Mapping[str, Any]],
    world: str | None = None,
) -> Recorded:
    """Store ``events`` (the watchers' well-formed event dicts) for ``instance`` in one
    ``BEGIN IMMEDIATE`` transaction; sessions follow (see the module docstring)."""
    out = Recorded()
    rows = sorted(
        (e for e in events if e.get("kind") not in NOT_STORED),
        key=lambda e: float(e["ts"]),
    )
    if not rows:
        return out
    inst = instance if instance in INSTANCES else None
    conn.execute("BEGIN IMMEDIATE")
    try:
        for e in rows:
            uid = str(e["id"])
            if conn.execute("SELECT 1 FROM event WHERE uid = ?", (uid,)).fetchone():
                out.duplicate += 1
                continue
            kind = str(e["kind"])
            data: Mapping[str, Any] = e["data"]
            ts = int(float(e["ts"]))
            sid = (
                _session_for(conn, inst, world, kind, data, ts, out)
                if inst is not None
                else None
            )
            payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
            if len(payload) > PAYLOAD_MAX:
                payload = json.dumps({"cut": True, "kind": kind})
            dim = _int(data.get("dim"))
            if dim is None and kind == "dim_change":
                dim = _int(data.get("to"))
            conn.execute(
                "INSERT INTO event(ts, session_id, kind, dim, x, y, z, ref, payload, uid, "
                "source, instance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    sid,
                    kind,
                    dim,
                    _int(data.get("x")),
                    _int(data.get("y")),
                    _int(data.get("z")),
                    _ref(kind, data),
                    payload,
                    uid,
                    str(e.get("source", ""))[:20],
                    inst,
                ),
            )
            out.stored += 1
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return out


def close_idle(conn: sqlite3.Connection, now: float) -> list[int]:
    """End every open session idle for more than :data:`IDLE_S` at its last activity."""
    out = Recorded()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for row in conn.execute(
            "SELECT id, last_ts, start_ts FROM session WHERE end_ts IS NULL"
        ).fetchall():
            last = row["last_ts"] if row["last_ts"] is not None else row["start_ts"]
            if now - last > IDLE_S:
                _close(conn, row["id"], last, "idle", out)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return out.closed


# --- reading ---------------------------------------------------------------------------------


def event_dict(row: sqlite3.Row) -> dict[str, Any]:
    try:
        data = json.loads(row["payload"]) if row["payload"] else {}
    except ValueError:
        data = {}
    return {
        "id": row["id"],
        "ts": row["ts"],
        "kind": row["kind"],
        "session": row["session_id"],
        "instance": row["instance"],
        "source": row["source"],
        "ref": row["ref"],
        "where": None
        if row["x"] is None
        else {"dim": row["dim"], "x": row["x"], "y": row["y"], "z": row["z"]},
        "data": data,
    }


def session_dict(row: sqlite3.Row, now: float) -> dict[str, Any]:
    end = row["end_ts"]
    last = row["last_ts"] if row["last_ts"] is not None else row["start_ts"]
    stop = end if end is not None else max(last, min(int(now), last + IDLE_S))
    return {
        "id": row["id"],
        "instance": row["instance"],
        "world": row["world"],
        "start_ts": row["start_ts"],
        "end_ts": end,
        "open": end is None,
        "start_why": row["start_why"],
        "end_why": row["end_why"],
        "last_ts": row["last_ts"],
        "played_s": max(0, stop - row["start_ts"]),
        "journal": row["journal_path"],
    }


def is_echo(kind: str, payload: str | None) -> bool:
    """A death another reader saw first (the watchers mark it ``same_as``): not counted."""
    if kind != "death" or not payload:
        return False
    try:
        data = json.loads(payload)
    except ValueError:
        return False
    return isinstance(data, dict) and "same_as" in data


def counts(conn: sqlite3.Connection, session_id: int) -> dict[str, int]:
    """A session's events by kind, counted here (deaths seen by several readers once)."""
    out: dict[str, int] = {}
    for row in conn.execute(
        "SELECT kind, payload FROM event WHERE session_id = ?", (session_id,)
    ):
        if is_echo(row["kind"], row["payload"]):
            continue
        out[row["kind"]] = out.get(row["kind"], 0) + 1
    return out
