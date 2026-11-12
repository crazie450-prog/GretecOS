"""The crop guide (gregtec.crops.planner, D273, D274) over the made-up world
(tests/fixtures/crops/cropworld.py). Expected odds and times are worked by hand from
gregtec.crops.rules' tests."""

from __future__ import annotations

import dataclasses
from types import ModuleType

import pytest

from gregtec.crops import planner as P


@pytest.fixture
def facts(world: ModuleType):  # noqa: ANN201 - the fixture module's Facts
    return world.facts()


def kinds(g: P.Guide) -> list[tuple[str, str]]:
    return [(s.kind, s.child) for s in g.steps]


def nums(lines: list[P.Line]) -> list[str]:
    return [x.text() for x in lines]


def test_a_recipe_path_from_alternate_seeds(facts) -> None:  # noqa: ANN001
    g = P.guide(facts, "cropsnh:reed")
    assert kinds(g) == [
        ("obtain", "cropsnh:carrot"),
        ("obtain", "cropsnh:wheat"),
        ("sticks", "cropsnh:reed"),
    ]
    step = g.steps[2]
    assert step.parents == ("cropsnh:carrot", "cropsnh:wheat")
    # 1/3 tries x half breed x the one recipe that matches = 1/6
    assert step.numbers[0].text() == "Chance per growth tick (every 256 ticks): 16.7%"
    # Wheat and Carrot (tier 1, Growth 1) at 27 points grow 15 a tick: 32 ticks to 480/600
    # = 409.6 s; then 6 ticks expected (76.8 s), 14 for 90% (1 - (5/6)^13 < 0.9)
    assert step.numbers[1].text() == (
        "Parents reach their breeding threshold (80% grown) after 7 min"
    )
    assert step.numbers[2].text() == "Then 77 s expected, 3 min for a 90% chance"
    assert step.expected_s == pytest.approx(409.6 + 76.8)
    # the other children: a copy of either parent, 1/12 each, for every 1/6 of reed
    assert step.numbers[3].text().startswith("Expect 1.0 other children")
    assert g.next_step() is g.steps[0] and not any(s.done for s in g.steps)
    assert P.check(g, facts) == []


def test_the_layout_puts_parents_north_and_south_of_the_double_stick(facts) -> None:  # noqa: ANN001
    s = P.guide(facts, "cropsnh:stoneLily").steps[-1]
    grid = [[c.role for c in row] for row in s.layout]
    assert grid == [
        ["empty", "parent", "empty"],
        ["empty", "double", "empty"],
        ["empty", "parent", "empty"],
    ]
    assert (
        s.layout[1][1].crop == "cropsnh:stoneLily" and s.layout[1][1].under == "Stone"
    )
    assert {s.layout[0][1].crop, s.layout[2][1].crop} == {
        "cropsnh:carrot",
        "cropsnh:reed",
    }
    assert any("east and west" in w.text() for w in s.warnings)


def test_a_sub_soil_block_is_needed_unless_my_base_stocks_it(facts) -> None:  # noqa: ANN001
    def sub(g: P.Guide) -> P.Requirement:
        return next(r for r in g.steps[-1].requirements if r.what == "sub_soil")

    g = P.guide(facts, "cropsnh:stoneLily")
    assert sub(g).status == "needed"
    assert (
        "Needs a block of stone under it to grow: Stone directly under"
        in sub(g).line.text()
    )
    g = P.guide(facts, "cropsnh:stoneLily", stock={"minecraft:stone@0"})
    assert sub(g).status == "met"


def test_crops_he_has_are_never_steps(facts) -> None:  # noqa: ANN001
    g = P.guide(facts, "cropsnh:stoneLily", {"cropsnh:reed": (10, 10, 10)})
    assert kinds(g) == [("obtain", "cropsnh:carrot"), ("sticks", "cropsnh:stoneLily")]
    keep = nums(g.steps[-1].keep)
    assert "Resistance 1" in keep[1]  # the child's stats as listed: none yet
    g = P.guide(facts, "cropsnh:stoneLily", {"cropsnh:stoneLily": (31, 31, 31)})
    assert g.steps == [] and g.next_step() is None


