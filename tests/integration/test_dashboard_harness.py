"""WO-2.6 on a DB built from a real GregtecLink harness run: the cards the real data gives fit the
catalog (D160).

A card that fails the catalog check is dropped (and logged) before it leaves the core, so it would
never reach monitor 2: a schema the real data doesn't fit has to fail here, not on Clayton's PC.
Checked: recipe cards for the first recipes of every recipe map and NEI handler of the run (every
machine kind, crafting and furnace included), the cards the lookup tools give Search and the
Ctrl+K palette for the fast-lane question set's items, every reply card of that question set (fake
model), and a core's state snapshot on that DB. As test_fast_lane_harness.py: only with
GOS_REQUIRE_HARNESS_RUN=1 after the client harness. Nothing is written outside tmp_path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gregtec import cards
from gregtec.agents import evals
from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.api import dashboard
from gregtec.api.app import Core
from gregtec.api.security import Auth
from gregtec.cards import build as cards_build
from gregtec.config import Config
from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.llm.fake import EchoClient
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

REPO = Path(__file__).resolve().parents[2]
PER_SOURCE = 25  # recipes drawn from each recipe map and each NEI handler
SAMPLE = """
SELECT ref FROM (
  SELECT r.ref, row_number() OVER (
    PARTITION BY r.recipe_map_id, r.nei_handler_id ORDER BY r.id) AS n
  FROM recipe AS r WHERE r.run_id = :run_id)
WHERE n <= :per
"""


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("dashboard-harness")
    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    return home


@pytest.fixture(scope="module")
def db(home: Path) -> Path:
    return access.db_path(home)


@pytest.fixture(scope="module")
def registry(db: Path) -> Registry:
    return build_registry(db, Glossary.find(None), deep=HandoffRecorder())


def problems_of(found: list[dict[str, object]]) -> list[str]:
    out = []
    for card in found:
        out += [f"{card.get('title')}: {p}" for p in cards.validate(card)[:3]]
    return out


def test_recipe_cards_of_every_map_and_handler_fit_the_catalog(db: Path) -> None:
    conn = access.connect(db, readonly=True)
    try:
        run = access.query(conn, "current_run")[0]
        refs = [
            r["ref"]
            for r in conn.execute(SAMPLE, {"run_id": run["id"], "per": PER_SOURCE})
        ]
        sources = conn.execute(
            "SELECT count(DISTINCT coalesce(recipe_map_id, -1) || ':' || "
            "coalesce(nei_handler_id, -1)) FROM recipe WHERE run_id = ?",
            (run["id"],),
        ).fetchone()[0]
    finally:
        conn.close()
    assert sources > 100, "a client harness run has every GT map and NEI handler"
    found = []
    for start in range(0, len(refs), 50):  # a card holds at most 50 recipes
        chunk = refs[start : start + 50]
        card = cards_build.recipe_card(db, chunk, title="sample", tool="recipe_find")
        assert [r["ref"] for r in card["recipes"]] == chunk  # each one, in order
        found.append(card)
    assert problems_of(found) == []
    recipes = [r for card in found for r in card["recipes"]]
    assert {r["tier"] for r in recipes} >= {"ULV", "LV", "MV", "HV"}
    assert any(s["fluid"] for r in recipes for s in r["inputs"] + r["outputs"])
    assert any(s["chance_pct"] for r in recipes for s in r["outputs"])
    assert any(s["circuit"] is not None for r in recipes for s in r["inputs"])
    assert any("recycling" in r["flags"] for r in recipes)


def test_the_lookup_tools_give_catalog_cards(registry: Registry, db: Path) -> None:
    """What Search and the Ctrl+K palette run (POST /api/tools), for the question set's items."""
    keys = sorted({q.key for q in evals.load() if q.key})
    found = []
    for key in keys:
        calls = [("recipe_find", {"item": key}), ("recipe_uses", {"item": key})]
        if key.startswith("gregtech:gt.blockmachines@"):
            calls.append(("machine_info", {"item": key}))
        for name, args in calls:
            out = registry.call(name, args, lane="fast")
            found.append(cards_build.from_tool(name, out.text, out.is_error, db))
    for text in ("steel", "electric blast furnace", "oxygen", "circuit"):
        out = registry.call("item_resolve", {"text": text}, lane="fast")
        found.append(cards_build.from_tool("item_resolve", out.text, out.is_error, db))
    assert problems_of(found) == []
    recipe_cards = [c for c in found if c["type"] == "recipe"]
    assert len(recipe_cards) >= len(
        keys
    )  # recipe_find and recipe_uses are recipe cards
    assert any(c["recipes"] for c in recipe_cards)


def test_every_reply_card_of_the_question_set_fits_the_catalog(
    registry: Registry, tmp_path: Path
) -> None:
    store = Store(tmp_path / "usage.sqlite")
    try:
        meter = UsageMeter(store, 100.0, (80, 100))
        dropped, with_card = [], 0
        for q in evals.load():  # a fresh asker per row, as the eval asks them
            deep = HandoffRecorder()
            asker = Asker(registry, deep, FastLane(EchoClient(), registry, meter=meter))
            answer = asker.ask(q.question).as_dict()
            original = answer["card"]
            if original is not None:
                with_card += 1
                if dashboard.checked_reply(answer)["card"] is None:
                    dropped.append(f"{q.id}: {cards.validate(original)[:3]}")
    finally:
        store.close()
    assert dropped == []
    assert with_card >= 40  # every recipe, item, quest and ambiguous row has a card


def test_the_state_snapshot_on_the_real_db(home: Path, registry: Registry) -> None:
    store = Store(access.db_path(home))
    try:
        cfg = Config(gos_home=home)
        core = Core(
            cfg=cfg,
            store=store,
            auth=Auth(store, "admin-" + "a" * 40),
            meter=UsageMeter(store, 100.0, (80, 100)),
            board=dashboard.Board(
                cfg, foreground=lambda: "javaw.exe", clients=lambda cfg: ["play"]
            ),
        )
        board = dashboard.board_of(core)
        board.refresh_focus()
        board.refresh_game()
        board.refresh_data()
        state = dashboard.state(core, True)
    finally:
        store.close()
    assert state["game"] == {
        "running": ["play"],
        "focused": True,
        "paused": None,
        "export": None,
        "error": None,
    }
    assert state["data"]["db_run"]["run_key"]  # the harness run the DB was built from
    assert state["data"]["stale"] is None  # no fingerprint recorded here (WO-1.4)
    assert state["health"] is not None and cards.validate(state["health"]) == []
    for card in state["alerts"] + state["approvals"]:
        assert cards.validate(card) == []
