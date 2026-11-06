"""CropsNH's crop data from an export run (WO-1.10, plan section 7.17, D134).

GregtecLink's client export runs CropsNH 2.0.140's own NEI data dumpers right after NEI's recipe
dump and copies their files, byte for byte, into the run's ``crops/`` (``parts.crops``; the
export contract). CropsNH (LGPL-3.0) is not vendored: the formats below were read from its
source (``CropRegistry.dumpCrops``, ``dumpAlternateSeeds``, ``MutationRegistry.
dumpDeterministicMutations``, ``dumpMutationPools``, ``SoilRegistry.dump``, ``DebugHelper``) and
re-implemented. The dumpers write with the JVM's default charset (``manifest.java.charset``)
and the platform's line separator.

- ``cropRegistry.csv``: a header, then one crop per line, its CropsNH id first
  (``cropsnh:allium``); the id is the citation key ``crop:<id>``. Drops are ``N x <display
  name>[<NBT>] @ <chance>%`` joined by `` | ``, where the chance is CropsNH's basis points
  written as ``value / 100`` ``.`` ``value % 100`` (not zero-padded: ``12.5%`` is 1205, ``12.50%``
  1250). Growth requirements are their English descriptions joined by `` | ``.
- ``deterministicMutations.csv``: the parent-combination recipes, one per line: the child's and
  the parents' display names (up to four parents), then one column per breeding condition.
- ``mutationPools.csv``: a header of pool names, then one line per crop in any pool, TRUE or
  FALSE per pool. Pools and crops are named by display name only.
- ``soilRegistry.txt``: each soil list's name, then its blocks (``- <registry name>[:<meta>]``,
  no meta for any meta), blank lines between lists.
- ``alternateSeed.csv``: items that plant a crop (registry name, meta or ``*``, crop id).

Display names are mapped to crop ids through the crop dump's own Name column, which CropsNH
renders the same way. Items are keyed by registry name + meta (+ NBT hash), never a numeric ID:
the dumps give a drop only by display name, so when NEI's step is ``ok`` a drop is keyed from
NEI's crop page of the same crop (``cropsnhCrops``, whose stacks carry registry names but no
chances), matched by amount and display name. NEI's CropsNH tabs also give each crop's sub-soil
blocks, which crops NEI shows, and which recipes its Crop Breeding tab shows (V33).

Requirement texts are classified by CropsNH's English descriptions (its ``en_US.lang``); a text
that matches none (another language, a new kind) is kept with kind ``other``. Nothing here reads
a game file: the run is a collected copy in GOS_HOME.
"""

from __future__ import annotations

import codecs
import csv
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from gregtec.ingest.nei import NeiRefs
from gregtec.ingest.normalize import ITEM, Recipe, Ref, canonical

DIR = "crops"
CROPS = "cropRegistry.csv"
RECIPES = "deterministicMutations.csv"
POOLS = "mutationPools.csv"
SOILS = "soilRegistry.txt"
SEEDS = "alternateSeed.csv"
FILES = (CROPS, RECIPES, POOLS, SOILS, SEEDS)
# CropsNH's seed item; the species is its NBT's "crop" (Names.NBT.crop), the stats gr/ga/re.
GENERIC_SEED = "cropsnh:genericSeed"
# NEI handler ids of CropsNH's crop-stick tabs (left out of the recipe tables, D136).
NEI_CROPS = "cropsnhCrops"
NEI_BREEDING = "cropsnhBreeding"
NEI_POOLS = "mutationPool"
NEI_SEEDS = "cropsnhAlternateSeeds"
NEI_HANDLERS = frozenset({NEI_CROPS, NEI_BREEDING, NEI_POOLS, NEI_SEEDS})
MAX_PARENTS = 4  # the dump's parent columns
_COLUMNS = {
    "id": "ID",
    "name": "Name",
    "unlocalized": "Unlocalised Name",
    "flavour": "Flavour Text",
    "creator": "Creator",
    "soil": "Soil Type",
    "tier": "Tier",
    "min_seed_bed_tier": "Min Seed Bed Tier",
    "liked_biomes": "Liked Biomes",
    "growth_duration": "Growth Duration",
    "breeding_threshold": "Breeding Threshold",
    "crossing_threshold": "Crossing Threshold",
    "weeds_above": "Starts Spreading Weeds If Growth Above",
    "requirements": "Growth Requirements",
    "drop_multiplier": "Drop Count Multiplier",
    "drops": "Drop Table",
    "alternate_seeds": "Alternate Seeds",
}
_LIST_SEP = " | "
_DROP = re.compile(r"([0-9]+) x (.*) @ ([0-9]+)\.([0-9]{1,2})%", re.DOTALL)
_CATALYST = re.compile(r"MachineCatalyst\((.*)\)", re.DOTALL)
_CATALYST_ITEM = re.compile(r"(.+) x ([0-9]+)", re.DOTALL)
_FORMATTING = re.compile("§.", re.DOTALL)
# CropsNH 2.0.140's English requirement descriptions -> (kind, has a number).
_KINDS = (
    (re.compile(r"Needs a light level of ([0-9]+) or higher"), "light_min"),
    (re.compile(r"Needs a light level of ([0-9]+) or lower"), "light_max"),
    (re.compile(r"Needs an? .+ under it to grow", re.DOTALL), "sub_soil"),
    (re.compile(r"Can only be harvested in the Industrial Farm"), "industrial_farm"),
    (re.compile(r"Requires the crop breeder"), "machine_only"),
    (re.compile(r"Must use crop sticks"), "world_only"),
    (re.compile(r"Invalid soil! Break to get seed!"), "migrated"),
)
KINDS = tuple(k for _, k in _KINDS) + ("catalyst", "other")


