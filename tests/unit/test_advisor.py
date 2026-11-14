"""WO-6.5: the advisor's checks and code-ranked steps, ``advisor_next``, and the proactive notices
(their gate, cooldown, hold and state), on the three scenarios of ``tests/evals/advisor_scenarios.py``.

The "Lab scenario" acceptance (both D71 default notices fire) is
:func:`test_both_default_notices_fire_in_the_lab_scenario`: the OC contract's own telemetry fixture
goes through WO-7.1's real ingest as the ``oc-lab`` binding, WO-7.4's live base and WO-7.5's alert
engine (which owns the stall, D281), the plans through the real store and EU ledger, the stock
through My base; the real Lab run is ``docs/runbooks/advisor.md``.
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
from gregtec.alerts.rules import RULES
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


def test_stalls_are_the_alert_engines_firing_rows(
    tmp_path: Path,
    conn_of: Any,  # noqa: ANN401
) -> None:
    """D281 (PM 2026-11-14): the advisor reads WO-7.5's ``machine_stalled`` alerts, not its own
    second detection: firing and fresh only, ``stopped`` (GT's own shutdown) included."""
    sc, lab = S.trouble_lab(tmp_path)
    conn = conn_of(sc.db)
    ebf = f"tel:lab/{S.EBF_ADDR}"
    try:
        found = checks.stalled_machines(conn, sc.now)
        assert [f.key for f in found] == [ebf]
        assert (
            found[0].title == "Machine stalled: blastfurnace"
        )  # the engine's card title
        assert ebf in found[0].detail and "alert firing for 0 min" in found[0].detail
        # stale: nothing heard for longer than WO-7.1's STALE_S, so nothing is claimed
        assert checks.stalled_machines(conn, sc.now + 3600) == []
        assert checks.power_deficit(conn, sc.now + 3600)[0].key == "ledger:running"
        # running again for the engine's clear_s: the alert clears, so no stall
        for dt in range(0, lab.alerts.rules.clear_s + 10, 5):
            lab.send(
                sc.now + dt, S.oc_message(10 + dt, active=True, eu_in=0, eu_out=300)
            )
        assert checks.stalled_machines(conn, lab.now) == []
        # work disabled, not by GOS (GT stops a multiblock on power loss): WO-7.4's
        # ``stopped`` counts (``[alerts] stalled_stopped``), once the cooldown is over
        t0 = lab.now + lab.alerts.rules.cooldown_s
        for dt in range(0, lab.alerts.rules.stalled_s + 10, 5):
            doc = S.oc_message(200 + dt, active=False, eu_in=0, eu_out=300)
            for m in doc["machines"]:
                m["allowed"] = False
            lab.send(t0 + dt, doc)
        found = checks.stalled_machines(conn, lab.now)
        assert [f.title for f in found] == ["Machine stopped: blastfurnace"]
        assert "work disabled, not by GOS" in found[0].detail
    finally:
        lab.close()


def test_a_restarted_engine_loses_its_rows_and_the_advisor_follows(
    tmp_path: Path,
    conn_of: Any,  # noqa: ANN401
) -> None:
    """WO-7.5's ``_lose_old_rows`` marks only the engine's own ``alert`` rows ``lost``: this
    branch writes no ``alert`` rows (the notices keep ``kv`` ``notices.state``), so nothing of
    the notices is lost, and a lost stall is no stall until the new engine fires it again."""
    sc = S.build(tmp_path, "trouble")
    n, _ = make(sc, Clock(sc.now))
    try:
        assert [x.kind for x in n.tick()] == ["power_deficit"]
    finally:
        n.close()
    conn = conn_of(sc.db)
    kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM alert")}
    assert kinds and kinds <= set(RULES)  # every alert row is the engine's
    state = conn.execute(
        "SELECT value FROM kv WHERE key = ?", (notices.KV_STATE,)
    ).fetchone()
    S.Lab(sc.db).close()  # a core restart: a new engine
    assert checks.stalled_machines(conn, sc.now) == []
    assert {r[0] for r in conn.execute("SELECT DISTINCT state FROM alert")} == {"lost"}
    assert (
        conn.execute(
            "SELECT value FROM kv WHERE key = ?", (notices.KV_STATE,)
        ).fetchone()
        == state
    )


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
    from current stock) fire in a Lab scenario, each once, each with a spoken line. The stall is
    WO-7.5's alert engine's (D281): its ``fired`` event carries the line, the notices add none."""
    sc, lab = S.trouble_lab(tmp_path)
    clock = Clock(sc.now)
    n, given = make(sc, clock)
    try:
        stall = [
            e
            for e in lab.events
            if e.kind == "fired" and e.alert.rule == "machine_stalled"
        ]
        assert len(stall) == 1
        said = [e.say for e in lab.events if e.say]  # one line per binding per tick
        assert [x for x in said if "stalled" in x] == [
            "Lab: power low and blastfurnace has stalled."
        ]
        first = n.tick()
        assert [x.kind for x in first] == ["power_deficit"]
        assert all(x.say and not any(c.isdigit() for c in x.say) for x in first)
        assert all(":" not in x.say.split(".", 1)[1] for x in first)  # no ids spoken
        assert n.tick() == []  # announced once while it holds
        alerts = n.alerts()
        assert [a["id"] for a in alerts] == ["notice:power_deficit"]
        for a in alerts:
            assert cards.validate(a) == []
        assert alerts[0]["ref"] == "plan:pl-macer"
        assert [c["title"] for c in lab.alerts.cards() if "stall" in c["title"]] == [
            "Machine stalled: blastfurnace"
        ]
        # the macerator line is accepted again and both its machines are put in stock: the
        # buildable notice waits for the problems' cooldown (an opportunity never talks over
        # a problem, the alert engine's included), then fires once
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
            lab.send(clock.now - 5, S.oc_message(seq, active=False, eu_in=9, eu_out=9))
            assert n.tick() == []
        clock.now = sc.now + notices.COOLDOWN_S + 1
        lab.send(clock.now - 5, S.oc_message(99, active=False, eu_in=9, eu_out=9))
        late = n.tick()
        assert [x.kind for x in late] == ["plan_buildable"]
        assert late[0].say == "You have everything to build Macerators · LV in stock."
        assert late[0].refs == ["plan:pl-macer"]
        fired = [e.alert.rule for e in lab.events if e.kind == "fired"]
        assert fired.count("machine_stalled") == 1  # said once while it holds
    finally:
        n.close()
        lab.close()
    assert [x.kind for x in given] == ["power_deficit", "plan_buildable"]


def test_a_stall_alert_holds_an_opportunity_back(tmp_path: Path) -> None:
    """The priority gate counts the alert engine's problem alerts as HIGH notices: a plan
    buildable from stock waits out the cooldown after a stall fired."""
    sc = S.build(tmp_path, "stocked")
    lab = S.trouble_oc(S.Lab(sc.db), sc.now)
    clock = Clock(sc.now)
    n, given = make(sc, clock)
    try:
        assert n.tick() == []
        clock.now = sc.now + notices.COOLDOWN_S - 10
        assert n.tick() == []
        clock.now = sc.now + notices.COOLDOWN_S
        assert [x.kind for x in n.tick()] == ["plan_buildable"]
    finally:
        n.close()
        lab.close()
    assert [x.kind for x in given] == ["plan_buildable"]


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


def drain(sc: Any, at: float, seq: int, out: int = 300) -> None:  # noqa: ANN401
    """One OC message with the battery buffer giving out ``out`` EU/t and taking in none."""
    S.send_oc(sc.db, at, S.oc_message(seq, active=True, eu_in=0, eu_out=out))


def test_a_kind_waits_out_its_cooldown_and_a_cleared_subject_can_come_back(
    tmp_path: Path,
) -> None:
    sc = S.build(tmp_path, "early")
    clock = Clock(sc.now)
    n, _ = make(sc, clock, enabled=("power_deficit",))
    try:
        drain(sc, sc.now, 1)
        assert n.tick() == []  # its hold starts
        clock.now += checks.DRAIN_HOLD_S
        drain(sc, clock.now, 2)
        assert [x.kind for x in n.tick()] == ["power_deficit"]
        said = clock.now
        # balanced again: the subject clears
        clock.now += 30
        drain(sc, clock.now, 3, out=0)
        assert n.tick() == [] and n.alerts() == []
        # and drains again past its hold: due, but its kind is in its cooldown
        clock.now += 30
        drain(sc, clock.now, 4)
        assert n.tick() == []
        clock.now += checks.DRAIN_HOLD_S + 5
        drain(sc, clock.now, 5)
        assert n.tick() == []
        clock.now = said + notices.COOLDOWN_S + 1
        drain(sc, clock.now, 6)
        assert [x.kind for x in n.tick()] == ["power_deficit"]
    finally:
        n.close()


def test_a_failed_check_or_a_quiet_oc_is_unknown_not_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M2: a check that raised (any exception) or an OC entry gone quiet (the game
    paused) keeps what was said, so the same ongoing problem isn't announced again after the
    cooldown; only a fresh reading that shows it gone, or a long silence, clears it."""
    sc = S.build(tmp_path, "early")
    clock = Clock(sc.now)
    n, given = make(sc, clock, enabled=("power_deficit",))
    real = checks.power_deficit
    try:
        drain(sc, sc.now, 1)
        n.tick()
        clock.now += checks.DRAIN_HOLD_S
        drain(sc, clock.now, 2)
        assert [x.kind for x in n.tick()] == ["power_deficit"]

        def broken(conn: sqlite3.Connection, now: float) -> list[checks.Finding]:
            raise ValueError("a bad plan file")

        monkeypatch.setattr(checks, "power_deficit", broken)
        clock.now += notices.COOLDOWN_S + 1
        drain(sc, clock.now - 1, 3)
        assert n.tick() == []  # not raised out of the tick
        assert n.failed == {"power_deficit"}
        assert any(p.startswith("power_deficit: a bad plan file") for p in n.problems)
        assert [a["id"] for a in n.alerts()] == ["notice:power_deficit"]  # still shows
        monkeypatch.setattr(checks, "power_deficit", real)
        clock.now += 10
        drain(sc, clock.now - 1, 4)
        assert n.tick() == []  # back, still true: not said again
        # the game pauses: OC goes quiet past STALE_S; the drain is unknown
        clock.now += 300
        assert n.tick() == []
        assert n.alerts() == []
        # it comes back, still draining, past the cooldown: still not said again
        clock.now += notices.COOLDOWN_S
        drain(sc, clock.now - 1, 5)
        assert n.tick() == []
        # silent past the grace: cleared, so it is announced when it holds again
        clock.now += notices.UNKNOWN_GRACE_S + 60
        assert n.tick() == []
        drain(sc, clock.now - 1, 6)
        assert n.tick() == []  # its hold again
        clock.now += checks.DRAIN_HOLD_S
        drain(sc, clock.now - 1, 7)
        assert [x.kind for x in n.tick()] == ["power_deficit"]
    finally:
        n.close()
    assert [x.kind for x in given] == ["power_deficit", "power_deficit"]


