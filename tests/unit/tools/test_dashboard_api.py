"""WO-2.6: the core's dashboard routes (gregtec.api.dashboard) over the tool tests' made-up DB.

The built UI at / with its security headers, the state the shell reads (game, data, AI, tier,
alerts, approvals, jobs), answers and their pushes, recipe cards by citation, the lookup tools as
catalog cards, the VERIFY list and doctor. Runs the real app in-process (Starlette's TestClient).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import shutil
import sqlite3
import sys
import time
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gregtec import cards, locks
from gregtec.agents import deep
from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.api import dashboard
from gregtec.api.app import Core, Socket, create_app
from gregtec.api.security import Auth, Principal
from gregtec.config import Config
from gregtec.ingest import fingerprint
from gregtec.llm import keys
from gregtec.llm.fake import EchoClient
from gregtec.llm.prices import Usage
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

from .conftest import KEY

ADMIN = "admin-" + "a" * 40
BASE = "http://gos.localhost:8765"
WS = "ws://gos.localhost:8765/ws"
EBF = "r:gt.recipe.blastfurnace/9c40869458cb"


def admin(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN}", **extra}


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


@pytest.fixture(autouse=True)
def key_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: True)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "fonts" / "licenses").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Gregtec OS</title>", "utf-8")
    (d / "assets" / "index-1234.js").write_text("console.log(1)", "utf-8")
    (d / "assets" / "index-1234.css").write_text("body{margin:0}", "utf-8")
    (d / "assets" / "font-1234.woff2").write_bytes(b"wOF2")
    (d / "fonts" / "licenses" / "README.txt").write_text("OFL", "utf-8")
    return d


@pytest.fixture
def core(
    tmp_path: Path, tool_db: Path, glossary: Glossary, dist: Path
) -> Iterator[Core]:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    db = home / "data" / "gos.sqlite"
    shutil.copyfile(tool_db, db)
    cfg = Config(gos_home=home)
    store = Store(db)
    deep = HandoffRecorder()
    registry = build_registry(db, glossary, key=KEY, deep=deep)
    c = Core(
        cfg=cfg,
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        asker=Asker(registry, deep, FastLane(EchoClient(), registry, meter=None)),  # type: ignore[arg-type]
        # The core's watcher runs while a test client is open: its own refreshes never come
        # due, so they can't race a test's (each test refreshes when it means to).
        board=dashboard.Board(
            cfg,
            dist=dist,
            foreground=lambda: "javaw.exe",
            clients=lambda cfg: ["play"],
            next_focus=math.inf,
            next_game=math.inf,
            next_data=math.inf,
        ),
    )
    yield c
    store.close()


@pytest.fixture
def client(core: Core) -> Iterator[TestClient]:
    core.key_status = ("ok", "stored")
    with TestClient(create_app(core), base_url=BASE) as c:
        yield c


def token(client: TestClient, name: str = "voice") -> dict[str, str]:
    issued = client.post(f"/api/tokens/{name}", headers=admin()).json()["token"]
    return {"Authorization": f"Bearer {issued}"}


# --- the built UI ------------------------------------------------------------------------------


def test_the_ui_is_served_with_a_strict_policy(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "Gregtec OS" in page.text
    csp = page.headers["content-security-policy"]
    for rule in ("default-src 'self'", "font-src 'self'", "connect-src 'self'",
                 "frame-ancestors 'none'", "script-src 'self'", "style-src 'self'"):  # fmt: skip
        assert rule in csp
    assert "googleapis" not in csp and "unsafe-inline" not in csp
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-cache"
    assert page.headers["content-type"].startswith("text/html")
    js = client.get("/assets/index-1234.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith(
        "text/javascript"
    )
    assert "immutable" in js.headers["cache-control"]
    # Types are fixed, never read from the Windows registry (nosniff would refuse a wrong one).
    css = client.get("/assets/index-1234.css")
    assert css.headers["content-type"].startswith("text/css")
    font = client.get("/assets/font-1234.woff2")
    assert font.headers["content-type"] == "font/woff2"
    licence = client.get("/fonts/licenses/README.txt")
    assert licence.text == "OFL" and licence.headers["content-type"].startswith(
        "text/plain"
    )


def test_the_ui_needs_no_credential_but_the_api_does(client: TestClient) -> None:
    """An unpaired browser must load the pairing screen; it reads nothing else."""
    assert client.get("/").status_code == 200
    for path in ("/api/state", "/api/answers", "/api/verify", f"/api/recipe?ref={EBF}"):
        assert client.get(path).status_code == 401
    assert client.post("/api/tools/recipe_find", json={"args": {}}).status_code == 401


def test_the_ui_still_checks_host_and_origin(client: TestClient) -> None:
    assert client.get("/", headers={"Host": "evil.example:8765"}).status_code == 403
    r = client.get("/assets/index-1234.js", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/assets/..%2Findex.html",
        "/assets/nope.js",
        "/assets/",
        "/fonts/../index.html",
        "/assets/x%00",
        "/assets/index-1234.js%00",
        "/assets/index-1234.js%3A%3A%24DATA",
    ],
)
def test_files_outside_dist_are_not_served(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_file_lookup_refuses_escapes(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    (root / "a.js").write_text("x", "utf-8")
    (tmp_path / "secret.txt").write_text("s", "utf-8")
    assert dashboard._file(root, "a.js") == (root / "a.js").resolve()
    for rel in ("", "../secret.txt", "a/../a.js", "..\\secret.txt", ".", "nope.js",
                "a.js\0", "x\0", "a.js:stream", "C:a.js"):  # fmt: skip
        assert dashboard._file(root, rel) is None
    if sys.platform != "win32":
        (root / "link.txt").symlink_to(tmp_path / "secret.txt")
        assert dashboard._file(root, "link.txt") is None


def test_loopback_names_go_to_the_dashboard_name(client: TestClient) -> None:
    for host in ("127.0.0.1:8765", "localhost:8765"):
        r = client.get("/", headers={"Host": host}, follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "http://gos.localhost:8765/"


def test_an_unbuilt_dashboard_says_how_to_build_it(
    client: TestClient, core: Core
) -> None:
    assert core.board is not None
    shutil.rmtree(core.board.dist)
    r = client.get("/")
    assert r.status_code == 503 and "pnpm -C dashboard build" in r.text
    # Static text with an inline style: nothing else may load.
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "style-src 'unsafe-inline'" in csp
    assert "script-src" not in csp


def test_hello_names_the_dashboard_build(
    client: TestClient, core: Core, dist: Path
) -> None:
    """A page that loaded another build reloads itself (M4: monitor 2 after a deploy)."""
    built = hashlib.sha256((dist / "index.html").read_bytes()).hexdigest()
    with client.websocket_connect(WS, headers=admin()) as ws:
        assert ws.receive_json()["build"] == built
    (dist / "index.html").write_text("<!doctype html><title>newer</title>", "utf-8")
    with client.websocket_connect(WS, headers=admin()) as ws:
        assert ws.receive_json()["build"] not in (built, None)
    shutil.rmtree(dist)
    with client.websocket_connect(WS, headers=admin()) as ws:
        assert ws.receive_json()["build"] is None


# --- state ------------------------------------------------------------------------------------


def refresh(core: Core) -> None:
    assert core.board is not None
    core.board.refresh_focus()
    core.board.refresh_game()
    core.board.refresh_data()


def test_the_state_snapshot(client: TestClient, core: Core) -> None:
    refresh(core)
    s = client.get("/api/state", headers=admin()).json()
    assert s["game"] == {"running": ["play"], "focused": True, "paused": None,
                         "export": None, "error": None}  # fmt: skip
    assert s["ai"] == {"online": True, "detail": "stored"}
    assert s["oc"]["status"] == "not_set_up"
    assert s["tier"]["stage"] == "stone" and s["tier"]["tier"] == 0
    assert s["data"]["db_run"]["run_key"] == "20261106T120000Z"
    assert s["data"]["stale"] is None and s["data"]["last_export"] is None
    assert s["health"]["type"] == "health" and cards.validate(s["health"]) == []
    assert s["jobs"] == [] and s["approvals"] == [] and s["asking"] == []


def test_the_focus_signal(core: Core) -> None:
    board = core.board
    assert board is not None
    for program, focused in (("javaw.exe", True), ("java.exe", True), ("chrome.exe", False),
                             (None, None)):  # fmt: skip
        board.foreground = lambda p=program: p  # type: ignore[misc]
        board.refresh_focus()
        assert board.focused is focused

    def broken() -> str | None:
        raise OSError("no desktop")

    board.foreground = broken
    board.refresh_focus()
    assert board.focused is None


def test_a_game_check_that_fails_is_unknown(core: Core) -> None:
    def broken(cfg: Config) -> list[str]:
        raise OSError("PowerShell exited 1")

    assert core.board is not None
    core.board.clients = broken
    core.board.refresh_game()
    assert core.board.running is None and "PowerShell" in (core.board.game_error or "")


def test_stale_data_and_credits_are_alerts(client: TestClient, core: Core) -> None:
    home = core.cfg.gos_home
    fingerprint._save_state(
        home,
        {"format": fingerprint.FORMAT, "stale": True, "play": {"checked_utc": "now"},
         "exports": [{"run_id": "20261106T120000Z", "fingerprint": "f" * 64,
                      "recorded_utc": "2026-11-06T12:00:00Z"}]},
    )  # fmt: skip
    core.meter.record("fast", "claude-haiku-5-5", Usage(), usd=85.0)
    refresh(core)
    s = client.get("/api/state", headers=admin()).json()
    assert s["data"]["stale"] is True and "20261106T120000Z" in s["data"]["line"]
    assert s["data"]["last_export"]["run_id"] == "20261106T120000Z"
    by_id = {a["id"]: a for a in s["alerts"]}
    assert by_id["data-stale"]["level"] == "warn"
    assert by_id["credits"]["value"] == 85.0 and by_id["credits"]["level"] == "warn"
    assert all(cards.validate(a) == [] for a in s["alerts"])


def test_a_running_export_is_a_job(client: TestClient, core: Core) -> None:
    with locks.hold(core.cfg.gos_home, "export", "exporting for a test"):
        refresh(core)
    s = client.get("/api/state", headers=admin()).json()
    assert s["game"]["export"]["reason"] == "exporting for a test"
    assert s["jobs"] == [
        {"id": "export", "kind": "export", "title": "Export running", "status": "running",
         "detail": "exporting for a test", "started": None}
    ]  # fmt: skip


def test_a_home_without_a_recipe_db_yet_is_no_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Before the first `gos db build` the DB file holds only the core's own tables: no run and
    no tier, and nothing logged (the core's watcher refreshes this every 5 s). A file that isn't
    a DB still says why."""
    store = Store(tmp_path / "data" / "gos.sqlite")
    try:
        board = dashboard.Board(Config(gos_home=tmp_path), store=store)
        with caplog.at_level(logging.DEBUG, logger="gregtec.api"):
            board.refresh_data()
        assert board.data["db_run"] is None and board.tier is None
        assert caplog.records == []
    finally:
        store.close()
    broken = tmp_path / "broken"
    (broken / "data").mkdir(parents=True)
    (broken / "data" / "gos.sqlite").write_bytes(b"not a database " * 100)
    with caplog.at_level(logging.DEBUG, logger="gregtec.api"):
        dashboard.Board(Config(gos_home=broken)).refresh_data()
    assert [r.getMessage() for r in caplog.records] == [
        "dashboard data refresh skipped"
    ]


