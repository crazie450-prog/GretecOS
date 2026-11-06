"""Quest ingest (WO-1.5) on GTNH 2.9.0's real quest book.

The pack's DefaultQuests is CC BY-NC-SA game data, so it is never in the repo: this runs only
with ``GOS_QUESTS_PACK`` set to the game folder (``.minecraft``) of an extracted GTNH 2.9.0
pack, e.g. Play's own (read-only) or the zip's ``GT New Horizons 2.9.0/.minecraft`` (only its
``config/betterquesting/`` is read). It checks, on the real book:

- the split DefaultQuests parses with no problem, and its world copy (written here into
  tmp_path as BetterQuesting's ``saveConfig`` writes ``QuestDatabase.json``) reads the same;
- every key quest in ``data/curated/tier_quests.tsv`` is in the book under its curated name,
  and each stage's key quest needs the stage before it (its prerequisite closure holds one);
- the tier and the frontier at every stage: with everything a key quest needs done, the tier
  is the stage before and the key quest is on the frontier; with the key quest done too, the
  tier is its stage; a new world's frontier is the first quest alone;
- the named frontier query gives the same list as :func:`gregtec.ingest.quests.frontier`.

Nothing is written outside tmp_path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.ingest import quests as bq

PLAYER = "11111111-2222-4333-8444-555555555555"


@pytest.fixture(scope="module")
def book() -> bq.Book:
    game = os.environ.get("GOS_QUESTS_PACK")
    if not game:
        pytest.skip(
            "set GOS_QUESTS_PACK to an extracted GTNH 2.9.0 pack's .minecraft folder"
        )
    defaults = Path(game) / bq.DEFAULTS
    if not (defaults / bq.SETTINGS_FILE).is_file():
        pytest.fail(f"no DefaultQuests in {defaults}")
    return bq.read_default_book(defaults)


@pytest.fixture(scope="module")
def keys() -> list[bq.TierKey]:
    return bq.read_tier_keys(quest_db.TIER_KEYS)


def closure(book: bq.Book, quest: str) -> set[str]:
    seen: set[str] = set()
    todo = [quest]
    while todo:
        for p in book.quests[todo.pop()].prereqs:
            if p in book.quests and p not in seen:
                seen.add(p)
                todo.append(p)
    return seen


def frontier_of(book: bq.Book, done: set[str]) -> list[str]:
    st = bq.states(book, {PLAYER: {q: bq.Completion(1, True) for q in done}}, PLAYER)
    return [q.id for _, _, q in bq.frontier(book, st)]


def test_the_book_parses(book: bq.Book) -> None:
    assert book.problems == []
    assert book.pack_name == "GT-New-Horizons" and book.format == "3.1.0"
    if book.pack_version == 2188:  # GTNH 2.9.0 [B§1.1]
        assert (len(book.quests), len(book.lines)) == (3814, 46)
    names = [ln.name for ln in book.lines]
    assert names[:3] == ["And So, It Begins", "Tips and Tricks", "Tier 0 - Stone Age"]
    # A prerequisite missing from the book counts as never done (BQ's isUnlocked). 2.9.0 has
    # three, all on OR quests whose other prerequisites can still unlock them.
    dangling = [
        q for q in book.quests.values() if any(p not in book.quests for p in q.prereqs)
    ]
    assert all(q.logic == "OR" for q in dangling) and len(dangling) <= 10
    # Items are keyed by registry name, never by a numeric ID.
    stacks = [s for q in book.quests.values() for t in q.tasks for s in t.stacks]
    assert all(s.registry_name or s.fluid for s in stacks)
    assert not any(s.registry_name and s.registry_name.isdigit() for s in stacks)


def _world_copy(book: bq.Book, path: Path) -> None:
    """``QuestDatabase.json`` as BQ's saveConfig writes it from the loaded defaults."""

    def entry(e: bq.Entry) -> dict[str, Any]:
        high, low = bq.decode_id(e.quest)
        return {
            "questIDHigh:4": high,
            "questIDLow:4": low,
            "sizeX:3": e.size_x,
            "sizeY:3": e.size_y,
            "x:3": e.x,
            "y:3": e.y,
        }

    lines = []
    for ln in book.lines:
        high, low = bq.decode_id(ln.id)
        lines.append(
            {
                "order:3": ln.ord,
                "properties:10": {
                    "betterquesting:10": {
                        "desc:8": ln.descr,
                        "name:8": ln.name,
                        "visibility:8": ln.visibility,
                    }
                },
                "questLineIDHigh:4": high,
                "questLineIDLow:4": low,
                "quests:9": {f"{i}:10": entry(e) for i, e in enumerate(ln.entries)},
            }
        )
    db = {
        "build:8": "3.8.92-GTNH",
        "format:8": "3.1.0",
        "questDatabase:9": {
            f"{i}:10": q.raw for i, q in enumerate(book.quests.values())
        },
        "questLines:9": {f"{i}:10": v for i, v in enumerate(lines)},
        "questSettings:10": {
            "betterquesting:10": {
                "pack_name:8": book.pack_name,
                "pack_version:3": book.pack_version,
            }
        },
    }
    path.parent.mkdir(parents=True)
    # Floats keep Java's text in the file BQ writes; repr gives the same for these values.
    path.write_text(json.dumps(db), encoding="utf-8")


def test_the_world_copy_reads_the_same(book: bq.Book, tmp_path: Path) -> None:
    path = tmp_path / "world" / bq.WORLD_DIR / bq.DATABASE
    _world_copy(book, path)
    world = bq.read_world_book(path)
    assert world.problems == []
    assert world.quests.keys() == book.quests.keys()
    for qid, q in book.quests.items():
        w = world.quests[qid]
        assert (w.name, w.logic, w.visibility, w.prereqs, w.tasks) == (
            q.name,
            q.logic,
            q.visibility,
            q.prereqs,
            q.tasks,
        )
    assert [(ln.id, ln.name, ln.entries) for ln in world.lines] == [
        (ln.id, ln.name, ln.entries) for ln in book.lines
    ]


def test_the_curated_key_quests(book: bq.Book, keys: list[bq.TierKey]) -> None:
    before: list[str] = []
    for stage in dict.fromkeys(k.stage for k in keys):
        mine = [k for k in keys if k.stage == stage]
        for k in mine:
            assert k.quest in book.quests, f"{k.stage}: q:{k.quest} is not in the book"
            assert book.quests[k.quest].name == k.name
            if before:
                assert closure(book, k.quest) & set(before), f"{k.name} skips a stage"
        before = [k.quest for k in mine]


def test_tier_and_frontier_at_every_stage(
    book: bq.Book, keys: list[bq.TierKey]
) -> None:
    assert frontier_of(book, set()) == [bq.encode_id(0, 0)]  # the first quest alone
    assert bq.detect_tier(keys, []).stage == "stone"
    stages = list(bq.STAGES)
    seen: set[str] = set()
    for k in keys:
        if k.stage in seen:
            continue  # the first key quest of each stage
        seen.add(k.stage)
        need = closure(book, k.quest)
        below = bq.detect_tier(keys, need)
        assert stages.index(below.stage) == stages.index(k.stage) - 1, k.name
        assert k.quest in frontier_of(book, need), f"{k.name} is not on the frontier"
        assert bq.detect_tier(keys, need | {k.quest}).stage == k.stage


def by_command(
    book: bq.Book, quests: list[str]
) -> tuple[dict[str, bq.Completion], dict[str, set[bq.TaskKey]]]:
    """Progress after ``/bq_admin complete`` on each quest (BQ's NetQuestEdit.completeQuests):
    unclaimed, timestamp 0, and its tasks marked done in order until the task logic holds."""
    done = {q: bq.Completion(0, False) for q in quests}
    tasks: dict[str, set[bq.TaskKey]] = {}
    for qid in quests:
        q = book.quests[qid]
        marked: set[bq.TaskKey] = set()
        if not bq.logic_result(q.task_logic, 0, len(q.tasks)):
            for t in q.tasks:
                marked.add((t.idx, t.kind))
                if bq.logic_result(q.task_logic, len(marked), len(q.tasks)):
                    break
        tasks[qid] = marked
    return done, tasks


