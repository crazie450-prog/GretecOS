"""WO-7.1: the action table and the confirm flow (gregtec.bridge.actions; plan 7.9, D59, D220).

Acceptance (plan 8): unconfirmed actions never run (a Hypothesis state machine over random
interleavings of propose, confirm, cancel, expiry, delivery, returns and results); a second
proposal cancels the first out loud; an expired action can't be confirmed. Also: agents and OC
messages can't confirm, only the allowlisted kinds exist, and each change is journalled.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
)

from gregtec.bridge import actions
from gregtec.bridge.actions import (
    KINDS,
    NOTHING_PENDING,
    PENDING_S,
    QUEUE_S,
    RESULT_S,
    VIAS,
    Action,
    ActionDesk,
    ActionError,
)
from gregtec.bridge.ingest import AgentId
from gregtec.bridge.ops import journal_to
from gregtec.store import Store
from gregtec.vault import schema

REPO = Path(__file__).resolve().parents[2]
ADDR = "1a000000-0000-4000-8000-000000000001"
OTHER = "1a000000-0000-4000-8000-000000000002"
MACHINE = "2e000000-0000-4000-8000-000000000008"
LAB = AgentId("lab", ADDR)
CRAFT = {"name": "gregtech:gt.metaitem.01", "damage": 11306, "amount": 64}
WAYPOINT = {"name": "Base", "x": 100, "y": 64, "z": -200, "dim": 0}
ARGS: dict[str, dict[str, Any]] = {
    "craft": CRAFT,
    "machine_pause": {"addr": MACHINE},
    "machine_resume": {"addr": MACHINE},
    "say": {"text": "hello"},
    "waypoint": WAYPOINT,
}


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Desk:
    """A desk over a temporary DB with every hook recorded."""

    def __init__(self, folder: Path, *, mark_fails: bool = False) -> None:
        self.clock = Clock()
        self.store = Store(folder / "gos.sqlite")
        self.said: list[tuple[int, str, str]] = []
        self.journal: list[str] = []
        self.marked: list[tuple[str, Mapping[str, Any]]] = []
        self.mark_fails = mark_fails
        self.desk = ActionDesk(
            self.store,
            locate=lambda binding, kind, args: ADDR,
            name=lambda n, d: "Stainless Steel Ingot" if d == 11306 else None,
            mark=self.mark,
            journal=self.journal.extend,
            on_change=lambda a, say: self.said.append((a.id, a.state, say)),
            clock=self.clock,
        )

    def mark(self, target: str, args: Mapping[str, Any]) -> str:
        if self.mark_fails:
            from gregtec.bridge.waypoint import WaypointError

            raise WaypointError("JourneyMap has no data for the world 'x' yet")
        self.marked.append((target, args))
        return "wrote waypoint Base_100,64,-200.json"


@pytest.fixture
def d(tmp_path: Path) -> Iterator[Desk]:
    desk = Desk(tmp_path)
    yield desk
    desk.store.close()


def propose(d: Desk, kind: str = "craft", target: str = "lab") -> Action:
    out = d.desk.propose(kind, ARGS[kind], target=target)
    assert out.ok and out.action is not None
    return out.action


# --- propose and read back ------------------------------------------------------------------


def test_a_proposal_is_read_back_and_waits(d: Desk) -> None:
    out = d.desk.propose("craft", CRAFT, target="play")
    assert out.text == "Craft 64 Stainless Steel Ingot. Say confirm."
    assert out.action is not None and out.action.state == "proposed"
    assert out.action.expires == out.action.created + PENDING_S
    assert d.desk.pending() == out.action
    assert d.said[-1] == (out.action.id, "proposed", out.text)


def test_read_backs_of_every_kind(d: Desk) -> None:
    words = {k: d.desk.describe(k, ARGS[k], "lab") for k in KINDS}
    assert words == {
        "craft": "Craft 64 Stainless Steel Ingot in Lab",
        "machine_pause": "Pause machine 2e000000 in Lab",
        "machine_resume": "Resume machine 2e000000 in Lab",
        "say": 'Say "hello" in chat in Lab',
        "waypoint": 'Add waypoint "Base" at 100, 64, -200 in dimension 0 in Lab',
    }
    unknown = dict(CRAFT, damage=1)
    assert d.desk.describe("craft", unknown, "play") == (
        "Craft 64 gregtech:gt.metaitem.01 (damage 1)"
    )


def test_a_second_proposal_cancels_the_first_out_loud(d: Desk) -> None:
    first = propose(d, "say")
    out = d.desk.propose("craft", CRAFT, target="lab")
    assert out.cancelled is not None and out.cancelled.id == first.id
    assert out.cancelled.state == "cancelled"
    assert out.text == (
        'Cancelled: Say "hello" in chat in Lab. Craft 64 Stainless Steel Ingot in Lab. '
        "Say confirm."
    )
    assert (first.id, "cancelled", 'Cancelled: Say "hello" in chat in Lab.') in d.said
    # Only one is pending, and the old one can't be confirmed by its id.
    assert d.desk.pending() == out.action
    refused = d.desk.confirm("dashboard", first.id)
    assert not refused.ok and refused.text == "That action is already cancelled."
    assert d.desk.get(first.id).state == "cancelled"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("kind", "args", "why"),
    [
        ("machine_destroy", {"addr": MACHINE}, "not a safe action"),
        ("craft_cancel", CRAFT, "not a safe action"),
        ("run_lua", {"code": "os.exit()"}, "not a safe action"),
        ("craft", {"name": "123", "damage": 0, "amount": 1}, "don't fit"),
        ("craft", dict(CRAFT, amount=0), "don't fit"),
        ("craft", dict(CRAFT, extra=1), "don't fit"),
        ("machine_pause", {"addr": "not-an-address"}, "don't fit"),
        ("say", {"text": ""}, "don't fit"),
        ("say", "hello", "args must be an object"),
        ("waypoint", dict(WAYPOINT, y=999), "waypoint args"),
        ("waypoint", dict(WAYPOINT, name="../../evil"), "waypoint name"),
    ],
)
def test_only_the_allowlist_with_valid_args(
    d: Desk, kind: str, args: object, why: str
) -> None:
    with pytest.raises(ActionError, match=why):
        d.desk.propose(kind, args, target="lab")
    assert d.desk.recent() == []


def test_a_proposal_needs_a_computer_that_can_run_it(d: Desk) -> None:
    def nobody(binding: str, kind: str, args: Mapping[str, Any]) -> str:
        raise ActionError("no lab OC computer reports a chat box")

    d.desk.locate = nobody
    with pytest.raises(ActionError, match="chat box"):
        d.desk.propose("say", {"text": "hi"}, target="lab")
    with pytest.raises(ActionError, match="target"):
        d.desk.propose("waypoint", WAYPOINT, target="export")


# --- confirm, cancel, expire ------------------------------------------------------------------


def test_confirm_queues_it_with_the_way_it_came(d: Desk) -> None:
    a = propose(d)
    out = d.desk.confirm("voice")
    assert out.ok and out.action is not None
    assert out.action.state == "confirmed" and out.action.confirmed_via == "voice"
    assert out.text == (
        "Confirmed: Craft 64 Stainless Steel Ingot in Lab. It goes to the lab OC computer next."
    )
    assert d.desk.pending() is None
    assert d.desk.confirm("voice").text == NOTHING_PENDING
    assert d.desk.get(a.id).confirmed_ts == int(d.clock.now)  # type: ignore[union-attr]


def test_a_button_confirms_only_its_own_action(d: Desk) -> None:
    a = propose(d)
    wrong = d.desk.confirm("phone", a.id + 1)
    assert not wrong.ok and wrong.text == "There is no such action."
    assert d.desk.get(a.id).state == "proposed"  # type: ignore[union-attr]
    assert d.desk.confirm("phone", a.id).ok


def test_an_expired_action_cant_be_confirmed(d: Desk) -> None:
    a = propose(d)
    d.clock.now += PENDING_S - 1
    assert d.desk.pending() is not None
    d.clock.now += 1
    by_id = d.desk.confirm("dashboard", a.id)
    assert not by_id.ok
    assert by_id.text.startswith("That expired: Craft 64 Stainless Steel Ingot")
    assert d.desk.confirm("voice").text == NOTHING_PENDING
    assert d.desk.get(a.id).state == "expired"  # type: ignore[union-attr]
    assert d.said[-1] == (
        a.id,
        "expired",
        "Expired: Craft 64 Stainless Steel Ingot in Lab. Nothing was done.",
    )
    assert d.desk.take(LAB, 8) == []


def test_cancel(d: Desk) -> None:
    a = propose(d)
    assert not d.desk.cancel("voice", a.id + 5).ok
    out = d.desk.cancel("voice")
    assert out.ok and out.text == "Cancelled: Craft 64 Stainless Steel Ingot in Lab."
    assert d.desk.get(a.id).state == "cancelled"  # type: ignore[union-attr]
    assert d.desk.cancel("voice").text == NOTHING_PENDING


@pytest.mark.parametrize("via", ["agent", "oc", "admin", "", "VOICE", None])
def test_only_claytons_ways_can_confirm(d: Desk, via: object) -> None:
    propose(d)
    with pytest.raises(ValueError, match="confirmations come by"):
        d.desk.confirm(via)  # type: ignore[arg-type]
    assert d.desk.pending() is not None


# --- delivery and results ---------------------------------------------------------------------


def test_take_delivers_once_to_its_own_computer_only(d: Desk) -> None:
    a = propose(d)
    d.desk.confirm("dashboard")
    # Play's computer, or another Lab computer, or a Lab message naming Play's address: no.
    assert d.desk.take(AgentId("play", ADDR), 8) == []
    assert d.desk.take(AgentId("lab", OTHER), 8) == []
    cmds = d.desk.take(LAB, 8)
    assert cmds == [
        {"id": a.id, "kind": "craft", "args": CRAFT, "confirmed_via": "dashboard"}
    ]
    assert d.desk.take(LAB, 8) == []  # at most once
    d.desk.returned([a.id], "the reply was full")
    assert d.desk.take(LAB, 8) == cmds  # again under the same id
    assert d.desk.get(a.id).state == "sent"  # type: ignore[union-attr]


def results_doc(
    *results: dict[str, Any], crafts: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"results": list(results), "crafts": crafts or []}


def test_results_finish_and_report(d: Desk) -> None:
    a = propose(d)
    d.desk.confirm("voice")
    d.desk.take(LAB, 8)
    res = {"id": a.id, "kind": "craft", "ok": True, "state": "submitted", "at": 3.0}
    d.desk.results(AgentId("lab", OTHER), results_doc(res))  # another computer: ignored
    assert d.desk.get(a.id).state == "sent"  # type: ignore[union-attr]
    d.desk.results(LAB, results_doc(res))
    done = d.desk.get(a.id)
    assert done is not None and done.state == "done"
    assert done.result == {"ok": True, "state": "submitted"}
    assert (
        d.said[-1][2]
        == "Started: Craft 64 Stainless Steel Ingot in Lab. AE2 is crafting it."
    )
    d.desk.results(
        LAB, results_doc(dict(res, ok=False, state="failed"))
    )  # a repeat: no change
    assert d.desk.get(a.id).state == "done"  # type: ignore[union-attr]
    craft = {
        "id": a.id,
        "name": CRAFT["name"],
        "damage": 11306,
        "amount": 64,
        "at": 4.0,
    }
    d.desk.results(LAB, results_doc(crafts=[dict(craft, state="crafting")]))
    assert d.desk.get(a.id).result["craft"] == "crafting"  # type: ignore[index, union-attr]
    d.desk.results(LAB, results_doc(crafts=[dict(craft, state="done")]))
    assert d.said[-1][2] == "Crafting done: Craft 64 Stainless Steel Ingot in Lab."


def test_a_failure_and_a_lost_craft(d: Desk) -> None:
    a = propose(d, "say")
    d.desk.confirm("voice")
    d.desk.take(LAB, 8)
    d.desk.results(
        LAB,
        results_doc(
            {
                "id": a.id,
                "kind": "say",
                "ok": False,
                "state": "refused",
                "err": "no chat box",
                "at": 1,
            }
        ),
    )
    assert d.said[-1][2] == 'Failed: Say "hello" in chat in Lab. no chat box'
    b = propose(d)
    d.desk.confirm("voice")
    d.desk.take(LAB, 8)
    err = "lost track of it after a world reload (OC keeps no AE2 link); the craft may still run, check AE2"
    d.desk.results(
        LAB,
        results_doc(
            {
                "id": b.id,
                "kind": "craft",
                "ok": False,
                "state": "failed",
                "err": err,
                "at": 1,
            }
        ),
    )
    assert d.said[-1][2].startswith("Check AE2: Craft 64")


def test_an_oc_message_cant_confirm_or_run_a_proposal(d: Desk) -> None:
    a = propose(d)
    d.desk.results(
        LAB,
        results_doc(
            {"id": a.id, "kind": "craft", "ok": True, "state": "done", "at": 1}
        ),
    )
    assert d.desk.get(a.id).state == "proposed"  # type: ignore[union-attr]
    assert d.desk.take(LAB, 8) == []


def test_timeouts(d: Desk) -> None:
    a = propose(d)
    d.desk.confirm("voice")
    d.clock.now += QUEUE_S
    d.desk.tick()
    got = d.desk.get(a.id)
    assert got is not None and got.state == "failed"
    assert got.result == {"why": "its OC computer didn't collect it; nothing ran"}
    b = propose(d)
    d.desk.confirm("voice")
    d.desk.take(LAB, 8)
    d.clock.now += RESULT_S
    d.desk.tick()
    got = d.desk.get(b.id)
    assert got is not None and got.state == "failed"
    assert got.result == {"why": "timed out; may have run"}
    assert d.said[-1][2].startswith("No result for: Craft 64")


def test_a_restart_drops_what_wasnt_sent(tmp_path: Path) -> None:
    first = Desk(tmp_path)
    a = propose(first)
    first.desk.confirm("voice")
    b = propose(first, "say")
    c = propose(first, "craft")
    first.desk.confirm("voice")
    first.desk.take(LAB, 1)  # a is sent
    d_ = propose(first, "say")
    first.store.close()
    again = Desk(tmp_path)
    try:
        states = {x: again.desk.get(x).state for x in (a.id, b.id, c.id, d_.id)}  # type: ignore[union-attr]
        assert states == {
            a.id: "sent",
            b.id: "cancelled",
            c.id: "failed",
            d_.id: "expired",
        }
    finally:
        again.store.close()


# --- waypoints ------------------------------------------------------------------------------


def test_a_confirmed_waypoint_is_written_by_the_core(d: Desk) -> None:
    a = propose(d, "waypoint")
    assert a.computer is None
    out = d.desk.confirm("voice")
    assert out.ok and d.marked == [("lab", WAYPOINT)]
    assert d.desk.get(a.id).state == "done"  # type: ignore[union-attr]
    assert (
        out.text == 'Done: Add waypoint "Base" at 100, 64, -200 in dimension 0 in Lab.'
    )
    assert d.desk.take(LAB, 8) == []  # never sent to OC


def test_a_waypoint_that_cant_be_written_fails(tmp_path: Path) -> None:
    d = Desk(tmp_path, mark_fails=True)
    try:
        a = propose(d, "waypoint")
        out = d.desk.confirm("voice")
        assert not out.ok and d.desk.get(a.id).state == "failed"  # type: ignore[union-attr]
        assert "JourneyMap has no data" in out.text
    finally:
        d.store.close()


# --- the journal ----------------------------------------------------------------------------


def test_each_change_is_journalled_in_the_note_grammar(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    shutil.copytree(REPO / "vault-template", vault)
    d = Desk(tmp_path)
    d.desk.journal = journal_to(vault)
    try:
        a = propose(d)
        d.desk.confirm("voice")
        logs = list((vault / "log").glob("*.md"))
        assert len(logs) == 1
        text = logs[0].read_text(encoding="utf-8")
        assert (
            f"action_{a.id}: proposed; craft; lab; Craft 64 Stainless Steel Ingot in Lab"
            in text
        )
        assert f"action_{a.id}: confirmed; craft; lab; via voice;" in text
        rel = f"log/{logs[0].name}"
        assert (
            schema.check_text(
                rel, text, schema.load_types(vault / "_gos" / "schema.md")
            )
            == []
        )
    finally:
        d.store.close()


# --- no agent path can confirm ----------------------------------------------------------------


def test_nothing_but_the_routes_and_the_router_calls_confirm() -> None:
    """Static: ``.confirm(`` on a desk appears only in the router's instant command and the
    confirm-by-id route, both of which pass a ``via`` derived from who called (D59)."""
    callers = []
    for path in sorted((REPO / "core" / "gregtec").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "confirm"
            ):
                callers.append(path.relative_to(REPO).as_posix())
    assert (
        sorted(set(callers))
        == [
            "core/gregtec/agents/ask.py",  # the instant "confirm", its via from the HTTP caller
            "core/gregtec/packupdate/cli.py",  # the pack-update playbook's own step, not a desk
        ]
    ), callers
    http = (REPO / "core" / "gregtec" / "bridge" / "http.py").read_text(
        encoding="utf-8"
    )
    assert "desk().confirm, via, action_id" in http


# --- the property: unconfirmed actions never run ----------------------------------------------


class ConfirmFlow(RuleBasedStateMachine):
    """Random interleavings of propose / confirm / cancel / clock / deliver / return / results.

    A model tracks which ids Clayton confirmed (with a way of his, before expiry); the desk may
    deliver an id to OC only if the model confirmed it, never twice without a return, and only
    to its own binding's computer."""

    proposed = Bundle("proposed")

    def __init__(self) -> None:
        super().__init__()
        self._dir = tempfile.TemporaryDirectory()
        self.d = Desk(Path(self._dir.name))
        self.pending: tuple[int, int] | None = None  # (id, created)
        self.confirmed: dict[int, int] = {}  # id -> when the model saw it confirmed
        self.cancelled: set[int] = set()
        self.in_flight: set[int] = set()
        self.delivered: list[int] = []
        self.finished: set[int] = set()

    def teardown(self) -> None:
        self.d.store.close()
        self._dir.cleanup()

    def _expire_model(self) -> None:
        now = int(self.d.clock.now)
        if self.pending is not None and self.pending[1] + PENDING_S <= now:
            self.cancelled.add(self.pending[0])
            self.pending = None

    @rule(target=proposed, kind=st.sampled_from(KINDS))
    def propose(self, kind: str) -> int:
        self._expire_model()
        out = self.d.desk.propose(kind, ARGS[kind], target="lab")
        assert out.action is not None
        if self.pending is not None:
            assert out.cancelled is not None and out.cancelled.id == self.pending[0]
            assert out.text.startswith("Cancelled: ")  # out loud
            self.cancelled.add(self.pending[0])
        else:
            assert out.cancelled is None
        self.pending = (out.action.id, out.action.created)
        return out.action.id

    @rule(
        via=st.sampled_from(VIAS),
        which=st.one_of(st.none(), proposed, st.integers(min_value=1, max_value=50)),
    )
    def confirm(self, via: str, which: int | None) -> None:
        self._expire_model()
        out = self.d.desk.confirm(via, which)
        expect = self.pending is not None and which in (None, self.pending[0])
        assert out.ok == expect or (
            expect and out.action is not None and out.action.kind == "waypoint"
        ), (out, self.pending, which)
        if expect:
            assert self.pending is not None
            self.confirmed[self.pending[0]] = int(self.d.clock.now)
            if out.action is not None and out.action.kind == "waypoint":
                self.finished.add(self.pending[0])
            self.pending = None

    @rule(which=st.one_of(st.none(), proposed))
    def cancel(self, which: int | None) -> None:
        self._expire_model()
        out = self.d.desk.cancel("voice", which)
        expect = self.pending is not None and which in (None, self.pending[0])
        assert out.ok == expect
        if expect:
            assert self.pending is not None
            self.cancelled.add(self.pending[0])
            self.pending = None

    @rule(seconds=st.sampled_from([0, 1, 30, 59, 60, 61, 120, 299, 300, 301]))
    def advance(self, seconds: int) -> None:
        self.d.clock.now += seconds
        self.d.desk.tick()
        self._expire_model()

    @rule(
        agent=st.sampled_from([LAB, AgentId("play", ADDR), AgentId("lab", OTHER)]),
        limit=st.integers(min_value=0, max_value=8),
    )
    def deliver(self, agent: AgentId, limit: int) -> None:
        self._expire_model()
        cmds = self.d.desk.take(agent, limit)
        assert len(cmds) <= limit
        for cmd in cmds:
            cid = cmd["id"]
            assert agent == LAB
            assert cid in self.confirmed, f"action {cid} ran unconfirmed"
            assert cid not in self.cancelled
            assert cid not in self.in_flight and cid not in self.finished
            assert cmd["confirmed_via"] in VIAS
            self.in_flight.add(cid)
            self.delivered.append(cid)

    @precondition(lambda self: bool(self.in_flight))
    @rule(data=st.data())
    def give_back(self, data: st.DataObject) -> None:
        ids = data.draw(st.lists(st.sampled_from(sorted(self.in_flight)), unique=True))
        self.d.desk.returned(ids, "the reply was full")
        for i in ids:
            if self.d.desk.get(i).state == "confirmed":  # type: ignore[union-attr]
                self.in_flight.discard(i)

    @precondition(lambda self: bool(self.in_flight))
    @rule(data=st.data(), ok=st.booleans())
    def report(self, data: st.DataObject, ok: bool) -> None:
        cid = data.draw(st.sampled_from(sorted(self.in_flight)))
        state = "done" if ok else "failed"
        self.d.desk.results(
            LAB,
            {
                "results": [
                    {"id": cid, "kind": "say", "ok": ok, "state": state, "at": 1}
                ]
            },
        )
        self.in_flight.discard(cid)
        self.finished.add(cid)

    @invariant()
    def one_pending_at_most(self) -> None:
        rows = self.d.store.rows("SELECT COUNT(*) FROM action WHERE state = 'proposed'")
        assert rows[0][0] <= 1

    @invariant()
    def nothing_unconfirmed_left_the_proposed_state_forward(self) -> None:
        for row in self.d.store.rows(
            "SELECT id, state, created, confirmed_via, confirmed_ts, sent_ts FROM action"
        ):
            if row["sent_ts"] is not None or row["state"] in (
                "confirmed",
                "sent",
                "done",
            ):
                assert row["confirmed_via"] in VIAS, dict(row)
                assert row["confirmed_ts"] is not None
                assert row["confirmed_ts"] < row["created"] + PENDING_S, dict(row)
                assert row["id"] in self.confirmed
            if row["state"] in ("cancelled", "expired"):
                assert row["id"] not in self.delivered


ConfirmFlow.TestCase.settings = settings(
    max_examples=150,
    stateful_step_count=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
TestConfirmFlow = ConfirmFlow.TestCase


def test_the_module_lists_no_destructive_kind() -> None:
    assert set(actions.KINDS) == {
        "craft",
        "machine_pause",
        "machine_resume",
        "say",
        "waypoint",
    }
