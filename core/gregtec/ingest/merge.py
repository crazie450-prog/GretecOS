"""Signature merge (GT vs NEI) and the collision test (plan section 5.2 step 3; D105, D106).

**Merge.** GT's dump is the source for every GT recipe map; NEI adds the handlers GT doesn't
have [A§5.3]. An NEI line whose handler is a GT recipe category is the NEI view of a GT recipe:

1. Its identity hash, taken as GT's would be (that category's map), equals a GT recipe's: the
   two are merged (``source`` ``gt+nei``; GT's numbers win).
2. Else it is compared with GT's recipes of the same category, EU/t and duration: every GT
   output must match one NEI output (same amount and chance) and every GT input a different NEI
   ingredient (same amount and consumed), where "match" also allows GT's wildcard meta 32767, an
   item among NEI's alternatives and, for an input of a recipe that isn't NBT-sensitive, NEI's
   stack with NBT where GT's has none (GT ignores NBT there). NEI may show more ingredients than
   GT's inputs only if they
   are not consumed or are one of the recipe's GT special items (data sticks, modules, petri
   dishes), and may leave out GT's not-consumed (0 mB) fluids. The first such GT recipe is
   merged.
3. An NEI line that matches only a GT recipe already merged is a duplicate; one that matches no
   GT recipe is dropped, because NEI shows some GT recipes in another form (fuel cells as fluids,
   for example) and GT's own row is the recipe. Both are counted by handler.

A merged slot also gets NEI's alternatives (``anyOf``: the oredict-equivalent stacks GT
accepts). The content hash stays GT's. NEI lines of other handlers, and of GT categories with
no recipe in GT's dump, are NEI-only recipes, keyed in their handler's namespace with oredict
slots keyed by their oredict name, or else their accepted set (D104). Lines of the handlers in
:data:`NOT_RECIPES` are left out: they are not recipes, and what they show changes between two
exports of one pack (D105).

**Collision test.** Recipes are grouped by citation key namespace and the first 12 hex digits
of their identity hash. A group whose recipes all have one content hash is one recipe registered
more than once: it is kept once (``duplicates``). A group with different content hashes is a
collision: two different recipes would share a key. Unless the allow-list
(:data:`ALLOW_LIST`, curated in the repo) names the group, the build fails and the collisions
are written out as a curation task. The allow-list gives each suffix its recipe by content hash
(the first 12 hex digits): ``r:<map>/<sig12>-1``, ``-2``, ... were first given in content-hash
order (plan section 5.2) and keep their recipes from then on. When one recipe of a group has
a new content hash (a rebalance) and the others are as listed, it keeps the one suffix left,
and the report asks for the allow-list to be updated; anything else fails the build (D106).
"""

from __future__ import annotations

import dataclasses
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.ingest import crops
from gregtec.ingest.normalize import (
    ALT_IDENTITY_MAPS,
    ITEM,
    SIG_CHARS,
    WILDCARD,
    OreIndex,
    Recipe,
    Ref,
    Slot,
    accepted,
    cite,
    signature,
)

ALLOW_LIST = Path(__file__).with_name("collisions.tsv")
ALLOW_HEADER = ("map", "sig12", "content12", "why")
_HEX = frozenset("0123456789abcdef")

# NEI handlers left out of the key space (D105): their lines are not recipes, and what they
# show changes between two exports of one pack. Their files stay in the collected run.
NOT_RECIPES = {
    "codechicken.nei.recipe.FuelRecipeHandler": (
        "furnace fuels: every line shows one random smelting example, never the fuel"
    ),
    "tconstruct.plugins.nei.RecipeHandlerToolMaterials": (
        "Tinkers' tool material stats, each shown with a random tool part"
    ),
    "gtneioreplugin.plugin.gregtech5.PluginGT5VeinStat": (
        "ore vein pages (for WO-1.6), showing the ore in a stone picked anew each run"
    ),
    "gtneioreplugin.plugin.gregtech5.PluginGT5SmallOreStat": (
        "small ore pages (for WO-1.6), showing the ore in a stone picked anew each run"
    ),
    "appeng.integration.modules.NEIHelpers.NEIFacadeRecipeHandler": (
        "AE2 cable facades: cosmetic, a set that changes with AE2's Facades.cfg, and NBT "
        "with numeric block IDs"
    ),
    # CropsNH's crop-stick tabs (WO-1.10, D136): crop facts, which the crop tables hold. Its
    # machines (Crop Breeder, Seed Generator, ...) are GT recipe maps and stay recipes.
    "cropsnhCrops": "CropsNH crop pages (seed, soils, drops without their chances): crop tables",
    "cropsnhBreeding": (
        "CropsNH breeding on crop sticks, where no parent is used up: crop tables"
    ),
    # Only lines of CropsNH seeds alone (crops.is_crop_line): the id has no cropsnh prefix.
    "mutationPool": "CropsNH mutation pools (members only, no pool name): crop tables",
    "cropsnhAlternateSeeds": "CropsNH alternate seeds (an item that plants a crop): crop tables",
}


