"""WO-9.1: the discord.py glue (``gregtec.discordbot.adapter``) with fake discord.py
interactions: no gateway, no network. The six commands are registered with the options plan
7.12 names, admin-only by default; every message goes out with mentions off; component and
modal interactions reach the bot's one door; the buttons' own view never runs a callback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from gregtec.discordbot import adapter
from gregtec.discordbot.handlers import Button, Message
from gregtec.remote.jobs import JobQueue
from gregtec.store import Store

from .fakes import CLAYTON, SETTINGS, STRANGER, FakeClock, FakeCore, designed


@dataclass
class Response:
    done: bool = False
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def is_done(self) -> bool:
        return self.done

    async def defer(self, **kw: object) -> None:
        self.calls.append(("defer", kw))
        self.done = True

    async def edit_message(self, **kw: object) -> None:
        self.calls.append(("edit_message", kw))
        self.done = True


@dataclass
class Followup:
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def send(self, **kw: object) -> None:
        self.sent.append(kw)


def fake_inter(
    user: int, itype: discord.InteractionType, data: dict[str, Any] | None = None
) -> Any:  # noqa: ANN401 - duck-typed discord.Interaction
    return SimpleNamespace(
        id=1_400_000_000_000_000_001,
        user=SimpleNamespace(id=user),
        type=itype,
        data=data or {},
        response=Response(),
        followup=Followup(),
    )


@pytest.fixture
def core(tmp_path: Path) -> Any:  # noqa: ANN401
    store = Store(tmp_path / "gos.sqlite")
    yield FakeCore(queue=JobQueue(store, designed), clock=FakeClock())
    store.close()


def test_kind_of_each_interaction_type() -> None:
    t = discord.InteractionType
    assert adapter.kind_of(fake_inter(1, t.application_command)) == "command"
    assert adapter.kind_of(fake_inter(1, t.autocomplete)) == "autocomplete"
    assert adapter.kind_of(fake_inter(1, t.modal_submit)) == "modal"
    assert (
        adapter.kind_of(fake_inter(1, t.component, {"component_type": 2}))
        == "component"
    )
    assert (
        adapter.kind_of(fake_inter(1, t.component, {"component_type": 3})) == "select"
    )
    assert adapter.kind_of(fake_inter(1, t.ping)) == "other"


def test_every_message_goes_out_with_mentions_off() -> None:
    async def go() -> None:
        out = adapter.payload(
            Message(
                "@everyone <@1> hi",
                files=(("a-flow.png", b"png"),),
                buttons=(Button("gos:confirm:1", "Confirm"),),
            )
        )
        mentions = out["allowed_mentions"]
        assert not mentions.everyone and not mentions.users and not mentions.roles
        assert [f.filename for f in out["files"]] == ["a-flow.png"]
        view = out["view"]
        assert [c.custom_id for c in view.children] == ["gos:confirm:1"]
        # The view itself never handles a press: on_interaction does, through the gate.
        assert (
            await view.interaction_check(
                fake_inter(CLAYTON, discord.InteractionType.component)
            )
            is False
        )

    asyncio.run(go())


def test_the_six_commands_are_registered_admin_only(core: Any) -> None:  # noqa: ANN401
    async def go() -> None:
        client = adapter.GosClient(SETTINGS, core)
        commands = {c.name: c for c in client.tree.get_commands()}
        assert sorted(commands) == [
            "alerts",
            "ask",
            "persona",
            "plan",
            "power",
            "status",
        ]
        for c in commands.values():
            assert (
                c.default_permissions is not None
                and c.default_permissions.administrator
            )
        params = {name: [p.name for p in c.parameters] for name, c in commands.items()}
        assert params == {
            "ask": ["text"],
            "plan": ["target", "amount", "within", "tier"],
            "power": ["mode", "tier"],
            "status": [],
            "alerts": ["mode", "hours"],
            "persona": ["name"],
        }
        assert client.intents.value == 0  # no message content, members or presences
        await client.close()

    asyncio.run(go())


def test_the_tree_gate_lets_only_clayton_through(core: Any) -> None:  # noqa: ANN401
    async def go() -> None:
        client = adapter.GosClient(SETTINGS, core)
        cmd = discord.InteractionType.application_command
        assert await client.tree.interaction_check(fake_inter(CLAYTON, cmd)) is True
        assert await client.tree.interaction_check(fake_inter(STRANGER, cmd)) is False
        await client.close()

    asyncio.run(go())


def test_a_command_callback_reaches_the_bot_and_defers(core: Any) -> None:  # noqa: ANN401
    async def go() -> None:
        client = adapter.GosClient(SETTINGS, core)
        inter = fake_inter(CLAYTON, discord.InteractionType.application_command)
        await client.tree.get_command("persona").callback(inter, name=None)  # type: ignore[union-attr]
        assert inter.response.calls == [("defer", {"thinking": True})]
        assert "Personas:" in inter.followup.sent[0]["content"]
        assert inter.followup.sent[0]["allowed_mentions"] == adapter.NO_MENTIONS
        await client.close()

    asyncio.run(go())


def test_a_button_press_from_clayton_goes_through_on_interaction(
    core: Any,  # noqa: ANN401
) -> None:
    async def go() -> None:
        client = adapter.GosClient(SETTINGS, core)
        inter = fake_inter(
            CLAYTON,
            discord.InteractionType.component,
            {"component_type": 2, "custom_id": "gos:confirm:7"},
        )
        await client.on_interaction(inter)
        assert inter.response.calls == [("edit_message", {"view": None})]
        assert ("confirm", (7,)) in core.calls
        # A command interaction is the tree's, never on_interaction's.
        cmd = fake_inter(CLAYTON, discord.InteractionType.application_command)
        await client.on_interaction(cmd)
        assert cmd.response.calls == [] and cmd.followup.sent == []
        await client.close()

    asyncio.run(go())
