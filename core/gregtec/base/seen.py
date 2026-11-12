"""My base's automatic machine counts: placement events and the census (WO-6.2, D211, D261).

Both sources say *where* each GT machine is, so the core keeps their sightings by position in
``my_base_machine_at`` (``source``, dim, x, y, z -> mte id) and derives each source's count per
machine in ``my_base_machine`` from it (WO-3.8's table, D211: ``manual`` > ``census`` > ``event``):

- ``machine_built`` puts the machine at its position (``event``); ``machine_removed`` takes the
  machine off it (only if that machine is the one there). Applying an event twice changes nothing,
  so a replayed events file or a retried post is harmless, and a removal of a machine the core
  never saw placed (it predates the listener) is ignored.
- ``machine_census`` (``scope: chunks``) replaces the census of the chunks it names with the
  machines it lists; ``scope: world`` (the end of a baseline) drops the census of every region it
  doesn't list (regions trimmed or deleted since).
- After each batch the touched machines are counted again. An ``event`` count that drops to 0 is
  deleted (events are the last resort anyway); a ``census`` count of 0 is kept, so the census, the
  truth of the last save, still outranks a stale event count for that machine (D211).

Only Play's watchers feed My base (D261); Lab's events and census are shown in the event feed and
by ``gos watch census --instance lab``, never counted as Clayton's base. Every write is one
``BEGIN IMMEDIATE`` transaction on the core's own connection (the core owns the DB, plan 4.2).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from gregtec.base.state import MACHINE_TABLE

AT_TABLE = "my_base_machine_at"
KINDS = ("machine_built", "machine_removed", "machine_census")
REGION_CHUNKS = 1024


@dataclass
class Applied:
    """What one batch changed: events and census rows taken, and the machines recounted."""

    events: int = 0
    census_chunks: int = 0
    census_machines: int = 0
    ignored: int = 0
    touched: dict[str, set[int]] = field(
        default_factory=lambda: {"event": set(), "census": set()}
    )

    @property
    def changed(self) -> bool:
        return any(self.touched.values())


def _ints(value: object, n: int) -> list[int] | None:
    if (
        not isinstance(value, list)
        or len(value) != n
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        return None
    return list(value)


def _pos(data: Mapping[str, Any]) -> tuple[int, int, int, int, int] | None:
    vals = [data.get(k) for k in ("dim", "x", "y", "z", "mte_id")]
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return None
    dim, x, y, z, mte = vals
    assert isinstance(mte, int)
    if mte <= 0:
        return None
    return dim, x, y, z, mte  # type: ignore[return-value]


def apply(
    conn: sqlite3.Connection,
    events: Iterable[Mapping[str, Any]],
    now: int | None = None,
) -> Applied:
    """Apply a batch of the watchers' events (any kinds; the machine ones are used) in order."""
    upd = int(time.time()) if now is None else now
    out = Applied()
    batch = [
        e for e in events if e.get("kind") in KINDS and isinstance(e.get("data"), dict)
    ]
    if not batch:
        return out
    conn.execute("BEGIN IMMEDIATE")
    try:
        for e in batch:
            data = e["data"]
            if e["kind"] == "machine_census":
                _census(conn, data, upd, out)
            else:
                _event(conn, e["kind"], data, upd, out)
        for source, ids in out.touched.items():
            _recount(conn, source, ids, upd)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return out


def _event(
    conn: sqlite3.Connection, kind: str, data: Mapping[str, Any], upd: int, out: Applied
) -> None:
    pos = _pos(data)
    if pos is None:
        out.ignored += 1
        return
    dim, x, y, z, mte = pos
    where = "source = 'event' AND dim = ? AND x = ? AND y = ? AND z = ?"
    row = conn.execute(
        f"SELECT mte_id FROM {AT_TABLE} WHERE {where}", (dim, x, y, z)
    ).fetchone()
    old = None if row is None else int(row[0])
    if kind == "machine_built":
        conn.execute(
            f"INSERT INTO {AT_TABLE}(source, dim, x, y, z, mte_id, upd) VALUES ('event',?,?,?,?,?,?)"
            " ON CONFLICT(source, dim, x, y, z) DO UPDATE SET mte_id = excluded.mte_id,"
            " upd = excluded.upd",
            (dim, x, y, z, mte, upd),
        )
        out.touched["event"].add(mte)
        if old is not None:
            out.touched["event"].add(old)
    elif old == mte:
        conn.execute(f"DELETE FROM {AT_TABLE} WHERE {where}", (dim, x, y, z))
        out.touched["event"].add(mte)
    else:
        out.ignored += 1
        return
    out.events += 1


