"""A small made-up CropsNH world for the breeding station's tests (WO-7.6): never pack data.

Wheat + Reed -> Carrot; Carrot + Melon -> Potato; Potato + Pumpkin (+ 1 bone meal) -> Tomato, all
Crop Breeder recipes at LV voltage (the same chain tests/oc/test_station.lua breeds in fakeoc, so
the labels the core builds are the ones the Lua side stocks). Every crop has a Seed Generator
duplication recipe; Tomato's copy uses one bone meal. A fourth path, Wheat + Reed + Melon ->
Gold Leaf, needs HV voltage, and "Wheat II" shares Wheat's name (a label clash).
"""

from __future__ import annotations

from gregtec.crops import facts as crop_facts
from gregtec.ingest.crops import Crop, CropData, ParentRecipe, Soil, SoilBlock

RUN_KEY = "20261113T000000Z"
BONE = ("minecraft:dye", 15, 1)

HAVE = {
    "cropsnh:wheat": (10, 8, 6),
    "cropsnh:reed": (6, 4, 2),
    "cropsnh:melon": (4, 4, 4),
    "cropsnh:pumpkin": (1, 1, 1),
}


def crop(cid: str, name: str, tier: int) -> Crop:
    return Crop(
        id=cid,
        name=name,
        unlocalized=f"crop.{cid}",
        flavour="",
        creator="test",
        soil="farmland",
        soil_lists=("farmland",),
        tier=tier,
        min_seed_bed_tier=-1,
        liked_biomes=(),
        growth_duration=600,
        breeding_threshold=0.8,
        crossing_threshold=0.8,
        weeds_above=24,
        drop_multiplier=1.0,
        requirements=(),
        drops=(),
        alternate_seeds=(),
        sub_soils=(),
        in_nei=True,
    )


CROPS = (
    crop("cropsnh:wheat", "Wheat", 1),
    crop("cropsnh:reed", "Reed", 1),
    crop("cropsnh:melon", "Melon", 2),
    crop("cropsnh:pumpkin", "Pumpkin", 2),
    crop("cropsnh:carrot", "Carrot", 2),
    crop("cropsnh:potato", "Potato", 3),
    crop("cropsnh:tomato", "Tomato", 4),
    crop("cropsnh:goldLeaf", "Gold Leaf", 6),
)


def ref(n: int) -> str:
    return f"r:cropsnh.recipes.cropBreeder/{n:012x}"


def gref(n: int) -> str:
    return f"r:cropsnh.recipes.seedGenerator/{n:012x}"


BREEDER = [
    crop_facts.BreederRecipe(
        ref(1),
        "cropsnh:carrot",
        frozenset({"cropsnh:wheat", "cropsnh:reed"}),
        30,
        200,
        1,
        (),
        (),
    ),
    crop_facts.BreederRecipe(
        ref(2),
        "cropsnh:potato",
        frozenset({"cropsnh:carrot", "cropsnh:melon"}),
        30,
        200,
        1,
        (),
        (),
    ),
    crop_facts.BreederRecipe(
        ref(3),
        "cropsnh:tomato",
        frozenset({"cropsnh:potato", "cropsnh:pumpkin"}),
        120,
        400,
        2,
        (("Bone Meal", 1),),
        (BONE,),
    ),
    crop_facts.BreederRecipe(
        ref(4),
        "cropsnh:goldLeaf",
        frozenset({"cropsnh:wheat", "cropsnh:reed", "cropsnh:melon"}),
        480,
        800,
        3,
        (),
        (),
    ),
]

GENERATOR = {
    c.id: [
        crop_facts.GeneratorRecipe(
            gref(i),
            c.id,
            (("minecraft:dye", 15, 1, "Bone Meal"),)
            if c.id == "cropsnh:tomato"
            else (),
        )
    ]
    for i, c in enumerate(CROPS, 1)
}

MACHINES = {
    "crop_breeder": [
        crop_facts.Machine(28025, "Basic Crop Breeder", 1),
        crop_facts.Machine(28026, "Advanced Crop Breeder", 2),
        crop_facts.Machine(28027, "Advanced Crop Breeder II", 3),
    ],
    "seed_generator": [
        crop_facts.Machine(28013, "Basic Seed Generator", 1),
        crop_facts.Machine(28014, "Advanced Seed Generator", 2),
    ],
}


def data(extra: tuple[Crop, ...] = ()) -> CropData:
    crops = {c.id: c for c in (*CROPS, *extra)}
    recipes = [ParentRecipe(r.child, tuple(sorted(r.parents)), ()) for r in BREEDER]
    return CropData(
        crops=dict(sorted(crops.items())),
        recipes=sorted(recipes, key=lambda r: r.key),
        pools=[],
        soils=[Soil("farmland", (SoilBlock("minecraft:farmland", None),))],
        seeds=[],
    )


def facts(
    breeder: list[crop_facts.BreederRecipe] | None = None,
    extra: tuple[Crop, ...] = (),
    generator: dict[str, list[crop_facts.GeneratorRecipe]] | None = None,
) -> crop_facts.Facts:
    return crop_facts.from_parts(
        data(extra),
        BREEDER if breeder is None else breeder,
        MACHINES,
        {},
        RUN_KEY,
        generator=GENERATOR if generator is None else generator,
    )
