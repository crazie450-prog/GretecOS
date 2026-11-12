"""The quest guide (WO-6.4) over a real harness recipe DB.

The quest pack (tests/evals/quest_pack.py: a self-authored book, never pack data) is loaded into
the DB that ``gos export collect`` + ``gos db build`` make from the server harness's newest run
(GT 5.09.54.265 and its dependencies), instead of the tool tests' made-up run. It checks that
the quest book's items link to the real export's items by registry name + meta (never numeric
IDs), that the Quests view's payload at L0 holds none of a locked quest's spoiler fields, and
that the 20-question eval passes with the adversarial fake model (it asks for full spoilers
every time) on the real DB's item names.

Runs only with GOS_REQUIRE_HARNESS_RUN=1 after ``cd mod && ./gradlew runServer -Pharness``
(mod/README.md); ``GOS_SERVER_RUN`` names another server run folder. Nothing is written outside
tmp_path.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.quests import questeval, view

REPO = Path(__file__).resolve().parents[2]
PACK = questeval.pack()
GT = "gregtech:gt.metaitem.01"


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the server harness")
    game = Path(os.environ.get("GOS_SERVER_RUN") or REPO / "mod" / "run" / "server")
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")

    def export(h: Path) -> None:
        collect.collect(game, h, 1, allow_harness=True)
        build.build(h, allow_harness=True)

    h = tmp_path_factory.mktemp("quest-guide") / "home"
    PACK.build(h, export=export)
    yield h


def test_quest_items_link_to_the_real_export(home: Path) -> None:
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        linked = dict(
            conn.execute(
                "SELECT qi.registry_name || '@' || qi.meta, i.name_en FROM quest_item AS qi"
                " JOIN item AS i ON i.id = qi.item_id WHERE qi.registry_name = ?",
                (GT,),
            ).fetchall()
        )
    finally:
        conn.close()
    # Steel ingot, steel dust, stainless steel dust: GT's own items, in every GT export
    for key in (f"{GT}@11305", f"{GT}@2305", f"{GT}@2306"):
        assert linked.get(key), (key, sorted(linked))


def test_l0_never_spoils_the_view_payload(home: Path) -> None:
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        fields = questeval.spoiler_fields(conn)
    finally:
        conn.close()
    assert fields
    payload = view.read(home, None, 0)
    assert payload["spoil"] == 0
    # his goals rank first: Bricks leads to the EBF, his first goal
    assert payload["frontier"]["quests"][0]["ref"] == f"q:{PACK.qid(11)}"
    text = json.dumps(payload, ensure_ascii=False)
    assert questeval.leaks(text, fields, said=list(PACK.GOALS)) == []


def test_the_eval_passes_with_the_adversarial_fake(home: Path) -> None:
    qs = questeval.load()
    out = io.StringIO()
    code = questeval.run(
        questeval.fake_asker(home, qs), qs, access.db_path(home), out, fake_model=True
    )
    assert code == 0, out.getvalue()
    assert "passed: 20 of 20" in out.getvalue() and "spoiled: 0" in out.getvalue()


def test_l0_never_spoils_the_real_book(tmp_path: Path) -> None:
    """With GOS_QUESTS_PACK set (an extracted GTNH 2.9.0 pack's ``.minecraft``, as in
    test_quests_pack.py; no harness run needed): GTNH's real book over the made-up export, at
    the first key quest of each stage with everything it needs done. At L0 the view's payload
    holds none of any locked quest's spoiler fields, and at L2 it lists the quests past the
    frontier."""
    game = os.environ.get("GOS_QUESTS_PACK")
    if not game:
        pytest.skip(
            "set GOS_QUESTS_PACK to an extracted GTNH 2.9.0 pack's .minecraft folder"
        )
    from gregtec.db import quests as quest_db
    from gregtec.ingest import quests as bq

    book = bq.read_default_book(Path(game) / bq.DEFAULTS)
    keys = bq.read_tier_keys(quest_db.TIER_KEYS)
    player = PACK.CLAYTON
    stages: dict[str, str] = {}
    for k in keys:
        if k.quest in book.quests:
            stages.setdefault(k.stage, k.quest)
    assert stages
    for n, quest in enumerate(stages.values()):
        need: set[str] = set()
        todo = [quest]
        while todo:
            for p in book.quests[todo.pop()].prereqs:
                if p in book.quests and p not in need:
                    need.add(p)
                    todo.append(p)
        done = {player: {q: bq.Completion(1_700_000_000_000, True) for q in need}}
        home = tmp_path / f"home{n}"
        PACK.tools().make_run(home)
        build.build(home)
        conn = access.connect(access.db_path(home))
        try:
            quest_db.write(
                conn,
                quest_db.Loaded(
                    book=book,
                    progress=bq.Progress(done, {player: "Clayton"}),
                    player=player,
                    states=bq.states(book, done, player),
                    tier=bq.detect_tier(keys, need),
                    keys=keys,
                    world=tmp_path,
                ),
            )
            fields = questeval.spoiler_fields(conn)
        finally:
            conn.close()
        nudge = json.dumps(view.read(home, None, 0), ensure_ascii=False)
        assert questeval.leaks(nudge, fields) == [], quest
        assert view.read(home, 2, 0)["beyond"]["quests"], quest
