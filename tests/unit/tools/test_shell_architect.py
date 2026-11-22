"""WO-10.7 acceptance, the architect's path (plan 8 row 10.7), end to end with a fake model.

The deep lane runs the **real Claude Code CLI** against
:class:`gregtec.llm.fake_api.FakeMessagesAPI`: the scripted orchestrator hands each request to
the ``architect`` subagent (the CLI's Agent tool), whose scripted turns call ``build_request`` on
the in-process ``gos`` MCP server with a design in the building vocabulary; code compiles it,
checks it and saves it or refuses it, for real. Four designs, as a model might send them:

- a good one: accepted and saved (design, layout, manifest, preview, notes);
- one with a chest (a tile entity): refused, nothing written, the reason in the answer;
- one with a pillar inside a machine's keep-out: repaired (those cells removed) and said;
- one over its max_blocks: refused, nothing written.

The architect's model is never asked to estimate anything: every number in a design is a
coordinate the code checks. No paid call is made; the real model's cost bound is the PR's.
Nothing leaves tmp_path or 127.0.0.1.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.agents import deep, deep_config
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.mcp import qualified
from gregtec.build import layout, shell, shell_store
from gregtec.db import access
from gregtec.llm import fake_api
from gregtec.llm.fake_api import ApiTurn, FakeMessagesAPI
from gregtec.llm.prices import SONNET
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec.vault.io import VaultIO

KEY = "sk-ant-fake-0000000000000000000000000000000000000"
WAIT_S = 120.0
HERE = Path(__file__).resolve().parent


def _module(name: str, path: Path) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


shells = _module("_wo107_shell_tests", HERE.parent / "test_shell.py")


class Architect:
    """The orchestrator hands the request to the architect; the architect sends the design
    named in the request (CASES) once, then answers from the tool's result."""

    def __init__(self, cases: dict[str, list[dict[str, Any]]]) -> None:
        self.cases = cases

    def __call__(self, body: dict[str, Any]) -> ApiTurn:
        model = str(body.get("model", ""))
        request = fake_api.request_text(body)
        case = next((c for c in self.cases if f"case {c}" in request), None)
        results = fake_api.last_tool_results(body)
        if model.startswith(SONNET):  # the orchestrator
            if not results:
                call = {
                    "subagent_type": "architect",
                    "description": "design the shell",
                    "prompt": f"Design a foundry shell around plan pl-steel (case {case}).",
                }
                return ApiTurn("Asking the architect.", [("Agent", call)])
            return ApiTurn(fake_api.result_text(results[0]).strip() or "Done.")
        if case is None:
            return ApiTurn("Done.")
        if not results:
            args = {
                "plans": "pl-steel",
                "theme": "th-foundry",
                "line_type": "smelting",
                "design": json.dumps(self.cases[case]),
            }
            if case == "too-many":
                args["max_blocks"] = 50
            return ApiTurn("", [(qualified("build_request"), args)])
        text = fake_api.result_text(results[0])
        if results[0].get("is_error"):
            why = text.split("nothing was written: ", 1)[-1].split(". Fix", 1)[0]
            return ApiTurn(f"I didn't build it: {why}.")
        build = next(x for x in text.splitlines() if x.startswith("build: "))
        status = next(x for x in text.splitlines() if x.startswith("status: "))
        return ApiTurn(f"The shell is designed. {build} {status}")


@dataclass
class Rig:
    home: Path
    service: deep.DeepLaneService
    cases: dict[str, list[dict[str, Any]]]


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Rig]:
    root = tmp_path_factory.mktemp("shell-architect")
    home = shells.make_home(root, shells.lay_tests.production())
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        lay = layout.plan_layout(conn, [shells.lay_tests.production()])
        spec = shells.spec_for(conn, lay, line_type="smelting")
    finally:
        conn.close()
    good = shells.good_design(spec)
    k = next(v for v in spec.clear if v.kind == "keepout")
    mid = [(k.box.lo[i] + k.box.hi[i]) // 2 for i in range(3)]
    cases = {
        "good": good,
        "chest": [
            *good,
            {"op": "fill", "block": shells.CHEST, "lo": [-1, 1, -1], "hi": [-1, 1, -1]},
        ],
        "breach": [
            *good,
            {
                "op": "fill",
                "role": "pillar",
                "lo": [mid[0], 0, mid[2]],
                "hi": [mid[0], 3, mid[2]],
            },
        ],
        "too-many": good,
    }
    vault = VaultIO(home / "vault")
    reg = build_registry(access.db_path(home), vault=vault)
    store = Store(home / "usage.sqlite")
    meter = UsageMeter(store, 100.0, (80, 100))
    with FakeMessagesAPI(Architect(cases)) as api:
        service = deep.DeepLaneService(
            reg,
            deep_config.Settings(task_max_usd=1.0),
            home=home / "agent",
            key=lambda: KEY,
            meter=meter,
            env=api.env(),
        )
        service.start()
        try:
            yield Rig(home, service, cases)
        finally:
            service.stop()
            store.close()


def run(rig: Rig, case: str) -> deep.DeepTask:
    handed = rig.service.handoff(
        "a shell",
        f"design a foundry shell around my steel line (case {case})",
        "router",
    )
    task = rig.service.wait(handed.id, WAIT_S)
    assert task is not None
    assert task.status == deep.DONE, task.error
    assert task.subagents == ["architect"], task.subagents
    assert qualified("build_request") in task.tools, (task.tools, list(task.progress))
    assert any("architect: mcp__gos__build_request" in p for p in task.progress), (
        task.progress
    )
    return task


def saved(rig: Rig) -> set[str]:
    root = rig.home / shell_store.ROOT
    return {p.name for p in root.iterdir()} if root.is_dir() else set()


def test_the_architect_designs_checks_and_saves(rig: Rig) -> None:
    before = saved(rig)
    task = run(rig, "good")
    assert "The shell is designed." in (task.text or "") and "status: accepted" in (
        task.text or ""
    )
    new = saved(rig) - before
    assert len(new) == 1
    rec = shell_store.load(rig.home, new.pop())
    assert rec["source"] == "architect" and rec["status"] == "accepted"
    assert rec["ops"] == rig.cases["good"]


def test_a_tile_entity_block_is_refused_and_nothing_written(rig: Rig) -> None:
    before = saved(rig)
    task = run(rig, "chest")
    assert "I didn't build it" in (task.text or "")
    assert "has a tile entity" in (task.text or "")
    assert saved(rig) == before


def test_a_keepout_breach_is_repaired(rig: Rig) -> None:
    before = saved(rig)
    task = run(rig, "breach")
    assert "status: repaired" in (task.text or "")
    new = saved(rig) - before
    rec = shell_store.load(rig.home, new.pop())
    assert rec["status"] == "repaired"
    assert any("inside keepout" in r for r in rec["repairs"])


def test_too_many_blocks_is_refused(rig: Rig) -> None:
    before = saved(rig)
    task = run(rig, "too-many")
    assert "more than max_blocks" in (task.text or "")
    assert saved(rig) == before
    assert shell.LIMITS["max_blocks"][0] > 50
