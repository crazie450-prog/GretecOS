"""The quest guide (WO-6.4, plan 7.4, D26, D31, D72, D268-D270) on its fixture pack.

tests/evals/quest_pack.py's book: Clayton has done 1 (First Steps) and 10 (Logs); 2 Steel Age,
3 Oxygen Run, 11 Bricks, 12 Charcoal Pile and 13 Either Way are his frontier; 4 Stainless Dreams
and 14 Blast Off are locked but shown; 5, 6, 7, 15, 20, 21 and 22 are locked and not shown. His
goals are the Electric Blast Furnace (quest 22) and oxygen (quest 3); a steel ingot plan is
accepted (quest 2 asks for steel ingots).
"""

from __future__ import annotations

import io
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec import config
from gregtec.agents import router
from gregtec.agents.ask import NOTHING_TO_SPOIL, Asker
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


# --- spoiler levels ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "level"),
    [
        ("tell me about the secret alloy quest", 0),
        ("what quests can i do", 0),
        ("spoil it", 2),
        ("Spoil it: what does pressure cooker give", 2),
        ("full answer please", 2),
        ("tell me everything about EBF Time", 2),
        ("partial answer", 1),
        ("a bit more", 1),
        ("what does the secret alloy quest need", 1),
        ("what are its requirements", 1),
        ("no spoilers, but what does it need", 0),
        ("don't spoil it", 0),
        ("what's my spoiler level", 0),
        ("", 0),
        (None, 0),
    ],
)
def test_asked_level(said: str | None, level: int) -> None:
    assert spoilers.asked_level(said) == level


def test_allowed_is_the_global_level_raised_only_by_his_words() -> None:
    assert spoilers.allowed("what is it", 0) == 0
    assert spoilers.allowed("what is it", 1) == 1
    assert spoilers.allowed("spoil it", 0) == 2
    assert spoilers.allowed("no spoilers", 2) == 2  # the setting is the floor
    with pytest.raises(ValueError):
        spoilers.allowed("x", 3)
    with pytest.raises(ValueError):
        spoilers.check_level(True)


def test_spoiler_default_is_a_service_key(tmp_path: Path) -> None:
    path = tmp_path / "gos.toml"
    path.write_text("[policy]\nspoiler_default = 2\n", encoding="utf-8")
    assert config.load(path).spoiler_default == 2
    assert config.Config().spoiler_default == 0  # D26: nudge by default
    path.write_text("[policy]\nspoiler_default = 5\n", encoding="utf-8")
    cfg = config.load(path)  # a bad one keeps the default and stops the core, not gos
    assert cfg.spoiler_default == 0
    assert any("spoiler_default" in e for e in cfg.service_errors)


# --- the code's guard: a model can't ask for more than he did -------------------------------


def _get(reg: Registry, n: int, **kw: object) -> tuple[dict[str, str], str]:
    out = reg.call("quest_get", {"id": q(n), "spoil": 2}, **kw)  # type: ignore[arg-type]
    assert not out.is_error, out.text
    return parse(out.text).head, out.text


def test_a_lane_gets_only_the_level_his_words_allow(reg: Registry) -> None:
    head, text = _get(
        reg, 5, lane="fast", request="tell me about the secret alloy quest"
    )
    assert head["spoil"] == "0" and head["name"] == "(not shown in the quest book yet)"
    assert "chromium" not in text and "spoil=2 was not shown" in text
    head, text = _get(
        reg, 5, lane="fast", request="what does the secret alloy quest need"
    )
    assert head["spoil"] == "1" and head["name"] == "Secret Alloy"
    assert "chromium" not in text and "@17306" not in text  # no reward, no description
    head, text = _get(reg, 5, lane="fast", request="spoil it")
    assert head["spoil"] == "2" and "chromium-rich" in text and "@17306" in text


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


def test_spoiling_a_quest_never_names_what_it_unlocks(reg: Registry) -> None:
    _, text = _get(reg, 5, lane="fast", request="spoil it")
    rows = parse(text).rows
    unlocks = [r for r in rows if r["part"] == "unlocks"]
    assert {r["cite"] for r in unlocks} == {q(6), q(7)}
    assert {r["what"] for r in unlocks} == {"(not shown in the quest book yet)"}


def test_quest_for_item_hides_a_locked_quests_amount_at_level_0(reg: Registry) -> None:
    out = reg.call("quest_for_item", {"item": "electric blast furnace"}, lane="fast")
    rows = parse(out.text).rows
    assert {r["quest"] for r in rows} == {q(15), q(22)}
    assert all(r["amount"] == "" and r["consume"] == "" for r in rows)


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


def test_the_view_at_levels_1_and_2(home: Path) -> None:
    one = {x["ref"]: x for x in _payload(home, 1)["beyond"]["quests"]}  # type: ignore[index]
    assert [r["part"] for r in one[q(14)]["rows"]] == ["after", "task"]
    assert one[q(14)]["description"] is None
    two = {x["ref"]: x for x in _payload(home, 2)["beyond"]["quests"]}  # type: ignore[index]
    assert two[q(14)]["description"] == "Plates for the furnace walls."


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
            ]
        ):
            rows.append(
                questeval.Question(
                    f"{n}-{k}", kind, 0, (), (), name.casefold(), question
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
    for row in rows:
        reply = asker_for().ask(row.question)
        assert reply.lane == "fast", (row.question, reply.text)
        said = [row.question, row.words, *goals]
        if reply.target:
            said += [reply.target["key"], reply.target["name"]]
        assert questeval.leaks(reply.text, fields, said=said) == [], row.question
        card = __import__("json").dumps(reply.card or {}, ensure_ascii=False)
        assert questeval.leaks(card, fields, said=said) == [], row.question


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


# --- "spoil it" ----------------------------------------------------------------------------


def test_spoil_it_routes_as_an_instant_command() -> None:
    for said in (
        "spoil it",
        "Spoil it.",
        "ok spoil it",
        "full answer",
        "partial answer",
    ):
        assert router.route(said).intent == router.SPOIL, said
    assert router.route("spoil it: what does it need").intent != router.SPOIL


def test_spoil_it_opens_the_last_answers_locked_quests(reg: Registry) -> None:
    asker = Asker(reg, HandoffRecorder(), None)
    first = asker.ask("spoil it")
    assert first.text == NOTHING_TO_SPOIL
    found = asker.ask(
        "tell me about the secret alloy quest"
    )  # no lane: the instant card
    assert found.card is not None and found.card["quests"][0]["name"] is None
    part = asker.ask("partial answer")
    assert part.lane == "instant" and part.card is not None
    assert part.card["spoil"] == 1 and part.card["quests"][0]["name"] == "Secret Alloy"
    assert part.card["quests"][0]["description"] is None
    full = asker.ask("spoil it")
    assert full.card is not None and full.card["spoil"] == 2
    assert "chromium" in (full.card["quests"][0]["description"] or "")
    assert q(5) in full.refs and "Secret Alloy" in full.text


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
