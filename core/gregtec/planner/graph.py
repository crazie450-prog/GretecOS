"""The recipe graph builder (WO-3.3, plan §7.1 steps 1 and 2, D11, D12, D61, D63, D64, D142).

**Step 1, resolve** (:func:`resolve_target`): the target text goes through the tool layer's
resolver (``item_resolve``: FTS + glossary, WO-2.2) and, when it is ambiguous, comes back as one
clarifying question naming the top three (WO-2.3's unsure-match logic), never a guess.

**Step 2, build** (:func:`build`): backward from the target, breadth first, from one export run
of the DB. Every node is identified by a stable key, so the same request on the same DB gives
the same graph (:meth:`Graph.canonical`):

- **item nodes** by their stable key: ``registry@meta[#nbt8]`` items, ``fluid:<name>`` fluids,
  ``ore:<name>`` oredict slots (D142 (h)), ``registry@32767`` wildcard slots, and ``eu``, the EU
  pseudo-item (each machine recipe consumes ``eut x duration`` EU per craft from it, for
  WO-3.4's conservation rows; power comes from WO-4's lines);
- **recipe nodes** by their citation key ``r:<map>/<sig12>``;
- **edges** ``in`` (item -> recipe, per craft), ``out`` (recipe -> item, expected per craft:
  amount x chance) and ``member`` (an oredict or wildcard member -> its slot node).

Loops are kept (it is a graph, not a tree): :attr:`Graph.cycles` lists every strongly connected
set of nodes.

**Candidate recipes** for an item are those of the run that output it, ``enabled`` and not
``fake`` (except GT's assembly-line recipes, which GT lists only in its fake NEI map: their
research recipe must be at the tier cap, D189). A candidate is rejected, with its reason, when:
it is above the tier cap or ``max_eut``; it is excluded (``r:``, an input's key, every machine by
``mte:``); it is an NEI-only recipe in a GT category (D142 (1)) or of an NEI handler with no
machine model; a fluid fills two of its input slots (GT counts those slot by slot, D191); or no
machine can run it for the player (:mod:`.machines`, WO-3.2: owned or unlockable at the tier,
``require_owned``, cleanroom, low gravity off, coil heat, addons off for ``base``, D12). The
machine check sizes multiblocks with one and two plain energy hatches of the tier cap only: by
D175 a recipe that runs at a tier runs at every higher one, so that is enough to decide. A
machine with a badge (flagged, low confidence, custom, D142 (a)) still counts, and a recipe that
only badged machines run carries the badge as a warning. Crafting-table and furnace recipes
(NEI's) are kept, flagged ``manual``.

**Pruning** (§7.1): candidates are ordered by the heuristic key :func:`heuristic` (recycling,
GT's container maps and EU-less recipes last, D142 (2), D189; then the reverse of one of the
item's own uses; avoid chanced-only; lower tier; the item as the main product before a
byproduct route; fluid cells after none; fewer inputs; base machine first; machine before
manual; unbadged first; key) and the first :data:`TOP_N` valid ones are kept; every candidate
stays listed with its status for D61's swap and "show all". Only primary choices (§7.1 step 3)
expand more than their first: the target and its direct inputs expand their top recipes but
their side routes (:func:`side_route`), and a chain template on the target's route expands its
recipes, its branch points primary (D189). A pin (D61) or a chain template (:mod:`.chains`)
replaces the heuristic's choice for its node, but a template's loop (:meth:`_Builder.in_loop`)
met off the route from outside the chain is left to the heuristic. Expansion stops at boundary items: the request's
``boundary``, My base stock and line outputs (D63), items no recipe makes (raw), items whose
every recipe was rejected (locked), catalysts (inputs a recipe doesn't use up: circuits,
moulds, lenses) and depth :data:`MAX_DEPTH`.
Oredict and wildcard slots expand to at most :data:`TOP_N` members (GregTech's own first).
An item slot with alternatives but no oredict name, and a fluid slot with alternatives, are
keyed by the stack GT shows (always accepted).

No solving here (WO-3.4) and no game number made up: every number is the DB's.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

from gregtec.agents import prefetch
from gregtec.agents.tools import items as item_tools
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Call, Env
from gregtec.planner import chains, machines
from gregtec.planner.gtmath.parallel import Supply
from gregtec.planner.gtmath.processing import EnergyHatch
from gregtec.planner.request import BaseInputs, Request, RequestError, is_stable_key

MAX_DEPTH = 12
TOP_N = 4
EU_KEY = "eu"
WILDCARD = 32767
FULL = 10000
MANUAL_SOURCES = frozenset({"crafting", "furnace"})
RESEARCH_MAPS = ("gt.recipe.researchStation", "gt.recipe.scanner")
RAW_INPUTS = chains.REPO_ROOT / "data" / "curated" / "raw_inputs.tsv"
GREGTECH = "gregtech"
IC2_CELL = "IC2:itemCellEmpty"
# GT maps that only move a material between containers or stack sizes (D189): ranked last.
CONTAINER_MAPS = frozenset(
    {
        "gt.recipe.canner",
        "gt.recipe.packager",
        "gt.recipe.unpackager",
    }
)

NodeKind = Literal["item", "fluid", "ore", "wildcard", "eu"]
Status = Literal["chosen", "alternative", "pruned", "unchecked", "rejected"]


class GraphError(RuntimeError):
    """The graph can't be built for this request; the message says why."""


# --- the graph -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Port:
    """One input or output of a recipe node, per craft."""

    key: str
    amount: int  # items, L, or EU (the eu port)
    chance_bp: int = FULL
    consumed: bool = True
    circuit: int | None = None  # a programmed circuit's number (not consumed)

    @property
    def expected(self) -> float:
        return self.amount * self.chance_bp / FULL


