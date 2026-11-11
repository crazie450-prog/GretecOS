"""Affected-plan flags (WO-1.9, plan sections 5.2 step 3 and 7.13, QA:D9, D147).

What this needs of a plan is fixed by Appendix D's ``plan`` table: a plan names the export run
it was solved on (``plan.run_id``) and cites recipe keys; its JSON (``plan.json_path``,
``data/plans/<id>.json``) lists them. Two JSON shapes are read:

- the canonical plan JSON of plan 7.1 (WO-3.4, ``gregtec.planner.plan``, ``v`` 1): its ``run``
  is the export run key, and its cites are the ``r:`` recipes of its nodes and pins and the
  ``mte:`` machines of its nodes (``gregtec.planner.plan.cites``, re-read here so a pack
  update needn't load the solver); its status lives in the DB row, so it reads as "";
- ``gos-plan/0``, the stand-in that fixtures and dry runs still use::

    {"format": "gos-plan/0", "id": "pl-0001", "name": "Steel", "status": "accepted",
     "run": "<export run key>", "cites": ["r:gt.recipe.blastfurnace/0123456789ab", "crop:..."]}

A plan is **affected** by a new run when a key it cites is missing from the new run (removed) or
has another content hash there than in the plan's own run (changed: a rebalance keeps the key,
plan 5.2 step 3). ``crop:`` keys are compared on the crop's data the same way. Other citations
(``q:``, ``w:``, ``plan:``) don't come from exports and are left alone. A cited key that the
plan's own run doesn't have is flagged too, as "not in its own run": the plan can't be checked.
Every plan is checked, whatever its status, so a draft that cites a removed recipe shows it too.

:func:`mark_affected` records a flag the way Appendix D does: ``plan.affected_run`` = the new
run's ``export_run.id``. The playbook writes it only for a run in the main DB (Play's adopted
export); a Lab run's flags are a preview in the update's report.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gregtec.db import crops as crop_db
from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest import crops

FORMAT = "gos-plan/0"
PLAN_V = 1  # the canonical plan JSON's version (gregtec.planner.plan.VERSION)
PLAN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
RECIPE, CROP = "r:", "crop:"
MAX_PLAN = 4 << 20  # bytes


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    status: str
    run: str  # the export run key it was solved on
    cites: tuple[str, ...]


def plan_cites(d: dict[str, object]) -> list[str]:
    """The cites of a canonical plan: ``r:`` recipes of its nodes and pins, ``mte:`` machines
    of its nodes, in order, each once (the same as ``gregtec.planner.plan.cites``)."""
    out: list[str] = []
    nodes = d.get("nodes")
    for node in nodes if isinstance(nodes, list) else ():
        if isinstance(node, dict):
            if isinstance(node.get("recipe"), str):
                out.append(node["recipe"])
            machine = node.get("machine")
            if isinstance(machine, dict) and isinstance(machine.get("mte"), int):
                out.append(f"mte:{machine['mte']}")
    pins = d.get("pins")
    for pin in pins if isinstance(pins, list) else ():
        if isinstance(pin, dict) and isinstance(pin.get("recipe"), str):
            out.append(pin["recipe"])
    return list(dict.fromkeys(out))


def parse(data: bytes, where: str) -> Plan:
    """A canonical plan or a ``gos-plan/0`` document; ValueError when it is neither."""
    try:
        d = json.loads(data)
    except ValueError as exc:
        raise ValueError(f"{where} isn't JSON: {exc}") from None
    canonical = (
        isinstance(d, dict)
        and "format" not in d
        and d.get("v") == PLAN_V
        and isinstance(d.get("nodes"), list)
    )
    if not isinstance(d, dict) or not (canonical or d.get("format") == FORMAT):
        raise ValueError(f"{where} isn't a {FORMAT} plan or a v{PLAN_V} plan")
    if canonical:
        d = {**d, "status": "", "cites": plan_cites(d)}
    pid, run, cites = d.get("id"), d.get("run"), d.get("cites")
    if not isinstance(pid, str) or not PLAN_ID.fullmatch(pid):
        raise ValueError(f"{where}: id must match {PLAN_ID.pattern}")
    if not isinstance(run, str) or not run:
        raise ValueError(f"{where}: run must name the export run it was solved on")
    if not isinstance(cites, list) or not all(isinstance(c, str) for c in cites):
        raise ValueError(f"{where}: cites must be a list of citation keys")
    return Plan(
        pid,
        str(d.get("name") or pid),
        str(d.get("status") or ""),
        run,
        tuple(dict.fromkeys(cites)),
    )


def read_plan(path: Path) -> Plan:
    data = read_bytes(path)
    if len(data) > MAX_PLAN:
        raise ValueError(f"{path} is over {MAX_PLAN >> 20} MiB")
    return parse(data, str(path))


def read_folder(folder: Path) -> tuple[list[Plan], list[str]]:
    """Every ``*.json`` plan in ``folder``, and why any other one didn't read."""
    plans: list[Plan] = []
    problems: list[str] = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        try:
            plans.append(read_plan(path))
        except (OSError, ValueError) as exc:
            problems.append(f"plan {path.name} not checked: {exc}")
    return plans, problems