@dataclass
class MergeStats:
    nei_rows: int = 0
    exact: int = 0
    matched: int = 0  # merged by step 2
    duplicates: Counter[str] = field(default_factory=Counter)  # by handler
    dropped: Counter[str] = field(default_factory=Counter)  # by handler
    # lines of the NOT_RECIPES handlers, left out, by handler
    not_recipes: Counter[str] = field(default_factory=Counter)
    nei_only: int = 0
    ore_named: int = 0  # NEI-only slots keyed by an oredict name
    ore_sets: int = 0  # NEI-only slots with alternatives no oredict name matches


def special_refs(meta: Mapping[str, Any]) -> set[tuple[str, int]]:
    """(registry, meta) of each item ref in a GT recipe's ``special_items``."""
    value = meta.get("special_items")
    found: set[tuple[str, int]] = set()
    for v in value if isinstance(value, list) else [value]:
        if (
            isinstance(v, dict)
            and isinstance(v.get("item"), str)
            and type(v.get("meta")) is int
        ):
            found.add((v["item"], v["meta"]))
    return found


def _same(g: Ref, n: Slot, any_nbt: bool = False) -> bool:
    """GT's ``g`` is what NEI's slot ``n`` shows: the same, among its alternatives, or GT's
    wildcard meta for NEI's item. With ``any_nbt`` (an input of a recipe that isn't
    NBT-sensitive, where GT ignores NBT), ``g`` without NBT also matches NEI's item with NBT."""
    if g == n.ref or g in n.alts:
        return True
    if not (g.kind == ITEM == n.ref.kind and g.name == n.ref.name):
        return False
    nbt = g.nbt_hash == n.ref.nbt_hash or (any_nbt and not g.nbt_hash)
    return nbt and g.meta in (WILDCARD, n.ref.meta)


def _pair(
    gt: Sequence[Slot], nei: Sequence[Slot], fits: Callable[[Slot, Slot], bool]
) -> dict[int, int] | None:
    """GT slot index -> NEI slot index, each NEI slot used once; exact refs are tried first."""
    used: set[int] = set()
    out: dict[int, int] = {}
    for exact in (True, False):
        for gi, g in enumerate(gt):
            if gi in out:
                continue
            for ni, n in enumerate(nei):
                if ni in used or not fits(g, n):
                    continue
                if exact and g.ref != n.ref:
                    continue
                out[gi] = ni
                used.add(ni)
                break
    return out if len(out) == len(gt) else None


def match(gt: Recipe, nei: Recipe) -> tuple[dict[int, int], dict[int, int]] | None:
    """(input pairing, output pairing) when ``nei`` shows ``gt`` (merge step 2), else None."""
    if len(gt.outputs) != len(nei.outputs):
        return None
    outs = _pair(
        gt.outputs,
        nei.outputs,
        lambda g, n: (
            g.amount == n.amount and g.chance_bp == n.chance_bp and _same(g.ref, n)
        ),
    )
    if outs is None:
        return None
    # A not-consumed fluid (0 mB: Eye of Harmony's hydrogen and helium) may be left out by NEI.
    required = [i for i, s in enumerate(gt.inputs) if s.consumed or s.ref.kind == ITEM]
    any_nbt = not gt.flags.get("nbt_sensitive", False)
    pairs = _pair(
        [gt.inputs[i] for i in required],
        nei.inputs,
        lambda g, n: (
            g.amount == n.amount
            and g.consumed == n.consumed
            and _same(g.ref, n, any_nbt)
        ),
    )
    if pairs is None:
        return None
    ins = {required[k]: v for k, v in pairs.items()}
    special = special_refs(gt.meta)
    paired = set(ins.values())
    for ni, n in enumerate(nei.inputs):
        if ni in paired or not n.consumed:
            continue
        if (n.ref.name, n.ref.meta) not in special:
            return None
    return ins, outs


def _exact_pairs(
    gt: Sequence[Slot], nei: Sequence[Slot], inputs: bool
) -> dict[int, int]:
    def fits(g: Slot, n: Slot) -> bool:
        return (
            g.ref == n.ref
            and g.amount == n.amount
            and (not inputs or g.consumed == n.consumed)
        )

    return _pair(gt, nei, fits) or {}


