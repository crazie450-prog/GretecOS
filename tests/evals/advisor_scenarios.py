"""The advisor's three scenarios (WO-6.5 acceptance, D266, D267): made-up GOS_HOMEs for ``gos eval
advisor`` (Clayton rates each answer "useful" or not) and the notice tests.

Each builds on the quest guide's pack (``tests/evals/quest_pack.py``: self-authored, never pack
data: his goals are the Electric Blast Furnace, then oxygen; the steel ingot plan is accepted) and
adds, through the real writers (the plan store's JSON, the EU ledger's ``record``, WO-7.1's OC
ingest with the contract's own telemetry fixture, WO-6.3's event store, My base's stock):

- **early**: nothing else. Day one: what now? (his goals' frontier quests, then the steel line);
- **trouble**: a running LV macerator line (two Basic Macerators, 64 EU/t) on a running 32 EU/t
  power line, so the EU ledger's running scope is short 32 EU/t; and OC (the Lab's ``oc-lab``
  binding) reporting an Electric Blast Furnace with a job that hasn't run for two minutes and an
  EV battery buffer giving out 300 EU/t with nothing coming in. Both default problem notices;
- **stocked**: the macerator line accepted, both Basic Macerators in My base's stock (so it is
  buildable from stock, the third default notice), and "Logs" finished two hours ago (it
  opened the Steam line's quests).

Times are relative to ``now`` (default: the clock), as the checks read readings' freshness by
it. Every number here is the fixture's own, never a GTNH value.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from gregtec import store as core_store
from gregtec.bridge.ingest import Bridge
from gregtec.bridge.telemetry import Telemetry
from gregtec.db import access
from gregtec.events import store as events
from gregtec.planner import ledger
from gregtec.planner.outputs import store as plan_store

REPO = Path(__file__).resolve().parents[2]
OC_FIRST = REPO / "tests" / "fixtures" / "oc" / "telemetry-first.json"
NAMES = ("early", "trouble", "stocked")
QUESTION = "What should I do next?"
MACHINES = "gregtech:gt.blockmachines"
MACERATOR = 31  # the tool fixtures' Basic Macerator (a single block)
EBF_ADDR = "2e000000-0000-4000-8000-000000000008"
BUFFER_ADDR = "3b000000-0000-4000-8000-000000000009"


@dataclass(frozen=True)
class Scenario:
    name: str
    home: Path
    db: Path
    now: float
    # what the advisor must put first, in order (step kinds), and what it must cite
    kinds: tuple[str, ...]
    cites: tuple[str, ...]
    # the notices a tick must give (kinds) when the defaults are on
    notices: tuple[str, ...]


def pack() -> ModuleType:
    name = "quest_pack"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO / "tests" / "evals" / "quest_pack.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("can't load the quest pack")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def plan_json(
    pid: str, name: str, *, kind: str = "production", **extra: object
) -> dict[str, Any]:
    return {"id": pid, "name": name, "v": 1, "run": "fixture", "kind": kind, **extra}


MACERATORS = plan_json(
    "pl-macer",
    "Macerators · LV",
    nodes=[
        {
            "n": "n1",
            "recipe": "r:gt.recipe.macerator/000000000001",
            "machine": {
                "mte": MACERATOR,
                "kind": "single",
                "tier": "LV",
                "name": "Basic Macerator",
            },
            "count": 2,
        }
    ],
    metrics={"eu": [{"tier": "LV", "eut_avg": 64, "eut_peak": 64, "amps": 2}]},
    target={"id": "gregtech:gt.metaitem.01@2032", "rate_s": 0.5},
)
BOILERS = plan_json(
    "pw-boil",
    "Small boilers · LV",
    kind="power",
    nodes=[],
    power={
        "net_eut": 32,
        "gross_eut": 32,
        "distribution": {"tier": "LV", "amps": 2},
        "generator": {"tier": "LV", "label": "steam turbine"},
    },
)


def save_plan(
    home: Path, plan: Mapping[str, Any], status: str, created: int = 2
) -> None:
    path = home / plan_store.json_rel(plan["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan), encoding="utf-8")
    conn = core_store.connect(access.db_path(home))
    try:
        conn.execute(
            "INSERT INTO plan(id, name, created, kind, target, status, json_path)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                plan["id"],
                plan["name"],
                created,
                plan["kind"],
                (plan.get("target") or {}).get("id"),
                status,
                plan_store.json_rel(plan["id"]),
            ),
        )
        if status in ledger.COUNTED:
            ledger.record(conn, plan)
        conn.commit()
    finally:
        conn.close()


def oc_message(seq: int, *, active: bool, eu_in: int, eu_out: int) -> dict[str, Any]:
    """The OC contract's own first telemetry (tests/fixtures/oc), with the EBF's and the battery
    buffer's readings set."""
    doc = copy.deepcopy(json.loads(OC_FIRST.read_text(encoding="utf-8")))
    doc["seq"] = seq
    for m in doc["machines"]:
        if m["addr"] == EBF_ADDR:
            m.update(active=active, work=True, allowed=True)
    for p in doc["power"]:
        if p["addr"] == BUFFER_ADDR:
            p.update(eu_in=eu_in, eu_out=eu_out)
    return doc


