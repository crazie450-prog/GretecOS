"""WO-7.6: the breeding station's plan (gregtec.crops.station), its start and stop through the
action desk, its status, its tools and routes (plan 7.17 item 4, D282-D284).

The plan is code's: the shortest Crop Breeder chain at his tier, each step's chance, runs,
copies made first and fertilizer from the breeding maths, every number cited; the crop_start
command it becomes fits the OC contract and one reply. Start and stop only propose (D59).
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from gregtec.bridge.actions import ActionDesk
from gregtec.bridge.ingest import AgentId, Received
from gregtec.bridge.telemetry import Telemetry
from gregtec.crops import rules, station
from gregtec.crops import station as S
from gregtec.store import Store

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests" / "fixtures" / "crops" / "stationworld.py"
ADDR = "1a000000-0000-4000-8000-000000000001"


def _world() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_wo76_stationworld", FIXTURE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = _world()


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads(
        (REPO / "docs" / "contracts" / "oc_bridge.schema.json").read_text(
            encoding="utf-8"
        )
    )
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]})


# --- the plan --------------------------------------------------------------------------------


def test_a_three_step_plan_at_mv() -> None:
    plan = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    assert [s.child for s in plan.steps] == [
        "cropsnh:carrot",
        "cropsnh:potato",
        "cropsnh:tomato",
    ]
    assert plan.tier == rules.MV and plan.count == S.TARGET_COUNT == 4
    chance = rules.breeder_chance(rules.MV)
    runs = rules.runs_for(chance, 0.9)
    assert (chance, runs) == (50, 4)
    for s in plan.steps:
        assert (s.chance_pct, s.runs, s.copies) == (50, 4, 5)
    # stats: the parents' averages (the breeding maths), never read from a seed (V31)
    carrot, potato, tomato = (s.stats for s in plan.steps)
    assert carrot == (8, 6, 4)  # Wheat 10/8/6, Reed 6/4/2
    assert potato == (6, 5, 4)  # with Melon 4/4/4
    assert tomato == (3, 3, 2)  # with Pumpkin 1/1/1
    assert plan.steps[2].fert_mb == rules.breeder_fertilizer(4, 3, 3, 2) == 1152
    # every step cites its recipe, the breeder and its crops
    assert plan.steps[2].refs[:2] == (W.ref(3), "mte:28026")
    assert set(plan.starters) == {
        "cropsnh:wheat",
        "cropsnh:reed",
        "cropsnh:melon",
        "cropsnh:pumpkin",
    }


def test_the_copy_list_puts_the_target_first_then_the_most_used_parents() -> None:
    plan = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    assert plan.copy[0] == "cropsnh:tomato"
    assert plan.copy[1:] == (
        "cropsnh:carrot",
        "cropsnh:melon",
        "cropsnh:potato",
        "cropsnh:pumpkin",
        "cropsnh:reed",
        "cropsnh:wheat",
    )
    assert plan.copy_cat == {"cropsnh:tomato": (("minecraft:dye", 15, 1, "Bone Meal"),)}
    one = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV, count=1)
    assert "cropsnh:tomato" not in one.copy, (
        "nothing to stock: no copier for the target"
    )


def test_a_crop_the_seed_generator_cant_copy_gets_no_copier() -> None:
    gen = {k: v for k, v in W.GENERATOR.items() if k != "cropsnh:melon"}
    plan = S.build(W.facts(generator=gen), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    assert "cropsnh:melon" not in plan.copy


def test_the_command_fits_the_contract_and_one_reply() -> None:
    plan = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    args = plan.args()
    assert not list(_validator("crop_start_args").iter_errors(args))
    assert args["target"] == "Tomato Seeds"
    assert args["steps"][0] == {
        "child": "Carrot Seeds",
        "parents": ["Reed Seeds", "Wheat Seeds"],
        "cat": [],
        "copies": 5,
        "fert": rules.breeder_fertilizer(2, 8, 6, 4),
        "ref": W.ref(1),
    }
    assert args["steps"][2]["cat"] == [{"name": "minecraft:dye", "damage": 15, "n": 1}]
    assert args["copy_cat"] == {
        "Tomato Seeds": [{"name": "minecraft:dye", "damage": 15, "n": 1}]
    }
    size = len(json.dumps(args, separators=(",", ":")))
    assert size <= S.ARGS_MAX
    # a numeric id never appears: items go by registry name and damage
    assert all(":" in c["name"] for s in args["steps"] for c in s["cat"])


def test_the_plan_id_is_its_content() -> None:
    a = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    b = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    c = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV, count=8)
    assert a.id == b.id and a.args() == b.args()
    assert c.id != a.id
    assert len(a.id) == 8 and int(a.id, 16) >= 0


def test_every_number_in_the_words_is_cited() -> None:
    plan = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    for line in plan.lines:
        if any(ch.isdigit() for ch in line.split("[")[0]):
            assert "[" in line and line.rstrip().endswith("]"), line


def test_recipes_the_breeder_cant_run_are_left_out() -> None:
    # Gold Leaf: 480 EU/t, over MV's 128; at HV it is one step.
    with pytest.raises(S.StationError, match="EU/t"):
        S.build(W.facts(), W.HAVE, "cropsnh:goldLeaf", tier=rules.MV)
    hv = S.build(W.facts(), W.HAVE, "cropsnh:goldLeaf", tier=rules.HV)
    assert [s.child for s in hv.steps] == ["cropsnh:goldLeaf"]
    assert hv.steps[0].chance_pct == 60


def test_a_run_over_the_tank_is_never_planned() -> None:
    high = dict(
        W.HAVE, **{"cropsnh:pumpkin": (31, 31, 31), "cropsnh:melon": (31, 31, 31)}
    )
    high.update({"cropsnh:wheat": (31, 31, 31), "cropsnh:reed": (31, 31, 31)})
    # MV's tank (6,400 mB) takes no run at 31/31/31 (Carrot: 144 x 2 + 72 x 93 mB)
    with pytest.raises(S.StationError, match="tank"):
        S.build(W.facts(), high, "cropsnh:tomato", tier=rules.MV)


def test_two_crops_of_one_name_are_refused() -> None:
    twin = dataclasses.replace(W.CROPS[0], id="cropsnh:wheat2")  # also "Wheat"
    breeder = [
        dataclasses.replace(
            W.BREEDER[0], parents=frozenset({"cropsnh:wheat", "cropsnh:wheat2"})
        ),
        *W.BREEDER[1:],
    ]
    have = dict(W.HAVE, **{"cropsnh:wheat2": (1, 1, 1)})
    with pytest.raises(S.StationError, match="by label only"):
        S.build(
            W.facts(breeder=breeder, extra=(twin,)), have, "cropsnh:tomato", rules.MV
        )


def test_what_the_station_cant_do() -> None:
    f = W.facts()
    with pytest.raises(S.StationError, match="LV or higher"):
        S.build(f, W.HAVE, "cropsnh:tomato", tier=0)
    with pytest.raises(S.StationError, match="already have"):
        S.build(f, W.HAVE, "cropsnh:wheat", tier=rules.MV)
    with pytest.raises(S.StationError, match="no crop"):
        S.build(f, W.HAVE, "cropsnh:nope", tier=rules.MV)
    with pytest.raises(S.StationError, match="no Crop Breeder recipe chain"):
        S.build(f, {"cropsnh:wheat": (1, 1, 1)}, "cropsnh:tomato", tier=rules.MV)
    with pytest.raises(S.StationError, match="count"):
        S.build(f, W.HAVE, "cropsnh:tomato", tier=rules.MV, count=0)


def test_readbacks() -> None:
    plan = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    assert S.readback("crop_start", plan.args()) == (
        "Start the breeding station: breed Tomato Seeds in 3 Crop Breeder steps and stock 4"
        "; Seed Generators keep one seed of each crop they copy, for good"
    )
    assert S.readback("crop_start", {**plan.args(), "copy": []}) == (
        "Start the breeding station: breed Tomato Seeds in 3 Crop Breeder steps and stock 4"
    )
    assert S.readback("crop_stop", {}) == "Stop the breeding station"


# --- start and stop through the desk ----------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def desk(tmp_path: Path) -> Iterator[tuple[ActionDesk, Telemetry, Clock]]:
    clock = Clock()
    store = Store(tmp_path / "gos.sqlite")
    tel = Telemetry(store, clock=clock)
    d = ActionDesk(store, locate=tel.locate, clock=clock)
    tel.results = d.results
    yield d, tel, clock
    store.close()


def _telemetry(seq: int, crops: dict[str, Any] | None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "v": 1,
        "kind": "telemetry",
        "agent": "0.1.0",
        "boot": "b0075eed",
        "seq": seq,
        "uptime": 10.0 + seq,
        "transport": "http",
        "cycle_s": 5,
        "sample_s": 0.1,
        "computer": {
            "address": ADDR,
            "arch": "Lua 5.3",
            "mem_free": 100000,
            "mem_total": 200000,
            "energy": 10.0,
            "energy_max": 10.0,
        },
        "components": {"transposer": 1, "gt_machine": 2},
        "power": [],
        "machines": [],
        "crafts": [],
        "results": [],
        "errors": [],
    }
    if crops is not None:
        doc["crops"] = crops
    return doc


def _receive(tel: Telemetry, clock: Clock, doc: dict[str, Any]) -> None:
    assert not list(_validator("telemetry").iter_errors(doc)), doc
    tel.ingest(Received(doc, "http", "test", clock.now, binding="lab"))


def test_start_needs_a_computer_with_a_station(
    desk: tuple[ActionDesk, Telemetry, Clock],
) -> None:
    d, tel, clock = desk
    plan = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    _receive(tel, clock, _telemetry(1, None))
    with pytest.raises(S.StationError, match="breeding station"):
        S.propose_start(d, plan, source="test")


def test_start_is_read_back_and_runs_only_once_confirmed(
    desk: tuple[ActionDesk, Telemetry, Clock],
) -> None:
    d, tel, clock = desk
    idle = {"state": "idle", "runs": 0, "ok": 0, "fail": 0, "stock": {}}
    _receive(tel, clock, _telemetry(1, idle))
    plan = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    out = S.propose_start(d, plan, target="lab", source="test")
    assert out.text == (
        "Start the breeding station: breed Tomato Seeds in 3 Crop Breeder steps and stock 4"
        " in Lab; Seed Generators keep one seed of each crop they copy, for good. Say confirm."
    )
    agent = AgentId("lab", ADDR)
    assert d.take(agent, 8) == [], "nothing goes to OC before Clayton confirms"
    assert out.action is not None
    assert d.confirm("dashboard", out.action.id).ok
    cmds = d.take(agent, 8)
    assert [c["kind"] for c in cmds] == ["crop_start"]
    assert cmds[0]["args"] == plan.args() and cmds[0]["confirmed_via"] == "dashboard"
    assert not list(_validator("command").iter_errors(cmds[0]))
    # the agent's result finishes it, in the station's words
    running = dict(idle, state="running", plan=plan.id, action=out.action.id)
    doc = _telemetry(2, running)
    doc["results"] = [
        {
            "id": out.action.id,
            "kind": "crop_start",
            "ok": True,
            "state": "done",
            "at": 12.0,
        }
    ]
    said: list[str] = []
    d.on_change = lambda a, s: said.append(s)
    _receive(tel, clock, doc)
    assert d.get(out.action.id).state == "done"  # type: ignore[union-attr]
    assert said[-1].startswith(
        "The breeding station took it: Start the breeding station"
    )


def test_a_dashboard_proposal_never_replaces_another_world_action(
    desk: tuple[ActionDesk, Telemetry, Clock],
) -> None:
    d, tel, clock = desk
    _receive(
        tel,
        clock,
        _telemetry(1, {"state": "idle", "runs": 0, "ok": 0, "fail": 0, "stock": {}}),
    )
    d.locate = lambda b, k, a: ADDR
    d.propose("say", {"text": "hello"}, target="lab")
    with pytest.raises(S.StationError, match="waiting for your confirm"):
        S.propose_stop(d, target="lab", source="dashboard", replace_any=False)
    out = S.propose_stop(d, target="lab", source="voice")
    assert out.cancelled is not None and out.text.startswith("Cancelled: ")


def test_status_from_telemetry_is_marked_stale(
    desk: tuple[ActionDesk, Telemetry, Clock],
) -> None:
    _, tel, clock = desk
    crops = {
        "state": "running",
        "plan": "0a1b2c3d",
        "target": "Tomato Seeds",
        "count": 4,
        "steps": 3,
        "step": 2,
        "runs": 3,
        "ok": 1,
        "fail": 2,
        "doing": "step 2: breeding Potato Seeds",
        "stock": {"Tomato Seeds": 0, "Carrot Seeds": 1},
        "copiers": ["Tomato Seeds", False],
    }
    _receive(tel, clock, _telemetry(1, crops))
    rows = S.status(tel)
    assert len(rows) == 1 and rows[0]["state"] == "running" and not rows[0]["stale"]
    assert rows[0]["ref"] == f"tel:lab/{ADDR}"
    clock.now += S.STALE_S + 1
    assert S.status(tel)[0]["stale"] is True
    assert S.status(tel, "play") == []


def test_the_agents_generic_propose_cant_send_a_plan() -> None:
    """crop_start's plan is code's: action_propose (the model's free-form proposal) doesn't
    offer the station's kinds; crop_station builds the plan and proposes it."""
    from gregtec.agents.tools import ops

    assert "crop_start" not in ops.KIND_ENUM and "crop_stop" not in ops.KIND_ENUM
    assert ops.ACTION_PROPOSE_SCHEMA["properties"]["kind"]["enum"] == ops.KIND_ENUM


def test_expected_fertilizer() -> None:
    plan = S.build(W.facts(), W.HAVE, "cropsnh:tomato", tier=rules.MV)
    assert station.expected_fertilizer(plan) == sum(s.fert_mb * 2 for s in plan.steps)