@dataclass(frozen=True)
class RecipeNode:
    id: str  # r:<map>/<sig12>
    map: str | None
    source: str
    eut: int | None
    duration: int | None
    tier: int  # GT voltage tier of the recipe (0 for manual recipes)
    inputs: tuple[Port, ...]  # the eu port last
    outputs: tuple[Port, ...]
    machines: tuple[int, ...]  # mte ids that can run it, base before addon
    base: bool  # a base-pack machine runs it (D12)
    badges: tuple[str, ...]  # warnings every machine option carries (D142 (a))
    flags: tuple[str, ...]  # manual, recycling, research, cleanroom, low_gravity
    research: str | None = None  # the research recipe (assembly line)

    def supplies(self, crafts: int) -> tuple[Supply, ...]:
        """What gtmath's parallel helper needs held for ``crafts`` crafts: an item that fills
        several slots merged into one supply (GT merges item inputs); an input that isn't used
        up (a circuit) present once, ``Supply(0, 1)`` (``Supply(0, 0)`` would mean it is
        missing and GT allows 0 crafts). The graph refuses recipes whose fluid fills several
        slots (D191), so each fluid is one slot here."""
        if crafts < 1:
            raise ValueError("crafts must be at least 1")
        held: dict[tuple[str, bool], int] = {}
        for p in self.inputs:
            if p.key == EU_KEY:
                continue
            fluid = p.key.startswith("fluid:")
            k = (p.key, fluid)
            held[k] = held.get(k, 0) + (p.amount if p.consumed else 0)
        return tuple(
            Supply(per_craft, per_craft * crafts if per_craft else 1, fluid)
            for (key, fluid), per_craft in sorted(held.items())
        )


@dataclass(frozen=True)
class Candidate:
    recipe: str
    status: Status
    reason: str = ""  # why it was rejected or pruned


@dataclass(frozen=True)
class ItemNode:
    id: str  # stable key
    kind: NodeKind
    name: str
    depth: int  # recipe steps from the target
    boundary: (
        str | None
    )  # request, stock, line, raw, locked, catalyst, depth, eu, byproduct
    candidates: tuple[
        Candidate, ...
    ] = ()  # every recipe making it, heuristic order (D61)
    chosen: tuple[str, ...] = ()  # the recipes expanded
    members: tuple[
        str, ...
    ] = ()  # ore and wildcard slots: their members, expanded first
    pinned: bool = False
    template: str | None = None  # the chain template that chose its recipes
    branch: str | None = None  # the template's branch-point id
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: Literal["in", "out", "member"]
    amount: float  # per craft (out: expected, amount x chance)


@dataclass(frozen=True)
class Graph:
    request: str  # the request's digest
    run_id: int
    target: str
    tier_cap: int
    items: tuple[ItemNode, ...]
    recipes: tuple[RecipeNode, ...]
    edges: tuple[Edge, ...]
    cycles: tuple[tuple[str, ...], ...]
    primary: tuple[str, ...]  # items whose recipe choice is primary (§7.1 step 3)
    templates: tuple[str, ...]  # chain templates used
    warnings: tuple[str, ...]

    def item(self, key: str) -> ItemNode:
        return next(i for i in self.items if i.id == key)

    def recipe(self, ref: str) -> RecipeNode:
        return next(r for r in self.recipes if r.id == ref)

    def canonical(self) -> str:
        return json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


# --- step 1: resolve -------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    key: str
    kind: str  # item or fluid
    name: str


def resolve_target(
    conn: sqlite3.Connection,
    text: str,
    glossary: Glossary | None = None,
    db: Path | None = None,
) -> Resolved | prefetch.Clarify:
    """The item or fluid ``text`` names (a machine means its item), or the one question to ask
    (QA:D11) when it isn't clear. Raises GraphError when nothing matches."""
    call = Call(conn, Env(db or Path(), glossary or Glossary()))
    res = item_tools.resolve(call, text)
    if not res.candidates:
        raise GraphError(f"nothing in the DB is called {text!r}")
    if res.ambiguous:
        found = [
            prefetch.Candidate(c.key, c.name, c.kind, c.mod or "")
            for c in res.candidates
        ]
        return prefetch.clarify(text, found)
    target = item_tools.target(call, text)
    if not isinstance(target, item_tools.Target):  # pragma: no cover - resolved above
        raise GraphError(f"{text!r} is ambiguous")
    return Resolved(target.key, target.kind, target.name)


# --- reading the DB --------------------------------------------------------------------------

_ITEM_KEY_SQL = (
    "i.registry_name || '@' || i.meta || CASE WHEN i.nbt_hash != '' THEN '#' ||"
    " substr(i.nbt_hash, 1, 8) ELSE '' END"
)
# GT's recycling recipes take everything apart: not a use, nor a way to make (D142 (2)).
_NOT_RECYCLING = (
    "coalesce(r.category, '') NOT LIKE '%recycling' AND NOT EXISTS (SELECT 1 FROM recipe_meta"
    " AS rm WHERE rm.recipe_id = r.id AND rm.key = 'recycle' AND rm.value_num != 0)"
)


@dataclass(frozen=True)
class _Info:
    key: str
    kind: NodeKind
    name: str
    mod: str
    cell: bool  # a GT or IC2 fluid cell (gt_prefix cell*, IC2:itemCellEmpty)


@dataclass
class _Recipe:
    id: int
    ref: str
    map: str | None
    source: str
    category: str | None
    eut: int | None
    duration: int | None
    amperage: int | None
    tier: int
    special: int | None
    fake: bool
    map_amperage: int
    inputs: list[tuple[int, Port, int | None]] = field(
        default_factory=list
    )  # slot, port, ore
    outputs: list[Port] = field(default_factory=list)
    meta: dict[str, float | str] = field(default_factory=dict)


