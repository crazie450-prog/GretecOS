"""The Designer (WO-3.7, plan §7.1, §7.14, D61, [QA:D13], D250-D252).

What the dashboard's Designer view, the line-designer skill's tools and ``gos plan`` share. Every
number is the planner's (:mod:`.plan`, ranked by :mod:`.rank`); nothing here computes one, and
the routes keep the order rank.py gives them.

- :func:`design`: solve a request, rank the routes and save each one (WO-3.6's
  :func:`gregtec.planner.outputs.store.save`: JSON, exports, the ``plan`` row, the vault note).
  The design's ids, in rank order, become the Designer's latest design
  (``data/plans/designer.json``), which the view opens with when its address names none.
- :func:`group`: a saved plan and its alternatives (the routes solved with it), in rank order.
- :func:`view`: what the Designer shows for a group: each route's summary (nodes with their
  item, machine, tier, count, pin and primary flags), the route comparison card with its
  differences marked, and each route's flow and BOM cards.
- :func:`alternatives`: a node's candidate recipes (D61's list, ``show_all`` for every one).
- :func:`pin`: pin a node of a saved plan to a recipe (or free it, ``recipe=None``) and re-solve:
  the pin is a fixed choice in the request, so it is in every resulting plan's JSON and holds
  through a reload and any re-solve (:func:`resolve`).
- :func:`accept`: the plan's lifecycle moves ``proposed -> accepted``; its vault note says so,
  ``base/lines/ln-<id>.md`` is written and the EU ledger hook runs (:mod:`.ledger`, a stub
  until WO-4.4). Accepting is Clayton's: the dashboard's button or ``gos plan accept``, never a
  model's tool (D251).
- :func:`rename`: a human plan name ([QA:D13]); the id stays for data. The row, the JSON, the
  exports and the note follow.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.agents import prefetch
from gregtec.agents.tools.glossary import Glossary
from gregtec.base.state import MyBase
from gregtec.cards import checked
from gregtec.cards.build import clip
from gregtec.db import access
from gregtec.packupdate.plans import PLAN_ID
from gregtec.planner import alternatives as alts
from gregtec.planner import graph, plan, rank, solve
from gregtec.planner.gtmath.tiers import VN
from gregtec.planner.ledger import ledger_update
from gregtec.planner.outputs import book as book_mod
from gregtec.planner.outputs import cards, store
from gregtec.planner.outputs.book import Book
from gregtec.planner.request import (
    RECIPE_KEY,
    Pin,
    Request,
    is_stable_key,
    parse_duration,
    parse_qty,
    tier_index,
)
from gregtec.vault.io import VaultError, VaultIO, atomic_write

LATEST = Path("data", "plans", "designer.json")  # in GOS_HOME
LINES = "base/lines"
NAME_MAX = 80
_LOCK = threading.Lock()  # one design, pin, accept or rename at a time per process

# The route comparison card's metrics, in the order the routes are ranked on (rank.METRICS),
# then the score; each says which way is better.
METRICS: tuple[tuple[str, str, str, str], ...] = (
    ("machines", "machines", "", "lower"),
    ("new_types", "new machine types", "", "lower"),
    ("eut_avg", "EU/t average", "EU/t", "lower"),
    ("eff_h", "effort", "h", "lower"),
    ("scarcity", "scarcity", "", "lower"),
    ("complexity", "complexity", "", "lower"),
    ("score", "score", "", "higher"),
)


class DesignerError(ValueError):
    """The request can't be done as asked; the message says why. Nothing was changed."""


@dataclass
class Designed:
    ids: list[str]  # rank order: the base routes, then the addon column
    plans: list[dict[str, Any]]
    problems: list[str] = field(default_factory=list)  # saved, but a vault note wasn't


def _db(home: Path) -> Path:
    db = access.db_path(home)
    if not db.is_file():
        raise DesignerError(f"{db} doesn't exist: run gos db build first")
    return db


