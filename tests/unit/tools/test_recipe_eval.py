"""WO-2.7: the recipe-lookup eval (question file, truth from the DB, scoring), its oracle fakes,
the recipe card and the skill, on conftest's self-authored DB. No test calls the API; the deep
lane's test runs the real Claude Code CLI against the fake Messages API (as WO-2.4's do)."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from gregtec import cli
from gregtec.agents import cards, deep, deep_config, prefetch, recipe_eval, router
from gregtec.agents.ask import Asker, Reply
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane, system_prompt
from gregtec.agents.recipe_eval import ANSWER, ASK, NONE, Question, Truth
from gregtec.agents.recipe_oracle import (
    FAKE_KEY,
    SKILL,
    OracleClient,
    Seen,
    decide,
    deep_policy,
)
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.agents.tools.tsv import parse
from gregtec.llm import keys
from gregtec.llm.fake_api import FakeMessagesAPI
from gregtec.vault import deploy

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "vault-template"
SKILL_FILE = TEMPLATE / "_gos" / "skills" / "recipe-lookup" / "SKILL.md"
STEEL = "gregtech:gt.metaitem.01@11305"
ASHES = "gregtech:gt.metaitem.01@2815"
IRON_DUST = "gregtech:gt.metaitem.01@2032"
STEEL_DUST = "gregtech:gt.metaitem.01@2305"
SPRUCE = "minecraft:planks@1"  # no recipe makes it in conftest's run
# conftest's recipes (its own run, not pack data)
ARC = "r:gt.recipe.arcfurnace/e51b48c38b9b"  # steel plate + 72 L oxygen, recycling
EBF_O2 = (
    "r:gt.recipe.blastfurnace/9c40869458cb"  # 120 EU/t, 500 t, 1000 L O2, ashes 11.11%
)
EBF_CIRCUIT = "r:gt.recipe.blastfurnace/cd313bfc2e4d"  # 120 EU/t, 1000 t, circuit 1
PBF = (
    "r:gt.recipe.primitiveblastfurnace/efa7aaa493c1"  # 1200 t, iron ingot + 2 charcoal
)
KILN = "r:test.nei.AlloyKilnHandler/07e41a118037"  # NEI-only, fake?
MAC_ORE = "r:gt.recipe.macerator/c04c5d068f50"  # any ore:ingotSteel (or 1 other)
MAC_REC = "r:gt.recipe.macerator/fb0dc76db935"
SHAPED = "r:codechicken.nei.recipe.ShapedRecipeHandler/de3fbba14c24"
WAIT_S = 120.0


@pytest.fixture
def ground(tool_db: Path) -> Iterator[recipe_eval.Ground]:
    g = recipe_eval.Ground(tool_db)
    yield g
    g.close()


@pytest.fixture
def lane_reg(tool_db: Path, glossary: Glossary) -> Registry:
    return build_registry(tool_db, glossary, deep=HandoffRecorder())


def q(
    truth: str, want: str = "", expect: str = ANSWER, kind: str = "recipe"
) -> Question:
    return recipe_eval.question("t1", kind, expect, "a question", truth, want)


def truths(ground: recipe_eval.Ground, question: Question) -> list[Truth]:
    return [ground.truth(s) for s in question.steps]


# --- the question file --------------------------------------------------------------------


def test_the_question_set() -> None:
    qs = recipe_eval.load()
    assert len(qs) == 50  # plan 8: a 50-question eval
    kinds = {k: [x for x in qs if x.kind == k] for k in recipe_eval.KINDS}
    for kind in ("recipe", "uses", "machine", "power", "time", "chance", "fluid"):
        assert len(kinds[kind]) >= 3, kind
    assert len(kinds["oredict"]) >= 3 and len(kinds["none"]) >= 1
    assert len(kinds["ambiguous"]) >= 5
    assert all(x.expect == ASK for x in kinds["ambiguous"])
    multi = kinds["multistep"]
    assert len(multi) >= 3 and all(len(x.steps) >= 2 for x in multi)
    # every kind of DB reference and wanted value appears
    filters = {k for x in qs for s in x.steps for k, _ in s.filters}
    assert filters == set(recipe_eval.FILTERS)
    wants = {w.split("=")[0] for x in qs for s in x.steps for w in s.want}
    assert {"eut", "dur", "tier", "machine", "count", "in", "out", "chance"} <= wants
    assert {"fact", "alts"} <= wants
    assert {s.op for x in qs for s in x.steps} == set(recipe_eval.OPS)


def test_the_file_holds_references_never_game_numbers() -> None:
    """Truth and want cells hold keys, map and oredict names, tiers and field names: with
    those out, no digit is left (a game number would be one)."""
    keys_ = re.compile(
        r"(?:mte|fluid):\S+|[\w.|-]+:[\w.|-]+@\d+|gt\.recipe\.\w+|ore=\S+"
    )
    for line in recipe_eval.QUESTIONS.read_text(encoding="utf-8").splitlines()[1:]:
        cells = line.split("\t")
        rest = keys_.sub(" ", " ".join(cells[4:]))
        assert not re.search(r"\d", rest), line


def test_the_router_places_each_row() -> None:
    lanes = {}
    for x in recipe_eval.load():
        got = router.route(x.question)
        lanes[x.id] = got.kind
        if x.expect == ASK:  # pre-retrieval asks: the router must find the item words
            assert got.kind != router.DEEP and got.subject, x.id
    counts = {k: sum(v == k for v in lanes.values()) for k in set(lanes.values())}
    # the eval goes through every lane: instant commands, the fast lane, the deep lane
    assert counts[router.INSTANT] >= 4 and counts[router.DEEP] >= 2
    assert counts[router.FAST] >= 30


def test_trailing_empty_cells_may_be_missing(tmp_path: Path) -> None:
    path = tmp_path / "q.tsv"
    path.write_text(
        "\t".join(recipe_eval.COLUMNS)
        + "\nx1\trecipe\tanswer\tHow do I make steel?\tmake "
        + STEEL
        + "\nx2\tambiguous\task\tHow do I make dust?\n",
        encoding="utf-8",
    )
    got = recipe_eval.load(path)
    assert [x.id for x in got] == ["x1", "x2"]
    assert got[0].steps[0].key == STEEL and got[1].steps == ()


@pytest.mark.parametrize(
    ("truth", "want", "expect", "error"),
    [
        ("make", "", ANSWER, "a step is"),
        ("craft " + STEEL, "", ANSWER, "a step is"),
        ("make 11305", "", ANSWER, "not an item or fluid key"),
        ("machine 1000", "", ANSWER, "mte:<id>"),
        ("machine mte:1000 map=x", "", ANSWER, "no filters"),
        (f"make {STEEL} machine=EBF", "", ANSWER, "bad filter"),
        (f"make {STEEL} tier=HV2", "", ANSWER, "not a GT tier"),
        (f"make {STEEL} in=steel", "", ANSWER, "not an item or fluid key"),
        (f"make {STEEL}", "fact=parallel", ANSWER, "can't be wanted"),
        ("machine mte:1000", "eut", ANSWER, "can't be wanted"),
        (f"make {STEEL}", "speed", ANSWER, "can't be wanted"),
        (f"make {STEEL}", "chance=ashes", ANSWER, "can't be wanted"),
        (f"make {STEEL}", "eut ; dur", ANSWER, "more want parts"),
        ("", "", ANSWER, "need one"),
        (f"make {STEEL}", "", ASK, "ask rows have no truth"),
        ("machine mte:1000", "", NONE, "one make or use step"),
        (f"make {STEEL} ; make {ASHES}", "", NONE, "one make or use step"),
    ],
)
def test_bad_rows_are_refused(truth: str, want: str, expect: str, error: str) -> None:
    with pytest.raises(recipe_eval.EvalError, match=error):
        recipe_eval.question("x", "recipe", expect, "a question", truth, want)


def test_load_refuses_a_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "q.tsv"
    bad.write_text("id\tquestion\nq1\thi\n", encoding="utf-8")
    with pytest.raises(recipe_eval.EvalError, match="header"):
        recipe_eval.load(bad)
    row = f"x1\trecipe\tanswer\tHi?\tmake {STEEL}\t"
    bad.write_text("\t".join(recipe_eval.COLUMNS) + f"\n{row}\n{row}\n", "utf-8")
    with pytest.raises(recipe_eval.EvalError, match="ids repeat"):
        recipe_eval.load(bad)
    bad.write_text("\t".join(recipe_eval.COLUMNS) + "\nx1\trecipe\n", "utf-8")
    with pytest.raises(recipe_eval.EvalError, match="2 cells"):
        recipe_eval.load(bad)


# --- the truth ----------------------------------------------------------------------------


def _all_refs(reg: Registry, tool: str, args: dict[str, object]) -> list[str]:
    out = reg.call(tool, args)
    refs = [r["ref"] for r in parse(out.text).rows]
    while cursor := next(
        (ln[6:] for ln in out.text.split("\n") if ln.startswith("more: ")), None
    ):
        out = reg.call("more", {"cursor": cursor})
        refs += [r["ref"] for r in parse(out.text).rows]
    return refs


@pytest.mark.parametrize(
    ("truth", "tool", "args"),
    [
        (f"make {STEEL}", "recipe_find", {"item": STEEL}),
        (
            f"make {STEEL} map=gt.recipe.blastfurnace",
            "recipe_find",
            {"item": STEEL, "machine": "gt.recipe.blastfurnace"},
        ),
        (f"make {STEEL} tier=LV", "recipe_find", {"item": STEEL, "tier_max": "LV"}),
        (f"use {STEEL}", "recipe_uses", {"item": STEEL}),
        ("make fluid:oxygen", "recipe_find", {"item": "fluid:oxygen"}),
        ("use fluid:oxygen", "recipe_uses", {"item": "fluid:oxygen"}),
        (f"make {SPRUCE}", "recipe_find", {"item": SPRUCE}),
    ],
)
def test_the_truth_is_the_set_the_tools_mean(
    ground: recipe_eval.Ground,
    lane_reg: Registry,
    truth: str,
    tool: str,
    args: dict[str, object],
) -> None:
    """The truth's own SQL and the tools agree on which rows a lookup means (the tools'
    answers are what the eval checks, so it can't take its truth from them)."""
    [t] = truths(ground, q(truth))
    assert sorted(t.rows) == sorted(_all_refs(lane_reg, tool, args))


