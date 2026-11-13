"""GT meta names (what OC's ``getName()`` gives) to machines in the game DB (WO-7.4; D214, D219).

No OC or Computronics method gives a machine's ``mte_id``, so the core maps GT's meta tile entity
name through the export's ``meta_name`` (``gt_machines.jsonl`` of the DB's newest export run, in
``<GOS_HOME>/data/exports/<run>/``; the DB's ``machine`` table has no ``meta_name`` column, so
this reads the export instead of changing the schema). D214's rules: most names are one machine;
a controller and its deprecated twin share one, and the one without the ``deprecated`` machine
fact is taken; TecTech's Tesla coils share one name across four sizes, and those are kept as a
set: such a name resolves to no ``mte_id`` (GOS never guesses a size) and shows GT's name.

The map is read once per export run and kept; a missing DB or export gives no names, never an
error (the displays then show the meta name).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from gregtec.db import access
from gregtec.ingest import collect, machines

log = logging.getLogger("gregtec.bridge")

CHECK_S = 30.0  # how often the DB's newest export run is looked up again


@dataclass(frozen=True)
class Named:
    """A meta name as the DB knows it."""

    mte_id: int | None  # None: several machines the name can't tell apart
    name: str | None  # the DB's English name (None: not in the DB)
    choices: int  # how many machines carry the name (deprecated twins left out)


class MetaNames:
    """``meta name -> Named`` for the DB's newest export run (thread-safe, read lazily)."""

    def __init__(self, gos_home: Path) -> None:
        self.gos_home = gos_home
        self._lock = threading.Lock()
        self._run: str | None = None
        self._map: dict[str, Named] = {}
        self._checked = -CHECK_S

    def _load(self, conn: sqlite3.Connection, run: str) -> dict[str, Named]:
        folder = self.gos_home / collect.DATA_EXPORTS / run
        by_name: dict[str, list[int]] = {}
        try:
            for m in machines.read_machines(folder):
                if m.meta_name:
                    by_name.setdefault(m.meta_name, []).append(m.mte_id)
        except (OSError, ValueError) as exc:
            log.info("meta names not read", extra={"why": str(exc)[:200]})
            return {}
        rows = conn.execute(
            "SELECT m.mte_id, m.name_en, EXISTS(SELECT 1 FROM machine_fact f WHERE "
            "f.machine_id = m.id AND f.key = 'deprecated') FROM machine m"
        ).fetchall()
        db = {int(r[0]): (r[1], bool(r[2])) for r in rows if r[0] is not None}
        out: dict[str, Named] = {}
        for meta, ids in by_name.items():
            live = [i for i in ids if not db.get(i, (None, False))[1]] or ids
            if len(live) == 1:
                out[meta] = Named(live[0], db.get(live[0], (None, False))[0], 1)
            else:
                out[meta] = Named(None, None, len(live))
        return out

    def table(self) -> dict[str, Named]:
        """Every meta name of the newest run (looked up again at most every :data:`CHECK_S`)."""
        with self._lock:
            now = time.monotonic()
            if now - self._checked < CHECK_S:
                return self._map
            self._checked = now
            db = access.db_path(self.gos_home)
            if not db.is_file():
                self._map, self._run = {}, None
                return self._map
            try:
                conn = access.connect(db, readonly=True)
            except (access.SchemaError, sqlite3.Error):
                return self._map
            try:
                row = conn.execute(
                    "SELECT run_key FROM export_run ORDER BY id DESC LIMIT 1"
                ).fetchone()
                run = str(row[0]) if row and row[0] else None
                if run is None:
                    self._map, self._run = {}, None
                elif run != self._run:
                    self._map, self._run = self._load(conn, run), run
            except sqlite3.Error as exc:
                log.info("meta names not read", extra={"why": str(exc)[:200]})
            finally:
                conn.close()
            return self._map

    def get(self, meta_name: str | None) -> Named | None:
        """The DB's view of ``meta_name``; None when there is no DB, export or such name."""
        if not meta_name:
            return None
        return self.table().get(meta_name)
