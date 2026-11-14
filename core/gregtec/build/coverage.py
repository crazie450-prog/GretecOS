"""Footprint coverage: which multis Clayton needs have a footprint, which need a Lab capture
(WO-10.2, plan 7.10, plan 8 row 10.2, D306).

A multi is *needed* when it is in an accepted plan (a ``plan`` row ``accepted``, ``building`` or
``running``, as the EU ledger counts them, D238: its production nodes and a power plan's
generators), or in My base (WO-3.8). The machine catalog (``machine`` rows with
``is_multiblock``, WO-1.3/1.7) is counted too, so the report also says how much of the pack is
covered. Each multi is looked up in the footprint library (:mod:`gregtec.build.footprints`) by its
controller, ``gregtech:gt.blockmachines`` + its MTE id, or the ``machine`` row a structure row is
linked to; never a numeric block id.

Statuses: ``footprint`` (the extractor's rows), ``capture`` (a Lab capture), ``needs capture``
(neither: a Lab capture is Clayton's PC step, L1). An extractor footprint the extractor noted a
caveat on (a variant family cut off at its sweep ceiling) says so. Read-only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.base import state as base_state
from gregtec.build import footprints as fp
from gregtec.planner.ledger import COUNTED
from gregtec.planner.outputs import store

FOOTPRINT = "footprint"
CAPTURE = "capture"
NEEDS = "needs capture"


@dataclass
class Need:
    mte: int
    name: str | None
    needed_by: list[str] = field(default_factory=list)  # plan:<id>, base, catalog
    status: str = NEEDS
    variants: int = 0
    lineage: str | None = None
    family: str | None = None
    caveats: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "mte": self.mte,
            "name": self.name,
            "needed_by": self.needed_by,
            "status": self.status,
            "variants": self.variants,
            "lineage": self.lineage,
            "family": self.family,
            "caveats": list(self.caveats),
        }


@dataclass(frozen=True)
class Coverage:
    needed: tuple[Need, ...]  # in an accepted plan or My base, by MTE id
    catalog: tuple[Need, ...]  # every multiblock in the machine catalog, by MTE id
    plans: tuple[str, ...]  # the plans read
    unreadable: tuple[str, ...] = ()  # accepted plans whose JSON didn't load

    def counts(self, which: Iterable[Need]) -> dict[str, int]:
        out = {FOOTPRINT: 0, CAPTURE: 0, NEEDS: 0}
        for n in which:
            out[n.status] += 1
        return out

    def to_json(self) -> dict[str, Any]:
        return {
            "plans": list(self.plans),
            "unreadable": list(self.unreadable),
            "needed": [n.to_json() for n in self.needed],
            "needed_counts": self.counts(self.needed),
            "catalog_counts": self.counts(self.catalog),
            "catalog_needs_capture": [n.mte for n in self.catalog if n.status == NEEDS],
        }


def plan_multis(plan: Mapping[str, Any]) -> dict[int, str | None]:
    """MTE id -> name of every multiblock a plan uses: its nodes' machines and a power plan's
    ``machines_used`` (generators)."""
    out: dict[int, str | None] = {}
    entries = [*plan.get("nodes", []), *plan.get("machines_used", [])]
    for n in entries:
        m = n.get("machine") if isinstance(n, Mapping) else None
        if (
            isinstance(m, Mapping)
            and m.get("kind") == "multiblock"
            and m.get("mte") is not None
        ):
            out.setdefault(int(m["mte"]), m.get("name"))
    return out


def accepted_plans(
    home: Path, conn: sqlite3.Connection
) -> tuple[list[dict[str, Any]], list[str]]:
    """The plans the ledger counts (accepted, building, running), loaded from their JSON, and
    the ids whose JSON didn't load."""
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "plan" not in tables:
        return [], []
    marks = ",".join("?" * len(COUNTED))
    ids = [
        str(r[0])
        for r in conn.execute(
            f"SELECT id FROM plan WHERE status IN ({marks}) ORDER BY id", COUNTED
        )
    ]
    plans, bad = [], []
    for pid in ids:
        try:
            plans.append(store.load(home, pid))
        except (OSError, ValueError):
            bad.append(pid)
    return plans, bad


def _status(
    lib: fp.Library, mte: int, linked: Mapping[int, list[fp.Footprint]]
) -> tuple[str, list[fp.Footprint]]:
    found = lib.for_mte(mte) or linked.get(mte, [])
    if not found:
        return NEEDS, []
    return (FOOTPRINT if found[0].source == fp.EXTRACTOR else CAPTURE), found


