"""Readers for GregtecLink's GT files (WO-1.3): ``gt_recipe_maps.json``, ``gt_recipes.jsonl``,
``items.jsonl`` and ``fluids.jsonl`` of one export run (the export contract), and the full ore
dictionary ``oredict.jsonl`` (D145).

Each reader turns the mod's raw facts into :mod:`gregtec.ingest.normalize` records. A line
that isn't the contract's shape raises :class:`FormatError` naming the file and line, because
the collected files were checked against their manifest and so can't be cut short: a bad line
means the export itself is wrong. Lines the mod marks as failed (``error``/``errors``) are data,
counted by the caller.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.ingest.normalize import (
    ALT_IDENTITY_MAPS,
    FLUID,
    FULL,
    ITEM,
    Json,
    Recipe,
    Ref,
    Slot,
    input_slot,
    item_ids,
    nbt_hash,
    normalize_nbt,
)

RECIPE_MAPS = "gt_recipe_maps.json"
RECIPES = "gt_recipes.jsonl"
ITEMS = "items.jsonl"
FLUIDS = "fluids.jsonl"
# An item with no registry name is keyed by the stand-in name "?<class>".
UNREGISTERED = "?"
FLAGS = (
    "enabled",
    "hidden",
    "fake",
    "needs_empty_output",
    "nbt_sensitive",
    "can_be_buffered",
)


class FormatError(ValueError):
    """A file or line of an export run isn't in the contract's shape."""


class Malformed(ValueError):
    """One field isn't in the contract's shape; the reader adds the file and line."""


def _int(value: object, what: str) -> int:
    if type(value) is not int:
        raise Malformed(f"{what} is not an integer: {value!r}")
    return value


def _opt_int(value: object, what: str) -> int | None:
    return None if value is None else _int(value, what)


def _opt_str(value: object, what: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise Malformed(f"{what} is not a string: {value!r}")
    return value


def _str(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise Malformed(f"{what} is not a non-empty string: {value!r}")
    return value


def _obj(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Malformed(f"{what} is not an object")
    return value


def _list(value: object, what: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise Malformed(f"{what} is not an array")
    return value


def jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """(line number, object) for each line of ``path``; a line that isn't an object raises."""
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                raise FormatError(f"{path.name}:{number}: not JSON ({exc})") from None
            if not isinstance(obj, dict):
                raise FormatError(f"{path.name}:{number}: not a JSON object")
            yield number, obj


@dataclass(frozen=True, slots=True)
class RecipeMap:
    name: str
    amperage: int
    backend: str | None
    frontend: str | None
    max_item_in: int | None
    max_item_out: int | None
    max_fluid_in: int | None
    max_fluid_out: int | None
    nei_register: bool | None
    owner_mod: str | None
    categories: tuple[tuple[str, str | None], ...]  # (category, owner mod)
    count: int | None
    errors: tuple[str, ...]


def read_recipe_maps(run: Path) -> list[RecipeMap]:
    path = run / RECIPE_MAPS
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError as exc:
            raise FormatError(f"{RECIPE_MAPS}: not JSON ({exc})") from None
    if not isinstance(data, list):
        raise FormatError(f"{RECIPE_MAPS}: not a JSON array")
    maps: list[RecipeMap] = []
    for i, e in enumerate(data):
        try:
            e = _obj(e, "entry")
            ui = _obj(e.get("ui") or {}, "ui")
            cats = tuple(
                (
                    _str(c.get("name"), "category name"),
                    _opt_str(c.get("owner_mod"), "owner_mod"),
                )
                for c in (
                    _obj(c, "category")
                    for c in _list(e.get("categories"), "categories")
                )
            )
            nei = e.get("nei_register")
            maps.append(
                RecipeMap(
                    name=_str(e.get("name"), "name"),
                    amperage=_opt_int(e.get("amperage"), "amperage") or 1,
                    backend=_opt_str(e.get("backend"), "backend"),
                    frontend=_opt_str(e.get("frontend"), "frontend"),
                    max_item_in=_opt_int(ui.get("max_item_in"), "max_item_in"),
                    max_item_out=_opt_int(ui.get("max_item_out"), "max_item_out"),
                    max_fluid_in=_opt_int(ui.get("max_fluid_in"), "max_fluid_in"),
                    max_fluid_out=_opt_int(ui.get("max_fluid_out"), "max_fluid_out"),
                    nei_register=nei if isinstance(nei, bool) else None,
                    owner_mod=_opt_str(e.get("owner_mod"), "owner_mod"),
                    categories=cats,
                    count=_opt_int(e.get("count"), "count"),
                    errors=tuple(str(x) for x in _list(e.get("errors"), "errors")),
                )
            )
        except ValueError as exc:
            raise FormatError(f"{RECIPE_MAPS}: entry {i}: {exc}") from None
    names = [m.name for m in maps]
    if len(set(names)) != len(names):
        raise FormatError(f"{RECIPE_MAPS}: a map name appears twice")
    return maps


