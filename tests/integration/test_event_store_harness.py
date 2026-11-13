"""The event store and the journal on the listener harness's real output (WO-6.3).

The listener harness (``./gradlew runServer -Pharness -PharnessListener``, WO-6.2) leaves
GregtecLink's real ``events/machines.jsonl`` (10 GT machine kinds placed, 4 broken by a fake
player) in ``mod/run/server/gregteclink/``, and ``runServer -Pharness`` an export run. Here the
watchers read those lines inside a Lab session (a login before, the quit line after), the core's
event store keeps them, and on a DB built from the harness's export:

- every placement and break is one stored event of that session, cited ``mte:<id>``, none lost or
  doubled when the file is read again;
- the session scribe's block counts them in code (``n_built: 10``, ``n_removed: 4``) and names
  the machines from the real catalog.

Runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job) and then fails without the harness output;
writes nothing outside tmp_path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from gregtec import store
from gregtec.db import access, build
from gregtec.events import journal
from gregtec.events import store as event_store
from gregtec.ingest import collect
from gregtec.watchers import machines

REPO = Path(__file__).resolve().parents[2]
GAME = REPO / "mod" / "run" / "server"
OUT = GAME / "gregteclink" / "listener-harness"


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    path = OUT / "expected.json"
    if not path.is_file():
        pytest.fail(
            f"no listener harness output in {OUT} (runServer -Pharness -PharnessListener)"
        )
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


class Clock:
    now = 1_790_000_000.0

    def __call__(self) -> float:
        return self.now


def test_listener_events_in_a_session_and_its_journal(
    expected: dict[str, Any], tmp_path: Path
) -> None:
    home = tmp_path / "home"
    collect.collect(GAME, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    clock = Clock()
    watch = machines.MachineEventWatch(GAME, clock, {})
    found = [e.as_dict() for e in watch.poll()]
    assert watch.refused == 0 and len(found) == 14
    t = int(clock.now)
    login = {
        "id": "login",
        "kind": "location",
        "ts": t - 60,
        "source": "log",
        "data": {"via": "login", "x": 0.5, "y": 64.0, "z": 0.5, "dim": 0},
    }
    quit_ = {
        "id": "quit",
        "kind": "world_closed",
        "ts": t + 60,
        "source": "log",
        "data": {"via": "log"},
    }
    conn = store.connect(access.db_path(home))
    try:
        got = event_store.record(conn, "lab", [login, *found, quit_], "harness")
        assert got.stored == 16
        again = machines.MachineEventWatch(GAME, clock, {}).poll()  # a lost tail offset
        assert event_store.record(conn, "lab", [e.as_dict() for e in again]).stored == 0
        (s,) = conn.execute("SELECT * FROM session").fetchall()
        assert (s["start_why"], s["end_why"]) == ("login", "quit")
        counts = event_store.counts(conn, s["id"])
        assert (counts["machine_built"], counts["machine_removed"]) == (10, 4)
        ids = [k["mte_id"] for k in expected["kinds"]]
        refs = [
            r["ref"]
            for r in conn.execute(
                "SELECT ref FROM event WHERE kind = 'machine_built' ORDER BY id"
            )
        ]
        assert refs == [f"mte:{i}" for i in ids]
        b = journal.block(conn, dict(s), journal.SESSION_MAX, True)
        assert b is not None
        assert (b.counts["built"], b.counts["removed"]) == (10, 4)
        names = conn.execute(
            f"SELECT mte_id, name_en FROM machine WHERE mte_id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        assert len(names) == len(ids)
        built_lines = [line for line in b.lines if line.startswith("built: ")]
        assert built_lines and all(
            any(f"mte:{m} {name}" in line for m, name in names) for line in built_lines
        )
    finally:
        conn.close()
