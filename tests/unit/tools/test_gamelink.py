"""WO-8.1 (D285, D286): GregtecLink's client link in the core: the "what's this?" answer from the
DB, the router's instant command, ``WS /mod/ws`` with its own tokens, the key and probe paths,
``!gos`` only from the player's own name, chat as data, status and events, revocation, and
failure notices. Starlette's TestClient on the tool tests' self-authored DB, no network."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from gregtec.agents import router
from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import Config
from gregtec.gamelink import link as gamelink
from gregtec.gamelink import whatsthis
from gregtec.ingest.normalize import nbt_hash
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

ADMIN = "admin-" + "g" * 40
KEY = b"k" * 32  # the tool tests' cursor key (conftest)
MACHINES = "gregtech:gt.blockmachines"
STEEL = "gregtech:gt.metaitem.01@11305"
BASE = "http://127.0.0.1:8765"
MOD_WS = "ws://127.0.0.1:8765/mod/ws"
DASH_WS = "ws://127.0.0.1:8765/ws"
EVENT_DDL = (
    "CREATE TABLE IF NOT EXISTS event(id INTEGER PRIMARY KEY, ts INT NOT NULL, "
    "session_id INT, kind TEXT NOT NULL, dim INT, x INT, y INT, z INT, ref TEXT, payload TEXT)"
)


def item(name: str, meta: int, nbt: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"item": name, "meta": meta}
    if nbt is not None:
        out["nbt"] = nbt
    return out


def target(
    it: dict[str, Any] | None, kind: str = "item", **kw: object
) -> dict[str, Any]:
    return {"kind": kind, "item": it, **kw}


# --- the answer ----------------------------------------------------------------------------


def test_keys_go_from_the_exact_nbt_to_the_plain_item_and_its_wildcard() -> None:
    nbt = {"charge": "10000.0D", "inner": {"__item": "minecraft:stone", "id": 1}}
    keys_ = whatsthis.keys_for(item("IC2:itemBatRE", 1, nbt))
    # the numeric id beside __item is dropped before hashing, as the export's NBT was
    assert keys_[0] == "IC2:itemBatRE@1#" + nbt_hash(
        {"charge": "10000.0D", "inner": {"__item": "minecraft:stone"}}
    )
    assert keys_[1:] == ["IC2:itemBatRE@1", "IC2:itemBatRE@32767"]
    assert whatsthis.keys_for(item("a:b", 32767)) == ["a:b@32767"]


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "minecraft:stone",
        {"item": "stone", "meta": 0},  # no namespace
        {"item": "a:b", "meta": -1},
        {"item": "a:b", "meta": True},
        {"item": "a:b@1", "meta": 0},
        {"meta": 0},
    ],
)
def test_a_malformed_item_is_nothing(reg: Registry, bad: object) -> None:
    got = whatsthis.answer(reg, {"kind": "item", "item": bad})
    assert got.text == whatsthis.NOTHING and not got.found


def test_an_item_is_named_from_the_db_with_its_recipes_and_uses(reg: Registry) -> None:
    got = whatsthis.answer(reg, target(item("gregtech:gt.metaitem.01", 11305)))
    assert got.found and got.key == STEEL and got.name == "Steel Ingot"
    assert got.text.startswith("Steel Ingot, from gregtech.")
    assert "make it; the first is" in got.text and "It goes into" in got.text
    assert got.refs and all(r.startswith("r:") for r in got.refs)
    assert got.refs[0] in got.text  # the citation is in the answer itself
    assert got.card is not None and got.card["type"] == "recipe"


def test_a_looked_at_machine_is_named_with_its_mte(reg: Registry) -> None:
    block = {"block": MACHINES, "meta": 3, "x": 1, "y": 2, "z": 3, "dim": 0}
    got = whatsthis.answer(reg, target(item(MACHINES, 1000), "block", block=block))
    assert got.found and got.name == "Electric Blast Furnace"
    assert "mte:1000" in got.refs and "(mte:1000)" in got.text


def test_an_unknown_nbt_falls_back_to_the_plain_item(reg: Registry) -> None:
    got = whatsthis.answer(
        reg, target(item("minecraft:iron_ingot", 0, {"display": {"Name": "Shiny"}}))
    )
    assert got.found and got.key == "minecraft:iron_ingot@0"
    assert "That exact NBT isn't in the DB" in got.text


def test_an_item_the_db_lacks_is_said_so_with_the_games_name(reg: Registry) -> None:
    got = whatsthis.answer(reg, target(item("nope:thing", 4), display="§eOdd\nThing"))
    assert (
        not got.found and got.text == "eOddThing (nope:thing@4) isn't in the recipe DB."
    )
    assert whatsthis.answer(reg, target(item("nope:thing", 4))).text == (
        "nope:thing@4 isn't in the recipe DB."
    )


def test_no_target_no_link_and_the_feed_off(reg: Registry) -> None:
    assert whatsthis.answer(reg, None).text == whatsthis.NOTHING
    assert whatsthis.answer(reg, None, linked=False).text == whatsthis.NO_LINK
    assert whatsthis.answer(reg, {"kind": "off"}).text == whatsthis.TARGET_OFF
    assert whatsthis.answer(reg, {"kind": "none"}).text == whatsthis.NOTHING


@pytest.mark.parametrize(
    "said",
    [
        "What's this?",
        "what is this",
        "Hey GOS, what's that?",
        "what is this block",
        "what am I looking at",
        "what am i hovering over",
        "what's this i'm looking at",
    ],
)
def test_the_router_takes_whats_this_as_an_instant_command(said: str) -> None:
    got = router.route(said)
    assert (got.kind, got.intent) == (router.INSTANT, router.WHATS_THIS)


def test_other_what_is_questions_are_unchanged() -> None:
    assert router.route("what is steel").intent == router.ITEM
    assert router.route("what's this recipe for steel").intent != router.WHATS_THIS


# --- the link ------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


@pytest.fixture
def core(tmp_path: Path, tool_db: Path, glossary: Glossary) -> Iterator[Core]:
    store = Store(tmp_path / "data" / "gos.sqlite")
    with store.transaction() as conn:
        conn.execute(EVENT_DDL)
    deep = HandoffRecorder()
    c = Core(
        cfg=Config(gos_home=tmp_path),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        asker=Asker(build_registry(tool_db, glossary, key=KEY), deep, None),
    )
    yield c
    store.close()


@pytest.fixture
def client(core: Core) -> Iterator[TestClient]:
    with TestClient(create_app(core), base_url=BASE) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def hello(player: str = "Clayton") -> str:
    return json.dumps(
        {
            "type": "hello",
            "v": 1,
            "player": player,
            "mod_version": "test",
            "feeds": {"position": True, "target": True, "chat": True, "notices": True},
        }
    )


def until(ws: WebSocketTestSession, kind: str, n: int = 20) -> dict[str, Any]:
    for _ in range(n):
        message = json.loads(ws.receive_text())
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind} message")


def wait_for(check: Callable[[], object], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def connect(
    client: TestClient, core: Core, binding: str = "lab"
) -> AbstractContextManager[WebSocketTestSession]:
    token = core.auth.issue_client_token(f"gregteclink-{binding}")
    return client.websocket_connect(MOD_WS, headers=bearer(token))


def test_only_gregteclinks_own_token_opens_the_link(
    client: TestClient, core: Core
) -> None:
    voice = core.auth.issue_client_token("voice")
    oc = core.auth.issue_client_token("oc-lab")
    for headers in ({}, bearer(voice), bearer(oc), bearer(ADMIN), bearer("x" * 43)):
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            client.websocket_connect(MOD_WS, headers=headers) as ws,
        ):
            ws.receive_text()
        assert refused.value.code == 1008
    link = core.auth.issue_client_token("gregteclink-play")
    # ... and it opens nothing else: not a client principal
    assert client.get("/api/session", headers=bearer(link)).status_code == 401
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(DASH_WS, headers=bearer(link)) as ws,
    ):
        ws.receive_text()
    with client.websocket_connect(MOD_WS, headers=bearer(link)) as ws:
        assert json.loads(ws.receive_text()) == {"type": "hello", "v": 1}
    assert core.auth.link_binding(link) == ("play", "gregteclink-play")
    assert core.auth.link_binding(voice) is None
    for bad in ("gregteclink", "gregteclink-export", "gregteclink-lab-2"):
        with pytest.raises(ValueError, match="gregteclink-play or gregteclink-lab"):
            core.auth.issue_client_token(bad)


def test_the_key_report_is_answered_in_game_and_on_the_dashboard(
    client: TestClient, core: Core
) -> None:
    with (
        client.websocket_connect(DASH_WS, headers=bearer(ADMIN)) as dash,
        connect(client, core) as mod,
    ):
        until(mod, "hello")
        mod.send_text(hello())
        report = {
            "type": "target",
            "v": 1,
            "id": 7,
            "why": "key",
            "kind": "item",
            "item": {"item": "minecraft:iron_ingot", "meta": 0},
            "display": "Iron Ingot",
        }
        mod.send_text(json.dumps(report))
        notice = until(mod, "notice")
        assert notice["kind"] == "answer" and notice["re"] == 7
        assert notice["key"] == "minecraft:iron_ingot@0"
        assert notice["name"] == "Iron Ingot"
        assert notice["text"].startswith("Iron Ingot, from minecraft.")
        pushed = until(dash, "ask.reply")["data"]
        assert pushed["by"] == "game" and pushed["request"] == "What's this?"
        assert pushed["intent"] == router.WHATS_THIS
        assert pushed["target"]["key"] == "minecraft:iron_ingot@0"


def _ask_in_thread(client: TestClient, text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def run() -> None:
        out["reply"] = client.post(
            "/api/ask", json={"text": text}, headers=bearer(ADMIN)
        ).json()

    t = threading.Thread(target=run)
    t.start()
    out["thread"] = t
    return out


def test_saying_whats_this_asks_the_game(client: TestClient, core: Core) -> None:
    with connect(client, core) as mod:
        until(mod, "hello")
        mod.send_text(hello())
        wait_for(lambda: core.gamelink is not None and core.gamelink.newest())
        asked = _ask_in_thread(client, "What's this?")
        probe = until(mod, "probe")
        mod.send_text(
            json.dumps(
                {
                    "type": "target",
                    "v": 1,
                    "id": 1,
                    "why": "probe",
                    "probe": probe["id"],
                    "kind": "block",
                    "item": {"item": MACHINES, "meta": 1000},
                    "block": {"block": MACHINES, "meta": 0, "x": 0, "y": 5, "z": 0},
                }
            )
        )
        asked["thread"].join(10)
        reply = asked["reply"]
        assert reply["intent"] == router.WHATS_THIS and reply["lane"] == "instant"
        assert reply["target"]["name"] == "Electric Blast Furnace"
        assert "mte:1000" in reply["refs"]


def test_a_game_that_doesnt_answer_falls_back_to_the_last_key_report(
    client: TestClient, core: Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gamelink, "PROBE_TIMEOUT_S", 0.2)
    with connect(client, core) as mod:
        until(mod, "hello")
        mod.send_text(hello())
        mod.send_text(
            json.dumps(
                {
                    "type": "target",
                    "id": 1,
                    "why": "key",
                    "kind": "item",
                    "item": {"item": "minecraft:iron_ingot", "meta": 0},
                }
            )
        )
        until(mod, "notice")
        got = client.post(
            "/api/ask", json={"text": "what's this"}, headers=bearer(ADMIN)
        ).json()
        assert (
            got["target"]["key"] == "minecraft:iron_ingot@0"
        )  # the probe went unanswered
        assert core.gamelink is not None
        core.gamelink.last_key = (time.time() - gamelink.KEY_FRESH_S - 1, {})
        got = client.post(
            "/api/ask", json={"text": "what's this"}, headers=bearer(ADMIN)
        ).json()
        assert got["text"] == whatsthis.NOTHING  # too old to stand in
    got = client.post(
        "/api/ask", json={"text": "what's this"}, headers=bearer(ADMIN)
    ).json()
    assert got["text"] == whatsthis.NO_LINK


def test_gos_in_chat_is_a_question_only_from_the_players_own_name(
    client: TestClient, core: Core
) -> None:
    with connect(client, core) as mod:
        until(mod, "hello")
        mod.send_text(hello("Clayton"))

        def chat(sender: str | None, body: str | None, text: str) -> None:
            msg: dict[str, Any] = {"type": "chat", "text": text}
            if sender is not None:
                msg["sender"], msg["body"] = sender, body
            mod.send_text(json.dumps(msg))

        # someone else, a server line that only looks like Clayton's, and plain chat: data only
        chat(
            "Mallory",
            "!gos recipes for steel ingot",
            "<Mallory> !gos recipes for steel ingot",
        )
        chat(None, None, "<Clayton> !gos recipes for steel ingot")
        chat("Clayton", "hello !gos recipes for steel", "<Clayton> hello !gos recipes")
        chat("Clayton", "!gos", "<Clayton> !gos")
        chat(
            "Clayton",
            "!GOS recipes for steel ingot",
            "<Clayton> !GOS recipes for steel ingot",
        )
        notice = until(mod, "notice")
        assert notice["kind"] == "gos" and notice["text"].startswith("Steel Ingot:")
        # a chat line can't confirm an action (D59)
        chat("Clayton", "!gos confirm", "<Clayton> !gos confirm")
        assert until(mod, "notice")["text"] in (
            "Nothing is waiting for confirmation.",
            "Confirm by voice, on the dashboard, on the phone or in Discord; this way in can't confirm.",
        )
        assert core.gamelink is not None
        wait_for(lambda: len(core.gamelink.chat) == 6)  # type: ignore[union-attr]
    rows = core.store.rows("SELECT kind, payload FROM event WHERE kind = 'chat'")
    assert len(rows) == 6
    assert json.loads(rows[0]["payload"])["sender"] == "Mallory"


def test_status_is_kept_and_a_dimension_change_is_an_event(
    client: TestClient, core: Core
) -> None:
    with connect(client, core, "play") as mod:
        until(mod, "hello")
        mod.send_text(hello())
        for dim, x in ((0, 10.5), (0, 11.0), (-1, 1.25), (-1, "bad")):
            mod.send_text(
                json.dumps(
                    {
                        "type": "status",
                        "dim": dim,
                        "dim_name": "D",
                        "x": x,
                        "y": 64,
                        "z": -3,
                        "paused": False,
                    }
                )
            )
        assert core.gamelink is not None
        wait_for(
            lambda: (
                core.gamelink.state()["status"] is not None  # type: ignore[union-attr]
                and core.gamelink.state()["status"]["dim"] == -1
            )  # type: ignore[union-attr]
        )
        state = client.get("/api/game", headers=bearer(ADMIN)).json()
        assert state["status"]["x"] == 1.25 and state["links"][0]["binding"] == "play"
        assert state["links"][0]["player"] == "Clayton"
    assert client.get("/api/game").status_code == 401
    kinds = [r["kind"] for r in core.store.rows("SELECT kind FROM event ORDER BY id")]
    assert kinds == ["location", "dim_change"]
    row = core.store.rows("SELECT payload, dim FROM event WHERE kind = 'dim_change'")[0]
    assert json.loads(row["payload"]) == {"from": 0, "to": -1} and row["dim"] == -1


def test_a_revoked_token_closes_the_link(
    client: TestClient, core: Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gamelink, "RECHECK_S", 0.05)
    with connect(client, core) as mod:
        until(mod, "hello")
        core.auth.revoke_client_token("gregteclink-lab")
        with pytest.raises(WebSocketDisconnect) as closed:
            for _ in range(100):
                mod.receive_text()
        assert closed.value.code == 1008


def test_failures_become_notices_in_the_game(client: TestClient, core: Core) -> None:
    with connect(client, core) as mod:
        until(mod, "hello")
        assert core.gamelink is not None
        wait_for(lambda: core.gamelink.newest() is not None)  # type: ignore[union-attr]
        item = {"id": "voice", "label": "Voice", "status": "ok", "detail": ""}
        core.gamelink.health_changed({"items": [item]})
        core.gamelink.health_changed(
            {"items": [{**item, "status": "error", "detail": "exited"}]}
        )
        assert until(mod, "notice") == {
            "type": "notice",
            "kind": "failure",
            "text": "Voice: exited",
        }
        core.gamelink.health_changed({"items": [item]})
        assert until(mod, "notice")["text"] == "Voice is back."


def test_an_event_without_the_table_is_skipped(tmp_path: Path) -> None:
    store = Store(tmp_path / "gos.sqlite")
    try:
        core = Core(
            cfg=Config(gos_home=tmp_path),
            store=store,
            auth=Auth(store, ADMIN),
            meter=UsageMeter(store, 100.0, (80, 100)),
        )
        gamelink.GameLink(core)._event("chat", None, {"text": "x"})  # no error
        with pytest.raises(sqlite3.OperationalError):
            store.rows("SELECT * FROM event")
    finally:
        store.close()
