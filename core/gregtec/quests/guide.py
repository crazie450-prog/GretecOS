"""The quest guide's four functions over the quest DB (plan 7.4, D31, D269), for the quest tools,
the Quests view and the questline summaries. Everything here is read from WO-1.5's tables and
ranked by rules, never by a model ([QA:D2]: models explain, code ranks).

1. **Requirements**: a quest's tasks (consumed or not) and rewards are WO-2.2's ``quest_get``;
   :func:`unlocks` adds the quests it unlocks.
2. **Progress and next quests**: :func:`rank_frontier` orders the frontier (D113) by
   Clayton's goals, then his active lines, then main quests, then how many quests each one
   unlocks, then quest-book order. Each row says why (``why``).
3. **Item<->quest links**: :func:`item_links`: the frontier quests' items (requires, rewards)
   for the view, and WO-2.2's ``quest_for_item`` the other way.
4. **Questline summaries**: :func:`line_summary` (:mod:`gregtec.quests.summaries` caches them as
   ``quests/qs-<line>.md``).

**Goals** are ``goal: <text>`` lines in ``me/goals.md`` (Clayton's, read only), first first. A goal
means the quests it names: a ``q:<id>``; a stage or tier ("MV", "reach HV") means that stage's
key quests (``data/curated/tier_quests.tsv``, D114); otherwise its words (3 letters or more,
common verbs left out), every one of them in the quest's name or in the name of an item its
tasks ask for. A frontier quest serves a goal when the goal's quest is it or needs it (through
prerequisites, any number of steps). **Active lines** are saved plans whose status is accepted,
building or running (WO-3.6's lifecycle); a frontier quest serves one when a task or reward is
the plan's target or in its bill of materials.

Spoilers (D26, D72): nothing here names a quest past the frontier. Reasons quote Clayton's own
goal words and plan names, and counts; :mod:`gregtec.quests.view` and the tools decide what each
quest shows.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.ingest import quests as bq
from gregtec.vault.io import VaultError, VaultIO

GOALS_NOTE = "me/goals.md"
ACTIVE = ("accepted", "building", "running")  # WO-3.6's lifecycle: a line being worked on
MAX_GOALS = 20
_GOAL = re.compile(r"^goal:\s*(.+?)\s*$")
_QID = re.compile(r"\bq:([A-Za-z0-9_-]{22})\b")
_WORD = re.compile(r"[a-z0-9]+")
# Words a goal says that name no quest: verbs and fillers ("get to MV", "make some steel").
_STOP = frozenset(
    "the and for with get got make made build craft reach unlock finish start some more first "
    "my all any into from that this then than want need tier age stage line lines quest quests "
    "set setup up automate".split()
)
_STAGES = {s.casefold(): s for s in bq.STAGES}


@dataclass(frozen=True)
class Goal:
    text: str  # Clayton's words, as written
    quests: frozenset[str]  # the quest ids it means


@dataclass(frozen=True)
class ActiveLine:
    plan: str  # plan id (cited plan:<id>)
    name: str
    keys: frozenset[str]  # item and fluid keys: its target and bill of materials


@dataclass
class Ranked:
    id: str
    name: str
    state: str
    main: bool
    lines: list[str]
    order: int  # quest-book order (tab, top to bottom, left to right)
    unlocks: int  # quests that list it as a prerequisite
    goal: tuple[int, int] | None = None  # (goal index, steps to the goal's quest)
    line: ActiveLine | None = None
    why: list[str] = field(default_factory=list)

    def key(self) -> tuple[int, int, int, int, int, int]:
        goal = self.goal or (MAX_GOALS + 1, 0)
        return (goal[0], goal[1], self.line is None, not self.main, -self.unlocks, self.order)


# --- reading -----------------------------------------------------------------------------------


def read_goals(vault: VaultIO | None) -> list[str]:
    """Clayton's goals, in his order (``goal: <text>`` lines of ``me/goals.md``); none when the
    note is missing."""
    if vault is None:
        return []
    try:
        _, text = vault.read(GOALS_NOTE)
    except (VaultError, OSError):
        return []
    out = []
    for line in text.splitlines():
        if m := _GOAL.match(line.strip()):
            out.append(m[1])
    return out[:MAX_GOALS]


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.casefold()) if len(w) >= 3 and w not in _STOP]


def goal_quests(conn: sqlite3.Connection, text: str) -> frozenset[str]:
    """The quests one goal means (see the module docstring)."""
    ids = set(_QID.findall(text))
    tokens = re.findall(r"[A-Za-z]+", text)
    stages = {_STAGES[t.casefold()] for t in tokens if t.casefold() in _STAGES}
    if stages:
        try:
            keys = bq.read_tier_keys(quest_db.TIER_KEYS)
        except (OSError, ValueError):
            keys = []
        ids |= {k.quest for k in keys if k.stage in stages}
    words = [w for w in _words(text) if w not in {s.casefold() for s in stages}]
    if not ids and words:
        names: dict[str, list[str]] = defaultdict(list)
        for qid, name in conn.execute("SELECT id, name FROM quest"):
            names[qid].append((name or "").casefold())
        for qid, item, fluid in conn.execute(
            "SELECT qi.quest_id, i.name_en, f.name_en FROM quest_item AS qi"
            " LEFT JOIN item AS i ON i.id = qi.item_id"
            " LEFT JOIN fluid AS f ON f.id = qi.fluid_id WHERE qi.part = 'task'"
        ):
            names[qid].append((item or fluid or "").casefold())
        for qid, texts in names.items():
            if any(all(w in t for w in words) for t in texts):
                ids.add(qid)
    known = {r[0] for r in conn.execute("SELECT id FROM quest")}
    return frozenset(ids & known)


def goals(conn: sqlite3.Connection, vault: VaultIO | None) -> list[Goal]:
    return [Goal(text, goal_quests(conn, text)) for text in read_goals(vault)]


def _key(registry: str | None, meta: int | None, fluid: str | None) -> str | None:
    if fluid:
        return f"fluid:{fluid}"
    if registry is not None and meta is not None:
        return f"{registry}@{meta}"
    return None


def active_lines(conn: sqlite3.Connection, db: Path | None) -> list[ActiveLine]:
    """Saved plans being worked on, with their target and bill of materials."""
    try:
        rows = conn.execute(
            "SELECT id, name, status, target FROM plan ORDER BY created DESC, id"
        ).fetchall()
    except sqlite3.Error:
        return []
    active = [r for r in rows if (r[2] or "") in ACTIVE]
    if not active:
        return []
    boms: dict[str, set[str]] = {}
    if db is not None:
        from gregtec.agents import saved_plans  # its card import would be a cycle at the top

        found, _ = saved_plans.load(db)
        boms = {p.id: {b.key for b in p.bom or []} for p in found}
    out = []
    for pid, name, _, target in active:
        keys = set(boms.get(str(pid), set()))
        if target:
            keys.add(str(target))
        out.append(ActiveLine(str(pid), str(name or pid), frozenset(keys)))
    return out


# --- the ranked frontier --------------------------------------------------------------------


def frontier(
    conn: sqlite3.Connection, player: str
) -> list[tuple[str, str, str, bool, list[str]]]:
    """(id, name, state, main, lines) of each frontier quest once, in book order (D113)."""
    order: dict[str, tuple[str, str, bool]] = {}
    lines: dict[str, list[str]] = defaultdict(list)
    rows = access.query(
        conn, "quest_frontier", {"player": player, "line": None, "limit": -1}
    )
    for r in rows:
        order.setdefault(r[2], (r[1], r[3], bool(r[4])))
        lines[r[2]].append(r[0])
    return [(q, n, s, m, lines[q]) for q, (n, s, m) in order.items()]


def _prereqs(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for q, p in conn.execute("SELECT quest_id, prereq_id FROM quest_prereq"):
        out[q].append(p)
    return out


def dependents(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for q, p in conn.execute("SELECT quest_id, prereq_id FROM quest_prereq ORDER BY quest_id"):
        out[p].append(q)
    return out


def steps_to(targets: Iterable[str], prereqs: dict[str, list[str]]) -> dict[str, int]:
    """For every quest a target needs (through prerequisites), the fewest steps to it."""
    dist: dict[str, int] = {}
    todo: deque[str] = deque()
    for t in targets:
        if t not in dist:
            dist[t] = 0
            todo.append(t)
    while todo:
        q = todo.popleft()
        for p in prereqs.get(q, ()):
            if p not in dist:
                dist[p] = dist[q] + 1
                todo.append(p)
    return dist


def _part_keys(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for qid, reg, meta, fluid in conn.execute(
        "SELECT quest_id, registry_name, meta, fluid FROM quest_item"
        " WHERE quest_id IN (SELECT value FROM json_each(?))",
        (json_ids(ids),),
    ):
        key = _key(reg, meta, fluid)
        if key:
            out[qid].add(key)
    return out


def json_ids(values: Sequence[str]) -> str:
    return json.dumps(list(values))


def rank_frontier(
    conn: sqlite3.Connection,
    player: str,
    goal_list: Sequence[Goal] = (),
    active: Sequence[ActiveLine] = (),
) -> list[Ranked]:
    """The frontier, best first: by goal (his first goal first, then the fewest steps to its
    quest), then an active line it serves, then main quests, then how many quests it unlocks,
    then book order. Deterministic: the same DB, goals and plans give the same order."""
    found = frontier(conn, player)
    if not found:
        return []
    deps = dependents(conn)
    out = [
        Ranked(q, name, state, main, lines, n, len(deps.get(q, ())))
        for n, (q, name, state, main, lines) in enumerate(found)
    ]
    if goal_list:
        prereqs = _prereqs(conn)
        for gi, goal in enumerate(goal_list[:MAX_GOALS]):
            dist = steps_to(goal.quests, prereqs)
            for r in out:
                if r.id in dist and r.goal is None:
                    r.goal = (gi, dist[r.id])
                    steps = dist[r.id]
                    r.why.append(
                        f"goal: {goal.text}"
                        + (" (this quest)" if steps == 0 else f" ({steps} steps)")
                    )
    if active:
        keys = _part_keys(conn, [r.id for r in out])
        for r in out:
            for line in active:
                if keys.get(r.id, set()) & line.keys:
                    r.line = line
                    r.why.append(f"line: {line.name} (plan:{line.plan})")
                    break
    for r in out:
        if r.main:
            r.why.append("main quest")
        if r.unlocks:
            r.why.append(f"unlocks {r.unlocks}")
    return sorted(out, key=Ranked.key)


def ranked(
    conn: sqlite3.Connection, vault: VaultIO | None, db: Path | None
) -> list[Ranked]:
    """The tracked player's frontier ranked by his goals and active lines."""
    player = quest_db.tracked_player(conn)
    return rank_frontier(conn, player, goals(conn, vault), active_lines(conn, db))


