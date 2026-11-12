"""The core's tables in ``data/gos.sqlite`` (WO-2.1): ``llm_usage``, ``device``, ``kv`` (Appendix D),
``client_token`` (D117), ``answer`` (the dashboard's Chat: the newest 50 answers, D159), the
OC bridge's ``tel_machine``, ``tel_sample`` and ``action`` (Appendix D; WO-7.1, D219, D220), and My
base's ``my_base_machine`` and ``my_base_stock`` (WO-3.8, D210: keyed on GT mte ids and stable
item/fluid keys, so they outlive every ``gos db build``; :mod:`gregtec.base.state` reads and writes
them), and ``my_base_machine_at``, where the placement events and the census put each machine they
saw (WO-6.2, D261; :mod:`gregtec.base.seen`), and the event store: Appendix D's ``event`` and
``session`` with the columns WO-6.3 adds (D263; :mod:`gregtec.events.store`), and ``age_mark``,
when each voltage age was first seen and its recap (D162, D265; :mod:`gregtec.events.ages`).

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
  cache_read INT, cache_write INT, usd REAL, task TEXT, ttft_ms INT);
CREATE INDEX IF NOT EXISTS ix_llm_usage_ts ON llm_usage(ts);
CREATE TABLE IF NOT EXISTS device(id TEXT PRIMARY KEY, name TEXT, paired INT, last_seen INT,
  revoked INT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, upd INT);
CREATE TABLE IF NOT EXISTS client_token(name TEXT PRIMARY KEY, token_sha256 TEXT NOT NULL,
  created INT NOT NULL, last_used INT, revoked INT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS answer(id TEXT PRIMARY KEY, ts INT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tel_machine(id TEXT PRIMARY KEY, name TEXT, mte_id INT, dim INT, x INT, y INT,
  z INT, line_id TEXT, last_seen INT, status TEXT);
CREATE TABLE IF NOT EXISTS tel_sample(ts INT, src TEXT, key TEXT, value REAL, text TEXT);
CREATE INDEX IF NOT EXISTS ix_tel ON tel_sample(src, key, ts);
CREATE TABLE IF NOT EXISTS action(id INTEGER PRIMARY KEY, created INT, kind TEXT, args TEXT,
  state TEXT CHECK(state IN ('proposed','confirmed','sent','done','failed','cancelled','expired')),
  confirmed_via TEXT, confirmed_ts INT, result TEXT);
CREATE TABLE IF NOT EXISTS my_base_machine(mte_id INT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('manual','event','census')),
  count INT NOT NULL CHECK(count >= 0), upd INT NOT NULL, PRIMARY KEY(mte_id, source));
CREATE TABLE IF NOT EXISTS my_base_stock(key TEXT PRIMARY KEY, qty REAL NOT NULL CHECK(qty > 0),
  upd INT NOT NULL);
CREATE TABLE IF NOT EXISTS my_base_machine_at(source TEXT NOT NULL CHECK(source IN ('event','census')),
  dim INT NOT NULL, x INT NOT NULL, y INT NOT NULL, z INT NOT NULL, mte_id INT NOT NULL,
  upd INT NOT NULL, PRIMARY KEY(source, dim, x, y, z));
CREATE INDEX IF NOT EXISTS ix_my_base_machine_at_mte ON my_base_machine_at(source, mte_id);
CREATE TABLE IF NOT EXISTS session(id INTEGER PRIMARY KEY, start_ts INT, end_ts INT, world TEXT,
  journal_path TEXT);
CREATE TABLE IF NOT EXISTS event(id INTEGER PRIMARY KEY, ts INT NOT NULL, session_id INT,
  kind TEXT NOT NULL, dim INT, x INT, y INT, z INT, ref TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS ix_event_ts ON event(ts);
CREATE INDEX IF NOT EXISTS ix_event_kind ON event(kind, ts);
CREATE TABLE IF NOT EXISTS age_mark(age INT PRIMARY KEY, stage TEXT NOT NULL, start_ts INT NOT NULL,
  end_ts INT, best_net_eut INT, best_ref TEXT, recap TEXT);
"""
# Indexes on columns ADDED_COLUMNS may have just added (a DB whose table predates them).
POST_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_event_uid ON event(uid);
CREATE INDEX IF NOT EXISTS ix_event_session ON event(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_session_open ON session(instance, end_ts);
"""
# Columns GOS adds to Appendix D tables, for a DB whose table was created without them.
ADDED_COLUMNS = (
    ("device", "token_sha256", "TEXT"),
    (
        "llm_usage",
        "ttft_ms",
        "INT",
    ),  # a streamed call's time to first text token (D152)
    # The confirm flow (WO-7.1, D220): which instance's OC computer an action is for, what
    # GOS read back, who proposed it, and when it was sent and finished.
    ("action", "target", "TEXT"),
    ("action", "computer", "TEXT"),
    ("action", "readback", "TEXT"),
    ("action", "source", "TEXT"),
    ("action", "sent_ts", "INT"),
    ("action", "done_ts", "INT"),
    # The event store (WO-6.3, D263): the watchers' event id (a retried post is one row), the
    # reader, and the instance it came from; a session's instance, how it began and ended, its
    # last activity and the session scribe's progress through it.
    ("event", "uid", "TEXT"),
    ("event", "source", "TEXT"),
    ("event", "instance", "TEXT"),
    ("session", "instance", "TEXT"),
    ("session", "start_why", "TEXT"),
    ("session", "end_why", "TEXT"),
    ("session", "last_ts", "INT"),
    ("session", "journal_upto", "INT"),  # the newest event id the journal has counted
    ("session", "journal_ts", "INT"),  # when the journal was last written
    ("session", "journal_lines", "INT"),  # lines written for this session (<= 40)
    ("session", "journal_done", "INT"),  # the end block is written (or not wanted)
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
    conn.executescript(POST_SCHEMA)
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
