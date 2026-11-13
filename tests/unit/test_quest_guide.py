"""The quest guide (WO-6.4, plan 7.4, D26, D31, D72, D268-D270) on its fixture pack.

tests/evals/quest_pack.py's book: Clayton has done 1 (First Steps) and 10 (Logs); 2 Steel Age,
3 Oxygen Run, 11 Bricks, 12 Charcoal Pile and 13 Either Way are his frontier; 4 Stainless Dreams
and 14 Blast Off are locked but shown; 5, 6, 7, 15, 20, 21 and 22 are locked and not shown. His
goals are the Electric Blast Furnace (quest 22) and oxygen (quest 3); a steel ingot plan is
accepted (quest 2 asks for steel ingots).
"""

from __future__ import annotations

import io
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec import config
from gregtec.agents import ask, router
from gregtec.agents.ask import NOTHING_TO_SPOIL, Asker, Reply
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane
from gregtec.agents.tools.catalog import default_registry
from gregtec.agents.tools.registry import Registry
from gregtec.agents.tools.tsv import parse
from gregtec.db import access
from gregtec.llm.fake import ScriptedClient, Turn
from gregtec.quests import guide, questeval, spoilers, summaries, view
from gregtec.vault import curator, schema
from gregtec.vault.io import VaultIO

PACK = questeval.pack()


def q(n: int) -> str:
    return f"q:{PACK.qid(n)}"


FRONTIER = [
    11,
    3,
    2,
    12,
    13,
]  # ranked: goal 1 (5 steps), goal 2 (itself), the plan, book order
LOCKED = [4, 5, 6, 7, 14, 15, 20, 21, 22]


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    h = tmp_path_factory.mktemp("quest-guide") / "home"
    PACK.build(h)
    return h


@pytest.fixture
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(home), readonly=True)
    yield c
    c.close()


@pytest.fixture
def reg(home: Path) -> Registry:
    return default_registry(home, deep=HandoffRecorder())


@pytest.fixture(scope="module")
def fields(home: Path) -> dict[str, questeval.Spoiler]:
    c = access.connect(access.db_path(home), readonly=True)
    try:
        return questeval.spoiler_fields(c)
    finally:
        c.close()


# --- spoiler levels: whole-utterance commands only (D268 R1) ------------------------------


@pytest.fixture(scope="module")
def names(home: Path) -> frozenset[str]:
    """The quest names a "spoil <quest>" can name in the pack (the asker's exact match)."""
    c = access.connect(access.db_path(home), readonly=True)
    try:
        return frozenset(
            spoilers.name_key(n) for (n,) in c.execute("SELECT name FROM quest")
        )
    finally:
        c.close()


def _level(said: str | None, names: frozenset[str]) -> int:
    """The level ``said`` raises to: a command, and its quest (if it names one) found."""
    c = spoilers.command(said)
    if c is None or (c.quest is not None and c.quest not in names):
        return spoilers.NUDGE
    return c.level


# The closed set (R1): command -> (level, the quest it names). A bare command names none.
_COMMANDS: dict[str, tuple[int, str | None]] = {
    **{c: (2, None) for c in spoilers.FULL_COMMANDS},
    **{c: (1, None) for c in spoilers.PARTIAL_COMMANDS},
    "spoil the secret alloy quest": (2, "secret alloy"),
    "spoil secret alloy": (2, "secret alloy"),
    "spoil the secret alloy quest fully": (2, "secret alloy"),
    "spoil EBF Time": (2, "ebf time"),
}
_FILLED = [
    "{cmd}",
    "{cmd} please",
    "{cmd}, please",
    "please {cmd}",
    "ok, {cmd}",
    "ok {cmd}",
    "hey gos, {cmd}",
    "Hey GOS {cmd}.",
    "gos: {cmd}",
    "can you {cmd}",
    "could you {cmd}?",
    "um {cmd}",
    "uh, {cmd}",
    "so {cmd}",
    "now {cmd}",
    "ok so {cmd} then",
    "{cmd} now",
    "{cmd} pls",
    "{cmd} for me",
    "{cmd}!",
]


def test_the_closed_set_raises_exactly_its_scope() -> None:
    for cmd, (level, quest) in _COMMANDS.items():
        for t in _FILLED:
            said = t.format(cmd=cmd)
            got = spoilers.command(said)
            assert got == spoilers.Command(level, quest), said
            # the router routes it as the instant command, and its normalisation keeps it
            assert router.route(said).intent == router.SPOIL, said
            assert spoilers.command(router.normalise(said)) == got, said
    by_id = spoilers.command(f"spoil {q(5)}")
    assert by_id == spoilers.Command(
        2, q(5)
    )  # the id as he wrote it (ids are case-sensitive)
    lower = spoilers.command(f"spoil {q(5).lower()}")  # another id: no such quest
    assert lower is not None and lower.quest != q(5)


