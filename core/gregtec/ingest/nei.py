"""Reader for NEI's recipe files in an export run's ``nei/`` (WO-1.3; the export contract).

NEI 2.8.172's own format, copied as is: one recipe per line, ``{handler, ingredients, results,
gt?}``. Readers key on each line's ``handler``, never on the file name. A stack is
``{strId, Damage, Count, name, tag?, chance?, anyOf?, returns?}`` or a GT fluid display stack
``{neiFluidName, Count (mB, often a typed "144L"), name}``. NEI's NBT (``tag``) has numeric item
IDs in ItemStack-shaped compounds; they are mapped to registry names through the run's own table
(:func:`gregtec.ingest.normalize.item_ids`), so no key depends on a numeric ID.

Only runs whose manifest says ``parts.nei == "ok"`` have usable NEI files; the caller checks.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec.ingest.gtdump import FormatError, Malformed, jsonl
from gregtec.ingest.normalize import (
    FLUID,
    FULL,
    ITEM,
    Json,
    Recipe,
    Ref,
    Slot,
    input_slot,
    nbt_hash,
    normalize_nbt,
    typed_int,
)

DIR = "nei"
SHAPED = "codechicken.nei.recipe.ShapedRecipeHandler"
SHAPELESS = "codechicken.nei.recipe.ShapelessRecipeHandler"
FURNACE = "codechicken.nei.recipe.FurnaceRecipeHandler"
# recipe.source for NEI's own vanilla handlers (Appendix D's CHECK); every other handler is "nei".
SOURCES = {SHAPED: "crafting", SHAPELESS: "crafting", FURNACE: "furnace"}


@dataclass
class NeiRefs:
    """Names and NBT of the stacks NEI's files reference, for items GT's files don't list."""

    id_names: Mapping[int, str | None]  # the run's numeric ID -> registry name
    names: dict[Ref, str]
    nbt: dict[Ref, Json]


def stack_ref(s: Mapping[str, Any], refs: NeiRefs) -> Ref:
    """The Ref of one NEI stack (an anyOf or returns entry too)."""
    if "neiFluidName" in s:
        name = s["neiFluidName"]
        if not isinstance(name, str) or not name:
            raise Malformed("neiFluidName is not a non-empty string")
        ref = Ref(FLUID, name)
    elif "strId" in s:
        reg = s["strId"]
        if not isinstance(reg, str) or not reg:
            raise Malformed("strId is not a non-empty string")
        tag = s.get("tag")
        nbt = None if tag is None else normalize_nbt(tag, refs.id_names)
        ref = Ref(ITEM, reg, typed_int(s.get("Damage", 0)), nbt_hash(nbt))
        if nbt is not None and ref.nbt_hash:
            refs.nbt.setdefault(ref, nbt)
    else:
        raise Malformed("a stack has neither strId nor neiFluidName")
    name = s.get("name")
    if isinstance(name, str) and name:
        refs.names.setdefault(ref, name)
    return ref


def _stacks(value: object, what: str) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(s, dict) for s in value):
        raise Malformed(f"{what} is not an array of stacks")
    return value


def _chance(s: Mapping[str, Any]) -> int:
    return typed_int(s.get("chance", FULL))


def recipe_from_line(r: Mapping[str, Any], refs: NeiRefs) -> Recipe:
    handler = r.get("handler")
    if not isinstance(handler, str) or not handler:
        raise Malformed("handler is not a non-empty string")
    inputs: list[Slot] = []
    for s in _stacks(r.get("ingredients"), "ingredients"):
        alts = [stack_ref(a, refs) for a in _stacks(s.get("anyOf"), "anyOf")]
        ret = s.get("returns")
        returns = stack_ref(ret, refs) if isinstance(ret, dict) else None
        inputs.append(
            input_slot(
                stack_ref(s, refs),
                typed_int(s.get("Count", 1)),
                _chance(s),
                alts=alts,
                returns=returns,
            )
        )
    outputs: list[Slot] = []
    for s in _stacks(r.get("results"), "results"):
        alts = tuple(
            sorted({stack_ref(a, refs) for a in _stacks(s.get("anyOf"), "anyOf")})
        )
        outputs.append(
            Slot(
                stack_ref(s, refs), typed_int(s.get("Count", 1)), _chance(s), alts=alts
            )
        )
    gt = r.get("gt")
    eut = duration = None
    if gt is not None:
        if not isinstance(gt, dict):
            raise Malformed("gt is not an object")
        eut = typed_int(gt.get("eut"))
        duration = typed_int(gt.get("durationTicks"))
    return Recipe(
        source=SOURCES.get(handler, "nei"),
        space=handler,
        inputs=inputs,
        outputs=outputs,
        handler=handler,
        eut=eut,
        duration=duration,
    ).seal()


def nei_files(manifest: Mapping[str, Any]) -> list[str]:
    """The run's ``nei/*.jsonl`` files, as listed in its manifest, sorted."""
    files = manifest.get("files")
    if not isinstance(files, dict):
        return []
    return sorted(r for r in files if r.startswith(DIR + "/") and r.endswith(".jsonl"))


def read_nei(run: Path, rels: list[str], refs: NeiRefs) -> Iterator[tuple[str, Recipe]]:
    """(file, recipe) for every line of the listed NEI files."""
    for rel in rels:
        for number, r in jsonl(run / rel):
            try:
                yield rel, recipe_from_line(r, refs)
            except ValueError as exc:
                raise FormatError(f"{rel}:{number}: {exc}") from None
