"""WO-6.3: the event store (``event``, ``session``) in the core DB and how sessions start and end
(plan 7.5, D263, D264)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec import store
from gregtec.db import access
from gregtec.events import store as events

SCHEMA_V1 = Path(__file__).resolve().parents[2] / "fixtures" / "gos-schema-v1.sql"
T0 = 1_762_000_000
_n = 0


def ev(kind: str, ts: float, **data: Any) -> dict[str, Any]:  # noqa: ANN401
    global _n
    _n += 1
    return {"id": f"e{_n}", "kind": kind, "ts": ts, "source": "log", "data": data}


def login(ts: float) -> dict[str, Any]:
    return ev("location", ts, x=1.0, y=64.0, z=2.0, via="login")


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = store.connect(tmp_path / "gos.sqlite")
    yield c
    c.close()


def sessions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM session ORDER BY id")]


# --- the tables -------------------------------------------------------------------------------


def test_an_existing_db_gets_the_new_columns_and_keeps_its_rows(tmp_path: Path) -> None:
    """Migration-safe: a DB made by an older GOS (schema v1, and v4 by `gos db build`) has
    Appendix D's event and session tables without WO-6.3's columns."""
    for name, make in (
        ("v1", lambda c: c.executescript(SCHEMA_V1.read_text(encoding="utf-8"))),
        ("v4", None),
    ):
        path = tmp_path / f"{name}.sqlite"
        if make is None:
            access.connect(path).close()
        else:
            raw = sqlite3.connect(path)
            make(raw)
            raw.close()
        raw = sqlite3.connect(path)
        raw.execute("INSERT INTO event(ts, kind) VALUES (?, 'chat')", (T0,))
        raw.commit()
        raw.close()
        conn = store.connect(path)
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(event)")}
            assert {"uid", "source", "instance"} <= cols
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(session)")}
            assert {
                "instance",
                "start_why",
                "end_why",
                "last_ts",
                "journal_upto",
            } <= cols
            events.record(conn, "play", [login(T0 + 1)])
            assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 2
        finally:
            conn.close()
        access.connect(path).close()  # and the recipe schema still opens it


def test_the_core_db_first_then_the_recipe_schema(tmp_path: Path) -> None:
    path = tmp_path / "gos.sqlite"
    conn = store.connect(path)
    events.record(conn, "play", [login(T0)])
    conn.close()
    access.connect(path).close()
    conn = store.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 1
    finally:
        conn.close()


def test_events_are_kept_once_with_their_place_and_reference(
    conn: sqlite3.Connection,
) -> None:
    built = ev(
        "machine_built",
        T0 + 5,
        dim=0,
        x=10,
        y=64,
        z=-3,
        mte_id=1000,
        ref="mte:1000",
        key="gregtech:gt.blockmachines@1000",
    )
    quest = ev("quest_done", T0 + 6, quest="q:AAAAAAAAAAAAAAAAAAAABQ")
    census = ev("machine_census", T0 + 7, scope="chunks", chunks=[], machines=[])
    got = events.record(conn, "play", [login(T0), built, quest, census])
    assert got.stored == 3 and got.duplicate == 0
    again = events.record(conn, "play", [built, quest])  # a retried post
    assert again.stored == 0 and again.duplicate == 2
    rows = {r["kind"]: r for r in conn.execute("SELECT * FROM event")}
    assert set(rows) == {"location", "machine_built", "quest_done"}
    b = rows["machine_built"]
    assert (b["dim"], b["x"], b["y"], b["z"], b["ref"]) == (0, 10, 64, -3, "mte:1000")
    assert rows["quest_done"]["ref"] == "q:AAAAAAAAAAAAAAAAAAAABQ"
    assert json.loads(b["payload"])["key"] == "gregtech:gt.blockmachines@1000"
    assert b["uid"] == built["id"] and b["instance"] == "play"


# --- sessions ---------------------------------------------------------------------------------


def test_a_session_runs_from_the_login_to_the_quit(conn: sqlite3.Connection) -> None:
    events.record(
        conn,
        "play",
        [
            login(T0),
            ev("chat", T0 + 60, text="hi", untrusted=True),
            ev("world_closed", T0 + 600, via="log"),
            ev("game_paused", T0 + 601, paused=None, why="the world closed"),
            # the quit's save, read a moment later, still belongs to it
            ev("quest_done", T0 + 610, quest="q:AAAAAAAAAAAAAAAAAAAABQ"),
        ],
        world="survival",
    )
    (s,) = sessions(conn)
    assert (s["start_ts"], s["end_ts"], s["start_why"], s["end_why"]) == (
        T0,
        T0 + 600,
        "login",
        "quit",
    )
    assert s["world"] == "survival" and s["instance"] == "play"
    assert {r[0] for r in conn.execute("SELECT session_id FROM event")} == {s["id"]}


def test_a_crash_ends_it_and_a_stray_error_starts_nothing(
    conn: sqlite3.Connection,
) -> None:
    events.record(conn, "play", [login(T0), ev("crash", T0 + 100, report="crash.txt")])
    events.record(conn, "play", [ev("error", T0 + 1000, message="x")])
    events.record(conn, "play", [ev("crash", T0 + 2000, report="later.txt")])
    (s,) = sessions(conn)
    assert s["end_why"] == "crash" and s["end_ts"] == T0 + 100
    loose = conn.execute("SELECT COUNT(*) FROM event WHERE session_id IS NULL")
    assert loose.fetchone()[0] == 2


def test_15_minutes_idle_ends_it_and_activity_resumes(conn: sqlite3.Connection) -> None:
    events.record(conn, "play", [login(T0), ev("chat", T0 + 120, text="a")])
    # nothing for 20 minutes, then he moves: the old session ended at its last activity
    events.record(conn, "play", [ev("location", T0 + 120 + 1200, x=1, y=2, z=3, dim=0)])
    a, b = sessions(conn)
    assert (a["end_ts"], a["end_why"]) == (T0 + 120, "idle")
    assert (b["start_why"], b["end_ts"]) == ("resume", None)
    # errors and the census are not activity: they never keep a session alive
    events.record(conn, "play", [ev("error", T0 + 120 + 1200 + 899, message="x")])
    assert events.close_idle(conn, T0 + 120 + 1200 + 901) == [b["id"]]
    assert sessions(conn)[1]["end_ts"] == T0 + 120 + 1200


def test_a_second_login_ends_the_first_and_instances_are_apart(
    conn: sqlite3.Connection,
) -> None:
    events.record(conn, "play", [login(T0), ev("chat", T0 + 5, text="a")])
    events.record(conn, "lab", [login(T0 + 10)])
    events.record(conn, "play", [login(T0 + 20)])
    play1, lab, play2 = sessions(conn)
    assert play1["end_why"] == "relogin" and lab["end_ts"] is None
    assert play2["start_why"] == "login" and play2["instance"] == "play"


def test_a_post_out_of_order_is_sorted_by_time(conn: sqlite3.Connection) -> None:
    events.record(conn, "play", [ev("chat", T0 + 5, text="a"), login(T0)])
    (s,) = sessions(conn)
    assert s["start_why"] == "login" and s["start_ts"] == T0


def test_an_unknown_instance_is_stored_without_a_session(
    conn: sqlite3.Connection,
) -> None:
    events.record(conn, None, [login(T0)])
    assert sessions(conn) == []
    assert conn.execute("SELECT instance FROM event").fetchone()[0] is None


def test_counts_take_a_death_seen_twice_once(conn: sqlite3.Connection) -> None:
    first = ev("death", T0 + 10, message="Clayton fell", via="chat")
    events.record(
        conn,
        "play",
        [login(T0), first, ev("death", T0 + 12, via="stats", same_as=first["id"])],
    )
    sid = sessions(conn)[0]["id"]
    assert events.counts(conn, sid) == {"location": 1, "death": 1}


def test_a_session_as_the_view_shows_it(conn: sqlite3.Connection) -> None:
    events.record(conn, "play", [login(T0), ev("chat", T0 + 300, text="a")])
    row = conn.execute("SELECT * FROM session").fetchone()
    open_ = events.session_dict(row, T0 + 400)
    assert open_["open"] and open_["played_s"] == 400
    # an open session idle past 15 minutes counts only up to its last activity + 15 minutes
    assert events.session_dict(row, T0 + 10_000)["played_s"] == 300 + events.IDLE_S
