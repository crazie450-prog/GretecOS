"""WO-2.4 on a DB built from a real GregtecLink harness run: the deep lane's tools through the
real Claude Code CLI's MCP calls (fake model, fake API).

The scripted orchestrator resolves an item, looks up its recipes and cites the first ``r:`` id
the tool returned; the db-analyst subagent counts recipes with ``db_sql``. Both read the harness
DB through the in-process ``gos`` MCP server, read-only, as on the PC. As
test_fast_lane_harness.py: only with GOS_REQUIRE_HARNESS_RUN=1 after the client harness.
Nothing is written outside tmp_path.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.agents import deep, deep_config
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.db import access
from gregtec.ingest import collect
from gregtec.llm import fake_api
from gregtec.llm.fake_api import ApiTurn, FakeMessagesAPI
from gregtec.llm.prices import HAIKU, SONNET
from gregtec.vault.io import VaultIO

REPO = Path(__file__).resolve().parents[2]
KEY = "sk-ant-fake-0000000000000000000000000000000000000"


@pytest.fixture(scope="module")
def home(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("deep-lane-harness")
    built_home(game, home)
    shutil.copytree(REPO / "vault-template", home / "vault")
    return home


class Steel:
    """Orchestrator: item_resolve, recipe_find with the key it got, then the db-analyst, then
    an answer citing the first recipe id; the analyst: one db_sql count, then its answer."""

    def __init__(self) -> None:
        self.key = ""
        self.ref = ""

    def __call__(self, body: dict[str, Any]) -> ApiTurn:
        results = fake_api.last_tool_results(body)
        text = fake_api.result_text(results[0]) if results else ""
        if str(body.get("model")) == HAIKU:
            if not results:
                sql = "SELECT COUNT(*) AS n FROM recipe"
                return ApiTurn("", [("mcp__gos__db_sql", {"select": sql})])
            return ApiTurn(f"Counted: {text.strip()[:200]}")
        if not results:
            return ApiTurn("", [("mcp__gos__item_resolve", {"text": "steel ingot"})])
        if not self.key:
            found = re.search(r"\n1\t[^\n]*", text)
            row = found.group(0).split("\t") if found else []
            self.key = next((c for c in row if c.startswith("gregtech:")), "")
            return ApiTurn("", [("mcp__gos__recipe_find", {"item": self.key})])
        if not self.ref:
            self.ref = (re.findall(r"r:[A-Za-z0-9_.:-]+/[0-9a-f]{12}", text) or [""])[0]
            agent = {
                "subagent_type": "db-analyst",
                "description": "count recipes",
                "prompt": "How many recipes does the DB hold?",
            }
            return ApiTurn("", [("Agent", agent)])
        return ApiTurn(f"Steel ingots: the first recipe is {self.ref}.")


@pytest.fixture
def lane(
    home: Path, tmp_path: Path
) -> Iterator[tuple[deep.DeepLaneService, FakeMessagesAPI, Steel]]:
    vault = VaultIO(home / "vault")
    reg = build_registry(
        access.db_path(home), Glossary.find(home / "vault"), vault=vault
    )
    policy = Steel()
    with FakeMessagesAPI(policy) as api:
        service = deep.DeepLaneService(
            reg,
            deep_config.Settings(),
            home=tmp_path / "agent",
            key=lambda: KEY,
            env=api.env(),
        )
        try:
            yield service, api, policy
        finally:
            service.stop()


def test_the_deep_lane_reads_the_harness_db(
    lane: tuple[deep.DeepLaneService, FakeMessagesAPI, Steel],
) -> None:
    service, api, policy = lane
    handed = service.handoff(
        "steel ingot recipes", "how do I make steel ingots", "router"
    )
    task = service.wait(handed.id, 180)
    assert task is not None and task.status == deep.DONE, task and task.error
    assert policy.key.startswith("gregtech:"), "item_resolve found no steel ingot"
    assert policy.ref.startswith("r:"), "recipe_find returned no recipe id"
    assert task.text == f"Steel ingots: the first recipe is {policy.ref}."
    assert task.tools == [
        "mcp__gos__item_resolve",
        "mcp__gos__recipe_find",
        "mcp__gos__db_sql",
    ]
    assert task.subagents == ["db-analyst"]
    models = [str(b.get("model")) for b in api.requests]
    assert models.count(HAIKU) == 2 and models.count(SONNET) == 4
    analyst = [b for b in api.requests if b.get("model") == HAIKU][-1]
    counted = fake_api.result_text(fake_api.last_tool_results(analyst)[0])
    assert re.search(r"\n1\t[1-9][0-9]*", counted), counted
