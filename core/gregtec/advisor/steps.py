"""The advisor's next steps (WO-6.5, plan 7.5 "Advisor", D71, D72, D266).

:func:`advise` reads the plan's inputs: ``me/goals.md`` (Clayton's, read only, never written), My
base (tier, machines, stock), the top :data:`FRONTIER_N` frontier quests (WO-6.4's ranking), the
last :data:`EVENTS_H` hours of events (WO-6.3's store), the EU ledger (WO-4.4), the problems
the advisor checks (a power deficit: :mod:`.checks`), the stalled and stopped machines WO-7.5's
alert engine has firing (its ``alert`` rows, D281; the head's ``problems``) and the active plans;
and returns the steps **ranked by code** ([QA:D2]: models explain, never reorder), each with its reasons, citations and
a dashboard link. The order is fixed by :data:`BUCKETS`, then each source's own order:

1. ``fix``: a power deficit (running lines, or a draining OC power entry), then stalled machines
   (one step for all of them), then the running lines' other power warnings;
2. ``build``: an accepted plan buildable from stock;
3. ``goal``: frontier quests that lead to his goals (WO-6.4's order: first goal first, fewest
   steps);
4. ``power``: the accepted lines' power warnings (what building them all would bring);
5. ``line``: an accepted plan to build (parts not all in stock) or a plan being built;
6. ``quest``: frontier quests that serve an active line (WO-6.4's order);
7. ``opened``: frontier quests a quest he finished in the last 48 h unlocked;
8. ``quest``: the rest of the frontier, WO-6.4's order (main quests, then what unlocks most).

**Spoilers** (D26, D72): a step names only quests on the frontier (in reach, so the book shows
them) and his own goal words; a locked quest is never named or described here, at any level. A
frontier quest's reasons are WO-6.4's (D269): they may count what it unlocks ("unlocks 3") or how
many steps a goal is away ("goal: X (5 steps)"), never which quests those are. Steps are **nudges**: what to look at and why, with a link; the detail (a quest's tasks, a
plan's BOM) is one tap or tool call away and shows at the global level. Every number in a step is
the DB's or a plan's, next to its citation.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.advisor import checks
from gregtec.quests.guide import Ranked
from gregtec.vault.io import VaultIO

FRONTIER_N = 20  # plan 7.5: the top 20 frontier quests
EVENTS_H = 48  # plan 7.5: 48 h of events
STEPS = 3  # plan 7.5: three ranked next steps
STEPS_MAX = 10
STALLED_SHOWN = 5  # stalled machines cited in their one step
BUCKETS = ("fix", "build", "goal", "power", "line", "quest_line", "opened", "quest")


@dataclass(frozen=True)
class Step:
    kind: str  # fix, build, goal, power, line, opened, quest
    title: str
    why: tuple[str, ...]
    refs: tuple[str, ...]
    link: str
    bucket: int = 0

    def as_dict(self, rank: int) -> dict[str, Any]:
        return {
            "rank": rank,
            "kind": self.kind,
            "title": self.title,
            "why": list(self.why),
            "refs": list(self.refs),
            "link": self.link,
        }


@dataclass
class Advice:
    steps: list[Step]
    tier: str
    spoil: int
    inputs: dict[str, int | str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return checks._has(conn, table)


def _events(conn: sqlite3.Connection, now: float) -> dict[str, int]:
    if not _has(conn, "event"):
        return {}
    return {
        str(k): int(n)
        for k, n in conn.execute(
            "SELECT kind, COUNT(*) FROM event WHERE ts >= ? GROUP BY kind ORDER BY kind",
            (int(now) - EVENTS_H * 3600,),
        )
    }


def _frontier(
    conn: sqlite3.Connection, vault: VaultIO | None, db: Path | None
) -> tuple[list[Ranked], int, list[str]]:
    """(the ranked frontier's top :data:`FRONTIER_N`, his goals counted, notes)."""
    from gregtec.db import quests as quest_db
    from gregtec.quests import guide

    try:
        player = quest_db.tracked_player(conn)
    except quest_db.QuestError:
        return [], 0, ["no quest book loaded yet (`gos quests load`): no quest steps"]
    goals = guide.goals(conn, vault)
    found = guide.rank_frontier(conn, player, goals, guide.active_lines(conn, db))
    notes = [] if goals else ["no goals in me/goals.md: quests ranked without them"]
    return found[:FRONTIER_N], len(goals), notes


def _quest_step(r: Ranked, kind: str, bucket: int, extra: Sequence[str] = ()) -> Step:
    line = r.lines[0] if r.lines else None
    title = f"Quest: {r.name}" + (f" ({line})" if line else "")
    return Step(
        kind,
        title,
        tuple(extra) + tuple(r.why),
        (f"q:{r.id}",),
        "#/quests",
        bucket,
    )


def advise(
    conn: sqlite3.Connection,
    *,
    vault: VaultIO | None,
    db: Path | None,
    home: Path | None,
    spoil: int = 0,
    now: float | None = None,
    n: int = STEPS,
) -> Advice:
    """The next ``n`` steps (see the module docstring); ``spoil``: the global level, shown with
    them (nothing here is past the frontier). Deterministic: the same DB, notes and clock give
    the same steps in the same order."""
    from gregtec.base import state

    t = time.time() if now is None else now
    b = {name: i for i, name in enumerate(BUCKETS)}
    out: list[Step] = []
    notes: list[str] = []

    # 1. fix
    deficits = checks.power_deficit(conn, t)
    if deficits:
        out.append(
            Step(
                "fix",
                deficits[0].title,
                tuple(f.detail for f in deficits),
                tuple(dict.fromkeys(r for f in deficits for r in f.refs)),
                deficits[0].link,
                b["fix"],
            )
        )
    stalled = checks.stalled_machines(conn, t)
    if stalled:
        shown = stalled[:STALLED_SHOWN]
        title = (
            stalled[0].title
            if len(stalled) == 1
            else f"Machines stalled: {len(stalled)} aren't running"
        )
        why = tuple(f.detail for f in shown)
        if len(stalled) > len(shown):
            why += (f"and {len(stalled) - len(shown)} more (Base view)",)
        out.append(
            Step(
                "fix",
                title,
                why,
                tuple(r for f in shown for r in f.refs),
                "#/base",
                b["fix"],
            )
        )
    warnings = checks.ledger_warnings(conn)
    running = [w for w in warnings if w.key.startswith("ledger:running:")]
    if running:
        out.append(
            Step(
                "fix",
                running[0].title,
                tuple(w.detail for w in running),
                tuple(dict.fromkeys(r for w in running for r in w.refs)),
                "#/power",
                b["fix"],
            )
        )

    # 2. build
    buildable = checks.buildable_plans(conn, home) if home is not None else []
    for f in buildable:
        out.append(
            Step(
                "build",
                f.title,
                (f.detail, "an accepted line: you picked it in the Designer"),
                f.refs,
                f.link,
                b["build"],
            )
        )

    # 3, 6, 7, 8: quests
    frontier, n_goals, quest_notes = _frontier(conn, vault, db)
    notes += quest_notes
    opened = {f.key: f for f in checks.opened_quests(conn, t, frontier)}
    for r in frontier:
        if r.goal is not None:
            out.append(_quest_step(r, "goal", b["goal"]))
        elif r.line is not None:
            out.append(_quest_step(r, "quest", b["quest_line"]))
        elif f"q:{r.id}" in opened:
            f = opened[f"q:{r.id}"]
            out.append(
                Step(
                    "opened",
                    f"Quest: {r.name}" + (f" ({r.lines[0]})" if r.lines else ""),
                    (f.detail, *r.why),
                    f.refs,
                    "#/quests",
                    b["opened"],
                )
            )
        else:
            out.append(_quest_step(r, "quest", b["quest"]))

    # 4. power for the accepted lines
    planned = [w for w in warnings if w.key.startswith("ledger:all:")]
    if planned:
        out.append(
            Step(
                "power",
                planned[0].title,
                tuple(w.detail for w in planned),
                tuple(dict.fromkeys(r for w in planned for r in w.refs)),
                "#/power",
                b["power"],
            )
        )

    # 5. lines to build
    ready = {f.key for f in buildable}
    for pid, name, status in checks.plan_rows(conn, ("accepted", "building")):
        if f"plan:{pid}" in ready:
            continue
        if status == "accepted":
            title = f"Build {name}"
            why = (
                (
                    f"you accepted plan:{pid}; its parts aren't all in My base's stock "
                    "(its BOM lists them)"
                ),
            )
        else:
            title = f"Finish building {name}"
            why = (f"plan:{pid} is being built: set it running when it is",)
        out.append(
            Step("line", title, why, (f"plan:{pid}",), f"#/designer/{pid}", b["line"])
        )

    out.sort(key=lambda s: s.bucket)  # stable: each source's own order within a bucket
    tier = state.tier(conn)
    counts = _events(conn, t)
    active = checks.plan_rows(conn, ("accepted", "building", "running"))
    inputs: dict[str, int | str] = {
        "goals": n_goals,
        "machines": len(state.owned_machines(conn))
        if _has(conn, "my_base_machine")
        else 0,
        "stocked": len(checks.stock_map(conn)),
        "frontier": len(frontier),
        f"events_{EVENTS_H}h": sum(counts.values()),
        "active_plans": len(active),
        "problems": len(deficits) + len(stalled),  # its checks and the stall alerts
    }
    if not out:
        notes.append(
            "nothing to suggest yet: load the quest book (`gos quests load`), add goals in "
            "Settings or accept a plan in the Designer"
        )
    return Advice(
        out[: max(1, min(n, STEPS_MAX))],
        f"{tier.stage} ({tier.how})",
        spoil,
        inputs,
        notes,
    )
