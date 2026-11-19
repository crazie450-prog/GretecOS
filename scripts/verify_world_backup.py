r"""Restore an off-instance world backup into a scratch folder and check it (WO-11.2). Clayton
runs this; gos-dev can't read GOS_HOME\backups\world (D75).

    GOS_APP\current\.venv\Scripts\python.exe GOS_APP\current\scripts\verify_world_backup.py
    ... verify_world_backup.py [--zip FILE] [--backups DIR] [--work DIR] [--keep] [--config PATH]

It reads the newest backup zip (or --zip), never writes into a Prism instance, and never
restores over Play. See gregtec.ops.worldbackup and docs/runbooks/ops.md.
"""

from gregtec.ops.worldbackup import main

if __name__ == "__main__":
    raise SystemExit(main())