def test_filters_narrow_a_step(ground: recipe_eval.Ground) -> None:
    def rows(truth: str) -> set[str]:
        return set(truths(ground, q(truth))[0].rows)

    assert rows(f"make {STEEL}") == {ARC, EBF_O2, EBF_CIRCUIT, PBF, KILN}
    assert rows(f"make {STEEL} map=gt.recipe.blastfurnace") == {EBF_O2, EBF_CIRCUIT}
    assert rows(f"make {STEEL} in=fluid:oxygen") == {ARC, EBF_O2}
    assert rows(f"make {STEEL} in={IRON_DUST} in=fluid:oxygen") == {EBF_O2}
    assert rows(f"make {STEEL} out={ASHES}") == {EBF_O2, PBF}
    assert rows(f"make {STEEL} tier=LV") == {ARC, PBF, KILN}
    assert rows(f"use {STEEL} ore=ingotSteel") == {MAC_ORE}
    # the oredict slot takes TConstruct's steel ingot too: a use of it, through the oredict
    assert rows("use TConstruct:materials@16") == {MAC_ORE}
    assert rows("make minecraft:planks@1") == set()
    assert rows("make gregtech:gt.metaitem.01@99999") == set()  # not in the DB


def test_a_rows_values_are_the_dbs(ground: recipe_eval.Ground) -> None:
    [t] = truths(ground, q(f"make {STEEL} map=gt.recipe.blastfurnace"))
    row = t.rows[EBF_O2]
    assert row.fields["eut"] == {"120"} and row.fields["amps"] == {"1"}
    assert row.fields["dur"] == {"500", "25"}  # ticks and seconds, both exact
    assert row.fields["in=fluid:oxygen"] == {"1000"}
    assert row.fields[f"chance={ASHES}"] == {"11.11"}
    assert row.fields["cond=coil_heat"] == {"1000"}
    assert row.fields["tier"] == {"MV"}
    assert {"Blast Furnace", "Electric Blast Furnace"} <= row.fields["machine"]
    assert {"120", "500", "25", "1000", "11.11", "1"} <= row.numbers
    assert t.counts >= {"2", "0"}  # 2 in all, none at a cap under MV
    [mac] = truths(ground, q(f"use {STEEL} ore=ingotSteel"))
    assert mac.rows[MAC_ORE].fields[f"alts={STEEL}"] == {"1"}  # "(or 1 other)"
    [ebf] = truths(ground, q("machine mte:1000"))
    facts = ebf.rows["mte:1000"].fields
    assert facts["fact=parallel"] == {"1"} and facts["fact=oc"] == {"heat"}
    assert "Electric Blast Furnace" in facts["machine"]
    assert ground.row("mte:99999") is None and ground.row("q:abc") is None


