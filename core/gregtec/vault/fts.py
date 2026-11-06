"""``note_fts``: the vault's notes mirrored into the DB for ``vault_search`` (plan 6.6, [D§3.6]).

Appendix D's table, ``note_fts(path UNINDEXED, t, upd UNINDEXED, body)``: one row per note (the
notes :func:`gregtec.vault.io.notes` lists), ``body`` the text after the frontmatter. The
curator mirrors the whole vault once a day (:func:`mirror_all`); the core mirrors each note an
agent writes as it is written (:func:`mirror_one`), so a search sees it at once.

The core is the DB's single writer (plan 4.2): these run in its process, on their own
connection (WAL lets the tools read meanwhile).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from gregtec.vault import io

DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5("
    "path UNINDEXED, t, upd UNINDEXED, body)"
)
MAX_TERMS = 12
_WORD = re.compile(r"\w+", re.UNICODE)


def _row(root: Path, rel: str) -> tuple[str, str, str, str] | None:
    try:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = text.replace("\r\n", "\n")
    t = upd = ""
    body = text
    lines = text.split("\n")
    if lines and lines[0] == "---" and "---" in lines[1:]:
        end = lines.index("---", 1)
        for line in lines[1:end]:
            key, _, value = line.partition(": ")
            if key == "t":
                t = value.strip()
            elif key == "upd":
                upd = value.strip()
        body = "\n".join(lines[end + 1 :])
    return rel, t, upd, body.strip("\n")


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(DDL)
    return conn


def mirror_all(db: Path, root: Path) -> int:
    """Replace ``note_fts`` with every note in the vault, in one transaction. Returns the count."""
    rows = [r for rel in io.notes(root) if (r := _row(root, rel)) is not None]
    conn = connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM note_fts")
            conn.executemany(
                "INSERT INTO note_fts(path, t, upd, body) VALUES (?, ?, ?, ?)", rows
            )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()
    return len(rows)


def mirror_one(db: Path, root: Path, rel: str) -> None:
    """Mirror one note (or drop its row if it is gone)."""
    row = _row(root, rel)
    conn = connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM note_fts WHERE path = ?", (rel,))
            if row is not None:
                conn.execute(
                    "INSERT INTO note_fts(path, t, upd, body) VALUES (?, ?, ?, ?)", row
                )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def match(text: str) -> str | None:
    """An FTS5 MATCH expression for the words of ``text`` (each quoted, so no FTS5 syntax a
    request contains is ever run; the last word also matches as a prefix). None: no words."""
    words = _WORD.findall(text.lower())[:MAX_TERMS]
    if not words:
        return None
    quoted = [f'"{w}"' for w in words]
    quoted[-1] += "*"
    return " ".join(quoted)