@contextmanager
def _conn(home: Path, *, readonly: bool) -> Iterator[sqlite3.Connection]:
    c = access.connect(_db(home), readonly=readonly)
    try:
        yield c
    finally:
        c.close()


def _vault(home: Path) -> VaultIO | None:
    root = home / "vault"
    return VaultIO(root) if root.is_dir() else None


def _pid(text: str) -> str:
    pid = str(text).strip().removeprefix("plan:")
    if not PLAN_ID.fullmatch(pid) or not pid.startswith("pl-"):
        raise DesignerError(f"{text!r} isn't a plan id (pl-...)")
    return pid


def load(home: Path, pid: str) -> dict[str, Any]:
    pid = _pid(pid)
    try:
        return store.load(home, pid)
    except (OSError, ValueError) as exc:
        raise DesignerError(f"plan {pid} isn't saved") from exc


def resolve_target(home: Path, text: str) -> graph.Resolved | prefetch.Clarify:
    """The item or fluid ``text`` names (a stable key passes through), or the clarifying
    question (:class:`~gregtec.agents.prefetch.Clarify`) when it isn't clear."""
    db = _db(home)
    with _conn(home, readonly=True) as conn:
        if is_stable_key(text):
            return graph.Resolved(
                text, "fluid" if text.startswith("fluid:") else "item", text
            )
        try:
            return graph.resolve_target(conn, text, Glossary.find(home / "vault"), db)
        except graph.GraphError as exc:
            raise DesignerError(str(exc)) from None


SPEC_KEYS = frozenset(
    {"target", "rate", "batch", "within", "tier", "routes", "addons", "pins"}
)


def request_from_spec(
    home: Path, spec: Mapping[str, Any]
) -> Request | prefetch.Clarify:
    """A request from the Designer's form or ``design_production``'s spec: ``target`` (words
    or a key), ``rate`` ("2/s", "30/min") or ``batch`` (a quantity, with ``within`` "2h"),
    ``tier`` (LV..MAX, else My base's), ``routes`` (1-3), ``addons`` (bool), ``pins``
    ([{node, recipe}]). An unclear target gives the clarifying question instead."""
    unknown = set(spec) - SPEC_KEYS
    if unknown:
        raise DesignerError(f"unknown fields: {', '.join(sorted(unknown))}")
    target = spec.get("target")
    if not isinstance(target, str) or not target.strip():
        raise DesignerError("name a target item or fluid")
    found = resolve_target(home, target.strip())
    if isinstance(found, prefetch.Clarify):
        return found
    try:
        common: dict[str, Any] = {
            "tier_cap": tier_index(str(spec["tier"])) if spec.get("tier") else None,
            "machines": "base+addons" if spec.get("addons") is True else "base",
            "pins": tuple(
                Pin(str(p["node"]), str(p["recipe"])) for p in spec.get("pins") or ()
            ),
            "routes": int(spec.get("routes") or 3),
        }
        if spec.get("batch") not in (None, ""):
            if spec.get("rate") not in (None, ""):
                raise DesignerError("give a rate or a batch, not both")
            qty, unit = parse_qty(str(spec["batch"]))
            if unit is not None:
                raise DesignerError("a batch is a quantity, without /s, /min or /h")
            within = parse_duration(str(spec["within"])) if spec.get("within") else None
            return Request(found.key, mode="batch", qty=qty, within_s=within, **common)
        if spec.get("within"):
            raise DesignerError("within is for a batch only")
        qty, unit = parse_qty(str(spec.get("rate") or "1/s"))
        if unit is None:
            raise DesignerError("a rate needs /s, /min or /h")
        return Request(found.key, qty=qty, unit=unit, **common)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, DesignerError):
            raise
        raise DesignerError(str(exc)) from None


# --- design, save, re-solve --------------------------------------------------------------------


