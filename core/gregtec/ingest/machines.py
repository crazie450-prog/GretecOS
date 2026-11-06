"""Reader for GregtecLink's machine pass, ``gt_machines.jsonl`` (WO-1.3; the export contract).

One line per GT meta tile entity: ``{mte_id, item, meta, class, mod, meta_name, name, tier?,
multiblock, recipe_map?, recipe_maps?, flags?, tooltip, structure_tooltip?, errors?}``. The key
is still registry name + meta (``gregtech:gt.blockmachines`` and ``meta`` = ``mte_id``), never a
numeric item ID. Tooltip lines stay raw, with their section-sign colour codes; parsing them into
machine facts is WO-1.7's.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from gregtec.ingest.gtdump import (
    FormatError,
    Malformed,
    _int,
    _list,
    _obj,
    _opt_int,
    _opt_str,
    _str,
    item_ref,
    jsonl,
)
from gregtec.ingest.normalize import Ref

MACHINES = "gt_machines.jsonl"
FLAGS = ("batch_mode", "input_separation", "void_protection", "recipe_locking")


@dataclass(frozen=True, slots=True)
class Machine:
    mte_id: int
    ref: Ref
    class_name: str
    mod: str | None
    meta_name: str | None
    name: str | None
    tier: int | None
    multiblock: bool
    recipe_maps: tuple[str, ...]  # in the machine's order
    flags: dict[str, bool]  # multiblocks only
    tooltip: tuple[str, ...]
    structure_tooltip: tuple[str, ...]
    errors: tuple[str, ...]


def read_machines(run: Path) -> Iterator[Machine]:
    for number, d in jsonl(run / MACHINES):
        try:
            ref, _nbt = item_ref(d)
            maps = [
                _str(m, "recipe map")
                for m in _list(d.get("recipe_maps"), "recipe_maps")
            ]
            single = _opt_str(d.get("recipe_map"), "recipe_map")
            if single is not None and single not in maps:
                maps.insert(0, single)
            raw_flags = _obj(d.get("flags") or {}, "flags")
            flags = {
                k: raw_flags[k] for k in FLAGS if isinstance(raw_flags.get(k), bool)
            }
            multiblock = d.get("multiblock")
            if not isinstance(multiblock, bool):
                raise Malformed("multiblock is not true or false")
            yield Machine(
                mte_id=_int(d.get("mte_id"), "mte_id"),
                ref=ref,
                class_name=_str(d.get("class"), "class"),
                mod=_opt_str(d.get("mod"), "mod"),
                meta_name=_opt_str(d.get("meta_name"), "meta_name"),
                name=_opt_str(d.get("name"), "name"),
                tier=_opt_int(d.get("tier"), "tier"),
                multiblock=multiblock,
                recipe_maps=tuple(maps),
                flags=flags,
                tooltip=tuple(str(x) for x in _list(d.get("tooltip"), "tooltip")),
                structure_tooltip=tuple(
                    str(x)
                    for x in _list(d.get("structure_tooltip"), "structure_tooltip")
                ),
                errors=tuple(str(x) for x in _list(d.get("errors"), "errors")),
            )
        except ValueError as exc:
            raise FormatError(f"{MACHINES}:{number}: {exc}") from None
