"""Quest ingest, progress, frontier and tier (WO-1.5) on a small self-authored quest book.

The book is BetterQuesting-shaped (typed JSON keys, ids as two longs, lists as ``"0:10"``
objects; the world's single QuestDatabase.json and the pack's split DefaultQuests) but every
quest, line, item and player in it is made up here: never pack data. It exercises each rule of
gregtec.ingest.quests: the quest logics, the seven visibilities, quest lines that are hidden or
shown once completed, quests in two lines or in none, a prerequisite missing from the book, a
global quest, and items with NBT, oredict names and fluids. Three progression states (a new
world, early LV, HV) check the frontier and the tier (WO-1.5's acceptance, in shape).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from gregtec import cli
from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.gamestate import safe_open
from gregtec.ingest import normalize
from gregtec.ingest import quests as bq

CLAYTON = "11111111-2222-4333-8444-555555555555"
FRIEND = "66666666-7777-4888-9999-aaaaaaaaaaaa"
# A quest with a random UUID id (not a legacy small one), as newer GTNH quests have.
BIG = (0x0F4E3C2A1B2C4D5E, -0x7F6F5E4D3C2B1A10)

Ident = int | tuple[int, int]


def ident(n: Ident, field: str = "questID") -> dict[str, int]:
    high, low = n if isinstance(n, tuple) else (0, n)
    return {f"{field}High:4": high, f"{field}Low:4": low}


def qid(n: Ident) -> str:
    high, low = n if isinstance(n, tuple) else (0, n)
    return bq.encode_id(high, low)


def typed_list(values: Iterable[Any], tag: int = 10) -> dict[str, Any]:
    return {f"{i}:{tag}": v for i, v in enumerate(values)}


def stack(
    item: str,
    meta: int = 0,
    count: int = 1,
    ore: str = "",
    tag: dict[str, Any] | None = None,
) -> dict[str, Any]:
    s: dict[str, Any] = {
        "Count:3": count,
        "Damage:2": meta,
        "OreDict:8": ore,
        "id:8": item,
    }
    if tag is not None:
        s["tag:10"] = tag
    return s


def retrieval(
    *stacks: dict[str, Any], consume: bool = False, ignore_nbt: bool = False
) -> dict[str, Any]:
    return {
        "autoConsume:1": 0,
        "consume:1": int(consume),
        "ignoreNBT:1": int(ignore_nbt),
        "index:3": 0,
        "partialMatch:1": 1,
        "requiredItems:9": typed_list(stacks),
        "taskID:8": "bq_standard:retrieval",
    }


CHECKBOX = {"index:3": 0, "taskID:8": "bq_standard:checkbox"}


def quest(
    n: Ident,
    name: str,
    prereqs: Sequence[Ident] = (),
    *,
    logic: str = "AND",
    vis: str = "NORMAL",
    main: bool = False,
    is_global: bool = False,
    tasks: Sequence[dict[str, Any]] = (CHECKBOX,),
    rewards: Sequence[dict[str, Any]] = (),
    desc: str = "",
) -> dict[str, Any]:
    return {
        "preRequisites:9": typed_list(ident(p) for p in prereqs),
        "properties:10": {
            "betterquesting:10": {
                "desc:8": desc,
                "isGlobal:1": int(is_global),
                "isMain:1": int(main),
                "name:8": name,
                "questLogic:8": logic,
                "repeatTime:3": -1,
                "taskLogic:8": "AND",
                "visibility:8": vis,
            }
        },
        **ident(n),
        "rewards:9": typed_list(rewards),
        "tasks:9": typed_list(tasks),
    }


# The book. Lines: (id, name, visibility, [(quest, x, y)]).
WIRE_TAG = {"GT.ToolStats:10": {"Damage:4": 0, "MaxDamage:4": 25600, "Speed:5": 4.0}}
QUESTS: list[dict[str, Any]] = [
    quest(
        1,
        "§9§lFirst Steps",
        main=True,
        desc="Look around. [note]Take your time.[/note]",
    ),
    quest(
        2,
        "Gather Wood",
        [1],
        tasks=[retrieval(stack("minecraft:log", 32767, 16, "logWood"))],
    ),
    quest(3, "Make a Pick", [2]),
    quest(
        4,
        "Boiler Time",
        [3],
        tasks=[retrieval(stack("gregtech:gt.blockmachines", 100))],
    ),
    quest(5, "Steam Machines", [4]),
    quest(
        6,
        "First Generator",
        [5],
        tasks=[
            {
                "ignoreNBT:1": 0,
                "index:3": 0,
                "requiredItems:9": typed_list(
                    [
                        stack("gregtech:gt.blockmachines", 1120),
                        stack("gregtech:gt.blockmachines", 1110),
                    ]
                ),
                "taskID:8": "bq_standard:crafting",
            }
        ],
    ),
    quest(7, "Welcome to LV", [6], main=True),
    quest(
        8,
        "Copper Wire",
        [7],
        tasks=[
            retrieval(stack("gregtech:gt.metaitem.01", 2035, 8), consume=True),
            {
                "consume:1": 0,
                "ignoreNBT:1": 1,
                "index:3": 1,
                "requiredFluids:9": typed_list(
                    [{"Amount:3": 1000, "FluidName:8": "water"}]
                ),
                "taskID:8": "bq_standard:fluid",
            },
        ],
        rewards=[
            {
                "index:3": 0,
                "rewardID:8": "bq_standard:item",
                "rewards:9": typed_list([stack("dreamcraft:CoinTechnician", 0, 2)]),
            },
            {
                "choices:9": typed_list(
                    [stack("minecraft:apple", 0, 4), stack("minecraft:bread", 0, 2)]
                ),
                "index:3": 1,
                "rewardID:8": "bq_standard:choice",
            },
        ],
    ),
    quest(9, "Pick a Side", [8, 10], logic="XOR"),
    quest(
        10,
        "MV Hull",
        [8],
        main=True,
        tasks=[retrieval(stack("gregtech:gt.blockmachines", 12))],
    ),
    quest(
        11,
        "HV Hull",
        [10],
        main=True,
        tasks=[retrieval(stack("gregtech:gt.blockmachines", 13))],
    ),
    quest(12, "A Secret", [3], vis="SECRET"),
    quest(13, "Hidden Helper", vis="HIDDEN"),
    quest(14, "Chained On", [12], vis="CHAIN"),
    quest(15, "Only When Done", [5], vis="COMPLETED"),
    quest(16, "Always There", [11], vis="ALWAYS"),
    quest(17, "Unlocked Only", [5], vis="UNLOCKED"),
    quest(18, "Either Way", [5, 8], logic="OR"),
    quest(19, "Not Both", [4, 5], logic="NAND"),
    quest(20, "Before Steam", [4], logic="NOR"),
    quest(21, "Missing Prereq", [2, 999]),
    quest(22, "Shared Trophy", [1], is_global=True),
    quest(
        BIG,
        "Two Lines",
        [3],
        tasks=[retrieval(stack("gregtech:gt.metaitem.01", 32500, 1, tag=WIRE_TAG))],
    ),
    quest(24, "Trigger", [], vis="NORMAL"),
    quest(
        25,
        "Tool Check",
        [3],
        tasks=[
            retrieval(
                stack("gregtech:gt.metaitem.01", 32500, 1, tag=WIRE_TAG),
                ignore_nbt=True,
            )
        ],
    ),
]
LINES: list[tuple[int, str, str, list[tuple[Ident, int, int]]]] = [
    (
        0,
        "Getting Started",
        "NORMAL",
        [(1, 0, 0), (2, 24, 0), (3, 48, 0), (21, 48, 24), (22, 72, 24)],
    ),
    (
        1,
        "§6Steam Stuff",
        "UNLOCKED",
        [
            (4, 0, 0),
            (5, 24, 0),
            (12, 0, 24),
            (14, 24, 24),
            (15, 48, 24),
            (17, 72, 24),
            (19, 96, 24),
            (20, 120, 24),
            (BIG, 48, 48),
            (25, 72, 48),
        ],
    ),
    (
        2,
        "Electric One",
        "UNLOCKED",
        [
            (6, 0, 0),
            (7, 24, 0),
            (8, 48, 0),
            (18, 72, 0),
            (9, 96, 0),
            (10, 24, 24),
            (BIG, 0, 48),
        ],
    ),
    (3, "Electric Two", "UNLOCKED", [(11, 0, 0), (16, 24, 0)]),
    (4, "Staff Only", "HIDDEN", [(13, 0, 0)]),
    (5, "Trophy Case", "COMPLETED", [(22, 0, 0)]),
]
# Quest 24 ("Trigger") is in no line, like GTNH's hidden trigger quests.
PACK = {
    "pack_name:8": "Testpack",
    "pack_version:3": 7,
    "editMode:1": 0,
    "hardcore:1": 0,
}


def line_json(
    n: int, name: str, vis: str, entries: list[tuple[Ident, int, int]] | None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "properties:10": {
            "betterquesting:10": {
                "desc:8": f"About {name}",
                "name:8": name,
                "visibility:8": vis,
            }
        },
        **ident(n, "questLineID"),
    }
    if entries is not None:
        out["quests:9"] = typed_list(
            {**ident(q), "sizeX:3": 24, "sizeY:3": 24, "x:3": x, "y:3": y}
            for q, x, y in entries
        )
    return out


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def write_world_book(
    world: Path, quests: list[dict[str, Any]] = QUESTS, pack_version: int = 7
) -> Path:
    """``<world>/betterquesting/QuestDatabase.json`` as BQ's SaveLoadHandler.saveConfig writes it."""
    lines = [
        {**line_json(n, name, vis, entries), "order:3": n}
        for n, name, vis, entries in LINES
    ]
    path = world / bq.WORLD_DIR / bq.DATABASE
    write_json(
        path,
        {
            "build:8": "3.8.92-GTNH",
            "format:8": "3.1.0",
            "questDatabase:9": typed_list(quests),
            "questLines:9": typed_list(reversed(lines)),  # file order isn't tab order
            "questSettings:10": {
                "betterquesting:10": {**PACK, "pack_version:3": pack_version}
            },
        },
    )
    return path


