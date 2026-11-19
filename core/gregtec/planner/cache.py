"""Process-level caches of what is pure per DB run (WO-3.9, D352).

Two things are kept between designs, never anything a design returns as such:

- **the graph builder's batch reader** (:class:`gregtec.planner.graph._Reader`): items, fluids,
  ore slots, recipes, the recipes that make a key, a key's uses, oredict members, research
  recipes and the machines of a recipe map, all read from one export run of one DB file. One
  reader per (DB file, DB generation, export run); a build holds its lock, so two builds never
  share one at the same moment.
- **the effort model's component solves** (:class:`gregtec.planner.effort.Effort`): a
  component's graph, its sized machines and its solved flow, keyed by every input the solve
  reads (:func:`gregtec.planner.effort.Effort._cache_key`).

**DB generation** (:func:`scope`): a digest of the file's identity (device and inode, so a
restored or replaced file is a new DB), every ``export_run`` row (id, run key, created, Play
fingerprint, its sources), the build stamp ``gos db build`` writes in ``kv`` (``db.build``, so a
rebuild of the same run is a new generation) and the ``structure`` and ``structure_run`` row
counts and largest rowids (WO-1.8's loader and WO-10.9's captures change footprints without a
build). Every entry is keyed by the DB file's path and generation, so nothing is shared across
DB files; when a path's generation changes, every entry of the old one is dropped at once. An
in-memory or temporary DB (no file) is never cached.

Bounded: :data:`EFFORT_MAX` component solves (least recently used dropped first) and
:data:`READERS_MAX` readers, a reader past :data:`READER_MAX_RECIPES` recipes dropped after its
build. ``GOS_PLANNER_CACHE=0`` in the environment, or :func:`disabled`, turns both off (the
differential tests and ``scripts/design_timing.py`` compare with and without).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from gregtec.planner import progress

EFFORT_MAX = 4096  # component solves kept (a power design walks about 140)
READERS_MAX = 4  # (DB file, generation, run) readers kept
READER_MAX_RECIPES = (
    250_000  # a reader past this many recipes is dropped after its build
)
BUILD_KEY = "db.build"  # the kv row gos db build stamps (D352)

MISS = object()


@dataclass
class Stats:
    reader_hits: int = 0
    reader_misses: int = 0
    effort_hits: int = 0
    effort_misses: int = 0
    dropped: int = 0  # generations dropped

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class _Slot[R]:
    reader: R
    lock: threading.Lock = field(default_factory=threading.Lock)


_lock = threading.Lock()
_on = os.environ.get("GOS_PLANNER_CACHE", "1") != "0"
_generation: dict[str, str] = {}  # DB path -> its newest generation seen
_readers: OrderedDict[tuple[str, str, int], _Slot[Any]] = OrderedDict()
_effort: OrderedDict[Hashable, Any] = OrderedDict()
stats = Stats()


def enabled() -> bool:
    return _on


def set_enabled(on: bool) -> None:
    """Turn the caches on or off (off also empties them)."""
    global _on
    with _lock:
        _on = on
    if not on:
        clear()


@contextmanager
def disabled() -> Iterator[None]:
    """Run without the caches (and empty them), then put the setting back."""
    was = _on
    set_enabled(False)
    try:
        yield
    finally:
        set_enabled(was)


def clear() -> None:
    """Drop every entry and zero the counters (tests reset between cases with this)."""
    global stats
    with _lock:
        _generation.clear()
        _readers.clear()
        _effort.clear()
        stats = Stats()


def db_file(conn: sqlite3.Connection) -> str | None:
    """The main DB file of ``conn``, normalised; None for an in-memory or temporary DB."""
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            path = str(row[2] or "")
            return os.path.normcase(os.path.realpath(path)) if path else None
    return None  # pragma: no cover - SQLite always lists main


def generation(conn: sqlite3.Connection, path: str) -> str:
    """The DB generation of ``conn`` (see the module docstring)."""
    h = hashlib.sha256()
    st = os.stat(path)
    h.update(f"file {st.st_dev} {st.st_ino}\n".encode())
    tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "export_run" in tables:
        for r in conn.execute(
            "SELECT id, run_key, created_at, fingerprint, sources_json FROM export_run"
            " ORDER BY id"
        ):
            h.update(repr(tuple(r)).encode("utf-8"))
    if "kv" in tables:
        row = conn.execute(
            "SELECT value FROM kv WHERE key = ?", (BUILD_KEY,)
        ).fetchone()
        h.update(f"build {row[0] if row else None}\n".encode())
    for t in ("structure", "structure_run"):
        if t in tables:
            n, top = conn.execute(f"SELECT count(*), max(rowid) FROM {t}").fetchone()
            h.update(f"{t} {n} {top}\n".encode())
    return h.hexdigest()


def scope(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """(DB path, generation) for keys, or None when the caches are off or the DB has no file.
    A path whose generation changed drops every entry of the old one first."""
    if not _on:
        return None
    path = db_file(conn)
    if path is None:
        return None
    gen = generation(conn, path)
    with _lock:
        old = _generation.get(path)
        if old != gen:
            if old is not None:
                for k in [k for k in _readers if k[0] == path]:
                    del _readers[k]
                for k in [k for k in _effort if k[0] == path]:  # type: ignore[index]
                    del _effort[k]
                stats.dropped += 1
            _generation[path] = gen
    return path, gen


@contextmanager
def reader[R](
    conn: sqlite3.Connection,
    run_id: int,
    make: Callable[[sqlite3.Connection, int], R],
    size: Callable[[R], int],
    bind: Callable[[R, sqlite3.Connection | None], None],
) -> Iterator[R]:
    """The run's shared batch reader, bound to ``conn`` and held for the block (a fresh one,
    not kept, when the caches are off or the DB has no file)."""
    where = scope(conn)
    if where is None:
        yield make(conn, run_id)
        return
    key = (where[0], where[1], run_id)
    with _lock:
        slot = _readers.get(key)
        if slot is None:
            stats.reader_misses += 1
            slot = _readers[key] = _Slot(make(conn, run_id))
            while len(_readers) > READERS_MAX:
                _readers.popitem(last=False)
        else:
            stats.reader_hits += 1
            _readers.move_to_end(key)
    with slot.lock:
        bind(slot.reader, conn)
        try:
            yield slot.reader
        except progress.Cancelled:
            raise  # cancelled between steps: what it read is whole
        except BaseException:
            # A build that failed part way may have left the reader half loaded: drop it.
            with _lock:
                if _readers.get(key) is slot:
                    del _readers[key]
            raise
        finally:
            bind(slot.reader, None)
        if size(slot.reader) > READER_MAX_RECIPES:
            with _lock:
                if _readers.get(key) is slot:
                    del _readers[key]


def effort_get(key: Hashable) -> object:
    """A cached component solve, or :data:`MISS`."""
    with _lock:
        if key not in _effort:
            stats.effort_misses += 1
            return MISS
        stats.effort_hits += 1
        _effort.move_to_end(key)
        return _effort[key]


def effort_put(key: Hashable, value: object) -> None:
    with _lock:
        if _generation.get(key[0]) != key[1]:  # type: ignore[index]
            return  # the DB changed while it was solved: not kept
        _effort[key] = value
        while len(_effort) > EFFORT_MAX:
            _effort.popitem(last=False)


def sizes() -> dict[str, int]:
    with _lock:
        return {"readers": len(_readers), "effort": len(_effort)}
