"""Normalisation rules for export runs (plan section 5.2 step 3, [A§5.3], WO-1.3).

GregtecLink dumps raw game facts and Python normalises them here, in one place (the export
contract, ``docs/contracts/gregteclink-export-v1.md``). Nothing here reads a file.

- **Item key** = (registry name, meta, NBT hash), never a numeric ID. Meta 32767 (wildcard) is
  kept raw. The NBT hash is the sha256 of the canonical JSON of the NBT after
  :func:`normalize_nbt`, or ``""`` for no NBT (an empty compound counts as none).
- **Fluid key** = the FluidRegistry name.
- **Programmed circuit** = ``gregtech:gt.integrated_circuit`` with stack size 0: ``circuit`` is
  its meta. Any stack-size-0 input is not consumed.
- **Chances** in basis points (10000 = 100%): GT's and NEI's raw values already are.
- **Voltage tier** = the smallest t with 8 * 4^t >= EU/t / amperage (0 = ULV).
- **Identity hash** (:func:`signature`, D104) = sha256 over the key namespace (a GT recipe map,
  or an NEI handler for recipes GT doesn't have), GT's recipe category, the sorted inputs (key,
  amount, consumed, circuit) and the sorted outputs (key, amount). Input and output order never
  matters, because GT's slot order is not stable between runs (contract:
  ``gt.recipe.eyeofharmony``). A GT input slot is keyed by its displayed item; its oredict name
  and accepted alternatives are content. An NEI-only slot with alternatives is keyed by the
  oredict name whose members are exactly what it accepts (:class:`OreIndex`, from the run's
  ``items.jsonl``), else by the sorted set of what it accepts; never by the stack NEI displays,
  which NEI picks anew on each run (D104). The GT maps in :data:`ALT_IDENTITY_MAPS` key their
  slots with alternatives the NEI-only way too, because their displayed item changes between
  runs (D135).
- **Content hash** (:func:`content_hash`) = everything else that changes how the recipe runs:
  EU/t, duration, amperage, raw special value, chances, oredict names and alternatives, NEI
  container items, metadata, special items and flags (not ``nei_desc``, which is display text).
  For an NEI-only slot with alternatives it is the accepted set, not the displayed stack or
  that stack's container item. A rebalance changes only the content hash, so a diff reports
  the recipe as changed and its citation key ``r:<map>/<sig12>`` survives; so does an oredict
  gaining or losing a member.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None

CIRCUIT = "gregtech:gt.integrated_circuit"
WILDCARD = 32767
FULL = 10000  # basis points: a 100% chance
SIG_CHARS = 12  # the hex digits of a signature in a citation key, r:<map>/<sig12>

ITEM, FLUID = "i", "f"
# NEI typed NBT integers: "1B", "3S", "144L" (NBTJson); ints are bare.
_TYPED_INT = re.compile(r"(-?[0-9]+)[BSL]?")
# recipe_meta keys that are display text only: kept in the DB, left out of the content hash.
DISPLAY_ONLY = frozenset({"nei_desc"})
# GT recipe maps whose input slots with alternatives show a different alternative on each run, so
# they are keyed by what they accept, as NEI-only slots are (D135). CropsNH's Crop Breeder builds
# a sub-soil slot from a hash set of blocks: 12 of its 109 dev-runtime recipes showed another
# block in the server and the client harness's exports, with the same alternatives.
ALT_IDENTITY_MAPS = frozenset({"cropsnh.recipes.cropBreeder"})


def canonical(value: object) -> str:
    """Compact JSON with sorted keys: the one form every hash here is taken over."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def typed_int(value: object) -> int:
    """An int, or an NBTJson typed integer string such as ``"144L"`` (NEI's fluid ``Count``)."""
    if type(value) is int:
        return value
    if isinstance(value, str) and (m := _TYPED_INT.fullmatch(value)):
        return int(m.group(1))
    raise ValueError(f"not an integer: {value!r}")


def _stack_shaped(tag: Mapping[str, object]) -> bool:
    """A compound shaped like a saved ItemStack: numeric ``id``, ``Count`` and ``Damage``."""
    if not {"id", "Count", "Damage"} <= tag.keys():
        return False
    try:
        typed_int(tag["id"])
    except ValueError:
        return False
    return True


def normalize_nbt(tag: Json, id_names: Mapping[int, str | None] | None = None) -> Json:
    """``tag`` with no numeric item ID left in any ItemStack-shaped compound (the contract).

    A compound with GregtecLink's ``__item`` loses its numeric ``id``. With ``id_names`` (the
    run's numeric ID -> registry name table, from GregtecLink's ``__item`` compounds), an
    ItemStack-shaped compound without ``__item`` (NEI's NBT) gets ``__item`` from the table
    (None for an ID it doesn't know) and loses ``id`` too, so both sources hash alike. Numeric
    IDs in compounds of any other shape are left as they are.
    """
    if isinstance(tag, dict):
        out = {k: normalize_nbt(v, id_names) for k, v in tag.items()}
        if "__item" in tag:
            out.pop("id", None)
        elif id_names is not None and _stack_shaped(tag):
            out["__item"] = id_names.get(typed_int(tag["id"]))
            del out["id"]
        return out
    if isinstance(tag, list):
        return [normalize_nbt(v, id_names) for v in tag]
    return tag


