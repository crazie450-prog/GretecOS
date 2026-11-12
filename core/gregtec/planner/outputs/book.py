"""What the outputs read from the DB about one plan: names and each node's recipe (WO-3.6).

A plan names items and fluids by stable key (``registry@meta``, ``fluid:<name>``, ``ore:<name>``)
and recipes by ``r:`` ref; the exports need the names and, for gtnhplanner.com and gtnh-flow, each
recipe's own slots, EU/t and duration. :func:`read` reads them from the export run the plan was
solved on (its ``run`` key) with WO-3.3's batch reader, so the slots are the ones the planner
solved with. A key the run doesn't have keeps the key as its name.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from gregtec.planner import graph
from gregtec.planner.graph import EU_KEY, Port


@dataclass(frozen=True)
class Recipe:
    ref: str
    map: str | None  # the recipe map's unlocalised name (gt.recipe.blastfurnace)
    map_name: str | None  # its English name (Blast Furnace)
    source: str
    eut: int | None
    duration: int | None  # ticks
    tier: int  # the recipe's GT voltage tier
    inputs: tuple[Port, ...]  # the EU port left out
    outputs: tuple[Port, ...]


@dataclass(frozen=True)
class Book:
    names: Mapping[str, str] = field(default_factory=dict)
    recipes: Mapping[str, Recipe] = field(default_factory=dict)

    def name(self, key: str) -> str:
        found = self.names.get(key)
        if found:
            return found
        if key.startswith("ore:"):
            return f"{key[4:]} (oredict)"
        return key

    def recipe(self, ref: str) -> Recipe | None:
        return self.recipes.get(ref)


def plan_keys(plan: Mapping[str, Any]) -> list[str]:
    """Every item or fluid key the plan itself names, in order, each once."""
    out: list[str] = [plan["target"]["id"]]
    for e in plan.get("edges") or ():
        out.append(e["item"])
    for side in ("in", "out"):
        for p in (plan.get("boundary") or {}).get(side) or ():
            out.append(p["id"])
    for n in plan.get("nodes") or ():
        out.extend(n.get("for") or ())
    return list(dict.fromkeys(k for k in out if k != EU_KEY))


def run_id(conn: sqlite3.Connection, run_key: str) -> int:
    row = conn.execute(
        "SELECT id FROM export_run WHERE run_key = ?", (run_key,)
    ).fetchone()
    if row is None:
        raise LookupError(f"export run {run_key} is not in the DB")
    return int(row[0])


def read(conn: sqlite3.Connection, plan: Mapping[str, Any]) -> Book:
    """The names and recipes of ``plan`` from the export run it was solved on."""
    rid = run_id(conn, str(plan["run"]))
    refs = [n["recipe"] for n in plan.get("nodes") or ()]
    reader = graph._Reader(conn, rid)
    ids: dict[int, str] = {}
    for r in conn.execute(
        "SELECT r.id, r.ref FROM json_each(?) AS j JOIN recipe AS r"
        " ON r.run_id = ? AND r.ref = j.value",
        (json.dumps(refs), rid),
    ):
        ids[int(r[0])] = str(r[1])
    reader.load_recipes(ids)
    map_names = {
        str(r[0]): str(r[1])
        for r in conn.execute(
            "SELECT unloc_name, name_en FROM recipe_map WHERE name_en IS NOT NULL"
        )
    }
    recipes: dict[str, Recipe] = {}
    keys: list[str] = plan_keys(plan)
    for rid_, ref in ids.items():
        rec = reader.recipes[rid_]
        ins = tuple(p for _, p, _ in sorted(rec.inputs, key=lambda t: t[0]))
        recipes[ref] = Recipe(
            ref=ref,
            map=rec.map,
            map_name=map_names.get(rec.map or ""),
            source=rec.source,
            eut=rec.eut,
            duration=rec.duration,
            tier=rec.tier,
            inputs=tuple(p for p in ins if p.key != EU_KEY),
            outputs=tuple(rec.outputs),
        )
        keys.extend(p.key for p in (*ins, *rec.outputs))
    return Book(_names(reader, keys), recipes)


def _names(reader: graph._Reader, keys: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in dict.fromkeys(keys):
        if k == EU_KEY or k.startswith("ore:"):
            continue
        info = reader.info(k)
        if info is not None and info.name:
            out[k] = info.name
    return out