class _Reader:
    """Batch reads of one export run, cached for the build."""

    def __init__(self, conn: sqlite3.Connection, run_id: int) -> None:
        self.conn = conn
        self.run = run_id
        self.items: dict[int, _Info] = {}
        self.fluids: dict[int, _Info] = {}
        self.ores: dict[int, str] = {}
        self.recipes: dict[int, _Recipe] = {}
        self.ids: dict[str, tuple[list[int], list[int]]] = {}  # key -> item, fluid ids
        self.makers: dict[
            str, list[tuple[int, int, int]]
        ] = {}  # key -> recipe, amount, chance
        self.machines: dict[str, list[machines.MachineInfo]] = {}
        self.uses: dict[str, frozenset[str]] = {}

    def _rows(self, sql: str, ids: Iterable[int]) -> list[sqlite3.Row]:
        return self.conn.execute(sql, (json.dumps(sorted(set(ids))),)).fetchall()

    def load_items(self, ids: Iterable[int]) -> None:
        want = [i for i in ids if i not in self.items]
        if not want:
            return
        for r in self._rows(
            f"SELECT i.id, {_ITEM_KEY_SQL} AS key, coalesce(i.name_en, '') AS name,"
            " coalesce(i.mod_id, '') AS mod, i.gt_prefix, i.registry_name FROM item AS i"
            " WHERE i.id IN (SELECT value FROM json_each(?))",
            want,
        ):
            cell = (r[4] or "").startswith("cell") or r[5] == IC2_CELL
            info = _Info(r["key"], "item", item_tools.clean(r["name"]), r["mod"], cell)
            self.items[int(r["id"])] = info
            self.ids.setdefault(info.key, ([], []))[0].append(int(r["id"]))

    def load_fluids(self, ids: Iterable[int]) -> None:
        want = [i for i in ids if i not in self.fluids]
        if not want:
            return
        for r in self._rows(
            "SELECT id, name, coalesce(name_en, '') FROM fluid"
            " WHERE id IN (SELECT value FROM json_each(?))",
            want,
        ):
            info = _Info(f"fluid:{r[1]}", "fluid", item_tools.clean(r[2]), "", False)
            self.fluids[int(r[0])] = info
            self.ids.setdefault(info.key, ([], []))[1].append(int(r[0]))

    def key_ids(self, key: str) -> tuple[list[int], list[int]]:
        """The DB ids of a stable key (an NBT key's prefix matches its full hash)."""
        if key in self.ids:
            return self.ids[key]
        items, fluids = chains._item_ids(self.conn, key)
        self.load_items(items)
        self.load_fluids(fluids)
        return self.ids.setdefault(key, ([], []))

    def info(self, key: str) -> _Info | None:
        items, fluids = self.key_ids(key)
        if items:
            return self.items[items[0]]
        if fluids:
            return self.fluids[fluids[0]]
        return None

    def load_makers(self, keys: Sequence[str]) -> None:
        """The recipes that output each of ``keys`` (enabled; not fake but the assembly line)."""
        keys = [k for k in keys if k not in self.makers]
        if not keys:
            return
        item_of: dict[int, str] = {}
        fluid_of: dict[int, str] = {}
        for k in keys:
            self.makers[k] = []
            items, fluids = self.key_ids(k)
            item_of.update({i: k for i in items})
            fluid_of.update({f: k for f in fluids})
        sql = (
            # CROSS JOIN keeps SQLite's planner on the output index (it would scan the run).
            "SELECT o.recipe_id, o.{col}, o.amount, o.chance_bp FROM json_each(?) AS j"
            " CROSS JOIN recipe_output AS o ON o.{col} = j.value"
            " CROSS JOIN recipe AS r ON r.id = o.recipe_id"
            " LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id"
            " WHERE r.run_id = ? AND r.enabled = 1"
            " AND (coalesce(r.fake, 0) = 0 OR m.unloc_name = ?)"
        )
        found: set[int] = set()
        for col, of in (("item_id", item_of), ("fluid_id", fluid_of)):
            if not of:
                continue
            for r in self.conn.execute(
                sql.format(col=col),
                (json.dumps(sorted(of)), self.run, chains.ASSEMBLY_LINE_MAP),
            ):
                self.makers[of[int(r[1])]].append((int(r[0]), int(r[2]), int(r[3])))
                found.add(int(r[0]))
        self.load_recipes(found)

    def load_recipes(self, ids: Iterable[int]) -> None:
        want = [i for i in ids if i not in self.recipes]
        if not want:
            return
        for r in self._rows(
            "SELECT r.id, r.ref, m.unloc_name, r.source, r.category, r.eut, r.duration_ticks,"
            " r.amperage, r.voltage_tier, r.special_value, r.fake, m.amperage"
            " FROM recipe AS r LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id"
            " WHERE r.id IN (SELECT value FROM json_each(?))",
            want,
        ):
            self.recipes[int(r[0])] = _Recipe(
                id=int(r[0]),
                ref=str(r[1]),
                map=r[2],
                source=str(r[3]),
                category=r[4],
                eut=r[5],
                duration=r[6],
                amperage=r[7],
                tier=int(r[8] or 0),
                special=r[9],
                fake=bool(r[10]),
                map_amperage=int(r[11]) if r[11] else 1,
            )
        slots = self._rows(
            "SELECT recipe_id, slot, item_id, fluid_id, ore_id, amount, consumed, chance_bp,"
            " circuit FROM recipe_input WHERE recipe_id IN (SELECT value FROM json_each(?))"
            " ORDER BY recipe_id, slot",
            want,
        )
        outs = self._rows(
            "SELECT recipe_id, slot, item_id, fluid_id, amount, chance_bp FROM recipe_output"
            " WHERE recipe_id IN (SELECT value FROM json_each(?)) ORDER BY recipe_id, slot",
            want,
        )
        self.load_items([r["item_id"] for r in [*slots, *outs] if r["item_id"]])
        self.load_fluids([r["fluid_id"] for r in [*slots, *outs] if r["fluid_id"]])
        ores = {int(r["ore_id"]) for r in slots if r["ore_id"]} - set(self.ores)
        for o in self._rows(
            "SELECT id, name FROM ore WHERE id IN (SELECT value FROM json_each(?))",
            ores,
        ):
            self.ores[int(o[0])] = str(o[1])
        for s in slots:
            rec = self.recipes[int(s["recipe_id"])]
            ore = int(s["ore_id"]) if s["ore_id"] else None
            key = self._slot_key(s["item_id"], s["fluid_id"], ore)
            if key is None:
                continue
            rec.inputs.append(
                (
                    int(s["slot"]),
                    Port(
                        key,
                        int(s["amount"]),
                        int(s["chance_bp"]),
                        bool(s["consumed"]),
                        s["circuit"],
                    ),
                    ore,
                )
            )
        for o in outs:
            key = self._slot_key(o["item_id"], o["fluid_id"], None)
            if key is not None:
                self.recipes[int(o["recipe_id"])].outputs.append(
                    Port(key, int(o["amount"]), int(o["chance_bp"]))
                )
        for m in self._rows(
            "SELECT recipe_id, key, value_num, value_text FROM recipe_meta"
            " WHERE recipe_id IN (SELECT value FROM json_each(?))"
            " AND key IN ('cleanroom', 'low_gravity', 'coil_heat', 'fusion_threshold',"
            " 'recycle')",
            want,
        ):
            value = m[2] if m[2] is not None else m[3]
            if value is not None:
                self.recipes[int(m[0])].meta[str(m[1])] = value

    def _slot_key(
        self, item_id: int | None, fluid_id: int | None, ore: int | None
    ) -> str | None:
        if ore is not None:
            return f"ore:{self.ores[ore]}"
        if fluid_id is not None:
            return self.fluids[int(fluid_id)].key
        if item_id is not None:
            return self.items[int(item_id)].key
        return None

    def research(self, key: str) -> tuple[str | None, int | None]:
        """The research recipe for an assembly-line output (fake scanner or research station
        recipes that show the item): the lowest-tier one, and that tier."""
        items, _ = self.key_ids(key)
        if not items:
            return None, None
        row = self.conn.execute(
            "SELECT r.ref, r.voltage_tier FROM json_each(?) AS j"
            " CROSS JOIN recipe_output AS o ON o.item_id = j.value"
            " CROSS JOIN recipe AS r ON r.id = o.recipe_id"
            " JOIN recipe_map AS m ON m.id = r.recipe_map_id"
            " WHERE r.run_id = ?"
            " AND m.unloc_name IN (?, ?) ORDER BY r.voltage_tier, r.ref LIMIT 1",
            (json.dumps(items), self.run, *RESEARCH_MAPS),
        ).fetchone()
        return (str(row[0]), int(row[1] or 0)) if row else (None, None)

    def members(self, key: str) -> list[str]:
        """The members of an oredict (``ore:``) or wildcard (``@32767``) slot."""
        if key.startswith("ore:"):
            rows = self.conn.execute(
                f"SELECT i.id, {_ITEM_KEY_SQL} FROM ore_member AS om JOIN ore AS o"
                " ON o.id = om.ore_id JOIN item AS i ON i.id = om.item_id WHERE o.name = ?",
                (key[4:],),
            ).fetchall()
        else:
            reg = key.rpartition("@")[0]
            rows = self.conn.execute(
                f"SELECT i.id, {_ITEM_KEY_SQL} FROM item AS i"
                " WHERE i.registry_name = ? AND i.meta != ?",
                (reg, WILDCARD),
            ).fetchall()
        self.load_items(int(r[0]) for r in rows)
        keys = {self.items[int(r[0])].key for r in rows}
        keys.discard(key)
        return sorted(
            keys,
            key=lambda k: (self.info(k).mod != GREGTECH, k),  # type: ignore[union-attr]
        )

    def made_from(self, key: str) -> frozenset[str]:
        """What the run's enabled recipes that use up ``key`` make (recycling aside), less
        what has a way of its own: a main-product recipe (not a container map, D189) that
        doesn't use ``key`` up. A recipe for ``key`` that consumes one of them undoes one of
        ``key``'s own uses (a loop: zinc sulfate, made only from sulfuric acid, back to the
        acid; sodium oxide, given off only by a recipe that takes sodium, back to sodium; an
        alloy taken apart). Water or hydrogen given off by a use, or salt made from salt water,
        have ways of their own, so salt and water in the mixer is a way to make salt water,
        not a reversal (review M1, D189)."""
        if key in self.uses:
            return self.uses[key]
        items, fluids = self.key_ids(key)
        found: set[tuple[int | None, int | None]] = set()
        for col, ids in (("item_id", items), ("fluid_id", fluids)):
            if ids:
                found.update(
                    (r[0], r[1])
                    for r in self.conn.execute(
                        "SELECT DISTINCT o.item_id, o.fluid_id FROM json_each(?) AS j"
                        f" CROSS JOIN recipe_input AS i ON i.{col} = j.value"
                        " CROSS JOIN recipe AS r ON r.id = i.recipe_id"
                        " CROSS JOIN recipe_output AS o ON o.recipe_id = i.recipe_id"
                        " WHERE i.consumed = 1 AND r.run_id = ? AND r.enabled = 1"
                        f" AND {_NOT_RECYCLING}",
                        (json.dumps(ids), self.run),
                    )
                )
        found = {
            (i, f)
            for i, f in found
            if (i is not None or f is not None)
            and not self._made_without(i, f, items, fluids)
        }
        self.load_items(i for i, _ in found if i is not None)
        self.load_fluids(f for i, f in found if i is None and f is not None)
        out = frozenset(
            self.items[i].key if i is not None else self.fluids[f].key for i, f in found
        )
        self.uses[key] = out
        return out

    def _made_without(
        self, item: int | None, fluid: int | None, items: list[int], fluids: list[int]
    ) -> bool:
        """Whether a recipe of the run (enabled; not fake but the assembly line; recycling and
        container maps aside) makes ``item`` (else ``fluid``) as its main product without
        using up any of ``items``/``fluids``."""
        col, value = ("item_id", item) if item is not None else ("fluid_id", fluid)
        row = self.conn.execute(
            "SELECT 1 FROM recipe_output AS o CROSS JOIN recipe AS r ON r.id = o.recipe_id"
            " LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id"
            f" WHERE o.{col} = ? AND o.slot = 0 AND r.run_id = ? AND r.enabled = 1"
            " AND (coalesce(r.fake, 0) = 0 OR m.unloc_name = ?)"
            " AND coalesce(m.unloc_name, '') NOT IN (SELECT value FROM json_each(?))"
            f" AND {_NOT_RECYCLING} AND NOT EXISTS (SELECT 1 FROM recipe_input AS i"
            " WHERE i.recipe_id = r.id AND i.consumed = 1"
            " AND (i.item_id IN (SELECT value FROM json_each(?))"
            " OR i.fluid_id IN (SELECT value FROM json_each(?)))) LIMIT 1",
            (
                value,
                self.run,
                chains.ASSEMBLY_LINE_MAP,
                json.dumps(sorted(CONTAINER_MAPS)),
                json.dumps(items),
                json.dumps(fluids),
            ),
        ).fetchone()
        return row is not None

    def machines_for(self, recipe_map: str) -> list[machines.MachineInfo]:
        if recipe_map not in self.machines:
            self.machines[recipe_map] = machines.load_machines(
                self.conn, recipe_map, self.run
            )
        return self.machines[recipe_map]