def nbt_hash(tag: object) -> str:
    """The sha256 of normalised NBT, or ``""`` for none (``None`` or an empty compound)."""
    if tag is None or tag == {}:
        return ""
    return sha256(canonical(tag))


def item_ids(value: object, out: dict[int, str | None]) -> dict[int, str | None]:
    """Collect numeric ID -> registry name from every ``__item`` compound in ``value``."""
    if isinstance(value, dict):
        if "__item" in value and "id" in value:
            try:
                out.setdefault(typed_int(value["id"]), value["__item"])
            except ValueError:
                pass
        for v in value.values():
            item_ids(v, out)
    elif isinstance(value, list):
        for v in value:
            item_ids(v, out)
    return out


def voltage_tier(eut: int, amperage: int) -> int:
    """Smallest t with 8 * 4^t >= eut / amperage, in integers (eut includes the amperage)."""
    amps = max(amperage, 1)
    tier = 0
    while 8 * 4**tier * amps < eut:
        tier += 1
    return tier


@dataclass(frozen=True, slots=True, order=True)
class Ref:
    """What a slot holds: an item (registry, meta, NBT hash) or a fluid (name)."""

    kind: str  # ITEM or FLUID
    name: str  # registry name, or fluid name
    meta: int = 0
    nbt_hash: str = ""

    def ident(self) -> list[object]:
        if self.kind == FLUID:
            return [FLUID, self.name]
        return [ITEM, self.name, self.meta, self.nbt_hash]

    def text(self) -> str:
        """``registry@meta`` (``#<nbt8>`` with NBT) or ``fluid:<name>``, for people."""
        if self.kind == FLUID:
            return f"fluid:{self.name}"
        tail = f"#{self.nbt_hash[:8]}" if self.nbt_hash else ""
        return f"{self.name}@{self.meta}{tail}"


@dataclass(frozen=True, slots=True)
class Slot:
    """One input or output of a recipe."""

    ref: Ref
    amount: int  # items, or mB
    chance_bp: int = FULL
    consumed: bool = True  # inputs only
    circuit: int | None = None  # a programmed circuit's number
    # GT's oredict name for the slot (GTRecipe_WithAlt); for NEI-only slots, OreIndex's
    ore: str | None = None
    alts: tuple[Ref, ...] = ()  # accepted alternatives (GT alt, NEI anyOf), sorted
    returns: Ref | None = None  # NEI: a crafting grid's container item (bucket)


def input_slot(
    ref: Ref,
    amount: int,
    chance_bp: int = FULL,
    *,
    ore: str | None = None,
    alts: Iterable[Ref] = (),
    returns: Ref | None = None,
) -> Slot:
    """An input slot with the circuit and not-consumed rules applied."""
    consumed = amount != 0
    circuit = ref.meta if (ref.name == CIRCUIT and not consumed) else None
    return Slot(
        ref,
        amount,
        chance_bp,
        consumed,
        circuit,
        ore,
        tuple(sorted(set(alts))),
        returns,
    )


@dataclass(slots=True)
class Recipe:
    """A normalised recipe from GT's dump or NEI's, ready for the DB."""

    source: str  # gt, nei, crafting, furnace (gt+nei after a merge)
    space: str  # key namespace: the GT map, or the NEI handler id
    inputs: list[Slot]
    outputs: list[Slot]
    category: str | None = None  # GT's recipe category; None for NEI-only recipes
    handler: str | None = None  # NEI handler id
    eut: int | None = None
    duration: int | None = None
    amperage: int | None = None
    special_value: int | None = None
    flags: dict[str, bool] = field(default_factory=dict)
    meta: dict[str, Json] = field(default_factory=dict)
    owners: list[str] = field(default_factory=list)
    # NEI-only: an input with alternatives is keyed by its oredict name or accepted set
    alt_identity: bool = False
    signature: str = ""
    content_hash: str = ""
    ref: str = ""  # the citation key r:<map>/<sig12>[-n], set by gregtec.ingest.merge

    def seal(self) -> Recipe:
        """Compute the identity and content hashes from the fields."""
        self.signature = signature(
            self.space, self.category, self.inputs, self.outputs, self.alt_identity
        )
        self.content_hash = content_hash(self)
        return self

    @property
    def voltage_tier(self) -> int | None:
        if self.eut is None:
            return None
        return voltage_tier(self.eut, self.amperage or 1)


def _sorted(entries: Iterable[list[object]]) -> list[list[object]]:
    return sorted(entries, key=canonical)


def accepted(s: Slot) -> list[Ref]:
    """Everything an input slot accepts: its displayed stack and its alternatives, sorted."""
    return sorted({*s.alts, s.ref})


def _in_key(s: Slot, alt_identity: bool) -> list[object]:
    if alt_identity and s.alts:
        if s.ore is not None:
            return ["ore", s.ore]
        return ["any", [r.ident() for r in accepted(s)]]
    return s.ref.ident()