# Every phrasing from the four review rounds: none is a command (R1).
_EARLIER = [
    # round 1 and 2: questions, negations, contractions without the apostrophe
    "tell me about the secret alloy quest", "what quests can i do",
    "Spoil it: what does pressure cooker give", "tell me everything about the EBF Time quest",
    "what does the secret alloy quest need", "what are the quest's requirements",
    "what does q:AAQ require", "which quests need steel", "i need to finish this quest",
    "more of a hint please, about steel", "ok just spoil it", "could you not spoil it",
    "what comes after it? spoil it", "i can't find it. spoil it", "i can't find it, spoil it",
    "yes spoil it", "no spoilers, but what does it need", "don't spoil it",
    "I don't want you to spoil it, what is the secret alloy quest",
    "I don’t want you to spoil it", "no need to spoil it, just a hint",
    "without spoiling anything", "I don't need the full answer", "don't tell me everything",
    "i dont need the full answer", "i dont want full spoilers",
    "I didnt ask for the full answer", "i wont need the full answer",
    "i cant handle full spoilers yet", "dont tell me everything",
    "show me everything i can make with steel", "tell me everything i can make with steel",
    "tell me everything about EBF Time", "don't spoil anything, just spoil it",
    "would that spoil the surprise?", "does it spoil anything", "what do i need to make steel",
    "what does my EBF need for power", "what's my next task", "what are its requirements",
    "show me some more recipes", "what's my spoiler level", "tell me everything",
    "i want full spoilers", "give me a partial hint", "if i get stuck, spoil it",
    "he said, spoil it", 'what does "spoil it" mean', "'spoil it' is what he typed",
    "what happens if you spoil it", "should you spoil it", "would spoil it be too much",
    "is full answer a setting", "i wonder whether to say spoil it",
    "ok so what comes after the secret alloy quest spoil it", "spoil it is not what i want",
    "spoil it dont", "is that the full answer", "a bit more steel please",
    "what's the full recipe for steel", "give me the full list of my plans",
    # round 3: conditionals, caveats, retractions after the command
    "spoil it, if i get stuck later", "spoil it, but only after i finish the steam age",
    "full answer, unless it ruins the surprise", "spoil it, just kidding", "spoil it, jk",
    "could you spoil it, or is that bad", "spoil it. never mind", "spoil it? no",
    "spoil it... nevermind", "spoil it? nah", "spoil it. wait no. hold on",
    "spoil it, if i get stuck", "spoil it, once i finish steel", "spoil it, later",
    "would you spoil it, if i asked", "could you spoil it, or would that ruin the fun",
    "spoil everything, except the rewards", "tell me everything, how do i make steel",
    "tell me everything about the steel for my next quest",
    "my brother says. spoil it. what does he mean",
    "tell me about the secret alloy quest. spoil it? no",
    "spoil it... nevermind, i want to work it out",
    "spoil the secret alloy quest, if i get stuck",
    "would you spoil the secret alloy quest, if i asked",
    "spoil it, what comes after the secret alloy quest",
    "spoil it. what comes after the secret alloy quest?",
    "what comes after the secret alloy quest? spoil it!",
    # round 4 (B1-B3 and the lows)
    "spoil it? what does that do", "spoil it. what does that mean",
    "spoil it? how does that work", "spoil me? what does that even mean",
    "full answer? is that a setting", "full answer. what does that mean",
    "in case i get stuck: spoil it", "in case i get stuck. spoil it",
    "suppose i get stuck. spoil it", "assuming i get stuck: spoil it",
    "hypothetically. spoil it", "after i finish the steam age: spoil it", "tomorrow. spoil it",
    "next session: spoil it", "the quest text reads: spoil it",
    "the quest description reads: spoil it. what does it mean",
    "someone in chat posted: spoil it", "my friend messaged me: spoil it",
    "bob in game chat: spoil it", "the sign in spawn: spoil it",
    "spoil it: what does it give, but only after steel", "spoil it, how about later",
    "spoil it: is that a bad idea", "spoil it? is that cheating", "spoil it, should i?",
    "my friend: spoil it", "note to self: spoil it", "q: spoil it", "dont. spoil it",
    "a bit more. how much steel does the ebf take",
    "the full answer: how many steel ingots for the coke oven", "not yet. spoil it",
    "i want to work it out myself. spoil it", "what comes after X? spoil it",
    "what comes after stainless dreams? spoil it",
    "what comes after the secret alloy quest? spoil it", "spoil it bro", "spoil it already",
    "you can spoil it", "", "spoilt",
]  # fmt: skip


def test_no_earlier_phrasing_raises(names: frozenset[str]) -> None:
    raised = [x for x in _EARLIER if spoilers.command(x) is not None]
    raised += [x for x in _EARLIER if _level(x, names)]
    raised += [x for x in _EARLIER if router.route(x or "x").intent == router.SPOIL]
    assert raised == []
    assert spoilers.command(None) is None


# Property style (R1): each command wrapped in generated prefixes, suffixes, labels,
# conditions, quotations and questions never raises.
_PREFIX = [
    "if i get stuck, ", "if i get stuck ", "in case i get stuck: ", "suppose i get stuck. ",
    "hypothetically ", "tomorrow. ", "after steel, ", "when i ask, ", "unless i say ",
    "he said ", "my friend: ", "the quest text reads: ", "someone in chat posted: ",
    "note to self: ", "not yet. ", "don't ", "dont ", "no ", "never ", "i don't want ",
    "should you ", "would you ", "what happens if you ", "what comes after it? ",
    "tell me about the secret alloy quest. ", "i can't find it. ", "yes ", "you can ",
    "i wonder whether to say ", "what does ", "is ",
]  # fmt: skip
_SUFFIX = [
    "? what does that do", ". what does that mean", ", if i get stuck", " if i get stuck",
    ", later", ". never mind", "? no", ", just kidding", " jk", ", but only after steel",
    ": what does it give", ", what comes after the secret alloy quest", " is too much",
    " bro", " already", "? is that cheating", ", should i?", " mean", " or not",
    " what does it give", " about steel", ", unless it ruins it",
]  # fmt: skip
_QUOTES = ['"{cmd}"', "'{cmd}'", "“{cmd}”", "say {cmd}", "({cmd})"]


def _wrapped() -> list[str]:
    out = []
    for cmd in _COMMANDS:
        out += [p + cmd for p in _PREFIX]
        out += [cmd + s for s in _SUFFIX]
        out += [p + cmd + s for p in _PREFIX[::3] for s in _SUFFIX[::3]]
    # STT style: no punctuation, and shouting
    out += [x.replace(",", "").replace("'", "").replace("?", "") for x in out]
    out += [x.upper() for x in out[::7]]
    out += [t.format(cmd=cmd) for t in _QUOTES for cmd in _COMMANDS]
    return sorted(set(out))


def test_a_wrapped_command_never_raises(names: frozenset[str]) -> None:
    """A "spoil <quest>" with words after it may still parse as a command, naming a quest no
    book has ("secret alloy quest bro"): it raises nothing, the asker says no quest has that
    name. So this checks the rise, and that no bare command is ever found inside."""
    wrapped = _wrapped()
    assert len(wrapped) > 3000
    assert [x for x in wrapped if _level(x, names) != spoilers.NUDGE] == []
    bare = [x for x in wrapped if (c := spoilers.command(x)) and c.quest is None]
    assert bare == []


def test_a_mention_says_how_to_ask() -> None:
    assert spoilers.mentions("what comes after it? spoil it")
    assert spoilers.mentions("the quest text reads: spoil it")
    assert not spoilers.mentions("spoil it")  # a command, not a mention
    assert not spoilers.mentions("don't spoil it")
    assert not spoilers.mentions("what does steel need")


def test_spoiler_default_is_a_service_key(tmp_path: Path) -> None:
    path = tmp_path / "gos.toml"
    path.write_text("[policy]\nspoiler_default = 2\n", encoding="utf-8")
    assert config.load(path).spoiler_default == 2
    assert config.Config().spoiler_default == 0  # D26: nudge by default
    path.write_text("[policy]\nspoiler_default = 5\n", encoding="utf-8")
    cfg = config.load(path)  # a bad one keeps the default and stops the core, not gos
    assert cfg.spoiler_default == 0
    assert any("spoiler_default" in e for e in cfg.service_errors)
    with pytest.raises(ValueError):
        spoilers.check_level(True)


# --- the code's guard: scope comes from code, never from the model (D268 R2) ---------------


