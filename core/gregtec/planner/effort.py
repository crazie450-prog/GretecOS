"""The effort model (WO-3.5, plan §7.2 "Effort model", D17, D62, D196).

``eff_h`` is the processing time to make a route's build components from boundary items at the
request's tier, plus ``unlock_h``. Gathering and hands-on building are left out; scarcity is
scored on its own (D62). Every time is the DB's: a GT recipe's machine-seconds per craft come
from the machine the planner sizes it with (:mod:`.sizing`, GT's math), never an estimate.

- **Build components** (:func:`components`): every node's machine item (``machine.item_id``)
  times its count, and for a multiblock the blocks of its smallest ``structure`` variant (WO-1.8)
  times its count. Hatches, cables and pipes wait for the layout (WO-3.6, WO-10.5).
- **Making a component** (:func:`steps`): the designer's own pipeline for the component as a
  batch at the tier (WO-3.3's graph, its boundary being raw inputs, My base stock, line outputs
  and the request's ``boundary``; WO-3.4's machines and staged MILP), every primary choice its
  first, simplest recipe (D11), so loops balance as in a line. Whole crafts per recipe; a GT
  recipe costs its crafts x machine-seconds per craft; a crafting-table or furnace step (NEI's,
  no machine and no duration in the DB) costs ``craft_s`` per craft (default 2 s, ``craft_s``
  in ``me/prefs.md``). An item the flow imports through a gate (WO-3.3's graph stopped before
  it: an MV hull in a machine's flow) is made the same way, in the quantity imported, down to
  :data:`GATE_DEPTH` levels and never inside its own making (D196, review M4). An item with no
  recipe or no route at the tier, or gated deeper than that, isn't counted; one note per cause
  lists them.
- **Spread over his machines**: each recipe map's seconds are divided by the machines he owns
  on that map (My base), at least 1 (§7.2's default of one per recipe map).
- **Unlocks** (``unlock_h``): with owned machines listed in My base, a component step on a
  recipe map where he owns no machine (and the route builds none) needs that machine first:
  its components are walked the same way, recursively to depth :data:`UNLOCK_DEPTH`, each map
  unlocked once (de-duplicated). With no owned machines listed (WO-3.8 not filled in), every map
  counts as one machine he has, so ``unlock_h`` is 0 and a note says so (D196).

The result's ``rows`` are the breakdown table: per part (``build`` or ``unlock``) and recipe map
(``crafting`` for hand steps), the crafts, the machines it is spread over and the hours.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from gregtec.planner import chains, graph, sizing, solve
from gregtec.planner.graph import Graph
from gregtec.planner.request import BaseInputs, Request, RequestError, is_stable_key

CRAFT_S = 2.0  # §7.2: a crafting-table step's default time
UNLOCK_DEPTH = 3  # §7.2: unlock recursion depth
# how deep make() follows items a component's flow imports through a gate
GATE_DEPTH = 3
HAND = "crafting"  # the breakdown's map name for crafting-table and furnace steps


@dataclass
class Walk:
    """The steps to make some components: per recipe map (``crafting`` for hand steps) the
    crafts and machine-seconds, and the machine each map's steps are sized on."""

    crafts: dict[str, int] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)
    machine: dict[str, int] = field(default_factory=dict)  # map -> mte (first met)
    missing: list[str] = field(default_factory=list)  # items with no recipe at the tier
    # gated items past GATE_DEPTH or in a loop
    deep: list[str] = field(default_factory=list)

    def add(self, other: Walk) -> None:
        for k, v in other.crafts.items():
            self.crafts[k] = self.crafts.get(k, 0) + v
        for k, s in other.seconds.items():
            self.seconds[k] = self.seconds.get(k, 0.0) + s
        for k, m in other.machine.items():
            self.machine.setdefault(k, m)
        for k in other.missing:
            if k not in self.missing:
                self.missing.append(k)
        for k in other.deep:
            if k not in self.deep:
                self.deep.append(k)