@dataclass(frozen=True)
class OreIndex:
    """The run's oredict, to name what an NEI slot accepts (D104): from ``oredict.jsonl`` when the
    run has it (D145), else as ``items.jsonl``'s ore lists give it (runs made before D145).

    An oredict member with meta 32767 (a wildcard, such as ``minecraft:planks@32767`` in
    ``plankWood``) stands for every meta of its item, as NEI expands it. Items with NBT are
    compared with their NBT hash.
    """

    # a member (a wildcard entry too) -> its ore names
    of: Mapping[Ref, frozenset[str]]
    # a registry name -> the ores that have it as a wildcard member
    wild_of: Mapping[str, frozenset[str]]
    # an ore -> its members that its wildcard members don't cover
    members: Mapping[str, frozenset[Ref]]
    # an ore -> the registry names of its wildcard members
    wild: Mapping[str, frozenset[str]]

    @classmethod
    def build(cls, items: Iterable[tuple[Ref, Iterable[str]]]) -> OreIndex:
        of: dict[Ref, set[str]] = {}
        listed: dict[str, set[Ref]] = {}
        for ref, ores in items:
            for o in ores:
                of.setdefault(ref, set()).add(o)
                listed.setdefault(o, set()).add(ref)
        wild = {
            o: {r.name for r in refs if r.meta == WILDCARD and not r.nbt_hash}
            for o, refs in listed.items()
        }
        wild_of: dict[str, set[str]] = {}
        for o, names in wild.items():
            for name in names:
                wild_of.setdefault(name, set()).add(o)
        members = {
            o: frozenset(
                r for r in refs if r.meta != WILDCARD and r.name not in wild[o]
            )
            for o, refs in listed.items()
        }
        return cls(
            {r: frozenset(o) for r, o in of.items()},
            {n: frozenset(o) for n, o in wild_of.items()},
            members,
            {o: frozenset(n) for o, n in wild.items()},
        )

    def name(self, refs: Iterable[Ref]) -> str | None:
        """The oredict name whose members are exactly ``refs`` (the first by name when several
        are, such as GT's ``blockAnyBronze`` and ``blockBronze``), or None."""
        refs = set(refs)
        if not refs or any(r.kind != ITEM for r in refs):
            return None
        names: set[str] | None = None
        for r in refs:
            ores = self.of.get(r, frozenset()) | self.wild_of.get(r.name, frozenset())
            names = set(ores) if names is None else names & ores
            if not names:
                return None
        assert names is not None
        covered = {r.name for r in refs}
        for o in sorted(names):
            if self.members[o] <= refs and self.wild[o] <= covered:
                return o
        return None


def signature(
    space: str,
    category: str | None,
    inputs: Iterable[Slot],
    outputs: Iterable[Slot],
    alt_identity: bool = False,
) -> str:
    """The identity hash: key namespace and category, the sorted inputs (key, amount, consumed,
    circuit) and the sorted outputs (key, amount).

    With ``alt_identity`` (NEI-only recipes), an input with alternatives is keyed by its oredict
    name (``ore``, which :class:`OreIndex` gives it) or else by the sorted set of everything it
    accepts, never by the stack NEI displays, so a recipe that takes any plank and one that
    takes only oak planks are two recipes, and each keeps its key between exports (D104).
    """
    body = {
        "map": space,
        "category": category,
        "in": _sorted(
            [_in_key(s, alt_identity), s.amount, s.consumed, s.circuit] for s in inputs
        ),
        "out": _sorted([s.ref.ident(), s.amount] for s in outputs),
    }
    return sha256(canonical(body))


def content_hash(recipe: Recipe) -> str:
    """Everything that changes how the recipe runs but not which recipe it is."""

    def ins(s: Slot) -> list[object]:
        returns = s.returns.ident() if s.returns else None
        shown: list[object] | None = s.ref.ident()
        alts = [a.ident() for a in s.alts]
        if recipe.alt_identity and s.alts:
            # NEI-only: the stack NEI displays changes between runs, and with it the container
            # item it gives back (a water bucket's bucket, none for a water cell); what the slot
            # accepts doesn't.
            shown, alts, returns = None, [a.ident() for a in accepted(s)], None
        return [
            shown,
            s.amount,
            s.consumed,
            s.circuit,
            s.chance_bp,
            s.ore,
            alts,
            returns,
        ]

    body = {
        "eut": recipe.eut,
        "duration": recipe.duration,
        "amperage": recipe.amperage,
        "special": recipe.special_value,
        "in": _sorted(ins(s) for s in recipe.inputs),
        "out": _sorted([s.ref.ident(), s.amount, s.chance_bp] for s in recipe.outputs),
        "meta": {k: v for k, v in recipe.meta.items() if k not in DISPLAY_ONLY},
        "flags": recipe.flags,
    }
    return sha256(canonical(body))


def cite(space: str, sig: str, suffix: int | None = None) -> str:
    """The citation key ``r:<map>/<sig12>``, with ``-<n>`` for an allow-listed collision."""
    tail = f"-{suffix}" if suffix is not None else ""
    return f"r:{space}/{sig[:SIG_CHARS]}{tail}"