def test_a_cleared_problem_pushes_the_change(tmp_path: Path) -> None:
    """Review M1: when an announced problem clears, ``changed`` is called once so the core
    pushes the state (the alert card goes) and Now asks the advisor again."""
    sc = S.build(tmp_path, "early")
    clock = Clock(sc.now)
    calls: list[int] = []
    n, _ = make(sc, clock, enabled=("power_deficit",), changed=lambda: calls.append(1))
    try:
        drain(sc, sc.now, 1)
        n.tick()
        clock.now += checks.DRAIN_HOLD_S
        drain(sc, clock.now, 2)
        assert len(n.tick()) == 1
        assert calls == []  # the notice's own push refreshes the state
        assert n.tick() == [] and calls == []  # nothing moved
        clock.now += 30
        drain(sc, clock.now, 3, out=0)
        assert n.tick() == []
        assert calls == [1] and n.alerts() == []
        assert n.tick() == [] and calls == [1]
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
    assert len(n.tick()) == 1
    n.close()
    again, _ = make(sc, clock)
    try:
        assert again.tick() == []
        assert len(again.alerts()) == 1
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
    assert set(notices.PRIORITY) == set(config.NOTICES) - set(notices.ALERT_OWNED)
    assert notices.KINDS == tuple(notices.PRIORITY)
    assert set(notices.ALERT_PROBLEMS) <= set(RULES)
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
        assert len(n.tick()) == 1
    finally:
        n.close()
    conn = core_store.connect(sc.db)
    try:
        row = conn.execute(
            "SELECT value FROM kv WHERE key = ?", (notices.KV_STATE,)
        ).fetchone()
    finally:
        conn.close()
    assert set(json.loads(row[0])) == {"seen", "said", "last", "kinds"}


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
            assert [x.kind for x in pushed] == ["power_deficit"]
            alerts = client.get("/api/state", headers=head).json()["alerts"]
            ids = [a["id"] for a in alerts]
            assert "notice:power_deficit" in ids
            assert "notice:machine_stalled" not in ids  # the alert engine's card (D281)
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


def test_a_plan_name_with_a_number_is_not_spoken() -> None:
    """Review L4: the spoken line holds no numbers, even from his own plan's name."""
    f = checks.Finding(
        "plan_buildable", "plan:pl-x", "You can build EBF 2 from stock", "", (), "#/"
    )
    title, say = notices._say("plan_buildable", [f])
    assert title == f.title
    assert say == "You have everything in stock for one of your accepted lines."
