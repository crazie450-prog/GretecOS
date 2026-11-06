"""The normalisation rules (WO-1.3, plan section 5.2 step 3, [A§5.3]) on small made-up values."""

from __future__ import annotations

import random

import pytest

from gregtec.ingest import normalize as n
from gregtec.ingest.normalize import FLUID, ITEM, Recipe, Ref, Slot


def item(name: str, meta: int = 0, nbt: str = "") -> Ref:
    return Ref(ITEM, name, meta, nbt)


def recipe(
    inputs: list[Slot],
    outputs: list[Slot],
    *,
    eut: int = 30,
    duration: int = 100,
    category: str | None = None,
    meta: dict[str, n.Json] | None = None,
) -> Recipe:
    return Recipe(
        source="gt",
        space="gt.recipe.mixer",
        inputs=inputs,
        outputs=outputs,
        category=category,
        eut=eut,
        duration=duration,
        amperage=1,
        meta=meta or {},
    ).seal()


@pytest.mark.parametrize(
    ("value", "want"), [(5, 5), ("144L", 144), ("1B", 1), ("-3S", -3), ("7", 7)]
)
def test_typed_int(value: object, want: int) -> None:
    assert n.typed_int(value) == want


@pytest.mark.parametrize("value", ["1.0F", "x", None, True, 1.5])
def test_typed_int_refuses_non_integers(value: object) -> None:
    with pytest.raises(ValueError, match="not an integer"):
        n.typed_int(value)


@pytest.mark.parametrize(
    ("eut", "amps", "tier"),
    [
        (0, 1, 0),
        (8, 1, 0),
        (9, 1, 1),
        (30, 1, 1),
        (32, 1, 1),
        (33, 1, 2),
        (120, 1, 2),
        (7680, 1, 5),
        (8192, 1, 5),
        (8193, 1, 6),
        (64, 2, 1),
        (65, 2, 2),
        (-20, 1, 0),
    ],
)
def test_voltage_tier(eut: int, amps: int, tier: int) -> None:
    # Smallest t with 8 * 4^t >= eut / amps: ULV 8, LV 32, MV 128, ..., IV 8192.
    assert n.voltage_tier(eut, amps) == tier


def test_gt_nbt_loses_numeric_ids_where_gregteclink_named_the_item() -> None:
    gt = {
        "output": {"Count": "1B", "Damage": "0S", "__item": "test:plate", "id": "628S"},
        "title": "Plate Construction Data",
    }
    assert n.normalize_nbt(gt) == {
        "output": {"Count": "1B", "Damage": "0S", "__item": "test:plate"},
        "title": "Plate Construction Data",
    }


def test_nei_nbt_is_named_through_the_runs_id_table() -> None:
    ids = n.item_ids({"x": [{"__item": "test:plate", "id": "628S", "Count": "1B"}]}, {})
    assert ids == {628: "test:plate"}
    gt = {
        "output": {"Count": "1B", "Damage": "0S", "__item": "test:plate", "id": "628S"}
    }
    nei = {"output": {"Count": "1B", "Damage": "0S", "id": "628S"}}
    assert n.normalize_nbt(nei, ids) == n.normalize_nbt(gt)
    assert n.nbt_hash(n.normalize_nbt(nei, ids)) == n.nbt_hash(n.normalize_nbt(gt))
    # An ID the run doesn't know names nothing, but still never reaches a key.
    unknown = n.normalize_nbt({"Count": "1B", "Damage": "0S", "id": "9S"}, ids)
    assert unknown == {"Count": "1B", "Damage": "0S", "__item": None}
    # Other shapes keep their numbers (the contract: only ItemStack-shaped compounds).
    ench = {"ench": [{"id": "14S", "lvl": "5S"}]}
    assert n.normalize_nbt(ench, ids) == ench


def test_nbt_hash_is_empty_for_no_nbt_and_ignores_key_order() -> None:
    assert n.nbt_hash(None) == n.nbt_hash({}) == ""
    assert n.nbt_hash({"a": 1, "b": "2B"}) == n.nbt_hash({"b": "2B", "a": 1}) != ""


def test_circuits_and_not_consumed_inputs() -> None:
    circuit = n.input_slot(item(n.CIRCUIT, 7), 0)
    assert (circuit.consumed, circuit.circuit, circuit.amount) == (False, 7, 0)
    mold = n.input_slot(item("test:mold"), 0)
    assert (mold.consumed, mold.circuit) == (False, None)
    used = n.input_slot(
        item(n.CIRCUIT, 7), 1
    )  # a consumed circuit item is just an item
    assert (used.consumed, used.circuit) == (True, None)


def test_identity_ignores_slot_order_and_numbers_but_not_amounts() -> None:
    ins = [n.input_slot(item("test:dust", i), 1 + i) for i in range(4)]
    ins.append(n.input_slot(Ref(FLUID, "water"), 1000))
    outs = [Slot(item("test:out", i), 1, 5000 + i) for i in range(5)]
    a = recipe(ins, outs)
    shuffled = ins[:], outs[:]
    random.Random(1).shuffle(shuffled[0])
    random.Random(2).shuffle(shuffled[1])
    b = recipe(*shuffled)
    assert (a.signature, a.content_hash) == (b.signature, b.content_hash)
    rebalanced = recipe(ins, outs, eut=120, duration=50)
    assert rebalanced.signature == a.signature != ""
    assert rebalanced.content_hash != a.content_hash
    more = recipe([*ins[:-1], n.input_slot(Ref(FLUID, "water"), 2000)], outs)
    assert more.signature != a.signature
    other_category = recipe(ins, outs, category="gt.recipe.category.mixer_recycling")
    assert other_category.signature != a.signature