def item_ref(
    d: Mapping[str, Any], ids: dict[int, str | None] | None = None
) -> tuple[Ref, Json]:
    """An item ref ``{item, meta, n?, nbt?}`` as (Ref, normalised NBT or None). With ``ids``,
    the numeric IDs of GregtecLink's ``__item`` compounds in its NBT are collected there first
    (the run's ID table for NEI's NBT)."""
    reg = d.get("item")
    if reg is None:  # contract: an item with no registry name carries its class
        reg = UNREGISTERED + str(d.get("class"))
    nbt = d.get("nbt")
    if nbt is not None and ids is not None:
        item_ids(nbt, ids)
    norm = None if nbt is None else normalize_nbt(nbt)
    return Ref(
        ITEM, _str(reg, "item"), _int(d.get("meta"), "meta"), nbt_hash(norm)
    ), norm


def fluid_ref(d: Mapping[str, Any]) -> Ref:
    name = d.get("fluid")
    return Ref(FLUID, _str(name if name is not None else "?unregistered", "fluid"))


def _chance(chances: list[Any], i: int) -> int:
    if i < len(chances) and chances[i] is not None:
        return _int(chances[i], "chance")
    return FULL


@dataclass
class GtRefs:
    """What the recipes reference beyond their own rows: for the item and ore tables."""

    nbt: dict[Ref, Json] = field(default_factory=dict)  # item Ref -> its normalised NBT
    ores: set[str] = field(default_factory=set)
    ids: dict[int, str | None] = field(
        default_factory=dict
    )  # numeric ID -> registry name
    fluid_nbt: int = 0  # fluid stacks whose NBT was dropped (fluids are keyed by name)


def _alts(slot: Mapping[str, Any], refs: GtRefs, fluid: bool) -> list[Ref]:
    out = []
    for a in _list(slot.get("alt"), "alt"):
        a = _obj(a, "alt")
        if fluid:
            out.append(fluid_ref(a))
        else:
            ref, nbt = item_ref(a, refs.ids)
            if nbt is not None:
                refs.nbt.setdefault(ref, nbt)
            out.append(ref)
    return out


def recipe_from_line(
    r: Mapping[str, Any], maps: Mapping[str, RecipeMap], refs: GtRefs
) -> Recipe:
    """One ``gt_recipes.jsonl`` object (not an error line) as a sealed :class:`Recipe`."""
    name = _str(r.get("map"), "map")
    if name not in maps:
        raise Malformed(f"map {name!r} is not in {RECIPE_MAPS}")
    inputs: list[Slot] = []
    outputs: list[Slot] = []
    in_ch = _list(r.get("in_chance"), "in_chance")
    for i, s in enumerate(_list(r.get("in"), "in")):
        if s is None:
            continue
        s = _obj(s, "in")
        ref, nbt = item_ref(s, refs.ids)
        if nbt is not None:
            refs.nbt.setdefault(ref, nbt)
        ore = _opt_str(s.get("ore"), "ore")
        if ore is not None:
            refs.ores.add(ore)
        alts = _alts(s, refs, fluid=False)
        inputs.append(
            input_slot(
                ref, _int(s.get("n"), "n"), _chance(in_ch, i), ore=ore, alts=alts
            )
        )
    fin_ch = _list(r.get("fin_chance"), "fin_chance")
    for i, s in enumerate(_list(r.get("fin"), "fin")):
        if s is None:
            continue
        s = _obj(s, "fin")
        refs.fluid_nbt += "nbt" in s
        alts = _alts(s, refs, fluid=True)
        inputs.append(
            input_slot(
                fluid_ref(s), _int(s.get("mb"), "mb"), _chance(fin_ch, i), alts=alts
            )
        )
    out_ch = _list(r.get("out_chance"), "out_chance")
    for i, s in enumerate(_list(r.get("out"), "out")):
        if s is None:
            continue
        ref, nbt = item_ref(_obj(s, "out"), refs.ids)
        if nbt is not None:
            refs.nbt.setdefault(ref, nbt)
        outputs.append(Slot(ref, _int(s.get("n"), "n"), _chance(out_ch, i)))
    fout_ch = _list(r.get("fout_chance"), "fout_chance")
    for i, s in enumerate(_list(r.get("fout"), "fout")):
        if s is None:
            continue
        s = _obj(s, "fout")
        refs.fluid_nbt += "nbt" in s
        outputs.append(Slot(fluid_ref(s), _int(s.get("mb"), "mb"), _chance(fout_ch, i)))
    flags = {}
    for flag in FLAGS:
        value = r.get(flag)
        if not isinstance(value, bool):
            raise Malformed(f"{flag} is not true or false")
        flags[flag] = value
    # Values may hold item refs with NBT (an ItemStack in metadata, special items): no numeric
    # IDs there either.
    raw_meta = dict(_obj(r.get("metadata") or {}, "metadata"))
    for extra in ("special_items", "nei_desc"):
        if extra in r:
            if extra in raw_meta:
                raise Malformed(
                    f"metadata has a key {extra!r}, which the loader also uses"
                )
            raw_meta[extra] = r[extra]
    item_ids(raw_meta, refs.ids)
    meta: dict[str, Any] = {k: normalize_nbt(v) for k, v in raw_meta.items()}
    owners = [_str(o, "owner") for o in _list(r.get("owners"), "owners")]
    return Recipe(
        source="gt",
        space=name,
        inputs=inputs,
        outputs=outputs,
        category=_opt_str(r.get("category"), "category"),
        eut=_int(r.get("eut"), "eut"),
        duration=_int(r.get("duration"), "duration"),
        amperage=maps[name].amperage,
        special_value=_opt_int(r.get("special"), "special"),
        flags=flags,
        meta=meta,
        owners=owners,
        alt_identity=name in ALT_IDENTITY_MAPS,
    ).seal()


