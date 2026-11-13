"""GregtecLink's machine events through the watchers to My base (WO-6.2, D260, D261): the events
file reader, the core's position-keyed apply with D211's precedence, and the route that feeds it
(Play only). A fake game folder and a fake core; no game, no network."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

import pytest
from fastapi.testclient import TestClient

from gregtec import store
from gregtec.api import dashboard
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.base import seen, state
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec.watchers import machines, runner
from gregtec.watchers.events import Event


class FakeClock(Protocol):
    def __call__(self) -> float: ...
    def advance(self, s: float) -> None: ...


GT = "gregtech:gt.blockmachines"
ADMIN = "admin-" + "q" * 40
BASE = "http://127.0.0.1:8765"


def line(
    kind: str = "machine_built", seq: int = 1, mte: int = 1000, **over: object
) -> str:
    o: dict[str, object] = {
        "v": 1,
        "kind": kind,
        "boot": 1700000000000,
        "seq": seq,
        "ts_ms": 1700000000500 + seq,
        "dim": 0,
        "x": 10 + seq,
        "y": 64,
        "z": -5,
        "mte_id": mte,
        "registry": GT,
        "meta": mte,
    }
    o.update(over)
    return json.dumps(o)


# --- the events file reader ------------------------------------------------------------------


def test_parse_keeps_valid_v1_lines_only() -> None:
    data = machines.parse(line())
    assert data == {
        "kind": "machine_built",
        "mte_id": 1000,
        "ref": "mte:1000",
        "registry": GT,
        "meta": 1000,
        "key": f"{GT}@1000",
        "dim": 0,
        "x": 11,
        "y": 64,
        "z": -5,
        "boot": 1700000000000,
        "seq": 1,
        "logged_ms": 1700000000501,
    }
    assert machines.parse(line(registry=None))["key"] is None  # type: ignore[index]
    bad = [
        "not json",
        "[1]",
        line(v=2),
        line(kind="machine_exploded"),
        line(meta=999),  # meta must be the mte id
        line(mte=0),
        line(y=300),
        line(x=True),
        line(registry="minecraft stone"),
        line(registry="x" * 300 + ":y"),
        json.dumps({**json.loads(line()), "id": 4096}),  # a numeric id field: never
        line(seq=0),
        line() + " " * 2000,
    ]
    assert [machines.parse(b) for b in bad] == [None] * len(bad)


def test_watch_tails_the_file_from_its_start_and_rotates(
    tmp_path: Path, clock: FakeClock
) -> None:
    game = tmp_path / ".minecraft"
    path = game / machines.EVENTS_FILE
    watch = machines.MachineEventWatch(game, clock, {})
    assert watch.poll() == []  # no file yet
    path.parent.mkdir(parents=True)
    path.write_text(
        line(seq=1) + "\n" + line("machine_removed", seq=2) + "\n" + "junk\n",
        encoding="utf-8",
    )
    events = watch.poll()
    assert [(e.kind, e.source, e.data["mte_id"]) for e in events] == [
        ("machine_built", "listener", 1000),
        ("machine_removed", "listener", 1000),
    ]
    assert events[0].id == "m18bcfe56800-1"
    assert watch.refused == 1 and watch.problem is not None
    with open(path, "a", encoding="utf-8") as f:
        f.write(line(seq=3, mte=66))  # no newline yet: not a whole line
    assert watch.poll() == []
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n")
    assert [e.data["mte_id"] for e in watch.poll()] == [66]
    # the mod rotates at start: a new file, read from its start
    path.rename(path.with_name("machines.1.jsonl"))
    path.write_text(line(seq=1, boot=1800000000000, mte=5) + "\n", encoding="utf-8")
    assert [e.data["mte_id"] for e in watch.poll()] == [5]


def test_watchers_include_the_listener_and_the_census(
    tmp_path: Path, clock: FakeClock
) -> None:
    game = tmp_path / ".minecraft"
    world = game / "saves" / "lab"
    world.mkdir(parents=True)
    w = runner.Watchers(game, world, tmp_path / "home", clock)
    kinds = {type(r).__name__ for r in w.others}
    assert {"MachineEventWatch", "CensusWatch"} <= kinds
    path = game / machines.EVENTS_FILE
    path.parent.mkdir(parents=True)
    path.write_text(line() + "\n", encoding="utf-8")
    assert [e.kind for e in w.poll_files()] == ["machine_built"]
    w.save_tails()
    saved = json.loads(
        (tmp_path / "home/data/watchers/tails.json").read_text(encoding="utf-8")
    )
    assert saved[str(path)]["offset"] == path.stat().st_size
    # a restart goes on from the saved offset
    w2 = runner.Watchers(game, world, tmp_path / "home", clock)
    assert [e.kind for e in w2.poll_files() if e.source == "listener"] == []


def test_machine_readers_wait_while_the_sender_is_behind(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Review M2: with the core away, a full queue must not drop events or census rows the
    readers have moved past; they wait, and the log readers go on."""
    game = tmp_path / ".minecraft"
    world = game / "saves" / "lab"
    world.mkdir(parents=True)
    path = game / machines.EVENTS_FILE
    path.parent.mkdir(parents=True)
    path.write_text(line() + "\n", encoding="utf-8")
    w = runner.Watchers(game, world, tmp_path / "home", clock)
    posted: list[dict[str, Any]] = []
    sender = runner.Sender(lambda body, timeout: posted.append(body), "lab")
    sender.put([Event("chat", 1.0, "log", {"text": "x"})] * runner.HOLD_AT)
    stop = threading.Event()
    spool = runner.Spool(tmp_path / "home" / "data" / "events")
    ticks = iter(range(3))

    def clock_once() -> float:
        if next(ticks, None) is None:
            stop.set()
        return clock()

    runner.run_loop(w, spool, sender, stop, clock=clock_once)
    assert w.hold_machines
    assert not [e for e in sender.q if e.source == "listener"]
    # the core is back: the queue drains and the line is read
    while sender.pending():
        sender.flush_once()
    w.hold_machines = False
    assert [e.kind for e in w.poll_files() if e.source == "listener"] == [
        "machine_built"
    ]


