"""WO-5.3: scripts/set_comm_ducking.ps1 on a throwaway registry key (Windows only; the real
communications setting is never touched: it is Clayton's, D205)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows PowerShell script"
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "set_comm_ducking.ps1"


@pytest.fixture
def key() -> Iterator[str]:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    import winreg

    sub = rf"Software\GregtecOS-test-{uuid.uuid4().hex}"
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{sub}\Audio").Close()
    try:
        yield rf"HKCU:\{sub}\Audio"
    finally:
        for name in (rf"{sub}\Audio", sub):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, name)
            except OSError:
                pass


def run(
    key: str, backup: Path | None, *extra: str, gos_home: Path | None = None
) -> str:
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    if gos_home is not None:
        env["GOS_HOME"] = str(gos_home)
    backup_args = ["-BackupFile", str(backup)] if backup is not None else []
    done = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-KeyPath",
            key,
            *backup_args,
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def value(key: str) -> int | None:
    import winreg

    sub = key.removeprefix("HKCU:\\")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as k:
        try:
            return int(winreg.QueryValueEx(k, "UserDuckingPreference")[0])
        except FileNotFoundError:
            return None


def test_sets_do_nothing_and_undo_restores_the_default(
    key: str, tmp_path: Path
) -> None:
    backup = tmp_path / "before.txt"
    assert "not set" in run(key, backup, "-Show")
    out = run(key, backup)
    assert (
        value(key) == 3
        and "do nothing" in out
        and backup.read_text().strip() == "unset"
    )
    assert "already" in run(key, backup)
    run(key, backup, "-Undo")
    assert value(key) is None and not backup.exists()


def test_the_backup_goes_under_gos_home_when_it_is_set(
    key: str, tmp_path: Path
) -> None:
    run(key, None, gos_home=tmp_path)
    backup = tmp_path / "data" / "comm_ducking_before.txt"
    assert value(key) == 3 and backup.read_text().strip() == "unset"
    run(key, None, "-Undo", gos_home=tmp_path)
    assert value(key) is None and not backup.exists()


def test_undo_puts_back_the_value_it_found(key: str, tmp_path: Path) -> None:
    import winreg

    sub = key.removeprefix("HKCU:\\")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "UserDuckingPreference", 0, winreg.REG_DWORD, 2)
    backup = tmp_path / "before.txt"
    run(key, backup)
    assert value(key) == 3
    assert "50%" in run(key, backup, "-Undo")
    assert value(key) == 2
