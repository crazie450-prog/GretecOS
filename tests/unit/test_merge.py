"""GT vs NEI merge rules and the collision suffixes (WO-1.3, D105, D106) on made-up recipes."""

from __future__ import annotations

import pytest

from gregtec.ingest import merge
from gregtec.ingest.normalize import ITEM, WILDCARD, Recipe, Ref, Slot, input_slot

NBT = "cd" * 32
PACKAGER = "gt.recipe.packager"


def item(name: str, meta: int = 0, nbt: str = "") -> Ref:
    return Ref(ITEM, name, meta, nbt)


def engraver(ins: list[Slot], *, nbt_sensitive: bool = False) -> Recipe:
    return Recipe(
        source="gt",
        space="gt.recipe.laserengraver",
        inputs=ins,
        outputs=[Slot(item("test:lapotron"), 1)],
        category="gt.recipe.laserengraver",
        eut=480,
        duration=200,
        amperage=1,
        flags={"nbt_sensitive": nbt_sensitive},
    ).seal()


def nei_engraver(ins: list[Slot]) -> Recipe:
    return Recipe(
        source="nei",
        space="gt.recipe.laserengraver",
        inputs=ins,
        outputs=[Slot(item("test:lapotron"), 1)],
        handler="gt.recipe.laserengraver",
        eut=480,
        duration=200,
    ).seal()


@pytest.mark.parametrize("meta", [WILDCARD, 1])
def test_a_gt_input_without_nbt_matches_neis_stack_with_nbt(meta: int) -> None:
    # GT ignores NBT for a recipe that isn't NBT-sensitive; NEI shows a charged battery pack.
    shown = nei_engraver([input_slot(item("IC2:itemArmorBatpack", 1, NBT), 1)])
    plain = engraver([input_slot(item("IC2:itemArmorBatpack", meta), 1)])
    assert merge.match(plain, shown) is not None
    sensitive = engraver(
        [input_slot(item("IC2:itemArmorBatpack", meta), 1)], nbt_sensitive=True
    )
    assert merge.match(sensitive, shown) is None
    # Another meta, or other NBT on GT's side, still doesn't match.
    other = engraver([input_slot(item("IC2:itemArmorBatpack", 2), 1)])
    assert merge.match(other, shown) is None
    tagged = engraver([input_slot(item("IC2:itemArmorBatpack", meta, "ef" * 32), 1)])
    assert merge.match(tagged, shown) is None
    merged, stats = merge.merge(
        [plain], [shown], {"gt.recipe.laserengraver": plain.space}
    )
    assert (stats.matched, sum(stats.dropped.values())) == (1, 0)
    assert merged == [plain] and plain.source == "gt+nei"


def test_lines_of_handlers_that_are_not_recipes_are_left_out() -> None:
    handler = "codechicken.nei.recipe.FuelRecipeHandler"
    fuel = Recipe(
        "nei",
        handler,
        [input_slot(item("test:ore"), 1)],
        [Slot(item("test:ingot"), 1)],
        handler=handler,
    ).seal()
    merged, stats = merge.merge([], [fuel], {})
    assert merged == [] and stats.not_recipes == {handler: 1} and stats.nei_only == 0


def pair(duration_a: int = 100, duration_b: int = 20) -> list[Recipe]:
    """One packing recipe registered twice, at two durations: a genuine collision."""
    ins = [input_slot(item("test:dust"), 1)]
    outs = [Slot(item("test:tiny"), 4)]
    return [
        Recipe("gt", PACKAGER, ins, outs, eut=4, duration=d).seal()
        for d in (duration_a, duration_b)
    ]


def content12(r: Recipe) -> str:
    return r.content_hash[:12]


def allow(recipes: list[Recipe]) -> dict[tuple[str, str], tuple[str, ...]]:
    """The allow-list row for ``recipes`` in the order given (-1 first)."""
    return {(PACKAGER, recipes[0].signature[:12]): tuple(map(content12, recipes))}


def test_allow_listed_suffixes_follow_the_list() -> None:
    a, b = pair()
    keys = merge.assign_keys([a, b], allow([b, a]))
    assert (a.ref[-2:], b.ref[-2:]) == ("-2", "-1")
    assert keys.relisted == [] and keys.stale == []


def test_a_rebalanced_recipe_keeps_its_suffix() -> None:
    a, b = pair()
    allowed = allow([a, b])
    a2, b2 = pair(100, 30)  # b rebalanced: 30 ticks now
    keys = merge.assign_keys([b2, a2], allowed)
    assert (a2.ref[-2:], b2.ref[-2:]) == ("-1", "-2")
    assert keys.relisted == [(b2.ref, content12(b2))]


def test_a_group_the_list_cant_tell_apart_fails() -> None:
    a, b = pair()
    allowed = allow([a, b])
    with pytest.raises(merge.CollisionError) as err:
        merge.assign_keys(pair(90, 30), allowed)  # both rebalanced
    (c,) = err.value.collisions
    assert c.listed == allowed[(PACKAGER, a.signature[:12])]
    # A third recipe in the group: no suffix is left for it.
    extra = pair(100, 50)[1]
    with pytest.raises(merge.CollisionError):
        merge.assign_keys([*pair(), extra], allowed)


def test_duplicates_are_counted_by_source() -> None:
    a, copy = pair(100, 100)
    crafted = [
        Recipe(
            "crafting",
            "h",
            [input_slot(item("x:a"), 1)],
            [Slot(item("x:b"), 1)],
            alt_identity=True,
        ).seal()
        for _ in range(2)
    ]
    keys = merge.assign_keys([a, copy, *crafted], {})
    assert keys.duplicates == {"gt": 1, "nei": 1}
    assert len(keys.recipes) == 2
