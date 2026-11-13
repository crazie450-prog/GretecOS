"""What is true of the base right now, for the advisor and the notices (WO-6.5, plan 7.5, D71).

Each check reads the DB only (the core's tables and the game DB, one ``gos.sqlite``) and returns
:class:`Finding` rows. A finding's numbers are the DB's or a plan's, copied, never estimated
(plan 0.1 principle 1), and it cites where each came from: ``plan:<id>`` (a plan's numbers,
through the EU ledger), ``tel:<id>`` (an OC reading, WO-7.1), ``mte:<id>`` (a machine), ``q:<id>``
(a quest). A stale reading is never used (WO-7.1's ``STALE_S``; a sample older than its entry's
``last_seen`` less ``HEARTBEAT_S`` was left out since, as :func:`gregtec.planner.ledger.reconcile`
reads them).

- :func:`power_deficit` (D71, on by default): the running lines use more than their sources give
  (the EU ledger's ``deficit`` warning in the ``running`` scope, D239), or a fresh OC power entry
  (an LSC, a battery buffer) whose average EU/t out is above its EU/t in (draining);
- :func:`stalled_machines` (D71, on by default): a GT machine OC sees with a job (``hasWork``),
  allowed to work, not running (``isMachineActive`` false) for :data:`STALL_S` or more;
- :func:`buildable_plans` (D71, on by default): an accepted plan whose every build component (its
  machines' items and, for a multiblock, the blocks of its smallest structure variant, WO-4.3's
  :func:`gregtec.planner.effort.components`) is in My base's stock in the amount it needs;
- :func:`opened_quests` (opt-in): a frontier quest that a quest done in the window unlocked;
- :func:`ledger_warnings`: the ledger's other warnings (the advisor's, not a notice).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec.bridge.telemetry import HEARTBEAT_S, STALE_S

STALL_S = 60  # a machine with a job that hasn't run this long is stalled
DRAIN_HOLD_S = (
    60  # an OC power entry draining this long is a deficit (the notices' hold)
)
OPENED_WINDOW_S = 48 * 3600  # plan 7.5: the advisor reads 48 h of events


@dataclass(frozen=True)
class Finding:
    kind: str  # power_deficit | machine_stalled | plan_buildable | quest_opened | power_warning
    key: str  # what it is about, stable while it holds ("ledger:running", "tel:<id>", ...)
    title: str  # one line, no numbers
    detail: str  # its numbers, each next to its citation
    refs: tuple[str, ...]
    link: str  # where on the dashboard it is shown (a #/ route)
    value: float | None = None  # the alert card's number (EU/t), when it has one
    unit: str | None = None
    hold_s: int = 0  # how long it must hold before a notice fires (a reading's flicker)


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def _fmt(v: float) -> str:
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.6g}"


def _sample(
    conn: sqlite3.Connection, src: str, key: str, since: int
) -> tuple[float, int] | None:
    """The newest ``key`` reading of ``src`` at or after ``since``: (value, ts)."""
    row = conn.execute(
        "SELECT value, ts FROM tel_sample WHERE src = ? AND key = ? AND ts >= ?"
        " AND value IS NOT NULL ORDER BY ts DESC LIMIT 1",
        (src, key, since),
    ).fetchone()
    return None if row is None else (float(row[0]), int(row[1]))


@dataclass(frozen=True)
class _Tel:
    id: str
    name: str | None
    last_seen: int


def _fresh(conn: sqlite3.Connection, status: str, now: float) -> list[_Tel]:
    """``tel_machine`` rows of ``status`` heard within WO-7.1's ``STALE_S``."""
    if not _has(conn, "tel_machine") or not _has(conn, "tel_sample"):
        return []
    return [
        _Tel(str(r[0]), None if r[1] is None else str(r[1]), int(r[2]))
        for r in conn.execute(
            "SELECT id, name, last_seen FROM tel_machine WHERE status = ? AND last_seen >= ?"
            " ORDER BY id",
            (status, int(now) - STALE_S),
        )
    ]


# --- power ---------------------------------------------------------------------------------