def _linked(conn: sqlite3.Connection, lib: fp.Library) -> dict[int, list[fp.Footprint]]:
    """MTE id -> footprints whose structure row is linked to that machine by ``machine_id``."""
    ids = {f.machine_id for f in lib.footprints if f.machine_id is not None}
    if not ids or not base_state.has_table(conn, "machine"):
        return {}
    mte_of = {
        int(r[0]): int(r[1])
        for r in conn.execute(
            f"SELECT id, mte_id FROM machine WHERE id IN ({','.join('?' * len(ids))})",
            sorted(ids),
        )
        if r[1] is not None
    }
    out: dict[int, list[fp.Footprint]] = {}
    for f in lib.footprints:
        if f.machine_id in mte_of:
            out.setdefault(mte_of[f.machine_id], []).append(f)
    return out


def _fill(n: Need, status: str, found: list[fp.Footprint]) -> Need:
    n.status = status
    n.variants = len(found)
    if found:
        n.lineage = found[0].lineage
        n.family = found[0].family
        n.caveats = found[0].caveats
        n.name = n.name or found[0].name
    return n


def coverage(
    conn: sqlite3.Connection,
    plans: Iterable[Mapping[str, Any]],
    *,
    unreadable: Iterable[str] = (),
    with_base: bool = True,
) -> Coverage:
    """The coverage of ``plans`` (and My base, with ``with_base``) and of the machine catalog."""
    lib = fp.library(conn)
    linked = _linked(conn, lib)
    names: dict[int, str | None] = {}
    catalog_ids: list[int] = []
    if base_state.has_table(conn, "machine"):
        for mte, name, multi in conn.execute(
            "SELECT mte_id, name_en, is_multiblock FROM machine WHERE mte_id IS NOT NULL"
        ):
            names[int(mte)] = name
            if multi:
                catalog_ids.append(int(mte))
    needed: dict[int, Need] = {}
    read: list[str] = []
    for p in plans:
        pid = str(p.get("id", "?"))
        read.append(pid)
        for mte, name in plan_multis(p).items():
            need = needed.setdefault(mte, Need(mte, names.get(mte) or name))
            need.needed_by.append(f"plan:{pid}")
    if with_base:
        for m in base_state.machines(conn):
            if m.count > 0 and m.multiblock:
                need = needed.setdefault(m.mte_id, Need(m.mte_id, m.name))
                need.needed_by.append("base")
    for n in needed.values():
        _fill(n, *_status(lib, n.mte, linked))
    catalog = [
        _fill(Need(mte, names.get(mte), ["catalog"]), *_status(lib, mte, linked))
        for mte in sorted(set(catalog_ids))
    ]
    return Coverage(
        tuple(sorted(needed.values(), key=lambda n: n.mte)),
        tuple(catalog),
        tuple(read),
        tuple(unreadable),
    )


def render(cov: Coverage, *, all_catalog: bool = False) -> list[str]:
    """The report as text lines (``--json`` gives :meth:`Coverage.to_json`)."""
    nc = cov.counts(cov.needed)
    cc = cov.counts(cov.catalog)
    out = [
        f"Footprint coverage: {len(cov.plans)} accepted plan(s) read"
        + (
            f", {len(cov.unreadable)} unreadable: {', '.join(cov.unreadable)}"
            if cov.unreadable
            else ""
        ),
        (
            f"  needed multis: {len(cov.needed)} ({nc[FOOTPRINT]} footprint, "
            f"{nc[CAPTURE]} capture, {nc[NEEDS]} need a Lab capture)"
        ),
    ]
    for n in cov.needed:
        label = n.name or "?"
        extra = []
        if n.lineage == fp.LEGACY:
            extra.append(f"legacy {n.family or ''}".strip())
        if n.caveats:
            extra.append("caveat: " + "; ".join(n.caveats))
        note = f" ({', '.join(extra)})" if extra else ""
        variants = f", {n.variants} variant(s)" if n.variants else ""
        out.append(
            f"    mte:{n.mte}\t{label}\t{n.status}{variants}{note}\t{', '.join(n.needed_by)}"
        )
    out.append(
        f"  machine catalog multis: {len(cov.catalog)} ({cc[FOOTPRINT]} footprint, "
        f"{cc[CAPTURE]} capture, {cc[NEEDS]} need a Lab capture)"
    )
    if all_catalog:
        for n in cov.catalog:
            if n.status == NEEDS:
                out.append(f"    mte:{n.mte}\t{n.name or '?'}\t{NEEDS}")
    return out


def dumps(cov: Coverage) -> str:
    return json.dumps(cov.to_json(), indent=1, ensure_ascii=False)