def test_pairing_codes_reach_trusted_callers_only(client: TestClient) -> None:
    voice = token(client)
    assert client.post("/api/pair/start", json={"name": "Pixel 9"}).status_code == 200
    trusted = client.get("/api/state", headers=admin()).json()["approvals"]
    assert [a["kind"] for a in trusted] == ["pairing"]
    code = client.get("/api/pair/pending", headers=admin()).json()[0]["code"]
    assert code[:4] in trusted[0]["readback"]
    assert client.get("/api/state", headers=voice).json()["approvals"] == []


def test_questions_being_asked_reach_trusted_callers_only(
    client: TestClient, core: Core
) -> None:
    """As the answers do (/api/answers): clients (voice, Discord, OC) see neither."""
    assert core.board is not None
    entry = core.board.asked("what uses steel")
    asking = client.get("/api/state", headers=admin()).json()["asking"]
    assert [q["text"] for q in asking] == ["what uses steel"]
    assert client.get("/api/state", headers=token(client)).json()["asking"] == []
    core.board.done(entry)


class FakeDeep:
    """The deep lane as the state reads it: its tasks and its health strip line."""

    def __init__(self, tasks: list[deep.DeepTask], health: tuple[str, str]) -> None:
        self._tasks, self._health = tasks, health

    def tasks(self) -> list[deep.DeepTask]:
        return self._tasks

    def health(self) -> tuple[str, str]:
        return self._health


