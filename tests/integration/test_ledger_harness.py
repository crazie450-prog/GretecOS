"""The EU ledger (WO-4.4, D238, D239) on a real server harness DB.

As test_designer_harness.py, it runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the
server harness; locally: mod/README.md) and writes nothing outside tmp_path. §8 row 4.4: the ledger
updates when a plan is accepted, here a real steel line and a real power line, through the
plan-accept path with a temp vault; running lines' outputs reach My base.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from gregtec import store as core_store
from gregtec.base.state import MyBase
from gregtec.cards import validate as card_problems
from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import designer, ledger, power
from gregtec.planner.request import DefaultBaseInputs, Request
from gregtec.vault import deploy as vault_deploy
from gregtec.vault import schema as vault_schema

REPO = Path(__file__).resolve().parents[2]
STEEL = "gregtech:gt.metaitem.01@11305"
HV = 3


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    h = tmp_path_factory.mktemp("ledger-harness")
    collect.collect(game, h, 1, allow_harness=True)
    build.build(h, allow_harness=True)
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    return h


def note_ok(home: Path, rel: str) -> str:
    text = (home / "vault" / rel).read_text(encoding="utf-8")
    types = vault_schema.load_types(home / "vault" / "_gos" / "schema.md")
    assert vault_schema.check_text(rel, text, types) == []
    return text


def read(home: Path) -> ledger.Ledger:
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        return ledger.read(conn)
    finally:
        conn.close()


def test_accepting_real_plans_updates_the_ledger(home: Path) -> None:
    d = designer.design(home, Request(STEEL, qty=2, tier_cap="MV"))
    steel = d.plans[0]
    done = designer.accept(home, steel["id"])
    assert done.problems == [] and done.ledger["recorded"]
    led = read(home)
    [ln] = led.lines
    assert ln.entry.plan == steel["id"] and ln.status == "accepted"
    # Every number is the plan's own, per tier.
    assert {t.tier: t.use_eut_avg for t in led.tiers["all"]} == {
        e["tier"]: float(e["eut_avg"]) for e in steel["metrics"]["eu"]
    }
    text = note_ok(home, ledger.NOTE)
    assert f"plan:{steel['id']}\t" in text
    assert "deficit\tall\t-\t" in text  # a line and no source yet

    conn = access.connect(access.db_path(home), readonly=True)
    try:
        pd = power.design(
            conn,
            power.PowerRequest(mode="best", tier_cap=HV),
            DefaultBaseInputs(HV),
            with_effort=False,
        )
    finally:
        conn.close()
    assert pd.options, pd.rejected
    opt = pd.options[0]
    done = designer.accept_power(home, opt)
    assert done.problems == [] and done.line == f"base/lines/ln-{opt['id']}.md"
    note_ok(home, done.line)
    led = read(home)
    assert {ln.entry.plan for ln in led.lines} == {steel["id"], opt["id"]}
    assert led.total("all", "gen_eut") == opt["power"]["net_eut"]
    gen, use = led.total("all", "gen_eut"), led.total("all", "use_eut_avg")
    assert any(w.kind == "deficit" and w.scope == "all" for w in led.warnings) == (
        use > gen
    )
    assert card_problems(ledger.card(led)) == []
    note_ok(home, ledger.NOTE)

    # Running: the steel line's output joins My base's line outputs.
    designer.set_status(home, steel["id"], "building")
    designer.set_status(home, steel["id"], "running")
    c: sqlite3.Connection = core_store.connect(access.db_path(home))
    try:
        assert MyBase.from_db(c).line_outputs() == {STEEL: 2.0}
    finally:
        c.close()
    designer.set_status(home, steel["id"], "retired")
    assert {ln.entry.plan for ln in read(home).lines} == {opt["id"]}
