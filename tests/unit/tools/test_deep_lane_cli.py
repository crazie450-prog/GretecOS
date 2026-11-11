"""WO-2.4 acceptance (plan 8) with the real Claude Code CLI and a fake Messages API.

The Agent SDK's bundled CLI runs as it would on the PC, pointed at
:class:`gregtec.llm.fake_api.FakeMessagesAPI` with a dummy key: it reports its session (the
init message), serves the in-process ``gos`` MCP server, runs GOS's hooks and enforces its own
tool list. Only the model's turns are scripted, so these check what the CLI and GOS's code do
with them:

- the init message lists only ``mcp__gos__*`` tools, ``Skill`` and the subagent tool;
- a shell request and a write outside the vault both fail, and nothing is written;
- the path guard blocks a write to ``me/``;
- hitting the budget cap pauses the task and asks; approving continues it;
- a subagent runs on its own model and waits, whatever model the orchestrator asks for.

Each test starts one session (a few seconds). Nothing leaves tmp_path or 127.0.0.1.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.agents import deep, deep_config, router
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.mcp import qualified
from gregtec.agents.tools.registry import Registry
from gregtec.db import access
from gregtec.llm import fake_api
from gregtec.llm.fake_api import ApiTurn, FakeMessagesAPI, Scripted
from gregtec.llm.prices import HAIKU, SONNET
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec.vault.io import VaultIO

TEMPLATE = Path(__file__).resolve().parents[3] / "vault-template"
KEY = "sk-ant-fake-0000000000000000000000000000000000000"
WAIT_S = 120.0
SKILL = (
    "---\nname: recipe-lookup\ndescription: Recipe answers.\n---\nUse recipe_find.\n"
)


class Lane:
    def __init__(self, home: Path, reg: Registry, meter: UsageMeter) -> None:
        self.home = home
        self.reg = reg
        self.meter = meter
        self.api = FakeMessagesAPI(Scripted({}))
        self.service: deep.DeepLaneService | None = None

    def run(
        self, scripts: dict[str, list[ApiTurn]], request: str = "plan a steel line"
    ) -> deep.DeepTask:
        self.api.policy = Scripted(scripts)
        assert self.service is not None
        handed = self.service.handoff("a steel line plan", request, "fast_lane")
        task = self.service.wait(handed.id, WAIT_S)
        assert task is not None
        return task

    def results(self) -> list[str]:
        """The text of every tool result the CLI sent back to the fake model."""
        return [
            fake_api.result_text(b)
            for body in self.api.requests
            for b in fake_api.last_tool_results(body)
        ]


@pytest.fixture(scope="module")
def lane(tool_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Lane]:
    home = tmp_path_factory.mktemp("deep-lane")
    root = home / "vault"
    shutil.copytree(TEMPLATE, root)
    skill = root / "_gos" / "skills" / "recipe-lookup"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(SKILL, encoding="utf-8")
    vault = VaultIO(root, today=dt.date.today)
    reg = build_registry(tool_db, vault=vault)
    store = Store(home / "usage.sqlite")
    meter = UsageMeter(store, 100.0, (80, 100))
    state = Lane(home, reg, meter)
    with state.api:
        state.service = deep.DeepLaneService(
            reg,
            deep_config.Settings(task_max_usd=1.0),
            home=home / "agent",
            key=lambda: KEY,
            meter=meter,
            env=state.api.env(),
        )
        state.service.start()
        try:
            yield state
        finally:
            state.service.stop()
            store.close()


def test_init_lists_only_gos_tools_and_skill(lane: Lane) -> None:
    task = lane.run({SONNET: [ApiTurn("Nothing to do.")]})
    assert task.status == deep.DONE, task.error
    assert task.init is not None
    tools = task.init["tools"]
    assert set(tools) - {"Skill", "Task"} == set(deep_config.served(lane.reg))
    assert all(t in ("Skill", "Task") or t.startswith("mcp__gos__") for t in tools)
    assert [s["name"] for s in task.init["mcp_servers"]] == ["gos"]
    assert task.init["apiKeySource"] == "ANTHROPIC_API_KEY"
    assert "gos:recipe-lookup" in task.init["skills"]
    # what the model was offered: the same tools (Agent is the subagent tool's API name)
    first = lane.api.requests[-1]
    offered = set(fake_api.tool_names(first)) - {
        "DeferredToolPlaceholder",
        "ToolSearch",
    }
    assert offered <= {"Skill", "Agent", *deep_config.served(lane.reg)}
    assert "Bash" not in offered and "Write" not in offered and "Read" not in offered
    assert task.text == "Nothing to do."


def test_a_shell_request_fails(lane: Lane) -> None:
    marker = lane.home / "shell-ran.txt"
    turns = [
        ApiTurn("Running it.", [("Bash", {"command": f"echo x > {marker}"})]),
        ApiTurn("", [("PowerShell", {"command": f"Set-Content {marker} x"})]),
        ApiTurn("I can't run commands."),
    ]
    task = lane.run({SONNET: turns}, "run dir on my PC")
    assert task.status == deep.DONE
    out = lane.results()
    assert any("No such tool available: Bash" in r for r in out)
    assert any("No such tool available: PowerShell" in r for r in out)
    assert not marker.exists()


def test_a_write_outside_the_vault_fails(lane: Lane) -> None:
    outside = lane.home / "outside.md"
    turns = [
        ApiTurn("", [("Write", {"file_path": str(outside), "content": "x"})]),
        ApiTurn(
            "", [(qualified("note_write"), {"path": "../outside.md", "body": "x: 1"})]
        ),
        ApiTurn("Couldn't write it."),
    ]
    task = lane.run({SONNET: turns}, "save this outside the vault")
    assert task.status == deep.DONE
    out = lane.results()
    assert any("No such tool available: Write" in r for r in out)
    assert any("each part of a path" in r for r in out)
    assert (
        not outside.exists()
        and not (lane.home / "vault" / ".." / "outside.md").exists()
    )


def test_the_path_guard_blocks_a_write_to_me(lane: Lane) -> None:
    goals = lane.home / "vault" / "me" / "goals.md"
    before = goals.read_bytes()
    turns = [
        ApiTurn(
            "", [(qualified("note_write"), {"path": "me/goals.md", "body": "goal: x"})]
        ),
        ApiTurn(
            "", [(qualified("note_write"), {"path": "_gos/schema.md", "body": "x: 1"})]
        ),
        ApiTurn("Proposed instead."),
    ]
    task = lane.run({SONNET: turns}, "change my goals")
    assert task.status == deep.DONE
    assert goals.read_bytes() == before
    assert any("me/goals.md is Clayton's" in d for d in task.denied)
    assert any("_gos/schema.md is the repo's" in d for d in task.denied)
    assert any("Clayton's" in r for r in lane.results())


def test_the_budget_cap_pauses_and_asks(lane: Lane) -> None:
    assert lane.service is not None
    read = (qualified("note_read"), {"path": "INDEX.md"})
    turns = [
        ApiTurn("Reading.", [read], input_tokens=300_000),  # $0.60 at Sonnet's rate
        ApiTurn("Reading more.", [read], input_tokens=300_000),
        ApiTurn("Still going.", [read], input_tokens=10),
        ApiTurn("All done after approval.", input_tokens=10),
    ]
    task = lane.run({SONNET: turns}, "research every quest line")
    assert task.status == deep.NEEDS_APPROVAL
    assert task.spent_usd >= task.cap_usd == 1.0
    assert any(p.startswith("paused at $") for p in task.progress)
    assert lane.service.approve(task.id)
    task = lane.service.wait(task.id, WAIT_S)
    assert task is not None and task.status == deep.DONE, task.error
    assert task.text == "All done after approval." and task.approvals == 1
    continued = [
        m
        for body in lane.api.requests
        for m in body["messages"]
        if m.get("role") == "user" and "Clayton approved" in str(m.get("content"))
    ]
    assert continued
    rows = lane.meter.store.rows(
        "SELECT SUM(usd) AS usd FROM llm_usage WHERE task = ?", (f"deep:{task.id}",)
    )
    assert rows[0]["usd"] == pytest.approx(task.cost_usd)
    assert task.cost_usd > 1.0


def test_a_subagent_runs_on_its_own_model_and_is_waited_for(lane: Lane) -> None:
    call = {
        "subagent_type": "db-analyst",
        "description": "count recipes",
        "prompt": "How many recipes make steel?",
        "model": "opus",
        "run_in_background": True,
    }
    scripts: dict[str, list[ApiTurn]] = {
        SONNET: [
            ApiTurn("Asking the analyst.", [("Agent", call)]),
            ApiTurn("The analyst answered."),
        ],
        HAIKU: [
            ApiTurn(
                "",
                [(qualified("db_sql"), {"select": "SELECT COUNT(*) AS n FROM recipe"})],
            ),
            ApiTurn("The DB has the count."),
        ],
    }
    task = lane.run(scripts, "how many steel recipes are there, by SQL")
    assert task.status == deep.DONE, task.error
    models = [str(b.get("model")) for b in lane.api.requests[-4:]]
    assert models.count(HAIKU) == 2 and "claude-opus-5-5" not in models
    assert task.subagents == ["db-analyst"]
    assert "db-analyst: mcp__gos__db_sql" in task.progress
    # the orchestrator got the analyst's answer before its own last turn (no background run)
    last = lane.api.requests[-1]
    assert any(
        "The DB has the count." in fake_api.result_text(b)
        for b in fake_api.last_tool_results(last)
    )


def test_the_orchestrator_cant_use_db_sql_or_unknown_agents(lane: Lane) -> None:
    turns = [
        ApiTurn("", [(qualified("db_sql"), {"select": "SELECT 1"})]),
        ApiTurn(
            "",
            [
                (
                    "Agent",
                    {
                        "subagent_type": "general-purpose",
                        "description": "d",
                        "prompt": "p",
                    },
                )
            ],
        ),
        ApiTurn("", [("Skill", {"skill": "update-config"})]),
        ApiTurn("Done."),
    ]
    task = lane.run({SONNET: turns}, "look it up any way you can")
    assert task.status == deep.DONE
    denied = "\n".join(task.denied)
    assert "mcp__gos__db_sql" in denied
    assert "subagent_type must be one of" in denied
    # the CLI's own skill filter refuses the bundled skill before the hook sees it
    assert any(
        "update-config is not in this session's skills" in r for r in lane.results()
    )


def test_hooks_and_servers_written_into_the_vaults_gos_never_run(lane: Lane) -> None:
    """Whatever writes into the live vault's _gos/ after the lane started (a sync, an Obsidian
    plugin) gets no shell: the CLI loads the lane's own copy, manifest and skills only."""
    gos = lane.home / "vault" / "_gos"
    marks = [lane.home / f"mark-{n}" for n in range(4)]
    hook = {"type": "command", "command": f"python -c \"open(r'{marks[0]}', 'w')\""}
    hook2 = {"type": "command", "command": f"python -c \"open(r'{marks[1]}', 'w')\""}
    hooks = {
        "hooks": {
            "SessionStart": [{"hooks": [hook]}],
            "PreToolUse": [{"matcher": "*", "hooks": [hook2]}],
        }
    }
    server = {"command": "python", "args": ["-c", f"open(r'{marks[2]}', 'w')"]}
    manifest = gos / ".claude-plugin" / "plugin.json"
    before = manifest.read_text(encoding="utf-8")
    written = [gos / "hooks" / "hooks.json", gos / ".mcp.json"]
    (gos / "hooks").mkdir()
    written[0].write_text(json.dumps(hooks), encoding="utf-8")
    written[1].write_text(json.dumps({"mcpServers": {"x": server}}), encoding="utf-8")
    inline = {"name": "gos", "hooks": hooks["hooks"], "mcpServers": {"y": server}}
    manifest.write_text(json.dumps(inline), encoding="utf-8")
    try:
        read = (qualified("note_read"), {"path": "INDEX.md"})
        # the first task may still take the session warmed before these were written
        for _ in range(2):
            task = lane.run({SONNET: [ApiTurn("", [read]), ApiTurn("Read it.")]})
            assert task.status == deep.DONE, task.error
            assert task.init is not None
            assert [s["name"] for s in task.init["mcp_servers"]] == ["gos"]
        assert not any(m.exists() for m in marks)
        copy = lane.home / "agent" / "plugin"
        assert not (copy / "hooks").exists() and not (copy / ".mcp.json").exists()
    finally:
        for path in written:
            path.unlink()
        (gos / "hooks").rmdir()
        manifest.write_text(before, encoding="utf-8")