def from_db(conn: sqlite3.Connection, home: Path) -> tuple[list[Plan], list[str]]:
    """The plans in the ``plan`` table, their cites from each one's JSON (``json_path``, from
    ``home`` when relative). A row whose JSON doesn't read is a problem, not a plan."""
    plans: list[Plan] = []
    problems: list[str] = []
    for pid, path, run_key in conn.execute(
        "SELECT p.id, p.json_path, e.run_key FROM plan AS p"
        " LEFT JOIN export_run AS e ON e.id = p.run_id ORDER BY p.id"
    ):
        if not path:
            problems.append(f"plan {pid} not checked: it has no json_path")
            continue
        full = Path(path) if Path(path).is_absolute() else home / path
        try:
            plan = read_plan(full)
        except (OSError, ValueError) as exc:
            problems.append(f"plan {pid} not checked: {exc}")
            continue
        if plan.id != pid:
            problems.append(f"plan {pid} not checked: {full} is plan {plan.id}")
            continue
        if run_key is not None and run_key != plan.run:
            problems.append(
                f"plan {pid}: its row's run is {run_key}, its JSON's {plan.run}; "
                f"checked against {run_key}"
            )
            plan = Plan(plan.id, plan.name, plan.status, str(run_key), plan.cites)
        plans.append(plan)
    return plans, problems


class RunView:
    """What one export run says about the keys plans cite. Loads each kind once, on use."""

    def __init__(self, conn: sqlite3.Connection, run_id: int, run_key: str) -> None:
        self.conn, self.run_id, self.run_key = conn, run_id, run_key
        self._hashes: dict[str, str] | None = None
        self._crops: crops.CropData | None = None

    def recipe(self, ref: str) -> str | None:
        """The content hash of ``ref`` in this run, or None."""
        if self._hashes is None:
            self._hashes = {
                str(r): str(h)
                for r, h in self.conn.execute(
                    "SELECT ref, content_hash FROM recipe WHERE run_id = ?",
                    (self.run_id,),
                )
            }
        return self._hashes.get(ref)

    def crop_data(self) -> crops.CropData:
        if self._crops is None:
            self._crops = crop_db.read(self.conn, self.run_id)
        return self._crops


def view(conn: sqlite3.Connection, run_key: str) -> RunView | None:
    row = conn.execute(
        "SELECT id FROM export_run WHERE run_key = ?", (run_key,)
    ).fetchone()
    return None if row is None else RunView(conn, int(row[0]), run_key)


@dataclass(frozen=True)
class Hit:
    cite: str
    why: str


@dataclass
class Flagged:
    plan: Plan
    hits: list[Hit] = field(default_factory=list)


def _crop_changes(old: RunView, new: RunView) -> tuple[set[str], set[str]]:
    """(removed, changed) crop ids from ``old`` to ``new``."""
    d = crops.diff(old.crop_data(), new.crop_data())
    return set(d.crops_removed), {c.key for c in d.crops_changed}


