"""Opening ``GOS_HOME/data/gos.sqlite`` and running named queries (plan section 5.3).

- :func:`connect` opens it in WAL mode (PM03), with autocommit so callers hold their own
  transactions, and applies ``schema.sql`` (every statement ``IF NOT EXISTS``) to a DB whose
  ``user_version`` is older than :data:`SCHEMA_VERSION`; opening a current DB writes nothing.
  An existing DB first gets ``migrations/<n>.sql`` for each version ``n`` it lacks (the changes
  ``IF NOT EXISTS`` can't make, such as new columns); a new DB (version 0) needs none. Both
  run in one transaction under the write lock, after reading the version again under it.
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
MIGRATIONS = HERE / "migrations"
SCHEMA_VERSION = 3
DB_FILE = Path("data", "gos.sqlite")  # in GOS_HOME (plan 4.3)
_QUERY_NAME = re.compile(r"[a-z][a-z0-9_]*")


class SchemaError(RuntimeError):
    """The DB was made by a newer Gregtec OS."""


def db_path(gos_home: Path) -> Path:
    return gos_home / DB_FILE


def connect(
    path: Path, *, readonly: bool = False, immutable: bool = False
) -> sqlite3.Connection:
    """Open ``path`` (made if missing, unless ``readonly``) with the schema applied.

    ``immutable`` (with ``readonly``): SQLite takes no lock and makes no -wal or -shm file, and
    reads only the main file, so a write still in the WAL is not seen. For a probe that must
    leave nothing behind (``gos doctor``'s GT pin, D223), never for a DB that is being written.
    """
    if immutable and not readonly:
        raise ValueError("immutable is read only")
    if readonly:
        mode = "mode=ro&immutable=1" if immutable else "mode=ro"
        conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?{mode}", uri=True, isolation_level=None
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
                _upgrade(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _upgrade(conn: sqlite3.Connection) -> None:
    """Migrate and apply the schema in one transaction, under the write lock. The version is
    read again under the lock: another process may have upgraded the DB (and written to it)
    since this one first looked, and a migration must not run twice."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            steps = [
                MIGRATIONS / f"{n}.sql" for n in range(version + 1, SCHEMA_VERSION + 1)
            ]
            scripts = [s for s in steps if version and s.is_file()] + [SCHEMA]
            for script in scripts:
                for statement in _statements(script.read_text(encoding="utf-8")):
                    conn.execute(statement)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _statements(script: str) -> list[str]:
    """``script``'s SQL statements one by one (``executescript`` would commit the transaction)."""
    out: list[str] = []
    pending = ""
    for piece in script.split(";"):
        pending += piece + ";"
        if sqlite3.complete_statement(pending):
            out.append(pending)
            pending = ""
    return out


def query_text(name: str) -> str:
    """The SQL of ``queries/<name>.sql``."""
    if not _QUERY_NAME.fullmatch(name):
        raise ValueError(f"not a query name: {name!r}")
    return (QUERIES / f"{name}.sql").read_text(encoding="utf-8")


def query(
    conn: sqlite3.Connection, name: str, params: Mapping[str, object] | None = None
) -> list[sqlite3.Row]:
    """Run ``queries/<name>.sql`` with named ``params``."""
    return conn.execute(query_text(name), dict(params or {})).fetchall()