class FormatError(ValueError):
    """A crop file isn't in the shape CropsNH 2.0.140 writes."""


@dataclass(frozen=True, slots=True)
class Requirement:
    """A growth requirement or breeding condition. ``value`` is the light level for
    ``light_min``/``light_max`` and the amount for ``catalyst`` (whose ``text`` is the item's
    display name or ``ore:<oredict name>``); ``text`` is otherwise CropsNH's description."""

    kind: str
    value: int | None
    text: str


@dataclass(frozen=True, slots=True)
class Drop:
    amount: int
    chance_bp: int  # basis points, 10000 = 100%
    name: str  # display name, formatting codes removed
    nbt: (
        str | None
    )  # the stack's NBT as CropsNH writes it (Minecraft's own text), if any
    item: Ref | None = None  # registry name + meta + NBT hash, from NEI's crop page


@dataclass(frozen=True, slots=True)
class Crop:
    id: str  # CropsNH's id, cited crop:<id>
    name: str
    unlocalized: str
    flavour: str
    creator: str
    soil: str  # CropsNH's soil id: a list name, "<name>(a+b)" for a union, "global" for any
    soil_lists: tuple[str, ...]  # the soil lists it grows on
    tier: int
    min_seed_bed_tier: int  # -1: none
    liked_biomes: tuple[str, ...]  # Forge BiomeDictionary types
    growth_duration: int
    breeding_threshold: float
    crossing_threshold: float
    weeds_above: int  # it spreads weeds when its Growth is above this
    drop_multiplier: float
    requirements: tuple[Requirement, ...]
    drops: tuple[Drop, ...]  # in the dump's order (ascending chance)
    alternate_seeds: tuple[str, ...]  # display names; the keyed list is CropData.seeds
    sub_soils: tuple[Ref, ...] = ()  # blocks (as items) under the soil, from NEI's page
    in_nei: bool | None = None  # on NEI's crop pages; None without NEI's data

    @property
    def ref(self) -> str:
        return f"crop:{self.id}"


@dataclass(frozen=True, slots=True)
class ParentRecipe:
    """A parent-combination ("deterministic mutation") recipe: these parents give ``child``."""

    child: str
    parents: tuple[str, ...]  # crop ids, in the dump's order
    requirements: tuple[Requirement, ...]
    in_nei: bool | None = None  # in NEI's Crop Breeding tab; None without NEI's data

    @property
    def key(self) -> str:
        return f"{self.child}<{'+'.join(sorted(self.parents))}"

    @property
    def machine_only(self) -> bool:
        return any(r.kind == "machine_only" for r in self.requirements)

    @property
    def world_only(self) -> bool:
        return any(r.kind == "world_only" for r in self.requirements)


@dataclass(frozen=True, slots=True)
class Pool:
    name: str  # display name (the dump has no other)
    members: tuple[str, ...]  # crop ids, sorted


@dataclass(frozen=True, slots=True)
class SoilBlock:
    registry_name: str  # a block's registry name
    meta: int | None  # None: any meta


@dataclass(frozen=True, slots=True)
class Soil:
    name: str
    blocks: tuple[
        SoilBlock, ...
    ]  # empty: registered, but no block (its mod isn't loaded)