def _get(reg: Registry, n: int, **kw: object) -> tuple[dict[str, str], str]:
    out = reg.call("quest_get", {"id": q(n), "spoil": 2}, **kw)  # type: ignore[arg-type]
    assert not out.is_error, out.text
    return parse(out.text).head, out.text


def _id(n: int) -> str:
    return PACK.qid(n)


def test_a_lane_never_raises_whatever_the_request(reg: Registry) -> None:
    """A lane's call is at the global level whatever his request says, commands included:
    only the asker's instant command grants (R2)."""
    for said in ("tell me about the secret alloy quest", "spoil it", "full answer",
                 "spoil the secret alloy quest", "partial answer", None):  # fmt: skip
        head, text = _get(reg, 5, lane="fast", request=said)
        assert head["spoil"] == "0", said
        assert head["name"] == "(not shown in the quest book yet)", said
        assert "chromium" not in text and "spoil=2 was not shown" in text, said


def test_a_grant_raises_that_quest_only(reg: Registry) -> None:
    grant = {_id(5): 2}
    head, text = _get(reg, 5, lane="fast", grant=grant)
    assert head["spoil"] == "2" and "chromium-rich" in text and "@17306" in text
    # what it unlocks, and the next quest along, stay at the global level (B4)
    rows = parse(text).rows
    unlocks = [r for r in rows if r["part"] == "unlocks"]
    assert {r["cite"] for r in unlocks} == {q(6), q(7)}
    assert {r["what"] for r in unlocks} == {"(not shown in the quest book yet)"}
    for n in (6, 7, 22):
        head, text = _get(reg, n, lane="fast", grant=grant)
        assert head["spoil"] == "0" and "worthy" not in text, n
        assert "changes everything" not in text, n
    # a model's spoil above the grant is ignored; below it is kept
    head, text = _get(reg, 5, lane="fast", grant={_id(5): 1})
    assert head["spoil"] == "1" and head["name"] == "Secret Alloy"
    assert "chromium" not in text and "@17306" not in text
    out = reg.call("quest_get", {"id": q(5), "spoil": 0}, lane="fast", grant=grant)
    assert parse(out.text).head["spoil"] == "0"
    # a grant never opens db_sql
    out = reg.call(
        "db_sql", {"select": "SELECT descr FROM quest"}, lane="fast", grant=grant
    )
    assert out.is_error


def test_a_granted_quests_hidden_prerequisite_stays_hidden(reg: Registry) -> None:
    head, text = _get(reg, 6, lane="fast", grant={_id(6): 2})
    assert head["spoil"] == "2" and head["name"] == "Hidden Cache"
    prereqs = [r for r in parse(text).rows if r["part"] == "prereq"]
    assert [r["cite"] for r in prereqs] == [q(5)]
    assert prereqs[0]["what"] == "(not shown in the quest book yet)"
    assert "Secret Alloy" not in text


def test_the_deep_lane_gets_the_global_level(home: Path) -> None:
    reg = default_registry(home, deep=HandoffRecorder())
    head, _ = _get(reg, 5, lane="deep")
    assert head["spoil"] == "0"
    reg = default_registry(home, deep=HandoffRecorder(), spoiler_default=1)
    head, _ = _get(reg, 5, lane="deep")
    assert head["spoil"] == "1"
    out = reg.call("quest_get", {"id": q(5)}, lane="deep")  # no spoil: the global level
    assert parse(out.text).head["spoil"] == "1"


def test_db_sql_reads_what_a_quest_is_only_at_the_full_level(home: Path) -> None:
    """The deep lane's db_sql can't go round the quest tools: below the full level it refuses
    quest names, descriptions, tasks, rewards and the quest text index (D268), and still reads
    the quest graph and states."""
    reg = default_registry(home, deep=HandoffRecorder())
    refused = [
        "SELECT name FROM quest",
        "SELECT id, descr FROM quest WHERE id = 'x'",
        "SELECT * FROM quest",
        "SELECT id FROM quest WHERE raw_json LIKE '%a%'",
        "SELECT id, task_logic FROM quest",  # quest_get shows it from level 1
        "SELECT id FROM quest WHERE repeat_time >= 0",
        "SELECT quest_id, amount FROM quest_item",
        "SELECT count(*) FROM quest_task",
        "SELECT * FROM quest_reward",
        "SELECT quest_id FROM quest_fts WHERE quest_fts MATCH 'chromium'",
    ]
    for select in refused:
        out = reg.call("db_sql", {"select": select}, lane="deep")
        assert out.is_error and "spoiler level" in out.text, select
    for select in (
        "SELECT id, main, line_id FROM quest",
        "SELECT quest_id, prereq_id FROM quest_prereq",
        "SELECT quest_id, state FROM quest_state",
        "SELECT name FROM quest_line",
    ):
        out = reg.call("db_sql", {"select": select}, lane="deep")
        assert not out.is_error, (select, out.text)
    for full in (
        default_registry(home, deep=HandoffRecorder(), spoiler_default=2),
        reg,  # the shell (gos tool): his own command
    ):
        lane = "deep" if full is not reg else None
        out = full.call("db_sql", {"select": "SELECT descr FROM quest"}, lane=lane)
        assert not out.is_error and "chromium" in out.text


def test_his_tap_and_his_shell_may_go_to_full(reg: Registry) -> None:
    head, _ = _get(reg, 5, lane="fast", spoil_max=2)  # the dashboard's tap
    assert head["spoil"] == "2"
    head, _ = _get(reg, 5)  # gos tool
    assert head["spoil"] == "2"
    out = reg.call("quest_get", {"id": q(5)})  # no spoil asked: the global level
    assert parse(out.text).head["spoil"] == "0"


def test_quest_for_item_hides_a_locked_quests_amount_at_level_0(reg: Registry) -> None:
    said = "which quest asks for the electric blast furnace"
    out = reg.call(
        "quest_for_item", {"item": "electric blast furnace"}, lane="fast", request=said
    )
    rows = parse(out.text).rows
    assert {r["quest"] for r in rows} == {q(15), q(22)}
    assert all(r["amount"] == "" and r["consume"] == "" for r in rows)


def test_l0_list_tools_are_no_oracle_for_what_a_locked_quest_holds(
    reg: Registry,
) -> None:
    """The final review (D268): for the plain L0 request "what comes after stainless dreams",
    a model that picks item keys and words itself (what q5 asks for and gives, its
    description's "chromium") learns nothing about q5 from quest_for_item or quest_find."""
    said = "what comes after stainless dreams"
    for item in (
        "gregtech:gt.metaitem.01@11306",
        "gregtech:gt.metaitem.01@17306",
        "gregtech:gt.metaitem.01@2306",
        "stainless steel ingot",
    ):
        out = reg.call("quest_for_item", {"item": item}, lane="fast", request=said)
        assert not out.is_error and q(5) not in out.text, item
    for words in ("chromium", "secret alloy", "secret", "stainless"):
        out = reg.call("quest_find", {"text": words}, lane="fast", request=said)
        assert not out.is_error and q(5) not in out.text, words