def test_the_deep_lane_alerts_only_what_nothing_else_says(core: Core) -> None:
    def alert_ids() -> list[str]:
        return [a["id"] for a in dashboard.state(core, True)["alerts"]]

    core.key_status = ("ok", "stored")
    offline = ("warn", "offline: LookupError: no Anthropic key")
    core.deep = FakeDeep([], offline)  # type: ignore[assignment]
    assert "health-deep_lane" in alert_ids()
    # The AI is offline: the "AI offline" state says it, so no alert repeats it.
    core.key_status = ("warn", "not stored: gos key set anthropic")
    assert "health-deep_lane" not in alert_ids()
    # A task paused at its cap: its approval card says it, with the countdown.
    core.key_status = ("ok", "stored")
    task = deep.DeepTask("h-1", "plan a steel line", "plan it", "router", 1.0)
    task.status, task.approve_by = deep.NEEDS_APPROVAL, time.time() + 1800
    core.deep = FakeDeep([task], ("warn", "h-1 paused at its cap: approve or stop it"))  # type: ignore[assignment]
    s = dashboard.state(core, True)
    assert "health-deep_lane" not in [a["id"] for a in s["alerts"]]
    assert [(a["id"], a["expires_at"]) for a in s["approvals"]] == [
        ("deep-h-1", int(task.approve_by))
    ]