# --- candidates ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Check:
    """A recipe's validity for this request (independent of which item asked)."""

    ok: bool
    reason: str = ""
    node: RecipeNode | None = None
    fitted: bool = False  # the machine check has run


def heuristic(
    item: str, rec: RecipeNode, made_from: frozenset[str] = frozenset()
) -> tuple[object, ...]:
    """§7.1's pruning order for recipe ``rec`` as a way to make ``item`` (smaller first):
    recycling, GT's container maps and EU-less recipes last (D142 (2), D189); then the reverse
    of one of ``item``'s own uses (it consumes the main product of a recipe that uses ``item``
    up, ``made_from``, D189); avoid chanced-only; lower tier; ``item`` as the recipe's main
    product (first output) before a byproduct route; recipes that use fluid cells after those
    that don't (D189); fewer inputs; base machine first (D12); machine before manual; unbadged
    first (D142 (a)); then the key. All but the last four are known before the machine check
    (:func:`_cheap`)."""
    return (
        *_cheap(item, rec, made_from),
        not rec.base,
        "manual" in rec.flags,
        bool(rec.badges),
        rec.id,
    )


def _cheap(
    item: str, rec: RecipeNode, made_from: frozenset[str] = frozenset()
) -> tuple[object, ...]:
    outs = [p for p in rec.outputs if p.key == item]
    chanced_only = bool(outs) and all(p.chance_bp < FULL for p in outs)
    consumed = {p.key for p in rec.inputs if p.consumed and p.key != EU_KEY}
    return (
        bool({"recycling", "container", "eu0"} & set(rec.flags)),
        bool(consumed & made_from),
        chanced_only,
        rec.tier,
        not rec.outputs or rec.outputs[0].key != item,
        "cells" in rec.flags,
        len(consumed),
    )


