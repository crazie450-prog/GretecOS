"""WO-2.7: the recipe-lookup eval (question file, truth from the DB, scoring), its oracle fakes,
the recipe card and the skill, on conftest's self-authored DB. No test calls the API; the deep
lane's test runs the real Claude Code CLI against the fake Messages API (as WO-2.4's do)."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from gregtec import cli
from gregtec.agents import (
    ask,
    cards,
    deep,
    deep_config,
    prefetch,
    recipe_eval,
    recipe_oracle,
    router,
)
from gregtec.agents.ask import Asker, Reply
from gregtec.agents.deep_eval import FAKE_KEY
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane, system_prompt
from gregtec.agents.recipe_eval import ANSWER, ASK, NONE, Question, Truth
from gregtec.agents.recipe_oracle import SKILL, OracleClient, Seen, decide, deep_policy
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
    truth: str,
    want: str = "",
    expect: str = ANSWER,
    kind: str = "recipe",
    words: str = "",
) -> Question:
    return recipe_eval.question("t1", kind, expect, "a question", truth, want, words)


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
    assert {"fact", "alts", "ore", "simplest"} <= wants
    assert {s.op for x in qs for s in x.steps} == set(recipe_eval.OPS)
    # the oredict questions check the oredict, and "how do I make" the simplest recipe
    assert all(any("ore=" in w for w in x.steps[0].want) for x in kinds["oredict"][:3])
    how = [
        x
        for x in qs
        if x.expect == ANSWER
        and x.question.lower().startswith(("how do i make", "how is"))
    ]
    assert len(how) >= 5 and all("simplest" in x.steps[0].want for x in how)
    # the fake models resolve every step (and every ask row) from the question's words
    for x in qs:
        assert len(x.words) == max(len(x.steps), 1) and all(x.words), x.id


def test_the_file_holds_references_never_game_numbers() -> None:
    """Truth, want and words cells hold keys, map and oredict names, tiers, field names and
    the question's words: with the keys out, no digit is left (a game number would be one)."""
    keys_ = re.compile(
        r"(?:mte|fluid):\S+|[\w.|-]+:[\w.|-]+@\d+|gt\.recipe\.\w+|ore=\S+"
    )
    for line in recipe_eval.QUESTIONS.read_text(encoding="utf-8").splitlines()[1:]:
        cells = line.split("\t")
        rest = keys_.sub(" ", " ".join(cells[4:]))
        assert not re.search(r"\d", rest), line


def test_the_router_places_each_row() -> None:
    lanes = {}
    asked: dict[str, list[str]] = {"prefetch": [], "fast": [], "deep": []}
    for x in recipe_eval.load():
        got = router.route(x.question)
        lanes[x.id] = got.kind
        if x.expect == ASK:  # who must ask: pre-retrieval, or a model's own lookup
            way = (
                "deep"
                if got.kind == router.DEEP
                else ("prefetch" if got.subject else "fast")
            )
            asked[way].append(x.id)
    counts = {k: sum(v == k for v in lanes.values()) for k in set(lanes.values())}
    # the eval goes through every lane: instant commands, the fast lane, the deep lane
    assert counts[router.INSTANT] >= 4 and counts[router.DEEP] >= 3
    assert counts[router.FAST] >= 30
    # unclear items asked about by pre-retrieval (no model), and by each lane's model
    assert len(asked["prefetch"]) >= 3 and asked["fast"] and asked["deep"]


def test_trailing_empty_cells_may_be_missing(tmp_path: Path) -> None:
    path = tmp_path / "q.tsv"
    path.write_text(
        "\t".join(recipe_eval.COLUMNS)
        + "\nx1\trecipe\tanswer\tHow do I make steel?\tmake "
        + STEEL
        + "\nx2\tambiguous\task\tHow do I make dust?\n"
        + f"x3\trecipe\tanswer\tSteel?\tmake {STEEL}\t\tsteel\n",
        encoding="utf-8",
    )
    got = recipe_eval.load(path)
    assert [x.id for x in got] == ["x1", "x2", "x3"]
    assert got[0].steps[0].key == STEEL and got[1].steps == () and got[0].words == ()
    assert got[2].words == ("steel",)


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
        ("machine mte:1000", "ore=ingotSteel", ANSWER, "can't be wanted"),
        (f"use {STEEL}", "simplest", ANSWER, "can't be wanted"),
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


