"""Route metrics (WO-3.5, plan §7.1 step 7, §7.2's effort model, D14, D17, D62, D196, D197).

:func:`route` measures one plan (a route from :mod:`.plan`) into its ``metrics`` object. Every
number is the plan's own (the DB's recipes and GT's math on them, WO-3.4) or a sum of them, the
curated scarcity table's weight times one, or the effort model's (:mod:`.effort`):

- ``machines`` (total) and ``by_type``: per machine (``mte``), its name, kind, tier, count, addon
  flag, ``machine_fact`` confidence and whether it is new to him;
- ``new_types``: the unlock list, the route's machines My base doesn't list as owned
  (``mte:<id>``, sorted);
- ``eu``: per tier, average and peak EU/t and amps (``count x amps`` per machine), and the
  totals ``eut_avg``, ``eut_peak``; ``tier`` is the highest machine tier;
- ``raw_in_h`` and ``byproducts_h``: the boundary's inputs and outputs per hour (EU and the
  target left out);
- ``scarcity`` (D62): sum over the inputs of per hour x GT material units x the material's weight
  in ``data/curated/scarcity.tsv`` (``scarce`` lists the weighted inputs);
- ``footprint``: None until WO-3.6;
- ``eff_h``, ``build_h``, ``unlock_h`` and ``effort`` (the breakdown table, :mod:`.effort`);
- ``complexity``: ``nodes``, ``fluids`` (distinct fluids the route moves), ``loops`` (strongly
  connected sets in the route's own item-recipe graph) and ``score`` (their sum);
- in batch mode, ``batch``: the quantity, window, ETA (``eta_s``, ``eta_h``), the batch's inputs
  and byproducts, total crafts and total EU (each the rate times the window, D14).
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec.planner import chains, graph
from gregtec.planner.graph import EU_KEY, Edge, Graph
from gregtec.planner.gtmath.tiers import VN
from gregtec.planner.request import is_stable_key

SCARCITY = chains.REPO_ROOT / "data" / "curated" / "scarcity.tsv"
SCARCITY_HEADER = ["key", "weight", "source", "rationale"]
MATERIAL = "material:"
UNIT = 3628800  # GT's material amount of one unit (a dust or an ingot)
TIERS = {name: i for i, name in enumerate(VN)}


@dataclass(frozen=True)
class Weight:
    key: str  # a stable key, or material:<GT material name>
    weight: float
    source: str
    rationale: str


def load_scarcity(path: Path = SCARCITY) -> dict[str, Weight]:
    """``data/curated/scarcity.tsv``: key -> its weight, checked."""
    out: dict[str, Weight] = {}
    header = False
    for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not text.strip() or text.startswith("#"):
            continue
        cells = text.split("\t")
        if not header:
            if cells != SCARCITY_HEADER:
                raise ValueError(f"{path.name}:{n}: header must be {SCARCITY_HEADER}")
            header = True
            continue
        if len(cells) != len(SCARCITY_HEADER) or not all(c.strip() for c in cells):
            raise ValueError(f"{path.name}:{n}: four non-empty cells")
        key, weight, source, why = cells
        if not (is_stable_key(key) or (key.startswith(MATERIAL) and len(key) > 9)):
            raise ValueError(
                f"{path.name}:{n}: {key!r} is not a stable key or material:<name>"
            )
        try:
            w = float(weight)
        except ValueError:
            raise ValueError(f"{path.name}:{n}: weight {weight!r} is not a number") from None
        if not math.isfinite(w) or not 0 <= w <= 1:
            raise ValueError(f"{path.name}:{n}: weight {w} must be between 0 and 1")
        if key in out:
            raise ValueError(f"{path.name}:{n}: {key} listed twice")
        out[key] = Weight(key, w, source, why)
    return out


def materials(conn: sqlite3.Connection, keys: list[str]) -> dict[str, tuple[str, float]]:
    """Item key -> (GT material, units per item) for the items GT tags with a material."""
    out: dict[str, tuple[str, float]] = {}
    for key in keys:
        if key.startswith("fluid:") or not is_stable_key(key):
            continue
        reg, _, rest = key.partition("@")
        meta = rest.split("#", 1)[0]
        row = conn.execute(
            "SELECT gt_material, gt_material_amount FROM item WHERE registry_name = ?"
            " AND meta = ? AND gt_material IS NOT NULL ORDER BY nbt_hash LIMIT 1",
            (reg, int(meta)),
        ).fetchone()
        if row is not None and row[0]:
            amount = row[1] if row[1] and row[1] > 0 else UNIT  # GT writes -1 for none
            out[key] = (str(row[0]), float(amount) / UNIT)
    return out


def _per_h(ports: list[dict[str, Any]], skip: set[str]) -> list[dict[str, Any]]:
    return [
        {"id": p["id"], "per_h": p["rate_s"] * 3600.0}
        for p in sorted(ports, key=lambda p: p["id"])
        if p["id"] not in skip
    ]


def scarcity(
    inputs: list[dict[str, Any]],
    table: Mapping[str, Weight],
    mats: Mapping[str, tuple[str, float]],
) -> tuple[float, list[dict[str, Any]]]:
    """(the scarcity score, the weighted inputs): per hour x units x weight, an explicit key
    before its material; fluids by key only, per 1000 L."""
    rows: list[dict[str, Any]] = []
    for p in inputs:
        k = p["id"]
        units, w = 1.0, table.get(k)
        if k.startswith("fluid:"):
            units = 1 / 1000
        elif w is None and k in mats:
            name, units = mats[k]
            w = table.get(MATERIAL + name)
        if w is None or w.weight <= 0:
            continue
        rows.append(
            {
                "id": k,
                "per_h": p["per_h"],
                "weight": w.weight,
                "by": w.key,
                "score": p["per_h"] * units * w.weight,
            }
        )
    return math.fsum(r["score"] for r in rows), rows


def complexity(g: Graph, plan: Mapping[str, Any]) -> dict[str, int]:
    """Nodes, fluids moved and loops of the route's own item-recipe graph."""
    edges: list[Edge] = []
    fluids: set[str] = set()
    for n in plan["nodes"]:
        rec = g.recipe(n["recipe"])
        for p in rec.inputs:
            if p.key != EU_KEY and p.consumed:
                edges.append(Edge(p.key, rec.id, "in", float(p.amount)))
        for p in rec.outputs:
            if p.expected > 0:
                edges.append(Edge(rec.id, p.key, "out", p.expected))
    for e in plan["edges"]:
        if e["item"].startswith("fluid:"):
            fluids.add(e["item"])
    for side in ("in", "out"):
        for p in plan["boundary"][side]:
            if p["id"].startswith("fluid:"):
                fluids.add(p["id"])
    if plan["target"]["id"].startswith("fluid:"):
        fluids.add(plan["target"]["id"])
    loops = len(graph.cycles(edges))
    nodes = len(plan["nodes"])
    return {
        "nodes": nodes,
        "fluids": len(fluids),
        "loops": loops,
        "score": nodes + len(fluids) + loops,
    }