# --- answers ----------------------------------------------------------------------------------


def test_an_answer_is_kept_and_pushed(client: TestClient) -> None:
    with client.websocket_connect(WS, headers=admin()) as ws:
        kinds = [ws.receive_json()["type"] for _ in range(4)]
        assert kinds == ["hello", "health", "pair.pending", "state"]
        reply = client.post(
            "/api/ask", json={"text": "recipe for steel"}, headers=admin()
        )
        assert reply.status_code == 200
        body = reply.json()
        assert body["card"]["type"] == "recipe" and cards.validate(body["card"]) == []
        pushed = None
        for _ in range(10):
            message = ws.receive_json()
            if message["type"] == "ask.reply":
                pushed = message["data"]
                break
        assert pushed is not None
        assert pushed["request"] == "recipe for steel" and pushed["by"] == "admin"
        assert pushed["text"] == body["text"] and pushed["card"] == body["card"]
    answers = client.get("/api/answers", headers=admin()).json()
    assert [a["request"] for a in answers] == ["recipe for steel"]
    assert (
        answers[0]["id"] == pushed["id"] and answers[0]["asked_ts"] <= answers[0]["ts"]
    )


def test_answers_are_for_trusted_callers(client: TestClient) -> None:
    assert client.get("/api/answers", headers=token(client)).status_code == 403


