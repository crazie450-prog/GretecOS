"""WO-8.4 (D362): the hover-item eval (``gos eval hover``): its case file, the item and rows
resolved from the DB, the deterministic scorer and its mutations, the oracle fake model, the
asker's hover context for questions about "this", and the paid run's estimate and caps. On
conftest's self-authored DB (its own run, not pack data); no test calls the API."""

from __future__ import annotations

import io
import re
import shutil
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gregtec import cli
from gregtec.agents import hover_eval, router
from gregtec.agents.ask import Asker, Reply
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane
from gregtec.agents.hover_eval import Case, HoverOracle, Pointing
from gregtec.agents.recipe_eval import Ground
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.gamelink import whatsthis
from gregtec.llm import keys
from gregtec.store import Store

GT = "gregtech:gt.metaitem.01"
MACHINES = "gregtech:gt.blockmachines"
STEEL = f"{GT}@11305"
# conftest's recipes (its own run, not pack data)
EBF_STAINLESS = (
    "r:gt.recipe.blastfurnace/"  # the only recipe of stainless steel (prefix)
)
PBF = (
    "r:gt.recipe.primitiveblastfurnace/efa7aaa493c1"  # iron ingot + 2 charcoal -> steel
)
HEAD = "\t".join(hover_eval.COLUMNS)
LAB = "In Lab, hover over it and ask."
# One case per expectation, on conftest's items
ROWS = [
    ("u01", "whats_this", "item", GT, "11305", "", "What's this?", "name"),
    ("u02", "make", "item", GT, "11305", "", "How do I make this?", "make"),
    ("u03", "uses", "item", "minecraft:iron_ingot", "0", "", "What is this used for?", "use"),
    ("u04", "tier", "item", GT, "11306", "", "Which tier is this?", "tier"),
    ("u05", "ambiguous", "item", "TConstruct:materials", "16", "", "What uses this one?", "use"),
    ("u06", "none", "item", MACHINES, "31", "", "How do I make this?", "none"),
    ("u07", "block", "block", MACHINES, "1000", "", "What am I looking at?", "machine"),
]  # fmt: skip


