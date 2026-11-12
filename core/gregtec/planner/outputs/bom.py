"""The plan's BOM as TSV (WO-3.6, plan §7.1 step 9, D10).

One row per node, in the plan's node order: ``node``, ``machine`` (its name), ``mte``
(``mte:<id>``), ``tier``, ``count``, ``oc`` (overclocks), ``parallel``, ``eut`` (EU/t of one machine
while it runs), ``eut_avg`` (the node's machines averaged over time), ``eut_peak`` (all of them
running at once), ``util`` and ``recipe`` (its ``r:`` id). A crafting-table or furnace step has no
machine, so its machine columns are empty. The last row, ``total``, sums ``count``, ``eut_avg``
and ``eut_peak``; :func:`check` says where those totals differ from the plan's ``metrics``
(``machines``, ``eut_avg``, ``eut_peak``), which never happens for a plan the designer made.
Every cell is the plan's own value, copied; the totals are its sums, rounded as the plan rounds.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from gregtec.planner.plan import _num

COLUMNS = (
    "node",
    "machine",
    "mte",
    "tier",
    "count",
    "oc",
    "parallel",
    "eut",
    "eut_avg",
    "eut_peak",
    "util",
    "recipe",
)
TOTAL = "total"


def rows(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One dict per node (the module docstring's columns), in node order."""
    out: list[dict[str, Any]] = []
    for n in plan["nodes"]:
        m = n.get("machine")
        out.append(
            {
                "node": n["n"],
                "machine": m["name"] if m else None,
                "mte": f"mte:{m['mte']}" if m else None,
                "tier": m["tier"] if m else None,
                "count": n.get("count"),
                "oc": n.get("oc"),
                "parallel": n.get("parallel"),
                "eut": n.get("eut"),
                "eut_avg": n.get("eut_avg"),
                "eut_peak": n.get("eut_peak"),
                "util": n.get("util"),
                "recipe": n["recipe"],
            }
        )
    return out


def totals(table: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The ``total`` row: the sums of ``count``, ``eut_avg`` and ``eut_peak``."""
    return {
        "node": TOTAL,
        "count": sum(int(r["count"] or 0) for r in table),
        "eut_avg": _num(math.fsum(float(r["eut_avg"] or 0) for r in table)),
        "eut_peak": sum(int(r["eut_peak"] or 0) for r in table),
    }


def check(plan: Mapping[str, Any]) -> list[str]:
    """Where the BOM's totals differ from the plan's ``metrics`` (empty: they agree)."""
    t = totals(rows(plan))
    m = plan.get("metrics") or {}
    problems = []
    for col, metric in (
        ("count", "machines"),
        ("eut_avg", "eut_avg"),
        ("eut_peak", "eut_peak"),
    ):
        if metric in m and t[col] != m[metric]:
            problems.append(f"BOM {col} total {t[col]} != metrics.{metric} {m[metric]}")
    return problems


def _cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return f"{v:.9g}"
    return " ".join(str(v).split())  # no tab or newline inside a cell


def tsv(plan: Mapping[str, Any]) -> str:
    """The BOM as TSV text: a header row, one row per node, the ``total`` row."""
    table = rows(plan)
    lines = ["\t".join(COLUMNS)]
    for r in [*table, totals(table)]:
        lines.append("\t".join(_cell(r.get(c)) for c in COLUMNS))
    return "\n".join(lines) + "\n"
