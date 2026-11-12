"""The flow diagram every output draws (WO-3.6): Mermaid, the PNG, the dashboard's flow card.

Boxes are the plan's nodes (machine, count, tier, ``r:`` id), plus one box per boundary input
(``in:<key>``, its rate) and output (``out:<key>``: the target and the byproducts). Links are the
plan's ``edges`` (item and rate, as the plan has them) and, for the boundary, a link from each
input box to the nodes whose recipe takes it and from each node that makes an output to its box;
a boundary link carries no rate of its own (the box shows the plan's rate), so no rate is
split here. :func:`layers` places boxes in columns (each box one column after the furthest box
feeding it, loops cut at their back edges) for the PNG and the gtnhplanner.com board; the
dashboard lays its card out with elkjs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gregtec.planner.graph import EU_KEY
from gregtec.planner.outputs.book import Book


@dataclass(frozen=True)
class Box:
    id: str
    label: str
    machine: str | None
    tier: str | None
    count: int | None
    ref: str | None  # the node's r: recipe; None for a boundary box
    kind: str  # node | in | out | target
    rate_s: float | None = None  # a boundary box's rate (the plan's)


@dataclass(frozen=True)
class Link:
    src: str
    dst: str
    item: str
    label: str
    rate_s: float | None


@dataclass(frozen=True)
class Flow:
    boxes: tuple[Box, ...]
    links: tuple[Link, ...]


def rate(v: float) -> str:
    """A rate per second as a diagram shows it."""
    return f"{v:.6g}/s"


def _label(n: Mapping[str, Any], book: Book) -> str:
    made = [k for k in n.get("for") or () if k != EU_KEY]
    if made:
        return book.name(made[0])
    rec = book.recipe(n["recipe"])
    if rec and rec.outputs:
        return book.name(rec.outputs[0].key)
    return str(n["recipe"])


def target_node(plan: Mapping[str, Any]) -> str | None:
    """The one node the exports anchor the target on: the node running the recipe the route
    chose for the target (``route.choices``), else the first node that makes it as a primary
    choice, else the first node that makes it at all (a node whose recipe only has it as a
    byproduct comes after the one the route sized for it); None when no node makes it."""
    target = plan["target"]["id"]
    makers = [n for n in plan["nodes"] if target in (n.get("for") or ())]
    chosen = {
        c["node"]: c["recipe"] for c in (plan.get("route") or {}).get("choices") or ()
    }
    for pick in (
        lambda n: chosen.get(target) == n["recipe"],
        lambda n: bool(n.get("primary")),
        lambda n: True,
    ):
        for n in makers:
            if pick(n):
                return str(n["n"])
    return None


def flow(plan: Mapping[str, Any], book: Book) -> Flow:
    """The plan's boxes and links (see the module docstring)."""
    target = plan["target"]["id"]
    boxes: list[Box] = []
    links: list[Link] = []
    uses: dict[str, list[str]] = {}
    makes: dict[str, list[str]] = {}
    for n in plan["nodes"]:
        m = n.get("machine")
        boxes.append(
            Box(
                id=n["n"],
                label=_label(n, book),
                machine=m["name"] if m else None,
                tier=m["tier"] if m else None,
                count=n.get("count"),
                ref=n["recipe"],
                kind="node",
            )
        )
        rec = book.recipe(n["recipe"])
        if rec is not None:
            for p in rec.inputs:
                if p.consumed and p.amount > 0:
                    uses.setdefault(p.key, []).append(n["n"])
            for p in rec.outputs:
                makes.setdefault(p.key, []).append(n["n"])
        for k in n.get("for") or ():
            if n["n"] not in makes.setdefault(k, []):
                makes[k].append(n["n"])
    for e in plan["edges"]:
        links.append(
            Link(e["from"], e["to"], e["item"], book.name(e["item"]), e["rate_s"])
        )
    boundary = plan.get("boundary") or {}
    for p in boundary.get("in") or ():
        bid = f"in:{p['id']}"
        boxes.append(
            Box(bid, book.name(p["id"]), None, None, None, None, "in", p["rate_s"])
        )
        for dst in dict.fromkeys(uses.get(p["id"], ())):
            links.append(Link(bid, dst, p["id"], book.name(p["id"]), None))
    outs = [{"id": target, "rate_s": plan["target"]["rate_s"]}]
    outs += [p for p in boundary.get("out") or () if p["id"] != target]
    for p in outs:
        bid = f"out:{p['id']}"
        kind = "target" if p["id"] == target else "out"
        boxes.append(
            Box(bid, book.name(p["id"]), None, None, None, None, kind, p["rate_s"])
        )
        for src in dict.fromkeys(makes.get(p["id"], ())):
            links.append(Link(src, bid, p["id"], book.name(p["id"]), None))
    return Flow(tuple(boxes), tuple(links))


def layers(
    ids: Sequence[str], edges: Sequence[tuple[str, str]]
) -> dict[str, tuple[int, int]]:
    """id -> (column, row): loops are cut at their back edges (a depth-first walk from the
    boxes nothing feeds, in input order), then each box goes one column after the furthest box
    feeding it; rows in input order. Deterministic: only the input order decides."""
    known = set(ids)
    out_edges: dict[str, list[str]] = {i: [] for i in ids}
    fed: set[str] = set()
    for a, b in edges:
        if a in known and b in known and a != b and b not in out_edges[a]:
            out_edges[a].append(b)
            fed.add(b)
    state: dict[str, int] = {}  # 1 on the walk's path, 2 done
    forward: list[tuple[str, str]] = []
    for root in [i for i in ids if i not in fed] + list(ids):
        if root in state:
            continue
        stack = [(root, iter(out_edges[root]))]
        state[root] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                continue  # a back edge: it closes a loop
            else:
                forward.append((node, nxt))
                if nxt not in state:
                    state[nxt] = 1
                    stack.append((nxt, iter(out_edges[nxt])))
    col = dict.fromkeys(ids, 0)
    for _ in range(len(ids)):
        moved = False
        for a, b in forward:
            if col[a] + 1 > col[b]:
                col[b] = col[a] + 1
                moved = True
        if not moved:
            break
    rows: dict[int, int] = {}
    placed: dict[str, tuple[int, int]] = {}
    for i in ids:
        c = col[i]
        placed[i] = (c, rows.get(c, 0))
        rows[c] = rows.get(c, 0) + 1
    return placed