def test_a_short_word_he_said_is_no_prefix_probe(reg: Registry) -> None:
    """Low (D268): his_words lets quest_find match a locked quest's name by his own words, as
    whole words: "a" in his request doesn't find "Secret Alloy" by prefix."""
    said = "what do i need for a steam age"
    out = reg.call("quest_find", {"text": "a"}, lane="fast", request=said)
    assert q(5) not in out.text and q(7) not in out.text
    out = reg.call("quest_find", {"text": "secret alloy"}, lane="fast",
                   request="is there a secret alloy quest")  # fmt: skip
    assert q(5) in out.text and "Secret Alloy" not in out.text  # by id, name hidden


def test_prerequisites_are_ordered_by_id_never_by_a_hidden_name(reg: Registry) -> None:
    out = reg.call("quest_get", {"id": q(13)}, lane="fast")
    rows = [r["cite"] for r in parse(out.text).rows if r["part"] == "prereq"]
    assert rows == sorted([q(3), q(10)])


# --- 1-3: requirements, the ranked frontier, links -----------------------------------------


def test_the_frontier_is_ranked_by_goals_then_lines(
    conn: sqlite3.Connection, home: Path
) -> None:
    ranked = guide.ranked(conn, VaultIO(home / "vault"), access.db_path(home))
    assert [r.id for r in ranked] == [PACK.qid(n) for n in FRONTIER]
    why = {r.name: r.why for r in ranked}
    assert why["Bricks"][0] == "goal: Electric Blast Furnace (5 steps)"
    assert why["Oxygen Run"][0] == "goal: get some oxygen (this quest)"
    assert why["Steel Age"][:2] == ["line: Steel ingots (plan:pl-steel)", "main quest"]
    assert why["Charcoal Pile"] == []


def test_with_no_goals_or_lines_main_quests_then_unlocks_then_book_order(
    conn: sqlite3.Connection,
) -> None:
    ranked = guide.rank_frontier(conn, PACK.CLAYTON)
    # Steel Age is main; Oxygen Run and Bricks unlock one quest each; then book order
    assert [r.name for r in ranked] == [
        "Steel Age",
        "Oxygen Run",
        "Bricks",
        "Charcoal Pile",
        "Either Way",
    ]


def test_goal_quests(conn: sqlite3.Connection) -> None:
    assert guide.goal_quests(conn, f"finish {q(14)} soon") == {PACK.qid(14)}
    assert guide.goal_quests(conn, "Electric Blast Furnace") == {PACK.qid(22)}
    assert guide.goal_quests(conn, "the bricks") == {PACK.qid(11)}  # a quest's name
    assert (
        guide.goal_quests(conn, "reach LV") == set()
    )  # 2.9.0's key quests: not this book
    assert guide.goal_quests(conn, "get some") == set()  # only verbs and fillers


def test_only_accepted_building_and_running_plans_are_active(
    conn: sqlite3.Connection, home: Path
) -> None:
    lines = guide.active_lines(conn, access.db_path(home))
    assert [(a.plan, a.name) for a in lines] == [("pl-steel", "Steel ingots")]
    assert "gregtech:gt.metaitem.01@11305" in lines[0].keys
    mem = sqlite3.connect(":memory:")
    mem.execute("CREATE TABLE plan(id, name, created, status, target)")
    mem.executemany(
        "INSERT INTO plan VALUES (?, ?, 1, ?, 'x@1')",
        [(s, s, s) for s in ("proposed", "accepted", "building", "running", "retired")],
    )
    assert [a.plan for a in guide.active_lines(mem, None)] == [
        "accepted",
        "building",
        "running",
    ]


def test_item_links_say_consumed_or_not(conn: sqlite3.Connection) -> None:
    links = guide.item_links(conn, [PACK.qid(2), PACK.qid(3)])
    steel = links[PACK.qid(2)]
    assert [(lk.part, lk.key, lk.name, lk.amount, lk.consume) for lk in steel] == [
        ("task", "gregtech:gt.metaitem.01@11305", "Steel Ingot", 16, True),
        ("reward", "gregtech:gt.metaitem.01@2305", "Steel Dust", 4, None),
    ]
    assert [(lk.key, lk.amount) for lk in links[PACK.qid(3)]] == [
        ("fluid:oxygen", 1000)
    ]


# --- 4: questline summaries ------------------------------------------------------------------


def test_summaries_are_cached_notes_that_never_name_a_locked_quest(home: Path) -> None:
    root = home / "vault"
    notes = sorted(p.name for p in (root / "quests").glob("qs-*.md"))
    assert notes == ["qs-steam.md", "qs-stone-age.md", "qs-tier-1-lv.md"]
    types = schema.load_types(root / "_gos" / "schema.md")
    for name in notes:
        text = (root / "quests" / name).read_text(encoding="utf-8")
        assert schema.check_text(f"quests/{name}", text, types) == []
        assert len(text.splitlines()) <= summaries.MAX_LINES
    steam = (root / "quests" / "qs-steam.md").read_text(encoding="utf-8")
    assert "summary: quest line Steam: 1 of 6 done, 3 to start now" in steam
    assert "locked: 2" in steam and "Blast Off" not in steam and "Pressure" not in steam
    assert f"{q(11)}\tBricks\tavailable" in steam
    again = summaries.refresh_home(home)
    assert again is not None and again.written == [] and again.unchanged == 3


def test_a_long_line_keeps_to_60_lines_and_names_stay_one_cell() -> None:
    many = [(f"{n:022d}", f"Quest\t{n}\nnext") for n in range(200)]
    s = guide.LineSummary("x", "Big\tLine", 0, 300, 10, 200, 90, many, 3, "2026-11-12")
    body = summaries.body(s)
    assert len(body) + 5 <= summaries.MAX_LINES
    assert body[-1].startswith("note: ") and "more frontier quests" in body[-1]
    rows = [ln for ln in body if ln.startswith("q:")]
    assert rows and all(ln.count("\t") == 2 for ln in rows)
    assert "line: Big Line" in body


def test_same_named_lines_get_numbered_paths() -> None:
    def line(name: str) -> guide.LineSummary:
        return guide.LineSummary(name, name, 0, 0, 0, 0, 0, [], 0, "")

    paths = summaries.paths([line("A b"), line("a-B"), line("!!")])
    assert paths == ["quests/qs-a-b.md", "quests/qs-a-b-2.md", "quests/qs-line.md"]