@dataclass
class Need:
    """Fractional crafts per recipe, summed over every component (and gated item) of one
    walk, before :meth:`walk` rounds each recipe up to whole crafts once."""

    crafts: dict[str, float] = field(default_factory=dict)  # recipe -> crafts
    # recipe -> (map or HAND, seconds per craft, mte or None), first met
    how: dict[str, tuple[str, float, int | None]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    deep: list[str] = field(default_factory=list)

    def add(self, other: Need) -> None:
        for k, v in other.crafts.items():
            self.crafts[k] = self.crafts.get(k, 0.0) + v
        for k, h in other.how.items():
            self.how.setdefault(k, h)
        self.missing.extend(k for k in other.missing if k not in self.missing)
        self.deep.extend(k for k in other.deep if k not in self.deep)

    def walk(self) -> Walk:
        """Whole crafts per recipe; a machine recipe costs crafts x its machine-seconds per
        craft, a hand step its ``craft_s``."""
        out = Walk(missing=list(self.missing), deep=list(self.deep))
        for pid in sorted(self.crafts):
            n = math.ceil(round(self.crafts[pid], 6))
            if n <= 0:
                continue
            where, per, mte = self.how[pid]
            if mte is not None:
                out.machine.setdefault(where, mte)
            out.crafts[where] = out.crafts.get(where, 0) + n
            out.seconds[where] = out.seconds.get(where, 0.0) + n * per
        return out


def needs(
    g: Graph,
    sized: Mapping[str, sizing.Sized],
    crafts: Mapping[str, float],
    qty: float,
    craft_s: float = CRAFT_S,
) -> Need:
    """The crafts to make ``qty`` components, from ``crafts``: each recipe's crafts for one
    (a solved flow's); a hand step (crafting table, furnace) costs ``craft_s`` per craft."""
    out = Need()
    for pid in sorted(crafts):
        c = crafts[pid] * qty
        if c <= 0:
            continue
        rec = g.recipe(pid)
        s = sized.get(pid)
        if s is not None and s.option is not None and s.seconds > 0:
            out.how[pid] = (rec.map or HAND, s.seconds, s.option.mte_id)
        else:
            out.how[pid] = (HAND, craft_s, None)
        out.crafts[pid] = c
    return out


def steps(
    g: Graph,
    sized: Mapping[str, sizing.Sized],
    crafts: Mapping[str, float],
    qty: float,
    craft_s: float = CRAFT_S,
) -> Walk:
    """:func:`needs` in whole crafts per recipe (:meth:`Need.walk`)."""
    return needs(g, sized, crafts, qty, craft_s).walk()


# --- the DB side ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Component:
    key: str  # the item's stable key
    qty: int
    of: int  # the machine (mte id) it is part of
    part: str  # machine | structure


def machine_items(conn: sqlite3.Connection, mtes: Sequence[int]) -> dict[int, str]:
    """mte id -> the stable key of its item."""
    rows = conn.execute(
        f"SELECT m.mte_id, {graph._ITEM_KEY_SQL} FROM machine AS m JOIN item AS i"
        " ON i.id = m.item_id WHERE m.mte_id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(set(mtes))),),
    ).fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


def structure_blocks(conn: sqlite3.Connection, mte: int) -> dict[str, int] | None:
    """The blocks of the multiblock's smallest structure variant, by stable key (None: no
    structure row)."""
    rows = conn.execute(
        "SELECT s.variant, s.blocks_json FROM structure AS s JOIN machine AS m"
        " ON m.id = s.machine_id WHERE m.mte_id = ? ORDER BY s.variant, s.source",
        (mte,),
    ).fetchall()
    best: dict[str, int] | None = None
    for _, text in rows:
        try:
            cells = json.loads(text or "[]")
        except ValueError:
            continue
        counts: dict[str, int] = {}
        for c in cells:
            if isinstance(c, dict) and isinstance(c.get("block"), str):
                k = f"{c['block']}@{int(c.get('meta') or 0)}"
                counts[k] = counts.get(k, 0) + 1
        if best is None or sum(counts.values()) < sum(best.values()):
            best = counts
    return best


def maps_of(
    conn: sqlite3.Connection, mtes: Sequence[int]
) -> dict[int, tuple[str, ...]]:
    """mte id -> the recipe maps it runs."""
    out: dict[int, list[str]] = {}
    for mte, name in conn.execute(
        "SELECT m.mte_id, rm.unloc_name FROM machine AS m JOIN machine_map AS mm"
        " ON mm.machine_id = m.id JOIN recipe_map AS rm ON rm.id = mm.recipe_map_id"
        " WHERE m.mte_id IN (SELECT value FROM json_each(?)) ORDER BY 1, 2",
        (json.dumps(sorted(set(mtes))),),
    ):
        out.setdefault(int(mte), []).append(str(name))
    return {k: tuple(v) for k, v in out.items()}


def components(
    conn: sqlite3.Connection, plan: Mapping[str, Any]
) -> tuple[list[Component], list[str]]:
    """A route's build components and notes (multiblocks with no structure data)."""
    counts: dict[int, int] = {}
    kinds: dict[int, str] = {}
    for n in plan["nodes"]:
        m = n.get("machine")
        if m and n.get("count"):
            counts[m["mte"]] = counts.get(m["mte"], 0) + int(n["count"])
            kinds[m["mte"]] = m["kind"]
    item_of = machine_items(conn, list(counts))
    out: list[Component] = []
    notes: list[str] = []
    for mte in sorted(counts):
        if mte in item_of:
            out.append(Component(item_of[mte], counts[mte], mte, "machine"))
        else:
            notes.append(f"mte:{mte} has no item in the DB: its making is not counted")
        if kinds[mte] == "multiblock":
            blocks = structure_blocks(conn, mte)
            if blocks is None:
                notes.append(
                    f"mte:{mte}: no structure data, its casings and coils are not counted"
                )
                continue
            for k in sorted(blocks):
                if is_stable_key(k):
                    out.append(Component(k, blocks[k] * counts[mte], mte, "structure"))
    return out, notes


@dataclass(frozen=True)
class _Solved:
    graph: Graph
    sized: Mapping[str, sizing.Sized]
    crafts: dict[str, float]  # recipe -> crafts for one component
    gated: tuple[tuple[str, float], ...]  # item -> qty imported through a gate, for one


class Effort:
    """The effort of one design's routes; each component is solved once."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        request: Request,
        base: BaseInputs,
        tier_cap: int,
        *,
        craft_s: float = CRAFT_S,
        templates: chains.Templates | None = None,
        run_id: int | None = None,
    ) -> None:
        self.conn = conn
        self.request = request
        self.base = base
        self.tier_cap = tier_cap
        self.craft_s = craft_s
        self.templates = templates
        self.run_id = run_id
        self._solved: dict[str, _Solved | None] = {}
        self.owned = {m: n for m, n in base.owned_machines().items() if n > 0}
        self.on_map: dict[str, int] = {}
        for mte, maps in maps_of(conn, list(self.owned)).items():
            for name in maps:
                self.on_map[name] = self.on_map.get(name, 0) + self.owned[mte]

    def _solve(self, key: str) -> _Solved | None:
        """One ``key``'s graph, machines and solved flow (cached); None when it has no
        recipe at the tier or no route."""
        if key in self._solved:
            return self._solved[key]
        from gregtec.planner import plan  # plan imports this module

        r = self.request
        out: _Solved | None = None
        try:
            req = Request(
                key,
                mode="batch",
                qty=1.0,
                tier_cap=self.tier_cap,
                machines=r.machines,
                boundary=r.boundary,
                constraints=r.constraints,
                routes=1,
            )
            g = graph.build(
                self.conn, req, self.base, templates=self.templates, run_id=self.run_id
            )
            if g.recipes:
                sized = sizing.size(
                    self.conn,
                    g,
                    graph.player(req, self.base),
                    addon=r.addons,
                    excluded_mtes=r.constraints.excluded_mtes(),
                )
                net = plan.network(g, req, sized, base_only=not r.addons)
                first = {k: options[0] for k, options in net.choices}
                flow = solve.solve_route(net, first)
                _, window = plan.rate_and_window(req)
                if window is None:  # a batch request always has one
                    raise RuntimeError(f"{key}: batch request without a window")
                crafts = {pid: x * window for pid, x in flow.crafts if x > 0}
                imports = dict(flow.imports)
                gated = tuple(
                    (k, imports[k] * window)
                    for k in flow.gates
                    if k != key and imports.get(k, 0) > 0
                )
                out = _Solved(g, sized, crafts, gated)
        except (graph.GraphError, RequestError, LookupError, solve.SolveError):
            out = None
        self._solved[key] = out
        return out

    def make(self, key: str, qty: float, within: tuple[str, ...] = ()) -> Need:
        """The steps to make ``qty`` of ``key`` from boundary items at the tier. An item its
        flow imports through a gate (an MV hull in a machine's flow: WO-3.3's graph stops
        before it) is made the same way, in the quantity imported, to :data:`GATE_DEPTH`
        levels (``within``: the items being made above this one, which also stops loops)."""
        solved = self._solve(key)
        if solved is None:
            return Need(missing=[key])
        w = needs(solved.graph, solved.sized, solved.crafts, qty, self.craft_s)
        path = (*within, key)
        for k, per in solved.gated:
            if len(path) > GATE_DEPTH or k in path:
                if k not in w.deep:
                    w.deep.append(k)
            else:
                w.add(self.make(k, per * qty, path))
        return w

    def _rows(self, part: str, w: Walk) -> list[dict[str, Any]]:
        rows = []
        for name in sorted(w.seconds):
            machines = 1 if name == HAND else max(self.on_map.get(name, 0), 1)
            rows.append(
                {
                    "part": part,
                    "map": name,
                    "mte": None if name == HAND else w.machine.get(name),
                    "crafts": w.crafts[name],
                    "machines": machines,
                    "h": w.seconds[name] / machines / 3600.0,
                }
            )
        return rows

    def route(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        """``eff_h``, ``build_h``, ``unlock_h`` and the breakdown for one route."""
        parts, notes = components(self.conn, plan)
        need = Need()
        for c in parts:
            need.add(self.make(c.key, c.qty))
        build = need.walk()
        unlocks: list[int] = []
        unlock = Walk()
        if not self.owned:
            notes.append(
                "My base lists no owned machines: each recipe map counts as one machine he "
                "has (plan 7.2's default), so unlock_h is 0"
            )
        else:
            covered = set(self.on_map)
            for maps in maps_of(self.conn, sorted({c.of for c in parts})).values():
                covered.update(maps)
            frontier = _uncovered(build, covered)
            for depth in range(1, UNLOCK_DEPTH + 1):
                if not frontier:
                    break
                item_of = machine_items(self.conn, [m for _, m in frontier])
                level_need = Need()
                for name, mte in frontier:
                    if name in covered:
                        continue
                    covered.add(name)
                    unlocks.append(mte)
                    if mte in item_of:
                        level_need.add(self.make(item_of[mte], 1))
                    else:
                        notes.append(f"unlock mte:{mte} has no item in the DB")
                level = level_need.walk()
                unlock.add(level)
                frontier = _uncovered(level, covered)
                if depth == UNLOCK_DEPTH and frontier:
                    notes.append(
                        "unlocks deeper than 3 levels are not counted: "
                        + ", ".join(f"mte:{m}" for _, m in frontier)
                    )
        missing = sorted(set(build.missing + unlock.missing))
        if missing:
            notes.append(
                f"not counted, no recipe or route from boundary items at the tier "
                f"({len(missing)}): " + ", ".join(missing)
            )
        deep = sorted(set(build.deep + unlock.deep) - set(missing))
        if deep:
            notes.append(
                f"not counted, imported through a gate more than {GATE_DEPTH} levels down "
                f"or in a loop ({len(deep)}): " + ", ".join(deep)
            )
        rows = self._rows("build", build) + self._rows("unlock", unlock)
        build_h = math.fsum(r["h"] for r in rows if r["part"] == "build")
        unlock_h = math.fsum(r["h"] for r in rows if r["part"] == "unlock")
        return {
            "eff_h": build_h + unlock_h,
            "build_h": build_h,
            "unlock_h": unlock_h,
            "craft_s": self.craft_s,
            "rows": rows,
            "components": [
                {"id": c.key, "qty": c.qty, "for": f"mte:{c.of}", "part": c.part}
                for c in parts
            ],
            "unlocks": [f"mte:{m}" for m in unlocks],
            "notes": list(dict.fromkeys(notes)),
        }


def _uncovered(w: Walk, covered: set[str]) -> list[tuple[str, int]]:
    """(map, mte) of the walk's machine steps on maps not yet covered, in map order."""
    return sorted((n, m) for n, m in w.machine.items() if n not in covered)
