"""Tool results as text (plan sections 5.3 and 7.0, [D§3.6], PM07).

A result is ``key: value`` lines (a record), then a TSV table with a header row, then the
``rows:`` line, the ``more:`` cursor when another page exists, and ``note:`` lines:

    item: Steel Ingot [gregtech:gt.metaitem.01@11305]
    run: 20261106T092709Z

    ref	machine	tier	eut	...
    r:gt.recipe.blastfurnace/0123456789ab	Electric Blast Furnace	MV	120	...

    rows: 1-50 of 120
    more: <cursor>
    note: ...

- A page holds at most :data:`PAGE` rows (50).
- A table of one column gets an ``n`` column (the row's number) first, so every table line has a
  tab: a lone cell could otherwise be empty or read like the ``rows:``, ``more:`` or ``note:``
  line after the table.
- Cells are escaped so a value can never break a row: backslash, tab, CR and LF become ``\\\\``,
  ``\\t``, ``\\r`` and ``\\n``; other control characters and Unicode line breaks become
  ``\\xNN`` or ``\\uNNNN``. A text cell longer than :data:`CELL_MAX` is cut, and says so.
- Numbers are written as the DB holds them: an int as is, a float that is a whole number
  without ``.0``, any other float as Python's shortest repr. Nothing is rounded.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

PAGE = 50
CELL_MAX = 2000  # characters of one text cell

_ESCAPES: dict[int, str] = {
    **{c: f"\\x{c:02x}" for c in (*range(0x20), 0x7F, *range(0x80, 0xA0))},
    ord("\\"): "\\\\",
    ord("\t"): "\\t",
    ord("\n"): "\\n",
    ord("\r"): "\\r",
    0x2028: "\\u2028",  # line separator
    0x2029: "\\u2029",  # paragraph separator
}


def escape(text: str) -> str:
    """``text`` with nothing left that could end a cell or a line."""
    return text.translate(_ESCAPES)


def number(value: float) -> str:
    if math.isfinite(value) and value.is_integer() and abs(value) < 2**53:
        return str(int(value))
    return repr(value)


def cell(value: object) -> str:
    """One escaped TSV cell: None is empty, a bool 1 or 0."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return number(value)
    text = str(value)
    if len(text) > CELL_MAX:
        text = f"{text[:CELL_MAX]}... [{len(text) - CELL_MAX} more characters cut]"
    return escape(text)


@dataclass
class Result:
    """What a tool returns: a record, a table page and notes."""

    columns: Sequence[str] = ()
    rows: list[Sequence[object]] = field(default_factory=list)
    head: list[tuple[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    total: int | None = None  # every matching row, when the query counts them
    offset: int = 0  # rows before this page
    more: bool = False  # another page exists

    def __post_init__(self) -> None:
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError(
                    f"row of {len(row)} cells for {len(self.columns)} columns"
                )


def span(result: Result) -> str:
    n = len(result.rows)
    of = f" of {result.total}" if result.total is not None else ""
    if n == 0:
        return f"0{of}"
    tail = " (more)" if result.more and result.total is None else ""
    return f"{result.offset + 1}-{result.offset + n}{of}{tail}"


def render(result: Result, cursor: str | None = None) -> str:
    lines = [f"{escape(k)}: {cell(v)}" for k, v in result.head]
    columns, rows = result.columns, result.rows
    if len(columns) == 1:
        columns = ("n", *columns)
        rows = [(result.offset + n, *row) for n, row in enumerate(rows, 1)]
    if columns:
        if lines:
            lines.append("")
        lines.append("\t".join(escape(c) for c in columns))
        lines.extend("\t".join(cell(v) for v in row) for row in rows)
        lines.append("")
        lines.append(f"rows: {span(result)}")
    if cursor is not None:
        lines.append(f"more: {cursor}")
    lines.extend(f"note: {escape(n)}" for n in result.notes)
    return "\n".join(lines) + "\n"
