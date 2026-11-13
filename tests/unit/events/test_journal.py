"""WO-6.3: the session scribe's journal, ``log/YYYY-MM-DD.md`` (plan 7.5, D142 (f), D263):
hourly and end blocks through gregtec.vault only, <= 40 lines a session, counts computed in
code, model notes only around those numbers."""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gregtec import store
from gregtec.events import journal
from gregtec.events import store as events
from gregtec.llm.summarise import SummariserOff, event_counts
from gregtec.vault import io, schema

TEMPLATE = Path(__file__).resolve().parents[3] / "vault-template"
TODAY = dt.date(2026, 11, 11)
T0 = int(dt.datetime(2026, 11, 11, 14, 0).timestamp())
LOG = "log/2026-11-11.md"
_n = 0


def ev(kind: str, ts: float, **data: Any) -> dict[str, Any]:  # noqa: ANN401
    global _n
    _n += 1
    return {"id": f"j{_n}", "kind": kind, "ts": ts, "source": "x", "data": data}


def login(ts: float) -> dict[str, Any]:
    return ev("location", ts, x=1.0, y=64.0, z=2.0, via="login")


def quest(ts: float, n: int) -> dict[str, Any]:
    return ev("quest_done", ts, quest=f"q:QUEST{n:04d}", completed_ms=1, claimed=0)


def built(ts: float, mte: int, removed: bool = False) -> dict[str, Any]:
    kind = "machine_removed" if removed else "machine_built"
    return ev(kind, ts, dim=0, x=5, y=70, z=-9, mte_id=mte, ref=f"mte:{mte}")


@pytest.fixture
def vault(tmp_path: Path) -> io.VaultIO:
    root = tmp_path / "vault"
    shutil.copytree(TEMPLATE, root)
    return io.VaultIO(root, today=lambda: TODAY)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = store.connect(tmp_path / "gos.sqlite")
    yield c
    c.close()


def text(vault: io.VaultIO, rel: str = LOG) -> str:
    return (vault.root / rel).read_text(encoding="utf-8")


def body(vault: io.VaultIO, rel: str = LOG) -> list[str]:
    lines = text(vault, rel).splitlines()
    return lines[lines.index("---", 1) + 1 :]