def test_the_board_keeps_fifty_answers() -> None:
    board = dashboard.Board(Config())
    for n in range(60):
        board.answered(board.asked(f"q{n}"), {"text": f"a{n}"}, "admin")
    kept = board.answers()
    assert len(kept) == 50 and kept[0]["request"] == "q10" and kept[-1]["text"] == "a59"
    assert board.asking() == []


def test_answers_outlive_a_restart(tmp_path: Path) -> None:
    """Chat re-opens any past card (plan 7.14): the newest 50 answers are kept in the DB."""
    db = tmp_path / "gos.sqlite"
    store = Store(db)
    board = dashboard.Board(Config(gos_home=tmp_path), store=store)
    for n in range(55):
        board.answered(board.asked(f"q{n}"), {"text": f"a{n}", "card": None}, "admin")
    assert store.rows("SELECT count(*) AS n FROM answer")[0]["n"] == 50
    store.close()
    store = Store(db)  # the core starts again
    try:
        again = dashboard.Board(Config(gos_home=tmp_path), store=store)
        kept = again.answers()
        assert [a["request"] for a in kept] == [f"q{n}" for n in range(5, 55)]
        assert kept[-1] == board.answers()[-1]
    finally:
        store.close()


def test_kept_answers_that_no_longer_read_are_skipped_or_lose_their_card(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "gos.sqlite")
    try:
        old = {"id": "a1", "ts": 1, "text": "x", "card": {"type": "recipe", "v": 0}}
        with store.transaction() as conn:
            conn.executemany(
                "INSERT INTO answer(id, ts, body) VALUES (?, ?, ?)",
                [("a0", 0, "{not json"), ("a1", 1, json.dumps(old)), ("a2", 2, "[]")],
            )
        kept = dashboard.Board(Config(gos_home=tmp_path), store=store).answers()
        assert kept == [
            {**old, "card": None}
        ]  # a card from an older catalog: not drawn
    finally:
        store.close()


