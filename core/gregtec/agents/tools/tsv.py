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
- A result can carry a note's text instead of a table (``note_read``): its lines as they are,
  tabs kept (a note's TSV tables), every other control character escaped as in a cell.
- Numbers are written as the DB holds them: an int as is, a float that is a whole number
  without ``.0``, any other float as Python's shortest repr. Nothing is rounded.
"""

from __future__ import annotations

import math
import re
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


_TEXT_ESCAPES = {k: v for k, v in _ESCAPES.items() if k != ord("\t")}


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
    text: str | None = None  # a note's text, shown after the head (no table then)

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
    if result.text is not None:
        if lines:
            lines.append("")
        lines.extend(
            line.translate(_TEXT_ESCAPES)
            for line in result.text.rstrip("\n").split("\n")
        )
        lines.append("")
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


_UNESCAPED = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}
_ESCAPE_SEQ = re.compile(r"\\(?:x([0-9a-f]{2})|u([0-9a-f]{4})|([\\tnr]))")


def unescape(text: str) -> str:
    """:func:`escape` undone."""

    def one(m: re.Match[str]) -> str:
        code = m[1] or m[2]
        return chr(int(code, 16)) if code else _UNESCAPED[m[3]]

    return _ESCAPE_SEQ.sub(one, text)


@dataclass
class Parsed:
    """A rendered result taken apart again, as the router and the fast lane read what the
    model reads: head lines, the table (cells unescaped), the ``rows:`` span and the notes."""

    head: dict[str, str] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    span: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int | None:
        """N of ``rows: a-b of N``, when the result counted its rows."""
        if self.span is None or " of " not in self.span:
            return None
        n = self.span.rsplit(" of ", 1)[1].split(" ", 1)[0]
        return int(n) if n.isdigit() else None


def parse(text: str) -> Parsed:
    """:func:`render`'s text taken apart. No head line has a tab and no table line is empty
    (cells are escaped), so the head runs to the first blank line and the table from there to
    the blank line before ``rows:``; notes follow the table."""
    lines = text.rstrip("\n").split("\n")
    out = Parsed()
    spans = [i for i, ln in enumerate(lines) if ln.startswith("rows: ")]
    end = spans[-1] if spans else len(lines)
    tail = lines[end:] if spans else lines
    out.notes = [unescape(ln[6:]) for ln in tail if ln.startswith("note: ")]
    body = lines[:end]
    blank = body.index("") if "" in body else len(body)
    for ln in body[:blank]:
        key, sep, value = ln.partition(": ")
        if sep and key not in ("note", "more"):
            out.head[unescape(key)] = unescape(value)
    if spans:
        out.span = lines[end].removeprefix("rows: ")
        grid = [ln for ln in body[blank + 1 :] if ln]
        if grid:
            out.columns = [unescape(c) for c in grid[0].split("\t")]
            for ln in grid[1:]:
                cells = [unescape(c) for c in ln.split("\t")]
                if len(cells) == len(out.columns):
                    out.rows.append(dict(zip(out.columns, cells, strict=True)))
    return out
