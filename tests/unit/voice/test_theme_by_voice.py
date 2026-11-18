"""WO-10.6 acceptance (plan 8 row 10.6: "Clayton creates and refines a theme by voice"), end to
end with a fake model through the voice text path.

Each request is what the voice client sends after the talk key (the fake transcriber's text):
the core's asker routes it (a theme request goes to the deep lane), the deep lane runs the
**real Claude Code CLI** against :class:`gregtec.llm.fake_api.FakeMessagesAPI`, whose scripted
architect calls ``theme_save`` on the in-process ``gos`` MCP server (catalog check and vault
write for real), and the task's ``deep.task`` push reaches the voice client, which speaks the
answer's first sentence. Four requests: create a theme, refine it twice (every version kept in
the vault), then a refinement with a tile-entity block, refused with the reason spoken and
nothing written. The real-voice check on Clayton's PC is a PC step (PR).

Nothing leaves tmp_path or 127.0.0.1.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from gregtec.agents import ask, deep, deep_config
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.mcp import qualified
from gregtec.build import themes
from gregtec.db import access
from gregtec.db import blocks as block_db
from gregtec.ingest import palette as ingest
from gregtec.llm import fake_api
from gregtec.llm.fake_api import ApiTurn, FakeMessagesAPI
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec.vault import schema as vault_schema
from gregtec.vault.io import VaultIO
from gregtec_voice.audio import Block
from gregtec_voice.client import Pushed, VoiceClient
from gregtec_voice.ptt import KeyEvent
from gregtec_voice.settings import VoiceSettings

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "vault-template"
KEY = "sk-ant-fake-0000000000000000000000000000000000000"
WAIT_S = 120.0
F13 = 0x7C  # the default talk key
THEME_ID = "th-brass-foundry"
PALETTE = [
    "wall gregtech:gt.blockconcretes@3 dark concrete bricks",
    "roof gregtech:gt.blockconcretes@7",
    "window minecraft:glass@0",
    "light minecraft:glowstone@0",
]
# What Clayton says, and the theme_save call the scripted architect makes for it.
SAID: dict[str, dict[str, Any]] = {
    "make a new theme called brass foundry with dark concrete and lots of glass": {
        "name": "Brass Foundry",
        "prompt": "make a new theme called brass foundry with dark concrete and lots of glass",
        "summary": "dark concrete halls with big plain windows",
        "fits": "smelting",
        "palette": "\n".join(PALETTE),
        "roof": "steep roofs",
        "window": "big plain windows",
    },
    "refine the brass foundry theme with reinforced glass windows": {
        "id": THEME_ID,
        "change": "reinforced glass windows",
        "palette": "\n".join(
            [*PALETTE[:2], "window gregtech:gt.blockglass1@10", PALETTE[3]]
        ),
    },
    "change the brass foundry theme to flat roofs": {
        "id": THEME_ID,
        "change": "flat roofs",
        "roof": "flat roofs",
    },
    "refine the brass foundry theme with chests as trim": {
        "id": THEME_ID,
        "change": "chests as trim",
        "palette": "\n".join([*PALETTE, "trim minecraft:chest@0"]),
    },
}


class Architect:
    """A fake model that follows the architect skill: one ``theme_save`` for the request, then
    the tool's ``say:`` line, or, when it refuses, the reason in the first sentence."""

    def __call__(self, body: dict[str, Any]) -> ApiTurn:
        request = fake_api.request_text(body)
        plan = next((args for words, args in SAID.items() if words in request), None)
        if plan is None:
            return ApiTurn("Done.")
        results = fake_api.last_tool_results(body)
        if not results:
            return ApiTurn("", [(qualified("theme_save"), plan)])
        text = fake_api.result_text(results[0])
        if results[0].get("is_error"):
            reason = text.split(" not saved: ", 1)[-1].split("; ")[0].strip()
            return ApiTurn(f"I didn't save it: {reason}. Pick a plain block instead.")
        say = re.search(r"^note: say: (.+)$", text, re.MULTILINE)
        assert say is not None, text
        return ApiTurn(f"{say[1]} The palette is on the dashboard.")


def state(block: str, meta: int, *, has_te: bool = False) -> ingest.BlockState:
    return ingest.BlockState(
        block,
        meta,
        block,
        meta,
        f"{block} {meta}",
        "tile.x",
        block.split(":")[0],
        has_te,
        True,
        True,
        True,
        True,
        False,
        False,
        0,
        255,
        "buildingBlocks",
        False,
        (),
    )


@dataclass
class Rig:
    home: Path
    vault: VaultIO
    service: deep.DeepLaneService
    asker: ask.Asker


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Rig]:
    home = tmp_path_factory.mktemp("theme-voice")
    shutil.copytree(TEMPLATE, home / "vault")
    db = access.db_path(home)
    db.parent.mkdir(parents=True)
    conn = access.connect(db)
    try:
        conn.execute(
            "INSERT INTO export_run(id, fingerprint, run_key) VALUES (1, '', 'run-a')"
        )
        states = [
            state("gregtech:gt.blockconcretes", 3),
            state("gregtech:gt.blockconcretes", 7),
            state("gregtech:gt.blockglass1", 10),
            state("minecraft:glass", 0),
            state("minecraft:glowstone", 0),
            state("minecraft:chest", 0, has_te=True),
        ]
        block_db.write(conn, 1, ingest.Palette(tuple(states), (), ()))
        conn.commit()
    finally:
        conn.close()
    vault = VaultIO(home / "vault")
    reg = build_registry(db, vault=vault)
    store = Store(home / "usage.sqlite")
    meter = UsageMeter(store, 100.0, (80, 100))
    with FakeMessagesAPI(Architect()) as api:
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
            yield Rig(home, vault, service, ask.Asker(reg, service, None))
        finally:
            service.stop()
            store.close()