def test_a_pool_only_crop_breeds_from_two_of_one_member(facts) -> None:  # noqa: ANN001
    g = P.guide(facts, "cropsnh:weedy")
    assert kinds(g)[-1] == ("pool", "cropsnh:weedy")
    s = g.steps[-1]
    # Carrot + Wheat would match Reed's recipe and never reach the pool: two Carrots it is,
    # 1/3 x 1/2 x one pool x 1/3 members = 1/18
    assert s.parents == ("cropsnh:carrot", "cropsnh:carrot")
    assert s.numbers[0].text() == "Chance per growth tick (every 256 ticks): 5.56%"
    assert "mutation pool Green" in s.title
    assert P.check(g, facts) == []


def test_a_crop_breeder_step_at_and_below_its_tier(facts) -> None:  # noqa: ANN001
    g = P.guide(facts, "cropsnh:ironFern", tier=2)
    s = g.steps[-1]
    assert s.kind == "breeder" and s.parents == ("cropsnh:reed", "cropsnh:stoneLily")
    machine = next(r for r in s.requirements if r.what == "machine")
    assert machine.status == "met" and "Advanced Crop Breeder" in machine.line.text()
    text = nums(s.numbers)
    assert text[0].startswith("Success chance at your tier: 50%")
    # tier 6 x 144 + (1 + 1 + 1) x 72
    assert (
        text[1]
        == "Enriched fertilizer per run: 1,080 mB (for 3 stat points of Growth + Gain + Resistance)"
    )
    assert text[2].startswith("Make 7 copies of each parent first")  # 1 - 0.5^7 > 0.99
    assert "One run: 20 s at 120 EU/t" in text
    cat = next(r for r in s.requirements if r.what == "catalyst")
    assert cat.line.text() == "Catalyst: 1 x Iron Ore"
    assert s.numbers[0].nums[0].refs == ("mte:28026", "crop:cropsnh:ironFern")
    assert P.BREEDER_CONFIDENCE and P.check(g, facts) == []
    low = P.guide(facts, "cropsnh:ironFern", tier=0).steps[-1]
    assert next(r for r in low.requirements if r.what == "machine").status == "needed"


def test_a_crop_manager_step_from_lv_done_once_my_base_has_one(facts) -> None:  # noqa: ANN001
    g = P.guide(facts, "cropsnh:reed", tier=1)
    first = g.steps[0]
    assert (
        first.kind == "crop_manager" and first.n == 1 and g.steps[-1].n == len(g.steps)
    )
    assert nums(first.numbers)[0] == (
        "It reaches 5 blocks on each side (11 blocks square) and 2 blocks up and down"
    )
    assert g.next_step() is first
    g = P.guide(facts, "cropsnh:reed", tier=1, owned={28001})
    assert g.steps[0].done and g.next_step() is g.steps[1]
    assert P.guide(facts, "cropsnh:reed", tier=0).steps[0].kind == "obtain"


def test_the_stats_plan(facts) -> None:  # noqa: ANN001
    by_hand = P.guide(facts, "cropsnh:reed", tier=0).stats
    lv_no_manager = P.guide(facts, "cropsnh:reed", tier=1).stats
    managed = P.guide(facts, "cropsnh:reed", tier=1, owned={28001}).stats
    assert by_hand is not None and managed is not None and lv_no_manager is not None
    # by hand the fertilizer runs out a growth tick at a time: the low list (review M1)
    assert by_hand.variations == (-1, -1, 0, 1, 1)
    # LV alone doesn't give 200: only a Crop Manager My base lists does (review M2)
    assert lv_no_manager.variations == (-1, -1, 0, 1, 1)
    assert managed.variations == (0, 0, 0, 1, 2, 3, 4)  # a Crop Manager's 200
    lines = nums(by_hand.lines)
    assert (
        "Children vary by one of -1, -1, 0, +1, +1 at 0 fertilizer (by hand it runs out)"
        in lines
    )
    # topped up to 100 it stays at 50 or more for 51 growth ticks (100 down to 50)
    assert (
        "Topped up to 100 fertilizer at least every 11 min (51 growth ticks) a stick stays at "
        "50 or more and children draw from -2, -1, 0, +1, +2 instead"
    ) in lines
    assert any(
        x.startswith("Kept topped up, the slowest stat takes about ") for x in lines
    )
    assert not any("Topped up" in x for x in nums(managed.lines))
    assert by_hand.target == (31, 31, 31)
    assert nums(managed.lines)[0].startswith("Growth 1 to 31: about ")
    assert any("raise Resistance first" in w.text() for w in managed.warnings)
    assert any("tank allows at most" in w.text() for w in managed.warnings)