def test_the_curator_refreshes_the_summaries(home: Path, tmp_path: Path) -> None:
    (home / "vault" / "quests" / "qs-steam.md").unlink()
    cur = curator.Curator(home / "vault", access.db_path(home), tmp_path / "b")
    [result] = cur.run(curator.dt.date(2026, 11, 12), ["quests"])
    assert (result.status, result.detail) == ("ok", "1 written, 2 unchanged")
    assert (home / "vault" / "quests" / "qs-steam.md").is_file()


def test_gos_quests_summaries(home: Path, tmp_path: Path) -> None:
    path = tmp_path / "gos.toml"
    path.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    out = io.StringIO()
    assert summaries.main(path, out) == 0
    assert "0 written, 3 unchanged" in out.getvalue()
    path.write_text(f'[paths]\ngos_home = "{(tmp_path / "x").as_posix()}"\n', "utf-8")
    out = io.StringIO()
    assert summaries.main(path, out) == 1 and "not written" in out.getvalue()


# --- the Quests view's payload ---------------------------------------------------------------


def _payload(home: Path, level: int) -> dict[str, object]:
    return view.read(home, level, 0)


def test_the_view_payload(home: Path) -> None:
    p = _payload(home, 0)
    front = p["frontier"]
    assert isinstance(front, dict)
    assert [x["ref"] for x in front["quests"]] == [q(n) for n in FRONTIER]
    bricks = front["quests"][0]
    assert bricks["rows"][0] == {
        "part": "why",
        "what": "goal: Electric Blast Furnace (5 steps)",
        "kind": None,
        "amount": None,
        "consume": None,
        "state": None,
        "ref": None,
    }
    assert p["goals"] == list(PACK.GOALS)
    links = p["links"]
    assert isinstance(links, dict)
    assert links[q(11)] == [
        {
            "part": "task",
            "key": "gregtech:gt.blockmachines@140",
            "name": "Bricked Blast Furnace",
            "amount": 1,
            "consume": False,
        }
    ]
    beyond = p["beyond"]
    assert isinstance(beyond, dict)
    assert [x["ref"] for x in beyond["quests"]] == [
        q(15 - 1),
        q(4),
    ]  # Blast Off, then 4
    shown = {x["ref"]: x for x in beyond["quests"]}
    assert shown[q(14)]["name"] == "Blast Off"  # the book previews it
    assert [r["part"] for r in shown[q(14)]["rows"]] == ["after"]
    assert [ln["name"] for ln in p["lines"]] == ["Stone Age", "Steam", "Tier 1 - LV"]  # type: ignore[index]


def test_after_rows_carry_the_frontier_quests_own_state(
    conn: sqlite3.Connection,
) -> None:
    from gregtec.db import quests as quest_db

    player = quest_db.tracked_player(conn)
    states = {r.id: r.state for r in guide.rank_frontier(conn, player)}
    first = next(iter(states))
    states[first] = "repeatable"  # a repeatable quest stays on the frontier
    after = [
        (r["ref"], r["state"])
        for x in view.beyond(conn, player, states, 0)
        for r in x["rows"]
        if r["part"] == "after"
    ]
    assert after
    assert all(state == states[ref.removeprefix("q:")] for ref, state in after)
    assert (f"q:{first}", "repeatable") in after


def test_the_view_at_levels_1_and_2(home: Path) -> None:
    one = {x["ref"]: x for x in _payload(home, 1)["beyond"]["quests"]}  # type: ignore[index]
    assert [r["part"] for r in one[q(14)]["rows"]] == ["after", "task"]
    assert one[q(14)]["description"] is None
    two = {x["ref"]: x for x in _payload(home, 2)["beyond"]["quests"]}  # type: ignore[index]
    assert two[q(14)]["description"] == "Plates for the furnace walls."


def _name_taps(reg: Registry, beyond: dict[str, object]) -> dict[str, tuple[int, str]]:
    """What the renderer's name taps open (renderers.tsx: openQuest(q.ref, q.spoil ?? 0)),
    through the dashboard's own call (dashboard.run_tool: spoil_max=FULL)."""
    out = {}
    for x in beyond["quests"]:  # type: ignore[attr-defined]
        if x["name"] is None:
            continue  # the blurred button opens at 0
        level = x["spoil"] if x["spoil"] is not None else 0
        got = reg.call(
            "quest_get",
            {"id": x["ref"].removeprefix("q:"), "spoil": level},
            lane="fast",
            spoil_max=spoilers.FULL,
        )
        assert not got.is_error, got.text
        out[x["ref"]] = (level, got.text)
    return out


@pytest.mark.parametrize("level", [spoilers.PARTIAL, spoilers.FULL])
def test_view_grant_for_one_quest_lets_no_name_tap_open_another(
    home: Path, reg: Registry, fields: dict[str, questeval.Spoiler], level: int
) -> None:
    """B1: "spoil stainless dreams" (or "a bit more") grants q4 only. The Beyond card is at the
    global level, each quest carries its own, and a tap on Blast Off's name (q14: locked, named
    by the book) opens it at 0, not at q4's grant."""
    p = view.read(home, level, spoilers.NUDGE, frozenset({q(4)}))
    beyond = p["beyond"]
    assert isinstance(beyond, dict)
    assert beyond["spoil"] == spoilers.NUDGE
    own = {x["ref"]: x["spoil"] for x in beyond["quests"]}
    assert own == {q(14): spoilers.NUDGE, q(4): level}
    taps = _name_taps(reg, beyond)
    assert set(taps) == {q(4), q(14)}
    assert taps[q(14)][0] == spoilers.NUDGE
    assert parse(taps[q(14)][1]).head["spoil"] == "0"
    allow = {q(4): level}
    for ref, (_, text) in taps.items():
        assert (
            questeval.leaks(text, fields, allow=allow, said=list(PACK.GOALS)) == []
        ), ref
    assert "Plates for the furnace walls." not in taps[q(14)][1]


def test_an_empty_open_keeps_every_beyond_quest_and_the_card_at_the_global_level(
    home: Path, reg: Registry, fields: dict[str, questeval.Spoiler]
) -> None:
    beyond = view.read(home, spoilers.FULL, spoilers.NUDGE, frozenset())["beyond"]
    assert isinstance(beyond, dict)
    assert beyond["spoil"] == spoilers.NUDGE
    assert {x["spoil"] for x in beyond["quests"]} == {spoilers.NUDGE}
    for ref, (_, text) in _name_taps(reg, beyond).items():
        assert questeval.leaks(text, fields, said=list(PACK.GOALS)) == [], ref
    # without ``open`` (his own "Spoil it" in the view) every quest is at the level he chose
    beyond = view.read(home, spoilers.FULL, spoilers.NUDGE)["beyond"]
    assert isinstance(beyond, dict)
    assert beyond["spoil"] == spoilers.FULL
    assert {x["spoil"] for x in beyond["quests"]} == {spoilers.FULL}
    assert {x["spoil"] for x in view.read(home, 0, 0)["frontier"]["quests"]} == {None}  # type: ignore[index]