@dataclass(frozen=True, slots=True)
class Seed:
    """An item that plants a crop at 1/1/1 (CropsNH's alternate seeds)."""

    registry_name: str
    meta: int | None  # None: any meta
    crop: str


@dataclass
class NeiCheck:
    """What NEI's CropsNH tabs show, for the coverage check (V33)."""

    crop_pages: int = 0
    breeding: int = 0  # recipes in the Crop Breeding tab
    breeding_crops: set[str] = field(default_factory=set)  # crops in that tab
    pools: int = 0
    pools_matched: int = 0
    drops_keyed: int = 0
    drops: int = 0  # drops of crops with an NEI page


@dataclass
class CropData:
    crops: dict[str, Crop]  # by id, sorted
    recipes: list[ParentRecipe]  # sorted by key
    pools: list[Pool]  # sorted by name
    soils: list[Soil]  # sorted by name
    seeds: list[Seed]  # sorted
    problems: list[str] = field(default_factory=list, compare=False)
    nei: NeiCheck | None = field(default=None, compare=False)  # None without NEI's data

    def has_nei(self) -> bool:
        return any(c.in_nei is not None for c in self.crops.values())


def status(manifest: Mapping[str, Any]) -> str:
    """``parts.crops``; ``absent`` for a run made before WO-1.10."""
    parts = manifest.get("parts")
    value = parts.get("crops") if isinstance(parts, dict) else None
    return value if isinstance(value, str) else "absent"


def charset(manifest: Mapping[str, Any]) -> str:
    java = manifest.get("java")
    name = java.get("charset") if isinstance(java, dict) else None
    if name is None:
        return "UTF-8"
    try:
        codecs.lookup(str(name))
    except LookupError:
        raise FormatError(
            f"manifest java.charset {name!r} is not a known charset"
        ) from None
    return str(name)