def _absorb(gt: Recipe, nei: Recipe, ins: dict[int, int]) -> None:
    """Mark ``gt`` merged with ``nei`` and give its inputs NEI's alternatives."""
    gt.source = "gt+nei"
    gt.handler = nei.handler
    for gi, ni in ins.items():
        g, n = gt.inputs[gi], nei.inputs[ni]
        extra = {*n.alts, n.ref} - {g.ref}
        if extra - set(g.alts):
            gt.inputs[gi] = dataclasses.replace(
                g, alts=tuple(sorted({*g.alts, *extra}))
            )


def _ore_named(r: Recipe, ores: OreIndex, stats: MergeStats) -> None:
    """Give each NEI-only input with alternatives the oredict name of what it accepts."""
    for i, s in enumerate(r.inputs):
        if not s.alts:
            continue
        name = ores.name(accepted(s))
        if name is None:
            stats.ore_sets += 1
        else:
            stats.ore_named += 1
            r.inputs[i] = dataclasses.replace(s, ore=name)


def merge(
    gt: Sequence[Recipe],
    nei: Iterable[Recipe],
    categories: Mapping[str, str],
    ores: OreIndex | None = None,
) -> tuple[list[Recipe], MergeStats]:
    """Merge NEI's recipes into GT's (``categories``: GT category -> its map; ``ores``: the
    run's oredict, to key NEI-only slots); return the GT recipes plus the NEI-only ones, in
    input order."""
    ores = ores or OreIndex.build(())
    stats = MergeStats()
    by_sig: dict[tuple[str, str], list[Recipe]] = defaultdict(list)
    by_run: dict[tuple[str | None, int | None, int | None], list[Recipe]] = defaultdict(
        list
    )
    for r in gt:
        by_sig[(r.space, r.signature)].append(r)
        by_run[(r.category, r.eut, r.duration)].append(r)
    filled = {r.category for r in gt}
    merged: set[int] = set()  # id() of merged GT recipes
    nei_only: list[Recipe] = []
    for n in nei:
        stats.nei_rows += 1
        handler = n.handler or ""
        if handler in NOT_RECIPES and (
            handler not in crops.NEI_HANDLERS or crops.is_crop_line(n)
        ):
            stats.not_recipes[handler] += 1
            continue
        space = categories.get(handler)
        if space is None or handler not in filled:
            # Not a GT category, or one with no recipe in GT's dump. In the dev runtime TecTech's
            # gt.recipe.fog_molten is empty in GT's dump (written at server start) while NEI
            # shows 723 recipes in it, so NEI's are the only ones there are.
            n.alt_identity = True
            _ore_named(n, ores, stats)
            nei_only.append(n.seal())
            stats.nei_only += 1
            continue
        # GT-style, so it matches the GT recipe's; keyed by what a slot accepts in the maps
        # whose displayed item is unstable (D135).
        sig = signature(space, handler, n.inputs, n.outputs, space in ALT_IDENTITY_MAPS)
        exact = by_sig.get((space, sig), [])
        free = [g for g in exact if id(g) not in merged]
        if free:
            g = free[0]
            _absorb(g, n, _exact_pairs(g.inputs, n.inputs, inputs=True))
            merged.add(id(g))
            stats.exact += 1
            continue
        if exact:
            stats.duplicates[handler] += 1
            continue
        found = None
        dup = False
        for g in by_run.get((handler, n.eut, n.duration), []):
            pairing = match(g, n)
            if pairing is None:
                continue
            if id(g) in merged:
                dup = True
                continue
            found = (g, pairing[0])
            break
        if found is not None:
            _absorb(found[0], n, found[1])
            merged.add(id(found[0]))
            stats.matched += 1
        elif dup:
            stats.duplicates[handler] += 1
        else:
            stats.dropped[handler] += 1
    return [*gt, *nei_only], stats


@dataclass(frozen=True)
class Collision:
    space: str
    sig12: str
    recipes: list[
        Recipe
    ]  # one per content hash: in suffix order, else content-hash order
    listed: tuple[
        str, ...
    ] = ()  # the allow-list's content12 for the group, if it has one


class CollisionError(RuntimeError):
    def __init__(self, collisions: list[Collision]) -> None:
        self.collisions = collisions
        super().__init__(
            f"{len(collisions)} identity collision(s): different recipes would share a key"
        )


