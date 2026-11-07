"""The core's tables in ``data/gos.sqlite`` (WO-2.1): ``llm_usage``, ``device``, ``kv`` (Appendix D)
and ``client_token`` (D117).

The core is the DB's single owner (plan 4.2); nothing else writes these tables. ``device`` keeps
Appendix D's columns plus ``token_sha256``, the SHA-256 of the device's cookie secret. Only hashes
are stored: gos-dev can read ``data\\`` (D82), so the DB never holds a usable token. The DDL is
idempotent and also runs on a DB that WO-1.3's schema created first.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DB_NAME = "gos.sqlite"
BUSY_TIMEOUT_MS = 5000  # how long a write waits for another writer
QUICK_TIMEOUT_MS = 50  # try_write's wait

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage(ts INT, lane TEXT, model TEXT, in_tok INT, out_tok INT,
  cache_read INT, cache_write INT, usd REAL, task TEXT);
CREATE INDEX IF NOT EXISTS ix_llm_usage_ts ON llm_usage(ts);
CREATE TABLE IF NOT EXISTS device(id TEXT PRIMARY KEY, name TEXT, paired INT, last_seen INT,
  revoked INT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, upd INT);
CREATE TABLE IF NOT EXISTS client_token(name TEXT PRIMARY KEY, token_sha256 TEXT NOT NULL,
  created INT NOT NULL, last_used INT, revoked INT NOT NULL DEFAULT 0);
"""
# Columns GOS adds to Appendix D tables, for a DB whose table was created without them.
ADDED_COLUMNS = (
    ("device", "token_sha256", "TEXT"),
    (
        "llm_usage",
        "ttft_ms",
        "INT",
    ),  # a streamed call's time to first text token (D152)
)


def connect(path: Path) -> sqlite3.Connection:
    """Open the DB (WAL, 5 s busy timeout, autocommit) and make sure the core's tables exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path,
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    for table, column, kind in ADDED_COLUMNS:
        have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    return conn


class Store:
    """One connection shared by the core's threads, one statement or transaction at a time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = connect(path)
        self._lock = threading.Lock()

    def rows(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """``BEGIN IMMEDIATE`` ... ``COMMIT``, rolled back if the block raises."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def try_write(self, sql: str, params: Sequence[Any] = ()) -> None:
        """One write statement that waits at most QUICK_TIMEOUT_MS for another writer.

        Raises ``sqlite3.OperationalError`` ("database is locked") if it can't have the DB.
        """
        with self._lock:
            self._conn.execute(f"PRAGMA busy_timeout = {QUICK_TIMEOUT_MS}")
            try:
                self._conn.execute(sql, params)
            finally:
                self._conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
