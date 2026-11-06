"""Check vault notes against ``_gos/schema.md`` (plan section 6.2).

The schema note follows the grammar it defines: ``key: value`` facts plus one ``tsv`` table of
note types (``t``, ``id`` pattern, ``path``, ``writer``, ``keys``).
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

FRONTMATTER = ("id", "t", "upd")
MAX_LINES = 150
KEY = re.compile(r"^([a-z][a-z0-9_]*): (.+)$")


@dataclass(frozen=True)
class NoteType:
    t: str
    id: str  # fnmatch pattern
    path: str
    writer: str
    keys: frozenset[str] | None  # None: any key


@dataclass
class Note:
    meta: dict[str, str]
    facts: list[tuple[str, str]]
    tables: list[list[list[str]]]


def parse(text: str) -> Note:
    """Parse a note; raise ``ValueError`` on anything outside the grammar."""
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise ValueError(f"{len(lines)} lines; the cap is {MAX_LINES}")
    if not lines or lines[0] != "---":
        raise ValueError("no frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError("frontmatter is not closed") from None
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(": ")
        if not sep:
            raise ValueError(f"bad frontmatter line: {line!r}")
        meta[key] = value.strip()
    if tuple(meta) != FRONTMATTER:
        raise ValueError(f"frontmatter must be exactly {', '.join(FRONTMATTER)}")
    dt.date.fromisoformat(meta["upd"])

    facts: list[tuple[str, str]] = []
    tables: list[list[list[str]]] = []
    table: list[list[str]] | None = None
    for n, line in enumerate(lines[end + 1 :], start=end + 2):
        if table is not None:
            if line == "```":
                tables.append(table)
                table = None
            else:
                table.append(line.split("\t"))
        elif line == "```tsv":
            table = []
        elif not line.strip():
            continue
        elif m := KEY.match(line):
            facts.append((m[1], m[2]))
        else:
            raise ValueError(f"line {n} is not key: value or a tsv table: {line!r}")
    if table is not None:
        raise ValueError("tsv fence is not closed")
    for rows in tables:
        if not rows or any(len(r) != len(rows[0]) for r in rows):
            raise ValueError(
                "tsv table needs a header row and the same number of columns per row"
            )
    return Note(meta, facts, tables)


def load_types(schema_note: Path) -> dict[str, NoteType]:
    note = parse(schema_note.read_text(encoding="utf-8"))
    [table] = note.tables
    header, *rows = table
    if header != ["t", "id", "path", "writer", "keys"]:
        raise ValueError(f"unexpected schema columns: {header}")
    types = {}
    for t, id_pattern, path, writer, keys in rows:
        allowed = None if keys == "*" else frozenset(k.strip() for k in keys.split(","))
        types[t] = NoteType(t, id_pattern, path, writer, allowed)
    return types


def check(path: Path, types: dict[str, NoteType]) -> list[str]:
    """Problems with one note; an empty list means it follows the schema."""
    try:
        note = parse(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [str(exc)]
    kind = types.get(note.meta["t"])
    if kind is None:
        return [f"unknown type t: {note.meta['t']}"]
    problems = []
    if not fnmatch.fnmatchcase(note.meta["id"], kind.id):
        problems.append(f"id {note.meta['id']} doesn't match {kind.id} for t: {kind.t}")
    if kind.keys is not None:
        problems += [
            f"key {k} isn't allowed for t: {kind.t}"
            for k, _ in note.facts
            if k not in kind.keys
        ]
    return problems


def type_for(rel: str, types: dict[str, NoteType]) -> NoteType | None:
    """The note type whose ``path`` holds ``rel`` (a vault-relative posix path): a type with a
    file path (``base/power.md``) holds that file only; one with a folder path (``plans/``)
    holds the notes directly in that folder, or in a subfolder no other type claims (the
    deepest matching folder wins, so ``base/lines/`` beats a type at ``base/``)."""
    exact = [k for k in types.values() if k.path == rel]
    if exact:
        return exact[0]
    folders = [
        k for k in types.values() if k.path.endswith("/") and rel.startswith(k.path)
    ]
    return max(folders, key=lambda k: len(k.path)) if folders else None


def id_for(rel: str, kind: NoteType) -> str:
    """The id a note at ``rel`` has: the type's fixed id for a file type, else the file name
    without ``.md`` (so a folder never holds two notes with one id), with the id pattern's
    prefix put in front when the name lacks it (``log/2026-11-06.md`` is ``log-2026-11-06``)."""
    if not kind.path.endswith("/"):
        return kind.id
    stem = rel.rsplit("/", 1)[-1].removesuffix(".md")
    prefix = kind.id.split("*", 1)[0] if kind.id.endswith("*") else ""
    return stem if stem.startswith(prefix) else prefix + stem


def check_text(rel: str, text: str, types: dict[str, NoteType]) -> list[str]:
    """Problems with a note's text at vault path ``rel``: :func:`check`'s, plus that its type
    lives at ``rel`` and its id is the one :func:`id_for` gives."""
    try:
        note = parse(text)
    except ValueError as exc:
        return [str(exc)]
    kind = types.get(note.meta["t"])
    if kind is None:
        return [f"unknown type t: {note.meta['t']}"]
    problems = []
    home = type_for(rel, types)
    if home is None or home.t != kind.t:
        problems.append(f"t: {kind.t} notes live at {kind.path}, not {rel}")
    elif note.meta["id"] != id_for(rel, kind):
        problems.append(f"id must be {id_for(rel, kind)} at {rel}")
    if not fnmatch.fnmatchcase(note.meta["id"], kind.id):
        problems.append(f"id {note.meta['id']} doesn't match {kind.id} for t: {kind.t}")
    if kind.keys is not None:
        problems += [
            f"key {k} isn't allowed for t: {kind.t}"
            for k, _ in note.facts
            if k not in kind.keys
        ]
    return problems
