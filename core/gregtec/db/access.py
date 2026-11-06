"""Opening ``GOS_HOME/data/gos.sqlite`` and running named queries (plan section 5.3).

- :func:`connect` opens it in WAL mode (PM03), with autocommit so callers hold their own
  transactions, and applies ``schema.sql`` (every statement ``IF NOT EXISTS``) to a DB whose
  ``user_version`` is older than :data:`SCHEMA_VERSION`; opening a current DB writes nothing.
  A DB whose ``user_version`` is newer is refused, because this code would not know its tables.
- Named read-only queries live in ``queries/<name>.sql`` and are run by name with named
  parameters (``:q``); WO-2.2's tool layer calls them the same way.
- Never copy the live file: back it up with ``VACUUM INTO`` (plan section 5.3).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).parent
SCHEMA = HERE / "schema.sql"
QUERIES = HERE / "queries"
SCHEMA_VERSION = 1
DB_FILE = Path("data", "gos.sqlite")  # in GOS_HOME (plan 4.3)
_QUERY_NAME = re.compile(r"[a-z][a-z0-9_]*")


class SchemaError(RuntimeError):
    """The DB was made by a newer Gregtec OS."""


def db_path(gos_home: Path) -> Path:
    return gos_home / DB_FILE


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open ``path`` (made if missing, unless ``readonly``) with the schema applied."""
    if readonly:
        conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None
        )
    else:
        conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise SchemaError(
                f"{path} has schema version {version}; this Gregtec OS knows {SCHEMA_VERSION}"
            )
        if not readonly:
            conn.execute("PRAGMA journal_mode=WAL")
            # Only a DB older than the schema is written to on open: setting user_version takes
            # the write lock, so doing it on every open would make each connection wait for a
            # long write (a build) and fail after the timeout.
            if version < SCHEMA_VERSION:
                conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    except BaseException:
        conn.close()
        raise
    return conn


def query(
    conn: sqlite3.Connection, name: str, params: Mapping[str, object] | None = None
) -> list[sqlite3.Row]:
    """Run ``queries/<name>.sql`` with named ``params``."""
    if not _QUERY_NAME.fullmatch(name):
        raise ValueError(f"not a query name: {name!r}")
    sql = (QUERIES / f"{name}.sql").read_text(encoding="utf-8")
    return conn.execute(sql, dict(params or {})).fetchall()
