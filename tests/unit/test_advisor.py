"""WO-6.5: the advisor's checks and code-ranked steps, ``advisor_next``, and the proactive notices
(their gate, cooldown, hold and state), on the three scenarios of ``tests/evals/advisor_scenarios.py``.

The "Lab scenario" acceptance (both D71 default notices fire) is
:func:`test_both_default_notices_fire_in_the_lab_scenario`: the OC contract's own telemetry fixture
goes through WO-7.1's real ingest as the ``oc-lab`` binding, the plans through the real store and
EU ledger, the stock through My base; the real Lab run is ``docs/runbooks/advisor.md``.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec import cards, config
from gregtec import store as core_store
from gregtec.advisor import checks, notices, steps
from gregtec.agents.tools.catalog import default_registry
from gregtec.agents.tools.tsv import parse
from gregtec.quests import questeval

REPO = Path(__file__).resolve().parents[2]


def _scenarios() -> ModuleType:
    name = "advisor_scenarios"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO / "tests" / "evals" / "advisor_scenarios.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


S = _scenarios()


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def ask(home: Path, n: int = 3) -> tuple[str, Any]:
    reg = default_registry(home)
    out = reg.call("advisor_next", {"n": n}, lane="fast")
    assert not out.is_error, out.text
    return out.text, parse(out.text)


@pytest.fixture
def conn_of() -> Iterator[Any]:
    opened: list[sqlite3.Connection] = []

    def open_(db: Path) -> sqlite3.Connection:
        c = core_store.connect(db)
        opened.append(c)
        return c

    yield open_
    for c in opened:
        c.close()


# --- the steps -----------------------------------------------------------------------------


@pytest.mark.parametrize("name", S.NAMES)
def test_each_scenario_gets_its_three_steps_in_code_order(
    tmp_path: Path, name: str
) -> None:
    sc = S.build(tmp_path, name)
    text, p = ask(tmp_path)
    assert [r["kind"] for r in p.rows] == list(sc.kinds)
    assert [r["rank"] for r in p.rows] == ["1", "2", "3"]
    for cite in sc.cites:
        assert cite in text
    for r in p.rows:  # every step links to the dashboard and cites what it rests on
        assert r["link"].startswith("#/")
        assert r["refs"]
    assert ask(tmp_path)[0] == text  # deterministic: the same DB gives the same steps
    assert dict(p.head)["spoil"] == "0"


def test_no_step_says_anything_past_the_frontier(tmp_path: Path) -> None:
    """D26, D72: the advisor names only frontier quests and his own goal words; every locked
    quest's name (when the book doesn't show it), tasks, rewards and description stay out."""
    for name in S.NAMES:
        home = tmp_path / name
        home.mkdir()
        sc = S.build(home, name)
        text, _ = ask(home, steps.STEPS_MAX)
        conn = sqlite3.connect(sc.db)
        try:
            fields = questeval.spoiler_fields(conn)
        finally:
            conn.close()
        assert fields, "the pack has locked quests"
        assert questeval.leaks(text, fields, said=S.pack().GOALS) == []  # his words


def test_the_tool_needs_nothing_loaded(tmp_path: Path) -> None:
    db = tmp_path / "data" / "gos.sqlite"
    db.parent.mkdir(parents=True)
    core_store.connect(db).close()
    reg = default_registry(tmp_path)
    out = reg.call("advisor_next", {}, lane="deep")
    assert not out.is_error
    assert "no quest book loaded yet" in out.text
    assert "nothing to suggest yet" in out.text


def test_buckets_put_problems_before_goals_before_lines() -> None:
    order = steps.BUCKETS
    assert order.index("fix") < order.index("build") < order.index("goal")
    assert order.index("goal") < order.index("line") < order.index("quest")


# --- the checks ----------------------------------------------------------------------------


def test_stalled_needs_a_job_a_minute_and_fresh_readings(
    tmp_path: Path,
    conn_of: Any,  # noqa: ANN401
) -> None:
    sc = S.build(tmp_path, "trouble")
    conn = conn_of(sc.db)
    ebf = f"tel:lab/{S.EBF_ADDR}"
    assert [f.key for f in checks.stalled_machines(conn, sc.now)] == [ebf]
    # stale: nothing heard for longer than WO-7.1's STALE_S, so nothing is claimed
    assert checks.stalled_machines(conn, sc.now + 3600) == []
    assert checks.power_deficit(conn, sc.now + 3600)[0].key == "ledger:running"
    # running again a moment ago: not stalled
    S.send_oc(sc.db, sc.now, S.oc_message(3, active=True, eu_in=0, eu_out=300))
    S.send_oc(sc.db, sc.now + 1, S.oc_message(4, active=False, eu_in=0, eu_out=300))
    assert checks.stalled_machines(conn, sc.now + 2) == []
    S.send_oc(sc.db, sc.now + 65, S.oc_message(5, active=False, eu_in=0, eu_out=300))
    assert [f.key for f in checks.stalled_machines(conn, sc.now + 70)] == [ebf]
    # paused by Clayton (work not allowed): never stalled
    doc = S.oc_message(6, active=False, eu_in=0, eu_out=300)
    for m in doc["machines"]:
        m["allowed"] = False
    S.send_oc(sc.db, sc.now + 80, doc)
    assert checks.stalled_machines(conn, sc.now + 200) == []


def test_a_balanced_power_entry_is_no_deficit(
    tmp_path: Path,
    conn_of: Any,  # noqa: ANN401
) -> None:
    sc = S.build(tmp_path, "trouble")
    conn = conn_of(sc.db)
    drain = checks.power_deficit(conn, sc.now)
    assert [f.key for f in drain] == ["ledger:running", f"tel:lab/{S.BUFFER_ADDR}"]
    assert drain[0].value == -32 and drain[0].hold_s == 0  # the plans' 32 - 64 EU/t
    assert drain[1].hold_s == checks.DRAIN_HOLD_S
    assert drain[1].value == -300 and drain[1].unit == "EU/t"
    S.send_oc(sc.db, sc.now, S.oc_message(3, active=True, eu_in=300, eu_out=300))
    keys = [f.key for f in checks.power_deficit(conn, sc.now + 1)]
    assert keys == [
        "ledger:running"
    ]  # the plans' deficit stays; the buffer is balanced


def test_buildable_needs_every_part_in_stock(
    tmp_path: Path,
    conn_of: Any,  # noqa: ANN401
) -> None:
    sc = S.build(tmp_path, "stocked")
    conn = conn_of(sc.db)
    found = checks.buildable_plans(conn, tmp_path)
    assert [f.key for f in found] == ["plan:pl-macer"]
    assert found[0].link == "#/designer/pl-macer"
    S.stock(sc.db, f"{S.MACHINES}@{S.MACERATOR}", 1, sc.now)  # one short
    assert checks.buildable_plans(conn, tmp_path) == []


def test_a_multiblock_with_no_structure_is_never_buildable(
    tmp_path: Path,
    conn_of: Any,  # noqa: ANN401
) -> None:
    sc = S.build(tmp_path, "early")
    plan = S.plan_json(
        "pl-ebf",
        "EBF",
        nodes=[
            {
                "n": "n1",
                "machine": {"mte": 1000, "kind": "multiblock", "tier": "LV"},
                "count": 1,
            }
        ],
    )
    S.save_plan(tmp_path, plan, "accepted")
    S.stock(sc.db, f"{S.MACHINES}@1000", 5, sc.now)
    conn = conn_of(sc.db)
    assert checks.buildable_plans(conn, tmp_path) == []  # its casings can't be counted
    assert checks.needs(conn, plan) == ({f"{S.MACHINES}@1000": 1}, 1)


def test_a_multiblock_is_buildable_when_its_whole_structure_is_stocked(
    tmp_path: Path,
    conn_of: Any,  # noqa: ANN401
) -> None:
    sc = S.build(tmp_path, "early")
    casing, coil = "gregtech:gt.blockcasings@11", "gregtech:gt.blockcasings5@0"
    blocks = [{"block": "gregtech:gt.blockcasings", "meta": 11}] * 4 + [
        {"block": "gregtech:gt.blockcasings5", "meta": 0}
    ] * 2
    small = [{"block": "gregtech:gt.blockcasings", "meta": 11}] * 3
    conn = conn_of(sc.db)
    mid = conn.execute("SELECT id FROM machine WHERE mte_id = 1000").fetchone()[0]
    conn.executemany(
        "INSERT INTO structure(machine_id, variant, blocks_json, source)"
        " VALUES (?, ?, ?, 'gtnh-extractor')",
        [
            (mid, "big", json.dumps(blocks)),
            (mid, "small", json.dumps(small + blocks[4:])),
        ],
    )
    plan = S.plan_json(
        "pl-ebf",
        "EBF",
        nodes=[{"n": "n1", "machine": {"mte": 1000, "kind": "multiblock"}, "count": 2}],
    )
    S.save_plan(tmp_path, plan, "accepted")
    # the smallest variant (5 blocks), times two furnaces, plus the two controllers
    want = {f"{S.MACHINES}@1000": 2, casing: 6, coil: 4}
    assert checks.needs(conn, plan) == (want, 0)
    # the fixture's export has no casing items, so the stock rows go in as the base would keep them
    put = "INSERT OR REPLACE INTO my_base_stock(key, qty, upd) VALUES (?, ?, 0)"
    conn.executemany(put, list(want.items()))
    assert [f.key for f in checks.buildable_plans(conn, tmp_path)] == ["plan:pl-ebf"]
    conn.execute(put, (coil, 3))
    assert checks.buildable_plans(conn, tmp_path) == []


def test_a_quest_done_opens_the_quests_it_unlocks(
    tmp_path: Path,
    conn_of: Any,  # noqa: ANN401
) -> None:
    from gregtec.quests import guide
    from gregtec.vault.io import VaultIO

    sc = S.build(tmp_path, "stocked")
    conn = conn_of(sc.db)
    frontier = guide.ranked(conn, VaultIO(tmp_path / "vault"), sc.db)
    found = checks.opened_quests(conn, sc.now, frontier)
    logs = f"q:{S.pack().qid(10)}"
    assert found and all(logs in f.refs for f in found)
    assert {f.key for f in found} == {
        f"q:{S.pack().qid(n)}" for n in (11, 12, 13)
    }  # Bricks, Charcoal Pile, Either Way: the Steam line's start
    assert checks.opened_quests(conn, sc.now, frontier, window_s=3600) == []


# --- the notices ---------------------------------------------------------------------------


def make(sc: Any, clock: Clock, **kw: Any) -> tuple[notices.Notices, list[Any]]:  # noqa: ANN401
    given: list[Any] = []
    n = notices.Notices(sc.db, clock=clock, deliver=given.append, **kw)
    return n, given


def test_both_default_notices_fire_in_the_lab_scenario(tmp_path: Path) -> None:
    """Plan 8 row 6.5: both D71 defaults (power deficit or machine stalled; a plan buildable
    from current stock) fire in a Lab scenario, each once, each with a spoken line."""
    sc = S.build(tmp_path, "trouble")
    clock = Clock(sc.now)
    n, given = make(sc, clock)
    try:
        first = n.tick()
        assert [x.kind for x in first] == ["power_deficit", "machine_stalled"]
        assert all(x.say and not any(c.isdigit() for c in x.say) for x in first)
        assert all(":" not in x.say.split(".", 1)[1] for x in first)  # no ids spoken
        assert n.tick() == []  # announced once while it holds
        alerts = n.alerts()
        assert [a["id"] for a in alerts] == [
            "notice:power_deficit",
            "notice:machine_stalled",
        ]
        for a in alerts:
            assert cards.validate(a) == []
        assert alerts[0]["ref"] == "plan:pl-macer"
        # the macerator line is accepted again and both its machines are put in stock: the
        # buildable notice waits for the problems' cooldown (an opportunity never talks over
        # a problem), then fires once
        conn = core_store.connect(sc.db)
        try:
            conn.execute("UPDATE plan SET status = 'accepted' WHERE id = 'pl-macer'")
            conn.commit()
        finally:
            conn.close()
        S.stock(sc.db, f"{S.MACHINES}@{S.MACERATOR}", 2, sc.now)
        # OC goes on reporting every few seconds (here: before each tick): the stall holds, the
        # buffer is balanced again
        for seq, at in enumerate(range(60, notices.COOLDOWN_S, 60), 3):
            clock.now = sc.now + at
            S.send_oc(
                sc.db, clock.now - 5, S.oc_message(seq, active=False, eu_in=9, eu_out=9)
            )
            assert n.tick() == []
        clock.now = sc.now + notices.COOLDOWN_S + 1
        S.send_oc(
            sc.db, clock.now - 5, S.oc_message(99, active=False, eu_in=9, eu_out=9)
        )
        late = n.tick()
        assert [x.kind for x in late] == ["plan_buildable"]
        assert late[0].say == "You have everything to build Macerators · LV in stock."
        assert late[0].refs == ["plan:pl-macer"]
    finally:
        n.close()
    assert [x.kind for x in given] == [
        "power_deficit",
        "machine_stalled",
        "plan_buildable",
    ]


def test_the_stocked_scenario_fires_the_buildable_notice_at_once(
    tmp_path: Path,
) -> None:
    sc = S.build(tmp_path, "stocked")
    n, given = make(sc, Clock(sc.now))
    try:
        assert [x.kind for x in n.tick()] == ["plan_buildable"]
        assert (
            n.alerts() == []
        )  # an opportunity is no alert card (D55: alerts are problems)
    finally:
        n.close()
    assert given[0].as_dict()["link"] == "#/designer/pl-macer"


def test_a_kind_waits_out_its_cooldown_and_a_cleared_subject_can_come_back(
    tmp_path: Path,
) -> None:
    sc = S.build(tmp_path, "trouble")
    clock = Clock(sc.now)
    n, _ = make(sc, clock, enabled=("machine_stalled",))
    try:
        assert [x.kind for x in n.tick()] == ["machine_stalled"]
        # it runs again: the subject clears
        clock.now += 30
        S.send_oc(sc.db, clock.now, S.oc_message(3, active=True, eu_in=0, eu_out=300))
        assert n.tick() == [] and n.alerts() == []
        # and stalls again: due, but its kind is in its cooldown
        clock.now += 30
        S.send_oc(sc.db, clock.now, S.oc_message(4, active=False, eu_in=0, eu_out=300))
        clock.now += checks.STALL_S + 5
        S.send_oc(sc.db, clock.now, S.oc_message(5, active=False, eu_in=0, eu_out=300))
        assert n.tick() == []
        clock.now = sc.now + notices.COOLDOWN_S + 1
        S.send_oc(sc.db, clock.now, S.oc_message(6, active=False, eu_in=0, eu_out=300))
        assert [x.kind for x in n.tick()] == ["machine_stalled"]
    finally:
        n.close()


def test_a_draining_store_must_hold_before_its_notice(tmp_path: Path) -> None:
    sc = S.build(tmp_path, "early")
    S.send_oc(sc.db, sc.now - 5, S.oc_message(1, active=True, eu_in=0, eu_out=300))
    clock = Clock(sc.now)
    n, _ = make(sc, clock, enabled=("power_deficit",))
    try:
        assert n.tick() == []  # one reading says nothing yet
        clock.now += checks.DRAIN_HOLD_S
        S.send_oc(
            sc.db, clock.now - 5, S.oc_message(2, active=True, eu_in=0, eu_out=300)
        )
        got = n.tick()
        assert [x.kind for x in got] == ["power_deficit"]
        assert got[0].say.endswith("A power store is draining.")
    finally:
        n.close()


def test_what_was_said_is_not_said_again_after_a_restart(tmp_path: Path) -> None:
    sc = S.build(tmp_path, "trouble")
    clock = Clock(sc.now)
    n, _ = make(sc, clock)
    assert len(n.tick()) == 2
    n.close()
    again, _ = make(sc, clock)
    try:
        assert again.tick() == []
        assert len(again.alerts()) == 2
    finally:
        again.close()


def test_opt_in_quest_notice_and_turned_off_defaults(tmp_path: Path) -> None:
    sc = S.build(tmp_path, "stocked")
    S.quest_done(sc.db, 1, sc.now - 60)  # First Steps, a minute ago: opens Oxygen Run
    off, _ = make(sc, Clock(sc.now), enabled=())
    try:
        assert off.tick() == []
    finally:
        off.close()
    n, _ = make(sc, Clock(sc.now), enabled=("quest_opened",))
    try:
        got = n.tick()
        assert [x.kind for x in got] == ["quest_opened"]
        assert f"q:{S.pack().qid(3)}" in got[0].refs
    finally:
        n.close()


def test_the_priorities_cover_every_configured_kind() -> None:
    assert set(notices.PRIORITY) == set(config.NOTICES)
    assert notices.DEFAULT == config.NOTICES_DEFAULT
    assert notices.PRIORITY["power_deficit"] > notices.PRIORITY["plan_buildable"]


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("", config.NOTICES_DEFAULT),
        ("[policy]\nnotices = []\n", ()),
        (
            '[policy]\nnotices = ["quest_opened", "power_deficit"]\n',
            ("quest_opened", "power_deficit"),
        ),
    ],
)
def test_policy_notices_is_read(
    tmp_path: Path, text: str, want: tuple[str, ...]
) -> None:
    path = tmp_path / "gos.toml"
    path.write_text(text, encoding="utf-8")
    cfg = config.load(path)
    assert cfg.notices == want
    assert cfg.service_errors == ()


def test_a_bad_notices_list_stops_only_the_service(tmp_path: Path) -> None:
    path = tmp_path / "gos.toml"
    path.write_text('[policy]\nnotices = ["fireworks"]\n', encoding="utf-8")
    cfg = config.load(path)
    assert cfg.notices == config.NOTICES_DEFAULT
    assert any("[policy] notices" in e for e in cfg.service_errors)


def test_the_state_survives_a_bad_kv_row(tmp_path: Path) -> None:
    sc = S.build(tmp_path, "trouble")
    conn = core_store.connect(sc.db)
    try:
        conn.execute(
            "INSERT INTO kv(key, value, upd) VALUES (?, ?, 0)",
            (notices.KV_STATE, "not json"),
        )
        conn.commit()
    finally:
        conn.close()
    n, _ = make(sc, Clock(time.time()))
    try:
        assert len(n.tick()) == 2
    finally:
        n.close()
    conn = core_store.connect(sc.db)
    try:
        row = conn.execute(
            "SELECT value FROM kv WHERE key = ?", (notices.KV_STATE,)
        ).fetchone()
    finally:
        conn.close()
    assert set(json.loads(row[0])) == {"seen", "said", "last"}


# --- the core ------------------------------------------------------------------------------


def test_the_core_shows_the_problems_and_pushes_each_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from gregtec.api import dashboard
    from gregtec.api.app import Core, create_app
    from gregtec.api.security import Auth
    from gregtec.llm import keys
    from gregtec.llm.usage import UsageMeter

    monkeypatch.setattr(keys, "has_key", lambda name: False)
    sc = S.build(tmp_path, "trouble")
    st = core_store.Store(sc.db)
    admin = "admin-" + "f" * 40
    pushed: list[Any] = []
    n = notices.Notices(sc.db, clock=Clock(sc.now), deliver=pushed.append)
    cfg = config.Config(gos_home=tmp_path)
    core = Core(
        cfg=cfg,
        store=st,
        auth=Auth(st, admin),
        meter=UsageMeter(st, 100.0, (80, 100)),
        notices=n,
    )
    core.board = dashboard.Board(cfg, store=st, clients=lambda _: ["play"])
    core.board.refresh_data = lambda: None  # type: ignore[method-assign]
    head = {"Authorization": f"Bearer {admin}"}
    try:
        with TestClient(create_app(core), base_url="http://127.0.0.1:8765") as client:
            for _ in range(100):  # the core's own notices tick runs at start
                if pushed:
                    break
                time.sleep(0.05)
            assert [x.kind for x in pushed] == ["power_deficit", "machine_stalled"]
            alerts = client.get("/api/state", headers=head).json()["alerts"]
            ids = [a["id"] for a in alerts]
            assert "notice:power_deficit" in ids and "notice:machine_stalled" in ids
            with client.websocket_connect("ws://127.0.0.1:8765/ws", headers=head) as ws:
                core.notice_given(pushed[0])
                got: dict[str, Any] = {}
                for _ in range(20):
                    got = json.loads(ws.receive_text())
                    if got["type"] == "notice":
                        break
                assert got["data"]["kind"] == "power_deficit"
                assert got["data"]["say"].startswith("Heads up: power deficit.")
                assert "plan:pl-macer" in got["data"]["refs"]
    finally:
        n.close()
        st.close()