def write_defaults(game: Path, unlisted_line: bool = False) -> Path:
    """The split ``config/betterquesting/DefaultQuests/`` as BQ's ``/bq_admin default save``."""
    root = game / bq.DEFAULTS
    write_json(
        root / bq.SETTINGS_FILE, {"betterquesting:10": PACK, "format:8": "3.1.0"}
    )
    order = []
    member: dict[str, list[int]] = {}
    for n, name, vis, entries in LINES:
        lid = bq.encode_id(0, n)
        order.append(f"{lid}==: {bq.clean(name)}")
        folder = root / "QuestLines" / f"Line{n}-{lid}=="
        write_json(folder / bq.LINE_FILE, line_json(n, name, vis, None))
        for q, x, y in entries:
            write_json(
                folder / f"Q-{qid(q)}==.json",
                {**ident(q), "sizeX:3": 24, "sizeY:3": 24, "x:3": x, "y:3": y},
            )
            member.setdefault(qid(q), []).append(n)
    if unlisted_line:
        folder = root / "QuestLines" / "Orphan-AAAAAAAAAAAAAAAAAAAAZA=="
        write_json(folder / bq.LINE_FILE, line_json(100, "Orphan", "NORMAL", None))
    (root / bq.LINE_ORDER_FILE).write_text("\n".join(order) + "\n", encoding="utf-8")
    for q in QUESTS:
        key = bq.encode_id(q["questIDHigh:4"], q["questIDLow:4"])
        lines = member.get(key, [])
        sub = (
            "NoQuestLine"
            if not lines
            else "MultipleQuestLine"
            if len(lines) > 1
            else f"Line{lines[0]}"
        )
        write_json(root / "Quests" / sub / f"Q-{key}==.json", q)
    return root