def side_route(
    item: str, rec: RecipeNode, made_from: frozenset[str] = frozenset()
) -> str | None:
    """Why ``rec`` is a side route to ``item``, or None: ranked last (recycling, a container
    map, EU-less), the reverse of one of the item's uses, or ``item`` only as a byproduct.
    A primary node doesn't expand its side routes while it has another (D189): the platline
    step that gives nitrogen dioxide off is no way to plan nitric acid."""
    cheap = _cheap(item, rec, made_from)
    if cheap[0]:
        return "ranked last"
    if cheap[1]:
        return "the reverse of one of its uses"
    if cheap[4]:
        return "makes it as a byproduct"
    return None


def player(request: Request, base: BaseInputs) -> machines.Player:
    """The player the machine check sizes for: the tier cap (the request's, else My base's),
    owned machines, the request's constraints, and multiblocks with one and two plain energy
    hatches of the tier cap (WO-3.4 sizes with the same player)."""
    cap = request.tier_cap if request.tier_cap is not None else base.tier_cap()
    cons = request.constraints
    layouts = (
        ((EnergyHatch(cap),), (EnergyHatch(cap), EnergyHatch(cap))) if cap >= 1 else ()
    )
    return machines.Player(
        tier=cap,
        owned=frozenset(m for m, n in base.owned_machines().items() if n > 0),
        require_owned=cons.require_owned,
        coil_max=cons.coil_max,
        cleanroom=cons.cleanroom,
        addons=request.addons,
        hatches=layouts,
    )