def test_no_way_and_unknown_crops(facts) -> None:  # noqa: ANN001
    with pytest.raises(P.GuideError, match="no way to Lonely"):
        P.guide(facts, "cropsnh:lonely")
    with pytest.raises(P.GuideError, match="no crop"):
        P.guide(facts, "cropsnh:nope")


def test_without_alternate_seeds_only_his_crops_start_a_path(facts) -> None:  # noqa: ANN001
    with pytest.raises(P.GuideError):
        P.guide(facts, "cropsnh:reed", alternate_seeds=False)
    have = {"cropsnh:wheat": (1, 1, 1), "cropsnh:carrot": (1, 1, 1)}
    g = P.guide(facts, "cropsnh:reed", have, alternate_seeds=False)
    assert kinds(g) == [("sticks", "cropsnh:reed")]


def test_the_validator_finds_uncited_numbers_and_unknown_refs(facts) -> None:  # noqa: ANN001
    g = P.guide(facts, "cropsnh:reed")
    assert P.check(g, facts) == []
    s = g.steps[-1]
    s.numbers.append(P.Line("{0} a tick", (P.Num(1.0, "%", (), "1%"),)))
    s.numbers.append(P.Line("about 5 minutes"))
    s.numbers.append(P.Line("{0}", (P.Num(2, "", ("crop:cropsnh:nope",), "2"),)))
    s.numbers.append(P.Line("{0}", (P.Num(2, "", ("r:nope/000000000000",), "2"),)))
    s.numbers.append(P.Line("{0}", (P.Num(2, "", ("mte:1",), "2"),)))
    s.numbers.append(P.Line("{0}", (P.Num(2, "", ("bad ref",), "2"),)))
    problems = P.check(g, facts)
    assert len(problems) == 6
    assert problems[0] == "uncited number '1%'"
    assert problems[1] == "digits in an uncited line: 'about 5 minutes'"


def test_the_dicts_carry_every_number_with_its_refs(facts) -> None:  # noqa: ANN001
    g = P.guide(facts, "cropsnh:ironFern", tier=2)
    d = P.guide_dict(g)
    assert d["target"] == "cropsnh:ironFern" and d["ref"] == "crop:cropsnh:ironFern"
    assert d["next"] == 1 and d["tier_name"] == "MV"
    step = d["steps"][-1]
    assert step["kind"] == "breeder"
    for line in step["numbers"]:
        assert line["refs"] and all(n["refs"] for n in line["nums"])
    assert d["stats"]["target"] == [31, 31, 31]


def test_the_pm_picks_cover_the_acceptance_shapes(world: ModuleType) -> None:
    """Five guides, every number cited: the made-up world's stand-ins for the five picks (one
    needs a sub-soil block, one the Crop Breeder)."""
    f = world.facts()
    picks = [
        "cropsnh:reed",
        "cropsnh:stoneLily",
        "cropsnh:ironFern",
        "cropsnh:weedy",
        "cropsnh:carrot",
    ]
    guides = [P.guide(f, c, tier=2) for c in picks]
    assert any(s.kind == "breeder" for g in guides for s in g.steps)
    assert any(
        r.what == "sub_soil" for g in guides for s in g.steps for r in s.requirements
    )
    for g in guides:
        assert P.check(g, f) == [], g.target