def write_progress(
    world: Path,
    done: dict[str, Iterable[Ident]],
    names: dict[str, str] | None = None,
) -> None:
    folder = world / bq.WORLD_DIR
    if names is not None:
        write_json(
            folder / bq.NAME_CACHE,
            {
                "nameCache:9": typed_list(
                    {"isOP:1": 0, "name:8": n, "uuid:8": u} for u, n in names.items()
                )
            },
        )
    for player, quests in done.items():
        entries = [
            {
                **ident(q),
                "completed:9": typed_list(
                    [
                        {
                            "claimed:1": 1,
                            "timestamp:4": 1_700_000_000_000 + i,
                            "uuid:8": player,
                        }
                    ]
                ),
                "tasks:9": {},
            }
            for i, q in enumerate(quests)
        ]
        # BQ lists every quest of the book, done or not; an entry with no completion is a no-op.
        entries.append({**ident(3000), "completed:9": {}, "tasks:9": {}})
        write_json(
            folder / bq.PROGRESS_DIR / f"{player}.json",
            {"questProgress:9": typed_list(entries)},
        )


# The three progression states (WO-1.5's acceptance): quests complete in each.
FRESH: list[Ident] = []
EARLY_LV: list[Ident] = [1, 2, 3, 4, 5, 6]
HV: list[Ident] = [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 18]