class FakeMessages:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=self.answer)],
            usage=SimpleNamespace(
                input_tokens=300,
                output_tokens=30,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


def scribe_with(answer: str, vault: io.VaultIO) -> tuple[journal.Scribe, FakeMessages]:
    messages = FakeMessages(answer)
    model = journal.HaikuScribe(SimpleNamespace(messages=messages))  # type: ignore[arg-type]
    return journal.Scribe(vault, lambda: model), messages


def test_hourly_then_end_blocks_with_counts_computed_in_code(
    conn: sqlite3.Connection, vault: io.VaultIO
) -> None:
    first_death = ev("death", T0 + 300, message="Clayton fell from a high place")
    events.record(
        conn,
        "play",
        [
            login(T0),
            quest(T0 + 60, 1),
            quest(T0 + 70, 2),
            quest(T0 + 80, 3),
            built(T0 + 100, 1000),
            built(T0 + 110, 1001),
            built(T0 + 120, 1001, removed=True),
            first_death,
            ev("death", T0 + 301, via="stats", same_as=first_death["id"]),
            *(ev("chat", T0 + 400 + i, text=f"hi {i}") for i in range(5)),
            # he keeps moving (not journalled), so the session never idles
            *(ev("location", T0 + 800 * i, x=i, y=64, z=0, dim=0) for i in range(1, 6)),
        ],
        world="survival",
    )
    scribe = journal.Scribe(vault)
    assert scribe.run(conn, T0 + 600) == []  # not an hour yet
    assert scribe.run(conn, T0 + 3601) == [LOG]
    hour = body(vault)
    assert hour[0].startswith("session: s1 play survival, from ")
    assert hour[1:5] == ["n_built: 2", "n_death: 1", "n_quest: 3", "n_removed: 1"]
    assert "quest: q:QUEST0001" in hour
    assert "built: mte:1000 at 5,70,-9 overworld" in hour
    assert "death: Clayton fell from a high place" in hour
    assert not any(line.startswith("chat") for line in hour)  # chat isn't journalled
    assert scribe.run(conn, T0 + 3700) == []  # the next block is an hour away
    events.record(
        conn,
        "play",
        [quest(T0 + 3800, 4), ev("world_closed", T0 + 4000, via="log")],
    )
    assert scribe.run(conn, T0 + 4010) == [LOG]
    lines = body(vault)
    end = lines[len(hour) :]
    assert end[0] == "n_quest: 1" and "quest: q:QUEST0004" in end
    assert end[-1].startswith("session_end: s1 to ") and end[-1].endswith(
        ", played_min 66, quit"
    )
    # the compaction's counts are exactly the session's (D142 (f))
    assert event_counts([(LOG, text(vault))]) == {
        "session": 1,
        "session_end": 1,
        "quest": 4,
        "built": 2,
        "removed": 1,
        "death": 1,
    }
    s = conn.execute("SELECT * FROM session").fetchone()
    assert s["journal_done"] == 1 and s["journal_lines"] == len(lines)
    assert s["journal_path"] == LOG
    assert scribe.run(conn, T0 + 9000) == []  # done once


def test_a_long_busy_session_stays_within_40_lines(
    conn: sqlite3.Connection, vault: io.VaultIO
) -> None:
    scribe = journal.Scribe(vault)
    events.record(conn, "play", [login(T0)])
    n = 0
    for hour in range(8):
        batch = []
        for i in range(30):
            n += 1
            ts = T0 + hour * 3600 + 60 + i * 110
            batch += [
                quest(ts, n),
                built(ts + 1, 1000 + i),
                ev("dim_change", ts + 2, **{"from": 0, "to": -1}),
            ]
        events.record(conn, "play", batch)
        scribe.run(conn, T0 + (hour + 1) * 3600 + 1)
    events.record(conn, "play", [ev("world_closed", T0 + 8 * 3600 + 30, via="log")])
    scribe.run(conn, T0 + 8 * 3600 + 40)
    lines = body(vault)
    assert len(lines) <= journal.SESSION_MAX
    assert lines[-1].startswith("session_end: s1")
    counts = event_counts([(LOG, text(vault))])
    assert counts["quest"] == 240 and counts["built"] == 240 and counts["dim"] == 240


def test_lab_sessions_stay_out_of_the_journal(
    conn: sqlite3.Connection, vault: io.VaultIO
) -> None:
    events.record(conn, "lab", [login(T0), ev("world_closed", T0 + 60, via="log")])
    assert journal.Scribe(vault).run(conn, T0 + 7200) == []
    assert not (vault.root / LOG).exists()
    assert conn.execute("SELECT journal_done FROM session").fetchone()[0] == 1


def end_session(conn: sqlite3.Connection) -> None:
    events.record(
        conn,
        "play",
        [
            login(T0),
            quest(T0 + 10, 1),
            quest(T0 + 20, 2),
            built(T0 + 30, 1000),
            ev("world_closed", T0 + 600, via="log"),
        ],
    )


def test_the_model_writes_notes_around_the_given_numbers(
    conn: sqlite3.Connection, vault: io.VaultIO
) -> None:
    end_session(conn)
    scribe, messages = scribe_with(
        "note: finished 2 quests and built mte:1000; next, power it", vault
    )
    scribe.run(conn, T0 + 700)
    lines = body(vault)
    assert lines[-2] == "note: finished 2 quests and built mte:1000; next, power it"
    assert messages.kwargs["model"] == "claude-haiku-5-5"
    assert messages.kwargs["system"] == journal.SYSTEM
    assert "n_quest: 2" in messages.kwargs["messages"][0]["content"]


@pytest.mark.parametrize(
    "answer",
    [
        "note: finished 17 quests",  # a number it made up
        "deaths: 0",  # not a note
        "note: a\nnote: b\nnote: c",  # too many
    ],
)
def test_notes_that_break_the_rules_are_left_out(
    conn: sqlite3.Connection, vault: io.VaultIO, answer: str
) -> None:
    end_session(conn)
    scribe, _ = scribe_with(answer, vault)
    scribe.run(conn, T0 + 700)
    lines = body(vault)
    assert not any(line.startswith("note:") for line in lines)
    assert lines[-1].startswith("session_end: s1")


def test_no_ai_writes_the_journal_without_notes(
    conn: sqlite3.Connection, vault: io.VaultIO
) -> None:
    end_session(conn)

    def off() -> None:
        raise SummariserOff("the AI is off (no key)")

    journal.Scribe(vault, off).run(conn, T0 + 700)
    assert body(vault)[-1].startswith("session_end: s1")


def test_a_full_daily_log_goes_on_in_the_next_one(
    conn: sqlite3.Connection, vault: io.VaultIO
) -> None:
    vault.append(LOG, "\n".join(f"decision: {i}" for i in range(140)))
    end_session(conn)
    assert journal.Scribe(vault).run(conn, T0 + 700) == ["log/2026-11-11-2.md"]
    assert body(vault, "log/2026-11-11-2.md")[0].startswith("session: s1")


def test_no_vault_is_a_problem_and_the_block_waits(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    end_session(conn)
    scribe = journal.Scribe(io.VaultIO(tmp_path / "nowhere", today=lambda: TODAY))
    assert scribe.run(conn, T0 + 700) == []
    assert scribe.problems and "no vault" in scribe.problems[0]
    assert conn.execute("SELECT journal_done FROM session").fetchone()[0] == 0


def test_agents_never_write_count_lines(vault: io.VaultIO) -> None:
    with pytest.raises(io.VaultError, match="computed counts"):
        vault.append(LOG, "n_quest: 3")
    with pytest.raises(io.VaultError, match="daily log only"):
        vault.append("base/power.md", "n_quest: 3", computed=True)
    # the schema check still refuses an n_ key the scribe never writes
    types = schema.load_types(vault.root / "_gos" / "schema.md")
    note = "---\nid: log-2026-11-11\nt: log\nupd: 2026-11-11\n---\nn_chat: 3\n"
    assert schema.check_text(LOG, note, types)


def test_a_sessions_lines_are_found_again(
    conn: sqlite3.Connection, vault: io.VaultIO
) -> None:
    vault.append(
        LOG,
        "session: s7 play w, from 09:00\nn_quest: 1\nsession_end: s7 to 10:00",
        computed=True,
    )
    end_session(conn)
    journal.Scribe(vault).run(conn, T0 + 700)
    mine = journal.journal_lines(text(vault), 1)
    assert mine[0].startswith("session: s1 ") and mine[-1].startswith("session_end: s1")
    assert not any("s7" in line for line in mine)
    # a block continued on a later day's log, before another session's
    later = "---\nid: x\nt: log\nupd: x\n---\nn_quest: 2\nsession_end: s1 to 01:00\nsession: s2 play w, from 02:00\n"
    assert journal.journal_lines(later, 1)[-2:] == [
        "n_quest: 2",
        "session_end: s1 to 01:00",
    ]
