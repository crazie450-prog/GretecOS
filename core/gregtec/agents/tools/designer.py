"""The line-designer skill's tools (WO-3.7, plan 7.0, §7.1, D61, D251).

- ``design_production(spec)``: solve a production request and save every route
  (:func:`gregtec.planner.designer.design`); the routes come back **in rank order** (planner
  rank.py, never a model's), one row each, with the weights and the templated explanation built
  from the metrics. An unclear target returns the candidates to ask about instead.
- ``plan_get(id, view)``: a saved plan: ``summary`` (its facts and metrics), ``nodes`` (one row
  per machine node: item, recipe, machine, tier, count, pinned, primary), ``routes`` (its design's
  routes side by side in rank order, the differing choices named) or ``alternatives`` with
  ``node`` (that node's candidate recipes, D61).
- ``plan_pin(id, node, recipe?)``: pin one node to a candidate recipe and re-solve (``unpin``
  frees it); the new routes come back as ``design_production``'s do.
- ``plan_list(status?)``: the saved plans, newest first.
- ``plan_rename(id, name)``: a human name ([QA:D13]).

Accepting a plan isn't a tool: it is Clayton's, in the Designer or ``gos plan accept`` (D251).
Every number in a row is the planner's, cited by the plan (``plan:<id>``) or the recipe (``r:``).
The writing tools (design, pin, rename) save only through the planner's store (``data/plans``
and the vault's ``plans/`` through the vault path guard) and are the deep lane's only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gregtec.agents import prefetch
from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result

GATE = (
    "the planner's store (gregtec.planner.outputs.store: data/plans, the plan row and the "
    "vault's plans/ through the vault path guard)"
)
VIEWS = ("summary", "nodes", "routes", "alternatives")
STATUSES = ("proposed", "accepted", "building", "running", "retired")

_ID = {
    "type": "string",
    "description": "the plan's id (pl-..., plan: optional)",
    "minLength": 1,
    "maxLength": 80,
}
_NODE = {
    "type": "string",
    "description": "the node's item or fluid key (registry@meta or fluid:<name>), as the "
    "nodes view or design_production names it",
    "minLength": 3,
    "maxLength": 300,
}

DESIGN_PRODUCTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": "the item or fluid to make: Clayton's words or its key",
            "minLength": 1,
            "maxLength": 200,
        },
        "rate": {
            "type": "string",
            "description": 'a rate: "2/s", "30/min", "100/h" (default 1/s)',
            "maxLength": 40,
        },
        "batch": {
            "type": "string",
            "description": "a batch quantity instead of a rate (items or L in all)",
            "maxLength": 40,
        },
        "within": {
            "type": "string",
            "description": 'with batch: the deadline, "90s", "30min", "2h"',
            "maxLength": 40,
        },
        "tier": {
            "type": "string",
            "description": "tier cap (LV, MV, HV, ...); default My base's tier",
            "maxLength": 8,
        },
        "routes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
            "description": "how many ranked routes, 1 to 3 (default 3)",
        },
        "addons": {
            "type": "boolean",
            "description": "also solve the best route on addon machines (its own column)",
        },
    },
    "required": ["target"],
    "additionalProperties": False,
}
PLAN_GET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": _ID,
        "view": {
            "type": "string",
            "enum": list(VIEWS),
            "description": "summary, nodes, routes or alternatives (with node)",
        },
        "node": {
            **_NODE,
            "description": "with view alternatives: " + _NODE["description"],
        },
        "all": {
            "type": "boolean",
            "description": "with view alternatives: every candidate, rejected ones too",
        },
    },
    "required": ["id", "view"],
    "additionalProperties": False,
}
PLAN_PIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": _ID,
        "node": _NODE,
        "recipe": {
            "type": "string",
            "description": "the r: id of one of the node's candidate recipes "
            "(plan_get view alternatives)",
            "minLength": 3,
            "maxLength": 200,
        },
        "unpin": {"type": "boolean", "description": "free the node instead"},
    },
    "required": ["id", "node"],
    "additionalProperties": False,
}
PLAN_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": list(STATUSES),
            "description": "only the plans with this status",
        }
    },
    "additionalProperties": False,
}
PLAN_RENAME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": _ID,
        "name": {
            "type": "string",
            "description": 'a short human name, e.g. "Steel line · 2/s · MV"',
            "minLength": 1,
            "maxLength": 80,
        },
    },
    "required": ["id", "name"],
    "additionalProperties": False,
}

ROUTE_COLS = (
    "rank",
    "plan",
    "name",
    "column",
    "score",
    "machines",
    "new_types",
    "eut_avg",
    "eff_h",
    "scarcity",
    "complexity",
    "differs",
)


def _home(call: Call) -> Path:
    return call.env.db.parent.parent  # GOS_HOME/data/gos.sqlite


def _designer() -> Any:  # noqa: ANN401 - the module, imported on first use (the planner is heavy)
    from gregtec.planner import designer

    return designer


def _guard(fn: Any, *args: object) -> Any:  # noqa: ANN401
    d = _designer()
    try:
        return fn(*args)
    except d.DesignerError as exc:
        raise ToolError(str(exc)) from None


def _routes(home: Path, pid: str, call: Call, offset: int, head: list[tuple[str, object]],
            notes: list[str]) -> Result:  # fmt: skip
    d = _designer()
    view = _guard(d.view, home, pid)
    card = view["comparison"]
    differs = {
        r["id"]: ", ".join(
            f"{c['label']} ({c['ref']})" for c in r["choices"] if c["differs"]
        )
        for r in card["routes"]
    }
    rows = []
    for r in view["routes"]:
        m = {x["key"]: x["value"] for x in _metrics(card, r["id"])}
        rows.append(
            (
                r["position"] if r["column"] != "addon" else "addon",
                f"plan:{r['id']}",
                r["name"],
                r["column"],
                r["score"],
                m.get("machines"),
                m.get("new_types"),
                m.get("eut_avg"),
                m.get("eff_h"),
                m.get("scarcity"),
                m.get("complexity"),
                differs.get(r["id"]) or "-",
            )
        )
    page = rows[offset : offset + call.page]
    return Result(
        ROUTE_COLS,
        list(page),
        head=[*head, ("weights", view["weights"])],
        notes=[
            "routes are in rank order from code (planner/rank.py): explain it, never reorder",
            *notes,
        ],
        total=len(rows),
        offset=offset,
        more=offset + call.page < len(rows),
    )


def _metrics(card: dict[str, Any], pid: str) -> list[dict[str, Any]]:
    return next((r["metrics"] for r in card["routes"] if r["id"] == pid), [])


def _designed(call: Call, d: Any, offset: int) -> Result:  # noqa: ANN401
    """The routes of a fresh design (``design_production`` and ``plan_pin``)."""
    from gregtec.planner import explain, rank

    home = _home(call)
    plans = d.plans
    target = plans[0]["target"]["id"] if plans else ""
    view = _guard(_designer().view, home, d.ids[0])
    name = view["routes"][0]["target"]["name"] if view["routes"] else target
    doc = explain.facts(plans, name, rank.read_prefs(home / "vault"))
    head: list[tuple[str, object]] = [
        ("target", f"{name} [{target}]"),
        ("design", ", ".join(f"plan:{i}" for i in d.ids)),
    ]
    notes = [f"saved: {p}" for p in d.problems]
    notes.append("explanation (template from the metrics): " + explain.template(doc))
    notes.append(
        "Clayton compares, pins and accepts in the Designer (dashboard key 4); "
        "accepting is his, not a tool's"
    )
    return _routes(home, d.ids[0], call, offset, head, notes)


def design_production(call: Call, args: dict[str, Any], offset: int) -> Result:
    d = _designer()
    home = _home(call)
    req = _guard(d.request_from_spec, home, args)
    if isinstance(req, prefetch.Clarify):
        return Result(
            ("n", "key", "name"),
            [(c["n"], c["key"], c["name"]) for c in req.card()["candidates"]],
            head=[("ambiguous", 1), ("question", req.question)],
            notes=["ambiguous: ask Clayton which one, then design with its key"],
        )
    return _designed(call, _guard(d.design, home, req), offset)


def _pid(text: str) -> str:
    return str(text).strip().removeprefix("plan:")


def plan_get(call: Call, args: dict[str, Any], offset: int) -> Result:
    d = _designer()
    home = _home(call)
    pid = _pid(args["id"])
    view = args["view"]
    cite = f"plan:{pid}"
    if view == "routes":
        return _routes(home, pid, call, offset, [("plan", cite)], [])
    if view == "alternatives":
        node = args.get("node")
        if not node:
            raise ToolError("view alternatives needs node (the item or fluid key)")
        found = _guard(d.alternatives, home, pid, node, bool(args.get("all")))
        rows = [
            (
                a["recipe"],
                a["status"],
                "yes" if a["used"] else "",
                a["map"],
                a["tier"],
                a["eut"],
                a["duration_t"],
                a["reason"],
            )
            for a in found["alternatives"]
        ]
        page = rows[offset : offset + call.page]
        return Result(
            ("recipe", "status", "used", "machine", "tier", "eut", "dur_t", "why"),
            list(page),
            head=[
                ("plan", cite),
                ("node", f"{found['name']} [{node}]"),
                ("pinned", found["pinned"] or "none"),
            ],
            notes=["pin one with plan_pin(id, node, recipe); cite each by its r: id"],
            total=len(rows),
            offset=offset,
            more=offset + call.page < len(rows),
        )
    v = _guard(d.view, home, pid)
    route = next((r for r in v["routes"] if r["id"] == pid), None)
    if route is None:  # pragma: no cover - view always holds the plan itself
        raise ToolError(f"plan {pid} isn't in its own design")
    if view == "nodes":
        rows = [
            (
                n["n"],
                n["name"],
                n["item"],
                n["recipe"],
                n["machine"] or n["map"] or "crafting",
                n["mte"],
                n["tier"],
                n["count"],
                "yes" if n["pinned"] else "",
                "yes" if n["primary"] else "",
            )
            for n in route["nodes"]
        ]
        page = rows[offset : offset + call.page]
        return Result(
            ("n", "item", "key", "recipe", "machine", "mte", "tier", "count", "pinned",
             "primary"),
            list(page),
            head=[("plan", cite), ("name", route["name"])],
            notes=[f"counts are the planner's; cite {cite} and each r: id"],
            total=len(rows),
            offset=offset,
            more=offset + call.page < len(rows),
        )  # fmt: skip
    p = d.load(home, pid)
    m = p.get("metrics") or {}
    head: list[tuple[str, object]] = [
        ("plan", cite),
        ("name", route["name"]),
        ("status", route["status"]),
        ("target", f"{route['target']['name']} [{p['target']['id']}]"),
        ("rate_s", p["target"]["rate_s"]),
        ("tier_cap", p["tier_cap"]),
        ("rank", f"{route['position']} of {route['of']} ({route['column']})"),
        ("score", route["score"]),
        ("machines", m.get("machines")),
        ("new_types", len(m.get("new_types") or ())),
        ("eut_avg", m.get("eut_avg")),
        ("eut_peak", m.get("eut_peak")),
        ("eff_h", m.get("eff_h")),
        ("scarcity", m.get("scarcity")),
        ("pins", ", ".join(f"{x['node']}={x['recipe']}" for x in route["pins"]) or "none"),
        ("alternatives", ", ".join(f"plan:{a}" for a in p.get("alternatives") or ()) or "none"),
    ]  # fmt: skip
    return Result(
        head=head,
        notes=[*route["warnings"][:10], f"every number is the planner's; cite {cite}"],
    )


def plan_pin(call: Call, args: dict[str, Any], offset: int) -> Result:
    d = _designer()
    recipe = args.get("recipe")
    if args.get("unpin"):
        if recipe:
            raise ToolError("give recipe or unpin, not both")
        recipe = None
    elif not recipe:
        raise ToolError("give the recipe to pin (plan_get view alternatives), or unpin")
    home = _home(call)
    return _designed(
        call, _guard(d.pin, home, _pid(args["id"]), args["node"], recipe), offset
    )


def plan_list(call: Call, args: dict[str, Any], offset: int) -> Result:
    from gregtec.agents import saved_plans

    found, problems = saved_plans.load(call.env.db, _home(call))
    want = args.get("status")
    rows = [
        (p.name, f"plan:{p.id}", p.kind or "", p.status)
        for p in found
        if want is None or p.status == want
    ]
    page = rows[offset : offset + call.page]
    return Result(
        ("name", "plan", "kind", "status"),
        list(page),
        notes=[*problems[:5], "newest first"],
        total=len(rows),
        offset=offset,
        more=offset + call.page < len(rows),
    )


def plan_rename(call: Call, args: dict[str, Any], offset: int) -> Result:
    d = _designer()
    out = _guard(d.rename, _home(call), _pid(args["id"]), args["name"])
    return Result(head=[("plan", f"plan:{out['id']}"), ("name", out["name"])])