# --- My base: the apply ----------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = store.connect(tmp_path / "gos.sqlite")
    yield c
    c.close()


def ev(kind: str, **data: object) -> dict[str, Any]:
    return Event(kind, time.time(), "listener", data).as_dict()


def built(x: int, mte: int) -> dict[str, Any]:
    return ev("machine_built", dim=0, x=x, y=64, z=0, mte_id=mte)


def removed(x: int, mte: int) -> dict[str, Any]:
    return ev("machine_removed", dim=0, x=x, y=64, z=0, mte_id=mte)


def census_ev(
    rx: int, rz: int, chunks: list[int], rows: list[list[int]], dim: int = 0
) -> dict[str, Any]:
    return ev(
        "machine_census",
        scope="chunks",
        dim=dim,
        region=[rx, rz],
        chunks=chunks,
        machines=rows,
    )


def counts(conn: sqlite3.Connection) -> dict[int, tuple[int, str, dict[str, int]]]:
    return {m.mte_id: (m.count, m.source, m.sources) for m in state.machines(conn)}


def test_events_count_by_position_and_replays_are_harmless(
    conn: sqlite3.Connection,
) -> None:
    batch = [built(1, 1000), built(2, 1000), built(3, 66), removed(3, 66)]
    a = seen.apply(conn, batch, now=5)
    assert (a.events, a.ignored) == (4, 0)
    assert counts(conn) == {1000: (2, "event", {"event": 2})}
    seen.apply(conn, batch, now=6)  # the same file read again
    assert counts(conn) == {1000: (2, "event", {"event": 2})}
    # a removal of a machine never seen placed, or of another machine than the one there
    a = seen.apply(conn, [removed(9, 1000), removed(1, 66)])
    assert (a.events, a.ignored) == (0, 2)
    # a machine replaced at a position moves the count
    seen.apply(conn, [built(1, 66)])
    assert counts(conn) == {
        1000: (1, "event", {"event": 1}),
        66: (1, "event", {"event": 1}),
    }
    seen.apply(conn, [removed(1, 66), removed(2, 1000)])
    assert counts(conn) == {}  # event rows at 0 are dropped
    assert seen.apply(conn, [ev("chat", text="hi")]).changed is False


def test_census_replaces_its_chunks_and_outranks_events(
    conn: sqlite3.Connection,
) -> None:
    seen.apply(conn, [built(1, 1000), built(2, 1000), built(40, 1000)])
    # the census of region 0,0: chunk 0 (x 0-15) has one EBF, chunk 2 (x 32-47) none
    a = seen.apply(conn, [census_ev(0, 0, [0, 2], [[1, 64, 0, 1000, 1, 0]])])
    assert (a.census_chunks, a.census_machines) == (2, 1)
    assert counts(conn) == {1000: (1, "census", {"event": 3, "census": 1})}
    # the census looks at chunk 0 again: the EBF is gone -> census 0 still outranks events
    seen.apply(conn, [census_ev(0, 0, [0], [])])
    assert counts(conn)[1000] == (0, "census", {"event": 3, "census": 0})
    assert state.owned_machines(conn) == {}
    # a manual count outranks both (D211)
    conn.execute(
        "INSERT INTO my_base_machine(mte_id, source, count, upd) VALUES (1000, 'manual', 4, 1)"
    )
    assert counts(conn)[1000][:2] == (4, "manual")
    # rows outside the chunks named, or malformed, are refused
    a = seen.apply(
        conn,
        [census_ev(0, 0, [0], [[100, 64, 0, 66, 0, 0], [1, 64, 0, 0, 0, 0], [1, 2]])],
    )
    assert (a.census_machines, a.ignored) == (0, 3)


