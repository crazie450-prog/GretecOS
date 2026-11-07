"""The GT pin check (WO-1.9, CLAUDE.md: GT pin 5.09.54.265, D148).

GOS's GT math (WO-3.1, ``planner/gtmath``) is a port of one GT version and is gated by that
version's own unit tests. An export of another GT version must never be planned with it quietly:
:func:`problem` says so loudly; ``gos db build`` and ``gos doctor`` exit 1 on it, and the
playbook refuses to adopt such an update until the pin has been bumped by the procedure in
``docs/runbooks/pack_update.md`` (which changes :data:`gregtec.config.GT_PIN` in the repo, so
the deployed code carries the new pin).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from gregtec import config
from gregtec.db import access

RUNBOOK = "docs/runbooks/pack_update.md (GT pin bump)"


def problem(gt_version: object, pin: str | None = None) -> str | None:
    """Why an export of ``gt_version`` doesn't match the pin (default
    :data:`gregtec.config.GT_PIN`), or None when it does."""
    pin = config.GT_PIN if pin is None else pin
    if gt_version == pin:
        return None
    seen = "no GT version" if gt_version in (None, "") else f"GT {gt_version}"
    return (
        f"GT PIN MISMATCH: the export has {seen}, GOS is pinned to GT {pin}. GOS's GT math "
        f"must not be used on it until the GT pin bump is done ({RUNBOOK})"
    )


def check_db(home: Path) -> tuple[bool, str]:
    """(ok, line): the pin check of the newest run in ``home``'s DB, for ``gos doctor``. No DB
    yet, or one that can't be read, is a note, not a mismatch."""
    db = access.db_path(home)
    head = f"GT pin {config.GT_PIN}"
    if not db.is_file():
        return True, f"{head}: no DB yet ({db})"
    try:
        conn = access.connect(db, readonly=True)
        try:
            row = conn.execute(
                "SELECT run_key, gt_version FROM export_run"
                " ORDER BY created_at DESC, run_key DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, access.SchemaError) as exc:
        return True, f"{head}: can't read {db} ({exc})"
    if row is None:
        return True, f"{head}: the DB has no export run yet"
    why = problem(row[1])
    if why is not None:
        return False, f"!!! the DB's newest run {row[0]}: {why}"
    return True, f"{head}: the DB's newest run {row[0]} matches"
