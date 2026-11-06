"""The ``structure`` table: multiblock footprints from gtnh-extractor (WO-1.8, Appendix D, D123).

Schema module for WO-1.3 to absorb: :data:`SCHEMA` goes into ``schema.sql`` as it stands, and
:func:`ensure_schema` stops being needed. Until then it creates the tables itself, and it adds
D123's columns to an Appendix D ``structure`` table that a WO-1.3 database already has (every
added column is nullable, so ``ALTER TABLE ... ADD COLUMN`` works on it).

- One row per (controller, variant, source). ``variant`` is ``t<trigger stack size>`` plus
  ``;<channel>=<value>`` for each StructureLib channel the extractor set (:attr:`Variant.key`).
- The controller is keyed by ``registry_name`` + ``meta`` (``gregtech:gt.blockmachines`` + the
  MTE id), never a numeric block ID. ``machine_id`` is ``machine.id`` when a WO-1.3 ``machine``
  table has that controller's item, else NULL; :func:`link_machines` fills it in again.
- ``size_x/y/z`` are the extractor's bbox. JSON columns keep the extractor's own shapes, offsets
  from the controller with its front facing NORTH (-Z): ``blocks_json`` = ``[{d, block, meta}]``
  (every non-air cell; hatch cells hold their fallback casing), ``hatches_json`` =
  ``[{d, kinds, output_layer?}]``, ``subs_json`` = ``{channel: [{channel_value, block, meta}]}``
  (tier swaps that keep the shape, e.g. coils), ``channels_json`` and ``failures_json`` (the
  extractor's caveats, e.g. a variant family cut off at its sweep ceiling).
- :func:`load` replaces every ``source = 'gtnh-extractor'`` row in one transaction and records
  the dump's provenance in ``structure_run``. Rows from other sources (Lab captures of addon
  multis, plan 7.10) are kept. ``schematic_path`` stays NULL until Phase 10.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from gregtec.ingest import extractor
from gregtec.ingest.structures import Controller, Dataset, FormatError, read_dataset

SOURCE = "gtnh-extractor"
DB_FILE = Path("data", "gos.sqlite")  # in GOS_HOME (plan 4.3), as WO-1.3's db.access
DATASETS = Path(
    "data", "structures"
)  # in GOS_HOME: where `gos structures extract` writes

# Appendix D's columns first, then D123's.
STRUCTURE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("machine_id", "INT"),
    ("variant", "TEXT"),
    ("size_x", "INT"),
    ("size_y", "INT"),
    ("size_z", "INT"),
    ("blocks_json", "TEXT"),
    ("schematic_path", "TEXT"),
    ("source", "TEXT"),
    ("run_id", "INT"),
    ("registry_name", "TEXT"),
    ("meta", "INT"),
    ("name_en", "TEXT"),
    ("class_name", "TEXT"),
    ("trigger_stack_size", "INT"),
    ("channels_json", "TEXT"),
    ("hatches_json", "TEXT"),
    ("subs_json", "TEXT"),
    ("failures_json", "TEXT"),
)
_RUN = (
    "CREATE TABLE IF NOT EXISTS structure_run(id INTEGER PRIMARY KEY, loaded_at TEXT NOT NULL,"
    " source TEXT NOT NULL, dataset TEXT, pack_version TEXT, gt_version TEXT,"
    " mod_versions_json TEXT, generated_at TEXT, extractor_sha TEXT, controllers INT,"
    " failures_json TEXT)"
)
_TABLE = "CREATE TABLE IF NOT EXISTS structure({})".format(
    ", ".join(f"{name} {kind}" for name, kind in STRUCTURE_COLUMNS)
)
_INDEXES = (
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_structure_key"
        " ON structure(registry_name, meta, variant, source)"
    ),
    "CREATE INDEX IF NOT EXISTS ix_structure_machine ON structure(machine_id)",
)
SCHEMA = ";\n".join((_RUN, _TABLE, *_INDEXES)) + ";\n"  # for WO-1.3's schema.sql


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the structure tables, or add D123's columns to an Appendix D ``structure``."""
    conn.execute(_RUN)
    have = {row[1] for row in conn.execute("PRAGMA table_info(structure)")}
    if not have:
        conn.execute(_TABLE)
    for name, kind in STRUCTURE_COLUMNS:
        if have and name not in have:
            conn.execute(f"ALTER TABLE structure ADD COLUMN {name} {kind}")
    for sql in _INDEXES:
        conn.execute(sql)