def check(
    plans: Iterable[Plan],
    run_of: Callable[[str], RunView | None],
    new: RunView,
) -> list[Flagged]:
    """The plans affected by ``new``, each with its hits. ``run_of`` gives the run a
    plan was solved on; a plan whose run is gone is checked against nothing and flagged."""
    flagged: list[Flagged] = []
    crop_cache: dict[str, tuple[set[str], set[str]]] = {}
    for plan in plans:
        own = run_of(plan.run)
        hits: list[Hit] = []
        if own is None:
            hits = [
                Hit(c, f"its run {plan.run} isn't in the DB, so it can't be checked")
                for c in plan.cites
                if c.startswith((RECIPE, CROP))
            ]
        else:
            for cite in plan.cites:
                if cite.startswith(RECIPE):
                    was, now = own.recipe(cite), new.recipe(cite)
                    if was is None:
                        hits.append(Hit(cite, f"not in its own run {plan.run}"))
                    elif now is None:
                        hits.append(Hit(cite, f"removed in {new.run_key}"))
                    elif now != was:
                        hits.append(Hit(cite, f"changed in {new.run_key}"))
                elif cite.startswith(CROP):
                    cid = cite[len(CROP) :]
                    if plan.run not in crop_cache:
                        crop_cache[plan.run] = _crop_changes(own, new)
                    removed, changed = crop_cache[plan.run]
                    if cid not in own.crop_data().crops:
                        hits.append(Hit(cite, f"not in its own run {plan.run}"))
                    elif cid in removed:
                        hits.append(Hit(cite, f"removed in {new.run_key}"))
                    elif cid in changed:
                        hits.append(Hit(cite, f"changed in {new.run_key}"))
        if hits:
            flagged.append(Flagged(plan, hits))
    return flagged


def lines(
    flagged: Sequence[Flagged], checked: int, problems: Sequence[str]
) -> list[str]:
    if not checked and not problems:
        return ["Plans: none to check"]
    out = [
        f"Plans: {checked} checked, {len(flagged)} affected by the update"
        + (":" if flagged else "")
    ]
    for f in flagged:
        out.append(
            f"  ! plan:{f.plan.id} ({f.plan.name}, {f.plan.status or 'no status'}, "
            f"run {f.plan.run}): affected by update"
        )
        out += [f"      {h.cite}: {h.why}" for h in f.hits[:20]]
        if len(f.hits) > 20:
            out.append(f"      ... and {len(f.hits) - 20} more")
    out += [f"  note: {p}" for p in problems]
    return out


def mark_affected(
    conn: sqlite3.Connection, plan_ids: Sequence[str], run_id: int
) -> int:
    """Set ``plan.affected_run`` for ``plan_ids`` in one transaction; rows changed."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        n = 0
        for pid in plan_ids:
            n += conn.execute(
                "UPDATE plan SET affected_run = ? WHERE id = ?", (run_id, pid)
            ).rowcount
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return n


def fixture(conn: sqlite3.Connection, run_id: int, run_key: str, spec: str) -> Plan:
    """A ``gos-plan/0`` fixture plan for a dry run: ``<id>=<registry>@<meta>`` cites every
    recipe of the run with that item among its outputs; ``<id>=crop:<crop id>`` cites the crop.
    ValueError when the spec is malformed or cites nothing."""
    pid, sep, what = spec.partition("=")
    if not sep or not PLAN_ID.fullmatch(pid):
        raise ValueError(
            f"{spec!r}: write <plan id>=<registry>@<meta> or <plan id>=crop:<id>"
        )
    if what.startswith(CROP):
        row = conn.execute(
            "SELECT 1 FROM crop WHERE run_id = ? AND id = ?",
            (run_id, what[len(CROP) :]),
        ).fetchone()
        cites = [what] if row else []
    else:
        registry, at, meta = what.rpartition("@")
        if not at or not registry or not meta.isdigit():
            raise ValueError(
                f"{spec!r}: the item is <registry>@<meta>, e.g. minecraft:chest@0"
            )
        cites = [
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT r.ref FROM recipe AS r"
                " JOIN recipe_output AS o ON o.recipe_id = r.id"
                " JOIN item AS i ON i.id = o.item_id"
                " WHERE r.run_id = ? AND i.registry_name = ? AND i.meta = ? ORDER BY r.ref",
                (run_id, registry, int(meta)),
            )
        ]
    if not cites:
        raise ValueError(f"{spec!r}: run {run_key} has nothing to cite for {what}")
    return Plan(pid, f"fixture: {what}", "fixture", run_key, tuple(cites))


def dumps(plan: Plan) -> bytes:
    return json.dumps(
        {
            "format": FORMAT,
            "id": plan.id,
            "name": plan.name,
            "status": plan.status,
            "run": plan.run,
            "cites": list(plan.cites),
        },
        indent=1,
    ).encode("utf-8")