def test_more_words_than_steps_are_refused() -> None:
    with pytest.raises(recipe_eval.EvalError, match="more words parts"):
        recipe_eval.question("x", "recipe", ANSWER, "q", f"make {STEEL}", "", "a ; b")
    asks = recipe_eval.question("x", "ambiguous", ASK, "q", "", "", "dust")
    assert asks.words == ("dust",)


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
    assert sorted(t.all) == sorted(_all_refs(lane_reg, tool, args))


def test_filters_narrow_a_step(ground: recipe_eval.Ground) -> None:
    def rows(truth: str) -> tuple[set[str], set[str]]:
        t = truths(ground, q(truth))[0]
        return set(t.rows), set(t.flagged)

    # fake? (the kiln) and recycling (the arc furnace) rows drop out: unflagged rows exist
    assert rows(f"make {STEEL}") == ({EBF_O2, EBF_CIRCUIT, PBF}, {ARC, KILN})
    assert rows(f"make {STEEL} map=gt.recipe.blastfurnace") == (
        {EBF_O2, EBF_CIRCUIT},
        set(),
    )
    assert rows(f"make {STEEL} in=fluid:oxygen") == ({EBF_O2}, {ARC})
    assert rows(f"make {STEEL} in={IRON_DUST} in=fluid:oxygen") == ({EBF_O2}, set())
    assert rows(f"make {STEEL} out={ASHES}") == ({EBF_O2, PBF}, set())
    assert rows(f"make {STEEL} tier=LV") == ({PBF}, {ARC, KILN})
    assert rows(f"use {STEEL} ore=ingotSteel") == ({MAC_ORE}, set())
    # only flagged rows: they are the step's rows
    assert rows(f"make {STEEL} map=gt.recipe.arcfurnace") == ({ARC}, set())
    # the oredict slot takes TConstruct's steel ingot too: a use of it, through the oredict
    assert rows("use TConstruct:materials@16") == ({MAC_ORE}, set())
    assert rows("make minecraft:planks@1") == (set(), set())
    assert rows("make gregtech:gt.metaitem.01@99999") == (set(), set())  # not in the DB


def test_the_simplest_rows_and_the_counts(ground: recipe_eval.Ground) -> None:
    [t] = truths(ground, q(f"make {STEEL}"))
    assert t.simplest() == {PBF}  # the lowest tier among the rows without a flag
    assert t.counts >= {"5", "3"}  # the tools' total, flagged rows in; 3 up to LV
    [ebf] = truths(ground, q(f"make {STEEL} map=gt.recipe.blastfurnace"))
    assert ebf.simplest() == {EBF_O2, EBF_CIRCUIT}  # both MV


def test_a_rows_values_are_the_dbs(ground: recipe_eval.Ground) -> None:
    [t] = truths(ground, q(f"make {STEEL} map=gt.recipe.blastfurnace"))
    row = t.rows[EBF_O2]
    assert row.fields["eut"] == {"120"} and row.fields["amps"] == {"1"}
    assert row.fields["dur"] == {"500", "25"}  # ticks and seconds, both exact
    assert row.fields["dur_t"] == {"500"} and row.fields["dur_s"] == {"25"}
    assert row.fields["in=fluid:oxygen"] == {"1000"}
    assert row.fields[f"chance={ASHES}"] == {"11.11"}
    assert row.fields["cond=coil_heat"] == {"1000"}
    assert row.fields["tier"] == {"MV"}
    assert {"Blast Furnace", "Electric Blast Furnace"} <= row.fields["machine"]
    assert {"120", "500", "25", "1000", "11.11", "1"} <= row.numbers
    assert not row.flags
    oxygen = next(s for s in row.slots if "fluid:oxygen" in s.keys)
    assert (oxygen.part, oxygen.amount, oxygen.names) == ("in", "1000", {"Oxygen"})
    ashes = next(s for s in row.slots if ASHES in s.keys)
    assert (ashes.part, ashes.chance) == ("out", "11.11")
    assert t.counts >= {"2", "0"}  # 2 in all, none at a cap under MV
    [mac] = truths(ground, q(f"use {STEEL} ore=ingotSteel"))
    assert mac.rows[MAC_ORE].fields[f"alts={STEEL}"] == {"1"}  # "(or 1 other)"
    assert mac.rows[MAC_ORE].fields["ore=ingotSteel"] == {"ingotSteel"}
    assert ground.row(ARC).flags == {"recycling"}  # type: ignore[union-attr]
    assert ground.row(KILN).flags == {"fake?"}  # type: ignore[union-attr]
    [ebf] = truths(ground, q("machine mte:1000"))
    facts = ebf.rows["mte:1000"].fields
    assert facts["fact=parallel"] == {"1"} and facts["fact=oc"] == {"heat"}
    assert "Electric Blast Furnace" in facts["machine"]
    assert ground.row("mte:99999") is None and ground.row("q:abc") is None