def _solve(conn: sqlite3.Connection, home: Path, request: Request) -> plan.Design:
    prefs = rank.read_prefs(home / "vault")
    try:
        return plan.design(conn, request, MyBase.from_db(conn), prefs=prefs)
    except solve.SolveError as exc:
        raise DesignerError(f"no route: {exc}") from None
    except (graph.GraphError, LookupError, ValueError) as exc:
        raise DesignerError(f"can't design it: {exc}") from None


def _save_all(home: Path, d: plan.Design, conn: sqlite3.Connection) -> Designed:
    out = Designed([], [])
    vault = _vault(home)
    with _conn(home, readonly=False) as rw:
        for p in d.plans:
            saved = store.save(home, p, rw, book_mod.read(conn, p), vault)
            out.ids.append(saved.id)
            out.problems.extend(saved.problems)
    out.plans = [store.load(home, pid) for pid in out.ids]  # names from the rows
    _remember(home, out.ids)
    return out


def design(home: Path, request: Request) -> Designed:
    """Solve, rank and save every route of ``request`` (see the module docstring)."""
    with _LOCK, _conn(home, readonly=True) as conn:
        return _save_all(home, _solve(conn, home, request), conn)


def resolve(home: Path, pid: str) -> Designed:
    """Re-solve a saved plan's request (its pins included): the same request on the same DB
    gives the same routes, ids and pins."""
    return design(home, Request.from_dict(load(home, pid)["request"]))


def pin(home: Path, pid: str, node: str, recipe: str | None) -> Designed:
    """Pin ``node`` of saved plan ``pid`` to ``recipe`` (``None`` frees it) and re-solve.
    The recipe must be one of the node's candidates that the graph didn't reject."""
    if not is_stable_key(node):
        raise DesignerError(f"{node!r} isn't an item or fluid key")
    if recipe is not None and not RECIPE_KEY.fullmatch(recipe):
        raise DesignerError(f"{recipe!r} isn't a recipe id (r:...)")
    request = Request.from_dict(load(home, pid)["request"])
    try:
        changed = (
            alts.unpin(request, node)
            if recipe is None
            else alts.pin(request, node, recipe)
        )
    except ValueError as exc:
        raise DesignerError(str(exc)) from None
    with _LOCK, _conn(home, readonly=True) as conn:
        d = _solve(conn, home, changed)
        if recipe is not None:
            try:
                found = alts.node_alternatives(d.graph, node, show_all=True)
            except KeyError:
                raise DesignerError(f"{node} isn't a node of this design") from None
            ok = {a["recipe"] for a in found if a["status"] != "rejected"}
            if recipe not in ok:
                why = next((a["reason"] for a in found if a["recipe"] == recipe), "")
                raise DesignerError(
                    f"{recipe} can't make {node} here"
                    + (f": {why}" if why else " (it isn't one of its recipes)")
                )
        return _save_all(home, d, conn)


def _remember(home: Path, ids: Sequence[str]) -> None:
    body = {"ids": list(ids), "ts": int(time.time())}
    atomic_write(home / LATEST, json.dumps(body, sort_keys=True).encode("utf-8"))