def _census(
    conn: sqlite3.Connection, data: Mapping[str, Any], upd: int, out: Applied
) -> None:
    scope = data.get("scope")
    if scope == "world":
        regions = data.get("regions")
        if data.get("complete") is not True or not isinstance(regions, list):
            return
        keep = {tuple(r) for r in (_ints(r, 3) for r in regions) if r is not None}
        stale = [
            (int(d), int(rx), int(rz))
            for d, rx, rz in conn.execute(
                f"SELECT DISTINCT dim, x >> 9, z >> 9 FROM {AT_TABLE} WHERE source = 'census'"
            )
            if (int(d), int(rx), int(rz)) not in keep
        ]
        for d, rx, rz in stale:
            _drop(conn, d, rx * 512, rx * 512 + 511, rz * 512, rz * 512 + 511, out)
        return
    region = _ints(data.get("region"), 2)
    dim, chunks, rows = data.get("dim"), data.get("chunks"), data.get("machines")
    if (
        scope != "chunks"
        or region is None
        or not isinstance(dim, int)
        or isinstance(dim, bool)
        or not isinstance(chunks, list)
        or not isinstance(rows, list)
    ):
        out.ignored += 1
        return
    rx, rz = region
    looked: set[tuple[int, int]] = set()
    for i in chunks:
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < REGION_CHUNKS:
            cx, cz = rx * 32 + (i & 31), rz * 32 + (i >> 5)
            looked.add((cx, cz))
            _drop(conn, dim, cx * 16, cx * 16 + 15, cz * 16, cz * 16 + 15, out)
    out.census_chunks += len(looked)
    for r in rows:
        row = _ints(r, 6)
        if row is None or row[3] <= 0 or (row[0] >> 4, row[2] >> 4) not in looked:
            out.ignored += 1
            continue
        x, y, z, mte = row[:4]
        conn.execute(
            f"INSERT INTO {AT_TABLE}(source, dim, x, y, z, mte_id, upd) VALUES ('census',?,?,?,?,?,?)"
            " ON CONFLICT(source, dim, x, y, z) DO UPDATE SET mte_id = excluded.mte_id,"
            " upd = excluded.upd",
            (dim, x, y, z, mte, upd),
        )
        out.touched["census"].add(mte)
        out.census_machines += 1


def _drop(
    conn: sqlite3.Connection, dim: int, x0: int, x1: int, z0: int, z1: int, out: Applied
) -> None:
    for (mte,) in conn.execute(
        f"DELETE FROM {AT_TABLE} WHERE source = 'census' AND dim = ?"
        " AND x BETWEEN ? AND ? AND z BETWEEN ? AND ? RETURNING mte_id",
        (dim, x0, x1, z0, z1),
    ).fetchall():
        out.touched["census"].add(int(mte))


def _recount(conn: sqlite3.Connection, source: str, ids: set[int], upd: int) -> None:
    for mte in sorted(ids):
        (n,) = conn.execute(
            f"SELECT COUNT(*) FROM {AT_TABLE} WHERE source = ? AND mte_id = ?",
            (source, mte),
        ).fetchone()
        if n == 0 and source == "event":
            conn.execute(
                f"DELETE FROM {MACHINE_TABLE} WHERE mte_id = ? AND source = 'event'",
                (mte,),
            )
            continue
        conn.execute(
            f"INSERT INTO {MACHINE_TABLE}(mte_id, source, count, upd) VALUES (?,?,?,?)"
            " ON CONFLICT(mte_id, source) DO UPDATE SET count = excluded.count,"
            " upd = excluded.upd",
            (mte, source, int(n), upd),
        )


def positions(
    conn: sqlite3.Connection, source: str
) -> dict[tuple[int, int, int, int], int]:
    """(dim, x, y, z) -> mte id for one source's sightings."""
    return {
        (int(d), int(x), int(y), int(z)): int(m)
        for d, x, y, z, m in conn.execute(
            f"SELECT dim, x, y, z, mte_id FROM {AT_TABLE} WHERE source = ?", (source,)
        )
    }
