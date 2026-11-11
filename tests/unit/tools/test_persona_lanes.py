"""WO-5.6: personas in the running core: ``GET/POST /api/persona`` (the dashboard dropdown
and the Discord bot's ``/persona``), the state push, and the deep lane's next reply in the new
persona through its ``UserPromptSubmit`` hook (a fake SDK client that runs the hook as the
Claude Code CLI does)."""

from __future__ import annotations

import math
import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, Message, ResultMessage, SystemMessage
from fastapi.testclient import TestClient

from gregtec.agents import deep, deep_config
from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.personas import NEVER_RULES, Personas
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.api import dashboard, persona_routes
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec.vault.io import VaultIO

from .conftest import KEY

ADMIN = "admin-" + "p" * 40
BASE = "http://gos.localhost:8765"
WS = "ws://gos.localhost:8765/ws"
TEMPLATE = Path(__file__).resolve().parents[3] / "vault-template"
API_KEY = "sk-ant-test-0000000000000000000000000000000000"


def admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN}"}


# --- the API -----------------------------------------------------------------------------------


@pytest.fixture
def core(tmp_path: Path, tool_db: Path, glossary: Glossary) -> Iterator[Core]:
    home = tmp_path / "corehome"
    (home / "data").mkdir(parents=True)
    db = home / "data" / "gos.sqlite"
    shutil.copyfile(tool_db, db)
    store = Store(db)
    cfg = Config(gos_home=home)
    found = Personas(None, state=cfg.data_dir / "persona.json")
    registry = build_registry(db, glossary, key=KEY, deep=HandoffRecorder())
    c = Core(
        cfg=cfg,
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        asker=Asker(registry, HandoffRecorder(), None, personas=found),  # type: ignore[arg-type]
        board=dashboard.Board(
            cfg,
            dist=tmp_path / "dist",
            foreground=lambda: None,
            clients=lambda cfg: [],
            next_focus=math.inf,
            next_game=math.inf,
            next_data=math.inf,
        ),
        personas=found,
    )
    persona_routes.wire(c)
    yield c
    store.close()


@pytest.fixture
def client(core: Core, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(keys, "has_key", lambda name: False)
    with TestClient(create_app(core), base_url=BASE) as c:
        yield c


def token(client: TestClient, name: str) -> dict[str, str]:
    issued = client.post(f"/api/tokens/{name}", headers=admin()).json()["token"]
    return {"Authorization": f"Bearer {issued}"}


def test_the_api_lists_and_switches(client: TestClient, core: Core) -> None:
    got = client.get("/api/persona", headers=admin()).json()
    assert got["active"] == "gt-veteran"
    assert [p["id"] for p in got["packs"]] == ["coop-buddy", "gt-veteran", "ship-ai"]
    out = client.post("/api/persona", headers=admin(), json={"id": "Ship AI"})
    assert out.status_code == 200
    body = out.json()
    assert body["persona"]["id"] == "ship-ai" and body["active"] == "ship-ai"
    assert core.personas is not None and core.personas.active is not None
    assert core.personas.active.id == "ship-ai"
    # the dashboard's state carries it (Settings' dropdown)
    state = client.get("/api/state", headers=admin()).json()
    assert state["persona"]["active"] == "ship-ai"
    # and the next answer is made in it
    reply = client.post("/api/ask", headers=admin(), json={"text": "which persona"})
    assert reply.json()["persona"]["id"] == "ship-ai"


def test_who_may_switch(client: TestClient) -> None:
    voice = token(client, "voice")
    assert client.get("/api/persona", headers=voice).status_code == 200
    refused = client.post("/api/persona", headers=voice, json={"id": "ship-ai"})
    assert refused.status_code == 403
    bot = token(client, "discord")  # WO-9.x's /persona command
    ok = client.post("/api/persona", headers=bot, json={"id": "coop buddy"})
    assert ok.status_code == 200 and ok.json()["active"] == "coop-buddy"
    assert client.post("/api/persona", json={"id": "ship-ai"}).status_code == 401


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"id": "butler"}, 404),
        ({"id": ""}, 400),
        ({"id": 3}, 400),
        ({}, 400),
        ({"id": "x" * 61}, 400),
    ],
)
def test_bad_switches(client: TestClient, body: dict[str, Any], status: int) -> None:
    out = client.post("/api/persona", headers=admin(), json=body)
    assert out.status_code == status
    if status == 404:
        assert "There is: Co-op Buddy, GT Veteran, Ship AI" in out.json()["error"]


def test_a_switch_by_voice_is_pushed_to_the_dashboard(client: TestClient) -> None:
    with client.websocket_connect(WS, headers=admin()) as ws:
        reply = client.post(
            "/api/ask", headers=admin(), json={"text": "persona coop buddy"}
        )
        assert reply.json()["persona"]["id"] == "coop-buddy"
        for _ in range(20):
            message = ws.receive_json()
            if (
                message.get("type") == "state"
                and (message["data"].get("persona") or {}).get("active") == "coop-buddy"
            ):
                break
        else:
            raise AssertionError("no state push with the new persona")