# --- numbers in context -------------------------------------------------------------------


def test_numbers_are_read_with_their_units() -> None:
    said = recipe_eval.mentions(
        "120 EU/t for 1000 ticks (50 s) at 1 A; 1,000L Water, 11.11% chance of Ashes; "
        "2x Steel Ingot or 16 others; 7 recipes in all; eut: 30, dur_t: 20, coil_heat=1700, "
        "speed 4"
    )
    assert [(m.value, m.role) for m in said] == [
        ("120", "eut"),
        ("1000", "dur_t"),
        ("50", "dur_s"),
        ("1", "amps"),
        ("1000", "amount"),
        ("11.11", "pct"),
        ("2", "amount"),
        ("16", "alts"),
        ("7", "count"),
        ("30", "eut"),
        ("20", "dur_t"),
        ("1700", "other"),
        ("4", "other"),
    ]
    water, chance = said[4], said[5]
    assert water.after.strip() == "Water" and "Ashes" in chance.after
    assert said[11].label == "coil_heat" and "speed" in said[12].before


# --- scoring ------------------------------------------------------------------------------

EBF_STEEL = f"make {STEEL} map=gt.recipe.blastfurnace"
EBF_AND_PBF = (
    f"make {STEEL} map=gt.recipe.blastfurnace ; "
    f"make {STEEL} map=gt.recipe.primitiveblastfurnace"
)
USE_ORE = f"use {STEEL} ore=ingotSteel"


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
        (EBF_STEEL, "tier", f"Steel Ingot: {EBF_O2}, at LV.", False, "states a tier"),
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
            f"Steel Ingot: {EBF_O2} takes 1,000 L of oxygen.",
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
            EBF_STEEL,
            "cond=coil_heat",
            f"Steel Ingot: {EBF_O2}, 1000 L Oxygen, coil heat 1700.",
            False,
            "doesn't state cond=coil_heat",
        ),
        (
            f"make {STEEL}",
            "count",
            f"Steel Ingot: 5 recipes in all; one is {PBF}.",
            True,
            "",
        ),
        (
            f"make {STEEL}",
            "count",
            f"Steel Ingot: 6 recipes in all ({PBF}).",
            False,
            "count",
        ),
        # a count at a tier cap ("at LV": 3) is one the tool can give
        (
            f"make {STEEL}",
            "count",
            f"Steel Ingot: 3 recipes up to LV ({PBF}).",
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
            EBF_AND_PBF,
            "eut ; dur",
            f"EBF ({EBF_O2}) at 120 EU/t; the bricked one ({PBF}) takes 1200 ticks.",
            True,
            "",
        ),
        (
            EBF_AND_PBF,
            "",
            f"Steel Ingot: the EBF ({EBF_O2}).",
            False,
            "cites no row of make",
        ),
        # HIGH-1: right numbers in the wrong field
        (
            EBF_STEEL,
            "eut,dur",
            f"Steel Ingot: {EBF_O2}, 500 EU/t, 120 ticks.",
            False,
            "doesn't state eut",
        ),
        (
            EBF_STEEL,
            "dur",
            f"Steel Ingot: {EBF_O2}, 120 ticks at 500 EU/t.",
            False,
            "doesn't state dur",
        ),
        (
            EBF_STEEL,
            "dur",
            f"Steel Ingot: {EBF_O2}, 25 ticks.",
            False,
            "doesn't state dur",
        ),
        (
            EBF_STEEL,
            "tier",
            f"Steel Ingot: {EBF_O2} needs LV, not MV.",
            False,
            "states a tier",
        ),
        (
            EBF_STEEL,
            "in=fluid:oxygen",
            f"Steel Ingot: {EBF_O2} takes 1000L Iron Dust and 1L Oxygen.",
            False,
            "doesn't state in=fluid:oxygen",
        ),
        (
            EBF_STEEL,
            "in=fluid:oxygen",
            f"Steel Ingot: {EBF_O2} takes 1,000 L.",  # 1000 L of what?
            False,
            "doesn't state in=fluid:oxygen",
        ),
        (
            EBF_STEEL,
            f"chance={ASHES}",
            f"Steel Ingot: {EBF_O2}, 100% Ashes and 11.11% Steel Ingot.",
            False,
            f"doesn't state chance={ASHES}",
        ),
        # an oredict question checks the oredict
        (
            USE_ORE,
            "ore=ingotSteel",
            f"Steel Ingot: {MAC_ORE} takes any ore:ingotSteel.",
            True,
            "",
        ),
        (
            USE_ORE,
            "ore=ingotSteel",
            f"Steel Ingot: {MAC_ORE}; no, only GT's ingot works.",
            False,
            "doesn't state ore=ingotSteel",
        ),
        (
            USE_ORE,
            f"alts={STEEL}",
            f"Steel Ingot: {MAC_ORE} or 1 other stack.",
            True,
            "",
        ),
        # HIGH-2: the simplest recipe without a flag
        (
            f"make {STEEL}",
            "simplest",
            f"Steel Ingot: the Bricked Blast Furnace ({PBF}).",
            True,
            "",
        ),
        (
            f"make {STEEL}",
            "simplest",
            f"Steel Ingot: the EBF ({EBF_O2}).",
            False,
            "simplest",
        ),
        (
            f"make {STEEL}",
            "",
            f"Steel Ingot: the Alloy Kiln ({KILN}).",
            False,
            "flagged rows",
        ),
        (
            f"make {STEEL}",
            "",
            f"Steel Ingot: the Arc Furnace ({ARC}).",
            False,
            "recycling",
        ),
        # LOW-6: a step's count is a count only in a count phrase
        (
            f"make {STEEL}",
            "",
            f"Steel Ingot: ({PBF}) takes 5 L.",
            False,
            "uncited numbers: 5",
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
    slot = recipe_eval.Slot(
        "out", frozenset({STEEL}), frozenset({"Steel Ingot"}), "9", "100", 0, None
    )
    row = recipe_eval.Row(
        cite, names={"ic2.neiIntegration.Handler"}, numbers={"9"}, slots=[slot]
    )
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
    # a cited row's number in another unit is uncited too
    swapped = recipe_eval.score(
        question, f"Steel: {EBF_O2}, 25 ticks.", False, truths(ground, question), ground
    )
    assert swapped.uncited == ["25"]


def test_ask_and_none_rows(ground: recipe_eval.Ground) -> None:
    ask_ = q("", expect=ASK, kind="ambiguous")
    question = "Which dust did you mean: Iron Dust, Steel Dust or Stainless Steel Dust?"
    assert recipe_eval.score(ask_, question, True, [], ground).passed
    assert not recipe_eval.score(
        ask_, "Steel Dust: macerator.", False, [], ground
    ).passed
    numbered = recipe_eval.score(ask_, "Which one: 2 or 3?", True, [], ground)
    assert not numbered.passed and "numbers" in numbered.why
    # a name with digits in it, as the asker shows it, isn't a number
    wire = "Which wire did you mean: 1x Copper Wire or 2x Copper Wire?"
    names = ["1x Copper Wire", "2x Copper Wire"]
    assert recipe_eval.score(ask_, wire, True, [], ground, names).passed

    none = q(f"make {SPRUCE}", expect=NONE, kind="none")
    ts = truths(ground, none)
    said = "Spruce Wood Planks: no recipe in the DB makes it."
    assert recipe_eval.score(none, said, False, ts, ground).passed
    cited = recipe_eval.score(none, f"Spruce: {EBF_O2}.", False, ts, ground)
    assert not cited.passed and "where none exists" in cited.why
    made_up = recipe_eval.score(none, "Spruce takes 4 logs.", False, ts, ground)
    assert not made_up.passed and made_up.uncited == ["4"]
    # a none row must say so: a refusal isn't an answer
    shrug = recipe_eval.score(none, "Sorry, I can't help with that.", False, ts, ground)
    assert not shrug.passed and "no recipe" in shrug.why
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
    (
        "o1",
        "recipe",
        ANSWER,
        "How do I make steel?",
        f"make {STEEL}",
        "simplest",
        "steel",
    ),
    (
        "o2",
        "recipe",
        ANSWER,
        "recipe for steel",
        f"make {STEEL}",
        "count,simplest",
        "steel",
    ),
    (
        "o3",
        "power",
        ANSWER,
        "How much EU/t does the EBF need to make steel from iron dust?",
        f"make {STEEL} map=gt.recipe.blastfurnace in={IRON_DUST}",
        "eut,dur",
        "steel",
    ),
    (
        "o4",
        "chance",
        ANSWER,
        "What's the chance of ashes from the EBF's oxygen steel recipe?",
        f"make {STEEL} map=gt.recipe.blastfurnace in=fluid:oxygen",
        f"chance={ASHES},in=fluid:oxygen",
        "steel",
    ),
    (
        "o5",
        "multistep",
        ANSWER,
        "What makes steel dust, and then what makes the steel ingot?",
        f"make {STEEL_DUST} ; make {STEEL} map=gt.recipe.blastfurnace",
        " ; machine",
        "steel dust ; steel ingot",
    ),
    ("o6", "uses", ANSWER, "What is steel used for?", f"use {STEEL}", "", "steel"),
    (
        "o7",
        "machine",
        ANSWER,
        "How many parallels does the electric blast furnace have?",
        "machine mte:1000",
        "fact=parallel",
        "electric blast furnace",
    ),
    ("o8", "ambiguous", ASK, "How do I make dust?", "", "", "dust"),
    (
        "o9",
        "none",
        NONE,
        "How do I make spruce wood planks?",
        f"make {SPRUCE}",
        "",
        "spruce wood planks",
    ),
    (
        "o10",
        "oredict",
        ANSWER,
        "Can any steel ingot go in the macerator?",
        f"use {STEEL} ore=ingotSteel",
        "ore=ingotSteel",
        "steel ingot",
    ),
    # an unclear item no pre-retrieval reached: the model's own lookup must ask
    ("o11", "ambiguous", ASK, "Which macerator recipes take a dust?", "", "", "dust"),
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
    assert "correct: 11 of 11 (100.0%; target >= 95%)" in report
    assert "uncited numbers: 0 (target 0)" in report
    assert report.rstrip().endswith("Result: PASS")
    by_id = {r.q.id: r for r in results}
    assert by_id["o2"].lane == "instant" and by_id["o8"].reply.asked
    # o1: the first row without a flag (the kiln's fake? row is listed first)
    assert by_id["o1"].score.cited == [PBF]
    assert (
        f"The first unflagged one is Bricked Blast Furnace ({PBF})" in by_id["o2"].text
    )
    # o3 named no item the router could pre-retrieve: the oracle looked its words up
    assert by_id["o3"].reply.tool_calls == ["recipe_find"]
    assert EBF_O2 in by_id["o4"].score.cited
    assert len(by_id["o5"].score.cited) == 2
    assert by_id["o7"].score.cited == ["mte:1000"]
    # o11: the model's own lookup was ambiguous, and it asked (no pre-retrieval did)
    assert not by_id["o11"].reply.asked and by_id["o11"].reply.tool_calls == [
        "item_resolve"
    ]


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
        "steel",
    )
    code, report, results = run_oracle(reg, ground, [question])
    assert code == 0, report
    assert results[0].reply.tool_calls == ["recipe_find", "more"]
    assert results[0].score.cited == [EBF_CIRCUIT]