class _Builder:
    def __init__(
        self,
        conn: sqlite3.Connection,
        request: Request,
        base: BaseInputs,
        templates: chains.Templates,
        run_id: int,
    ) -> None:
        self.req = request
        self.reader = _Reader(conn, run_id)
        self.templates = templates
        self.loops: dict[str, frozenset[str]] = {}  # chain -> items on its loops
        self.player = player(request, base)
        self.cap = self.player.tier
        cons = request.constraints
        self.excluded_recipes = cons.excluded_recipes()
        self.excluded_keys = cons.excluded_keys()
        self.excluded_mtes = cons.excluded_mtes()
        self.boundary: dict[str, str] = dict.fromkeys(raw_inputs(), "raw")
        for k, v in sorted(base.line_outputs().items()):
            if v > 0:
                self.boundary[k] = "line"
        for k, v in sorted(base.stock().items()):
            if v > 0:
                self.boundary[k] = "stock"
        for k in request.boundary:
            self.boundary[k] = "request"
        self.uses: dict[str, frozenset[str]] = {}  # item -> made_from, minus boundaries
        self.checks: dict[int, _Check] = {}
        self.fit: dict[
            tuple[object, ...], tuple[list[machines.MachineOption], str]
        ] = {}
        self.warnings: list[str] = []

    # one recipe -----------------------------------------------------------------------------

    def _machine_fit(self, r: _Recipe) -> tuple[list[machines.MachineOption], str]:
        assert r.map is not None and r.eut is not None and r.duration is not None
        info = machines.RecipeInfo(
            ref=r.ref,
            map=r.map,
            eut=r.eut,
            duration=r.duration,
            map_amperage=r.map_amperage,
            special_value=r.special,
            meta={k: v for k, v in r.meta.items() if k != "recycle"},
        )
        key = (
            info.map,
            info.eut,
            info.duration,
            info.map_amperage,
            info.special_value,
            tuple(sorted(info.meta.items())),
        )
        if key not in self.fit:
            ms = [
                m
                for m in self.reader.machines_for(r.map)
                if m.mte_id not in self.excluded_mtes
            ]
            sel = machines.choose(info, ms, self.player)
            reasons = sorted({f"{x.name}: {x.reason}" for x in sel.rejected})
            why = "; ".join(reasons[:3]) or f"no machine runs {r.map}"
            self.fit[key] = (list(sel.options), why)
        return self.fit[key]

    def check(self, rid: int, machines_too: bool = True) -> _Check:
        """Recipe ``rid``'s validity: the cheap checks, then (``machines_too``) the machines."""
        c = self.checks.get(rid)
        if c is None:
            c = self.checks[rid] = self._check(self.reader.recipes[rid])
        if not machines_too or not c.ok or c.node is None or c.fitted:
            return c
        c = self.checks[rid] = self._fit(self.reader.recipes[rid], c.node)
        return c

    def _check(self, r: _Recipe) -> _Check:
        if r.ref in self.excluded_recipes:
            return _Check(False, "excluded")
        consumed = [p for _, p, _ in r.inputs if p.consumed]
        for p in consumed:
            if p.key in self.excluded_keys:
                return _Check(False, f"uses excluded {p.key}")
        fluids = [p.key for p in consumed if p.key.startswith("fluid:")]
        dup = sorted({k for k in fluids if fluids.count(k) > 1})
        if dup:
            return _Check(
                False,
                f"{dup[0]} fills {fluids.count(dup[0])} input slots: GT counts them slot "
                "by slot, which the planner doesn't model (D191)",
            )
        manual = r.source in MANUAL_SOURCES
        if r.source == "nei" and not manual:
            if r.map is not None:
                return _Check(False, "NEI-only recipe in a GT category (D142 (1))")
            return _Check(False, "NEI-only handler: no machine model")
        flags: list[str] = []
        if manual:
            flags.append("manual")
        elif r.map is None or r.eut is None or r.duration is None:
            return _Check(False, "no recipe map, EU/t or duration")
        elif r.eut <= 0:
            # The Eye of Harmony, the neutron activator...: the machine powers it its own
            # way. Kept when a machine runs it (always a badged one), ranked last (D189);
            # fuels are WO-4's and their generators run no production recipe (WO-3.2).
            flags.append("eu0")
        if r.tier > self.cap:
            return _Check(False, f"tier {r.tier} is above the cap {self.cap}")
        cons = self.req.constraints
        if cons.max_eut is not None and r.eut is not None and r.eut > cons.max_eut:
            return _Check(False, f"{r.eut} EU/t is above max_eut {cons.max_eut}")
        if r.meta.get("recycle") or (r.category or "").endswith("_recycling"):
            flags.append("recycling")
        if r.map in CONTAINER_MAPS:
            flags.append("container")
        if any(
            _kind(p.key) == "item"
            and (info := self.reader.info(p.key)) is not None
            and info.cell
            for p in consumed
        ):
            flags.append("cells")
        research: str | None = None
        if r.map == chains.ASSEMBLY_LINE_MAP:
            made = sorted({p.key for p in r.outputs})
            research, tier = self.reader.research(made[0]) if made else (None, None)
            if research is None or tier is None:
                return _Check(False, "assembly line: no research recipe for its output")
            if tier > self.cap:
                return _Check(False, f"assembly line: its research needs tier {tier}")
            flags.append("research")
        inputs = [p for _, p, _ in sorted(r.inputs, key=lambda x: x[0])]
        if r.eut is not None and r.duration is not None and r.eut > 0:
            inputs.append(Port(EU_KEY, r.eut * r.duration))
        node = RecipeNode(
            id=r.ref,
            map=r.map,
            source=r.source,
            eut=r.eut,
            duration=r.duration,
            tier=r.tier,
            inputs=tuple(inputs),
            outputs=tuple(r.outputs),
            machines=(),
            base=True,
            badges=(),
            flags=tuple(sorted(flags)),
            research=research,
        )
        return _Check(True, node=node, fitted=manual)

    def _fit(self, r: _Recipe, node: RecipeNode) -> _Check:
        """The machine check (WO-3.2's model) of a recipe that passed the cheap ones."""
        options, why = self._machine_fit(r)
        if not options:
            return _Check(False, f"no machine: {why}", fitted=True)
        common = set(options[0].badges)
        needs = set(options[0].requires)
        for o in options[1:]:
            common &= set(o.badges)
            needs &= set(o.requires)
        return _Check(
            True,
            node=replace(
                node,
                machines=tuple(dict.fromkeys(o.mte_id for o in options)),
                base=any(not o.addon for o in options),
                badges=tuple(sorted(common)),
                flags=tuple(sorted({*node.flags, *needs})),
            ),
            fitted=True,
        )

    # one item -------------------------------------------------------------------------------

    def candidates(
        self, key: str
    ) -> tuple[list[RecipeNode], list[Candidate], list[Candidate]]:
        """(valid recipes in heuristic order, unchecked, rejected) for ``key``. The machine
        check runs in heuristic order, a group of equal cheap keys at a time, until
        :data:`TOP_N` are valid, and always for the recipes a pin or a template names; the
        rest are listed unchecked (D61's "show all" checks one when it is picked)."""
        need = (
            set(self.templates.choices[key].recipes)
            if key in self.templates.choices
            else set()
        )
        if (pin := self.req.pin_for(key)) is not None:
            need.add(pin)
        passed: list[tuple[int, RecipeNode]] = []
        rejected: list[Candidate] = []
        seen: set[int] = set()
        for rid, _, _ in self.reader.makers.get(key, []):
            if rid in seen:
                continue
            seen.add(rid)
            c = self.check(rid, machines_too=False)
            if c.ok and c.node is not None:
                passed.append((rid, c.node))
            else:
                rejected.append(
                    Candidate(self.reader.recipes[rid].ref, "rejected", c.reason)
                )
        uses = self.uses[key] = (
            self.reader.made_from(key) - self.boundary.keys()
            if len(passed) > 1
            else frozenset()
        )
        cheap = sorted(
            ((_cheap(key, n, uses), rid, n) for rid, n in passed),
            key=lambda x: (x[0], x[2].id),
        )
        valid: list[RecipeNode] = []
        unchecked: list[Candidate] = []
        group: tuple[object, ...] | None = None
        for prefix, rid, node in cheap:
            if len(valid) >= TOP_N and prefix != group and node.id not in need:
                unchecked.append(Candidate(node.id, "unchecked", "below the top 4"))
                continue
            group = prefix if len(valid) < TOP_N else group
            c = self.check(rid)
            if c.ok and c.node is not None:
                valid.append(c.node)
            else:
                rejected.append(Candidate(node.id, "rejected", c.reason))
        valid.sort(key=lambda n: heuristic(key, n, uses))
        rejected.sort(key=lambda c: c.recipe)
        return valid, unchecked, rejected

    def choose(
        self,
        key: str,
        valid: list[RecipeNode],
        rejected: list[Candidate],
        template: bool = True,
    ) -> tuple[list[str], bool, str | None, str | None, list[str]]:
        """(chosen refs, pinned, template, branch, warnings); ``template`` False leaves
        ``key``'s chain template out (a loop of the chain entered from outside it)."""
        refs = [n.id for n in valid]
        pin = self.req.pin_for(key)
        if pin is not None:
            if pin not in refs:
                why = next(
                    (c.reason for c in rejected if c.recipe == pin),
                    "it doesn't make this item",
                )
                raise RequestError(f"pin {pin} for {key}: not a candidate here ({why})")
            return [pin], True, None, None, []
        choice = self.templates.choices.get(key) if template else None
        if choice is not None:
            picked = [r for r in choice.recipes if r in refs]
            warns = [
                f"chain {choice.chain}: {r} for {key} is not a candidate here"
                for r in choice.recipes
                if r not in refs
            ]
            if picked:
                return picked, False, choice.chain, choice.branch, warns
            warns.append(
                f"chain {choice.chain}: none of its recipes for {key} fit; heuristic used"
            )
            return refs[:TOP_N], False, None, None, warns
        return refs[:TOP_N], False, None, None, []

    def in_loop(self, key: str) -> bool:
        """Whether ``key`` is on a loop of its chain template: its template recipes use up,
        through the template's own rows, an item made from ``key`` (the platline's calcium,
        electrolysed back from the calcium chloride its platinum step gives off)."""
        choice = self.templates.choices[key]
        if choice.chain not in self.loops:
            rows = {
                k: c
                for k, c in self.templates.choices.items()
                if c.chain == choice.chain
            }
            refs = sorted({r for c in rows.values() for r in c.recipes})
            ids = [
                int(r[0])
                for r in self.reader.conn.execute(
                    "SELECT id FROM recipe WHERE run_id = ?"
                    " AND ref IN (SELECT value FROM json_each(?))",
                    (self.reader.run, json.dumps(refs)),
                )
            ]
            self.reader.load_recipes(ids)
            inputs: dict[str, set[str]] = {}
            for r in self.reader.recipes.values():
                inputs[r.ref] = {p.key for _, p, _ in r.inputs if p.consumed}
            feeds = {
                k: {i for r in c.recipes for i in inputs.get(r, ()) if i in rows}
                for k, c in rows.items()
            }
            looped: set[str] = set()
            for start, fed in feeds.items():
                seen: set[str] = set()
                stack = list(fed)
                while stack:
                    k = stack.pop()
                    if k == start:
                        looped.add(start)
                        break
                    if k not in seen:
                        seen.add(k)
                        stack.extend(feeds.get(k, ()))
            self.loops[choice.chain] = frozenset(looped)
        return key in self.loops[choice.chain]