def test_no_inherited_claude_or_anthropic_setting_steers_the_cli(
    tool_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parent Claude Code session, a gateway or model overrides in GOS's own environment
    don't reach the lane's CLI: its own key, its own session, the models GOS chose."""
    parent = "00000000-0000-4000-8000-000000000001"
    for name, value in {
        "CLAUDE_CODE_SESSION_ID": parent,
        "ANTHROPIC_AUTH_TOKEN": "parent-token",
        "ANTHROPIC_CUSTOM_HEADERS": "x-parent: leaked",
        "ANTHROPIC_MODEL": "claude-opus-5-5",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-opus-5-5",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-opus-5-5",
        "CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-5-5",
    }.items():
        monkeypatch.setenv(name, value)
    root = tmp_path / "vault"
    shutil.copytree(TEMPLATE, root)
    reg = build_registry(tool_db, vault=VaultIO(root, today=dt.date.today))
    call = {"subagent_type": "db-analyst", "description": "d", "prompt": "count"}
    scripts = {
        SONNET: [ApiTurn("", [("Agent", call)]), ApiTurn("Done.")],
        HAIKU: [ApiTurn("Counted.")],
    }
    with FakeMessagesAPI(Scripted(scripts)) as api:
        lane = deep.DeepLaneService(
            reg,
            deep_config.Settings(task_max_usd=1.0),
            home=tmp_path / "agent",
            key=lambda: KEY,
            env=api.env(),
        )
        try:
            task = lane.wait(lane.handoff("x", "count it", "router").id, WAIT_S)
        finally:
            lane.stop()
    assert task is not None and task.status == deep.DONE, task.error
    assert task.init is not None and task.init["apiKeySource"] == "ANTHROPIC_API_KEY"
    assert task.session_id and task.session_id != parent
    assert {str(b.get("model")) for b in api.requests} == {SONNET, HAIKU}
    # (names and yes/no only: a failure must never print a credential)
    assert api.headers
    assert all(h.get("x-api-key") == KEY for h in api.headers), "not the lane's key"
    leaked = {n for h in api.headers for n in h if n in ("authorization", "x-parent")}
    assert not leaked


def requests_with(lane: Lane, text: str) -> list[dict[str, Any]]:
    return [b for b in lane.api.requests if text in str(b.get("messages"))]


def test_the_request_is_the_instruction_and_the_note_is_data(lane: Lane) -> None:
    lane.run({SONNET: [ApiTurn("ok")]}, "compare EBF setups")
    sent = requests_with(lane, "compare EBF setups")[-1]
    text = fake_api.request_text(sent)
    assert "<request>\ncompare EBF setups\n</request>" in text
    assert '<task_note source="fast_lane">' in text


# --- gos eval deep-lane ---------------------------------------------------------------------


def test_the_cost_eval_with_the_fake_model(tmp_path: Path) -> None:
    from gregtec.agents import deep_eval
    from gregtec.vault import deploy

    deploy.deploy(tmp_path / "vault")
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    table = tmp_path / "costs.tsv"
    out = io.StringIO()
    assert deep_eval.main(cfg, None, True, 2, table, out) == 0, out.getvalue()
    text = out.getvalue()
    assert text.splitlines()[0].split("\t") == list(deep_eval.TABLE)
    assert "d01\tdesign\tdone\t" in text and "d02\tcompare\tdone\t" in text
    assert "over 2 tasks" in text and "Result: PASS" in text
    rows = table.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3 and rows[1].startswith("d01\tdesign\tdone\t")
    assert not access.db_path(
        tmp_path
    ).exists()  # nothing recorded: no llm_usage, no DB


def test_the_task_file_has_ten_representative_tasks() -> None:
    from gregtec.agents import deep_eval

    rows = deep_eval.load(deep_eval.TASKS)
    assert len(rows) == 10 and len({r.kind for r in rows}) == 10
    # most route straight to the deep lane; the rest reach it through the fast lane's handoff
    deep_rows = [r.id for r in rows if router.route(r.request).kind == router.DEEP]
    assert len(deep_rows) >= 7


def test_the_cost_eval_needs_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gregtec.agents import deep_eval
    from gregtec.llm import keys

    def missing(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", missing)
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    out = io.StringIO()
    assert deep_eval.main(cfg, None, False, 1, tmp_path / "t.tsv", out) == 1
    assert "Not run" in out.getvalue()
