"""gtnh-flow YAML (WO-3.6, plan §7.1 step 9, [E§1.1], D202).

gtnh-flow (https://github.com/OrderedSet86/gtnh-flow, MIT) reads a project as a YAML list of
machines, ``m`` (name), ``tier`` (lower case, ``lv`` and up), ``I`` and ``O`` (ingredient name ->
amount per craft), ``eut`` and ``dur`` (seconds per craft, with its ``DUR_FORMAT: sec``), and at
least one ``target`` (``{ingredient: rate per second}``) or ``number`` (a machine count) to
balance the rest; it pools
ingredients by name and draws the solved graph with Graphviz. PlanNH's test harness reads the
same files. This module writes that format from the plan; nothing of gtnh-flow is imported or
copied (D202).

gtnh-flow's own overclock math is older than 2.9's ([E§1.1]), so each machine is written as GOS
sized it, with ``do_not_overclock: true``: ``eut`` is the EU/t of one machine while it runs, and
``dur`` the seconds one machine takes per craft (``ticks_eff / 20 / parallel``), so gtnh-flow's
machine counts are the plan's before rounding up. The target node carries ``target`` at the
plan's rate. A chanced output is its expected amount (gtnh-flow's own convention), an input
that isn't used up (a circuit, a catalyst) is left out. Ingredient names are the DB's names, made
unique: two keys with one name get their key appended. A node with no machine (a crafting-table
step) isn't a gtnh-flow machine, so it is left out and listed in the header comment, and its
product shows as a source.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from gregtec.planner.graph import EU_KEY, FULL
from gregtec.planner.outputs.book import Book

TIERS = (
    "lv",
    "mv",
    "hv",
    "ev",
    "iv",
    "luv",
    "zpm",
    "uv",
    "uhv",
    "uev",
    "uiv",
    "umv",
    "uxv",
)


def _q(s: str) -> str:
    """A YAML double-quoted scalar (JSON's string syntax is valid YAML)."""
    return json.dumps(s, ensure_ascii=False)


def _n(v: float) -> str:
    return f"{v:.9g}"


def _tier(name: str | None) -> str:
    low = (name or "lv").lower()
    return low if low in TIERS else ("lv" if low == "ulv" else TIERS[-1])


def ingredient_names(plan: Mapping[str, Any], book: Book) -> dict[str, str]:
    """key -> its gtnh-flow ingredient name (the DB's name, lower case, unique)."""
    keys: list[str] = []
    for n in plan["nodes"]:
        rec = book.recipe(n["recipe"])
        if rec is not None:
            keys += [p.key for p in (*rec.inputs, *rec.outputs)]
    keys.append(plan["target"]["id"])
    keys = list(dict.fromkeys(k for k in keys if k != EU_KEY))
    base = {k: " ".join(book.name(k).lower().split()) or k for k in keys}
    seen: dict[str, int] = {}
    for name in base.values():
        seen[name] = seen.get(name, 0) + 1
    return {k: (f"{v} ({k})" if seen[v] > 1 else v) for k, v in base.items()}


def text(plan: Mapping[str, Any], book: Book) -> str:
    """The project file's text."""
    names = ingredient_names(plan, book)
    target = plan["target"]
    out = [
        f"# Gregtec OS plan:{plan['id']} · {plan['name']}",
        (
            f"# export run {plan['run']}, GT {plan['gt']}; machines as GOS sized them "
            "(do_not_overclock), dur = seconds per craft per machine"
        ),
    ]
    skipped: list[str] = []
    anchored = False
    first: int | None = None
    for n in plan["nodes"]:
        rec = book.recipe(n["recipe"])
        m = n.get("machine")
        if m is None or rec is None or not n.get("ticks_eff"):
            skipped.append(f"{n['n']} {n['recipe']}")
            continue
        parallel = max(1, int(n.get("parallel") or 1))
        ins: dict[str, float] = {}
        for p in rec.inputs:
            if p.consumed and p.amount > 0 and p.circuit is None:
                ins[names[p.key]] = (
                    ins.get(names[p.key], 0.0) + p.amount * p.chance_bp / FULL
                )
        outs: dict[str, float] = {}
        for p in rec.outputs:
            if p.amount > 0:
                outs[names[p.key]] = outs.get(names[p.key], 0.0) + p.expected
        if first is None:
            first = len(out)
        out.append(f"- m: {_q(m['name'])}")
        out.append(f"  tier: {_tier(m['tier'])}")
        out.append("  I:" + ("" if ins else " {}"))
        out += [f"    {_q(k)}: {_n(v)}" for k, v in ins.items()]
        out.append("  O:" + ("" if outs else " {}"))
        out += [f"    {_q(k)}: {_n(v)}" for k, v in outs.items()]
        out.append(f"  eut: {int(n['eut'] or 0)}")
        out.append(f"  dur: {_n(n['ticks_eff'] / 20 / parallel)}")
        out.append("  do_not_overclock: true")
        out.append(f"  gos_node: {_q(n['n'])}")
        out.append(f"  gos_recipe: {_q(n['recipe'])}")
        if target["id"] in (n.get("for") or ()):
            out.append("  target:")
            out.append(f"    {_q(names[target['id']])}: {_n(target['rate_s'])}")
            anchored = True
    if not anchored and first is not None:
        # No node makes the target here: gtnh-flow needs one fixed node, so the first machine
        # keeps its count.
        node = next(n for n in plan["nodes"] if n.get("machine") and n.get("ticks_eff"))
        out.insert(first + 1, f"  number: {int(node['count'] or 1)}")
    if skipped:
        out.insert(
            2,
            "# not machines, left out (their products show as sources): "
            + ", ".join(skipped),
        )
    return "\n".join(out) + "\n"
