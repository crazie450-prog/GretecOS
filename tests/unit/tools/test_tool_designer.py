"""WO-3.7: the line-designer skill's tools (design_production, plan_get, plan_pin, plan_list,
plan_rename) through the registry, on the tool tests' made-up export (steel ingot: three routes
at HV). The routes keep rank.py's order; every row cites a plan or a recipe; the writing tools are
the deep lane's only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from gregtec.agents import deep_config
from gregtec.agents.plan_writer import WriterError
from gregtec.agents.tools.catalog import build_registry
from gregtec.agents.tools.registry import DEEP, FAST
from gregtec.agents.tools.tsv import parse
from gregtec.db import access
from gregtec.planner import designer
from gregtec.planner.explain import Writer
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


def _explain_run(home: Path, writer: Writer | None) -> Runner:
    reg = build_registry(
        access.db_path(home), vault=VaultIO(home / "vault"), writer=lambda: writer
    )
    return Runner(reg, DEEP)


def _ids(run: Runner) -> list[str]:
    p = run("design_production", target=STEEL, tier="HV")
    return [x.removeprefix("plan:") for x in p.col("plan")]


def test_plan_explain_checks_the_model_with_the_validator(home: Path) -> None:
    """Review H1, D199: the explanation goes through explain.explain. A model that states a
    number the plans don't have is refused twice, then GOS's template comes back."""
    prompts: list[str] = []

    def wrong(prompt: str) -> str:
        prompts.append(prompt)
        return "Route two needs 987654 Electric Blast Furnaces."

    run = _explain_run(home, wrong)
    ids = _ids(run)
    out = run.out("plan_explain", id=f"plan:{ids[1]}")
    assert not out.is_error, out.text
    p = parse(out.text)
    assert p.head["source"] == "template" and p.head["attempts"] == "2"
    assert p.head["design"] == ", ".join(f"plan:{i}" for i in ids)  # rank order
    refused = [n for n in p.notes if "refused by the validator" in n]
    assert len(refused) == 2 and all("987654" in n for n in refused)
    assert "Ranked by code" in out.text  # the template's first line
    assert "Electric Blast Furnaces." not in out.text
    assert len(prompts) == 2 and "987654" in prompts[1]  # the retry names it
    assert '"position":1' in prompts[0]  # the metrics JSON, code's order


def test_plan_explain_says_the_model_text_when_it_checks_out(home: Path) -> None:
    run = _explain_run(home, lambda prompt: "The first route ranks first on its score.")
    out = run.out("plan_explain", id=_ids(run)[0])
    p = parse(out.text)
    assert p.head["source"] == "model" and p.head["attempts"] == "1"
    assert "The first route ranks first on its score." in out.text


def test_plan_explain_without_a_model_is_the_template(home: Path) -> None:
    def down(prompt: str) -> str:
        raise WriterError("APIConnectionError: no network")

    for writer, why in ((None, "no planner model"), (down, "couldn't be reached")):
        run = _explain_run(home, writer)
        p = parse(run.out("plan_explain", id=_ids(run)[0]).text)
        assert p.head["source"] == "template"
        assert any(why in n for n in p.notes)
    assert "isn't saved" in run.fail("plan_explain", id="pl-0000000000")


def test_the_fast_lane_gets_only_the_readers(home: Path) -> None:
    reg = build_registry(access.db_path(home), vault=VaultIO(home / "vault"))
    fast = {s["name"] for s in reg.specs(FAST)}
    assert {"plan_get", "plan_list"} <= fast
    assert not {"design_production", "plan_pin", "plan_rename", "plan_explain"} & fast
    out = Runner(reg, FAST).out("design_production", target=STEEL)
    assert out.is_error


def test_the_line_designer_skill(run: Runner, tmp_path: Path) -> None:
    """The skill (authored in vault-template/_gos, deployed): plain front matter the CLI parses on
    Windows too, only tools the deep lane has, no game numbers, and the rules that matter."""
    path = REPO / "vault-template" / "_gos" / "skills" / "line-designer" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    meta = dict(line.split(": ", 1) for line in head.splitlines())
    assert meta["name"] == "line-designer" and set(meta) == {"name", "description"}
    assert len(meta["description"]) <= 1024
    for value in meta.values():
        assert not re.search(r": | #|^[-?:,\[\]{}#&*!|>'\"%@`]", value), value
    named = set(re.findall(r"`([a-z_]+)\(", body))
    assert {"design_production", "plan_get", "plan_pin", "plan_explain"} <= named
    assert "Don't ask the `planner` subagent" in body  # H1: the checked text only
    assert named <= {t.name for t in run.reg.tools(DEEP)}
    assert "never change the order" in body and "Accepting a plan is Clayton's" in body
    assert "!`" not in body  # no shell: the deep lane has none (D154)
    assert not re.search(r"r:[\w.]+/[0-9a-f]{12}", body)  # no game numbers
    plugin = deep_config.build_plugin(
        REPO / "vault-template" / "_gos", tmp_path / "plugin"
    )
    assert "gos:line-designer" in deep_config.skill_names(plugin)