def send_oc(db: Path, at: float, doc: Mapping[str, Any]) -> None:
    """One OC message through WO-7.1's ingest, as ``/oc/telemetry`` takes it from ``oc-lab``."""
    st = core_store.Store(db)
    try:
        tel = Telemetry(st, clock=lambda: at)
        out = Bridge(tel, None).receive(
            json.dumps(doc).encode(),
            transport="http",
            source="fixture",
            binding="lab",
            now=at,
        )
        if not out.ok:
            raise RuntimeError(f"the OC fixture was refused: {out}")
    finally:
        st.close()


def stock(db: Path, key: str, qty: float, now: float) -> None:
    from gregtec.base import state

    conn = core_store.connect(db)
    try:
        state.set_stock(conn, key, qty, int(now))
        conn.commit()
    finally:
        conn.close()


def quest_done(db: Path, n: int, at: float) -> None:
    conn = core_store.connect(db)
    try:
        events.record(
            conn,
            "play",
            [
                {
                    "id": f"fixture-quest-{n}",
                    "kind": "quest_done",
                    "ts": at,
                    "source": "quests",
                    "data": {"quest": f"q:{pack().qid(n)}"},
                }
            ],
        )
    finally:
        conn.close()


def build(home: Path, name: str, now: float | None = None) -> Scenario:
    """Scenario ``name`` in ``home`` (an empty folder)."""
    if name not in NAMES:
        raise ValueError(f"no scenario {name!r}: one of {', '.join(NAMES)}")
    t = time.time() if now is None else now
    p = pack()
    db = p.build(home)
    core_store.connect(
        db
    ).close()  # the core's tables (My base, telemetry, events, ledger)
    steel = "plan:pl-steel"
    if name == "early":
        return Scenario(name, home, db, t, ("goal", "goal", "line"), (steel,), ())
    if name == "trouble":
        save_plan(home, MACERATORS, "running")
        save_plan(home, BOILERS, "running", created=3)
        send_oc(db, t - 120, oc_message(1, active=False, eu_in=0, eu_out=300))
        send_oc(db, t - 5, oc_message(2, active=False, eu_in=0, eu_out=300))
        return Scenario(
            name,
            home,
            db,
            t,
            ("fix", "fix", "goal"),
            ("plan:pl-macer", f"tel:lab/{EBF_ADDR}", f"tel:lab/{BUFFER_ADDR}"),
            ("power_deficit", "machine_stalled"),
        )
    save_plan(home, MACERATORS, "accepted")
    stock(db, f"{MACHINES}@{MACERATOR}", 2, t)
    quest_done(db, 10, t - 2 * 3600)
    return Scenario(
        name,
        home,
        db,
        t,
        ("build", "goal", "goal"),
        ("plan:pl-macer",),
        ("plan_buildable",),
    )