def power_deficit(conn: sqlite3.Connection, now: float) -> list[Finding]:
    """The running lines' deficit (the ledger, plans' numbers) and each draining OC power
    entry (fresh readings), in that order."""
    from gregtec.planner import ledger

    out: list[Finding] = []
    led = ledger.build(ledger.lines(conn))
    for w in led.warnings:
        if w.kind == "deficit" and w.scope == "running":
            gen = led.total("running", "gen_eut")
            use = led.total("running", "use_eut_avg")
            cites = tuple(f"plan:{p}" for p in w.plans)
            out.append(
                Finding(
                    "power_deficit",
                    "ledger:running",
                    "Power deficit: your running lines use more than they make",
                    f"running lines use {_fmt(use)} EU/t, sources give {_fmt(gen)} EU/t, "
                    f"net {_fmt(gen - use)} EU/t (EU ledger: {', '.join(cites)})",
                    cites,
                    "#/power",
                    gen - use,
                    "EU/t",
                )
            )
    for r in _fresh(conn, "power", now):
        since = int(r.last_seen) - HEARTBEAT_S
        got_in = _sample(conn, r.id, "eu_in", since)
        got_out = _sample(conn, r.id, "eu_out", since)
        if got_in is None or got_out is None or got_out[0] <= got_in[0]:
            continue
        cite = f"tel:{r.id}"
        name = r.name or "a power entry"
        out.append(
            Finding(
                "power_deficit",
                cite,
                f"Power deficit: {name} is draining",
                f"{name} averages {_fmt(got_in[0])} EU/t in and {_fmt(got_out[0])} EU/t out, "
                f"net {_fmt(got_in[0] - got_out[0])} EU/t ({cite}, OC reading)",
                (cite,),
                "#/base",
                got_in[0] - got_out[0],
                "EU/t",
                DRAIN_HOLD_S,
            )
        )
    return out


def ledger_warnings(conn: sqlite3.Connection) -> list[Finding]:
    """The ledger's warnings other than the running deficit (that one is
    :func:`power_deficit`'s): the running lines' tier and amp warnings, and every warning of all
    accepted lines (what building them would bring)."""
    from gregtec.planner import ledger

    led = ledger.build(ledger.lines(conn))
    out = []
    for w in led.warnings:
        if w.kind == "deficit" and w.scope == "running":
            continue
        cites = tuple(f"plan:{p}" for p in w.plans)
        scope = "running lines" if w.scope == "running" else "accepted lines"
        out.append(
            Finding(
                "power_warning",
                f"ledger:{w.scope}:{w.kind}:{w.tier}",
                f"Power: {w.kind.replace('_', ' ')} in your {scope}"
                + (f" at {w.tier}" if w.tier else ""),
                f"{w.detail} (EU ledger: {', '.join(cites) or 'no plans'})",
                cites,
                "#/power",
            )
        )
    return out


# --- machines ------------------------------------------------------------------------------


