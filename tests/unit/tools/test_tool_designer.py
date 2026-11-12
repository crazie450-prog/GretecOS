"""WO-3.7: the line-designer skill's tools (design_production, plan_get, plan_pin, plan_list,
plan_rename) through the registry, on the tool tests' made-up export (steel ingot: three routes
at HV). The routes keep rank.py's order; every row cites a plan or a recipe; the writing tools are
the deep lane's only.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gregtec.agents.tools.catalog import build_registry
from gregtec.agents.tools.registry import DEEP, FAST
from gregtec.db import access
from gregtec.planner import designer
from gregtec.vault import deploy as vault_deploy
from gregtec.vault.io import VaultIO

from .conftest import STEEL, Runner

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def home(fresh_home: Callable[..., Path]) -> Path:
    h = fresh_home()
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    return h


@pytest.fixture
def run(home: Path) -> Runner:
    reg = build_registry(access.db_path(home), vault=VaultIO(home / "vault"))
    return Runner(reg, DEEP)


def test_design_production_ranks_and_saves(run: Runner, home: Path) -> None:
    p = run("design_production", target=STEEL, rate="1/s", tier="HV")
    assert p.columns[:3] == ["rank", "plan", "name"]
    assert p.col("rank") == ["1", "2", "3"]
    ids = [x.removeprefix("plan:") for x in p.col("plan")]
    assert p.head["design"] == ", ".join(f"plan:{i}" for i in ids)
    assert p.head["weights"].startswith("ranked by weights (balanced)")
    assert any("never reorder" in n for n in p.notes)
    assert any(n.startswith("explanation (template") for n in p.notes)
    assert all(r["differs"] != "-" for r in p.rows)
    assert designer.latest(home) == ids
    assert [float(s) for s in p.col("score")] == sorted(
        (float(s) for s in p.col("score")), reverse=True
    )
    # the same request again: the same order
    again = run("design_production", target=STEEL, rate="1/s", tier="HV")
    assert again.col("plan") == p.col("plan")


def test_design_production_asks_when_unclear(run: Runner) -> None:
    p = run("design_production", target="ingot", tier="HV")
    assert p.head["ambiguous"] == "1"
    assert {r["name"] for r in p.rows} >= {"Steel Ingot", "Iron Ingot"}


def test_plan_get_views_and_pin(run: Runner) -> None:
    ids = [
        x.removeprefix("plan:")
        for x in run("design_production", target=STEEL, tier="HV").col("plan")
    ]
    summary = run("plan_get", id=f"plan:{ids[0]}", view="summary")
    assert summary.head["plan"] == f"plan:{ids[0]}"
    assert summary.head["status"] == "proposed" and summary.head["pins"] == "none"
    nodes = run("plan_get", id=ids[1], view="nodes")
    assert all(r["recipe"].startswith("r:") for r in nodes.rows)
    dust = next(r for r in nodes.rows if r["key"] != STEEL)
    routes = run("plan_get", id=ids[2], view="routes")
    assert routes.col("plan") == [f"plan:{i}" for i in ids]
    alts = run("plan_get", id=ids[1], view="alternatives", node=dust["key"])
    other = next(r["recipe"] for r in alts.rows if r["used"] != "yes")
    assert "need" in run.fail("plan_get", id=ids[1], view="alternatives")
    pinned = run("plan_pin", id=ids[1], node=dust["key"], recipe=other)
    new = pinned.col("plan")[0].removeprefix("plan:")
    got = run("plan_get", id=new, view="summary")
    assert f"{dust['key']}={other}" in got.head["pins"]
    freed = run("plan_pin", id=new, node=dust["key"], unpin=True)
    assert (
        run("plan_get", id=freed.col("plan")[0], view="summary").head["pins"] == "none"
    )
    assert "not both" in run.fail(
        "plan_pin", id=new, node=dust["key"], recipe=other, unpin=True
    )
    assert "give the recipe" in run.fail("plan_pin", id=new, node=dust["key"])


def test_plan_list_and_rename(run: Runner) -> None:
    ids = [
        x.removeprefix("plan:")
        for x in run("design_production", target=STEEL, tier="HV").col("plan")
    ]
    listed = run("plan_list")
    assert set(listed.col("plan")) == {f"plan:{i}" for i in ids}
    assert set(listed.col("status")) == {"proposed"}
    assert run("plan_list", status="accepted").rows == []
    renamed = run("plan_rename", id=ids[0], name="Steel works")
    assert renamed.head["name"] == "Steel works"
    assert "Steel works" in run("plan_list").col("name")
    assert "a name can't" in run.fail("plan_rename", id=ids[0], name="a|b")
    assert "isn't saved" in run.fail("plan_get", id="pl-0000000000", view="summary")


def test_the_fast_lane_gets_only_the_readers(home: Path) -> None:
    reg = build_registry(access.db_path(home), vault=VaultIO(home / "vault"))
    fast = {s["name"] for s in reg.specs(FAST)}
    assert {"plan_get", "plan_list"} <= fast
    assert not {"design_production", "plan_pin", "plan_rename"} & fast
    out = Runner(reg, FAST).out("design_production", target=STEEL)
    assert out.is_error