def _tap(reg: Registry, tool: str, args: dict[str, object]) -> tuple[str, str]:
    """The dashboard's /api/tools call (dashboard.run_tool: spoil_max=FULL): the tool's text,
    and that text with the card it returns."""
    import json

    from gregtec.cards import build as cards_build

    out = reg.call(tool, args, lane="fast", spoil_max=spoilers.FULL)
    card = cards_build.from_tool(tool, out.text, out.is_error, reg.env.db)
    return out.text, out.text + "\n" + json.dumps(card, ensure_ascii=False)


@pytest.mark.parametrize("level", [spoilers.PARTIAL, spoilers.FULL])
def test_a_step_up_tap_never_names_a_hidden_prerequisite(
    reg: Registry, fields: dict[str, questeval.Spoiler], level: int
) -> None:
    """The final review: the modal's "What does it ask for?" on Hidden Cache (q6) is
    quest_get {q6, spoil 1} with spoil_max FULL; its prerequisite Secret Alloy (q5, locked,
    unshown) stays at its own level, as through the chat path."""
    head, _ = _get(reg, 6, lane="fast", spoil_max=spoilers.FULL)
    assert head["spoil"] == "2" and head["name"] == "Hidden Cache"
    _, text = _tap(reg, "quest_get", {"id": q(6), "spoil": level})
    assert "Secret Alloy" not in text
    assert questeval.leaks(text, fields, allow={q(6): level}) == []


def test_no_dashboard_tool_call_shows_another_locked_quest_above_its_level(
    reg: Registry, conn: sqlite3.Connection, fields: dict[str, questeval.Spoiler]
) -> None:
    """The class (D268): every quest tool the dashboard reaches through /api/tools, at every
    level asked for every locked quest, shows no OTHER locked quest above its own level."""
    from gregtec.api.dashboard import TOOLS

    quest_tools = sorted(t for t in TOOLS if t.startswith("quest_"))
    assert quest_tools == [
        "quest_find",
        "quest_for_item",
        "quest_frontier",
        "quest_get",
    ]
    GOALS = list(PACK.GOALS)  # his own words (the frontier's "why")
    for ref in fields:
        qid = ref.removeprefix("q:")
        name = conn.execute("SELECT name FROM quest WHERE id = ?", (qid,)).fetchone()[0]
        keys = sorted(
            f"{r}@{m}"
            for r, m in conn.execute(
                "SELECT registry_name, meta FROM quest_item WHERE quest_id = ? "
                "AND registry_name IS NOT NULL",
                (qid,),
            )
        )
        for level in (spoilers.NUDGE, spoilers.PARTIAL, spoilers.FULL):
            calls: list[tuple[str, dict[str, object]]] = [
                ("quest_get", {"id": ref, "spoil": level}),
                ("quest_find", {"text": name}),
                ("quest_frontier", {}),
                *(("quest_for_item", {"item": k}) for k in keys),
            ]
            for tool, args in calls:
                raw, text = _tap(reg, tool, args)
                # what he typed or tapped, echoed back (the item as it resolved), is his own
                said = GOALS + [str(v) for v in args.values()]
                said.append(parse(raw).head.get("item", "").rsplit(" [", 1)[0])
                leaks = questeval.leaks(text, fields, allow={ref: level}, said=said)
                assert leaks == [], (ref, level, tool, args)


def test_quest_for_item_orders_a_locked_unshown_quest_by_id(
    home: Path, tmp_path: Path
) -> None:
    """Low (D268): a hidden name must not show through the order of quest_for_item's rows.
    With Welcome to LV (q20) also asking for the EBF, it comes before EBF Time (q22) by id,
    not after it by name."""
    from gregtec.db import quests as quest_db

    c = sqlite3.connect(tmp_path / "gregtec.sqlite")
    try:
        src = access.connect(access.db_path(home), readonly=True)
        src.backup(c)
        src.close()
        c.execute(
            "INSERT INTO quest_item (quest_id, part, idx, slot, registry_name, meta, "
            "nbt_hash, amount) VALUES (?, 'task', 9, 0, 'gregtech:gt.blockmachines', "
            "1000, '', 1)",
            (_id(20),),
        )
        c.row_factory = sqlite3.Row
        params = {"registry": "gregtech:gt.blockmachines", "meta": 1000, "ores": "[]",
                  "fluid": None, "named": 1, "player": quest_db.tracked_player(c),
                  "limit": 10, "offset": 0}  # fmt: skip

        def order(level: int) -> list[str]:
            found = access.query(c, "quests_for_item", {**params, "level": level})
            return [f"q:{r['quest_id']}" for r in found if r["part"] == "task"]

        assert order(spoilers.NUDGE) == [q(20), q(22)]
        assert order(spoilers.PARTIAL) == [q(22), q(20)]  # names shown: by name
    finally:
        c.close()


# --- acceptance: L0 never spoils ------------------------------------------------------------


def _words(
    fields: dict[str, questeval.Spoiler], qref: str, conn: sqlite3.Connection
) -> str:
    return conn.execute(
        "SELECT name FROM quest WHERE id = ?", (qref.removeprefix("q:"),)
    ).fetchone()[0]


def test_l0_never_spoils_the_view_payload(
    home: Path, fields: dict[str, questeval.Spoiler]
) -> None:
    import json

    p = _payload(home, 0)
    text = json.dumps(p, ensure_ascii=False)
    assert set(fields) == {q(n) for n in LOCKED}
    assert questeval.leaks(text, fields, said=list(PACK.GOALS)) == []