# --- scoring ------------------------------------------------------------------------------

EBF_STEEL = f"make {STEEL} map=gt.recipe.blastfurnace"


@pytest.mark.parametrize(
    ("truth", "want", "text", "passed", "why"),
    [
        (
            EBF_STEEL,
            "eut",
            f"Steel Ingot: the Blast Furnace ({EBF_O2}), 120 EU/t.",
            True,
            "",
        ),
        (
            EBF_STEEL,
            "eut",
            f"Steel Ingot: the Macerator ({MAC_ORE}).",
            False,
            "cites no row",
        ),
        (
            EBF_STEEL,
            "",
            "Steel Ingot: the Blast Furnace (r:gt.recipe.blastfurnace/000000000000).",
            False,
            "the DB doesn't have",
        ),
        (
            EBF_STEEL,
            "eut",
            f"Steel Ingot: ({EBF_O2}) at 480 EU/t.",
            False,
            "doesn't state eut",
        ),
        (
            EBF_STEEL,
            "",
            f"Steel Ingot: ({EBF_O2}) at 480 EU/t.",
            False,
            "uncited numbers: 480",
        ),
        (
            EBF_STEEL,
            "",
            "Steel Ingot takes 120 EU/t in the EBF.",
            False,
            "cites no row",
        ),
        (EBF_STEEL, "dur", f"Steel Ingot: {EBF_O2}, 25 s.", True, ""),
        (EBF_STEEL, "dur", f"Steel Ingot: {EBF_O2}, 500 ticks.", True, ""),
        (
            EBF_STEEL,
            "dur",
            f"Steel Ingot: {EBF_O2}, 1000 ticks.",
            False,
            "doesn't state dur",
        ),
        (EBF_STEEL, "tier", f"Steel Ingot: {EBF_O2}, at MV.", True, ""),
        (
            EBF_STEEL,
            "tier",
            f"Steel Ingot: {EBF_O2}, at LV.",
            False,
            "doesn't state tier",
        ),
        (
            EBF_STEEL,
            "machine",
            f"Steel Ingot: the Electric Blast Furnace ({EBF_O2}).",
            True,
            "",
        ),
        (
            EBF_STEEL,
            "machine",
            f"Steel Ingot: the Macerator ({EBF_O2}).",
            False,
            "machine",
        ),
        (EBF_STEEL, f"chance={ASHES}", f"Ashes: {EBF_O2}, 11.11% chance.", True, ""),
        (
            EBF_STEEL,
            f"chance={ASHES}",
            f"Ashes: {EBF_O2}, 11.1% chance.",
            False,
            "chance",
        ),
        (
            EBF_STEEL,
            "in=fluid:oxygen",
            f"Steel Ingot: {EBF_O2} takes 1000L Oxygen.",
            True,
            "",
        ),
        (
            EBF_STEEL,
            "in=fluid:oxygen",
            f"Steel Ingot: {EBF_O2} takes 1,000 L.",
            True,
            "",
        ),
        (
            EBF_STEEL,
            "cond=coil_heat",
            f"Steel Ingot: {EBF_O2}, coil_heat 1000.",
            True,
            "",
        ),
        (
            f"make {STEEL}",
            "count",
            f"Steel Ingot: 5 recipes in all; the first is {KILN}.",
            True,
            "",
        ),
        (
            f"make {STEEL}",
            "count",
            f"Steel Ingot: 6 recipes in all ({KILN}).",
            False,
            "count",
        ),
        # a count at a tier cap ("at LV": 3) is one the tool can give
        (
            f"make {STEEL}",
            "count",
            f"Steel Ingot: 3 recipes up to LV ({KILN}).",
            True,
            "",
        ),
        # the digits of a name the row holds aren't numbers
        (
            EBF_STEEL,
            "",
            f"Steel Ingot: {EBF_CIRCUIT} with a Programmed Circuit 1.",
            True,
            "",
        ),
        # the deep lane's shape: a spoken sentence without ids, the detail with them
        (
            EBF_STEEL,
            "eut,dur",
            f"Steel Ingot: the Blast Furnace at 120 EU/t for 25 s.\n\nref: {EBF_O2}, eut: 120",
            True,
            "",
        ),
        (
            f"make {STEEL} map=gt.recipe.blastfurnace ; make {STEEL} map=gt.recipe.primitiveblastfurnace",
            "eut ; dur",
            f"EBF ({EBF_O2}) at 120 EU/t; the bricked one ({PBF}) takes 1200 ticks.",
            True,
            "",
        ),
        (
            f"make {STEEL} map=gt.recipe.blastfurnace ; make {STEEL} map=gt.recipe.primitiveblastfurnace",
            "",
            f"Steel Ingot: the EBF ({EBF_O2}).",
            False,
            "cites no row of make",
        ),
    ],
)
def test_score(
    ground: recipe_eval.Ground,
    truth: str,
    want: str,
    text: str,
    passed: bool,
    why: str,
) -> None:
    question = q(truth, want)
    got = recipe_eval.score(question, text, False, truths(ground, question), ground)
    assert got.passed is passed, got
    assert why in got.why