def raw_inputs(path: Path = RAW_INPUTS) -> tuple[str, ...]:
    """``data/curated/raw_inputs.tsv``'s keys: inputs taken from the world (D189)."""
    keys: list[str] = []
    header = False
    for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not text.strip() or text.startswith("#"):
            continue
        cells = text.split("\t")
        if not header:
            if cells[:2] != ["key", "note"]:
                raise ValueError(f"{path.name}:{n}: header must be key, note")
            header = True
            continue
        if not is_stable_key(cells[0]):
            raise ValueError(
                f"{path.name}:{n}: {cells[0]!r} is not an item or fluid key"
            )
        keys.append(cells[0])
    return tuple(keys)


# --- step 2: build ---------------------------------------------------------------------------


def _kind(key: str) -> NodeKind:
    if key == EU_KEY:
        return "eu"
    if key.startswith("fluid:"):
        return "fluid"
    if key.startswith("ore:"):
        return "ore"
    if key.endswith(f"@{WILDCARD}"):
        return "wildcard"
    return "item"


def build(
    conn: sqlite3.Connection,
    request: Request,
    base: BaseInputs,
    *,
    templates: chains.Templates | None = None,
    run_id: int | None = None,
    max_depth: int = MAX_DEPTH,
) -> Graph:
    """The recipe graph for ``request`` (see the module docstring)."""
    run = machines.current_run(conn) if run_id is None else run_id
    b = _Builder(
        conn,
        request,
        base,
        chains.load() if templates is None else templates,
        run,
    )
    reader = b.reader
    if reader.info(request.target) is None:
        raise GraphError(f"{request.target} is not in export run {run}")
    nodes: dict[str, ItemNode] = {}
    recipes: dict[str, RecipeNode] = {}
    flows: dict[tuple[str, str, str], float] = {}  # (src, dst, kind) -> per craft
    depth_of: dict[str, int] = {request.target: 0}
    catalysts: set[str] = set()
    made_at: dict[str, int] = {}  # recipe -> the depth of the item it was chosen for
    direct: set[str] = set()  # the target's direct inputs (primary choices)
    via: dict[str, set[str]] = {}  # item -> the chain templates that asked for it
    level = [request.target]
    depth = 0

    def on_route(direct: set[str]) -> set[str]:
        # The templates that choose the target's recipe or a direct input's: only their
        # branch points are primary choices (D189); one met elsewhere fixes its first pick.
        return {
            c.chain
            for k, c in b.templates.choices.items()
            if k == request.target or k in direct
        }

    while level:
        upcoming: set[str] = set()
        pending = sorted(set(level))
        reader.load_makers(
            [
                k
                for k in pending
                if _kind(k) in ("item", "fluid") and k not in b.boundary
            ]
        )
        while pending:
            same: set[str] = set()
            for key in pending:
                if key in nodes:
                    continue
                kind = _kind(key)
                info = reader.info(key) if kind in ("item", "fluid") else None
                name = info.name if info else key
                if key in b.boundary:
                    nodes[key] = ItemNode(key, kind, name, depth, b.boundary[key])
                    continue
                if kind == "eu":
                    nodes[key] = ItemNode(key, kind, "EU", depth, "eu")
                    continue
                if kind in ("ore", "wildcard"):
                    members = reader.members(key)
                    for m in members[:TOP_N]:
                        flows[(m, key, "member")] = 1.0
                        if m not in nodes:
                            depth_of.setdefault(m, depth)
                            same.add(m)
                    boundary = None if members else "raw"
                    nodes[key] = ItemNode(
                        key, kind, key[4:] if kind == "ore" else name, depth, boundary,
                        members=tuple(members),
                    )  # fmt: skip
                    continue
                if depth >= max_depth:
                    nodes[key] = ItemNode(key, kind, name, depth, "depth")
                    continue
                reader.load_makers([key])
                valid, unchecked, rejected = b.candidates(key)
                if not valid:
                    if request.pin_for(key) is not None:
                        b.choose(
                            key, valid, rejected
                        )  # raises: the pin can't be honoured
                    # Recipes make it, but none this player can run: locked, not a raw input.
                    nodes[key] = ItemNode(
                        key, kind, name, depth, "locked" if rejected else "raw",
                        candidates=tuple(rejected),
                    )  # fmt: skip
                    continue
                # A chain template off the route fixes its first pick, but a loop of the
                # chain is entered only from within it: calcium for an epoxid's calcium
                # acetate is no reason to run the platline (D189).
                choice = b.templates.choices.get(key)
                use = (
                    choice is None
                    or choice.chain in on_route(direct)
                    or choice.chain in via.get(key, ())
                    or not b.in_loop(key)
                )
                top, pinned, template, branch, warns = b.choose(
                    key, valid, rejected, use
                )
                # Only a primary choice (§7.1 step 3) keeps all of its top recipes in the
                # graph; the heuristic fixes every other node's choice to its first (D11),
                # and swapping one is a pin, which rebuilds (D61; D189).
                side = {
                    n.id: why
                    for n in valid
                    if (why := side_route(key, n, b.uses.get(key, frozenset())))
                }
                if template is not None and template in on_route(direct):
                    chosen = top
                elif key == request.target or key in direct:
                    # A primary choice, but not between side routes while it has others.
                    chosen = (
                        top if pinned else [r for r in top if r not in side] or top[:1]
                    )
                else:
                    chosen = top[:1]
                if key == request.target:
                    for ref in chosen:
                        rec = next(n for n in valid if n.id == ref)
                        direct.update(p.key for p in rec.inputs if p.consumed)
                status: dict[str, Status] = {
                    n.id: "chosen"
                    if n.id in chosen
                    else "alternative"
                    if n.id in top
                    else "pruned"
                    for n in valid
                }
                cands = (
                    tuple(
                        Candidate(
                            n.id,
                            status[n.id],
                            _reason(status[n.id], pinned, template, side.get(n.id)),
                        )
                        for n in valid
                    )
                    + tuple(unchecked)
                    + tuple(rejected)
                )
                nodes[key] = ItemNode(
                    key, kind, name, depth, None, cands, tuple(chosen), (), pinned,
                    template, branch, tuple(warns),
                )  # fmt: skip
                for ref in chosen:
                    if ref in recipes:  # chosen for another item already
                        continue
                    rec = next(n for n in valid if n.id == ref)
                    recipes[ref] = rec
                    made_at[ref] = depth
                    for p in rec.inputs:
                        k = (p.key, ref, "in")
                        flows[k] = flows.get(k, 0.0) + p.amount
                        if not p.consumed:
                            catalysts.add(p.key)
                            continue
                        if template is not None:
                            via.setdefault(p.key, set()).add(template)
                        if p.key not in nodes and p.key not in depth_of:
                            depth_of[p.key] = depth + 1
                            upcoming.add(p.key)
                    for p in rec.outputs:
                        k = (ref, p.key, "out")
                        flows[k] = flows.get(k, 0.0) + p.expected
            pending = sorted(same - set(nodes))
        level = sorted(upcoming - set(nodes))
        depth += 1
    # Catalysts and byproducts that nothing asked to make: listed, not expanded.
    for key in sorted(catalysts - set(nodes)):
        info = reader.info(key) if _kind(key) in ("item", "fluid") else None
        nodes[key] = ItemNode(
            key,
            _kind(key),
            info.name if info else key,
            depth_of.get(key, 0),
            "catalyst",
        )
    edges = tuple(Edge(s, d, k, a) for (s, d, k), a in sorted(flows.items()))  # type: ignore[arg-type]
    for e in edges:
        if e.kind == "out" and e.dst not in nodes:
            info = reader.info(e.dst)
            nodes[e.dst] = ItemNode(
                e.dst, _kind(e.dst), info.name if info else e.dst, made_at[e.src],
                "byproduct",
            )  # fmt: skip
    for p in request.pins:
        if p.node not in nodes:
            b.warnings.append(
                f"pin {p.recipe} for {p.node}: that node isn't in the graph"
            )
        elif (bound := nodes[p.node].boundary) not in (None, "locked"):
            b.warnings.append(
                f"pin {p.recipe} for {p.node}: not used, the node is a {bound} boundary"
            )
    items = tuple(nodes[k] for k in sorted(nodes))
    used = sorted({n.template for n in items if n.template})
    primary = {request.target}
    for ref in nodes[request.target].chosen:
        primary.update(
            p.key for p in recipes[ref].inputs if p.consumed and p.key != EU_KEY
        )
    routed = on_route(direct)
    primary.update(n.id for n in items if n.branch and n.template in routed)
    primary &= {n.id for n in items if n.chosen}
    warnings = list(b.warnings)
    for n in items:
        warnings.extend(n.warnings)
    for r in sorted(recipes.values(), key=lambda r: r.id):
        if r.badges:
            warnings.append(f"{r.id}: every machine for it is {', '.join(r.badges)}")
    return Graph(
        request=request.digest(),
        run_id=run,
        target=request.target,
        tier_cap=b.cap,
        items=items,
        recipes=tuple(recipes[k] for k in sorted(recipes)),
        edges=edges,
        cycles=cycles(edges),
        primary=tuple(sorted(primary)),
        templates=tuple(used),
        warnings=tuple(warnings),
    )


