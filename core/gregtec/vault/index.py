"""``INDEX.md``: the vault's table of contents, cached in the lanes' system prompts (plan 6.1, 6.4).

Built deterministically from the notes' frontmatter and first facts, never by a model:

- one TSV row ``id t path summary upd`` per note, sorted by path; ``log/`` and ``INDEX.md`` itself
  are left out (plan 6.1), as are ``_gos/skills/`` and ``_gos/agents/``;
- a folder with more than 20 notes gets one row instead (``id`` the folder, ``t`` its notes' type
  or ``mixed``, ``summary`` how many notes, ``upd`` the newest), so a growing ``plans/`` costs one
  line; if the index would still pass 200 lines, the folders with the most notes collapse first;
- ``summary`` is the note's ``summary:`` fact, else its first ``title:``, ``note:`` or other fact,
  cut to :data:`SUMMARY_MAX` characters, tabs and control characters removed;
- the index's ``upd`` is its newest row's, so an unchanged vault rebuilds byte-for-byte the same
  file and the curator writes nothing (no churn in git or in the prompt cache).

A note whose frontmatter can't be read is listed with ``t`` ``?`` and the schema check reports it.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from gregtec.vault import io, schema

INDEX = "INDEX.md"
MAX_LINES = 200
FOLDER_MAX = 20
SUMMARY_MAX = 60
SUMMARY_KEYS = ("summary", "title", "note")
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]+")
HEAD_NOTE = (
    "note: one line per note (a folder with more than 20 notes gets one line); log/ is "
    "excluded; at most 200 lines"
)


@dataclass(frozen=True)
class Row:
    id: str
    t: str
    path: str
    summary: str
    upd: str

    def line(self) -> str:
        return f"{self.id}\t{self.t}\t{self.path}\t{self.summary}\t{self.upd}"


def _cell(text: str, limit: int | None = None) -> str:
    text = _CONTROL.sub(" ", text.replace("\t", " ")).strip()
    if limit is not None and len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _meta(text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    lines = text.replace("\r\n", "\n").split("\n")
    meta: dict[str, str] = {}
    facts: list[tuple[str, str]] = []
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return meta, facts
    end = lines.index("---", 1)
    for line in lines[1:end]:
        key, sep, value = line.partition(": ")
        if sep:
            meta[key] = value.strip()
    for line in lines[end + 1 :]:
        if m := schema.KEY.match(line):
            facts.append((m[1], m[2]))
        if line == "```tsv":
            break  # facts come before tables in practice; don't read a table as facts
    return meta, facts


def summary(facts: list[tuple[str, str]]) -> str:
    for key in SUMMARY_KEYS:
        for k, v in facts:
            if k == key:
                return _cell(v, SUMMARY_MAX)
    if facts:
        k, v = facts[0]
        return _cell(f"{k}: {v}", SUMMARY_MAX)
    return ""


def note_row(root: Path, rel: str) -> Row:
    try:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    meta, facts = _meta(text)
    return Row(
        _cell(meta.get("id", "")) or rel.rsplit("/", 1)[-1].removesuffix(".md"),
        _cell(meta.get("t", "")) or "?",
        rel,
        summary(facts),
        _cell(meta.get("upd", "")),
    )


def _folder_row(folder: str, rows: list[Row]) -> Row:
    types = {r.t for r in rows}
    return Row(
        folder.rstrip("/"),
        types.pop() if len(types) == 1 else "mixed",
        folder,
        f"{len(rows)} notes; vault_search finds one",
        max(r.upd for r in rows),
    )


def rows(root: Path) -> list[Row]:
    """The index rows for the vault at ``root`` (see the module docstring)."""
    found = [
        note_row(root, rel)
        for rel in io.notes(root)
        if rel != INDEX and not rel.startswith("log/")
    ]
    by_folder: dict[str, list[Row]] = defaultdict(list)
    for row in found:
        folder = row.path.rsplit("/", 1)[0] + "/" if "/" in row.path else ""
        by_folder[folder].append(row)
    collapsed = {f for f, rs in by_folder.items() if f and len(rs) > FOLDER_MAX}
    room = MAX_LINES - len(_head(dt.date.min)) - 2  # header row and closing fence

    def build() -> list[Row]:
        out = [r for f, rs in by_folder.items() if f not in collapsed for r in rs]
        out += [_folder_row(f, by_folder[f]) for f in collapsed]
        return sorted(out, key=lambda r: r.path)

    out = build()
    for folder in sorted(
        (f for f in by_folder if f and f not in collapsed),
        key=lambda f: (-len(by_folder[f]), f),
    ):
        if len(out) <= room:
            break
        collapsed.add(folder)
        out = build()
    return out[:room]


def _head(upd: dt.date | str) -> list[str]:
    stamp = upd.isoformat() if isinstance(upd, dt.date) else upd
    return [
        "---",
        "id: index",
        "t: index",
        f"upd: {stamp}",
        "---",
        HEAD_NOTE,
        "",
        "```tsv",
    ]


def _date(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text) if len(text) == 10 else None
    except ValueError:
        return None


def render(index_rows: list[Row], fallback: dt.date) -> str:
    dates = [d for r in index_rows if (d := _date(r.upd)) is not None]
    upd = max(dates).isoformat() if dates else fallback.isoformat()
    lines = _head(upd) + ["id\tt\tpath\tsummary\tupd"]
    lines += [r.line() for r in index_rows] + ["```"]
    return "\n".join(lines) + "\n"


def build(root: Path, today: dt.date) -> bool:
    """Rebuild ``INDEX.md``; True if it changed (and was written)."""
    text = render(rows(root), today)
    target = root / INDEX
    try:
        if target.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    io.atomic_write(target, text.encode("utf-8"))
    return True