# --- links --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Link:
    quest: str
    part: str  # task (requires) or reward
    key: str  # item or fluid key, or ore:<name>
    name: str
    amount: int | None
    consume: bool | None


def item_links(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, list[Link]]:
    """Each quest's items: what its tasks ask for (consumed or not) and what it rewards. Only
    call it for quests in reach: for one past the frontier these are spoilers."""
    out: dict[str, list[Link]] = defaultdict(list)
    rows = conn.execute(
        "SELECT qi.quest_id, qi.part, qi.registry_name, qi.meta, qi.ore, qi.fluid, qi.amount,"
        " t.consume, coalesce(i.name_en, f.name_en) AS name"
        " FROM quest_item AS qi"
        " LEFT JOIN quest_task AS t ON qi.part = 'task' AND t.quest_id = qi.quest_id"
        "  AND t.idx = qi.idx"
        " LEFT JOIN item AS i ON i.id = qi.item_id LEFT JOIN fluid AS f ON f.id = qi.fluid_id"
        " WHERE qi.quest_id IN (SELECT value FROM json_each(?))"
        " ORDER BY qi.quest_id, qi.part = 'reward', qi.idx, qi.slot",
        (json_ids(ids),),
    ).fetchall()
    for qid, part, reg, meta, ore, fluid, amount, consume, name in rows:
        key = _key(reg, meta, fluid) or (f"ore:{ore}" if ore else None)
        if key is None:
            continue
        label = _clean(name) or (f"any ore:{ore}" if ore and reg is None else key)
        out[qid].append(
            Link(
                qid,
                part,
                key,
                label,
                amount,
                None if consume is None else bool(consume),
            )
        )
    return out