class _Rows:
    """A stand-in for :class:`recipe_eval.Ground` that knows a few rows."""

    def __init__(self, rows: dict[str, recipe_eval.Row]) -> None:
        self.rows = rows

    def row(self, cite: str) -> recipe_eval.Row | None:
        return self.rows.get(cite)


def test_a_name_inside_a_citation_isnt_masked_out_of_it() -> None:
    """A handler id with digits ("ic2.nei...") is both a machine name the row holds and part
    of the recipe's id: masking names must not cut the id apart into stray numbers."""
    cite = "r:ic2.neiIntegration.Handler/f4610ee28395"
    row = recipe_eval.Row(cite, names={"ic2.neiIntegration.Handler"}, numbers={"9"})
    question = q(f"make {STEEL}")
    truth = Truth(question.steps[0], {cite: row}, {"1"})
    ground = cast(recipe_eval.Ground, _Rows({cite: row}))
    text = f"Steel Ingot: 9x from the ic2.neiIntegration.Handler ({cite})."
    got = recipe_eval.score(question, text, False, [truth], ground)
    assert got.passed and got.uncited == [], got


def test_an_extra_real_citation_is_reported_not_failed(
    ground: recipe_eval.Ground,
) -> None:
    question = q(EBF_STEEL, "eut")
    text = f"Steel Ingot: the EBF ({EBF_O2}) at 120 EU/t; the macerator ({MAC_ORE}) takes 2."
    got = recipe_eval.score(question, text, False, truths(ground, question), ground)
    assert got.passed and got.extra == [MAC_ORE] and got.uncited == []


