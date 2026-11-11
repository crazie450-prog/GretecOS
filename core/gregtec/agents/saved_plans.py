"""Saved plans with no model call (plan 7.16 "plan views, BOMs ... of saved plans"; WO-2.8, D165).

Plans arrive with the Designer (WO-3.7). Until then this reads them the way WO-1.9's affected-plan
flags do (:mod:`gregtec.packupdate.plans`): a row in Appendix D's ``plan`` table whose
``json_path`` (``data/plans/<id>.json``, from GOS_HOME when relative) holds a ``gos-plan/0``
document. The one addition is an optional ``bom`` list, which the planner writes with the plan::

    "bom": [{"key": "gregtech:gt.metaitem.01@11305", "name": "Steel Ingot", "amount": 64,
             "unit": "items", "ref": "r:gt.recipe.blastfurnace/0123456789ab"}]

Every number on a BOM row is the planner's, copied as it is (plan 0.1 principle 1): this module
computes none, and the card cites the plan (``plan:<id>``) for any row without its own ``ref``.
WO-3.7's ``plan_get``/``plan_list`` tools take these cards over; the router's instant commands
("show plan X", "BOM for X", "my plans") call them now, so they work with the AI offline.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.cards.build import clip
from gregtec.db import access
from gregtec.gamestate.safe_open import read_bytes
from gregtec.packupdate import plans as plan_format

REF = re.compile(r"^(r|q|mte|crop|plan|w):\S+$")
BOM_MAX = 500  # the bom card's row limit
LIST_MAX = 50  # the table card's row limit


@dataclass(frozen=True)
class BomRow:
    key: str
    name: str
    amount: float
    unit: str
    ref: str | None


@dataclass
class SavedPlan:
    id: str
    name: str
    status: str
    kind: str | None
    run: str
    affected_run: int | None
    bom: list[BomRow] | None  # None: the plan's JSON has no BOM
    problems: list[str] = field(default_factory=list)  # rows left out, and why


def _bom(raw: object, problems: list[str]) -> list[BomRow] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        problems.append("its bom isn't a list, so it isn't shown")
        return None
    rows: list[BomRow] = []
    for i, r in enumerate(raw):
        ok = isinstance(r, dict)
        amount = r.get("amount") if isinstance(r, dict) else None
        ref = r.get("ref") if isinstance(r, dict) else None
        if not ok or not all(
            isinstance(r.get(k), str) and r.get(k) for k in ("key", "name", "unit")
        ):
            problems.append(f"bom row {i + 1} left out: it needs key, name and unit")
            continue
        if (
            isinstance(amount, bool)
            or not isinstance(amount, int | float)
            or not math.isfinite(amount)
            or amount < 0
        ):
            problems.append(f"bom row {i + 1} left out: its amount isn't a number")
            continue
        if ref is not None and not (isinstance(ref, str) and REF.match(ref)):
            problems.append(f"bom row {i + 1} left out: its ref isn't a citation id")
            continue
        rows.append(BomRow(r["key"], r["name"], amount, r["unit"], ref))
    return rows


def _read(home: Path, path: str, pid: str) -> tuple[plan_format.Plan, Any]:
    full = Path(path) if Path(path).is_absolute() else home / path
    data = read_bytes(full)
    if len(data) > plan_format.MAX_PLAN:
        raise ValueError(f"{full.name} is over {plan_format.MAX_PLAN >> 20} MiB")
    plan = plan_format.parse(data, full.name)
    if plan.id != pid:
        raise ValueError(f"{full.name} is plan {plan.id}")
    return plan, json.loads(data).get("bom")


def load(db: Path) -> tuple[list[SavedPlan], list[str]]:
    """Every plan in the ``plan`` table, newest first, and why any row didn't read. No DB, or a
    DB built before plans exist, has none."""
    if not db.is_file():
        return [], []
    home = db.parent.parent  # GOS_HOME/data/gos.sqlite
    conn = access.connect(db, readonly=True)
    try:
        try:
            rows = conn.execute(
                "SELECT p.id, p.name, p.kind, p.status, p.json_path, p.affected_run,"
                " e.run_key FROM plan AS p LEFT JOIN export_run AS e ON e.id = p.run_id"
                " ORDER BY p.created DESC, p.id"
            ).fetchall()
        except sqlite3.Error:
            return [], []
    finally:
        conn.close()
    out: list[SavedPlan] = []
    problems: list[str] = []
    for pid, name, kind, status, path, affected, run_key in rows:
        if not path:
            problems.append(f"plan {pid} has no json_path")
            continue
        try:
            plan, raw_bom = _read(home, str(path), str(pid))
        except (OSError, ValueError) as exc:
            problems.append(f"plan {pid} didn't read: {exc}")
            continue
        notes: list[str] = []
        out.append(
            SavedPlan(
                plan.id,
                str(name or plan.name),
                str(status or plan.status),
                kind,
                str(run_key or plan.run),
                affected,
                _bom(raw_bom, notes),
                notes,
            )
        )
    return out, problems


def find(found: list[SavedPlan], words: str) -> list[SavedPlan]:
    """The plans ``words`` name: an id (``plan:`` optional), else a name, exactly, else the one
    whose name holds the words ("the steel plan" is "steel"). Several: all of them (the caller
    lists them)."""
    said = words.strip().casefold().removeprefix("plan:").removesuffix(" plan")
    by_id = [p for p in found if p.id == said]
    if by_id:
        return by_id
    exact = [p for p in found if p.name.casefold() == said]
    if exact:
        return exact
    return [p for p in found if said and said in p.name.casefold()]


def _amount(n: float) -> float:
    return int(n) if float(n).is_integer() else n


def bom_card(plan: SavedPlan) -> dict[str, Any]:
    """The plan's BOM card (the catalog's ``bom``), its notes saying the plan's state."""
    notes = [f"status: {plan.status or 'none'}", f"solved on export run {plan.run}"]
    if plan.affected_run is not None:
        notes.append(
            f"affected by a pack update (export run id {plan.affected_run}): check it"
        )
    rows = plan.bom or []
    if plan.bom is None:
        notes.append("no BOM is saved with this plan")
    if len(rows) > BOM_MAX:
        notes.append(f"the first {BOM_MAX} of {len(rows)} rows")
    notes += plan.problems[: 20 - len(notes)]
    return {
        "type": "bom",
        "title": clip(f"{plan.name} · BOM", 300),
        "ref": clip(f"plan:{plan.id}", 200),
        "rows": [
            {
                "key": clip(r.key, 300),
                "name": clip(r.name, 300),
                "amount": _amount(r.amount),
                "unit": clip(r.unit, 30),
                "ref": r.ref,
            }
            for r in rows[:BOM_MAX]
        ],
        "notes": [clip(n, 2000) for n in notes[:20]],
    }


def list_card(found: list[SavedPlan], problems: list[str]) -> dict[str, Any]:
    """The saved plans as a table (the name WO-3.7's ``plan_list`` tool will have)."""
    rows = [
        [
            clip(p.name, 2000),
            f"plan:{p.id}",
            clip(p.kind or "", 2000),
            clip(p.status or "", 2000),
            "yes" if p.affected_run is not None else "",
            "" if p.bom is None else f"{len(p.bom)} rows",
        ]
        for p in found[:LIST_MAX]
    ]
    return {
        "type": "table",
        "tool": "plan_list",
        "error": None,
        "title": "Saved plans",
        "head": [],
        "columns": ["name", "plan", "kind", "status", "affected", "bom"],
        "rows": rows,
        "span": f"1-{len(rows)}" if rows else None,
        "total": len(found),
        "notes": [clip(p, 2000) for p in problems[:20]],
    }