def test_the_runbook_states(book: bq.Book, keys: list[bq.TierKey]) -> None:
    """docs/runbooks/quests_check.md's expected output, state by state, on 2.9.0's book."""
    if book.pack_version != 2188:
        pytest.skip("the runbook's numbers are 2.9.0's (pack_version 2188)")
    lv, hv = "AAAAAAAAAAAAAAAAAAAARg", "AAAAAAAAAAAAAAAAAAAAoA"
    kitchenware, chef = "AAAAAAAAAAAAAAAAAAACmQ", "AAAAAAAAAAAAAAAAAAADWg"

    def front(
        done: dict[str, bq.Completion], tasks: dict[str, set[bq.TaskKey]]
    ) -> tuple[list[tuple[str, str, str]], dict[str, bq.State]]:
        st = bq.states(book, {PLAYER: done}, PLAYER, {PLAYER: tasks})
        return [
            (ln.name, q.name, st[q.id].state) for ln, _, q in bq.frontier(book, st)
        ], st

    rows, _ = front({}, {})
    assert rows == [("And So, It Begins", "Your First Night", "available")]
    rows, st = front(*by_command(book, [lv]))
    assert (len(rows), len({r[1] for r in rows})) == (6, 4)
    in_lv = [r[1] for r in rows if r[0] == "Tier 1 - LV"]
    assert len(in_lv) == 2 and in_lv[0] == "Welcome to Tier 1, LV"
    assert [r[1] for r in rows if r[0] == "Tier 0.5 - Steam"] == ["Safety First"]
    done = [q for q, s in st.items() if s.state in bq.COMPLETE]
    assert bq.detect_tier(keys, done).stage == "LV"
    rows, st = front(*by_command(book, [lv, hv]))
    assert len({r[1] for r in rows}) == 17
    assert len([r for r in rows if r[0] == "Tier 3 - HV"]) == 4
    done = [q for q, s in st.items() if s.state in bq.COMPLETE]
    assert bq.detect_tier(keys, done).stage == "HV"
    # Step 5: Chef Training repeats as soon as it is claimed; once BQ resets it (claimed
    # false, timestamp 0, tasks cleared), it is on the frontier again.
    assert book.quests[chef].repeat_time == 0 and book.quests[chef].rewards
    done, tasks = by_command(book, [lv, hv, kitchenware, chef])
    rows, st = front(done, tasks)
    assert st[chef].state == "done"  # completed by command: its tasks are done
    tasks[chef] = set()
    rows, st = front(done, tasks)
    assert [s for s in st.values() if s.state == bq.REPEATABLE] == [st[chef]]
    assert ("Feeding Yourself", "Chef Training", "repeatable") in rows
    assert len({r[1] for r in rows}) == 21
    # Step 6: everything completed by command leaves nothing open, repeatable ones included.
    rows, st = front(*by_command(book, list(book.quests)))
    assert rows == [] and bq.detect_tier(keys, list(st)).stage == "UXV"
    # D113's numbers: 93 repeatable quests, all with rewards and in a chapter.
    repeatable = [q for q in book.quests.values() if q.repeat_time >= 0]
    placed = {e.quest for ln in book.lines for e in ln.entries}
    assert len(repeatable) == 93
    assert all(q.rewards and q.id in placed for q in repeatable)


def test_the_named_query_gives_the_same_frontier(
    book: bq.Book, keys: list[bq.TierKey], tmp_path: Path
) -> None:
    conn = access.connect(tmp_path / "gos.sqlite")
    try:
        for k in keys[3:6]:  # MV, HV and EV
            done = closure(book, k.quest)
            progress = bq.Progress(
                {PLAYER: {q: bq.Completion(1, True) for q in done}}, {PLAYER: "Clayton"}
            )
            st = bq.states(book, progress.done, PLAYER)
            loaded = quest_db.Loaded(
                book=book,
                progress=progress,
                player=PLAYER,
                states=st,
                tier=bq.detect_tier(keys, done),
                keys=keys,
                world=tmp_path,
            )
            summary = quest_db.write(conn, loaded)
            rows = [r[2] for r in quest_db.frontier_rows(conn)]
            assert rows == frontier_of(book, done)
            assert summary.frontier == len(set(rows)) > 0
    finally:
        conn.close()


def test_quest_items_link_to_a_harness_export(
    book: bq.Book, keys: list[bq.TierKey], tmp_path: Path
) -> None:
    """With GOS_REQUIRE_HARNESS_RUN=1 too (after ``./gradlew runServer -Pharness``, as in
    test_db_harness.py; ``GOS_SERVER_RUN`` names another server run folder): the harness's
    recipe DB, then the real book loaded into it. Every GT machine a quest names that the
    export has gets its item, and so do the key quests' machine hulls."""
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the server harness")
    from gregtec.db import build
    from gregtec.ingest import collect

    repo = Path(__file__).resolve().parents[2]
    game = Path(os.environ.get("GOS_SERVER_RUN") or repo / "mod" / "run" / "server")
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path / "home"
    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    loaded = quest_db.Loaded(
        book=book,
        progress=bq.Progress({}, {PLAYER: "Clayton"}),
        player=PLAYER,
        states=bq.states(book, {}, PLAYER),
        tier=bq.detect_tier(keys, []),
        keys=keys,
        world=tmp_path,
    )
    conn = access.connect(access.db_path(home))
    conn.row_factory = None
    try:
        summary = quest_db.write(conn, loaded)
        assert summary.linked > 0
        machines = conn.execute(
            "SELECT count(*), count(i.item_id) FROM quest_item AS i JOIN machine AS m"
            " ON m.mte_id = i.meta WHERE i.registry_name = 'gregtech:gt.blockmachines'"
            " AND i.nbt_hash = ''"
        ).fetchone()
        assert machines[0] > 0 and machines[0] == machines[1]
        hulls = dict(
            conn.execute(
                "SELECT t.quest_id, m.name_en FROM quest_task AS t"
                " JOIN machine AS m ON m.item_id = t.item_id"
            ).fetchall()
        )
        for k in keys:
            if k.stage not in ("steam", "LV"):
                assert hulls.get(k.quest, "").endswith("Machine Hull"), k.name
    finally:
        conn.close()