def _machines(
    plan: Mapping[str, Any], owned: Mapping[int, int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    types: dict[int, dict[str, Any]] = {}
    tiers: dict[str, dict[str, Any]] = {}
    for n in plan["nodes"]:
        m = n.get("machine")
        if not m or not n.get("count"):
            continue
        t = types.setdefault(
            m["mte"],
            {
                "mte": m["mte"],
                "name": m["name"],
                "kind": m["kind"],
                "tier": m["tier"],
                "count": 0,
                "addon": m["addon"],
                "confidence": m["confidence"],
                "new": owned.get(m["mte"], 0) <= 0,
            },
        )
        t["count"] += n["count"]
        t["confidence"] = min(t["confidence"], m["confidence"])
        e = tiers.setdefault(
            m["tier"],
            {"tier": m["tier"], "eut_avg": 0.0, "eut_peak": 0, "amps": 0},
        )
        e["eut_avg"] += n["eut_avg"]
        e["eut_peak"] += n["eut_peak"]
        e["amps"] += n["count"] * n["amps"]
    by_type = sorted(types.values(), key=lambda t: (TIERS[t["tier"]], t["mte"]))
    eu = sorted(tiers.values(), key=lambda e: TIERS[e["tier"]])
    return by_type, eu


def route(
    conn: sqlite3.Connection,
    g: Graph,
    plan: Mapping[str, Any],
    owned: Mapping[int, int],
    table: Mapping[str, Weight],
    effort: Mapping[str, Any],
) -> dict[str, Any]:
    """The ``metrics`` object of one plan (see the module docstring)."""
    by_type, eu = _machines(plan, owned)
    target = plan["target"]["id"]
    raw = _per_h(plan["boundary"]["in"], {EU_KEY, target})
    byproducts = _per_h(plan["boundary"]["out"], {EU_KEY, target})
    names = {i.id: i.name for i in g.items}
    for p in raw + byproducts:
        p["name"] = names.get(p["id"], p["id"])
    mats = materials(conn, [p["id"] for p in raw])
    score, scarce = scarcity(raw, table, mats)
    out: dict[str, Any] = {
        "machines": sum(t["count"] for t in by_type),
        "by_type": by_type,
        "new_types": [f"mte:{t['mte']}" for t in sorted(by_type, key=lambda t: t["mte"])
                      if t["new"]],  # fmt: skip
        "eu": eu,
        "eut_avg": math.fsum(e["eut_avg"] for e in eu),
        "eut_peak": sum(e["eut_peak"] for e in eu),
        "tier": max((t["tier"] for t in by_type), key=TIERS.__getitem__)
        if by_type
        else None,
        "raw_in_h": raw,
        "byproducts_h": byproducts,
        "scarcity": score,
        "scarce": scarce,
        "footprint": None,
        "eff_h": effort["eff_h"],
        "build_h": effort["build_h"],
        "unlock_h": effort["unlock_h"],
        "effort": {k: effort[k] for k in ("craft_s", "rows", "components", "unlocks",
                                          "notes")},  # fmt: skip
        "complexity": complexity(g, plan),
    }
    window = plan["target"].get("window_s")
    if window is not None:
        out["batch"] = batch(plan, window)
    return out


def batch(plan: Mapping[str, Any], window: float) -> dict[str, Any]:
    """Batch totals (D14): every total is the plan's rate times the window."""
    t = plan["target"]
    target = t["id"]
    return {
        "qty": t["qty"],
        "window_s": window,
        "eta_s": t["eta_s"],
        "eta_h": t["eta_s"] / 3600.0,
        "inputs": [
            {"id": p["id"], "qty": p["rate_s"] * window}
            for p in sorted(plan["boundary"]["in"], key=lambda p: p["id"])
            if p["id"] not in (EU_KEY, target)
        ],
        "byproducts": [
            {"id": p["id"], "qty": p["rate_s"] * window}
            for p in sorted(plan["boundary"]["out"], key=lambda p: p["id"])
            if p["id"] not in (EU_KEY, target)
        ],
        "crafts": math.fsum(n["crafts_s"] * window for n in plan["nodes"]),
        "eu": math.fsum((n["eut_avg"] or 0) * 20 * window for n in plan["nodes"]),
    }
