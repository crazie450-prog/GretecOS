"""WO-7.1: the ops tools (telemetry_get, action_propose, action_status) and the rule that no
agent path can confirm an action (plan 7.0, 7.9; D59, D220)."""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.agents import router
from gregtec.agents.ask import CONFIRM_ELSEWHERE, NOTHING_PENDING, Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.registry import DEEP, FAST, Registry
from gregtec.bridge import ops as bridge_ops
from gregtec.bridge.actions import SETTLE_S
from gregtec.bridge.ingest import Bridge
from gregtec.config import Config
from gregtec.store import Store

REPO = Path(__file__).resolve().parents[3]
FIRST = json.loads(
    (REPO / "tests" / "fixtures" / "oc" / "telemetry-first.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture
def ops(tmp_path: Path) -> Iterator[bridge_ops.Ops]:
    store = Store(tmp_path / "gos.sqlite")
    o = bridge_ops.build(Config(gos_home=tmp_path), store)
    yield o
    store.close()


@pytest.fixture
def reg(tmp_path: Path, ops: bridge_ops.Ops) -> Registry:
    return build_registry(tmp_path / "no.sqlite", ops=ops)


def feed(bridge: Bridge, seq: int = 1) -> None:
    doc = copy.deepcopy(FIRST)
    doc["seq"] = seq
    assert bridge.receive(
        json.dumps(doc).encode(), transport="mailbox", source="t", binding="lab"
    ).ok


def test_lanes(reg: Registry) -> None:
    fast = {t.name for t in reg.tools(FAST)}
    deep = {t.name for t in reg.tools(DEEP)}
    assert {"telemetry_get", "action_status"} <= fast & deep
    assert "action_propose" in deep and "action_propose" not in fast
    out = reg.call("action_propose", {"kind": "say", "args": {"text": "x"}}, lane=FAST)
    assert out.is_error and "isn't offered in the fast lane" in out.text


def test_telemetry_get(reg: Registry, ops: bridge_ops.Ops) -> None:
    empty = reg.call("telemetry_get", {"scope": "all"}, lane=FAST)
    assert "no OC agent has reported" in empty.text
    feed(ops.bridge)
    out = reg.call("telemetry_get", {"scope": "all", "instance": "lab"}, lane=FAST)
    assert not out.is_error
    assert f"tel:lab/{FIRST['computer']['address']}\tcomputer" in out.text
    assert "multimachine.blastfurnace\trunning\t0\t0\t" in out.text
    assert "progress=120" in out.text and "eu_str=123456789012345678901234" in out.text
    assert "never a current value" in out.text
    play = reg.call("telemetry_get", {"scope": "machines", "instance": "play"})
    assert "rows: 0" in play.text


def test_action_propose_reads_back_and_status(
    reg: Registry, ops: bridge_ops.Ops
) -> None:
    feed(ops.bridge)
    out = reg.call(
        "action_propose",
        {"kind": "say", "args": {"text": "dinner"}, "instance": "lab"},
        lane=DEEP,
    )
    assert not out.is_error, out.text
    assert 'say: Say "dinner" in chat in Lab. Say confirm.' in out.text
    assert "only he can confirm, and you can't" in out.text
    status = reg.call("action_status", {}, lane=FAST)
    assert "pending: action:1" in status.text and "proposed" in status.text
    # Plain errors come back as the tool's error, never an exception.
    bad = reg.call("action_propose", {"kind": "say", "args": {"text": ""}}, lane=DEEP)
    assert bad.is_error
    nobody = reg.call(
        "action_propose",
        {"kind": "say", "args": {"text": "x"}, "instance": "play"},
        lane=DEEP,
    )
    assert nobody.is_error and "no fresh telemetry from the play" in nobody.text


@pytest.mark.parametrize(
    "extra",
    [
        {"confirmed": True},
        {"confirmed_via": "voice"},
        {"state": "confirmed"},
        {"via": "dashboard"},
    ],
)
def test_no_tool_argument_can_confirm(
    reg: Registry, ops: bridge_ops.Ops, extra: dict[str, object]
) -> None:
    feed(ops.bridge)
    out = reg.call(
        "action_propose",
        {"kind": "say", "args": {"text": "x"}, "instance": "lab", **extra},
        lane=DEEP,
    )
    assert out.is_error
    sneaky = reg.call(
        "action_propose",
        {"kind": "say", "args": {"text": "x", **extra}, "instance": "lab"},
        lane=DEEP,
    )
    assert sneaky.is_error  # not in the contract's say args either
    assert ops.desk.recent() == []


def test_no_tool_call_confirms(reg: Registry, ops: bridge_ops.Ops) -> None:
    """Every tool, called after a proposal, leaves it unconfirmed."""
    feed(ops.bridge)
    reg.call(
        "action_propose",
        {"kind": "say", "args": {"text": "x"}, "instance": "lab"},
        lane=DEEP,
    )
    for tool in reg.tools():
        reg.call(tool.name, {}, lane=None)
        reg.call(
            tool.name, {"id": 1, "kind": "say", "args": {}, "scope": "all"}, lane=None
        )
    pending = ops.desk.pending()
    assert pending is not None and pending.state == "proposed"
    assert all(a.confirmed_via is None for a in ops.desk.recent())


def test_the_instant_confirm_needs_one_of_claytons_ways(
    reg: Registry, ops: bridge_ops.Ops
) -> None:
    asker = Asker(reg, HandoffRecorder(), None, actions=ops.desk)
    assert asker.ask("confirm", via="voice").text == NOTHING_PENDING
    feed(ops.bridge)
    ops.desk.propose("say", {"text": "x"}, target="lab")
    for via in (None, "agent", "admin"):
        reply = asker.ask("confirm", via=via)
        assert reply.text == CONFIRM_ELSEWHERE and reply.pending is True
    ops.desk.clock = lambda: time.time() + SETTLE_S  # read back before a plain confirm
    reply = asker.ask("yes, confirm it", via="voice")
    assert reply.intent == router.CONFIRM and reply.text.startswith("Confirmed:")
    assert reply.refs == ["action:1"]
    ops.desk.propose("say", {"text": "y"}, target="lab")
    cancelled = asker.ask("cancel")
    assert cancelled.text.startswith("Cancelled:") and cancelled.pending is True
    assert asker.ask("cancel").pending is False
