"""My base's state in ``GOS_HOME/data/gos.sqlite`` (WO-3.8, D63, D64, D210, D211).

- **Tier** (D64): WO-1.5's quest tier detection and its override, as they are
  (:func:`gregtec.db.quests.effective_tier`, ``kv`` ``tier.detected`` and ``tier.override``):
  My base shows the detected stage next to the override, sets the override and clears it.
- **Owned machines**: ``my_base_machine`` (one of the core's tables, :mod:`gregtec.store`), a
  count per GT MetaTileEntity id and per ``source``: ``manual`` (ticked in the dashboard),
  ``event`` (placement events) and ``census`` (the tile-entity census); WO-6.2 adds those rows
  without a schema change. The count in use is the manual row's when there is one (a manual 0
  says "none", whatever the automatic sources counted), else the census's, else the events'
  (D211).
- **Stock** (D63): ``my_base_stock``, the quantity on hand per stable item or fluid key (items
  counted, fluids in L); nothing is stocked until Clayton enters it.

Both tables are keyed on stable keys, never on the game DB's surrogate ``item.id``,
``fluid.id`` or ``machine.id`` (D210): ``gos db build`` writes into this same file and keeps
those ids today, but they are not stable by contract (a DB built from scratch numbers them
again), and CLAUDE.md never keys on numeric ids. A key is checked against the catalog when it is
written and stored in the form the catalog gives it, so the designer meets the same key in the
recipe graph. A key a later pack update drops stays listed, marked as not in the current DB.

:class:`MyBase` is what the designer reads: WO-3.3's ``BaseInputs`` interface
(``gregtec.planner.request``), implemented structurally with exactly its four methods. Accepted
lines have no source yet (plans are accepted from WO-3.7, the EU ledger is WO-4.4), so
:meth:`MyBase.line_outputs` is empty until one exists (D211).

Edits come from Clayton in the dashboard (:mod:`gregtec.base.routes`), never from an agent
(plan 7.0: ``base_set`` is UI-only, D212). Every write is one ``BEGIN IMMEDIATE`` transaction on
an autocommit connection, as :mod:`gregtec.db.quests` writes the tier override.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.ingest import quests as bq

MACHINE_TABLE = "my_base_machine"
STOCK_TABLE = "my_base_stock"
SOURCES = ("manual", "event", "census")
PRECEDENCE = ("manual", "census", "event")  # which source's count is used (D211)
MAX_COUNT = 1_000_000
MAX_QTY = 1e15
MAX_KEY = 300  # characters: the tool layer's keys are far shorter


class BaseError(ValueError):
    """An edit My base refuses; the message says why."""


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


@contextmanager
def _write(conn: sqlite3.Connection) -> Iterator[None]:
    for table in (MACHINE_TABLE, STOCK_TABLE):
        if not has_table(conn, table):
            raise BaseError(
                f"{table} doesn't exist: open the DB with gregtec.store.connect (the core's)"
            )
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# --- owned machines ------------------------------------------------------------------------


@dataclass(frozen=True)
class Machine:
    """One machine My base lists: the count in use, the source it came from and every source's
    own count; ``name`` and ``tier`` from the catalog (None when the DB has no such machine)."""

    mte_id: int
    count: int
    source: str
    sources: dict[str, int]
    upd: int
    name: str | None = None
    tier: int | None = None
    multiblock: bool | None = None


def machines(conn: sqlite3.Connection) -> list[Machine]:
    """Every machine with a row, by mte id; an empty list before the core made the table."""
    if not has_table(conn, MACHINE_TABLE):
        return []
    by_id: dict[int, dict[str, tuple[int, int]]] = {}
    for mte_id, source, count, upd in conn.execute(
        f"SELECT mte_id, source, count, upd FROM {MACHINE_TABLE} ORDER BY mte_id"
    ):
        by_id.setdefault(int(mte_id), {})[str(source)] = (int(count), int(upd))
    catalog = _machine_names(conn, list(by_id))
    out = []
    for mte_id, rows in by_id.items():
        source = next(s for s in PRECEDENCE if s in rows)
        name, tier, multi = catalog.get(mte_id, (None, None, None))
        out.append(
            Machine(
                mte_id,
                rows[source][0],
                source,
                {s: rows[s][0] for s in SOURCES if s in rows},
                max(u for _, u in rows.values()),
                name,
                tier,
                multi,
            )
        )
    return out


def _machine_names(
    conn: sqlite3.Connection, ids: list[int]
) -> dict[int, tuple[str | None, int | None, bool | None]]:
    if not ids or not has_table(conn, "machine"):
        return {}
    marks = ",".join("?" * len(ids))
    return {
        int(r[0]): (r[1], r[2], None if r[3] is None else bool(r[3]))
        for r in conn.execute(
            f"SELECT mte_id, name_en, tier, is_multiblock FROM machine WHERE mte_id IN ({marks})",
            ids,
        )
    }


def owned_machines(conn: sqlite3.Connection) -> dict[int, int]:
    """GT mte id -> the count in use, for the machines he has at least one of."""
    return {m.mte_id: m.count for m in machines(conn) if m.count > 0}


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BaseError("count must be a whole number")
    if not 0 <= value <= MAX_COUNT:
        raise BaseError(f"count must be 0 to {MAX_COUNT}")
    return value


def _mte(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 10**9:
        raise BaseError("mte_id must be a GT MetaTileEntity id (a whole number)")
    return value


def set_machine(
    conn: sqlite3.Connection,
    mte_id: int,
    count: int,
    source: str = "manual",
    now: int | None = None,
) -> Machine:
    """Set ``source``'s count of machine ``mte_id`` (a manual 0 means "none"). The machine has
    to be in the catalog (``gos db build``)."""
    mte_id, count = _mte(mte_id), _count(count)
    if source not in SOURCES:
        raise BaseError(f"source must be one of {', '.join(SOURCES)}")
    if not has_table(conn, "machine"):
        raise BaseError("no recipe DB yet: run `gos db build` first")
    if mte_id not in _machine_names(conn, [mte_id]):
        raise BaseError(f"mte:{mte_id} isn't a machine in the current DB")
    with _write(conn):
        conn.execute(
            f"INSERT INTO {MACHINE_TABLE}(mte_id, source, count, upd) VALUES (?,?,?,?)"
            " ON CONFLICT(mte_id, source) DO UPDATE SET count = excluded.count,"
            " upd = excluded.upd",
            (mte_id, source, count, int(time.time()) if now is None else now),
        )
    return next(m for m in machines(conn) if m.mte_id == mte_id)


def clear_machine(
    conn: sqlite3.Connection, mte_id: int, source: str = "manual"
) -> bool:
    """Drop ``source``'s row for ``mte_id`` (the other sources' counts apply again). False when
    there was none."""
    mte_id = _mte(mte_id)
    with _write(conn):
        gone = conn.execute(
            f"DELETE FROM {MACHINE_TABLE} WHERE mte_id = ? AND source = ?",
            (mte_id, source),
        ).rowcount
    return gone > 0


# --- stock ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stocked:
    key: str
    qty: float
    upd: int
    kind: str  # item or fluid
    name: str | None = None  # None: not in the current DB

    @property
    def unit(self) -> str:
        return "L" if self.kind == "fluid" else "items"


def _kind(key: str) -> str:
    return "fluid" if key.startswith("fluid:") else "item"


def _item_name(conn: sqlite3.Connection, key: str) -> list[tuple[str, str, str]]:
    """``key``'s catalog entries as (kind, key, name): the tool layer's lookup by key
    (named queries ``fluid_by_name`` and ``item_by_key``)."""
    from gregtec.agents.tools.items import FLUID_KEY, ITEM_KEY  # the tool layer's key forms

    if not has_table(conn, "item"):
        return []
    found: list[tuple[str, str, str]] = []
    if m := FLUID_KEY.fullmatch(key):
        rows = access.query(conn, "fluid_by_name", {"name": m["name"]})
        found += [("fluid", r["key"], r["name"]) for r in rows]
    if m := ITEM_KEY.fullmatch(key):
        rows = access.query(
            conn,
            "item_by_key",
            {"registry": m["reg"], "meta": int(m["meta"]), "nbt": m["nbt"] or ""},
        )
        found += [("item", r["key"], r["name"]) for r in rows]
    return found


def stock(conn: sqlite3.Connection) -> list[Stocked]:
    """Everything stocked, by key; an empty list before the core made the table."""
    if not has_table(conn, STOCK_TABLE):
        return []
    out = []
    for key, qty, upd in conn.execute(
        f"SELECT key, qty, upd FROM {STOCK_TABLE} ORDER BY key"
    ):
        named = _item_name(conn, key)
        out.append(
            Stocked(
                key,
                float(qty),
                int(upd),
                _kind(key),
                named[0][2] if len(named) == 1 else None,
            )
        )
    return out


def _qty(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BaseError("qty must be a number")
    qty = float(value)
    if not 0 < qty <= MAX_QTY:  # also refuses NaN
        raise BaseError(f"qty must be above 0 and at most {MAX_QTY:g}")
    return qty


def set_stock(
    conn: sqlite3.Connection, key: str, qty: float, now: int | None = None
) -> Stocked:
    """Set the quantity on hand of the item or fluid ``key`` names (items counted, fluids in L).
    The key has to name exactly one item or fluid of the catalog; it is stored as the catalog
    gives it."""
    qty = _qty(qty)
    if not isinstance(key, str) or not key or len(key) > MAX_KEY:
        raise BaseError("key must be an item or fluid key")
    if not has_table(conn, "item"):
        raise BaseError("no recipe DB yet: run `gos db build` first")
    named = _item_name(conn, key)
    if not named:
        raise BaseError(
            f"{key!r} isn't an item or fluid key in the current DB "
            "(registry@meta, registry@meta#<nbt>, or fluid:<name>)"
        )
    if len(named) > 1:
        raise BaseError(
            f"{key!r} names {len(named)} items or fluids: "
            + ", ".join(k for _, k, _ in named)
        )
    kind, canonical, name = named[0]
    upd = int(time.time()) if now is None else now
    with _write(conn):
        conn.execute(
            f"INSERT INTO {STOCK_TABLE}(key, qty, upd) VALUES (?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET qty = excluded.qty, upd = excluded.upd",
            (canonical, qty, upd),
        )
    return Stocked(canonical, qty, upd, kind, name)


def remove_stock(conn: sqlite3.Connection, key: str) -> bool:
    """Drop ``key`` from the stock (as it is stored). False when it wasn't there."""
    if not isinstance(key, str) or not key or len(key) > MAX_KEY:
        raise BaseError("key must be an item or fluid key")
    with _write(conn):
        gone = conn.execute(
            f"DELETE FROM {STOCK_TABLE} WHERE key = ?", (key,)
        ).rowcount
    return gone > 0


