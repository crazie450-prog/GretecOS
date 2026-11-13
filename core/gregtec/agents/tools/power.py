"""The power-designer skill's tools (WO-4.6, plan 7.0, §7.2, D13, D67, D244).

- ``design_power(mode, tier?, scale?, bound?, kinds?, fuels?, rotor?, options?)``: the power
  designer (:func:`gregtec.planner.powerview.design`): 2-3 power lines for one of the four
  objective modes, **in rank order** (planner/rank.py's ``order_power``, never a model's), each
  saved as a proposed power plan (``plan:<id>``), one row each with its net EU/t (= what its
  generator gives less what its own fuel chain uses), score, effort, upkeep and the validator's
  verdict, the weights printed in the head and the templated explanation in the notes.
- ``effort_estimate(id)``: a saved plan's effort breakdown (WO-4.3, D17, D237): one row per
  recipe (part, map, machine, recipe, crafts, machines, hours) with the totals in the head.
- ``plan_explain`` (:mod:`.designer`) explains a power design too: the planner model writes from
  the metrics only, the numeric validator and the order check
  (:func:`gregtec.planner.power_explain.in_order`) refuse a text that adds a number or reorders
  the options.

Accepting a line isn't a tool: it is Clayton's, in the Power view or ``gos power design ...
--accept N`` (D251, D238). Every number in a row is the planner's, cited by its plan (``plan:``),
recipe (``r:``) or machine (``mte:``). ``design_power`` saves only through the planner's store
(``data/plans`` and the ``plan`` row) and is the deep lane's only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result

GATE = "the planner's store (gregtec.planner.outputs.store.save_power: data/plans, the plan row)"
MODES = ("best", "max_output", "output_per_effort", "self_sustaining")
KINDS = ("single_block", "gas_turbine", "plasma_turbine", "boiler_steam")

DESIGN_POWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": list(MODES),
            "description": "best (the weighted score), max_output (net EU/t within the scale "
            "bound), output_per_effort (net EU/t per effort hour) or self_sustaining "
            "(nothing from outside the free set, proven by the validator)",
        },
        "tier": {
            "type": "string",
            "description": "tier cap (LV, MV, HV, ...); default My base's tier",
            "maxLength": 8,
        },
        "scale": {
            "type": "string",
            "enum": ["unit", "generators", "eut_cap", "raw_budget"],
            "description": "max_output's scale bound: one build unit (default), a generator "
            "count, a net EU/t cap or a raw-input budget per hour (with bound)",
        },
        "bound": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "the scale's bound: a count, EU/t or inputs per hour",
        },
        "kinds": {
            "type": "array",
            "items": {"type": "string", "enum": list(KINDS)},
            "maxItems": 4,
            "description": "only these generator kinds",
        },
        "fuels": {
            "type": "array",
            "items": {"type": "string", "minLength": 3, "maxLength": 200},
            "maxItems": 8,
            "description": "only these fuels, by key (item_resolve): fluid:<name> or "
            "registry@meta",
        },
        "options": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
            "description": "how many ranked options, 1 to 3 (default 3)",
        },
    },
    "required": ["mode"],
    "additionalProperties": False,
}
EFFORT_ESTIMATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "a saved plan's id (pl-... or pw-..., plan: optional)",
            "minLength": 1,
            "maxLength": 80,
        }
    },
    "required": ["id"],
    "additionalProperties": False,
}
OPTION_COLS = (
    "rank",
    "plan",
    "generator",
    "fuel",
    "net_eut",
    "gross_eut",
    "chain_eut",
    "tier",
    "amps",
    "score",
    "eut_per_effort_h",
    "eff_h",
    "upkeep_h",
    "closed",
)


def _home(call: Call) -> Path:
    return call.env.db.parent.parent  # GOS_HOME/data/gos.sqlite


def _guard(fn: Any, *args: object, **kw: object) -> Any:  # noqa: ANN401
    from gregtec.planner import designer

    try:
        return fn(*args, **kw)
    except designer.DesignerError as exc:
        raise ToolError(str(exc)) from None


def option_rows(plans: list[dict[str, Any]]) -> list[tuple[object, ...]]:
    """One row per option, in the plans' (rank) order."""
    rows = []
    for p in plans:
        pw, r, m = p["power"], p["rank"], p.get("metrics") or {}
        dist = pw.get("distribution") or {}
        rows.append(
            (
                r["position"],
                f"plan:{p['id']}",
                pw["generator"]["label"],
                pw["fuel"]["id"],
                pw["net_eut"],
                pw["gross_eut"],
                pw["chain_eut"],
                dist.get("tier"),
                dist.get("amps"),
                r["score"],
                r.get("eut_per_effort_h"),
                m.get("eff_h"),
                m.get("upkeep"),
                "yes" if (p.get("closed") or {}).get("ok") else "no",
            )
        )
    return rows


def design_power(call: Call, args: dict[str, Any], offset: int) -> Result:
    from gregtec.planner import power_explain, powerview

    home = _home(call)
    req = _guard(powerview.request_from_spec, args)
    d = _guard(powerview.design, home, req)
    rows = option_rows(d.plans)
    doc = power_explain.facts(d.plans)
    notes = [
        "options are in rank order from code (planner/rank.py): explain it, never reorder",
        "net_eut = gross_eut (the generator) - chain_eut (its own fuel chain's machines)",
        "explanation (template from the metrics): " + power_explain.template(doc),
        "with two or more options, plan_explain gives the checked explanation to say",
        *(f"note: {n}" for n in d.notes[:8]),
        *(f"rejected {g}: {w}" for g, w in d.rejected[:5]),
        (
            "Clayton compares and accepts in the Power view (dashboard key 5); accepting is his, "
            "not a tool's"
        ),
    ]
    page = rows[offset : offset + call.page]
    return Result(
        OPTION_COLS,
        list(page),
        head=[
            ("mode", req.mode),
            ("design", ", ".join(f"plan:{i}" for i in d.ids)),
            ("weights", d.weights),
        ],
        notes=notes,
        total=len(rows),
        offset=offset,
        more=offset + call.page < len(rows),
    )


def effort_estimate(call: Call, args: dict[str, Any], offset: int) -> Result:
    from gregtec.planner import designer, effort

    pid = str(args["id"]).strip().removeprefix("plan:")
    p = _guard(designer.load, _home(call), pid, power=True)
    m = p.get("metrics") or {}
    eff = m.get("effort")
    if not eff:
        raise ToolError(f"plan {pid} has no effort breakdown (solved without effort)")
    rows = [tuple(r) for r in effort.table(eff)]
    page = rows[offset : offset + call.page]
    return Result(
        tuple(effort.COLUMNS),
        list(page),
        head=[
            ("plan", f"plan:{pid}"),
            ("eff_h", m.get("eff_h")),
            ("build_h", m.get("build_h")),
            ("unlock_h", m.get("unlock_h")),
            ("uncounted", int(eff.get("uncounted") or 0)),
            ("craft_s", eff.get("craft_s")),
            ("unlocks", ", ".join(eff.get("unlocks") or ()) or "none"),
        ],
        notes=[
            *list(eff.get("notes") or ())[:10],
            (
                "hours are the effort model's (D17): crafting and processing time plus unlocking "
                "machines he doesn't own; gathering and building by hand are not counted"
            ),
            f"cite plan:{pid} and each row's mte: and r: ids",
        ],
        total=len(rows),
        offset=offset,
        more=offset + call.page < len(rows),
    )