def test_l0_never_spoils_an_answer(
    home: Path, conn: sqlite3.Connection, fields: dict[str, questeval.Spoiler]
) -> None:
    """For every quest past the frontier: asked about by name, by what it needs, by what it
    unlocks, and by every item it asks for or gives, the L0 answer (from a model that asks for
    the full spoiler every time and repeats all it reads) and its card hold none of its
    spoiler fields."""
    rows: list[questeval.Question] = []
    for n, qref in enumerate(sorted(fields)):
        name = _words(fields, qref, conn)
        for k, (kind, question) in enumerate(
            [
                ("quest", f"tell me about the {name} quest"),
                ("unlocks", f"what comes after the {name} quest"),
                ("quest", f"how do i get to the {name} quest"),
                (
                    "unlocks",
                    f"I don't want you to spoil it, but what comes after the {name} quest",
                ),
                ("quest", f"no need to spoil it, just a hint: the {name} quest?"),
                ("quest", f"would the {name} quest spoil the surprise?"),
                # round 2's adversarial rows (D268): no apostrophe, "everything" about
                # something else, a conditional, a quotation, an STT run-on
                (
                    "unlocks",
                    f"I dont want the full answer, what comes after the {name} quest",
                ),
                (
                    "unlocks",
                    f"dont tell me everything, but what comes after the {name} quest",
                ),
                (
                    "unlocks",
                    f"show me everything i can make, and what comes after the {name} quest",
                ),
                ("quest", f"if i get stuck, spoil it: the {name} quest"),
                ("quest", f'what does "spoil it" do for the {name} quest'),
                ("quest", f"what does the {name} quest need"),
                ("quest", f"i cant handle full spoilers yet so whats the {name} quest"),
                # the final review's rows (D268): a caveat, a condition or a retraction after
                # the command
                (
                    "unlocks",
                    (
                        "full answer, unless it ruins the surprise: what comes after the "
                        f"{name} quest"
                    ),
                ),
                (
                    "unlocks",
                    f"spoil it, if i get stuck later. what comes after the {name} quest",
                ),
                (
                    "unlocks",
                    f"spoil it, just kidding. what comes after the {name} quest",
                ),
                ("quest", f"tell me about the {name} quest. spoil it? no"),
                (
                    "quest",
                    (
                        f"tell me about the {name} quest. spoil it... nevermind, i want to "
                        "work it out"
                    ),
                ),
                ("quest", f"spoil the {name} quest, if i get stuck"),
                ("quest", f"would you spoil the {name} quest, if i asked"),
                # round 4 (B1-B5, D268 R1/R2): a question about the command, a condition or
                # reported text before it, a command with a question in the same utterance,
                # and conversations whose "spoil it" has no card of his to go by
                ("quest", f"spoil it? what does the {name} quest do"),
                ("quest", f"in case i get stuck. spoil it: the {name} quest"),
                (
                    "quest",
                    f"the quest description reads: spoil it. what does the {name} quest mean",
                ),
                ("unlocks", f"what comes after the {name} quest? spoil it"),
                ("unlocks", "what comes after stainless dreams? spoil it"),
                ("quest", f"my friend: spoil the {name} quest"),
                ("quest", f"how am i doing overall{questeval.TURNS}spoil it"),
                ("quest", f"what does the {name} quest need{questeval.TURNS}spoil it"),
                (
                    "quest",
                    f"what does the {name} quest need{questeval.TURNS}full answer",
                ),
                # B4: a scoped "spoil it" opens the quest his card showed, nothing it unlocks
                ("scoped", f"tell me about the {name} quest{questeval.TURNS}spoil it"),
                ("scoped", f"what is the {name} quest{questeval.TURNS}a bit more"),
            ]
        ):
            rows.append(
                questeval.Question(
                    f"{n}-{k}" + ("-scoped" if kind == "scoped" else ""),
                    "quest" if kind == "scoped" else kind,
                    0,
                    (),
                    (),
                    name.casefold(),
                    question,
                )
            )
        items = conn.execute(
            "SELECT DISTINCT coalesce(i.name_en, f.name_en) FROM quest_item AS qi"
            " LEFT JOIN item AS i ON i.id = qi.item_id LEFT JOIN fluid AS f"
            " ON f.id = qi.fluid_id WHERE qi.quest_id = ?",
            (qref.removeprefix("q:"),),
        ).fetchall()
        for m, (item,) in enumerate(items):
            for verb in ("need", "give"):
                rows.append(
                    questeval.Question(
                        f"{n}-i{m}{verb}", "item", 0, (), (), item.casefold(),
                        f"which quests {verb} {item.casefold()}",
                    )
                )  # fmt: skip
    assert len(rows) > 40
    asker_for = questeval.fake_asker(home, rows)
    goals = list(PACK.GOALS)
    scoped = 0
    for row in rows:
        replies = questeval.converse(asker_for(), row)
        allow: dict[str, int] = {}
        if row.id.endswith("-scoped"):
            # the quests the first reply's card showed him, at the level he asked: no others
            last = replies[-1]
            shown = [f"q:{i}" for i in ask.shown_locked(replies[0])]
            assert last.spoil is not None and last.spoil["quests"] == shown, (
                row.question
            )
            allow = dict.fromkeys(shown, last.spoil["level"])
            scoped += 1
        else:
            assert all(r.spoil is None for r in replies), row.question
            if questeval.TURNS not in row.question:
                assert replies[-1].lane == "fast", (row.question, replies[-1].text)
        for reply in replies:
            said = [row.question, row.words, *goals]
            if reply.target:
                said += [reply.target["key"], reply.target["name"]]
            text = reply.text + " " + json.dumps(reply.card or {}, ensure_ascii=False)
            assert questeval.leaks(text, fields, allow, said) == [], row.question
    assert scoped > 0


def test_the_leak_check_finds_a_spoiler(fields: dict[str, questeval.Spoiler]) -> None:
    assert questeval.leaks("It's the Secret Alloy quest", fields) == [
        f"{q(5)}: Secret Alloy"
    ]
    assert questeval.leaks("Secret Alloy", fields, {q(5): 1}) == []
    assert (
        questeval.leaks("The secret is a chromium-rich alloy.", fields, {q(5): 1}) != []
    )
    assert (
        questeval.leaks("The secret is a chromium-rich alloy.", fields, {q(5): 2}) == []
    )
    # his own words don't count
    assert questeval.leaks("the secret alloy", fields, said=["secret alloy"]) == []


# --- "spoil it": the instant command (D268 R2, R3) --------------------------------------------


def test_spoil_it_routes_as_an_instant_command() -> None:
    for said in ("spoil it", "Spoil it.", "ok spoil it", "full answer", "partial answer",
                 "a bit more", "give me a hint", "spoil the secret alloy quest"):  # fmt: skip
        assert router.route(said).intent == router.SPOIL, said
    for said in ("spoil it: what does it need", "what comes after it? spoil it"):
        assert router.route(said).intent != router.SPOIL, said