def _reason(
    status: Status, pinned: bool, template: str | None, side: str | None = None
) -> str:
    if status == "chosen":
        return ""
    if pinned:
        return "pinned to another recipe"
    if template:
        return f"chain template {template} chose others"
    if status == "alternative" and side:
        return f"a side route ({side}): not expanded unless pinned (D61, D189)"
    if status == "alternative":
        return "not a primary choice: the first is used unless pinned (D11, D61)"
    return f"below the top {TOP_N}"


def cycles(edges: Sequence[Edge]) -> tuple[tuple[str, ...], ...]:
    """Every strongly connected set of two or more nodes (loops), each sorted, in order
    (Tarjan's algorithm, iterative)."""
    succ: dict[str, list[str]] = {}
    for e in edges:
        succ.setdefault(e.src, []).append(e.dst)
        succ.setdefault(e.dst, [])
    for v in succ.values():
        v.sort()
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    out: list[tuple[str, ...]] = []
    counter = 0
    for root in sorted(succ):
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on.add(v)
            nexts = succ[v]
            if i < len(nexts):
                work.append((v, i + 1))
                w = nexts[i]
                if w not in index:
                    work.append((w, 0))
                elif w in on:
                    low[v] = min(low[v], index[w])
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    out.append(tuple(sorted(comp)))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return tuple(sorted(out))