def test_uncited_numbers_are_counted(ground: recipe_eval.Ground) -> None:
    question = q(EBF_STEEL)
    got = recipe_eval.score(
        question,
        "Steel takes 120 EU/t and 25 s.",
        False,
        truths(ground, question),
        ground,
    )
    assert not got.passed and got.uncited == ["25", "120"]


def test_ask_and_none_rows(ground: recipe_eval.Ground) -> None:
    ask = q("", expect=ASK, kind="ambiguous")
    question = "Which dust did you mean: Iron Dust, Steel Dust or Stainless Steel Dust?"
    assert recipe_eval.score(ask, question, True, [], ground).passed
    assert not recipe_eval.score(
        ask, "Steel Dust: macerator.", False, [], ground
    ).passed
    numbered = recipe_eval.score(ask, "Which one: 2 or 3?", True, [], ground)
    assert not numbered.passed and "numbers" in numbered.why
    # a name with digits in it, as the asker shows it, isn't a number
    wire = "Which wire did you mean: 1x Copper Wire or 2x Copper Wire?"
    names = ["1x Copper Wire", "2x Copper Wire"]
    assert recipe_eval.score(ask, wire, True, [], ground, names).passed

    none = q(f"make {SPRUCE}", expect=NONE, kind="none")
    ts = truths(ground, none)
    said = "Spruce Wood Planks: no recipe in the DB makes it."
    assert recipe_eval.score(none, said, False, ts, ground).passed
    cited = recipe_eval.score(none, f"Spruce: {EBF_O2}.", False, ts, ground)
    assert not cited.passed and "where none exists" in cited.why
    made_up = recipe_eval.score(none, "Spruce takes 4 logs.", False, ts, ground)
    assert not made_up.passed and made_up.uncited == ["4"]
    steel = q(f"make {STEEL}", expect=NONE, kind="none")
    here = recipe_eval.score(steel, said, False, truths(ground, steel), ground)
    assert not here.passed and "not a none row here" in here.why


def test_a_question_back_counts_as_asking() -> None:
    reply = Reply("Which steel?", "fast", "recipe")
    assert recipe_eval.asks(reply, "Which steel?")
    assert not recipe_eval.asks(reply, f"Is it {EBF_O2}?")  # cited: an answer
    assert recipe_eval.asks(Reply("x", "instant", "recipe", asked=True), "x")


# --- the run, with the fast lane's oracle --------------------------------------------------

QUESTIONS = [
    ("o1", "recipe", ANSWER, "How do I make steel?", f"make {STEEL}", ""),
    ("o2", "recipe", ANSWER, "recipe for steel", f"make {STEEL}", "count"),
    (
        "o3",
        "power",
        ANSWER,
        "How much EU/t does the EBF need to make steel from iron dust?",
        f"make {STEEL} map=gt.recipe.blastfurnace in={IRON_DUST}",
        "eut,dur",
    ),
    (
        "o4",
        "chance",
        ANSWER,
        "What's the chance of ashes from the EBF's oxygen steel recipe?",
        f"make {STEEL} map=gt.recipe.blastfurnace in=fluid:oxygen",
        f"chance={ASHES},in=fluid:oxygen",
    ),
    (
        "o5",
        "multistep",
        ANSWER,
        "What makes steel dust, and then what makes the steel ingot?",
        f"make {STEEL_DUST} ; make {STEEL} map=gt.recipe.blastfurnace",
        " ; machine",
    ),
    ("o6", "uses", ANSWER, "What is steel used for?", f"use {STEEL}", ""),
    (
        "o7",
        "machine",
        ANSWER,
        "How many parallels does the electric blast furnace have?",
        "machine mte:1000",
        "fact=parallel",
    ),
    ("o8", "ambiguous", ASK, "How do I make dust?", "", ""),
    ("o9", "none", NONE, "How do I make spruce wood planks?", f"make {SPRUCE}", ""),
    (
        "o10",
        "oredict",
        ANSWER,
        "Can any steel ingot go in the macerator?",
        f"use {STEEL} ore=ingotSteel",
        "",
    ),
]


def oracle_questions() -> list[Question]:
    return [recipe_eval.question(*row) for row in QUESTIONS]


def run_oracle(
    reg: Registry, ground: recipe_eval.Ground, questions: list[Question]
) -> tuple[int, str, list[recipe_eval.Result]]:
    deep_lane = HandoffRecorder()
    fast = FastLane(OracleClient(plans=recipe_eval.plans(questions)), reg)
    out = io.StringIO()
    code, results = recipe_eval.run(
        lambda: Asker(reg, deep_lane, fast), questions, ground, out, fake=True
    )
    return code, out.getvalue(), results


def test_the_oracle_passes_every_row(
    lane_reg: Registry, ground: recipe_eval.Ground
) -> None:
    code, report, results = run_oracle(lane_reg, ground, oracle_questions())
    assert code == 0, report
    assert "correct: 10 of 10 (100.0%; target >= 95%)" in report
    assert "uncited numbers: 0 (target 0)" in report
    assert report.rstrip().endswith("Result: PASS")
    by_id = {r.q.id: r for r in results}
    assert by_id["o2"].lane == "instant" and by_id["o8"].reply.asked
    # o3 named no item the router could pre-retrieve: the oracle looked it up itself
    assert by_id["o3"].reply.tool_calls == ["recipe_find"]
    assert EBF_O2 in by_id["o4"].score.cited
    assert len(by_id["o5"].score.cited) == 2
    assert by_id["o7"].score.cited == ["mte:1000"]