@pytest.fixture
def make(
    clock: Any,  # noqa: ANN401 - the conftest Clock
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
) -> Callable[..., VoiceClient]:
    def build() -> VoiceClient:
        return VoiceClient(
            VoiceSettings(), core, transcriber=transcriber, speaker=speaker, clock=clock
        )

    return build


def say_by_voice(
    rig: Rig,
    words: str,
    make: Callable[..., VoiceClient],
    core: Any,  # noqa: ANN401 - the conftest FakeCore
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
    run_events: Callable[[VoiceClient, Callable[[], bool]], None],
) -> str:
    """One talk-key turn saying ``words``; returns what the voice client said for the task."""
    replies: list[dict[str, Any]] = []

    def answer(text: str) -> dict[str, Any]:
        reply = rig.asker.ask(text, via="voice").as_dict()
        replies.append(reply)
        return reply

    core.answer = answer
    transcriber.text = words
    voice = make()
    before = len(speaker.said)
    voice.handle(KeyEvent(F13, True, clock()))
    for _ in range(50):
        voice.recorder.feed(Block(bytes(640), clock.advance(0.02)))
    voice.handle(KeyEvent(F13, False, clock()))
    for _ in range(10):
        voice.recorder.feed(Block(bytes(640), clock.advance(0.02)))
    run_events(voice, lambda: len(speaker.said) > before)
    voice.drain()
    assert speaker.said[before] == ask.HANDED_OFF, speaker.said
    [reply] = replies
    assert reply["lane"] == "deep", reply
    task = rig.service.wait(reply["handoff"]["id"], WAIT_S)
    assert task is not None and task.status == deep.DONE, task and task.error
    # the real CLI ran the session and the one theme_save call
    assert task.init is not None and task.tools == [qualified("theme_save")], task.tools
    voice.handle(Pushed({"type": "deep.task", "data": task.as_dict()}))
    voice.drain()
    assert len(speaker.said) == before + 2, speaker.said
    return str(speaker.said[-1])


def note_ok(vault: VaultIO, rel: str) -> str:
    text = (vault.root / rel).read_text(encoding="utf-8")
    types = vault_schema.load_types(vault.root / "_gos" / "schema.md")
    assert vault_schema.check_text(rel, text, types) == []
    return text


def test_create_and_refine_a_theme_by_voice(
    rig: Rig,
    make: Callable[..., VoiceClient],
    core: Any,  # noqa: ANN401
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
    run_events: Callable[[VoiceClient, Callable[[], bool]], None],
) -> None:
    turn = [make, core, speaker, transcriber, clock, run_events]
    said = list(SAID)
    store = themes.Themes(rig.vault)

    assert say_by_voice(rig, said[0], *turn) == (
        "Saved Brass Foundry as a new theme, version 1."
    )
    first = store.get(THEME_ID)
    assert first.v == 1 and first.prompt == said[0]

    assert say_by_voice(rig, said[1], *turn) == (
        "Saved Brass Foundry as a new version, version 2."
    )
    assert say_by_voice(rig, said[2], *turn) == (
        "Saved Brass Foundry as a new version, version 3."
    )
    now = store.get(THEME_ID)
    assert now.v == 3 and now.roof == ("flat roofs",)
    assert [s.block for s in now.palette if s.role == "window"] == [
        "gregtech:gt.blockglass1@10"
    ]
    assert now.change == ("v2 reinforced glass windows", "v3 flat roofs")
    # every version is kept, each a note in the grammar, never in me/ or _gos/
    assert store.versions(THEME_ID) == [1, 2]
    assert store.get(THEME_ID, 1).roof == ("steep roofs",)
    assert store.get(THEME_ID, 2).palette[2].block == "gregtech:gt.blockglass1@10"
    for rel in (
        f"themes/{THEME_ID}.md",
        f"themes/versions/{THEME_ID}--v1.md",
        f"themes/versions/{THEME_ID}--v2.md",
    ):
        note_ok(rig.vault, rel)
    assert not list((rig.vault.root / "me").rglob("th-*"))
    assert not list((rig.vault.root / "_gos").rglob(f"{THEME_ID}*"))

    # an invalid palette entry: refused, the reason spoken, nothing written
    files = sorted(p for p in (rig.vault.root / "themes").rglob("*") if p.is_file())
    before = [p.read_bytes() for p in files]
    spoken = say_by_voice(rig, said[3], *turn)
    assert spoken.startswith(
        "I didn't save it: minecraft:chest@0: has a tile entity"
    ), spoken
    assert "shell holds plain blocks only" in spoken
    assert (
        sorted(p for p in (rig.vault.root / "themes").rglob("*") if p.is_file())
        == files
    )
    assert [p.read_bytes() for p in files] == before
    assert store.get(THEME_ID).v == 3
