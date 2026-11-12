"""The Designer (WO-3.7) on a real harness DB: the dev runtime's recipes and machines.

As test_outputs_harness.py, it runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the
server harness; locally: mod/README.md), and writes nothing outside tmp_path. §8 row 3.7:

- end to end, a **text** request goes to the deep lane (the real Claude Code CLI against the fake
  model replaying ``tests/evals/design_tokens.tsv``), whose ``design_production`` call designs and
  saves the routes on this DB; Clayton accepts one and it is in the vault (``plans/``,
  ``base/lines/``);
- a pin on a node of a real design survives loading the plan again and a re-solve;
- the deep-lane cost harness reports every task and gives a ``task_max_usd``.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from gregtec.agents import design_cost
from gregtec.db import build
from gregtec.ingest import collect
from gregtec.planner import designer
from gregtec.planner.request import Request
from gregtec.vault import deploy as vault_deploy
from gregtec.vault import schema as vault_schema

REPO = Path(__file__).resolve().parents[2]
STEEL = "gregtech:gt.metaitem.01@11305"


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    h = tmp_path_factory.mktemp("designer-harness")
    collect.collect(game, h, 1, allow_harness=True)
    build.build(h, allow_harness=True)
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    (h / "config").mkdir()
    (h / "config" / "gos.toml").write_text(
        f'[paths]\ngos_home = "{h.as_posix()}"\n', encoding="utf-8"
    )
    return h


def note_ok(home: Path, rel: str) -> str:
    text = (home / "vault" / rel).read_text(encoding="utf-8")
    types = vault_schema.load_types(home / "vault" / "_gos" / "schema.md")
    assert vault_schema.check_text(rel, text, types) == []
    return text


def test_a_text_request_to_an_accepted_plan_in_the_vault(home: Path) -> None:
    out = io.StringIO()
    code = design_cost.main(
        home / "config" / "gos.toml",
        fake=True,
        limit=1,  # g01: "plan a steel ingot line that makes 2 ingots per second at MV"
        table_path=home / "costs.tsv",
        out=out,
    )
    text = out.getvalue()
    assert code == 0, text
    assert "g01\tdesign\tdone\t" in text and "# task_max_usd = " in text
    # The eval runs on a copy of the DB and vault (review L2): the plans are there.
    assert designer.latest(home) == []
    home = home / design_cost.SCRATCH
    ids = designer.latest(home)
    assert len(ids) >= 2, "the deep lane's design_production saved no routes"
    # plan_explain's writer call ran on the plan it saved (review H1)
    row = (home.parents[2] / "costs.tsv").read_text("utf-8").splitlines()[1]
    explain_usd = dict(zip(design_cost.TABLE, row.split("\t"), strict=True))[
        "explain_usd"
    ]
    assert float(explain_usd) > 0
    plans = [designer.load(home, i) for i in ids]
    assert {p["target"]["id"] for p in plans} == {STEEL}
    assert [
        p["rank"]["position"] for p in plans if p["rank"]["column"] == "base"
    ] == list(range(1, len([p for p in plans if p["rank"]["column"] == "base"]) + 1))
    done = designer.accept(home, ids[0])
    assert done.problems == [] and done.line is not None
    line = note_ok(home, done.line)
    assert "st: accepted\n" in line and f"plan: plan:{ids[0]}\n" in line
    assert "st: accepted\n" in note_ok(home, f"plans/{ids[0]}.md")


def test_a_pin_survives_reload_and_re_solve(home: Path) -> None:
    d = designer.design(home, Request(STEEL, qty=1, tier_cap="HV"))
    view = designer.view(home, d.ids[0])
    # a node with another candidate recipe, in any route
    for route in view["routes"]:
        for node in route["nodes"]:
            if not node["item"]:
                continue
            alts = designer.alternatives(home, route["id"], node["item"])
            free = [a for a in alts["alternatives"] if not a["used"]]
            if free:
                break
        else:
            continue
        break
    else:
        pytest.skip("no node of this design has another recipe in the harness DB")
    pinned = designer.pin(home, route["id"], node["item"], free[0]["recipe"])
    want = {"node": node["item"], "recipe": free[0]["recipe"]}
    assert all(want in designer.load(home, i)["pins"] for i in pinned.ids)
    again = designer.resolve(home, pinned.ids[0])
    assert again.ids == pinned.ids
    assert want in again.plans[0]["pins"]
