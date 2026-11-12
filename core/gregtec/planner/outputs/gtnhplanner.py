"""gtnhplanner.com project JSON, the primary planner export (WO-3.6, plan §7.1 step 9, D66, D202).

The format is GTNH Factory Flow's project file (https://github.com/jackwrichards/gtnh-factory-flow,
MIT): ``schemaVersion`` 1, with the recipes inside the file (``recipes[]``), so it needs no NEI ids
and no dataset; ``nodes[]`` (one per plan node: its recipe, machine count, parallel, overclock tier
and a position), ``storages[]`` (drawers: a source per boundary input, a product drawer for the
target, a byproduct drawer per byproduct) and ``edges[]`` (the plan's edges, and wires from and to
the drawers), ``fuelProfiles`` (none). It is written here from the format as the site's zod schema
(``src/lib/model/schemas.ts``) and its own exports describe it; no code of the site's is copied
(D202).

- Item ids are GOS's stable keys (``registry@meta``), which is also how the site's 2.9 datasets
  name items; a fluid is its Forge name (the key without ``fluid:``); an oredict slot keeps its
  ``ore:<name>`` key. So on import the site can match a recipe to its 2.9 dataset by content, and
  keeps the file's own recipe where it can't.
- A recipe is the DB's: its ``r:`` id, its map, base EU/t and duration, every slot with its
  amount, chance and whether it is used up; a programmed circuit is ``programmedCircuit``. The
  node carries GOS's machine count and parallel; GOS's sizing (machine, overclocks, EU/t per
  machine, ticks per craft, utilisation) rides along in the recipe's ``metadata.gos``, which the
  site keeps as written. The site does its own overclock math from the tier, so its rates are its
  own; the plan JSON stays the source of truth.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from gregtec.config import GT_PIN
from gregtec.planner.graph import FULL, Port
from gregtec.planner.gtmath.tiers import VN
from gregtec.planner.outputs.book import Book, Recipe
from gregtec.planner.outputs.diagram import flow, layers

SCHEMA_VERSION = 1
COL_W, ROW_H = 420, 260  # board units between columns and rows
SITE_TIERS = frozenset(VN) - {"MAX+"}  # the site's tiers (src/lib/model/tiers.ts)


def _kind(key: str) -> str:
    return "fluid" if key.startswith("fluid:") else "item"


def resource_id(key: str) -> str:
    """The site's id for a GOS key: a fluid's Forge name, else the key itself."""
    return key.removeprefix("fluid:")


def _tier(name: str | None) -> str:
    return name if name in SITE_TIERS else "LV"


def _amounts(ports: tuple[Port, ...], book: Book, side: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in ports:
        if p.amount <= 0 or p.circuit is not None:
            continue
        row: dict[str, Any] = {
            "kind": _kind(p.key),
            "id": resource_id(p.key),
            "displayName": book.name(p.key),
            "amount": p.amount,
        }
        if side == "in":
            if not p.consumed:
                row["consumed"] = False
            if p.chance_bp < FULL:
                row["consumeChance"] = p.chance_bp / FULL
        elif p.chance_bp < FULL:
            row["chance"] = p.chance_bp / FULL
        out.append(row)
    return out


def _recipe(n: Mapping[str, Any], rec: Recipe | None, book: Book) -> dict[str, Any]:
    m = n.get("machine")
    made = (rec.outputs[0].key if rec and rec.outputs else None) or (
        (n.get("for") or [None])[0]
    )
    machine = (m or {}).get("name") or (rec.map_name if rec else None) or "Crafting"
    tier = VN[rec.tier] if rec and 0 <= rec.tier < len(VN) else None
    circuits = [p.circuit for p in (rec.inputs if rec else ()) if p.circuit is not None]
    out: dict[str, Any] = {
        "id": n["recipe"],
        "name": f"{machine}: {book.name(made)}" if made else machine,
        "kind": "gregtech_machine" if rec and rec.map else "custom",
        "machineType": machine,
        "minimumTier": _tier(tier),
        "durationTicks": max(1, int(rec.duration or 1)) if rec else 1,
        "eut": max(0, int(rec.eut or 0)) if rec else 0,
        "inputs": _amounts(rec.inputs, book, "in") if rec else [],
        "outputs": _amounts(rec.outputs, book, "out") if rec else [],
        "source": {
            "exporter": "unknown",
            "rawRecipeId": n["recipe"],
            "sourceIdentifier": "gregtec-os",
            **({"recipeMap": rec.map_name or rec.map} if rec and rec.map else {}),
        },
        "metadata": {
            "recipeMapId": rec.map if rec else None,
            "gos": {
                "node": n["n"],
                "recipe": n["recipe"],
                "machine": m,
                "count": n.get("count"),
                "oc": n.get("oc"),
                "parallel": n.get("parallel"),
                "eut": n.get("eut"),
                "ticks_eff": n.get("ticks_eff"),
                "util": n.get("util"),
                "crafts_s": n.get("crafts_s"),
            },
        },
    }
    if circuits:
        out["programmedCircuit"] = str(circuits[0])
    if not out["outputs"] and made:
        # The site needs a positive amount; a recipe the DB has no slots for shows its product.
        out["outputs"] = [
            {"kind": _kind(made), "id": resource_id(made), "displayName": book.name(made), "amount": 1}
        ]  # fmt: skip
    return out


def project(plan: Mapping[str, Any], book: Book) -> dict[str, Any]:
    """The project JSON for ``plan`` (see the module docstring)."""
    target = plan["target"]
    f = flow(plan, book)
    place = layers([b.id for b in f.boxes], [(x.src, x.dst) for x in f.links])

    def position(bid: str) -> dict[str, int]:
        c, r = place[bid]
        return {"x": c * COL_W, "y": r * ROW_H}

    recipes: list[dict[str, Any]] = []
    seen: set[str] = set()
    nodes: list[dict[str, Any]] = []
    for n in plan["nodes"]:
        if n["recipe"] not in seen:
            seen.add(n["recipe"])
            recipes.append(_recipe(n, book.recipe(n["recipe"]), book))
        m = n.get("machine")
        node: dict[str, Any] = {
            "id": n["n"],
            "recipeId": n["recipe"],
            "machineCount": int(n.get("count") or 0),
            "parallel": max(1, int(n.get("parallel") or 1)),
            "overclockTier": _tier(m["tier"] if m else None),
            "enabled": True,
            "position": position(n["n"]),
        }
        if target["id"] in (n.get("for") or ()):
            node["targetOutput"] = {
                "kind": _kind(target["id"]),
                "resourceId": resource_id(target["id"]),
                "amountPerSecond": target["rate_s"],
                "displayName": book.name(target["id"]),
            }
        nodes.append(node)
    storages: list[dict[str, Any]] = []
    sid: dict[str, str] = {}
    for b in f.boxes:
        if b.kind == "node":
            continue
        key = b.id.split(":", 1)[1]
        sid[b.id] = f"{b.kind}-{len(storages) + 1}"
        store: dict[str, Any] = {
            "id": sid[b.id],
            "kind": _kind(key),
            "resourceId": resource_id(key),
            "displayName": b.label,
            "position": position(b.id),
        }
        if b.kind != "in":
            store["drainMode"] = "product" if b.kind == "target" else "byproduct"
        storages.append(store)
    edges: list[dict[str, Any]] = []
    for i, x in enumerate(f.links, 1):
        edge: dict[str, Any] = {
            "id": f"e{i}",
            "source": sid.get(x.src, x.src),
            "target": sid.get(x.dst, x.dst),
            "resourceKind": _kind(x.item),
            "resourceId": resource_id(x.item),
            "label": x.label,
        }
        if x.rate_s is not None and x.rate_s > 0:
            edge["ratePerSecond"] = x.rate_s
        edges.append(edge)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": f"gos-{plan['id']}",
        "name": plan["name"],
        "description": (
            f"Gregtec OS plan:{plan['id']} (export run {plan['run']}, GT {plan.get('gt', GT_PIN)}). "
            "Machine counts and parallels are GOS's; the plan JSON is the source of truth."
        ),
        "targetRate": {
            "kind": _kind(target["id"]),
            "resourceId": resource_id(target["id"]),
            "amountPerSecond": target["rate_s"],
            "displayName": book.name(target["id"]),
        },
        "recipes": recipes,
        "nodes": nodes,
        "storages": storages,
        "edges": edges,
        "fuelProfiles": [],
        "notes": f"plan:{plan['id']} · {plan['name']}",
        "metadata": {"source": f"Gregtec OS plan:{plan['id']}"},
    }


def text(plan: Mapping[str, Any], book: Book) -> str:
    """The project as the file's text (stable: sorted keys, two-space indent)."""
    return (
        json.dumps(project(plan, book), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )
