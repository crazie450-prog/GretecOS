"""Signature merge (GT vs NEI) and the collision test (plan section 5.2 step 3; D105, D106).

**Merge.** GT's dump is the source for every GT recipe map; NEI adds the handlers GT doesn't
have [A§5.3]. An NEI line whose handler is a GT recipe category is the NEI view of a GT recipe:

1. Its identity hash, taken as GT's would be (that category's map), equals a GT recipe's: the
   two are merged (``source`` ``gt+nei``; GT's numbers win).
2. Else it is compared with GT's recipes of the same category, EU/t and duration: every GT
   output must match one NEI output (same amount and chance) and every GT input a different NEI
   ingredient (same amount and consumed), where "match" also allows GT's wildcard meta 32767 and
   an item among NEI's alternatives. NEI may show more ingredients than GT's inputs only if they
   are not consumed or are one of the recipe's GT special items (data sticks, modules, petri
   dishes), and may leave out GT's not-consumed (0 mB) fluids. The first such GT recipe is
   merged.
3. An NEI line that matches only a GT recipe already merged is a duplicate; one that matches no
   GT recipe is dropped, because NEI shows some GT recipes in another form (fuel cells as fluids,
   for example) and GT's own row is the recipe. Both are counted by handler.

A merged slot also gets NEI's alternatives (``anyOf``: the oredict-equivalent stacks GT
accepts). The content hash stays GT's. NEI lines of other handlers, and of GT categories with
no recipe in GT's dump, are NEI-only recipes, keyed in their handler's namespace with oredict
slots keyed by their accepted set (D104).

**Collision test.** Recipes are grouped by citation key namespace and the first 12 hex digits
of their identity hash. A group whose recipes all have one content hash is one recipe registered
more than once: it is kept once (``duplicates``). A group with different content hashes is a
collision: two different recipes would share a key. Unless the allow-list
(:data:`ALLOW_LIST`, curated in the repo) names the group with its size, the build fails and
the collisions are written out as a curation task. An allow-listed group's recipes get the keys
``r:<map>/<sig12>-1``, ``-2``, ... in content-hash order.
"""

from __future__ import annotations

import dataclasses
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.ingest.normalize import (
    ITEM,
    SIG_CHARS,
    WILDCARD,
    Recipe,
    Ref,
    Slot,
    cite,
    signature,
)

ALLOW_LIST = Path(__file__).with_name("collisions.tsv")
ALLOW_HEADER = ("map", "sig12", "recipes", "why")


@dataclass
class MergeStats:
    nei_rows: int = 0
    exact: int = 0
    matched: int = 0  # merged by step 2
    duplicates: Counter[str] = field(default_factory=Counter)  # by handler
    dropped: Counter[str] = field(default_factory=Counter)  # by handler
    nei_only: int = 0


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


def _same(g: Ref, n: Slot) -> bool:
    if g == n.ref or g in n.alts:
        return True
    return (
        g.kind == ITEM == n.ref.kind
        and g.meta == WILDCARD
        and g.name == n.ref.name
        and g.nbt_hash == n.ref.nbt_hash
    )


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
    pairs = _pair(
        [gt.inputs[i] for i in required],
        nei.inputs,
        lambda g, n: (
            g.amount == n.amount and g.consumed == n.consumed and _same(g.ref, n)
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


def merge(
    gt: Sequence[Recipe], nei: Iterable[Recipe], categories: Mapping[str, str]
) -> tuple[list[Recipe], MergeStats]:
    """Merge NEI's recipes into GT's (``categories``: GT category -> its map); return the GT
    recipes plus the NEI-only ones, in input order."""
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
        space = categories.get(handler)
        if space is None or handler not in filled:
            # Not a GT category, or one with no recipe in GT's dump. In the dev runtime TecTech's
            # gt.recipe.fog_molten is empty in GT's dump (written at server start) while NEI
            # shows 723 recipes in it, so NEI's are the only ones there are.
            n.alt_identity = True
            nei_only.append(n.seal())
            stats.nei_only += 1
            continue
        sig = signature(space, handler, n.inputs, n.outputs)
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
    recipes: list[Recipe]  # one per content hash, in content-hash order


class CollisionError(RuntimeError):
    def __init__(self, collisions: list[Collision]) -> None:
        self.collisions = collisions
        super().__init__(
            f"{len(collisions)} identity collision(s): different recipes would share a key"
        )


@dataclass
class KeyResult:
    recipes: list[Recipe]  # one per key, sorted by key
    duplicates: int  # rows dropped as copies of a kept recipe
    allowed: list[Collision]  # allow-listed collisions, suffixed
    stale: list[tuple[str, str]]  # allow-list entries with no collision in this run


def read_allow_list(path: Path = ALLOW_LIST) -> dict[tuple[str, str], int]:
    """(map, sig12) -> recipe count from the curated TSV (``#`` lines are comments)."""
    allowed: dict[tuple[str, str], int] = {}
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
        if (
            len(cols) != len(ALLOW_HEADER)
            or len(cols[1]) != SIG_CHARS
            or not cols[2].isdigit()
        ):
            raise ValueError(f"{path.name}: row {number} is not map, sig12, count, why")
        allowed[(cols[0], cols[1])] = int(cols[2])
    return allowed


def assign_keys(
    recipes: Iterable[Recipe], allowed: Mapping[tuple[str, str], int]
) -> KeyResult:
    """Drop duplicates, check collisions and set each recipe's ``ref``; raise
    :class:`CollisionError` for collisions not on the allow-list."""
    groups: dict[tuple[str, str], list[Recipe]] = defaultdict(list)
    for r in recipes:
        groups[(r.space, r.signature[:SIG_CHARS])].append(r)
    kept: list[Recipe] = []
    duplicates = 0
    bad: list[Collision] = []
    ok: list[Collision] = []
    for (space, sig12), group in groups.items():
        distinct: dict[str, Recipe] = {}
        for r in group:
            if r.content_hash in distinct:
                duplicates += 1
            else:
                distinct[r.content_hash] = r
        if len(distinct) == 1:
            group[0].ref = cite(space, sig12)
            kept.append(group[0])
            continue
        ordered = [distinct[h] for h in sorted(distinct)]
        collision = Collision(space, sig12, ordered)
        if allowed.get((space, sig12)) != len(ordered):
            bad.append(collision)
            continue
        ok.append(collision)
        for i, r in enumerate(ordered, 1):
            r.ref = cite(space, sig12, i)
            kept.append(r)
    if bad:
        raise CollisionError(sorted(bad, key=lambda c: (c.space, c.sig12)))
    seen = {(c.space, c.sig12) for c in ok}
    stale = sorted(k for k in allowed if k not in seen)
    kept.sort(key=lambda r: r.ref)
    return KeyResult(kept, duplicates, ok, stale)
