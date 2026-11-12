"""The flow diagram as Mermaid, for the plan's vault note (WO-3.6, plan §7.1 step 9, D201).

``flowchart LR``: a node is a rectangle ``n1["Steel Ingot<br/>23x Volcanus · MV"]``, a boundary
input a stadium ``in1(["Iron Ore · 18/s"])``, the target and byproducts subroutines
``out1[["Steel Ingot · 1/s"]]``; a link ``n2 -->|"Steel Dust 1/s"| n1``. Mermaid ids are made
here (``n1``, ``in1``, ``out1``), never from item keys. Label text is the DB's names and the plan's
numbers; a quote, ``<``, ``>``, ``|`` or ``#`` in a name becomes Mermaid's entity code, so no name
can end a label or inject a directive.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gregtec.planner.outputs.book import Book
from gregtec.planner.outputs.diagram import Box, flow, rate

HEAD = "flowchart LR"
_ESC = {'"': "#quot;", "<": "#lt;", ">": "#gt;", "|": "#124;", "#": "#35;"}


def esc(text: str) -> str:
    """Text safe inside a quoted Mermaid label (one line)."""
    text = " ".join(text.split())
    return "".join(_ESC.get(c, c) for c in text)


def _box(b: Box, mid: str) -> str:
    if b.kind == "node":
        sub = " · ".join(
            x
            for x in (
                f"{b.count}x" if b.count is not None else None,
                b.machine or "no machine",
                b.tier,
            )
            if x
        )
        return f'{mid}["{esc(b.label)}<br/>{esc(sub)}"]'
    text = esc(b.label) + (f" · {rate(b.rate_s)}" if b.rate_s is not None else "")
    if b.kind == "in":
        return f'{mid}(["{text}"])'
    return f'{mid}[["{text}"]]'


def lines(plan: Mapping[str, Any], book: Book) -> list[str]:
    """The diagram's lines (no fence)."""
    f = flow(plan, book)
    ids: dict[str, str] = {}
    counts = {"in": 0, "out": 0}
    for b in f.boxes:
        if b.kind == "node":
            ids[b.id] = b.id
        else:
            side = "in" if b.kind == "in" else "out"
            counts[side] += 1
            ids[b.id] = f"{side}{counts[side]}"
    out = [HEAD]
    out += [f"  {_box(b, ids[b.id])}" for b in f.boxes]
    for link in f.links:
        label = esc(link.label)
        if link.rate_s is not None:
            label += f" {rate(link.rate_s)}"
        out.append(f'  {ids[link.src]} -->|"{label}"| {ids[link.dst]}')
    return out


def text(plan: Mapping[str, Any], book: Book) -> str:
    """The diagram as a ``.mmd`` file's text."""
    return "\n".join(lines(plan, book)) + "\n"
