"""NEI's recipe files: Thaumcraft aspect stacks (Aspect Recipe Index) and unknown stack shapes
(D379). Synthetic lines in the four shapes the full pack's run showed, never pack data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gregtec.ingest import nei
from gregtec.ingest.gtdump import FormatError

ARI = "com.gtnewhorizons.aspectrecipeindex.nei."
ALCHEMY = ARI + "AlchemyRecipeHandler"
INFUSION = ARI + "InfusionRecipeHandler"
COMBINATION = ARI + "AspectCombinationHandler"
CONTAINING = ARI + "ItemsContainingAspectHandler"


def item(reg: str, count: int = 1, **kw: object) -> dict[str, object]:
    return {"strId": reg, "Damage": 0, "Count": count, "name": reg, **kw}


def aspect(tag: str, count: int, name: str = "Unknown Aspect") -> dict[str, object]:
    return {"Count": count, "TCAspect": tag, "name": name}


def line(handler: str, ins: list[object], outs: list[object]) -> dict[str, object]:
    return {"handler": handler, "ingredients": ins, "results": outs}


def write(run: Path, files: dict[str, list[dict[str, object]]]) -> list[str]:
    for rel, lines in files.items():
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(x) + "\r\n" for x in lines)
        (run / rel).write_bytes(text.encode())
    return sorted(files)


def aspect_files() -> dict[str, list[dict[str, object]]]:
    return {
        "nei/Alchemy.jsonl": [  # aspect ingredient (unresearched)
            line(
                ALCHEMY,
                [item("minecraft:sand"), aspect("vitreus", 10)],
                [item("Thaumcraft:ItemResource")],
            ),
        ],
        "nei/Arcane Infusion.jsonl": [  # aspect inside an anyOf of an ingredient
            line(
                INFUSION,
                [
                    item(
                        "minecraft:diamond",
                        anyOf=[item("minecraft:diamond"), aspect("sensus", 32)],
                    )
                ],
                [item("Thaumcraft:ItemGoggles")],
            ),
        ],
        "nei/Aspect Combination.jsonl": [  # lookup page: aspects in and out
            line(COMBINATION, [aspect("aer", 1, "Aer")], [aspect("aer", 1, "Aer")]),
        ],
        "nei/Items Containing Aspect.jsonl": [  # lookup page: an aspect result
            line(CONTAINING, [item("minecraft:feather")], [aspect("aer", 1, "Aer")]),
            line(  # an aspect in a result's anyOf
                CONTAINING,
                [item("minecraft:feather")],
                [item("minecraft:feather", anyOf=[aspect("aer", 1, "Aer")])],
            ),
        ],
        "nei/Shaped Crafting.jsonl": [  # an ordinary recipe is still read
            line(nei.SHAPED, [item("minecraft:stick", 2)], [item("minecraft:torch")]),
        ],
    }


def test_aspect_lines_are_skipped_and_counted_per_handler(tmp_path: Path) -> None:
    rels = write(tmp_path, aspect_files())
    refs = nei.NeiRefs({}, {}, {})
    read = list(nei.read_nei(tmp_path, rels, refs))
    assert [(rel, r.handler) for rel, r in read] == [
        ("nei/Shaped Crafting.jsonl", nei.SHAPED)
    ]
    assert refs.aspect_lines == {
        ALCHEMY: 1,
        INFUSION: 1,
        COMBINATION: 1,
        CONTAINING: 2,
    }
    # A skipped line names nothing: neither its aspects ("Unknown Aspect") nor its items.
    assert {r.name for r in refs.names} == {"minecraft:stick", "minecraft:torch"}


@pytest.mark.parametrize(
    "stack",
    [
        {"Count": 1, "bloodMagicEssence": "x", "name": "Something"},
        {"Count": 1, "TCAspect": "", "name": "Unknown Aspect"},  # no aspect key to read
        {"Count": 1, "name": "No key at all"},
    ],
)
def test_any_other_stack_shape_still_fails_with_file_and_line(
    tmp_path: Path, stack: dict[str, object]
) -> None:
    rels = write(
        tmp_path,
        {
            "nei/Other.jsonl": [
                line(nei.SHAPED, [item("minecraft:stick")], [item("minecraft:torch")]),
                line("some.Handler", [stack], [item("minecraft:torch")]),
            ]
        },
    )
    with pytest.raises(
        FormatError, match=r"^nei/Other\.jsonl:2: a stack has neither strId"
    ):
        list(nei.read_nei(tmp_path, rels, nei.NeiRefs({}, {}, {})))


def test_an_aspect_needs_a_valid_handler_too(tmp_path: Path) -> None:
    rels = write(tmp_path, {"nei/Bad.jsonl": [line("", [aspect("aer", 1)], [])]})
    with pytest.raises(FormatError, match=r"^nei/Bad\.jsonl:1: handler"):
        list(nei.read_nei(tmp_path, rels, nei.NeiRefs({}, {}, {})))