def test_words_that_name_another_item_fail_the_row(
    lane_reg: Registry, ground: recipe_eval.Ground
) -> None:
    """The oracle looks the item up by the question's words, as a model would: words that
    resolve to another item give a wrong answer, which the scorer fails."""
    question = recipe_eval.question(
        "w0",
        "recipe",
        ANSWER,
        "How do I make stainless steel?",
        f"make {STEEL}",
        "",
        "stainless steel",
    )
    code, _, results = run_oracle(lane_reg, ground, [question])
    assert code == 1 and "cites no row of make" in results[0].score.why
    assert "Stainless Steel Ingot" in results[0].text


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
        "steel",
    )
    other = recipe_eval.question(
        "w1",
        "power",
        ANSWER,
        "How much EU/t?",
        f"make {STEEL} map=gt.recipe.arcfurnace",
        "eut",
        "steel",
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
    # the report and the table name the real id no step means
    assert results[0].score.extra == [ARC] and f"\t{ARC}\t" in out.getvalue()


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


class _Lane:
    """A stand-in deep lane whose tasks are already where a test wants them."""

    def __init__(self, tasks: list[deep.DeepTask]) -> None:
        self.done = tasks
        self.stopped: list[str] = []

    def tasks(self) -> list[deep.DeepTask]:
        return list(self.done)

    def wait(self, task_id: str, timeout: float) -> deep.DeepTask | None:
        return next((t for t in self.done if t.id == task_id), None)

    def stop_task(self, task_id: str) -> bool:
        self.stopped.append(task_id)
        for t in self.done:
            if t.id == task_id:
                t.status = deep.STOPPED
        return True


def test_the_deep_lanes_answers_are_waited_for() -> None:
    def task(id_: str, status: str, text: str | None = None) -> deep.DeepTask:
        t = deep.DeepTask(id_, "r", "r", "router", 2.0, status=status, text=text)
        t.cost_usd = 0.5
        return t

    old = task("h-old", deep.DONE, "an earlier row's answer")
    lane = _Lane(
        [old, task("h-1", deep.DONE, "Steel Ingot: ..."), task("h-2", deep.FAILED)]
    )
    text, cost, why = recipe_eval._deep_answer(
        cast(deep.DeepLaneService, lane), {"h-old"}, 1.0
    )
    assert text == "Steel Ingot: ..." and cost == 1.0 and why is not None
    assert "failed" in why
    capped = _Lane([task("h-3", deep.NEEDS_APPROVAL)])
    text, _, why = recipe_eval._deep_answer(
        cast(deep.DeepLaneService, capped), set(), 1.0
    )
    assert capped.stopped == ["h-3"]  # never approved (D157)
    assert text == "" and why is not None and "cap" in why


def test_decide_resolves_words_and_asks_when_unclear(lane_reg: Registry) -> None:
    question = recipe_eval.question(
        "a", "recipe", ANSWER, "x", f"make {STEEL}", "", "dust"
    )
    out = lane_reg.call("item_resolve", {"text": "dust"}, lane="fast")
    plan = decide(
        question, [Seen("item_resolve", {"text": "dust"}, out.text)], tools=True
    )
    assert plan.ask and not plan.calls
    # nothing yet: the fast lane passes the words to the lookup; the deep lane resolves
    # them first (the skill's step 1); tools off: no call, the answer says what's missing
    steel = recipe_eval.question(
        "b", "recipe", ANSWER, "x", f"make {STEEL}", "", "steel"
    )
    assert decide(steel, [], tools=True).calls == [("recipe_find", {"item": "steel"})]
    assert decide(steel, [], tools=True, resolve_first=True).calls == [
        ("item_resolve", {"text": "steel"})
    ]
    assert decide(steel, [], tools=False).calls == []
    # a recipe tool that says ambiguous: ask
    unclear = lane_reg.call("recipe_find", {"item": "dust"}, lane="fast")
    plan = decide(
        recipe_eval.question("c", "recipe", ANSWER, "x", f"make {STEEL}", "", "dust"),
        [Seen("recipe_find", {"item": "dust"}, unclear.text)],
        tools=True,
    )
    assert plan.ask and len(plan.ask) == 3


def test_a_step_takes_its_item_from_the_chosen_rows_in_cell() -> None:
    """The skill's "what makes X, then its input Y": Y's key from the slot named Y."""
    row = {
        "in": "1x 1x Copper Wire [gregtech:gt.blockmachines@1360] (any ore:wireGt01AnyCopper); "
        "1x Steel Rod [gregtech:gt.metaitem.01@23305]; "
        "1x Refined Iron Plate [IC2:itemPlates@5] (any ore:plateSteel)"
    }
    assert recipe_oracle.chained("copper wire", row) == "gregtech:gt.blockmachines@1360"
    assert recipe_oracle.chained("steel rods", row) == "gregtech:gt.metaitem.01@23305"
    # an oredict slot that shows another item: no slot is named "steel plate"
    assert recipe_oracle.chained("steel plate", row) is None
    assert (
        recipe_oracle.chained("steel", row) is None
    )  # a word of a name isn't its name


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
    assert "correct: 11 of 11" in printed and "Result: PASS" in printed
    assert "\textra\twhy\t" in printed and "extra citations" in printed
    rows = table.read_text(encoding="utf-8").splitlines()
    header = rows[0].split("\t")
    assert header[:4] == ["id", "kind", "lane", "pass"] and len(rows) == 12
    assert header[5:8] == ["cited", "extra", "why"]
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


# --- the instant template -------------------------------------------------------------------


def test_the_instant_answer_names_the_first_row_without_a_flag(
    lane_reg: Registry,
) -> None:
    """The kiln's fake? row is listed first; the instant answer names the first without a
    flag, as the skill does, and says so."""
    pre = prefetch.prefetch(lane_reg, router.route("recipe for steel"))
    text = ask.card_text(pre, router.RECIPES)
    assert text is not None and text.startswith("Steel Ingot: 5 recipes, lowest tier")
    assert f"The first unflagged one is Bricked Blast Furnace ({PBF})." in text
    assert pre.main is not None and pre.main.parsed.rows[0]["ref"] == KILN  # the card
    uses = prefetch.prefetch(lane_reg, router.route("what uses steel"))
    said = ask.card_text(uses, router.USES)
    assert said is not None and MAC_REC not in said  # the recycling row isn't named


# --- the deep lane, with the real CLI and the oracle model ----------------------------------

DEEP_Q = recipe_eval.question(
    "dq",
    "multistep",
    ANSWER,
    "Compare the EBF and the bricked blast furnace recipes for steel.",
    EBF_AND_PBF,
    "eut,dur ; dur",
    "steel ; steel",
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


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_the_deep_lane_loads_the_skill_and_answers(
    tool_db: Path,
    glossary: Glossary,
    ground: recipe_eval.Ground,
    tmp_path: Path,
    newline: str,
) -> None:
    """The skill file as a Linux or a Windows checkout has it (git's autocrlf writes CRLF):
    the CLI leaves out a skill whose CRLF front matter doesn't parse as YAML."""
    template = tmp_path / "vault-template"
    shutil.copytree(TEMPLATE, template)
    skill = template / "_gos" / "skills" / "recipe-lookup" / "SKILL.md"
    text = SKILL_FILE.read_bytes().decode("utf-8").replace("\r\n", "\n")
    skill.write_bytes(text.replace("\n", newline).encode("utf-8"))
    code, results, api, lane = run_deep(
        tool_db, glossary, ground, tmp_path / "agent", template
    )
    [result] = results
    assert code == 0, (result.score, result.text)
    assert result.lane == "deep" and set(result.score.cited) == {EBF_O2, PBF}
    [task] = lane.tasks()
    assert task.init is not None and SKILL in task.init["skills"]
    # the model is told about it, with its description, before it loads it
    offered = f"{SKILL}: {skill_meta()[0]['description']}"
    assert any(offered in json.dumps(body) for body in api.requests)
    names = [
        b.get("name")
        for body in api.requests
        for m in body.get("messages") or []
        if isinstance(m.get("content"), list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    assert names[0] == "Skill" and "mcp__gos__recipe_find" in names
    # the skill's step 1: the words resolved before the lookup
    assert names.index("mcp__gos__item_resolve") < names.index("mcp__gos__recipe_find")
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
    assert "isn't offered" in results[0].text


def test_the_deep_oracle_loads_only_a_skill_it_is_offered() -> None:
    """As a model would: no skill list naming it, no ``Skill`` call; a failed load ends."""
    policy = deep_policy(recipe_eval.plans([DEEP_Q]))
    request = {"type": "text", "text": f"<request>\n{DEEP_Q.question}\n</request>"}
    listed = {"type": "text", "text": f"Skills:\n- {SKILL}: Recipe and item answers."}
    call = {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": SKILL}}
    error = {
        "type": "tool_result",
        "tool_use_id": "t1",
        "is_error": True,
        "content": "?",
    }

    def turn(*messages: list[dict[str, object]]) -> str:
        roles = ("user", "assistant") * 2
        got = policy(
            {"messages": [{"role": r, "content": m} for r, m in zip(roles, messages)]}
        )
        return f"{got.text} {[name for name, _ in got.tools]}"

    assert "isn't offered" in turn([request])
    assert turn([listed, request]).endswith("['Skill']")
    assert "didn't load" in turn([listed, request], [call], [error])


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


def skill_meta() -> tuple[dict[str, str], str]:
    """The skill file's front matter (``key: value`` lines) and its body."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    return dict(line.split(": ", 1) for line in head.splitlines()), body


def test_the_recipe_lookup_skill(lane_reg: Registry, tmp_path: Path) -> None:
    meta, body = skill_meta()
    assert meta["name"] == "recipe-lookup" and set(meta) == {"name", "description"}
    assert len(meta["description"]) <= 1024
    # plain YAML scalars: the CLI leaves out a skill whose front matter doesn't parse when
    # the file has CRLF line endings (a Windows checkout), and ": " or " #" in a value, or
    # an indicator first, breaks the parse
    for value in meta.values():
        assert not re.search(r": | #|^[-?:,\[\]{}#&*!|>'\"%@`]", value), value
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
    # the flag rule, and no game numbers: its example is placeholders, not a real recipe
    assert "first row without a flag" in body
    assert not re.search(r"r:[\w.]+/[0-9a-f]{12}", body)
    assert not re.search(r"\b(?:eut|dur_t|dur_s): \d", body)
    # the deep lane's plugin copy carries it, as gos:recipe-lookup
    plugin = deep_config.build_plugin(TEMPLATE / "_gos", tmp_path / "plugin")
    assert deep_config.skill_names(plugin) == [SKILL]
    # deployed into a vault, the fast lane's cached prompt lists it (one line, plan 4.4)
    # and carries its body: the fast lane answers recipe questions itself (plan 7.3)
    vault = tmp_path / "vault"
    deploy.deploy(vault)
    rules = system_prompt(vault)[0]["text"]
    assert f"- recipe-lookup: {meta['description']}" in rules
    assert body.strip() in rules and rules.count("# Recipe lookup") == 1
    # without a vault too (the release's copy): the body is the same one source
    assert body.strip() in system_prompt(None)[0]["text"]


def test_the_fast_lane_answers_with_the_card(lane_reg: Registry) -> None:
    """``/api/ask``'s reply carries the recipe card in its card's ``recipe`` key."""
    deep_lane = HandoffRecorder()
    question = recipe_eval.question(
        "c1", "recipe", ANSWER, "How do I make steel?", f"make {STEEL}", "", "steel"
    )
    fast = FastLane(OracleClient(plans=recipe_eval.plans([question])), lane_reg)
    reply = Asker(lane_reg, deep_lane, fast).ask("How do I make steel?")
    card = reply.as_dict()["card"]
    assert card["type"] == "tool_result" and card["recipe"]["total"] == 5