def test_nutrients_show_the_dry_stick_and_its_upkeep(world: ModuleType) -> None:
    """A stick uses 1 water and 1 fertilizer every growth tick (review M1): the requirement
    shows the dry score too, and a crop that gets sick once they run out is a "check"."""

    def need(g: P.Guide, crop: str) -> P.Requirement:
        return next(
            r
            for s in g.steps
            for r in s.requirements
            if r.what == "nutrients" and crop in r.line.text()
        )

    reed = need(P.guide(world.facts(), "cropsnh:reed"), "Child Reed")
    assert reed.status == "met"  # tier 2 needs 4: even dry it grows at full speed
    facts = world.facts()
    crops = facts.data.crops
    crops["cropsnh:reed"] = dataclasses.replace(crops["cropsnh:reed"], tier=6)
    g = P.guide(facts, "cropsnh:reed")
    hot = need(g, "Child Reed")
    # tier 6 needs 12; topped up 27 (5 + 10 + 10 + 2 sky); dry 7 (5 + 2): 35 of 60 scaled is
    # 25 short, 100 - 4 x 25 = 0, so it gets sick
    assert hot.status == "check"
    assert hot.line.text() == (
        "Child Reed: needs 12 points for full speed; topped up with water and fertilizer "
        "it gets 27 points, and 7 points once they run dry (a stick uses 1 of each every growth "
        "tick: 21 min from 100): top them up before they run out, or it gets sick"
    )
    assert P.check(g, facts) == []
    assert "best case" in g.notes[0].text()
    managed = P.guide(facts, "cropsnh:reed", tier=1, owned={28001})
    hot = need(managed, "Child Reed")
    assert hot.status == "met"
    assert "the Crop Manager keeps it at 27 points" in hot.line.text()
    assert "kept topped up by the Crop Manager" in managed.notes[0].text()


def test_the_crop_manager_step_says_its_switches_start_off(facts) -> None:  # noqa: ANN001
    s = P.guide(facts, "cropsnh:reed", tier=1).steps[0]
    req = next(r for r in s.requirements if r.what == "machine")
    assert req.status == "check" and "start switched off" in req.line.text()
    assert req.line.refs == ("mte:28001", "crop:cropsnh:reed")


def test_a_parent_that_never_grows_has_no_time_and_no_total(facts) -> None:  # noqa: ANN001
    """Review L4: a tier-16 parent is sick at any nutrients the setup gives."""
    crops = facts.data.crops
    crops["cropsnh:carrot"] = dataclasses.replace(crops["cropsnh:carrot"], tier=16)
    g = P.guide(facts, "cropsnh:reed")
    step = g.steps[-1]
    assert step.kind == "sticks" and step.expected_s is None
    assert nums(step.numbers)[1].startswith(
        "A parent doesn't grow at the assumed setup"
    )
    assert not any("after 0 s" in x for x in nums(step.numbers))
    assert g.total is None
    assert any("No total time" in n.text() for n in g.notes)
    carrot = next(
        r
        for r in step.requirements
        if r.what == "nutrients" and "Carrot" in r.line.text()
    )
    assert carrot.status == "needed"
    assert P.check(g, facts) == []


def test_a_much_faster_pool_is_noted_never_chosen(facts, world: ModuleType) -> None:  # noqa: ANN001
    """The advisor's D274 suggestion: the recipe path stays first; a pool path at most half
    its time is a note."""
    crops = facts.data.crops
    crops["cropsnh:wheat"] = dataclasses.replace(
        crops["cropsnh:wheat"], growth_duration=60000
    )
    pool = facts.data.pools[0]
    facts.data.pools[0] = dataclasses.replace(
        pool, members=(*pool.members, "cropsnh:reed")
    )
    g = P.guide(facts, "cropsnh:reed")
    assert g.steps[-1].kind == "sticks"  # recipes first
    note = g.notes[-1].text()
    assert note.startswith(
        "Faster but a mutation pool's luck: Carrot + Carrot on sticks gives Reed in about "
    )
    assert "for the path below" in note
    assert P.check(g, facts) == []
    assert not any(
        "pool" in n.text() for n in P.guide(world.facts(), "cropsnh:reed").notes
    )


def test_the_validator_checks_digits_against_the_rows_cited(facts) -> None:  # noqa: ANN001
    """Review L1: a cited line's own digits must be in the rows it cites."""
    g = P.guide(facts, "cropsnh:reed")
    s = g.steps[-1]
    s.warnings.append(P.Line("Wait 5 min", refs=("crop:cropsnh:reed",)))
    s.warnings.append(P.Line("Growth above 24", refs=("crop:cropsnh:reed",)))
    s.warnings.append(P.Line("Basic Crop Manager", refs=("mte:28001",)))
    problems = P.check(g, facts)
    assert problems == ["digits '5' in a line its citations don't hold: 'Wait 5 min'"]