def stalled_machines(conn: sqlite3.Connection, now: float) -> list[Finding]:
    """GT machines OC sees with a job, allowed to work and not running for :data:`STALL_S` or
    more (fresh readings only; one Clayton paused himself is ``paused``, never stalled)."""
    out = []
    for r in _fresh(conn, "idle", now):
        since = int(r.last_seen) - HEARTBEAT_S
        work = _sample(conn, r.id, "work", since)
        if work is None or work[0] != 1.0:
            continue
        ran = conn.execute(
            "SELECT max(ts) FROM tel_sample WHERE src = ? AND key = 'active' AND value = 1",
            (r.id,),
        ).fetchone()[0]
        first = conn.execute(
            "SELECT min(ts) FROM tel_sample WHERE src = ? AND key = 'active'", (r.id,)
        ).fetchone()[0]
        start = ran if ran is not None else first
        if start is None or now - float(start) < STALL_S:
            continue
        minutes = int((now - float(start)) // 60)
        cite = f"tel:{r.id}"
        name = r.name or "a machine"
        out.append(
            Finding(
                "machine_stalled",
                cite,
                f"Machine stalled: {name}",
                f"{name} has a job but hasn't run for {minutes} min"
                + (
                    ""
                    if ran is not None
                    else " (not seen running since OC began reporting it)"
                )
                + f" ({cite}, OC reading)",
                (cite,),
                "#/base",
            )
        )
    return out


# --- plans -----------------------------------------------------------------------------------


def stock_map(conn: sqlite3.Connection) -> dict[str, float]:
    if not _has(conn, "my_base_stock"):
        return {}
    return {
        str(k): float(q) for k, q in conn.execute("SELECT key, qty FROM my_base_stock")
    }


def plan_rows(
    conn: sqlite3.Connection, statuses: Sequence[str]
) -> list[tuple[str, str, str]]:
    """(id, name, status) of the saved plans in ``statuses``, oldest first."""
    if not _has(conn, "plan"):
        return []
    marks = ",".join("?" * len(statuses))
    return [
        (str(r[0]), str(r[1] or r[0]), str(r[2]))
        for r in conn.execute(
            f"SELECT id, name, status FROM plan WHERE status IN ({marks})"
            " ORDER BY created, id",
            tuple(statuses),
        )
    ]


def _load(home: Path, pid: str) -> Mapping[str, Any] | None:
    from gregtec.planner.outputs import store

    try:
        return store.load(home, pid)
    except (OSError, ValueError):
        return None


def needs(
    conn: sqlite3.Connection, plan: Mapping[str, Any]
) -> tuple[dict[str, int], int] | None:
    """A plan's build components summed by stable key, and how many it can't count; None when
    the DB can't say (no game DB, a plan with no nodes)."""
    from gregtec.planner import effort

    if not isinstance(plan.get("nodes"), list) or not _has(conn, "machine"):
        return None
    try:
        found, _, skipped = effort.components(conn, plan)
    except (KeyError, TypeError, ValueError, sqlite3.Error):
        return None
    total: dict[str, int] = {}
    for c in found:
        total[c.key] = total.get(c.key, 0) + int(c.qty)
    return total, skipped


def buildable_plans(conn: sqlite3.Connection, home: Path) -> list[Finding]:
    """Accepted plans every build component of which is in stock (D63: stock is what Clayton
    entered; a plan with a part GOS can't count is never called buildable)."""
    stock = stock_map(conn)
    if not stock:
        return []
    out = []
    for pid, name, _ in plan_rows(conn, ("accepted",)):
        plan = _load(home, pid)
        got = None if plan is None else needs(conn, plan)
        if got is None:
            continue
        total, skipped = got
        if skipped or not total:
            continue
        if any(stock.get(k, 0.0) < q for k, q in total.items()):
            continue
        cite = f"plan:{pid}"
        parts = sum(total.values())
        out.append(
            Finding(
                "plan_buildable",
                cite,
                f"You can build {name} from stock",
                f"every build part of {name} is in My base's stock: {len(total)} kind{'' if len(total) == 1 else 's'}, "
                f"{parts} blocks and machines in all ({cite})",
                (cite,),
                f"#/designer/{pid}",
            )
        )
    return out


# --- quests --------------------------------------------------------------------------------


def opened_quests(
    conn: sqlite3.Connection,
    now: float,
    frontier: Sequence[Any],
    window_s: int = OPENED_WINDOW_S,
) -> list[Finding]:
    """Frontier quests (``frontier``: :class:`gregtec.quests.guide.Ranked` rows, in their
    order) that a quest done in the last ``window_s`` unlocked. Only frontier quests are named:
    they are in reach, so the book shows them (D26)."""
    if not frontier or not _has(conn, "event"):
        return []
    done: dict[str, int] = {}
    for ref, ts in conn.execute(
        "SELECT ref, max(ts) FROM event WHERE kind = 'quest_done' AND ts >= ?"
        " AND ref LIKE 'q:%' GROUP BY ref",
        (int(now) - window_s,),
    ):
        done[str(ref)[2:]] = int(ts)
    if not done:
        return []
    prereqs: dict[str, list[str]] = {}
    for q, p in conn.execute("SELECT quest_id, prereq_id FROM quest_prereq"):
        prereqs.setdefault(str(q), []).append(str(p))
    out = []
    for r in frontier:
        by = [p for p in prereqs.get(r.id, []) if p in done]
        if not by:
            continue
        p = max(by, key=lambda q: (done[q], q))
        hours = max(0, int((now - done[p]) // 3600))
        out.append(
            Finding(
                "quest_opened",
                f"q:{r.id}",
                f"A quest just opened up: {r.name}",
                f"q:{r.id} opened when you finished q:{p} "
                + ("within the hour" if hours == 0 else f"{hours} h ago"),
                (f"q:{r.id}", f"q:{p}"),
                "#/quests",
            )
        )
    return out