def test_chances_alternatives_and_metadata_are_content() -> None:
    ins = [
        n.input_slot(
            item("test:dye"), 1, ore="dyeBlack", alts=[item("test:dye"), item("x:ink")]
        )
    ]
    outs = [Slot(item("test:glass"), 8)]
    base = recipe(ins, outs)
    plain = recipe([n.input_slot(item("test:dye"), 1)], outs)
    chanced = recipe(ins, [Slot(item("test:glass"), 8, 2500)])
    heat = recipe(ins, outs, meta={"coil_heat": 1800})
    desc = recipe(ins, outs, meta={"nei_desc": ["Needs a coil"]})
    assert {plain.signature, chanced.signature, heat.signature} == {base.signature}
    hashes = {
        base.content_hash,
        plain.content_hash,
        chanced.content_hash,
        heat.content_hash,
    }
    assert len(hashes) == 4
    assert desc.content_hash == base.content_hash  # display text only


def test_nei_only_recipes_key_an_oredict_slot_by_what_it_accepts() -> None:
    oak = item("minecraft:planks")
    any_plank = n.input_slot(oak, 1, alts=[oak, item("minecraft:planks", 1)])
    only_oak = n.input_slot(oak, 1)
    out = [Slot(item("test:table"), 1)]
    a = Recipe("crafting", "h", [any_plank], out, alt_identity=True).seal()
    b = Recipe("crafting", "h", [only_oak], out, alt_identity=True).seal()
    assert a.signature != b.signature
    # GT-style identity keys the slot by its displayed item only.
    assert n.signature("h", None, [any_plank], out) == n.signature(
        "h", None, [only_oak], out
    )


def test_nei_only_content_ignores_the_displayed_stack_and_its_container() -> None:
    # NEI shows a different accepted stack on each run (a water bucket, then a water cell).
    bucket, cell = item("minecraft:water_bucket"), item("IC2:itemCellEmpty", 1)
    out = [Slot(item("test:clay"), 1)]

    def nei_only(shown: Ref, returns: Ref | None) -> Recipe:
        s = n.input_slot(shown, 1, alts=[bucket, cell], returns=returns)
        return Recipe("crafting", "h", [s], out, alt_identity=True).seal()

    a = nei_only(bucket, item("minecraft:bucket"))
    b = nei_only(cell, None)
    assert (a.signature, a.content_hash) == (b.signature, b.content_hash)
    # Once oredict-named, accepting one more stack changes the content, not the identity.
    named = n.input_slot(bucket, 1, ore="listAllwater", alts=[bucket, cell])
    more = n.input_slot(
        bucket, 1, ore="listAllwater", alts=[bucket, cell, item("x:water")]
    )
    c = Recipe("crafting", "h", [named], out, alt_identity=True).seal()
    d = Recipe("crafting", "h", [more], out, alt_identity=True).seal()
    assert c.signature == d.signature != a.signature
    assert c.content_hash != d.content_hash


def test_ore_index_names_exactly_the_accepted_set() -> None:
    oak, birch = item("minecraft:planks", 0), item("minecraft:planks", 2)
    gt_wood = item("gregtech:gt.metaitem.01", 17809)
    block0, block1 = item("test:block", 0), item("test:block", 1)
    gem = item("test:gem", 0, "ab" * 32)
    ores = n.OreIndex.build(
        [
            (item("minecraft:planks", n.WILDCARD), ["plankWood"]),
            (oak, ["plankWood"]),
            (gt_wood, ["plankWood"]),
            (block0, ["blockBronze", "blockAnyBronze"]),
            (block1, ["blockBronze", "blockAnyBronze"]),
            (gem, ["gemTest"]),
        ]
    )
    # The wildcard member stands for every plank NEI expands it to.
    assert ores.name([oak, birch, gt_wood]) == "plankWood"
    # Less than all of plankWood (no GT wood plank), or more than it: no name.
    assert ores.name([oak, birch]) is None
    assert ores.name([oak, gt_wood, item("test:stick")]) is None
    # Two names with the same members (GT's "Any" names): the first by name.
    assert ores.name([block0, block1]) == "blockAnyBronze"
    assert ores.name([block0]) is None
    # NBT counts; fluids and nothing have no oredict name.
    assert ores.name([gem]) == "gemTest"
    assert ores.name([item("test:gem", 0)]) is None
    assert ores.name([Ref(FLUID, "water")]) is None
    assert ores.name([]) is None


def test_citation_key() -> None:
    sig = "0123456789abcdef" * 4
    assert n.cite("gt.recipe.mixer", sig) == "r:gt.recipe.mixer/0123456789ab"
    assert n.cite("gt.recipe.mixer", sig, 2) == "r:gt.recipe.mixer/0123456789ab-2"