def test_pack_problems_reach_trusted_callers_only(
    client: TestClient, core: Core, tmp_path: Path
) -> None:
    """Review L2: the problems name files and parse errors, so only admin and paired devices
    (the trusted snapshot, the API as them) see them; clients get the packs alone."""
    vault = tmp_path / "vault"
    (vault / "personas").mkdir(parents=True)
    (vault / "personas" / "Bad Name.md").write_text("x", encoding="utf-8")
    core.personas = Personas(vault, template=TEMPLATE)
    snaps = dashboard.snapshots(core)
    assert snaps[True]["persona"]["problems"] == [
        "Bad Name.md: a pack is pe-<name>.md (lower case, digits, -)"
    ]
    assert snaps[False]["persona"]["problems"] == []
    assert snaps[False]["persona"]["active"] == snaps[True]["persona"]["active"]
    assert client.get("/api/persona", headers=admin()).json()["problems"]
    voice = token(client, "voice")
    assert client.get("/api/persona", headers=voice).json()["problems"] == []
    bot = token(client, "discord")
    out = client.post("/api/persona", headers=bot, json={"id": "ship ai"})
    assert out.status_code == 200 and out.json()["problems"] == []


def test_no_personas_is_a_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)
    store = Store(tmp_path / "gos.sqlite")
    core = Core(
        cfg=Config(gos_home=tmp_path),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )
    try:
        with TestClient(create_app(core), base_url=BASE) as c:
            assert c.get("/api/persona", headers=admin()).status_code == 503
    finally:
        store.close()


# --- the deep lane: the next reply uses the new persona ----------------------------------------


class HookedClient:
    """A fake SDK client that, like the CLI, runs the ``UserPromptSubmit`` hooks on each prompt
    and answers in the persona their ``additionalContext`` named."""

    def __init__(self, options: ClaudeAgentOptions, log: list[str]) -> None:
        self.options = options
        self.log = log
        self.contexts: list[str] = []
        self.answers: list[str] = []

    async def connect(self) -> None:
        self.log.append("connect")

    async def query(self, prompt: str) -> None:
        hooks = (self.options.hooks or {}).get("UserPromptSubmit", [])
        added = ""
        for matcher in hooks:
            for hook in matcher.hooks:
                out = await hook(
                    {"hook_event_name": "UserPromptSubmit", "prompt": prompt},
                    None,
                    {"signal": None},
                )
                added += (out.get("hookSpecificOutput") or {}).get(
                    "additionalContext", ""
                )
        self.contexts.append(added)
        name = next(
            (
                line.removeprefix("name: ")
                for line in added.splitlines()
                if line.startswith("name: ")
            ),
            "nobody",
        )
        self.answers.append(f"As {name}: the line is planned.")
        self.log.append(f"query:{name}")

    async def receive_response(self) -> AsyncIterator[Message]:
        if len(self.contexts) == 1:
            yield SystemMessage(
                "init",
                {
                    "tools": ["Task", "Skill"],
                    "mcp_servers": [{"name": "gos", "status": "connected"}],
                    "apiKeySource": "ANTHROPIC_API_KEY",
                    "session_id": "sess-1",
                },
            )
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            total_cost_usd=0.001,
            result=self.answers[-1],
            terminal_reason="completed",
        )

    async def interrupt(self) -> None:
        self.log.append("interrupt")

    async def disconnect(self) -> None:
        self.log.append("disconnect")


@pytest.fixture
def lane_reg(tool_db: Path, tmp_path: Path) -> Registry:
    root = tmp_path / "vault"
    shutil.copytree(TEMPLATE, root)
    return build_registry(tool_db, vault=VaultIO(root))


def test_the_next_deep_lane_reply_uses_the_new_persona(
    lane_reg: Registry, tmp_path: Path
) -> None:
    log: list[str] = []
    clients: list[HookedClient] = []

    def factory(options: ClaudeAgentOptions) -> HookedClient:
        clients.append(HookedClient(options, log))
        return clients[-1]

    lane = deep.DeepLaneService(
        lane_reg,
        deep_config.Settings(task_max_usd=1.0),
        home=tmp_path / "agent",
        key=lambda: API_KEY,
        client_factory=factory,
    )
    found = Personas(None)
    lane.persona = found.context  # as ask.setup wires it
    try:
        first = lane.handoff("steel", "plan a steel line", "router")
        task = lane.wait(first.id, 10)
        assert task is not None and task.status == deep.DONE, task
        assert task.text == "As GT Veteran: the line is planned."
        found.set("ship ai", by="test")  # between two tasks
        second = lane.handoff("steel", "plan another steel line", "router")
        task = lane.wait(second.id, 10)
        assert task is not None and task.status == deep.DONE
        assert task.text == "As Ship AI: the line is planned."
    finally:
        lane.stop()
    # Each task's session got the persona from the hook, never from its system prompt.
    asked = [c for c in clients if c.contexts]
    assert [c.contexts[0].count("name: ") for c in asked] == [1, 1]
    for client in asked:
        assert client.contexts[0].endswith("\n".join(NEVER_RULES))
        assert "Persona" not in str(client.options.system_prompt or "")
