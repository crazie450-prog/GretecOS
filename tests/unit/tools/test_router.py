"""WO-2.3: the router's rules (D150) and the TSV parser the router and the fast lane read with."""

from __future__ import annotations

import pytest

from gregtec.agents import router
from gregtec.agents.tools.tsv import Result, parse, render, unescape


@pytest.mark.parametrize(
    ("text", "kind", "intent", "subject", "tier"),
    [
        # instant commands: no model
        ("repeat that", "instant", "repeat", None, None),
        ("Say it again.", "instant", "repeat", None, None),
        ("confirm", "instant", "confirm", None, None),
        ("never mind", "instant", "cancel", None, None),
        ("What's my tier?", "instant", "tier", None, None),
        ("what tier am I at", "instant", "tier", None, None),
        ("recipe for stainless steel", "instant", "recipes", "stainless steel", None),
        ("Show me the recipes for the EBF", "instant", "recipes", "ebf", None),
        ("steel gear recipe", "instant", "recipes", "steel gear", None),
        ("recipes for a vacuum tube at LV", "instant", "recipes", "vacuum tube", "LV"),
        ("what uses red alloy?", "instant", "uses", "red alloy", None),
        ("uses of tin", "instant", "uses", "tin", None),
        # deep lane
        ("Design me a steel line at MV", "deep", "handoff", None, None),
        (
            "compare the EBF and the bricked blast furnace",
            "deep",
            "handoff",
            None,
            None,
        ),
        (
            "How many EBFs do I need for 2 steel per second?",
            "deep",
            "handoff",
            None,
            None,
        ),
        ("what should I do next", "deep", "handoff", None, None),
        ("is a steel line worth automating", "deep", "handoff", None, None),
        ("recipe for a steel line plan", "deep", "handoff", None, None),
        # fast lane, with what pre-retrieval fetches
        ("How do I make steel?", "fast", "recipe", "steel", None),
        (
            "hey gos, how do I make an LV motor please",
            "fast",
            "recipe",
            "lv motor",
            None,
        ),
        ("How is rubber made at LV?", "fast", "recipe", "rubber", "LV"),
        ("what machine makes sulfuric acid", "fast", "recipe", "sulfuric acid", None),
        ("What can I make with a vacuum tube?", "fast", "uses", "vacuum tube", None),
        ("What is steel used for?", "fast", "uses", "steel", None),
        ("What does the EBF do?", "fast", "item", "ebf", None),
        ("Tell me about the steel hull.", "fast", "item", "steel hull", None),
        ("Which quest asks for steel?", "fast", "quest_item", "steel", None),
        ("what quests can I do now", "fast", "frontier", None, None),
        ("What are my next quests?", "fast", "frontier", None, None),
        (
            "tell me about the quest called first steps",
            "fast",
            "quests",
            "first steps",
            None,
        ),
        ("Why does GregTech have so many tiers?", "fast", "general", None, None),
        # a question that only looks like "X recipe"
        ("what's the best steel recipe", "fast", "general", None, None),
        ("which is the cheapest steel recipe", "fast", "general", None, None),
        # words that name no item, and a leading count
        ("show me the recipes", "fast", "general", None, None),
        ("recipes for 2 steel", "instant", "recipes", "steel", None),
        ("recipe for 16x copper wire", "instant", "recipes", "16x copper wire", None),
        # lookups that only sound quantitative or like planning
        ("How many steel ingots does the EBF need?", "fast", "general", None, None),
        ("what does the plan say about steel", "fast", "general", None, None),
        ("plan my power line", "deep", "handoff", None, None),
        ("how many macerators do I need", "deep", "handoff", None, None),
    ],
)
def test_route(
    text: str, kind: str, intent: str, subject: str | None, tier: str | None
) -> None:
    got = router.route(text)
    assert (got.kind, got.intent, got.subject, got.tier) == (
        kind,
        intent,
        subject,
        tier,
    )
    assert got.text == text  # the request is kept as given


def test_normalise() -> None:
    assert (
        router.normalise("  Hey GOS:  How do   I make Steel?! ")
        == "how do i make steel"
    )
    assert router.normalise("recipe for steel, please.") == "recipe for steel"
    assert router.normalise("what’s my tier") == "what's my tier"


def test_subject_must_look_like_an_item() -> None:
    assert router.subject_of("the steel ingot") == "steel ingot"
    assert router.subject_of("what is best") is None
    assert router.subject_of(" ".join(["word"] * 9)) is None


def test_parse_round_trips_a_rendered_result() -> None:
    weird = "a\tb\nc\\d\x01e "
    text = render(
        Result(
            ("ref", "name"),
            [("r:m/0123456789ab", weird), ("r:m/ba9876543210", "plain")],
            head=[("item", "Steel Ingot [x@1]"), ("ambiguous", 0)],
            notes=["first note", "note: with\ta tab"],
            total=7,
        ),
        "CURSOR",
    )
    p = parse(text)
    assert p.head == {"item": "Steel Ingot [x@1]", "ambiguous": "0"}
    assert p.columns == ["ref", "name"]
    assert [r["name"] for r in p.rows] == [weird, "plain"]
    assert p.span == "1-2 of 7" and p.total == 7
    assert p.notes == ["first note", "note: with\ta tab"]
    assert (
        unescape("\\\\t") == "\\t"
    )  # an escaped backslash before a t stays a backslash


def test_parse_a_result_without_a_table() -> None:
    p = parse(render(Result(head=[("handoff", "h-1")], notes=["said"])))
    assert p.head == {"handoff": "h-1"} and p.rows == [] and p.span is None
    assert p.notes == ["said"] and p.total is None
    one = parse(render(Result(("name",), [("x",)])))  # a one-column table gets n first
    assert one.columns == ["n", "name"] and one.rows == [{"n": "1", "name": "x"}]