def write_cases(path: Path, rows: list[tuple[str, ...]] = ROWS) -> Path:
    lines = [HEAD] + ["\t".join([*r, LAB]) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def ground(tool_db: Path) -> Iterator[Ground]:
    g = Ground(tool_db)
    yield g
    g.close()


@pytest.fixture
def lane_reg(tool_db: Path, glossary: Glossary) -> Registry:
    return build_registry(tool_db, glossary, deep=HandoffRecorder())


@pytest.fixture
def cases(tmp_path: Path) -> dict[str, Case]:
    return {c.id: c for c in hover_eval.load(write_cases(tmp_path / "cases.tsv"))}


def oracle_asker(reg: Registry) -> Asker:
    return Asker(reg, HandoffRecorder(), FastLane(HoverOracle(), reg, meter=None))


def ask(reg: Registry, case: Case) -> Reply:
    asker = oracle_asker(reg)
    asker.game = Pointing(case.message())
    return asker.ask(case.question, task=hover_eval.TASK)


def scored(
    ground: Ground, case: Case, reply: Reply, text: str | None = None
) -> list[str]:
    item = hover_eval.resolve(ground, case)
    assert not isinstance(item, str), item
    return hover_eval.score(
        case,
        hover_eval.rows(ground, item),
        reply.text if text is None else text,
        target_key=(reply.target or {}).get("key"),
        asked=reply.asked,
        ground=ground,
    )


# --- the case file ----------------------------------------------------------------------


def test_the_committed_cases_are_plan_9s_ten_and_cover_what_wo_8_4_names() -> None:
    cases = hover_eval.load()
    assert len(cases) == 10
    assert {c.kind for c in cases} == set(hover_eval.KINDS)
    assert {c.expect for c in cases} == set(hover_eval.EXPECTS)
    said = {c.question.casefold().rstrip("?") for c in cases}
    assert {"what's this", "how do i make this", "what is this used for"} <= said
    assert "which tier is this" in said
    nbt = [c for c in cases if "nbt" in c.item]
    assert len(nbt) == 2 and all(isinstance(c.item["nbt"], dict) for c in nbt)
    assert any(c.target == "block" for c in cases)


def test_the_case_file_holds_no_game_number_and_no_numeric_id() -> None:
    text = hover_eval.CASES.read_text(encoding="utf-8")
    assert not re.search(r"\b(?:r|mte|q|w|plan):", text)  # ids come from the DB
    for case in hover_eval.load():
        assert not re.search(r"\d", case.question)  # no number is typed in a question
        # a registry name, never a numeric id; the meta is part of the stable key
        assert re.fullmatch(r"[A-Za-z0-9_.]+:[A-Za-z0-9_.]+", case.item["item"])
        msg = case.message()
        assert msg["type"] == "target" and whatsthis.item_of(msg) is not None
        assert "id" not in keys_of(msg["item"].get("nbt", {}))  # as the contract strips


def keys_of(value: object) -> set[str]:
    """Every key of a JSON value, at any depth."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


@pytest.mark.parametrize(
    ("row", "error"),
    [
        (("x1", "nope", "item", GT, "1", "", "q", "name"), "kind must be"),
        (("x1", "make", "hand", GT, "1", "", "q", "name"), "target must be"),
        (("x1", "make", "item", GT, "1", "", "q", "guess"), "expect must be"),
        (("x1", "make", "item", GT, "one", "", "q", "name"), "meta must be"),
        (("x1", "make", "item", GT, "1", "[1]", "q", "name"), "non-empty JSON object"),
        (("x1", "make", "item", GT, "1", "{", "q", "name"), "isn't JSON"),
        (("x1", "make", "item", "1234", "1", "", "q", "name"), "isn't a registry name"),
        (("x1", "make", "item", GT, "1", "", " ", "name"), "can't be empty"),
    ],
)
def test_a_malformed_case_is_refused(
    tmp_path: Path, row: tuple[str, ...], error: str
) -> None:
    with pytest.raises(hover_eval.EvalError, match=error):
        hover_eval.load(write_cases(tmp_path / "bad.tsv", [row]))


def test_repeated_ids_and_a_bad_header_are_refused(tmp_path: Path) -> None:
    with pytest.raises(hover_eval.EvalError, match="ids repeat"):
        hover_eval.load(write_cases(tmp_path / "dup.tsv", [ROWS[0], ROWS[0]]))
    bad = tmp_path / "head.tsv"
    bad.write_text("id\tkind\n", encoding="utf-8")
    with pytest.raises(hover_eval.EvalError, match="header"):
        hover_eval.load(bad)


# --- resolving from the DB in use -------------------------------------------------------


def test_items_resolve_by_stable_keys_and_a_missing_one_says_so(
    ground: Ground, cases: dict[str, Case]
) -> None:
    got = hover_eval.resolve(ground, cases["u01"])
    assert isinstance(got, hover_eval.Resolved)
    assert (got.key, got.name, got.plain, got.mte) == (STEEL, "Steel Ingot", None, None)
    ebf = hover_eval.resolve(ground, cases["u07"])
    assert isinstance(ebf, hover_eval.Resolved) and ebf.mte == "mte:1000"
    gone = replace(cases["u01"], item={"item": "nope:thing", "meta": 4})
    assert hover_eval.resolve(ground, gone) == f"{hover_eval.NOT_IN_DB}: nope:thing@4"
    # an NBT case is about that variant: the plain item doesn't stand in for it
    charged = replace(
        cases["u01"], item={"item": GT, "meta": 11305, "nbt": {"a": "1b"}}
    )
    assert hover_eval.resolve(ground, charged) == (
        f"{hover_eval.NOT_IN_DB}: {STEEL} with this NBT"
    )
    gone, charged = replace(gone, id="g1"), replace(charged, id="g2")
    assert hover_eval.unresolved(ground, [cases["u01"], gone, charged]) == {
        "g1": f"{hover_eval.NOT_IN_DB}: nope:thing@4",
        "g2": f"{hover_eval.NOT_IN_DB}: {STEEL} with this NBT",
    }


def test_a_variant_s_rows_include_its_plain_and_any_meta_items(ground: Ground) -> None:
    item = hover_eval.Resolved(f"{STEEL}#{'ab' * 32}", "Steel Ingot", STEEL, None)
    held = hover_eval.rows(ground, item)
    assert not held.make.all and not held.use.all  # nothing has that NBT
    assert [t.step.key for t in held.also] == [
        STEEL,
        STEEL,
        f"{GT}@32767",
        f"{GT}@32767",
    ]
    assert PBF in held.citable()  # the plain item's recipe, as "what's this?" names it


def test_same_item_reads_the_tools_cut_hash() -> None:
    full = f"IC2:itemBatRE@1#{'5d64e913' + 'c' * 56}"
    assert hover_eval.same_item("IC2:itemBatRE@1#5d64e913", full)
    assert hover_eval.same_item(full, full)
    assert not hover_eval.same_item("IC2:itemBatRE@1", full)  # the plain item
    assert not hover_eval.same_item("IC2:itemBatRE@1#5d64e914", full)  # another variant
    assert not hover_eval.same_item("IC2:itemBatRE@1#5d64", full)  # too short to tell
    assert not hover_eval.same_item("IC2:itemBatRE@2", "IC2:itemBatRE@1")


# --- the run with the oracle ------------------------------------------------------------


def test_every_unit_case_passes_through_the_asker_with_the_oracle(
    lane_reg: Registry, ground: Ground, cases: dict[str, Case]
) -> None:
    out = io.StringIO()
    code, results = hover_eval.run(
        lambda: oracle_asker(lane_reg),
        list(cases.values()),
        ground,
        out,
        fake_model=True,
    )
    assert code == 0, out.getvalue()
    lanes = {r.case.id: r.reply.lane for r in results if r.reply is not None}
    assert lanes["u01"] == "instant" and lanes["u07"] == "instant"  # "what's this?"
    assert lanes["u02"] == "fast" and lanes["u04"] == "fast"
    assert "passed: 7 of 7" in out.getvalue() and "Result: PASS" in out.getvalue()


def test_a_case_the_db_lacks_is_reported_and_fails_the_run(
    lane_reg: Registry, ground: Ground, cases: dict[str, Case]
) -> None:
    gone = replace(cases["u02"], id="u99", item={"item": "nope:thing", "meta": 4})
    out = io.StringIO()
    code, results = hover_eval.run(
        lambda: oracle_asker(lane_reg),
        [cases["u01"], gone],
        ground,
        out,
        fake_model=True,
    )
    assert code == 1
    assert results[1].reply is None and results[1].why == [
        f"{hover_eval.NOT_IN_DB}: nope:thing@4"
    ]
    text = out.getvalue()
    assert "passed: 1 of 2" in text and f"{hover_eval.NOT_IN_DB}: 1" in text


def test_without_the_hover_context_the_oracle_cannot_answer(
    lane_reg: Registry, ground: Ground, cases: dict[str, Case]
) -> None:
    """The oracle reads only the looked-up rows: with no game link (the code before D362)
    "how do I make this?" names no item, and the scorer fails it."""
    asker = oracle_asker(lane_reg)  # no game link
    reply = asker.ask(cases["u02"].question)
    why = scored(ground, cases["u02"], reply)
    assert why and ("about no item" in why[0] or "not the hovered" in why[0])


def test_the_scorer_s_mutations_each_fail(
    lane_reg: Registry, ground: Ground, cases: dict[str, Case]
) -> None:
    case = cases["u02"]  # how do I make this? (Steel Ingot)
    reply = ask(lane_reg, case)
    assert scored(ground, case, reply) == []
    cited = re.search(r"r:\S+?(?=\))", reply.text)
    assert cited is not None
    ref = cited[0]
    stainless = hover_eval.rows(
        ground, hover_eval.Resolved(f"{GT}@11306", "Stainless Steel Ingot", None, None)
    ).make.all
    other = next(iter(stainless))
    # a wrong citation: a real recipe that neither makes nor takes the hovered item
    why = scored(ground, case, reply, reply.text.replace(ref, other))
    assert any("neither makes nor takes" in w for w in why)
    # an id the DB doesn't have
    why = scored(
        ground,
        case,
        reply,
        reply.text.replace(ref, "r:gt.recipe.blastfurnace/000000000000"),
    )
    assert any("ids the DB doesn't have" in w for w in why)
    # a missing citation
    why = scored(ground, case, reply, reply.text.replace(f" ({ref})", ""))
    assert any("cites no row of make" in w for w in why)
    # an invented number
    why = scored(ground, case, reply, reply.text + " It takes 9999 EU/t.")
    assert any("uncited numbers: 9999" in w for w in why)
    # the wrong item variant: the reply resolved the plain item, not the hovered NBT one
    variant = replace(case, item={**case.item, "nbt": {"a": "1b"}})
    held = hover_eval.rows(
        ground, hover_eval.Resolved(f"{STEEL}#{'ab' * 32}", "Steel Ingot", STEEL, None)
    )
    why = hover_eval.score(
        variant, held, reply.text, target_key=STEEL, asked=False, ground=ground
    )
    assert why and "not the hovered" in why[0]
    # an answer that ignores the hover context: about no item, or about another one
    assert (
        "about no item"
        in hover_eval.score(
            case,
            hover_eval.rows(ground, hover_eval.resolve(ground, case)),  # type: ignore[arg-type]
            reply.text,
            target_key=None,
            asked=False,
            ground=ground,
        )[0]
    )
    why = scored(ground, case, replace(reply, target={"key": f"{GT}@11306"}))
    assert why and "not the hovered" in why[0]
    why = scored(ground, case, reply, "It's made in the Blast Furnace.")
    assert any("doesn't name 'Steel Ingot'" in w for w in why)
    # asking back when the hover says which item
    assert scored(ground, case, replace(reply, asked=True))[0].startswith("asked back")


def test_tier_and_none_and_machine_mutations(
    lane_reg: Registry, ground: Ground, cases: dict[str, Case]
) -> None:
    tier = cases["u04"]
    reply = ask(lane_reg, tier)
    assert scored(ground, tier, reply) == [], reply.text
    stated = next(t for t in ("LV", "MV", "HV", "EV") if f"at {t}" in reply.text)
    wrong = "LuV"
    why = scored(ground, tier, reply, reply.text.replace(stated, wrong))
    assert any("tier" in w for w in why)
    none = cases["u06"]
    reply = ask(lane_reg, none)
    assert scored(ground, none, reply) == [], reply.text
    why = scored(ground, none, reply, reply.text + f" Try {PBF}.")
    assert any("cites recipes where none exists" in w for w in why)
    why = scored(ground, none, reply, "The Basic Macerator: it needs 12 plates.")
    assert any("numbers where no recipe exists" in w for w in why)
    assert any("doesn't say that no recipe" in w for w in why)
    steel_none = replace(none, item={"item": GT, "meta": 11305})
    why = scored(
        ground,
        steel_none,
        replace(reply, target={"key": STEEL}),
        "No recipe in the DB makes the Steel Ingot.",
    )
    assert any("not a none case on this DB" in w for w in why)
    block = cases["u07"]
    reply = ask(lane_reg, block)
    assert scored(ground, block, reply) == [], reply.text
    why = scored(ground, block, reply, reply.text.replace("(mte:1000)", ""))
    assert any("doesn't cite mte:1000" in w for w in why)


def test_the_ambiguous_name_is_answered_for_the_hovered_item(
    lane_reg: Registry, ground: Ground, cases: dict[str, Case]
) -> None:
    """TConstruct's steel ingot shares GT's name; the hover names the exact item, so the
    answer is about it (asked by name, "steel ingot" would resolve to GT's)."""
    case = cases["u05"]
    reply = ask(lane_reg, case)
    assert (reply.target or {}).get("key") == "TConstruct:materials@16"
    assert scored(ground, case, reply) == [], reply.text
    by_name = oracle_asker(lane_reg).ask("What uses the steel ingot?")
    assert (by_name.target or {}).get("key") != "TConstruct:materials@16"


# --- the hover context in the asker (D362) ----------------------------------------------


class Counting(Pointing):
    asked: int = 0

    def current_target(self) -> tuple[dict[str, Any] | None, bool]:
        self.asked += 1
        return super().current_target()


def test_a_question_about_this_takes_the_pointed_at_item(lane_reg: Registry) -> None:
    asker = oracle_asker(lane_reg)
    asker.game = Pointing({"kind": "item", "item": {"item": GT, "meta": 11305}})
    for said in ("how do I make this?", "what is this used for", "which tier is this?"):
        reply = asker.ask(said)
        assert (reply.target or {}).get("key") == STEEL, said
        assert "Steel Ingot" in reply.text
    reply = asker.ask("recipes for that one")  # an instant command
    assert reply.lane == router.INSTANT and (reply.target or {}).get("key") == STEEL


def test_this_with_nothing_pointed_at_says_so_and_other_questions_are_unchanged(
    lane_reg: Registry,
) -> None:
    asker = oracle_asker(lane_reg)
    game = Counting(None)
    asker.game = game
    reply = asker.ask("how do I make this?")
    assert reply.text == whatsthis.NOTHING and reply.lane == router.INSTANT
    game.target = {"kind": "item", "item": {"item": "nope:thing", "meta": 4}}
    assert (
        asker.ask("what is this used for").text
        == "nope:thing@4 isn't in the recipe DB."
    )
    game.target = {"kind": "off"}
    assert asker.ask("how do I make this").text == whatsthis.TARGET_OFF
    asked = game.asked
    reply = asker.ask("how do I make a steel ingot?")  # names its item: no probe
    assert game.asked == asked and (reply.target or {}).get("key") == STEEL
    # a question that only mentions "this" with nothing pointed at goes on as before
    game.target = None
    reply = asker.ask("is that the tier of my base")
    assert reply.text != whatsthis.NOTHING
    # no game link at all (gos ask, the CLI): the router's subject is kept, as before
    asker.game = None
    assert asker.ask("how do I make this?").text != whatsthis.NOTHING


# --- the CLI and the paid run -----------------------------------------------------------


def home_with(tmp_path: Path, tool_db: Path) -> Path:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    shutil.copyfile(tool_db, home / "data" / "gos.sqlite")
    toml = tmp_path / "gos.toml"
    toml.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    return toml


def test_gos_eval_hover_fake(
    tmp_path: Path, tool_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    toml = home_with(tmp_path, tool_db)
    argv = ["eval", "hover", "--fake", "--config", str(toml)]
    assert cli.main([*argv, "--cases", str(write_cases(tmp_path / "c.tsv"))]) == 0
    out = capsys.readouterr().out
    assert "passed: 7 of 7" in out and "(simulated: the fake model)" in out
    # the committed cases are about pack items this self-authored DB lacks: each is said
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    assert f"{hover_eval.NOT_IN_DB}: " in out and "Result: FAIL" in out
    assert not (tmp_path / "home" / "vault").exists()  # nothing written to a vault


def test_the_paid_run_states_its_estimate_first_and_respects_the_caps(
    tmp_path: Path, tool_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    toml = home_with(tmp_path, tool_db)
    cases = write_cases(tmp_path / "c.tsv")
    out = io.StringIO()
    code = hover_eval.main(toml, cases, False, False, None, out, lambda: "no")
    text = out.getvalue()
    assert code == 1 and "Not run." in text
    worst = float(text.split("worst case $")[1].split(",")[0])
    assert 0 < worst < 1 and "an estimate" in text  # seven cases on the fast model

    def missing(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", missing)
    out = io.StringIO()
    assert hover_eval.main(toml, cases, False, True, None, out) == 1
    assert "worst case $" in out.getvalue() and "no Anthropic key" in out.getvalue()
    # the day cap: today's spend plus the estimate may not pass [budget] day_max_usd
    store = Store(tmp_path / "home" / "data" / "gos.sqlite")
    try:
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO llm_usage(ts, lane, model, in_tok, out_tok, cache_read, "
                "cache_write, usd, task) VALUES (?, 'fast', 'x', 0, 0, 0, 0, 9.999, 'other')",
                (int(time.time()),),
            )
    finally:
        store.close()
    out = io.StringIO()
    assert hover_eval.main(toml, cases, False, True, None, out) == 1
    assert "day cap" in out.getvalue() and "no Anthropic key" not in out.getvalue()
    # a case the DB lacks: nothing is stated or spent
    gone = tmp_path / "gone.tsv"
    write_cases(
        gone,
        [("x1", "make", "item", "nope:thing", "4", "", "How do I make this?", "make")],
    )
    out = io.StringIO()
    assert hover_eval.main(toml, gone, False, True, None, out) == 2
    assert "worst case" not in out.getvalue() and hover_eval.NOT_IN_DB in out.getvalue()


def test_the_real_run_stops_at_its_estimate(
    lane_reg: Registry, ground: Ground, cases: dict[str, Case]
) -> None:
    out = io.StringIO()
    code, results = hover_eval.run(
        lambda: oracle_asker(lane_reg),
        list(cases.values()),
        ground,
        out,
        fake_model=False,
        stop_usd=0.0,
    )
    assert code == 1 and results == [] and "# stopped:" in out.getvalue()