def connect(path: Path) -> sqlite3.Connection:
    """Open ``path`` in WAL mode with autocommit, as WO-1.3's ``db.access.connect`` does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@dataclass
class Report:
    run_id: int
    dataset: Path
    pack_version: str
    gt_version: str | None
    controllers: int = 0
    variants: int = 0
    cells: int = 0
    hatch_slots: int = 0
    linked: int = 0  # controllers whose machine_id was found
    families: Counter[str] = field(default_factory=Counter)
    unnamed: int = 0  # controllers with no display name (an untranslated lang key)
    caveats: int = 0  # controllers the extractor noted a caveat on
    # Controllers the extractor could not dump: (registry#meta, reason).
    failures: tuple[tuple[str, str], ...] = ()

    def lines(self) -> list[str]:
        out = [
            f"structures: run {self.run_id} from {self.dataset}",
            f"  pack {self.pack_version}, GT {self.gt_version}",
            (
                f"  controllers {self.controllers} (with dimensions and cells), variants "
                f"{self.variants}, cells {self.cells}, hatch slots {self.hatch_slots}"
            ),
            "  by package: "
            + ", ".join(f"{k} {v}" for k, v in sorted(self.families.items())),
            f"  linked to machine rows: {self.linked}"
            + ("" if self.linked else " (no WO-1.3 machine table yet, or none match)"),
        ]
        if self.unnamed:
            out.append(f"  without an English name: {self.unnamed}")
        if self.caveats:
            out.append(f"  with an extractor caveat (failures_json): {self.caveats}")
        out.append(f"  not dumped by the extractor: {len(self.failures)}")
        out += [f"    {name}: {reason}" for name, reason in self.failures]
        return out


def load(
    conn: sqlite3.Connection,
    dataset: Path,
    *,
    gt_version: str | None = extractor.PINS[extractor.GT],
    now: datetime | None = None,
) -> Report:
    """Replace the extractor's rows with ``dataset``'s, in one transaction; return the counts."""
    data = read_dataset(dataset, gt_version=gt_version)
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    conn.execute("BEGIN IMMEDIATE")
    try:
        ensure_schema(conn)
        run_id = _insert_run(conn, data, stamp)
        conn.execute("DELETE FROM structure WHERE source = ?", (SOURCE,))
        report = Report(run_id, data.path, data.meta.pack_version, data.meta.gt_version)
        conn.executemany(
            "INSERT INTO structure({}) VALUES ({})".format(
                ", ".join(n for n, _ in STRUCTURE_COLUMNS),
                ", ".join("?" for _ in STRUCTURE_COLUMNS),
            ),
            _rows(data.controllers, run_id, report),
        )
        report.linked = link_machines(conn)
        report.failures = tuple((f.registry_name, f.reason) for f in data.meta.failures)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return report


def _insert_run(conn: sqlite3.Connection, data: Dataset, stamp: str) -> int:
    m = data.meta
    cur = conn.execute(
        "INSERT INTO structure_run(loaded_at, source, dataset, pack_version, gt_version,"
        " mod_versions_json, generated_at, extractor_sha, controllers, failures_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            stamp,
            SOURCE,
            str(data.path),
            m.pack_version,
            m.gt_version,
            _json(dict(m.mod_versions)),
            m.generated_at,
            m.extractor_sha,
            len(data.controllers),
            _json(
                [
                    {"registry_name": f.registry_name, "reason": f.reason}
                    for f in m.failures
                ]
            ),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _rows(
    controllers: Iterable[Controller], run_id: int, report: Report
) -> Iterable[tuple[object, ...]]:
    for c in controllers:
        report.controllers += 1
        report.families[c.family] += 1
        report.unnamed += c.name is None
        report.caveats += bool(c.failures)
        subs = _json(
            {
                channel: [
                    {"channel_value": s.channel_value, "block": s.block, "meta": s.meta}
                    for s in entries
                ]
                for channel, entries in sorted(c.substitutions.items())
            }
        )
        for v in c.variants:
            report.variants += 1
            report.cells += len(v.blocks)
            report.hatch_slots += len(v.hatch_slots)
            blocks = [
                {"d": list(b.d), "block": b.block, "meta": b.meta} for b in v.blocks
            ]
            hatches = [
                {"d": list(s.d), "kinds": list(s.kinds)}
                | ({} if s.output_layer is None else {"output_layer": s.output_layer})
                for s in v.hatch_slots
            ]
            yield (
                None,  # machine_id: link_machines
                v.key,
                *v.size,
                _json(blocks),
                None,  # schematic_path: Phase 10
                SOURCE,
                run_id,
                c.registry_name,
                c.meta,
                c.name,
                c.class_name,
                v.trigger_stack_size,
                _json(dict(sorted(v.channels.items()))),
                _json(hatches),
                subs,
                _json(list(c.failures)),
            )


def link_machines(conn: sqlite3.Connection) -> int:
    """Point ``machine_id`` at WO-1.3's ``machine`` rows by item registry name + meta.

    Returns how many controllers are linked; 0 (and nothing changed) without those tables.
    """
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not {"machine", "item"} <= tables:
        return 0
    conn.execute(
        "UPDATE structure SET machine_id = (SELECT m.id FROM machine AS m"
        " JOIN item AS i ON i.id = m.item_id WHERE i.registry_name = structure.registry_name"
        " AND i.meta = structure.meta AND i.nbt_hash = '' ORDER BY m.id LIMIT 1)"
        " WHERE registry_name IS NOT NULL"
    )
    row = conn.execute(
        "SELECT count(DISTINCT registry_name || '@' || meta) FROM structure"
        " WHERE source = ? AND machine_id IS NOT NULL",
        (SOURCE,),
    ).fetchone()
    return int(row[0])


def newest_dataset(gos_home: Path) -> Path | None:
    """The newest dataset ``gos structures extract`` wrote (names sort by UTC time)."""
    root = gos_home / DATASETS
    found = sorted(
        (p for p in root.glob("*") if (p / "multiblocks" / "_meta.json").is_file()),
        key=lambda p: p.name,
    )
    return found[-1] if found else None


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def main(gos_home: Path | None, dataset: Path | None, out: TextIO | None = None) -> int:
    """``gos structures load [DATASET]``: load a dump into ``GOS_HOME/data/gos.sqlite``."""
    out = out or sys.stdout
    home = gos_home or Path(os.environ.get("GOS_HOME") or Path.home() / "GregtecOS")
    path = dataset or newest_dataset(home)
    if path is None:
        print(
            f"No dataset in {home / DATASETS}; run `gos structures extract` first, or name "
            "one.",
            file=sys.stderr,
        )
        return 2
    conn = connect(home / DB_FILE)
    try:
        report = load(conn, path)
    except (FormatError, OSError, sqlite3.Error) as exc:
        print(f"Not loaded: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"Loaded into {home / DB_FILE}", file=out)
    for line in report.lines():
        print(line, file=out)
    return 0