@dataclass
class KeyResult:
    recipes: list[Recipe]  # one per key, sorted by key
    # rows dropped as copies of a kept recipe: "gt" (GT's own rows) or "nei" (NEI-only lines)
    duplicates: Counter[str]
    allowed: list[Collision]  # allow-listed collisions, suffixed
    stale: list[tuple[str, str]]  # allow-list entries with no collision in this run
    # allow-listed recipes with a new content hash that kept their suffix: (ref, content12)
    relisted: list[tuple[str, str]] = field(default_factory=list)


def _content12(text: str) -> bool:
    return len(text) == SIG_CHARS and set(text) <= _HEX


def read_allow_list(path: Path = ALLOW_LIST) -> dict[tuple[str, str], tuple[str, ...]]:
    """(map, sig12) -> the content12 of suffixes -1, -2, ... from the curated TSV (``#``
    lines are comments)."""
    allowed: dict[tuple[str, str], tuple[str, ...]] = {}
    if not path.is_file():
        return allowed
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not lines or tuple(lines[0].split("\t")) != ALLOW_HEADER:
        raise ValueError(
            f"{path.name}: the first line must be {chr(9).join(ALLOW_HEADER)}"
        )
    for number, line in enumerate(lines[1:], 2):
        cols = line.split("\t")
        content = tuple(cols[2].split(",")) if len(cols) > 2 else ()
        if (
            len(cols) != len(ALLOW_HEADER)
            or not _content12(cols[1])
            or len(content) < 2
            or len(set(content)) != len(content)
            or not all(_content12(c) for c in content)
        ):
            raise ValueError(
                f"{path.name}: row {number} is not map, sig12, content12 (two or more, "
                "comma-separated), why"
            )
        if (cols[0], cols[1]) in allowed:
            raise ValueError(
                f"{path.name}: row {number} lists {cols[0]} {cols[1]} again"
            )
        allowed[(cols[0], cols[1])] = content
    return allowed


def _suffixes(
    recipes: Sequence[Recipe], listed: Sequence[str]
) -> tuple[list[Recipe], Recipe | None] | None:
    """The group's recipes in suffix order (-1 first) and the one that kept its suffix with a
    new content hash, or None when the allow-list can't tell which recipe is which."""
    by_content = {r.content_hash[:SIG_CHARS]: r for r in recipes}
    if len(listed) != len(recipes) or len(by_content) != len(recipes):
        return None
    ordered = [by_content.pop(c, None) for c in listed]
    free = [i for i, r in enumerate(ordered) if r is None]
    if len(free) > 1:
        return None  # two or more changed: which suffix is whose can't be told
    changed = None
    if free:
        (changed,) = by_content.values()
        ordered[free[0]] = changed
    return [r for r in ordered if r is not None], changed


def assign_keys(
    recipes: Iterable[Recipe], allowed: Mapping[tuple[str, str], Sequence[str]]
) -> KeyResult:
    """Drop duplicates, check collisions and set each recipe's ``ref``; raise
    :class:`CollisionError` for collisions the allow-list doesn't cover."""
    groups: dict[tuple[str, str], list[Recipe]] = defaultdict(list)
    for r in recipes:
        groups[(r.space, r.signature[:SIG_CHARS])].append(r)
    kept: list[Recipe] = []
    duplicates: Counter[str] = Counter()
    bad: list[Collision] = []
    ok: list[Collision] = []
    relisted: list[tuple[str, str]] = []
    for (space, sig12), group in groups.items():
        distinct: dict[str, Recipe] = {}
        for r in group:
            if r.content_hash in distinct:
                duplicates["gt" if r.source in ("gt", "gt+nei") else "nei"] += 1
            else:
                distinct[r.content_hash] = r
        if len(distinct) == 1:
            group[0].ref = cite(space, sig12)
            kept.append(group[0])
            continue
        listed = tuple(allowed.get((space, sig12), ()))
        found = _suffixes(list(distinct.values()), listed) if listed else None
        if found is None:
            ordered = [distinct[h] for h in sorted(distinct)]
            bad.append(Collision(space, sig12, ordered, listed))
            continue
        ordered, changed = found
        ok.append(Collision(space, sig12, ordered, listed))
        for i, r in enumerate(ordered, 1):
            r.ref = cite(space, sig12, i)
            kept.append(r)
            if r is changed:
                relisted.append((r.ref, r.content_hash[:SIG_CHARS]))
    if bad:
        raise CollisionError(sorted(bad, key=lambda c: (c.space, c.sig12)))
    seen = {(c.space, c.sig12) for c in ok}
    stale = sorted(k for k in allowed if k not in seen)
    kept.sort(key=lambda r: r.ref)
    return KeyResult(kept, duplicates, ok, stale, sorted(relisted))