def tier_keys(tmp_path: Path) -> Path:
    rows = [
        ("steam", 4, "Boiler Time"),
        ("LV", 6, "First Generator"),
        ("LV", 7, "Welcome to LV"),
        ("MV", 10, "MV Hull"),
        ("HV", 11, "HV Hull"),
    ]
    rest = [s for s in bq.STAGES if s not in ("stone", "steam", "LV", "MV", "HV")]
    rows += [(s, 500 + i, f"Not in this book {s}") for i, s in enumerate(rest)]
    path = tmp_path / "tier_quests.tsv"
    path.write_text(
        "# test keys\nstage\tquest\tname\tnote\n"
        + "".join(f"{s}\t{qid(q)}\t{name}\t\n" for s, q, name in rows),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def world(tmp_path: Path) -> Path:
    w = tmp_path / "game" / "saves" / "lab-progress"
    write_world_book(w)
    return w


def book_of(world: Path) -> bq.Book:
    return bq.read_book(world, None)


def done_map(
    quests: Iterable[Ident], player: str = CLAYTON
) -> dict[str, dict[str, bq.Completion]]:
    return {player: {qid(q): bq.Completion(1, True) for q in quests}}


def frontier_ids(book: bq.Book, done: Iterable[Ident]) -> list[str]:
    st = bq.states(book, done_map(done), CLAYTON)
    return [q.id for _, _, q in bq.frontier(book, st)]


# --- ids, typed JSON, NBT, text ----------------------------------------------------------------


def test_ids_are_bqs_compact_form() -> None:
    # B§1.3's examples: legacy quest 442, and the Tier 10 line's random UUID.
    assert bq.encode_id(0, 442) == "AAAAAAAAAAAAAAAAAAABug"
    assert bq.decode_id("AAAAAAAAAAAAAAAAAAABug==") == (0, 442)
    assert bq.decode_id("q:AAAAAAAAAAAAAAAAAAABug") == (0, 442)
    high, low = bq.decode_id("D_aFb1hFQH2PDkN0_KvAEQ")
    assert bq.encode_id(high, low) == "D_aFb1hFQH2PDkN0_KvAEQ"
    assert low < 0  # a signed long, as BQ stores it
    assert bq.decode_id(bq.encode_id(*BIG)) == BIG
    for bad in ("", "AAAA", "AAAAAAAAAAAAAAAAAAAB+g", "not a quest id at all!"):
        with pytest.raises(ValueError):
            bq.decode_id(bad)


def test_typed_keys_split_on_the_last_colon() -> None:
    assert bq.split_key("questIDLow:4") == ("questIDLow", 4)
    assert bq.split_key("gregtech:gt.metaitem.01:10") == ("gregtech:gt.metaitem.01", 10)
    assert bq.split_key("plain") == ("plain", None)
    assert bq.split_key("odd:x") == ("odd:x", None)
    t = bq.Typed({"a:9": {"1:8": "second", "0:8": "first"}, "n:3": 5, "f:1": True})
    assert t.seq("a") == ["second", "first"]  # file order, as Gson reads it
    assert t.num("n") == 5 and t.flag("f") and t.text("missing", "x") == "x"


def test_nbt_json_matches_gregteclinks_typing() -> None:
    raw = bq.loads(
        b'{"b:1": 1, "s:2": -3, "i:3": 7, "l:4": 144, "f:5": 1.5, "d:6": 0.1, "bytes:7": [1, -2],'
        b' "str:8": "x", "list:9": {"0:8": "a", "1:8": "b"}, "empty:9": {}, "ints:11": [4],'
        b' "noints:11": [], "c:10": {"k:4": 2}}'
    )
    assert isinstance(raw, dict)
    out = bq.nbt_json(10, raw)
    assert out == {
        "b": "1B",
        "s": "-3S",
        "i": 7,
        "l": "144L",
        "f": "1.5F",
        "d": "0.1D",
        "bytes": ["1B", "-2B"],
        "str": "x",
        "list": ["a", "b"],
        "empty": {"__custom_type": "net.minecraft.nbt.NBTTagList"},
        "ints": [4],
        "noints": {"__custom_type": "net.minecraft.nbt.NBTTagIntArray"},
        "c": {"k": "2L"},
    }
    # A float keeps Java's text: 1.0F, never Python's 1.0 -> "1.0" by luck alone.
    assert bq.nbt_json(5, bq.loads(b"[1.0]")[0]) == "1.0F"  # type: ignore[index]


def test_quest_text_loses_colour_codes_and_markup() -> None:
    assert bq.clean('§9§l"Baked" Potatoes') == '"Baked" Potatoes'
    assert (
        bq.clean("Hi [note]careful[/note] [warn]hot[/warn] [url]x.org[/url]")
        == "Hi careful hot x.org"
    )
    assert (
        bq.clean("See [quest]Other[/quest].[img height=200]pic.png[/img]")
        == "See Other."
    )
    assert (
        bq.clean("keep [damp, normal, arid] and [Player Name]")
        == "keep [damp, normal, arid] and [Player Name]"
    )


# --- reading the book ----------------------------------------------------------------------------


def test_world_book(world: Path) -> None:
    book = book_of(world)
    assert book.source == "world" and book.problems == []
    assert (book.pack_name, book.pack_version, book.format, book.build) == (
        "Testpack",
        7,
        "3.1.0",
        "3.8.92-GTNH",
    )
    assert len(book.quests) == len(QUESTS)
    # Tab order comes from each line's "order", not the file order.
    assert [ln.name for ln in book.lines] == [
        "Getting Started",
        "Steam Stuff",
        "Electric One",
        "Electric Two",
        "Staff Only",
        "Trophy Case",
    ]
    first = book.quests[qid(1)]
    assert (
        first.name == "First Steps"
        and first.descr == "Look around. Take your time."
        and first.main
    )
    assert book.quests[qid(9)].logic == "XOR" and book.quests[qid(9)].prereqs == (
        qid(8),
        qid(10),
    )
    assert book.quests[qid(22)].is_global
    two = [ln.name for ln in book.lines if any(e.quest == qid(BIG) for e in ln.entries)]
    assert two == ["Steam Stuff", "Electric One"]


def test_items_get_gos_item_keys(world: Path) -> None:
    book = book_of(world)
    wood = book.quests[qid(2)].tasks[0]
    assert wood.kind == "bq_standard:retrieval" and wood.consume is False
    assert wood.stacks == (bq.Stack(16, "minecraft:log", 32767, "", "logWood"),)
    wire, water = book.quests[qid(8)].tasks
    assert wire.consume is True and water.stacks == (bq.Stack(1000, fluid="water"),)
    coin, choice = book.quests[qid(8)].rewards
    assert (
        coin.kind == "bq_standard:item"
        and coin.stacks[0].registry_name == "dreamcraft:CoinTechnician"
    )
    assert [s.registry_name for s in choice.stacks] == [
        "minecraft:apple",
        "minecraft:bread",
    ]
    # NBT hashed as WO-1.3 hashes GregtecLink's NBT: the same tag in NBTJson typing.
    tool = book.quests[qid(BIG)].tasks[0].stacks[0]
    nei_typed = {
        "GT.ToolStats": {"Damage": "0L", "MaxDamage": "25600L", "Speed": "4.0F"}
    }
    assert tool.nbt_hash == normalize.nbt_hash(normalize.normalize_nbt(nei_typed)) != ""
    # A task that ignores NBT accepts the item with any NBT: its key has none.
    assert book.quests[qid(25)].tasks[0].stacks[0].nbt_hash == ""


def test_defaults_read_like_the_world_copy(tmp_path: Path, world: Path) -> None:
    game = tmp_path / "pack"
    write_defaults(game, unlisted_line=True)
    defaults = bq.read_default_book(game / bq.DEFAULTS)
    from_world = book_of(world)
    assert defaults.source == "defaults"
    assert (defaults.pack_name, defaults.pack_version) == ("Testpack", 7)
    assert defaults.quests == from_world.quests
    assert [
        (ln.id, ln.name, ln.visibility, ln.ord, set(ln.entries))
        for ln in defaults.lines
    ] == [
        (ln.id, ln.name, ln.visibility, ln.ord, set(ln.entries))
        for ln in from_world.lines
    ]
    # BQ keeps only the lines QuestLinesOrder.txt lists.
    assert defaults.problems == [
        "line AAAAAAAAAAAAAAAAAAAAZA is not in QuestLinesOrder.txt; BQ drops it"
    ]


def test_a_world_with_no_book_yet_reads_the_packs_defaults(tmp_path: Path) -> None:
    game = tmp_path / "game"
    write_defaults(game)
    fresh = game / "saves" / "new-world"
    fresh.mkdir(parents=True)
    book = bq.read_book(fresh, None)  # the game folder is two up from saves/<world>
    assert book.source == "defaults" and len(book.quests) == len(QUESTS)
    assert "doesn't exist yet" in book.problems[0]
    with pytest.raises(bq.FormatError, match="no DefaultQuests"):
        bq.read_book(tmp_path / "elsewhere", None)


def test_a_partial_read_is_retried_then_refused(
    world: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_open, "BACKOFF_S", 0)
    path = world / bq.WORLD_DIR / bq.DATABASE
    full = path.read_bytes()
    reads: list[int] = []
    real = safe_open.safe_open

    def flaky(p: safe_open.StrPath) -> contextlib.AbstractContextManager[BinaryIO]:
        reads.append(1)
        if len(reads) == 1:  # the game is mid-save: a cut-off file
            path.write_bytes(full[: len(full) // 2])
        else:
            path.write_bytes(full)
        return real(p)

    monkeypatch.setattr(safe_open, "safe_open", flaky)
    assert len(bq.read_world_book(path).quests) == len(QUESTS) and len(reads) == 2
    path.write_bytes(full[:100])
    monkeypatch.setattr(safe_open, "safe_open", real)
    with pytest.raises(bq.FormatError):
        bq.read_world_book(path)


# --- progress -----------------------------------------------------------------------------------


def test_progress_and_the_tracked_player(world: Path) -> None:
    write_progress(world, {CLAYTON: [1, 2]}, {CLAYTON: "Clayton"})
    p = bq.read_progress(world)
    assert set(p.done[CLAYTON]) == {qid(1), qid(2)} and p.names == {CLAYTON: "Clayton"}
    assert p.done[CLAYTON][qid(2)] == bq.Completion(1_700_000_000_001, True)
    assert bq.pick_player(p) == CLAYTON
    assert bq.pick_player(p, "clayton") == CLAYTON
    assert bq.pick_player(p, CLAYTON.upper()) == CLAYTON
    write_progress(world, {FRIEND: [1]}, {CLAYTON: "Clayton", FRIEND: "Friend"})
    p = bq.read_progress(world)
    with pytest.raises(ValueError, match="several players"):
        bq.pick_player(p)
    assert bq.pick_player(p, "Friend") == FRIEND
    with pytest.raises(ValueError, match="no player named"):
        bq.pick_player(p, "Nobody")


def test_a_fresh_world_tracks_the_one_player_bq_has_seen(world: Path) -> None:
    with pytest.raises(ValueError, match="no player"):
        bq.pick_player(bq.read_progress(world))
    write_progress(world, {}, {CLAYTON: "Clayton"})  # NameCache only: nothing done yet
    p = bq.read_progress(world)
    assert p.done == {} and bq.pick_player(p) == CLAYTON


# --- unlocking, visibility, frontier --------------------------------------------------------------


@pytest.mark.parametrize(
    ("logic", "results"),
    [
        # complete prerequisites out of 3: 0, 1, 2, 3 (BQ's EnumLogic.getResult)
        ("AND", [False, False, False, True]),
        ("OR", [False, True, True, True]),
        ("NAND", [True, True, True, False]),
        ("NOR", [True, False, False, False]),
        ("XOR", [False, True, False, False]),
        ("XNOR", [False, False, True, False]),
    ],
)
def test_quest_logic(logic: str, results: list[bool]) -> None:
    assert [bq.logic_result(logic, n, 3) for n in range(4)] == results


def test_states_follow_bqs_rules(world: Path) -> None:
    book = book_of(world)

    def state(
        done: Iterable[Ident],
        n: Ident,
        player: str = CLAYTON,
        other: Iterable[Ident] = (),
    ) -> tuple[str, bool]:
        d = done_map(done)
        d[FRIEND] = {qid(q): bq.Completion(1, True) for q in other}
        s = bq.states(book, d, player)[qid(n)]
        return s.state, s.shown

    # NORMAL foreshadows: locked but shown once every prerequisite is unlocked.
    assert state([], 2) == ("locked", True)
    assert state([], 3) == ("locked", False)
    # SECRET and UNLOCKED show only once unlocked; HIDDEN never; ALWAYS always.
    assert state([1, 2], 12) == ("locked", False) and state([1, 2, 3], 12) == (
        "available",
        True,
    )
    assert state([1, 2, 3, 4], 17) == ("locked", False) and state(
        [1, 2, 3, 4, 5], 17
    ) == ("available", True)
    assert state([], 13) == ("available", False)
    assert state([], 16) == ("locked", True)
    # COMPLETED shows only when done; CHAIN when a prerequisite is shown.
    assert state(EARLY_LV, 15) == ("available", False) and state(
        [*EARLY_LV, 15], 15
    ) == ("done", True)
    assert state([1, 2], 14) == ("locked", False) and state([1, 2, 3], 14) == (
        "locked",
        True,
    )
    # OR, NAND, NOR, XOR.
    assert state(EARLY_LV, 18)[0] == "available"
    assert (
        state([1, 2, 3, 4], 19)[0] == "available"
        and state(EARLY_LV[:5], 19)[0] == "locked"
    )
    assert (
        state([1, 2, 3], 20)[0] == "available"
        and state([1, 2, 3, 4], 20)[0] == "locked"
    )
    assert state([*EARLY_LV, 7, 8], 9)[0] == "available"  # one of 8 and 10
    assert state([*EARLY_LV, 7, 8, 10], 9)[0] == "locked"  # both: XOR locks it again
    # A prerequisite missing from the book counts against AND, so the quest never unlocks.
    assert state([1, 2], 21) == ("locked", True)
    # A global quest is done for everyone once anyone has done it.
    assert state([1], 22, other=[22])[0] == "done"
    assert state([1], 22)[0] == "available"


def test_frontier_in_three_progression_states(world: Path) -> None:
    book = book_of(world)
    # A new world: the first quest, and the NAND and NOR quests, which BQ unlocks while none
    # of their prerequisites is done (the hidden helper is unlocked too, but never shown).
    assert frontier_ids(book, FRESH) == [qid(1), qid(19), qid(20)]
    # Early LV: the LV welcome and the side quests steam opened; quest 15 (COMPLETED) is not.
    assert frontier_ids(book, EARLY_LV) == [
        qid(22),  # Getting Started (Trophy Case is a COMPLETED line: not listed yet)
        qid(12),
        qid(17),
        qid(BIG),
        qid(25),  # Steam Stuff, top to bottom, left to right
        qid(7),
        qid(18),
        qid(BIG),  # Electric One: the two-line quest is in both
    ]
    # HV: the chained quest opened, quest 9 (XOR) is locked again with both sides done, the
    # ALWAYS quest is available; quest 15 (COMPLETED) stays out of the book until done.
    assert frontier_ids(book, HV) == [
        qid(22),
        qid(14),
        qid(17),
        qid(BIG),
        qid(25),
        qid(BIG),
        qid(16),
    ]


# --- tier ---------------------------------------------------------------------------------------


def test_tier_in_three_progression_states(tmp_path: Path) -> None:
    keys = bq.read_tier_keys(tier_keys(tmp_path))
    assert bq.detect_tier(keys, []) == bq.Tier("stone", 0, None)
    assert bq.detect_tier(keys, map(qid, [1, 2, 3, 4])) == bq.Tier(
        "steam", 0, qid(4), "Boiler Time"
    )
    assert bq.detect_tier(keys, map(qid, EARLY_LV)) == bq.Tier(
        "LV", 1, qid(6), "First Generator"
    )
    assert bq.detect_tier(keys, map(qid, HV)) == bq.Tier("HV", 3, qid(11), "HV Hull")
    # Either LV key will do: the checkbox alone (a quest completed by command) counts.
    assert bq.detect_tier(keys, [qid(7)]).stage == "LV"
    # The highest stage reached wins, even with a lower one skipped.
    assert bq.detect_tier(keys, [qid(10)]).stage == "MV"


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("stage\tquest\tname\n", "header"),
        ("stage\tquest\tname\tnote\nsteam\tAAAAAAAAAAAAAAAAAAAABA\tx\n", "3 cells"),
        (
            "stage\tquest\tname\tnote\nULV\tAAAAAAAAAAAAAAAAAAAABA\tx\t\n",
            "unknown stage",
        ),
        (
            "stage\tquest\tname\tnote\nstone\tAAAAAAAAAAAAAAAAAAAABA\tx\t\n",
            "unknown stage",
        ),
        ("stage\tquest\tname\tnote\nsteam\tnope\tx\t\n", "not a quest id"),
        (
            "stage\tquest\tname\tnote\nMV\tAAAAAAAAAAAAAAAAAAAABA\tx\t\nLV\tAAAAAAAAAAAAAAAAAAAABA\tx\t\n",
            "comes after",
        ),
        (
            "stage\tquest\tname\tnote\nsteam\tAAAAAAAAAAAAAAAAAAAABA\tx\t\n",
            "no key quest for LV",
        ),
    ],
)
def test_bad_tier_files_are_refused(tmp_path: Path, body: str, error: str) -> None:
    path = tmp_path / "keys.tsv"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        bq.read_tier_keys(path)


def test_the_curated_tier_quests() -> None:
    keys = bq.read_tier_keys(quest_db.TIER_KEYS)
    assert [k.stage for k in keys][:3] == ["steam", "LV", "LV"]
    assert len({k.quest for k in keys}) == len(keys)
    assert {k.stage for k in keys} == set(bq.STAGES) - {"stone"}
    assert all(k.name for k in keys)


# --- the DB and the command ----------------------------------------------------------------------


def prism(tmp_path: Path, world_name: str = "lab-progress") -> tuple[Path, Path]:
    """A Prism instances folder with a Lab whose world has the book, and a gos.toml."""
    instances = tmp_path / "instances"
    lab = instances / "GTNH-Lab"
    (lab / ".minecraft" / "saves").mkdir(parents=True)
    (lab / "instance.cfg").write_text("name=GTNH-Lab\n", encoding="utf-8")
    world = lab / ".minecraft" / "saves" / world_name
    write_world_book(world)
    home = tmp_path / "home"
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{home.as_posix()}"\nprism_instances = "{instances.as_posix()}"\n',
        encoding="utf-8",
    )
    return world, toml