_FORMAT = re.compile(r"\u00a7.")  # Minecraft formatting codes in names


def _clean(name: str | None) -> str:
    return " ".join(_FORMAT.sub("", name).split()) if name else ""


def unlocks(conn: sqlite3.Connection, qid: str) -> list[str]:
    """The quests that list ``qid`` as a prerequisite."""
    return dependents(conn).get(qid, [])


# --- questlines -----------------------------------------------------------------------------


@dataclass
class LineSummary:
    id: str
    name: str
    order: int
    total: int
    done: int
    available: int
    locked: int
    frontier: list[tuple[str, str]]  # (id, name) of its frontier quests, book order
    main_left: int  # main quests not done yet
    src: str  # the quest load it was made from (kv quests.source's loaded_utc)

    @property
    def slug(self) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", self.name.casefold()).strip("-")
        return s[:60] or "line"


def lines(conn: sqlite3.Connection, player: str | None = None) -> list[LineSummary]:
    """Every quest line the book lists (D113's rule: never a HIDDEN line, a COMPLETED one once
    one of its quests is done), with its progress for the tracked player."""
    if player is None:
        player = quest_db.tracked_player(conn)
    src = _loaded(conn)
    front = defaultdict(list)
    for line_name, name, qid, _ in quest_db.frontier_rows(conn, player):
        front[line_name].append((qid, name))
    out = []
    for lid, name, ord_, vis in conn.execute(
        "SELECT id, name, ord, visibility FROM quest_line ORDER BY ord, id"
    ):
        rows = conn.execute(
            "SELECT coalesce(s.state, 'locked'), q.main FROM quest_line_entry AS e"
            " JOIN quest AS q ON q.id = e.quest_id"
            " LEFT JOIN quest_state AS s ON s.quest_id = q.id AND s.player_uuid = ?"
            " WHERE e.line_id = ?",
            (player, lid),
        ).fetchall()
        done = sum(1 for s, _ in rows if s in bq.COMPLETE)
        if vis == "HIDDEN" or (vis == "COMPLETED" and not done):
            continue
        available = sum(1 for s, _ in rows if s == bq.AVAILABLE)
        locked = sum(1 for s, _ in rows if s == bq.LOCKED)
        main_left = sum(1 for s, m in rows if m and s not in bq.COMPLETE)
        out.append(
            LineSummary(
                lid,
                name or lid,
                ord_ or 0,
                len(rows),
                done,
                available,
                locked,
                front.get(name or "", []),
                main_left,
                src,
            )
        )
    return out


def _loaded(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM kv WHERE key = ?", (quest_db.KV_SOURCE,)
    ).fetchone()
    if row is None:
        return ""
    try:
        value = json.loads(row[0])
    except ValueError:
        return ""
    return str(value.get("loaded_utc") or "") if isinstance(value, dict) else ""


def line_summary(conn: sqlite3.Connection, words: str) -> LineSummary | None:
    """The quest line ``words`` names: its name exactly (any case), else the one line whose name
    holds every word. None when no line or several match."""
    found = lines(conn)
    said = " ".join(words.casefold().split())
    exact = [s for s in found if s.name.casefold() == said]
    if len(exact) == 1:
        return exact[0]
    ws = _WORD.findall(said)
    near = [s for s in found if ws and all(w in s.name.casefold() for w in ws)]
    return near[0] if len(near) == 1 else None