def test_an_answer_that_cant_be_kept_is_still_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "gos.sqlite")
    try:
        board = dashboard.Board(Config(gos_home=tmp_path), store=store)

        def locked(*_: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(store, "transaction", locked)
        answer = board.answered(board.asked("q"), {"text": "a"}, "admin")
        assert board.answers() == [answer]
    finally:
        store.close()


def test_a_question_never_stays_asked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2: whatever fails after a question starts, it stops showing as being asked."""

    async def failing(core: Core) -> None:
        raise RuntimeError("the push failed")

    monkeypatch.setattr(dashboard, "push_state", failing)
    with pytest.raises(RuntimeError):
        client.post("/api/ask", json={"text": "recipe for steel"}, headers=admin())
    assert client.get("/api/state", headers=admin()).json()["asking"] == []


def test_a_card_outside_the_catalog_is_dropped_from_a_reply() -> None:
    assert dashboard.checked_reply({"card": {"type": "html"}})["card"] is None
    good = {
        "card": {"type": "choices", "subject": "s", "question": "q", "candidates": []}
    }
    assert dashboard.checked_reply(good) == good


# --- recipe cards and lookup tools ------------------------------------------------------------


def test_a_citation_opens_its_recipe_card(client: TestClient) -> None:
    card = client.get(f"/api/recipe?ref={EBF}", headers=admin()).json()
    assert cards.validate(card) == []
    assert card["title"] == f"Blast Furnace · {EBF}"
    recipe = card["recipes"][0]
    assert (recipe["ref"], recipe["tier"], recipe["eut"], recipe["dur_t"], recipe["dur_s"]) == (
        EBF, "MV", 120, 500, "25")  # fmt: skip
    oxygen = next(s for s in recipe["inputs"] if s["key"] == "fluid:oxygen")
    assert oxygen["fluid"] and oxygen["amount"] == 1000
    ashes = next(s for s in recipe["outputs"] if s["name"] == "Ashes")
    assert ashes["chance_pct"] == "11.11"
    assert recipe["conditions"] == [{"key": "coil_heat", "value": "1000"}]
    assert client.get("/api/recipe?ref=q:AAAA", headers=admin()).status_code == 400
    missing = client.get(
        "/api/recipe?ref=r:gt.recipe.blastfurnace/000000000000", headers=admin()
    )
    assert missing.status_code == 404


def tool(client: TestClient, name: str, **args: object) -> dict[str, Any]:
    r = client.post(f"/api/tools/{name}", json={"args": args}, headers=admin())
    assert r.status_code == 200, r.text
    card: dict[str, Any] = r.json()["card"]
    assert cards.validate(card) == []
    return card


def test_recipe_find_is_a_structured_recipe_card(client: TestClient) -> None:
    card = tool(client, "recipe_find", item="steel")
    assert card["type"] == "recipe" and card["tool"] == "recipe_find"
    assert card["item"] == {
        "key": "gregtech:gt.metaitem.01@11305",
        "name": "Steel Ingot",
    }
    refs = [r["ref"] for r in card["recipes"]]
    assert len(refs) == card["total"] == 5 and EBF in refs
    flags = {r["ref"]: r["flags"] for r in card["recipes"]}
    assert flags["r:test.nei.AlloyKilnHandler/07e41a118037"] == ["fake?"]
    assert flags["r:gt.recipe.arcfurnace/e51b48c38b9b"] == ["recycling"]
    circuit = next(
        r
        for r in card["recipes"]
        if r["ref"] == "r:gt.recipe.blastfurnace/cd313bfc2e4d"
    )
    programmed = next(s for s in circuit["inputs"] if s["circuit"] is not None)
    assert programmed["circuit"] == 1 and programmed["consumed"] is False


def test_quest_tools_are_quest_cards_nudge_first(client: TestClient) -> None:
    frontier = tool(client, "quest_frontier", n=3)
    assert frontier["type"] == "quest" and frontier["title"] == "Frontier"
    assert frontier["tier"].startswith("stone")
    found = tool(client, "quest_find", text="steel")
    hidden = [q for q in found["quests"] if not q["shown"]]
    assert hidden and all(
        q["name"] is None for q in hidden
    )  # D26: the book doesn't show it
    nudge = tool(client, "quest_get", id=hidden[0]["ref"], spoil=0)
    assert nudge["spoil"] == 0 and nudge["quests"][0]["name"] is None
    assert {r["part"] for r in nudge["quests"][0]["rows"]} <= {"prereq", "start"}
    spoiled = tool(client, "quest_get", id=hidden[0]["ref"], spoil=2)
    assert spoiled["quests"][0]["name"] is not None


def test_other_tools_are_tables_and_errors_say_why(client: TestClient) -> None:
    found = tool(client, "item_resolve", text="steel")
    assert found["type"] == "table" and "key" in found["columns"]
    bad = tool(client, "recipe_find", nope=1)
    assert bad["type"] == "table" and "unknown argument" in (bad["error"] or "")


@pytest.mark.parametrize("name", ["handoff", "db_sql", "note_write", "more", "nope"])
def test_only_the_lookup_tools_run(client: TestClient, name: str) -> None:
    """handoff starts paid deep-lane work; the note tools write; db_sql is the db-analyst's."""
    r = client.post(f"/api/tools/{name}", json={"args": {}}, headers=admin())
    assert r.status_code == 404


def test_tool_bodies_are_json_objects(client: TestClient) -> None:
    r = client.post("/api/tools/item_resolve", content="args=1", headers=admin())
    assert r.status_code == 415
    r = client.post("/api/tools/item_resolve", json={"args": [1]}, headers=admin())
    assert r.status_code == 400


# --- VERIFY and doctor ------------------------------------------------------------------------


def test_the_verify_list_with_doctors_statuses(client: TestClient, core: Core) -> None:
    env = {
        "generated": "2026-11-07T10:00:00+00:00",
        "verify": {"V03": "open: Export run (WO-1.2)"},
    }
    (core.cfg.gos_home / "data" / "env.json").write_text(json.dumps(env), "utf-8")
    v = client.get("/api/verify", headers=admin()).json()
    assert v["generated"] == env["generated"]
    by_id = {i["id"]: i for i in v["items"]}
    assert by_id["V03"]["status"] == "open: Export run (WO-1.2)"
    assert by_id["V01"]["status"] is None and len(by_id) >= 33


def test_doctor_runs_for_trusted_callers_one_at_a_time(
    client: TestClient, core: Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path | None] = []

    def fake_doctor(
        roots: object, home: Path | None, hash_mods: bool, out: StringIO
    ) -> int:
        calls.append(home)
        print("Prism (portable): C:\\Games\\PrismLauncher", file=out)
        return 0

    monkeypatch.setattr(dashboard.doctor, "main", fake_doctor)
    assert client.post("/api/doctor", headers=token(client)).status_code == 403
    r = client.post("/api/doctor", headers=admin())
    assert r.json() == {
        "exit": 0,
        "output": "Prism (portable): C:\\Games\\PrismLauncher\n",
    }
    assert calls == [core.cfg.gos_home]
    assert core.board is not None
    with core.board.doctor_lock:
        assert client.post("/api/doctor", headers=admin()).status_code == 409


# --- pushes -----------------------------------------------------------------------------------


def test_tick_pushes_the_state_when_it_changes(core: Core) -> None:
    trusted = Socket(Principal("admin", "admin"), asyncio.Queue())
    client = Socket(Principal("client", "voice"), asyncio.Queue())
    core.sockets.update({trusted, client})
    core.pairing.start("Pixel 9")

    async def run() -> None:
        await dashboard.tick(core)
        first_t, first_c = trusted.queue.get_nowait(), client.queue.get_nowait()
        assert first_t is not None and first_c is not None
        assert json.loads(first_t)["data"]["approvals"][0]["kind"] == "pairing"
        assert json.loads(first_c)["data"]["approvals"] == []
        core.started -= 5  # uptimes move on their own: not a change worth a push
        await dashboard.tick(core)  # nothing changed, heartbeat not due
        assert trusted.queue.empty()
        assert core.board is not None
        core.board.next_focus = 0.0
        core.board.foreground = lambda: "chrome.exe"
        await dashboard.tick(core)
        changed = trusted.queue.get_nowait()
        assert (
            changed is not None
            and json.loads(changed)["data"]["game"]["focused"] is False
        )

    asyncio.run(run())


def test_a_socket_that_joins_while_the_state_is_built_gets_it(
    core: Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2: a client socket accepted while push_state waits on its thread got a KeyError."""
    trusted = Socket(Principal("admin", "admin"), asyncio.Queue())
    late = Socket(Principal("client", "voice"), asyncio.Queue())
    core.sockets.add(trusted)
    core.pairing.start("Pixel 9")
    built = dashboard.snapshots

    def joining(c: Core) -> dict[bool, dict[str, Any]]:
        core.sockets.add(late)  # the voice client connects meanwhile
        return built(c)

    monkeypatch.setattr(dashboard, "snapshots", joining)
    asyncio.run(dashboard.push_state(core))
    to_trusted, to_late = trusted.queue.get_nowait(), late.queue.get_nowait()
    assert to_trusted is not None and to_late is not None
    assert [a["kind"] for a in json.loads(to_trusted)["data"]["approvals"]] == [
        "pairing"
    ]
    assert json.loads(to_late)["data"]["approvals"] == []  # no pairing code for clients


def test_a_failed_state_push_is_logged_never_raised(
    core: Core, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    core.sockets.add(Socket(Principal("admin", "admin"), asyncio.Queue()))

    def broken(c: Core) -> dict[bool, dict[str, Any]]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(dashboard, "snapshots", broken)
    with caplog.at_level(logging.ERROR, logger="gregtec.api"):
        asyncio.run(dashboard.push_state(core))
    assert "state push failed" in caplog.text