@dataclass(frozen=True, slots=True)
class RecipeError:
    """A ``{"map", "idx", "error"}`` line: a recipe the mod couldn't serialise."""

    map: str
    idx: int | None
    error: str


def read_recipes(
    run: Path, maps: Mapping[str, RecipeMap], refs: GtRefs
) -> Iterator[Recipe | RecipeError]:
    for number, r in jsonl(run / RECIPES):
        try:
            if "error" in r:
                yield RecipeError(str(r.get("map")), r.get("idx"), str(r["error"]))
                continue
            yield recipe_from_line(r, maps, refs)
        except ValueError as exc:
            raise FormatError(f"{RECIPES}:{number}: {exc}") from None


@dataclass(frozen=True, slots=True)
class Item:
    ref: Ref
    nbt: Json  # normalised NBT, or None
    unlocalized: str | None
    name: str | None
    ore: tuple[str, ...]
    gt_prefix: str | None
    gt_material: str | None
    gt_amount: int | None
    errors: tuple[str, ...]


def read_items(run: Path, ids: dict[int, str | None] | None = None) -> Iterator[Item]:
    for number, d in jsonl(run / ITEMS):
        try:
            ref, nbt = item_ref(d, ids)
            gt = _obj(d.get("gt") or {}, "gt")
            yield Item(
                ref=ref,
                nbt=nbt,
                unlocalized=_opt_str(d.get("unlocalized"), "unlocalized"),
                name=_opt_str(d.get("name"), "name"),
                ore=tuple(_str(o, "ore") for o in _list(d.get("ore"), "ore")),
                gt_prefix=_opt_str(gt.get("prefix"), "gt prefix"),
                gt_material=_opt_str(gt.get("material"), "gt material"),
                gt_amount=_opt_int(gt.get("amount"), "gt amount"),
                errors=tuple(str(x) for x in _list(d.get("errors"), "errors")),
            )
        except ValueError as exc:
            raise FormatError(f"{ITEMS}:{number}: {exc}") from None


@dataclass(frozen=True, slots=True)
class Fluid:
    name: str
    unlocalized: str | None
    name_en: str | None
    temperature: int | None
    density: int | None
    viscosity: int | None
    gaseous: bool | None
    error: str | None


def read_fluids(run: Path) -> Iterator[Fluid]:
    for number, d in jsonl(run / FLUIDS):
        try:
            gaseous = d.get("gaseous")
            yield Fluid(
                name=_str(d.get("fluid"), "fluid"),
                unlocalized=_opt_str(d.get("unlocalized"), "unlocalized"),
                name_en=_opt_str(d.get("name"), "name"),
                temperature=_opt_int(d.get("temperature"), "temperature"),
                density=_opt_int(d.get("density"), "density"),
                viscosity=_opt_int(d.get("viscosity"), "viscosity"),
                gaseous=gaseous if isinstance(gaseous, bool) else None,
                error=None if d.get("error") is None else str(d["error"]),
            )
        except ValueError as exc:
            raise FormatError(f"{FLUIDS}:{number}: {exc}") from None


ORE_DICT = "oredict.jsonl"


@dataclass(frozen=True, slots=True)
class OreName:
    """One oredict name and its members (D145): wildcard meta 32767 kept raw."""

    name: str
    members: tuple[Ref, ...]
    error: str | None  # the mod couldn't read the name's members


def oredict_status(manifest: Mapping[str, Any]) -> str:
    """``parts.oredict``; ``absent`` for a run made before D145."""
    parts = manifest.get("parts")
    value = parts.get("oredict") if isinstance(parts, dict) else None
    return value if isinstance(value, str) else "absent"


def read_oredict(
    run: Path, ids: dict[int, str | None] | None = None
) -> Iterator[OreName]:
    """``oredict.jsonl``: one :class:`OreName` per line, members keyed as every item ref is."""
    for number, d in jsonl(run / ORE_DICT):
        try:
            members = tuple(
                item_ref(_obj(m, "member"), ids)[0]
                for m in _list(d.get("members"), "members")
            )
            error = d.get("error")
            yield OreName(
                _str(d.get("ore"), "ore"),
                members,
                None if error is None else str(error),
            )
        except ValueError as exc:
            raise FormatError(f"{ORE_DICT}:{number}: {exc}") from None
