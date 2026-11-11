"""Every node's alternatives and pins (WO-3.4, plan §7.1 step 3, D11, D61).

- :func:`node_alternatives`: the recipes for one item of a graph, the pruned candidates by
  default (chosen, alternative and pruned, in the heuristic's order, each with why it isn't the
  one used) and with ``show_all`` also the unchecked ones below the top 4 and the rejected ones
  with their reason (D61's "show all").
- :func:`pin` / :func:`unpin`: a request with one node's recipe fixed (or freed). Any node can
  be pinned, primary or not; a pin is a fixed choice and the design re-runs from the graph
  (:func:`gregtec.planner.plan.design`). Pins live in the request and so in the plan JSON.
- :func:`branch`: the items a node's recipe reaches backward (its branch), so a caller can
  check that a pin changed only that branch.
"""

from __future__ import annotations

from dataclasses import replace

from gregtec.planner.graph import Graph
from gregtec.planner.request import Pin, Request, RequestError

SHOWN = ("chosen", "alternative", "pruned")


def node_alternatives(
    g: Graph, key: str, show_all: bool = False
) -> list[dict[str, str]]:
    """The candidate recipes for item ``key`` (see the module docstring)."""
    try:
        item = g.item(key)
    except StopIteration:
        raise KeyError(f"{key} is not a node of this graph") from None
    return [
        {"recipe": c.recipe, "status": c.status, "reason": c.reason}
        for c in item.candidates
        if show_all or c.status in SHOWN
    ]


def pin(request: Request, node: str, recipe: str) -> Request:
    """``request`` with ``node`` pinned to ``recipe`` (replacing any pin it had)."""
    pins = tuple(p for p in request.pins if p.node != node) + (Pin(node, recipe),)
    return replace(request, pins=pins)


def unpin(request: Request, node: str) -> Request:
    if not any(p.node == node for p in request.pins):
        raise RequestError(f"{node} has no pin")
    return replace(request, pins=tuple(p for p in request.pins if p.node != node))


def branch(g: Graph, key: str) -> frozenset[str]:
    """``key`` and every item its chosen recipes use up, transitively: the node's branch."""
    consumed = {r.id: {p.key for p in r.inputs if p.consumed} for r in g.recipes}
    chosen = {i.id: i.chosen for i in g.items}
    out: set[str] = set()
    todo = [key]
    while todo:
        k = todo.pop()
        if k in out:
            continue
        out.add(k)
        for ref in chosen.get(k, ()):
            todo.extend(consumed.get(ref, ()))
    return frozenset(out)