def test_spoil_it_opens_the_last_cards_locked_quests(reg: Registry) -> None:
    asker = Asker(reg, HandoffRecorder(), None)
    first = asker.ask("spoil it")
    assert first.text == NOTHING_TO_SPOIL and first.spoil is None
    found = asker.ask(
        "tell me about the secret alloy quest"
    )  # no lane: the instant card
    assert found.card is not None and found.card["quests"][0]["name"] is None
    part = asker.ask("partial answer")
    assert part.lane == "instant" and part.card is not None
    assert part.card["spoil"] == 1 and part.card["quests"][0]["name"] == "Secret Alloy"
    assert part.card["quests"][0]["description"] is None
    assert part.spoil == {"level": 1, "quests": [q(5)]}  # R3: the level travels with it
    for said in (
        "spoil it",
        "can you spoil it",
        "hey gos spoil it",
        "um, spoil it pls",
    ):
        full = asker.ask(said)
        assert full.card is not None and full.card["spoil"] == 2, said
        assert "chromium" in (full.card["quests"][0]["description"] or ""), said
        assert full.refs == [q(5)] and "Secret Alloy" in full.text, said
        assert full.spoil == {"level": 2, "quests": [q(5)]}, said
        # its card's unlocks rows still hide what comes next (B4)
        rows = full.card["quests"][0]["rows"]
        assert {r["what"] for r in rows if r["part"] == "unlocks"} == {
            "(not shown in the quest book yet)"
        }
        asker.ask("tell me about the secret alloy quest")  # the next one starts locked


def test_spoil_it_never_opens_quests_the_model_cited(reg: Registry) -> None:
    """B5: on a general question the model looks up q22 and q6 and cites them; "spoil it"
    after it has no card of his to go by, so it asks which quest."""
    lane = FastLane(
        ScriptedClient(
            turns=[
                Turn(
                    "Looking.",
                    [{"name": "quest_get", "input": {"id": q(22), "spoil": 2}}],
                ),
                Turn(
                    "Looking.",
                    [{"name": "quest_get", "input": {"id": q(6), "spoil": 2}}],
                ),
                Turn(f"Keep going with Bricks; the road ends at {q(22)} and {q(6)}."),
            ]
        ),
        reg,
        meter=None,
    )
    asker = Asker(reg, HandoffRecorder(), lane)
    first = asker.ask("how am i doing overall")
    assert first.lane == "fast" and q(22) in first.refs
    for said in ("spoil it", "full answer", "a bit more"):
        spoiled = asker.ask(said)
        text = json.dumps(spoiled.card or {}) + spoiled.text
        assert spoiled.text == NOTHING_TO_SPOIL and spoiled.spoil is None, said
        assert "EBF Time" not in text and "Hidden Cache" not in text, said


def test_spoil_a_named_quest(reg: Registry) -> None:
    asker = Asker(reg, HandoffRecorder(), None)
    reply = asker.ask("spoil the ebf time quest")
    assert reply.spoil == {"level": 2, "quests": [q(22)]}
    assert reply.card is not None and "changes everything" in json.dumps(reply.card)
    # what it comes after stays at the global level
    assert "Macerate Everything" not in json.dumps(reply.card)
    by_id = asker.ask(f"spoil {q(6)}")
    assert by_id.spoil == {"level": 2, "quests": [q(6)]}
    none = asker.ask("spoil the dragon quest")
    assert none.asked and none.spoil is None and "dragon" in none.text
    # a bare command after it goes by the card that reply showed: the quest it named
    asker.ask("spoil hidden cache")
    again = asker.ask("a bit more")
    assert again.spoil == {"level": 1, "quests": [q(6)]}


def test_spoil_it_asks_which_when_the_card_showed_many(home: Path) -> None:
    reg = default_registry(home, deep=HandoffRecorder())
    asker = Asker(reg, HandoffRecorder(), None)
    card = {"type": "quest", "quests": [
        {"ref": q(n), "state": "locked"} for n in (5, 6, 7, 20)
    ]}  # fmt: skip
    asker._last = Reply("x", "fast", "quests", card=card)
    assert ask.shown_locked(asker._last) == [PACK.qid(n) for n in (5, 6, 7, 20)]
    reply = asker.ask("spoil it")
    assert reply.asked and reply.spoil is None and q(5) in reply.text


def test_a_mention_keeps_the_level_and_says_how(reg: Registry) -> None:
    asker = Asker(reg, HandoffRecorder(), None)
    reply = asker.ask("what comes after the secret alloy quest? spoil it")
    assert reply.text.endswith(spoilers.HOW_TO_ASK) and reply.spoil is None
    assert "chromium" not in reply.text + json.dumps(reply.card or {})
    assert not asker.ask("don't spoil it").text.endswith(spoilers.HOW_TO_ASK)


def _spoiling_lane(reg: Registry, target: int) -> FastLane:
    """B1-B3: a model that calls quest_get(target, spoil=2) and repeats what it got."""
    return FastLane(
        ScriptedClient(
            turns=[
                Turn(
                    "Looking.",
                    [{"name": "quest_get", "input": {"id": q(target), "spoil": 2}}],
                ),
                Turn("The secret is whatever quest_get said."),
            ]
        ),
        reg,
        meter=None,
    )


@pytest.mark.parametrize(
    "said",
    [
        "spoil it? what does that do",
        "the quest description reads: spoil it. what does it mean",
        "in case i get stuck. spoil it",
        "what comes after the secret alloy quest? spoil it",
        "my friend: spoil it",
    ],
)
def test_an_unasked_rise_spoils_nothing(reg: Registry, said: str) -> None:
    asker = Asker(reg, HandoffRecorder(), _spoiling_lane(reg, 5))
    reply = asker.ask(said)
    assert reply.lane == "fast" and reply.spoil is None, reply.text
    out = reg.call("quest_get", {"id": q(5), "spoil": 2}, lane="fast", request=said)
    assert "chromium" not in out.text


# --- the eval -------------------------------------------------------------------------------


def test_the_question_file() -> None:
    qs = questeval.load()
    assert len(qs) == 20
    assert {x.kind for x in qs} == set(questeval.KINDS)
    assert sum(1 for x in qs if x.level) == 3


def test_the_eval_passes_with_the_adversarial_fake(home: Path) -> None:
    qs = questeval.load()
    out = io.StringIO()
    code = questeval.run(
        questeval.fake_asker(home, qs), qs, access.db_path(home), out, fake_model=True
    )
    assert code == 0, out.getvalue()
    assert "passed: 20 of 20" in out.getvalue() and "spoiled: 0" in out.getvalue()


def test_the_eval_fails_a_spoiling_model(home: Path) -> None:
    qs = [x for x in questeval.load() if x.id == "g08"]
    reg = default_registry(home, deep=HandoffRecorder())
    leak = f"Secret Alloy (q:{PACK.qid(5)}): the secret is a chromium-rich alloy."
    lane = FastLane(ScriptedClient(turns=[Turn(leak)]), reg, meter=None)
    out = io.StringIO()
    code = questeval.run(
        lambda: Asker(reg, HandoffRecorder(), lane), qs, access.db_path(home), out,
        fake_model=True,
    )  # fmt: skip
    assert code == 1 and "spoiled" in out.getvalue()