def latest(home: Path) -> list[str]:
    """The ids of the Designer's latest design, or none."""
    try:
        data = json.loads((home / LATEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ids = data.get("ids") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        return []
    return [i for i in ids if isinstance(i, str) and PLAN_ID.fullmatch(i)]


# --- what the view shows -----------------------------------------------------------------------


def _order_key(p: Mapping[str, Any]) -> tuple[int, int, str]:
    r = p.get("rank") or {}
    return (
        1 if r.get("column") == "addon" else 0,
        int(r.get("position") or 0),
        p["id"],
    )


def group(home: Path, pid: str) -> list[dict[str, Any]]:
    """``pid`` and the routes solved with it (its ``alternatives``) that are still saved, in
    rank order (the base routes by position, then the addon column)."""
    first = load(home, pid)
    plans = [first]
    for other in first.get("alternatives") or ():
        try:
            plans.append(store.load(home, other))
        except (OSError, ValueError):
            continue
    return sorted(plans, key=_order_key)


def _statuses(home: Path, ids: Sequence[str]) -> dict[str, str]:
    with _conn(home, readonly=True) as conn:
        found = conn.execute(
            "SELECT p.id, p.status FROM json_each(?) AS j JOIN plan AS p ON p.id = j.value",
            (json.dumps(list(ids)),),
        ).fetchall()
    return {str(r[0]): str(r[1] or "proposed") for r in found}


def _made(n: Mapping[str, Any]) -> str | None:
    made = [k for k in n.get("for") or () if k != graph.EU_KEY]
    return made[0] if made else None


def _node(n: Mapping[str, Any], b: Book) -> dict[str, Any]:
    m = n.get("machine") if isinstance(n.get("machine"), dict) else None
    item = _made(n)
    rec = b.recipe(n["recipe"])
    return {
        "n": n["n"],
        "item": item,
        "name": b.name(item) if item else n["recipe"],
        "recipe": n["recipe"],
        "map": rec.map_name if rec and rec.map_name else n.get("map"),
        "machine": m.get("name") if m else None,
        "mte": f"mte:{m['mte']}" if m and isinstance(m.get("mte"), int) else None,
        "tier": m.get("tier") if m else None,
        "count": n.get("count"),
        "pinned": bool(n.get("pinned")),
        "primary": bool(n.get("primary")),
    }


def _metric(p: Mapping[str, Any], key: str) -> float:
    m = p.get("metrics") or {}
    if key == "new_types":
        return float(len(m.get("new_types") or ()))
    if key == "complexity":
        return float((m.get("complexity") or {}).get("score") or 0)
    if key == "score":
        return float((p.get("rank") or {}).get("score") or 0)
    return float(m.get(key) or 0)


def comparison_card(
    plans: Sequence[Mapping[str, Any]],
    books: Mapping[str, Book],
    statuses: Mapping[str, str],
    prefs_line: str,
) -> dict[str, Any]:
    """The catalog's ``route_comparison`` card for up to 3 routes, in the order given (rank
    order). A metric is marked ``best`` on the routes that have the best value when the routes
    don't all tie; a choice ``differs`` when another route makes that item with another recipe
    or not at all."""
    shown = list(plans[:3])
    by_route: list[dict[str, str]] = []
    for p in shown:
        made: dict[str, str] = {}
        for n in p.get("nodes") or ():
            item = _made(n)
            if item is not None:
                made.setdefault(item, n["recipe"])
        by_route.append(made)
    routes = []
    for i, p in enumerate(shown):
        metrics = []
        for key, label, unit, better in METRICS:
            vals = [_metric(q, key) for q in shown]
            best = min(vals) if better == "lower" else max(vals)
            metrics.append(
                {
                    "key": key,
                    "label": label,
                    "value": _metric(p, key),
                    "unit": unit,
                    "better": better,
                    "best": len(set(vals)) > 1 and _metric(p, key) == best,
                }
            )
        b = books[p["id"]]
        choices = []
        for n in (p.get("nodes") or ())[:100]:
            item = _made(n)
            info = _node(n, b)
            differs = len(shown) > 1 and any(
                other.get(item or "") != n["recipe"]
                for j, other in enumerate(by_route)
                if j != i
            )
            choices.append(
                {
                    "node": clip(item or n["n"], 100),
                    "label": clip(info["name"], 300),
                    "machine": clip(info["machine"] or info["map"] or "crafting", 300),
                    "tier": cards._tier(info["tier"]),
                    "ref": clip(n["recipe"], 200),
                    "differs": differs,
                }
            )
        r = p.get("rank") or {}
        routes.append(
            {
                "id": p["id"],
                "name": clip(p["name"], 200),
                "rank": int(r.get("position") or i + 1),
                "ref": f"plan:{p['id']}",
                "accepted": statuses.get(p["id"], "proposed") != "proposed",
                "metrics": metrics,
                "choices": choices,
            }
        )
    notes = [
        prefs_line,
        "ranked by code (planner/rank.py); differences are marked on each route",
    ]
    addon = [p for p in shown if (p.get("rank") or {}).get("column") == "addon"]
    if addon:
        notes.append("the addon column is ranked against the base routes, shown last")
    title = shown[0]["name"] if shown else "Routes"
    return checked(
        {
            "type": "route_comparison",
            "title": clip(title.split(" · route ")[0], 300),
            "ref": f"plan:{shown[0]['id']}" if shown else None,
            "routes": routes,
            "notes": [clip(n, 2000) for n in notes],
        }
    )


def view(home: Path, pid: str) -> dict[str, Any]:
    """Everything the Designer shows for plan ``pid``'s group (see the module docstring)."""
    plans = group(home, pid)
    statuses = _statuses(home, [p["id"] for p in plans])
    books: dict[str, Book] = {}
    with _conn(home, readonly=True) as conn:
        for p in plans:
            try:
                books[p["id"]] = book_mod.read(conn, p)
            except LookupError as exc:
                raise DesignerError(f"plan {p['id']}: {exc}") from None
    prefs = rank.read_prefs(home / "vault")
    routes = []
    for p in plans:
        b = books[p["id"]]
        r = p.get("rank") or {}
        routes.append(
            {
                "id": p["id"],
                "name": p["name"],
                "status": statuses.get(p["id"], "proposed"),
                "column": r.get("column"),
                "position": r.get("position"),
                "of": r.get("of"),
                "score": r.get("score"),
                "target": {**p["target"], "name": b.name(p["target"]["id"])},
                "tier_cap": p["tier_cap"],
                "pins": p.get("pins") or [],
                "nodes": [_node(n, b) for n in p.get("nodes") or ()],
                "warnings": p.get("warnings") or [],
                "flow": cards.flow_card(p, b),
                "bom": cards.bom_card(p),
            }
        )
    return {
        "id": pid,
        "weights": rank.describe(prefs),
        "routes": routes,
        "comparison": comparison_card(plans, books, statuses, rank.describe(prefs)),
    }


def alternatives(
    home: Path, pid: str, node: str, show_all: bool = False
) -> dict[str, Any]:
    """Node ``node``'s candidate recipes in saved plan ``pid``'s graph (D61)."""
    p = load(home, pid)
    request = Request.from_dict(p["request"])
    with _conn(home, readonly=True) as conn:
        try:
            g = graph.build(conn, request, MyBase.from_db(conn))
        except (graph.GraphError, LookupError, ValueError) as exc:
            raise DesignerError(f"can't build the graph: {exc}") from None
        try:
            found = alts.node_alternatives(g, node, show_all)
        except KeyError:
            raise DesignerError(f"{node} isn't a node of this design") from None
        names = dict(
            conn.execute(
                "SELECT unloc_name, name_en FROM recipe_map WHERE name_en IS NOT NULL"
            ).fetchall()
        )
    in_graph = {r.id: r for r in g.recipes}
    used = {n["recipe"] for n in p.get("nodes") or () if node in (n.get("for") or ())}
    out = []
    for a in found:
        r = in_graph.get(a["recipe"])
        out.append(
            {
                **a,
                "map": names.get(r.map or "", r.map) if r else None,
                "tier": VN[r.tier] if r else None,
                "eut": r.eut if r else None,
                "duration_t": r.duration if r else None,
                "used": a["recipe"] in used,
            }
        )
    return {
        "plan": pid,
        "node": node,
        "name": g.item(node).name,
        "pinned": request.pin_for(node),
        "all": show_all,
        "alternatives": out,
    }


# --- accept and rename -------------------------------------------------------------------------


def line_id(pid: str) -> str:
    return "ln-" + _pid(pid).removeprefix("pl-")


def line_body(p: Mapping[str, Any], status: str) -> str:
    """``base/lines/ln-<id>.md``'s body: the line's facts, every number the plan's."""
    m = p.get("metrics") or {}
    facts: list[tuple[str, object]] = [
        ("st", status),
        ("plan", f"plan:{p['id']}"),
        ("tgt", p["target"]["id"]),
        ("tgt_rate_s", p["target"]["rate_s"]),
        ("eut", m.get("eut_avg")),
        ("tier", p["tier_cap"]),
        ("note", p["name"]),
        ("note", f"plan note [[{p['id']}]], json {store.json_rel(p['id'])}"),
    ]
    return "".join(f"{k}: {store._fact(v)}\n" for k, v in facts if v not in (None, ""))


@dataclass
class Accepted:
    id: str
    status: str
    line: str | None  # the vault path written
    note: str | None
    ledger: dict[str, Any]
    problems: list[str] = field(default_factory=list)


def _notes(
    home: Path, p: Mapping[str, Any], status: str, problems: list[str]
) -> tuple[str | None, str | None]:
    """Rewrite the plan note with ``status`` and write its line note when accepted or later."""
    vault = _vault(home)
    if vault is None:
        problems.append("no vault: the notes weren't written")
        return None, None
    with _conn(home, readonly=True) as conn:
        b = book_mod.read(conn, p)
    note = line = None
    try:
        note = vault.write(f"plans/{p['id']}.md", store.note_body(p, b, status))
    except (VaultError, OSError) as exc:
        problems.append(f"the plan note wasn't written: {exc}")
    if status != "proposed":
        try:
            line = vault.write(f"{LINES}/{line_id(p['id'])}.md", line_body(p, status))
        except (VaultError, OSError) as exc:
            problems.append(f"the line note wasn't written: {exc}")
    return note, line


def accept(home: Path, pid: str) -> Accepted:
    """Accept saved plan ``pid`` (Clayton's action; see the module docstring)."""
    p = load(home, pid)
    with _LOCK:
        with _conn(home, readonly=False) as rw:
            try:
                store.set_status(rw, p["id"], "accepted")
            except ValueError as exc:
                raise DesignerError(str(exc)) from None
        problems: list[str] = []
        note, line = _notes(home, p, "accepted", problems)
        entry = ledger_update(p, "accepted")
    return Accepted(p["id"], "accepted", line, note, entry.as_dict(), problems)


def clean_name(text: object) -> str:
    if not isinstance(text, str):
        raise DesignerError("a name is text")
    name = " ".join(text.split())
    if not name:
        raise DesignerError("a name can't be empty")
    if len(name) > NAME_MAX:
        raise DesignerError(f"a name is at most {NAME_MAX} characters")
    if any(ord(c) < 0x20 or c in "[]|`" for c in name):
        raise DesignerError("a name can't hold [ ] | or `")
    return name


def rename(home: Path, pid: str, name: object) -> dict[str, Any]:
    """Give saved plan ``pid`` a human name ([QA:D13]); returns the plan as saved."""
    new = clean_name(name)
    p = load(home, pid)
    with _LOCK:
        with _conn(home, readonly=False) as rw:
            rw.execute("UPDATE plan SET name = ? WHERE id = ?", (new, p["id"]))
            status = str(
                rw.execute(
                    "SELECT status FROM plan WHERE id = ?", (p["id"],)
                ).fetchone()[0]
                or "proposed"
            )
            with _conn(home, readonly=True) as conn:
                b = book_mod.read(conn, p)
            # The row's name wins in save (WO-3.6): the JSON, the exports and the note follow.
            store.save(home, {**p, "name": new}, rw, b, None)
        _notes(home, {**p, "name": new}, status, [])
    return store.load(home, p["id"])