# --- tier ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tier:
    """The stage in use (the override's, else the quests'), GT voltage tier, how it was set,
    and the detected stage with the key quest that shows it (WO-1.5, D114)."""

    stage: str
    tier: int
    how: str  # override or quests
    detected: str | None
    quest: str | None = None
    quest_name: str | None = None


def tier(conn: sqlite3.Connection) -> Tier:
    stage, how, detected = quest_db.effective_tier(conn)
    evidence = quest_db._json_kv(conn, quest_db.KV_TIER) or {}
    quest = evidence.get("quest")
    return Tier(
        stage,
        bq.STAGES.get(stage, 0),
        how,
        detected,
        str(quest) if quest else None,
        str(evidence.get("name")) if quest and evidence.get("name") else None,
    )


def set_tier(conn: sqlite3.Connection, stage: str | None) -> Tier:
    """Override the tier with ``stage`` (D64), or clear the override with None."""
    if stage is not None and stage not in bq.STAGES:
        raise BaseError(f"unknown stage {stage!r}: one of {', '.join(bq.STAGES)}")
    quest_db.set_override(conn, stage)
    return tier(conn)


# --- what the designer reads ---------------------------------------------------------------


@dataclass(frozen=True)
class MyBase:
    """My base as the designer reads it: WO-3.3's ``BaseInputs`` (structural), one snapshot."""

    tier: int
    owned: Mapping[int, int] = field(default_factory=dict)
    stocked: Mapping[str, float] = field(default_factory=dict)
    lines: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> MyBase:
        return cls(
            tier(conn).tier,
            owned_machines(conn),
            {s.key: s.qty for s in stock(conn)},
            {},  # accepted lines have no source yet (D211)
        )

    def tier_cap(self) -> int:
        """The GT voltage tier index (0 = ULV), after any override."""
        return self.tier

    def owned_machines(self) -> Mapping[int, int]:
        """GT mte id -> how many he has."""
        return dict(self.owned)

    def stock(self) -> Mapping[str, float]:
        """Stable key -> quantity on hand."""
        return dict(self.stocked)

    def line_outputs(self) -> Mapping[str, float]:
        """Stable key -> rate per second from accepted lines."""
        return dict(self.lines)


def snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Everything My base holds, as the API and the dashboard show it."""
    t = tier(conn)
    return {
        "tier": {
            "stage": t.stage,
            "tier": t.tier,
            "how": t.how,
            "detected": t.detected,
            "detected_tier": bq.STAGES.get(t.detected) if t.detected else None,
            "quest": f"q:{t.quest}" if t.quest else None,
            "quest_name": t.quest_name,
        },
        "stages": [{"stage": s, "tier": n} for s, n in bq.STAGES.items()],
        "machines": [
            {
                "mte_id": m.mte_id,
                "ref": f"mte:{m.mte_id}",
                "name": m.name,
                "tier": m.tier,
                "multiblock": m.multiblock,
                "count": m.count,
                "source": m.source,
                "sources": m.sources,
                "upd": m.upd,
            }
            for m in machines(conn)
        ],
        "stock": [
            {
                "key": s.key,
                "name": s.name,
                "kind": s.kind,
                "qty": s.qty,
                "unit": s.unit,
                "upd": s.upd,
            }
            for s in stock(conn)
        ],
        "lines": [],
        "has_db": has_table(conn, "machine"),
    }