def _rows(path: Path, encoding: str) -> list[list[str]]:
    try:
        with open(path, encoding=encoding, newline="") as f:
            return list(csv.reader(f, strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise FormatError(f"{DIR}/{path.name}: {exc}") from None


def _int(text: str, where: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise FormatError(f"{where}: {text!r} is not a whole number") from None


def _float(text: str, where: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise FormatError(f"{where}: {text!r} is not a number") from None


def clean(name: str) -> str:
    """A display name without Minecraft's formatting codes (NEI drops them too)."""
    return _FORMATTING.sub("", name).strip()


def requirement(text: str) -> list[Requirement]:
    """One requirement or condition column; a ``MachineCatalyst(...)`` gives one per item."""
    m = _CATALYST.fullmatch(text)
    if m:
        out = []
        for part in m.group(1).split(_LIST_SEP):
            item = _CATALYST_ITEM.fullmatch(part)
            if item is None:
                out.append(Requirement("other", None, text))
                break
            out.append(Requirement("catalyst", int(item.group(2)), item.group(1)))
        return out
    for pattern, kind in _KINDS:
        found = pattern.fullmatch(text)
        if found:
            value = int(found.group(1)) if found.groups() else None
            return [Requirement(kind, value, text)]
    return [Requirement("other", None, text)]


def _requirements(cells: Iterable[str]) -> tuple[Requirement, ...]:
    """Sorted: CropsNH keeps some in hash sets, and their order means nothing."""
    found = {r for c in cells if c for r in requirement(c)}
    return tuple(sorted(found, key=lambda r: (r.kind, r.text, r.value or 0)))


def _drops(text: str, where: str) -> tuple[Drop, ...]:
    out = []
    for part in text.split(_LIST_SEP) if text else ():
        m = _DROP.fullmatch(part)
        if m is None:
            raise FormatError(f"{where}: drop {part!r} is not 'N x name @ chance%'")
        whole, frac = int(m.group(3)), int(m.group(4))
        name, nbt = m.group(2), None
        brace = name.find("{")
        if brace > 0 and name.endswith("}"):
            name, nbt = name[:brace], name[brace:]
        out.append(Drop(int(m.group(1)), whole * 100 + frac, clean(name), nbt))
    # CropsNH sorts by chance; drops of one chance come in hash-map order, so sort them fully.
    return tuple(
        sorted(out, key=lambda d: (d.chance_bp, d.name, d.amount, d.nbt or ""))
    )


def soil_lists(soil: str) -> tuple[str, ...]:
    """The soil lists of a crop's soil id: ``mushroom(stone+dirtGrass+mycelium)`` is the union of
    three lists; ``global`` is CropsNH's list of every registered soil."""
    m = re.fullmatch(r"[^(]*\((.*)\)", soil)
    if m is None:
        return (soil,)
    return tuple(p for p in m.group(1).split("+") if p)


def parse_crops(rows: list[list[str]]) -> dict[str, Crop]:
    if not rows:
        raise FormatError(f"{DIR}/{CROPS} is empty")
    header = rows[0]
    col = {}
    for key, title in _COLUMNS.items():
        if title not in header:
            raise FormatError(f"{DIR}/{CROPS}: no column {title!r}")
        col[key] = header.index(title)
    body = rows[1:]
    if body == [["empty"]]:  # CropsNH registered no crop
        return {}
    crops: dict[str, Crop] = {}
    for n, row in enumerate(body, 2):
        if len(row) != len(header):
            raise FormatError(
                f"{DIR}/{CROPS} line {n}: {len(row)} fields, the header has {len(header)}"
            )
        v = {k: row[i] for k, i in col.items()}
        cid = v["id"]
        if not cid or cid in crops:
            raise FormatError(
                f"{DIR}/{CROPS} line {n}: crop id {cid!r} empty or repeated"
            )
        where = f"{DIR}/{CROPS} {cid}"
        crops[cid] = Crop(
            id=cid,
            name=clean(v["name"]),
            unlocalized=v["unlocalized"],
            flavour=v["flavour"],
            creator=v["creator"],
            soil=v["soil"],
            soil_lists=soil_lists(v["soil"]),
            tier=_int(v["tier"], f"{where} Tier"),
            min_seed_bed_tier=_int(
                v["min_seed_bed_tier"], f"{where} Min Seed Bed Tier"
            ),
            # A hash set of enums in CropsNH, written in a different order on each run.
            liked_biomes=tuple(
                sorted({b.strip() for b in v["liked_biomes"].split(",") if b.strip()})
            ),
            growth_duration=_int(v["growth_duration"], f"{where} Growth Duration"),
            breeding_threshold=_float(
                v["breeding_threshold"], f"{where} Breeding Threshold"
            ),
            crossing_threshold=_float(
                v["crossing_threshold"], f"{where} Crossing Threshold"
            ),
            weeds_above=_int(v["weeds_above"], f"{where} weeds"),
            drop_multiplier=_float(
                v["drop_multiplier"], f"{where} Drop Count Multiplier"
            ),
            requirements=_requirements(v["requirements"].split(_LIST_SEP)),
            drops=_drops(v["drops"], where),
            alternate_seeds=tuple(
                sorted({clean(s) for s in v["alternate_seeds"].split(_LIST_SEP) if s})
            ),
        )
    return dict(sorted(crops.items()))


def by_name(crops: Mapping[str, Crop]) -> tuple[dict[str, str], set[str]]:
    """{display name: crop id} for the names one crop has, and the names several crops share."""
    seen: dict[str, list[str]] = {}
    for c in crops.values():
        seen.setdefault(c.name, []).append(c.id)
    return {n: ids[0] for n, ids in seen.items() if len(ids) == 1}, {
        n for n, ids in seen.items() if len(ids) > 1
    }


def _crop_id(name: str, names: Mapping[str, str], shared: set[str], where: str) -> str:
    if name in shared:
        raise LookupError(f"{where}: several crops are named {name!r}")
    try:
        return names[name]
    except KeyError:
        raise LookupError(f"{where}: no crop is named {name!r}") from None


def parse_recipes(
    rows: list[list[str]], crops: Mapping[str, Crop], problems: list[str]
) -> list[ParentRecipe]:
    if not rows or rows[0][:2] != ["output", "parent1"]:
        raise FormatError(f"{DIR}/{RECIPES}: no 'output,parent1,...' header")
    names, shared = by_name(crops)
    out: dict[str, ParentRecipe] = {}
    for n, row in enumerate(rows[1:], 2):
        if not row or row == [""]:
            continue
        where = f"{DIR}/{RECIPES} line {n}"
        if len(row) < 1 + MAX_PARENTS:
            raise FormatError(
                f"{where}: {len(row)} fields, want at least {1 + MAX_PARENTS}"
            )
        try:
            child = _crop_id(clean(row[0]), names, shared, where)
            parents = tuple(
                _crop_id(clean(p), names, shared, where)
                for p in row[1 : 1 + MAX_PARENTS]
                if p
            )
        except LookupError as exc:
            problems.append(f"recipe left out: {exc}")
            continue
        if not parents:
            raise FormatError(f"{where}: no parent")
        r = ParentRecipe(child, parents, _requirements(row[1 + MAX_PARENTS :]))
        if r.key in out:
            problems.append(f"{where}: recipe {r.key} listed twice; the first is kept")
            continue
        out[r.key] = r
    return [out[k] for k in sorted(out)]


def parse_pools(
    rows: list[list[str]], crops: Mapping[str, Crop], problems: list[str]
) -> list[Pool]:
    if not rows or rows[0] == ["Empty"]:
        return []
    header = rows[0]
    if header[0] != "Crop":
        raise FormatError(f"{DIR}/{POOLS}: no 'Crop,...' header")
    pools = [clean(p) for p in header[1:]]
    if len(set(pools)) != len(pools):
        raise FormatError(f"{DIR}/{POOLS}: a pool name is repeated")
    names, shared = by_name(crops)
    members: dict[str, set[str]] = {p: set() for p in pools}
    for n, row in enumerate(rows[1:], 2):
        if row in ([], [""], ["Empty"], ["Mutation pools are all empty"]):
            continue
        where = f"{DIR}/{POOLS} line {n}"
        if len(row) != len(header):
            raise FormatError(
                f"{where}: {len(row)} fields, the header has {len(header)}"
            )
        try:
            cid = _crop_id(clean(row[0]), names, shared, where)
        except LookupError as exc:
            problems.append(f"pool membership left out: {exc}")
            continue
        for pool, cell in zip(pools, row[1:], strict=True):
            if cell not in ("TRUE", "FALSE"):
                raise FormatError(f"{where}: {cell!r} is not TRUE or FALSE")
            if cell == "TRUE":
                members[pool].add(cid)
    return [Pool(p, tuple(sorted(m))) for p, m in sorted(members.items()) if m]


def _block(text: str, where: str) -> SoilBlock:
    """``modid:name`` or ``modid:name:meta`` (GameRegistry's unique identifier, then the meta)."""
    head, sep, tail = text.rpartition(":")
    if sep and head.count(":") >= 1 and tail.isdigit():
        return SoilBlock(head, int(tail))
    if ":" not in text:
        raise FormatError(f"{where}: {text!r} is not a registry name")
    return SoilBlock(text, None)


def parse_soils(text: str) -> list[Soil]:
    soils: dict[str, list[SoilBlock]] = {}
    current: str | None = None
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            current = None
            continue
        if line.startswith("#"):  # "# empty", "# compound list", "# inner list <n>"
            continue
        if line.startswith("- "):
            if current is None:
                raise FormatError(
                    f"{DIR}/{SOILS} line {n}: a block outside a soil list"
                )
            soils[current].append(_block(line[2:].strip(), f"{DIR}/{SOILS} line {n}"))
            continue
        if line in soils:
            raise FormatError(f"{DIR}/{SOILS} line {n}: soil list {line!r} repeated")
        current = line
        soils[current] = []
    # A list is a set of blocks, which CropsNH iterates in hash order: sort it.
    return [
        Soil(name, tuple(sorted(set(blocks), key=_block_order)))
        for name, blocks in sorted(soils.items())
    ]


def _block_order(b: SoilBlock) -> tuple[str, int]:
    return (b.registry_name, -1 if b.meta is None else b.meta)


def parse_seeds(
    rows: list[list[str]], crops: Mapping[str, Crop], problems: list[str]
) -> list[Seed]:
    if not rows or rows[0] != ["Item", "Meta", "CropCard"]:
        raise FormatError(f"{DIR}/{SEEDS}: no 'Item,Meta,CropCard' header")
    out = set()
    for n, row in enumerate(rows[1:], 2):
        if row in ([], [""], ["empty"]):
            continue
        where = f"{DIR}/{SEEDS} line {n}"
        if len(row) != 3:
            raise FormatError(f"{where}: {len(row)} fields, want 3")
        reg, meta, crop = row
        if crop not in crops:
            problems.append(
                f"{where}: alternate seed for an unknown crop {crop!r}, left out"
            )
            continue
        out.add(Seed(reg, None if meta == "*" else _int(meta, where), crop))
    return sorted(
        out, key=lambda s: (s.registry_name, -1 if s.meta is None else s.meta, s.crop)
    )


def _species(ref: Ref, refs: NeiRefs) -> str | None:
    """The crop id of a CropsNH seed stack (its NBT's ``crop``), else None."""
    tag = refs.nbt.get(ref)
    if ref.name != GENERIC_SEED or not isinstance(tag, dict):
        return None
    crop = tag.get("crop")
    return crop if isinstance(crop, str) else None


def apply_nei(data: CropData, lines: Iterable[Recipe], refs: NeiRefs) -> None:
    """Key drops and sub-soils from NEI's crop pages and check what NEI's CropsNH tabs show
    against the dumps (V33). Changes ``data`` in place."""
    check = NeiCheck()
    data.nei = check
    pages: dict[str, Recipe] = {}
    breeding: set[str] = set()
    for r in lines:
        if r.handler == NEI_CROPS and r.inputs:
            crop = _species(r.inputs[0].ref, refs)
            if crop is None:
                data.problems.append("an NEI crop page shows no CropsNH seed")
                continue
            check.crop_pages += 1
            pages[crop] = r
        elif r.handler == NEI_BREEDING:
            check.breeding += 1
            ids = [_species(s.ref, refs) for s in (*r.inputs, *r.outputs)]
            if None in ids or len(r.outputs) != 1:
                data.problems.append(
                    "an NEI Crop Breeding recipe isn't seeds to one seed"
                )
                continue
            crops = [i for i in ids if i is not None]
            check.breeding_crops.update(crops)
            child, parents = crops[-1], crops[:-1]
            breeding.add(f"{child}<{'+'.join(sorted(parents))}")
        elif r.handler == NEI_POOLS:
            check.pools += 1
            members = tuple(sorted({_species(s.ref, refs) or "?" for s in r.inputs}))
            if any(p.members == members for p in data.pools):
                check.pools_matched += 1
            else:
                data.problems.append(
                    f"an NEI mutation pool page ({len(members)} crops) matches no dumped pool"
                )
    shown = set(pages) | check.breeding_crops
    for cid in sorted(shown - set(data.crops)):
        data.problems.append(
            f"NEI shows crop {cid}, which CropsNH's crop dump doesn't list"
        )
    for cid, crop in list(data.crops.items()):
        page = pages.get(cid)
        if page is None:
            data.crops[cid] = replace(crop, in_nei=False)
            continue
        drops = []
        for d in crop.drops:
            check.drops += 1
            found = {
                s.ref
                for s in page.outputs
                if s.amount == d.amount and clean(refs.names.get(s.ref, "")) == d.name
            }
            if len(found) > 1:  # one item with and without NBT: the dump shows which
                found = {r for r in found if (r.nbt_hash != "") == (d.nbt is not None)}
            item = found.pop() if len(found) == 1 else None
            check.drops_keyed += item is not None
            drops.append(replace(d, item=item))
        sub: tuple[Ref, ...] = ()
        if len(page.inputs) > 2:
            s = page.inputs[2]
            sub = tuple(sorted({s.ref, *s.alts}))
        elif any(q.kind == "sub_soil" for q in crop.requirements):
            data.problems.append(f"crop:{cid}: NEI's page shows no sub-soil block")
        data.crops[cid] = replace(crop, drops=tuple(drops), sub_soils=sub, in_nei=True)
    keys = set()
    for n, r in enumerate(data.recipes):
        keys.add(r.key)
        data.recipes[n] = replace(r, in_nei=r.key in breeding)
        hidden = data.crops[r.child].in_nei is False
        if r.key not in breeding and not r.machine_only and not hidden:
            data.problems.append(
                f"recipe {r.key} isn't for the Crop Breeder only, but NEI's Crop Breeding tab "
                "doesn't show it"
            )
    for key in sorted(breeding - keys):
        data.problems.append(
            f"NEI's Crop Breeding tab shows {key}, which the dump doesn't"
        )


def read(
    run: Path,
    manifest: Mapping[str, Any],
    nei_lines: Iterable[Recipe] | None = None,
    refs: NeiRefs | None = None,
) -> CropData:
    """A collected run's crop data; the caller checks ``status(manifest) == "ok"`` first.
    ``nei_lines`` (the run's lines of :data:`NEI_HANDLERS`) and ``refs`` only when the run's
    ``parts.nei`` is ``ok``."""
    enc = charset(manifest)
    folder = run / DIR
    for name in FILES:
        if not (folder / name).is_file():
            raise FormatError(f"{DIR}/{name} is missing")
    problems: list[str] = []
    crops = parse_crops(_rows(folder / CROPS, enc))
    if not crops:
        problems.append("CropsNH's crop dump lists no crop")
    try:
        soils_text = (folder / SOILS).read_text(encoding=enc)
    except UnicodeDecodeError as exc:
        raise FormatError(f"{DIR}/{SOILS}: {exc}") from None
    data = CropData(
        crops=crops,
        recipes=parse_recipes(_rows(folder / RECIPES, enc), crops, problems),
        pools=parse_pools(_rows(folder / POOLS, enc), crops, problems),
        soils=parse_soils(soils_text),
        seeds=parse_seeds(_rows(folder / SEEDS, enc), crops, problems),
        problems=problems,
    )
    known = {s.name for s in data.soils} | {"global"}
    for c in crops.values():
        for s in c.soil_lists:
            if s not in known:
                problems.append(f"crop:{c.id}: soil list {s!r} isn't in {SOILS}")
    if nei_lines is not None and refs is not None:
        apply_nei(data, nei_lines, refs)
    return data


def item_refs(data: CropData) -> set[Ref]:
    """The item refs the crop tables point at: keyed drops, sub-soils and alternate seeds (any
    meta aside)."""
    out = {d.item for c in data.crops.values() for d in c.drops if d.item is not None}
    out.update(r for c in data.crops.values() for r in c.sub_soils)
    out.update(
        Ref(ITEM, s.registry_name, s.meta) for s in data.seeds if s.meta is not None
    )
    return out


# --- diff ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Change:
    key: str  # crop id, recipe key or soil list name
    field: str
    old: str
    new: str


@dataclass
class CropDiff:
    """What changed in crop data between two runs (for WO-1.9's export diff)."""

    crops_added: list[str] = field(default_factory=list)
    crops_removed: list[str] = field(default_factory=list)
    crops_changed: list[Change] = field(default_factory=list)
    recipes_added: list[str] = field(default_factory=list)
    recipes_removed: list[str] = field(default_factory=list)
    recipes_changed: list[Change] = field(default_factory=list)
    pools_added: list[str] = field(default_factory=list)
    pools_removed: list[str] = field(default_factory=list)
    pool_members_added: list[tuple[str, str]] = field(default_factory=list)
    pool_members_removed: list[tuple[str, str]] = field(default_factory=list)
    soils_changed: list[Change] = field(default_factory=list)
    seeds_added: list[str] = field(default_factory=list)
    seeds_removed: list[str] = field(default_factory=list)
    nei_compared: bool = (
        True  # NEI-only facts (item keys, sub-soils, in_nei) were compared
    )

    def empty(self) -> bool:
        return not any(
            getattr(self, f)
            for f in (
                "crops_added",
                "crops_removed",
                "crops_changed",
                "recipes_added",
                "recipes_removed",
                "recipes_changed",
                "pools_added",
                "pools_removed",
                "pool_members_added",
                "pool_members_removed",
                "soils_changed",
                "seeds_added",
                "seeds_removed",
            )
        )


def _text(value: object) -> str:
    return value if isinstance(value, str) else canonical(_plain(value))


def _plain(value: object) -> object:
    if isinstance(value, Ref):
        return value.text()
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    if isinstance(value, Requirement):
        return [value.kind, value.value, value.text]
    if isinstance(value, Drop):
        return [
            value.amount,
            value.chance_bp,
            value.name,
            value.nbt,
            _plain(value.item),
        ]
    return value


_CROP_FIELDS = (
    "name",
    "unlocalized",
    "flavour",
    "creator",
    "soil",
    "tier",
    "min_seed_bed_tier",
    "liked_biomes",
    "growth_duration",
    "breeding_threshold",
    "crossing_threshold",
    "weeds_above",
    "drop_multiplier",
    "requirements",
    "drops",
    "alternate_seeds",
)
_NEI_CROP_FIELDS = ("sub_soils", "in_nei")


def _seed_text(s: Seed) -> str:
    return f"{s.registry_name}@{'*' if s.meta is None else s.meta} -> crop:{s.crop}"


def diff(old: CropData, new: CropData) -> CropDiff:
    """Pure: what changed from ``old`` to ``new``. Facts only NEI gives (drop item keys,
    sub-soils, whether NEI shows a crop or recipe) are compared only when both have NEI's data,
    so a run whose NEI step failed doesn't show every drop as changed."""
    nei = old.has_nei() and new.has_nei()
    d = CropDiff(nei_compared=nei)
    d.crops_added = sorted(set(new.crops) - set(old.crops))
    d.crops_removed = sorted(set(old.crops) - set(new.crops))
    for cid in sorted(set(old.crops) & set(new.crops)):
        a, b = old.crops[cid], new.crops[cid]
        for name in _CROP_FIELDS + (_NEI_CROP_FIELDS if nei else ()):
            x, y = getattr(a, name), getattr(b, name)
            if name == "drops" and not nei:
                x = tuple(replace(q, item=None) for q in x)
                y = tuple(replace(q, item=None) for q in y)
            if x != y:
                d.crops_changed.append(Change(cid, name, _text(x), _text(y)))
    ra = {r.key: r for r in old.recipes}
    rb = {r.key: r for r in new.recipes}
    d.recipes_added = sorted(set(rb) - set(ra))
    d.recipes_removed = sorted(set(ra) - set(rb))
    for key in sorted(set(ra) & set(rb)):
        # The key holds the parents, so only the conditions (and NEI's view) can change.
        for name in ("requirements",) + (("in_nei",) if nei else ()):
            x, y = getattr(ra[key], name), getattr(rb[key], name)
            if x != y:
                d.recipes_changed.append(Change(key, name, _text(x), _text(y)))
    pa = {p.name: set(p.members) for p in old.pools}
    pb = {p.name: set(p.members) for p in new.pools}
    d.pools_added = sorted(set(pb) - set(pa))
    d.pools_removed = sorted(set(pa) - set(pb))
    for name in sorted(set(pa) & set(pb)):
        d.pool_members_added += [(name, c) for c in sorted(pb[name] - pa[name])]
        d.pool_members_removed += [(name, c) for c in sorted(pa[name] - pb[name])]
    sa = {s.name: s.blocks for s in old.soils}
    sb = {s.name: s.blocks for s in new.soils}
    for name in sorted(set(sa) | set(sb)):
        x, y = sa.get(name), sb.get(name)
        if x != y:
            d.soils_changed.append(Change(name, "blocks", _soil_text(x), _soil_text(y)))
    old_seeds = {_seed_text(s) for s in old.seeds}
    new_seeds = {_seed_text(s) for s in new.seeds}
    d.seeds_added = sorted(new_seeds - old_seeds)
    d.seeds_removed = sorted(old_seeds - new_seeds)
    return d


def _soil_text(blocks: tuple[SoilBlock, ...] | None) -> str:
    if blocks is None:
        return "(no such soil list)"
    return (
        ", ".join(
            b.registry_name + ("" if b.meta is None else f":{b.meta}") for b in blocks
        )
        or "(no block)"
    )


def diff_lines(d: CropDiff, limit: int = 40) -> list[str]:
    """The diff for people, at most ``limit`` lines per kind of change."""
    if d.empty():
        return ["Crops: no change"]
    out = [
        (
            f"Crops: {len(d.crops_added)} added, {len(d.crops_removed)} removed, "
            f"{len({c.key for c in d.crops_changed})} changed; parent recipes "
            f"{len(d.recipes_added)} added, {len(d.recipes_removed)} removed, "
            f"{len({c.key for c in d.recipes_changed})} changed; mutation pools "
            f"{len(d.pools_added)} added, {len(d.pools_removed)} removed, "
            f"{len(d.pool_members_added)} members added, {len(d.pool_members_removed)} removed; "
            f"{len(d.soils_changed)} soil lists changed; alternate seeds "
            f"{len(d.seeds_added)} added, {len(d.seeds_removed)} removed"
        )
    ]
    if not d.nei_compared:
        out.append(
            "  (item keys, sub-soils and NEI visibility not compared: a run has no NEI data)"
        )

    def block(title: str, items: list[str]) -> None:
        for line in items[:limit]:
            out.append(f"  {title} {line}")
        if len(items) > limit:
            out.append(f"  ... {len(items) - limit} more {title} lines")

    block("+ crop", [f"crop:{c}" for c in d.crops_added])
    block("- crop", [f"crop:{c}" for c in d.crops_removed])
    block(
        "~ crop",
        [f"crop:{c.key} {c.field}: {c.old} -> {c.new}" for c in d.crops_changed],
    )
    block("+ recipe", d.recipes_added)
    block("- recipe", d.recipes_removed)
    block(
        "~ recipe",
        [f"{c.key} {c.field}: {c.old} -> {c.new}" for c in d.recipes_changed],
    )
    block("+ pool", d.pools_added)
    block("- pool", d.pools_removed)
    block("+ pool member", [f"{p}: crop:{c}" for p, c in d.pool_members_added])
    block("- pool member", [f"{p}: crop:{c}" for p, c in d.pool_members_removed])
    block("~ soil", [f"{c.key}: {c.old} -> {c.new}" for c in d.soils_changed])
    block("+ seed", d.seeds_added)
    block("- seed", d.seeds_removed)
    return out