def test_the_oracle_follows_more_pages(
    tool_db: Path, glossary: Glossary, ground: recipe_eval.Ground
) -> None:
    """One row a page: the recipe a step means is on the second page of its lookup."""
    reg = build_registry(tool_db, glossary, deep=HandoffRecorder(), page=1)
    question = recipe_eval.question(
        "p1",
        "time",
        ANSWER,
        "How long does the EBF take for steel from steel dust?",
        f"make {STEEL} map=gt.recipe.blastfurnace in={STEEL_DUST}",
        "dur",
    )
    code, report, results = run_oracle(reg, ground, [question])
    assert code == 0, report
    assert results[0].reply.tool_calls == ["recipe_find", "more"]
    assert results[0].score.cited == [EBF_CIRCUIT]


def test_a_wrong_answer_fails_the_run(
    lane_reg: Registry, ground: recipe_eval.Ground
) -> None:
    """The scorer, not the lane's own check, catches an answer about the wrong recipe."""
    question = recipe_eval.question(
        "w1",
        "power",
        ANSWER,
        "How much EU/t?",
        f"make {STEEL} map=gt.recipe.blastfurnace",
        "eut",
    )
    other = recipe_eval.question(
        "w1",
        "power",
        ANSWER,
        "How much EU/t?",
        f"make {STEEL} map=gt.recipe.arcfurnace",
        "eut",
    )
    deep_lane = HandoffRecorder()
    fast = FastLane(OracleClient(plans=recipe_eval.plans([other])), lane_reg)
    out = io.StringIO()
    code, results = recipe_eval.run(
        lambda: Asker(lane_reg, deep_lane, fast), [question], ground, out, fake=True
    )
    assert code == 1 and not results[0].score.passed
    assert results[
        0
    ].reply.checked  # the lane's number check passed it: its numbers are real
    assert "cites no row" in results[0].score.why


def test_a_handoff_with_no_deep_lane_fails(
    lane_reg: Registry, ground: recipe_eval.Ground
) -> None:
    question = recipe_eval.question(
        "d1",
        "multistep",
        ANSWER,
        "Compare the EBF and the bricked blast furnace for steel.",
        f"make {STEEL} map=gt.recipe.blastfurnace",
        "",
    )
    code, _, results = run_oracle(lane_reg, ground, [question])
    assert code == 1 and results[0].lane == "deep"
    assert "doesn't run here" in results[0].score.why


def test_decide_asks_on_an_ambiguous_result(lane_reg: Registry) -> None:
    out = lane_reg.call("item_resolve", {"text": "dust"}, lane="fast")
    question = recipe_eval.question("a", "recipe", ANSWER, "x", f"make {STEEL}")
    plan = decide(
        question, [Seen("item_resolve", {"text": "dust"}, out.text)], tools=True
    )
    assert plan.ask and not plan.calls
    # nothing yet: the step's lookup; tools off: no call, the answer says what's missing
    plan = decide(question, [], tools=True)
    assert plan.calls == [("recipe_find", {"item": STEEL})]
    assert decide(question, [], tools=False).calls == []


