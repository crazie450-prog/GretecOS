"""WO-0.8: scripts/backup_world.ps1 against temporary folders (Windows only; touches nothing else)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows PowerShell script"
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup_world.ps1"
HOUR_AGO = time.time() - 3600


def run(
    instance: Path, dest: Path, log: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-Instance",
            str(instance),
            "-Dest",
            str(dest),
            "-LogFile",
            str(log),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        # CI runs pytest from PowerShell 7; its PSModulePath breaks Windows PowerShell 5.1's modules.
        env={k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"},
        timeout=120,
    )


def make_zip(path: Path, mtime: float = HOUR_AGO) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("level.dat", "x" * 500)
    os.utime(path, (mtime, mtime))


def snapshot(folder: Path) -> dict[str, tuple[int, float]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime) for p in folder.iterdir()}


def test_copies_only_complete_backups_and_prunes(tmp_path: Path) -> None:
    backups = tmp_path / "GTNH-Lab" / "minecraft" / "backups"
    backups.mkdir(parents=True)
    dest = tmp_path / "world"
    dest.mkdir()
    # complete: copied
    make_zip(backups / "2026-11-02-10-00-00.zip")
    # just written: skipped
    make_zip(backups / "2026-11-02-10-30-00.zip", mtime=time.time())
    # not a complete zip yet: skipped
    (backups / "2026-11-02-09-30-00.zip").write_bytes(b"PK\x03\x04 truncated")
    os.utime(backups / "2026-11-02-09-30-00.zip", (HOUR_AGO, HOUR_AGO))
    # held open for writing during the first run: skipped, then copied on the second
    make_zip(backups / "2026-11-02-09-00-00.zip")
    # an old copy past 14 days: kept, because the newest 12 copies always stay
    make_zip(dest / "2026-10-01-00-00-00.zip", mtime=time.time() - 20 * 86400)
    (dest / "2026-10-02-00-00-00.zip.partial").write_bytes(b"left by a crash")
    before = snapshot(backups)

    with open(backups / "2026-11-02-09-00-00.zip", "ab"):
        first = run(tmp_path / "GTNH-Lab", dest, tmp_path / "log.txt")
    assert first.returncode == 0, first.stdout + first.stderr
    assert sorted(p.name for p in dest.iterdir()) == [
        "2026-10-01-00-00-00.zip",
        "2026-11-02-10-00-00.zip",
    ]
    assert zipfile.ZipFile(dest / "2026-11-02-10-00-00.zip").namelist() == ["level.dat"]
    assert "still open for writing" in first.stdout
    assert snapshot(backups) == before  # the source is never written

    second = run(tmp_path / "GTNH-Lab", dest, tmp_path / "log.txt")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "2026-11-02-09-00-00.zip" in {p.name for p in dest.iterdir()}
    assert not list(dest.glob("*.partial"))


def test_no_backups_folder_is_a_no_op(tmp_path: Path) -> None:
    (tmp_path / "GTNH-Play" / "minecraft").mkdir(parents=True)
    result = run(tmp_path / "GTNH-Play", tmp_path / "world", tmp_path / "log.txt")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing to do" in result.stdout
    assert not (tmp_path / "world").exists()


def test_prunes_old_copies_beyond_the_floor(tmp_path: Path) -> None:
    (tmp_path / "GTNH-Lab" / "minecraft" / "backups").mkdir(parents=True)
    dest = tmp_path / "world"
    dest.mkdir()
    for day in range(1, 6):  # five copies, 30-34 days old
        make_zip(
            dest / f"2026-10-0{day}-00-00-00.zip",
            mtime=time.time() - (35 - day) * 86400,
        )
    result = run(tmp_path / "GTNH-Lab", dest, tmp_path / "log.txt", "-KeepAtLeast", "2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in dest.iterdir()) == [
        "2026-10-04-00-00-00.zip",
        "2026-10-05-00-00-00.zip",
    ]


def test_skips_a_copy_that_would_fill_the_disk(tmp_path: Path) -> None:
    backups = tmp_path / "GTNH-Lab" / "minecraft" / "backups"
    backups.mkdir(parents=True)
    make_zip(backups / "2026-11-02-10-00-00.zip")
    dest = tmp_path / "world"
    result = run(
        tmp_path / "GTNH-Lab", dest, tmp_path / "log.txt", "-MinFreeGB", "100000000"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "would leave less than 100000000 GB free" in result.stdout
    assert not list(dest.iterdir())