def run(*args: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def db_of(toml: Path) -> sqlite3.Connection:
    """The DB in the gos.toml's GOS_HOME, made with the schema if missing."""
    home = Path(toml.read_text(encoding="utf-8").split('gos_home = "')[1].split('"')[0])
    (home / "data").mkdir(parents=True, exist_ok=True)
    conn = access.connect(access.db_path(home))
    conn.row_factory = None  # plain tuples, to compare with
    return conn


def tree(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setattr(quest_db, "TIER_KEYS", tier_keys(tmp_path))
    return prism(tmp_path)


def test_load_writes_the_quest_tables(lab: tuple[Path, Path]) -> None:
    world, toml = lab
    write_progress(
        world,
        {CLAYTON: EARLY_LV, FRIEND: [1, 22]},
        {CLAYTON: "Clayton", FRIEND: "Friend"},
    )
    before = tree(world)
    code, out = run(
        "quests",
        "load",
        "--instance",
        "lab",
        "--world",
        "lab-progress",
        "--player",
        "Clayton",
        "--config",
        str(toml),
    )
    assert code == 0, out
    assert tree(world) == before  # read-only: nothing in the world changed
    assert "Tier detected: LV (GT voltage tier 1) from q:" in out
    # 7 quests are open, but the friend's done the global trophy, so it's done for Clayton too.
    assert "Frontier: 6 quests" in out
    conn = db_of(toml)
    try:

        def count(table: str) -> int:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        assert count("quest") == len(QUESTS) and count("quest_line") == len(LINES)
        assert count("quest_line_entry") == sum(len(e) for *_, e in LINES)
        assert count("quest_prereq") == sum(len(q["preRequisites:9"]) for q in QUESTS)
        assert count("quest_progress") == len(EARLY_LV) + 2
        assert count("quest_state") == len(QUESTS)
        row = conn.execute(
            "SELECT line_id, name, logic, visibility, main, task_logic, is_global, repeat_time FROM quest WHERE id = ?",
            (qid(BIG),),
        ).fetchone()
        assert row == (
            bq.encode_id(0, 1),
            "Two Lines",
            "AND",
            "NORMAL",
            0,
            "AND",
            0,
            -1,
        )
        raw = json.loads(
            conn.execute(
                "SELECT raw_json FROM quest WHERE id = ?", (qid(1),)
            ).fetchone()[0]
        )
        assert raw["properties:10"]["betterquesting:10"]["name:8"] == "§9§lFirst Steps"
        assert conn.execute(
            "SELECT quest_id FROM quest_fts WHERE quest_fts MATCH 'wire'"
        ).fetchall() == [(qid(8),)]
        # A task with several stacks has no single item; quest_item has each.
        assert conn.execute(
            "SELECT kind, amount, consume FROM quest_task WHERE quest_id = ? ORDER BY idx",
            (qid(8),),
        ).fetchall() == [
            ("bq_standard:retrieval", 8, 1),
            ("bq_standard:fluid", 1000, 0),
        ]
        assert conn.execute(
            "SELECT amount FROM quest_task WHERE quest_id = ?", (qid(6),)
        ).fetchone() == (None,)
        assert conn.execute(
            "SELECT part, idx, slot, registry_name, meta, nbt_hash, ore, fluid, amount FROM quest_item WHERE quest_id = ? ORDER BY part DESC, idx, slot",
            (qid(8),),
        ).fetchall() == [
            ("task", 0, 0, "gregtech:gt.metaitem.01", 2035, "", None, None, 8),
            ("task", 1, 0, None, None, None, None, "water", 1000),
            ("reward", 0, 0, "dreamcraft:CoinTechnician", 0, "", None, None, 2),
            ("reward", 1, 0, "minecraft:apple", 0, "", None, None, 4),
            ("reward", 1, 1, "minecraft:bread", 0, "", None, None, 2),
        ]
        detected = json.loads(
            conn.execute("SELECT value FROM kv WHERE key = 'tier.detected'").fetchone()[
                0
            ]
        )
        assert detected == {
            "name": "First Generator",
            "player": CLAYTON,
            "quest": qid(6),
            "stage": "LV",
            "tier": 1,
        }
        source = json.loads(
            conn.execute("SELECT value FROM kv WHERE key = 'quests.source'").fetchone()[
                0
            ]
        )
        assert source["source"] == "world" and source["player_name"] == "Clayton"
        assert source["players"] == sorted([CLAYTON, FRIEND])
    finally:
        conn.close()


def test_the_named_frontier_query_is_the_books(lab: tuple[Path, Path]) -> None:
    world, toml = lab
    book = book_of(world)
    for done in (FRESH, EARLY_LV, HV):
        write_progress(world, {CLAYTON: done}, {CLAYTON: "Clayton"})
        assert (
            run(
                "quests",
                "load",
                "--instance",
                "lab",
                "--world",
                "lab-progress",
                "--config",
                str(toml),
            )[0]
            == 0
        )
        conn = db_of(toml)
        try:
            rows = [r[2] for r in quest_db.frontier_rows(conn)]
        finally:
            conn.close()
        assert rows == frontier_ids(book, done)
    code, out = run(
        "quests", "frontier", "--line", "electric two", "--config", str(toml)
    )
    assert code == 0
    assert out.splitlines()[1:] == [
        "line\tquest\tid",
        f"Electric Two\tAlways There\tq:{qid(16)}",
        "# 1 rows, 1 quests",
    ]


def test_items_link_to_the_recipe_db(lab: tuple[Path, Path]) -> None:
    world, toml = lab
    write_progress(world, {}, {CLAYTON: "Clayton"})
    conn = db_of(toml)
    try:
        tool_hash = book_of(world).quests[qid(BIG)].tasks[0].stacks[0].nbt_hash
        conn.executemany(
            "INSERT INTO item(registry_name, meta, nbt_hash) VALUES (?,?,?)",
            [
                ("gregtech:gt.blockmachines", 100, ""),
                ("gregtech:gt.metaitem.01", 32500, tool_hash),
                ("gregtech:gt.metaitem.01", 32500, ""),
            ],
        )
        conn.execute("INSERT INTO fluid(name) VALUES ('water')")
        conn.commit()
    finally:
        conn.close()
    code, out = run(
        "quests",
        "load",
        "--instance",
        "lab",
        "--world",
        "lab-progress",
        "--config",
        str(toml),
    )
    assert code == 0 and "4 found in the recipe DB" in out
    conn = db_of(toml)
    try:
        ids = dict(
            conn.execute(
                "SELECT registry_name || '@' || meta || '#' || nbt_hash, id FROM item"
            )
        )
        linked = conn.execute(
            "SELECT quest_id, item_id, fluid_id FROM quest_item WHERE item_id IS NOT NULL OR fluid_id IS NOT NULL ORDER BY quest_id"
        ).fetchall()
        assert sorted(linked) == sorted(
            [
                (qid(4), ids["gregtech:gt.blockmachines@100#"], None),
                (qid(BIG), ids[f"gregtech:gt.metaitem.01@32500#{tool_hash}"], None),
                (qid(25), ids["gregtech:gt.metaitem.01@32500#"], None),  # ignores NBT
                (qid(8), None, 1),
            ]
        )
        assert conn.execute(
            "SELECT item_id FROM quest_task WHERE quest_id = ?", (qid(4),)
        ).fetchone() == (ids["gregtech:gt.blockmachines@100#"],)
    finally:
        conn.close()


def test_tier_override(lab: tuple[Path, Path]) -> None:
    world, toml = lab
    write_progress(world, {CLAYTON: HV}, {CLAYTON: "Clayton"})
    assert run("quests", "tier", "--config", str(toml))[0] == 1  # nothing loaded yet
    assert (
        run(
            "quests",
            "load",
            "--instance",
            "lab",
            "--world",
            "lab-progress",
            "--config",
            str(toml),
        )[0]
        == 0
    )
    code, out = run("quests", "tier", "--config", str(toml))
    assert code == 0 and out.startswith("Tier: HV (GT voltage tier 3) (quests)")
    code, out = run("quests", "tier", "--set", "EV", "--config", str(toml))
    assert (
        out.startswith("Tier: EV (GT voltage tier 4) (override)")
        and "Detected from quests: HV" in out
    )
    # A reload keeps the override (D64: it sticks until he clears it).
    assert (
        run(
            "quests",
            "load",
            "--instance",
            "lab",
            "--world",
            "lab-progress",
            "--config",
            str(toml),
        )[0]
        == 0
    )
    assert run("quests", "tier", "--config", str(toml))[1].startswith("Tier: EV")
    assert run("quests", "tier", "--clear", "--config", str(toml))[1].startswith(
        "Tier: HV"
    )
    with pytest.raises(SystemExit):  # argparse refuses an unknown stage
        cli.main(["quests", "tier", "--set", "nope"])


def test_a_failed_load_changes_nothing(lab: tuple[Path, Path]) -> None:
    world, toml = lab
    write_progress(world, {CLAYTON: EARLY_LV}, {CLAYTON: "Clayton"})
    assert (
        run(
            "quests",
            "load",
            "--instance",
            "lab",
            "--world",
            "lab-progress",
            "--config",
            str(toml),
        )[0]
        == 0
    )
    (world / bq.WORLD_DIR / bq.DATABASE).write_text("{not json", encoding="utf-8")
    code, out = run(
        "quests",
        "load",
        "--instance",
        "lab",
        "--world",
        "lab-progress",
        "--config",
        str(toml),
    )
    assert code == 1 and out.startswith("Not loaded:")
    conn = db_of(toml)
    try:
        assert conn.execute("SELECT count(*) FROM quest").fetchone() == (len(QUESTS),)
    finally:
        conn.close()


def test_load_refusals(tmp_path: Path, lab: tuple[Path, Path]) -> None:
    world, toml = lab
    assert run("quests", "load", "--instance", "lab", "--config", str(toml))[
        1
    ].startswith("Not loaded: name the world")
    assert (
        "doesn't exist"
        in run(
            "quests",
            "load",
            "--instance",
            "lab",
            "--world",
            "other",
            "--config",
            str(toml),
        )[1]
    )
    assert (
        "folder name"
        in run(
            "quests",
            "load",
            "--instance",
            "lab",
            "--world",
            "../x",
            "--config",
            str(toml),
        )[1]
    )
    assert (
        "GTNH-Play"
        in run("quests", "load", "--world", "lab-progress", "--config", str(toml))[1]
    )
    assert (
        "no player"
        in run(
            "quests",
            "load",
            "--instance",
            "lab",
            "--world",
            "lab-progress",
            "--config",
            str(toml),
        )[1]
    )
    bad = tmp_path / "bad.toml"
    bad.write_text("[paths\n", encoding="utf-8")
    assert run("quests", "load", "--config", str(bad))[0] == 2
    if hasattr(os, "symlink"):
        link = world.parent / "linked"
        try:
            link.symlink_to(world, target_is_directory=True)
        except OSError:
            return
        assert (
            "link"
            in run(
                "quests",
                "load",
                "--instance",
                "lab",
                "--world",
                "linked",
                "--config",
                str(toml),
            )[1]
        )


def test_world_from_config_and_world_dir(
    tmp_path: Path, lab: tuple[Path, Path]
) -> None:
    world, toml = lab
    write_progress(world, {CLAYTON: [1]}, {CLAYTON: "Clayton"})
    code, out = run("quests", "load", "--world-dir", str(world), "--config", str(toml))
    assert code == 0 and "1 quests complete" in out
    toml.write_text(
        toml.read_text(encoding="utf-8") + 'world = "lab-progress"\n', encoding="utf-8"
    )
    assert run("quests", "load", "--instance", "lab", "--config", str(toml))[0] == 0


def test_a_newer_pack_book_is_reported(lab: tuple[Path, Path]) -> None:
    # The world's book is version 5, the pack's DefaultQuests version 7: BQ imports the pack's
    # book when the world next loads, so what GOS read is about to change.
    world, toml = lab
    write_world_book(world, pack_version=5)
    write_defaults(world.parent.parent)
    write_progress(world, {}, {CLAYTON: "Clayton"})
    code, out = run(
        "quests",
        "load",
        "--instance",
        "lab",
        "--world",
        "lab-progress",
        "--config",
        str(toml),
    )
    assert code == 0 and "DefaultQuests is version 7" in out
    write_world_book(world, pack_version=7)
    out = run(
        "quests",
        "load",
        "--instance",
        "lab",
        "--world",
        "lab-progress",
        "--config",
        str(toml),
    )[1]
    assert "DefaultQuests is version" not in out


def test_schema_1_db_gets_the_new_quest_tables(tmp_path: Path) -> None:
    path = tmp_path / "gos.sqlite"
    conn = access.connect(path)
    conn.row_factory = None
    conn.execute("INSERT INTO item(registry_name, meta) VALUES ('minecraft:stone', 0)")
    # Make it a version-1 DB: version 1's quest and quest_line, without the new columns.
    conn.executescript(
        "DROP TABLE quest; DROP TABLE quest_line; DROP TABLE quest_state;"
        " CREATE TABLE quest(id TEXT PRIMARY KEY, line_id TEXT, name TEXT, descr TEXT, logic TEXT,"
        " visibility TEXT, main INT, raw_json TEXT);"
        " CREATE TABLE quest_line(id TEXT PRIMARY KEY, name TEXT, ord INT);"
        " PRAGMA user_version = 1;"
    )
    conn.close()
    conn = access.connect(path)
    conn.row_factory = None
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(quest)")}
        assert {"task_logic", "is_global", "repeat_time"} <= cols
        assert {r[1] for r in conn.execute("PRAGMA table_info(quest_line)")} >= {
            "descr",
            "visibility",
        }
        assert conn.execute("SELECT count(*) FROM quest_state").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM item").fetchone() == (
            1,
        )  # recipe data kept
        assert conn.execute("PRAGMA user_version").fetchone() == (
            access.SCHEMA_VERSION,
        )
    finally:
        conn.close()