def test_the_eval_main(
    tmp_path: Path, tool_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    out = io.StringIO()
    assert recipe_eval.main(cfg, None, True, 1, None, out) == 1
    assert "Not run: no DB" in out.getvalue()
    (home / "data").mkdir(parents=True)
    shutil.copyfile(tool_db, home / "data" / "gos.sqlite")
    bad = tmp_path / "bad.tsv"
    bad.write_text("x\n", encoding="utf-8")
    out = io.StringIO()
    assert recipe_eval.main(cfg, bad, True, None, None, out) == 2
    assert "Can't read the questions" in out.getvalue()

    def missing(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", missing)
    out = io.StringIO()
    assert recipe_eval.main(cfg, None, False, 1, None, out) == 1
    assert "no Anthropic key is stored" in out.getvalue()


def test_gos_eval_recipe_lookup_fake(
    tmp_path: Path, tool_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    shutil.copyfile(tool_db, home / "data" / "gos.sqlite")
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    questions = tmp_path / "q.tsv"
    lines = ["\t".join(recipe_eval.COLUMNS)] + ["\t".join(r) for r in QUESTIONS]
    questions.write_text("\n".join(lines) + "\n", encoding="utf-8")
    table = tmp_path / "report.tsv"
    argv = [
        "eval",
        "recipe-lookup",
        "--fake",
        "--config",
        str(cfg),
        "--questions",
        str(questions),
    ]
    assert cli.main([*argv, "--out", str(table)]) == 0
    printed = capsys.readouterr().out
    assert "correct: 10 of 10" in printed and "Result: PASS" in printed
    rows = table.read_text(encoding="utf-8").splitlines()
    assert rows[0].split("\t")[:4] == ["id", "kind", "lane", "pass"] and len(rows) == 11
    assert cli.main([*argv, "--limit", "2"]) == 0
    assert "correct: 2 of 2" in capsys.readouterr().out
    # nothing recorded, nothing written to a vault
    assert not (home / "vault").exists()


def test_the_cli_parser_stays_light() -> None:
    """Every gos command builds the parser, so it must not import the Anthropic SDK."""
    probe = "import sys, gregtec.cli; print('anthropic' in sys.modules)"
    got = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert got.stdout.strip() == "False"


# --- the deep lane, with the real CLI and the oracle model ----------------------------------

DEEP_Q = recipe_eval.question(
    "dq",
    "multistep",
    ANSWER,
    "Compare the EBF and the bricked blast furnace recipes for steel.",
    f"make {STEEL} map=gt.recipe.blastfurnace ; make {STEEL} map=gt.recipe.primitiveblastfurnace",
    "eut,dur ; dur",
)


def run_deep(
    tool_db: Path,
    glossary: Glossary,
    ground: recipe_eval.Ground,
    home: Path,
    template: Path,
) -> tuple[int, list[recipe_eval.Result], FakeMessagesAPI, deep.DeepLaneService]:
    plans = recipe_eval.plans([DEEP_Q])
    api = FakeMessagesAPI(deep_policy(plans))
    with api:
        lane = deep.DeepLaneService(
            None,
            deep_config.Settings(task_max_usd=1.0),
            home=home,
            key=lambda: FAKE_KEY,
            env=api.env(),
            template=template,
        )
        reg = build_registry(tool_db, glossary, deep=lane)
        lane.registry = reg
        fast = FastLane(OracleClient(plans=plans), reg)
        try:
            code, results = recipe_eval.run(
                lambda: Asker(reg, lane, fast),
                [DEEP_Q],
                ground,
                io.StringIO(),
                fake=True,
                deep=lane,
                wait_s=WAIT_S,
            )
        finally:
            lane.stop()
    return code, results, api, lane


def test_the_deep_lane_loads_the_skill_and_answers(
    tool_db: Path, glossary: Glossary, ground: recipe_eval.Ground, tmp_path: Path
) -> None:
    code, results, api, lane = run_deep(
        tool_db, glossary, ground, tmp_path / "agent", TEMPLATE
    )
    [result] = results
    assert code == 0, (result.score, result.text)
    assert result.lane == "deep" and set(result.score.cited) == {EBF_O2, PBF}
    [task] = lane.tasks()
    assert task.init is not None and SKILL in task.init["skills"]
    names = [
        b.get("name")
        for body in api.requests
        for m in body.get("messages") or []
        if isinstance(m.get("content"), list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    assert names[0] == "Skill" and "mcp__gos__recipe_find" in names
    # the spoken first line has no ids; the detail cites both rows
    first, _, rest = result.text.partition("\n")
    assert "r:" not in first and EBF_O2 in rest and PBF in rest


def test_a_missing_skill_fails_the_deep_row(
    tool_db: Path, glossary: Glossary, ground: recipe_eval.Ground, tmp_path: Path
) -> None:
    template = tmp_path / "vault-template"
    shutil.copytree(TEMPLATE, template)
    shutil.rmtree(template / "_gos" / "skills" / "recipe-lookup")
    code, results, _, _ = run_deep(
        tool_db, glossary, ground, tmp_path / "agent", template
    )
    assert code == 1 and not results[0].score.passed
    assert "didn't load" in results[0].text


# --- the recipe card ------------------------------------------------------------------------


def test_the_recipe_card(lane_reg: Registry) -> None:
    out = lane_reg.call("recipe_find", {"item": STEEL}, lane="fast")
    card = cards.recipe_card("recipe_find", parse(out.text))
    assert card is not None
    assert card["type"] == "recipe" and card["v"] == 0 and card["tool"] == "recipe_find"
    assert card["item"] == {"key": STEEL, "name": "Steel Ingot"}
    assert card["total"] == 5 and len(card["recipes"]) == 5
    by_ref = {r["ref"]: r for r in card["recipes"]}
    ebf = by_ref[EBF_O2]
    assert (ebf["machine"], ebf["tier"], ebf["eut"], ebf["amps"]) == (
        "Blast Furnace",
        "MV",
        120,
        1,
    )
    assert (ebf["dur_t"], ebf["dur_s"]) == (500, "25")
    assert ebf["inputs"][1] == {
        "key": "fluid:oxygen",
        "name": "Oxygen",
        "amount": 1000,
        "unit": "L",
        "chance_pct": "100",
        "consumed": True,
        "circuit": None,
        "ore": None,
        "alternatives": 0,
    }
    assert (
        ebf["outputs"][1]["key"] == ASHES and ebf["outputs"][1]["chance_pct"] == "11.11"
    )
    assert ebf["conditions"] == [{"key": "coil_heat", "value": "1000"}]
    circuit = by_ref[EBF_CIRCUIT]["inputs"][1]
    assert (
        circuit["circuit"] == 1
        and circuit["consumed"] is False
        and circuit["amount"] == 0
    )
    assert by_ref[ARC]["flags"] == ["recycling"] and by_ref[KILN]["tier"] is None
    uses = lane_reg.call("recipe_uses", {"item": STEEL}, lane="fast")
    card = cards.recipe_card("recipe_uses", parse(uses.text))
    assert card is not None
    mac = {r["ref"]: r for r in card["recipes"]}[MAC_ORE]
    assert mac["inputs"][0]["ore"] == "ingotSteel"


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (
            "4x Energy Core [ULV] [miscutils:item.itemBufferCore1@0]",
            {
                "key": "miscutils:item.itemBufferCore1@0",
                "name": "Energy Core [ULV]",
                "amount": 4,
            },
        ),
        (
            "1x Black Stained Glass [minecraft:stained_glass@32767] (or 16 others)",
            {"key": "minecraft:stained_glass@32767", "alternatives": 16, "ore": None},
        ),
        ("1x Iron Rod [x:y@1] (or 1 other)", {"alternatives": 1}),
        ("Sulfuric Acid [fluid:sulfuricacid]", {"amount": 0, "unit": None}),
        (
            "2x Stick [minecraft:stick@0] 13.5% chance",
            {"chance_pct": "13.5", "unit": "item"},
        ),
        (
            "1x 1x Tin Cable [gregtech:gt.blockmachines@1246]",
            {"name": "1x Tin Cable", "amount": 1},
        ),
        (
            "[gregtech:gt.metaitem.01@1]",
            {"name": "", "key": "gregtech:gt.metaitem.01@1"},
        ),
        ("no key here", {"text": "no key here"}),
    ],
)
def test_slots(text: str, want: dict[str, object]) -> None:
    got = cards.slot(text)
    assert {k: got.get(k) for k in want} == want


def test_only_recipe_results_have_a_recipe_card(lane_reg: Registry) -> None:
    pre = prefetch.prefetch(lane_reg, router.route("how do I make steel"))
    assert pre.main is not None and pre.main.card()["recipe"]["type"] == "recipe"
    resolved = pre.blocks[0].card()
    assert resolved["tool"] == "item_resolve" and "recipe" not in resolved
    ambiguous = lane_reg.call("recipe_find", {"item": "dust"}, lane="fast")
    assert cards.recipe_card("recipe_find", parse(ambiguous.text)) is None
    assert cards.recipe_card("machine_info", parse(ambiguous.text)) is None
    error = prefetch.Block("recipe_find", {}, "error: no DB\n", True)
    assert "recipe" not in error.card()


# --- the skill ------------------------------------------------------------------------------


def test_the_recipe_lookup_skill(lane_reg: Registry, tmp_path: Path) -> None:
    text = SKILL_FILE.read_text(encoding="utf-8")
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    meta = dict(line.split(": ", 1) for line in head.splitlines())
    assert meta["name"] == "recipe-lookup" and set(meta) == {"name", "description"}
    assert len(meta["description"]) <= 1024
    for tool in ("item_resolve", "recipe_find", "recipe_uses", "machine_info"):
        assert tool in meta["description"], tool  # plan 7.0's main tools
    named = set(re.findall(r"`([a-z_]+)\(", body))
    assert named == {
        "item_resolve",
        "recipe_find",
        "recipe_uses",
        "machine_info",
        "more",
    }
    deep_tools = {t.name for t in lane_reg.tools("deep")}
    assert named <= deep_tools  # every tool it names is one the deep lane has
    assert "r:<map>/<sig12>" in body and "mte:<id>" in body and "ambiguous: 1" in body
    assert "!`" not in body  # no shell: the deep lane has none (D154)
    # the deep lane's plugin copy carries it, as gos:recipe-lookup
    plugin = deep_config.build_plugin(TEMPLATE / "_gos", tmp_path / "plugin")
    assert deep_config.skill_names(plugin) == [SKILL]
    # deployed into a vault, the fast lane's cached prompt lists it (one line, plan 4.4)
    vault = tmp_path / "vault"
    deploy.deploy(vault)
    rules = system_prompt(vault)[0]["text"]
    assert f"- recipe-lookup: {meta['description']}" in rules


def test_the_fast_lane_answers_with_the_card(lane_reg: Registry) -> None:
    """``/api/ask``'s reply carries the recipe card in its card's ``recipe`` key."""
    deep_lane = HandoffRecorder()
    question = recipe_eval.question(
        "c1", "recipe", ANSWER, "How do I make steel?", f"make {STEEL}"
    )
    fast = FastLane(OracleClient(plans=recipe_eval.plans([question])), lane_reg)
    reply = Asker(lane_reg, deep_lane, fast).ask("How do I make steel?")
    card = reply.as_dict()["card"]
    assert card["type"] == "tool_result" and card["recipe"]["total"] == 5