def test_census_world_event_drops_gone_regions(conn: sqlite3.Connection) -> None:
    seen.apply(
        conn,
        [
            census_ev(0, 0, [0], [[1, 64, 0, 1000, 0, 0]]),
            census_ev(-1, -1, [1023], [[-1, 64, -1, 66, 0, 0]]),
            census_ev(0, 0, [0], [[5, 10, 5, 31, 0, 0]], dim=-1),
        ],
    )
    assert seen.positions(conn, "census") == {
        (0, 1, 64, 0): 1000,
        (0, -1, 64, -1): 66,
        (-1, 5, 10, 5): 31,
    }
    # incomplete: nothing dropped
    seen.apply(conn, [ev("machine_census", scope="world", regions=[], complete=False)])
    assert len(seen.positions(conn, "census")) == 3
    seen.apply(
        conn,
        [
            ev(
                "machine_census",
                scope="world",
                regions=[[0, 0, 0], [-1, 0, 0]],
                complete=True,
            )
        ],
    )
    assert seen.positions(conn, "census") == {(0, 1, 64, 0): 1000, (-1, 5, 10, 5): 31}
    assert counts(conn)[66] == (0, "census", {"census": 0})


def test_my_base_designer_input_sees_the_automatic_counts(
    conn: sqlite3.Connection,
) -> None:
    seen.apply(conn, [built(1, 1000), census_ev(0, 0, [5], [[80, 64, 0, 66, 0, 0]])])
    assert state.MyBase.from_db(conn).owned_machines() == {1000: 1, 66: 1}


# --- the route: Play only ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


@pytest.fixture
def core(tmp_path: Path) -> Iterator[Core]:
    st = Store(tmp_path / "data" / "gos.sqlite")
    cfg = Config(gos_home=tmp_path)
    c = Core(
        cfg=cfg, store=st, auth=Auth(st, ADMIN), meter=UsageMeter(st, 100.0, (80, 100))
    )
    c.board = dashboard.Board(cfg, store=st, clients=lambda _: ["play"])
    yield c
    st.close()


def post(client: TestClient, instance: str, events: list[dict[str, Any]]) -> None:
    r = client.post(
        "/api/game/events",
        json={"instance": instance, "events": events, "paused": {"paused": False}},
        headers={"Authorization": f"Bearer {ADMIN}"},
    )
    assert r.status_code == 200, r.text


def test_play_events_reach_my_base_and_lab_events_do_not(
    core: Core, tmp_path: Path
) -> None:
    with TestClient(create_app(core), base_url=BASE) as client:
        post(client, "lab", [built(1, 1000)])
        db = store.connect(core.store.path)
        try:
            assert counts(db) == {}
            first = built(2, 66)
            post(
                client, "play", [first, census_ev(0, 0, [0], [[3, 64, 0, 1000, 0, 0]])]
            )
            assert counts(db) == {
                66: (1, "event", {"event": 1}),
                1000: (1, "census", {"census": 1}),
            }
            post(
                client, "play", [first, removed(2, 66)]
            )  # a retried event is not applied twice
            assert counts(db) == {1000: (1, "census", {"census": 1})}
            r = client.get(
                "/api/game/events?kind=machine_census",
                headers={"Authorization": f"Bearer {ADMIN}"},
            )
            assert len(r.json()["events"]) == 1
            # the feed keeps a census as its counts (review L5)
            assert r.json()["events"][0]["data"]["machines"] == 1
        finally:
            db.close()


def test_a_failed_db_write_is_refused_and_the_retry_applies(
    core: Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M2: the post answers 503 when My base wasn't written, and the watchers' retry of
    the same batch (events the feed has already seen) is applied."""
    real = seen.apply
    fails = [sqlite3.OperationalError("database is locked")]

    def flaky(
        conn: sqlite3.Connection,
        events: list[dict[str, Any]],
        now: int | None = None,
    ) -> seen.Applied:
        if fails:
            raise fails.pop()
        return real(conn, events, now)

    monkeypatch.setattr(seen, "apply", flaky)
    batch = [built(2, 66), census_ev(0, 0, [0], [[3, 64, 0, 1000, 0, 0]])]
    body = {"instance": "play", "events": batch, "paused": {"paused": True}}
    auth = {"Authorization": f"Bearer {ADMIN}"}
    with TestClient(create_app(core), base_url=BASE) as client:
        r = client.post("/api/game/events", json=body, headers=auth)
        assert r.status_code == 503
        assert (
            core.game is not None and core.game.paused is True
        )  # the state still went
        post(client, "play", batch)
    db = store.connect(core.store.path)
    try:
        assert counts(db) == {
            66: (1, "event", {"event": 1}),
            1000: (1, "census", {"census": 1}),
        }
    finally:
        db.close()
