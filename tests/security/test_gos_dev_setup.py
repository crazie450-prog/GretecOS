"""WO-0.8: run scripts/setup_gos_dev.ps1 for real and probe the result as the dev user.

This is destructive: it creates a local user, changes ACLs, writes a Claude Code managed-settings
drop-in and registers a scheduled task. It runs only on the GitHub Actions Windows runner, which is
an ephemeral VM, when GITHUB_ACTIONS=true and GOS_ISOLATION_CI=1. It never runs on Clayton's PC.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import secrets
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not (
        sys.platform == "win32"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GOS_ISOLATION_CI") == "1"
    ),
    reason="destructive: runs only on the GitHub Actions Windows runner with GOS_ISOLATION_CI=1",
)

SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup_gos_dev.ps1"
MANAGED = Path(r"C:\Program Files\ClaudeCode\managed-settings.d")
LOGON32_LOGON_INTERACTIVE = 2
LOGON32_LOGON_NETWORK = 3


@dataclass(frozen=True)
class Setup:
    user: str
    password: str
    app: Path
    dev: Path
    prism: Path
    home: Path
    task: str

    @property
    def instances(self) -> Path:
        return self.prism / "instances"

    def args(self, *, download: bool) -> str:
        args = (
            f"-DevUser '{self.user}' -AppRoot '{self.app}' -DevRoot '{self.dev}' "
            f"-PrismRoot '{self.prism}' -GosHome '{self.home}' -TaskName '{self.task}' -Yes "
            "-DevPassword (ConvertTo-SecureString $env:GOS_CI_PASSWORD -AsPlainText -Force)"
        )
        return args if download else args + " -SkipPrismDownload"


def powershell(command: str, password: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "GOS_CI_PASSWORD": password},
        check=False,
        timeout=900,
    )


def run_setup(
    cfg: Setup, *, download: bool = False
) -> subprocess.CompletedProcess[str]:
    script = f"try {{ & '{SETUP}' {cfg.args(download=download)}; exit $LASTEXITCODE }} "
    script += "catch { $_ | Out-String | Write-Host; exit 1 }"
    return powershell(script, cfg.password)


def sddl(paths: list[Path]) -> dict[str, str]:
    quoted = ",".join(f"'{p}'" for p in paths)
    out = powershell(
        f"@({quoted}) | ForEach-Object {{ @{{ p = $_; s = (Get-Acl -LiteralPath $_).Sddl }} }} | ConvertTo-Json"
    )
    assert out.returncode == 0, out.stdout + out.stderr
    return {row["p"]: row["s"] for row in json.loads(out.stdout)}


@contextlib.contextmanager
def impersonate(user: str, password: str) -> Iterator[None]:
    """Run the block with the calling thread impersonating `user` (real NTFS access checks)."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token = ctypes.c_void_p()
    for logon_type in (LOGON32_LOGON_INTERACTIVE, LOGON32_LOGON_NETWORK):
        if advapi32.LogonUserW(user, ".", password, logon_type, 0, ctypes.byref(token)):
            break
    else:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not advapi32.ImpersonateLoggedOnUser(token):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            name = ctypes.create_unicode_buffer(257)
            size = ctypes.c_uint32(len(name))
            assert advapi32.GetUserNameW(name, ctypes.byref(size)), (
                "GetUserNameW failed"
            )
            assert name.value.lower() == user.lower(), (
                f"impersonation did not take: running as {name.value}"
            )
            yield
        finally:
            advapi32.RevertToSelf()
    finally:
        kernel32.CloseHandle(token)


def can_write(directory: Path) -> bool:
    probe = directory / f"probe-{secrets.token_hex(4)}.txt"
    try:
        probe.write_text("probe")
    except PermissionError:
        return False
    probe.unlink()
    return True


def can_read(path: Path) -> bool:
    try:
        if path.is_dir():
            os.listdir(path)
        else:
            path.read_bytes()
    except PermissionError:
        return False
    return True


@pytest.fixture(scope="module")
def cfg() -> Iterator[Setup]:
    tag = secrets.token_hex(4)
    # Directly under C:\, so the "Authenticated Users: Modify" it inherits is in play.
    base = Path(f"C:/gos-ci-{tag}")
    setup = Setup(
        user=f"gosci{tag}",
        password=f"Gos-{secrets.token_urlsafe(18)}-9a",
        app=base / "app",
        dev=base / "dev",
        prism=base / "games" / "PrismLauncher",
        home=Path.home() / f"gos-ci-home-{tag}",
        task=f"GregtecOS CI {tag}",
    )
    yield setup
    powershell(
        f"Unregister-ScheduledTask -TaskPath '\\GregtecOS\\' -TaskName '{setup.task}' -Confirm:$false -ErrorAction SilentlyContinue; "
        f"Remove-LocalUser -Name '{setup.user}' -ErrorAction SilentlyContinue; "
        f"Remove-Item -LiteralPath '{MANAGED / 'gregtec-os.json'}' -Force -ErrorAction SilentlyContinue; "
        f"foreach ($p in '{base}', '{setup.home}') {{ if (Test-Path -LiteralPath $p) {{ "
        "icacls $p /reset /T /C /Q | Out-Null; Remove-Item -LiteralPath $p -Recurse -Force }} }}"
    )


@pytest.fixture(scope="module")
def installed(cfg: Setup) -> Setup:
    first = run_setup(cfg, download=True)
    assert first.returncode == 0, first.stdout + first.stderr
    # What WO-0.7 / WO-0.3 and Prism itself would create later, as Clayton.
    for name in ("GTNH-Play", "GTNH-Export", "GTNH-Lab"):
        (cfg.instances / name / "minecraft").mkdir(parents=True)
        (cfg.instances / name / "minecraft" / "options.txt").write_text(
            "pauseOnLostFocus:false\n"
        )
    (cfg.prism / "accounts.json").write_text('{"accounts": []}')
    (cfg.home / "data" / "gos.sqlite").write_bytes(b"SQLite format 3\x00")
    (cfg.home / "backups" / "world" / "2026-01-01-00-00-00.zip").write_bytes(b"PK")
    second = run_setup(cfg)
    assert second.returncode == 0, second.stdout + second.stderr
    return cfg


def test_prism_portable_installed(installed: Setup) -> None:
    assert (installed.prism / "prismlauncher.exe").is_file()
    assert (installed.prism / "portable.txt").is_file()


def test_rerun_is_idempotent(installed: Setup) -> None:
    paths = [
        installed.app,
        installed.dev,
        installed.prism,
        installed.instances,
        installed.home / "data",
    ]
    paths += [installed.instances / n for n in ("GTNH-Play", "GTNH-Export", "GTNH-Lab")]
    before = sddl(paths)
    rerun = run_setup(installed)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert sddl(paths) == before


def test_dev_user_access(installed: Setup) -> None:
    c = installed
    with impersonate(c.user, c.password):
        writes = {
            "dev": can_write(c.dev),
            "app": can_write(c.app),
            "app/scripts": can_write(c.app / "scripts"),
            "prism parent": can_write(c.prism.parent),
            "prism root": can_write(c.prism),
            "instances": can_write(c.instances),
            "Play": can_write(c.instances / "GTNH-Play" / "minecraft"),
            "Export": can_write(c.instances / "GTNH-Export" / "minecraft"),
            "Lab": can_write(c.instances / "GTNH-Lab" / "minecraft"),
            "GOS_HOME": can_write(c.home),
            "data": can_write(c.home / "data"),
            "backups/world": can_write(c.home / "backups" / "world"),
            "managed settings": can_write(MANAGED),
        }
        reads = {
            "app script": can_read(c.app / "scripts" / "backup_world.ps1"),
            "Play options": can_read(
                c.instances / "GTNH-Play" / "minecraft" / "options.txt"
            ),
            "data file": can_read(c.home / "data" / "gos.sqlite"),
            "managed drop-in": can_read(MANAGED / "gregtec-os.json"),
            "accounts.json": can_read(c.prism / "accounts.json"),
            "prism root listing": can_read(c.prism),
            "GOS_HOME listing": can_read(c.home),
            "backups/world listing": can_read(c.home / "backups" / "world"),
        }
    assert writes == {
        "dev": True,
        "app": False,
        "app/scripts": False,
        "prism parent": False,
        "prism root": False,
        "instances": False,
        "Play": False,
        "Export": True,
        "Lab": True,
        "GOS_HOME": False,
        "data": False,
        "backups/world": False,
        "managed settings": False,
    }
    assert reads == {
        "app script": True,
        "Play options": True,
        "data file": True,
        "managed drop-in": True,
        "accounts.json": False,
        "prism root listing": False,
        "GOS_HOME listing": False,
        "backups/world listing": False,
    }


def test_claude_code_deny_rules(installed: Setup) -> None:
    rules = json.loads((MANAGED / "gregtec-os.json").read_text(encoding="utf-8"))[
        "permissions"
    ]["deny"]
    prism = "//c/" + installed.prism.as_posix()[3:]
    assert f"Edit({prism}/instances/GTNH-Play/**)" in rules
    assert f"Read({prism}/accounts.json)" in rules
    assert any(r.startswith("Edit(//c/") and r.endswith("/app/**)") for r in rules)


def test_backup_task_copies_play_backup(installed: Setup) -> None:
    info = powershell(
        f"$t = Get-ScheduledTask -TaskPath '\\GregtecOS\\' -TaskName '{installed.task}'; "
        "@{ logon = [string]$t.Principal.LogonType; args = $t.Actions[0].Arguments; "
        "interval = $t.Triggers[0].Repetition.Interval; duration = [string]$t.Triggers[0].Repetition.Duration } | ConvertTo-Json"
    )
    assert info.returncode == 0, info.stdout + info.stderr
    task = json.loads(info.stdout)
    assert task["logon"] == "S4U"
    assert str(installed.app / "scripts" / "backup_world.ps1") in task["args"]
    assert task["interval"] == "PT30M"
    assert task["duration"] == ""  # repeats indefinitely

    backups = installed.instances / "GTNH-Play" / "minecraft" / "backups"
    backups.mkdir()
    name = "2026-11-02-10-00-00.zip"
    with zipfile.ZipFile(backups / name, "w") as z:
        z.writestr("level.dat", "x" * 1000)
    old = time.time() - 3600
    os.utime(backups / name, (old, old))

    started = powershell(
        f"Start-ScheduledTask -TaskPath '\\GregtecOS\\' -TaskName '{installed.task}'"
    )
    assert started.returncode == 0, started.stdout + started.stderr
    copied = installed.home / "backups" / "world" / name
    deadline = time.monotonic() + 120
    while not copied.exists() and time.monotonic() < deadline:
        time.sleep(2)
    log = installed.home / "logs" / "backup_world.log"
    assert copied.exists(), log.read_text() if log.exists() else "no backup_world.log"
    assert zipfile.ZipFile(copied).namelist() == ["level.dat"]


def test_self_check_fails_on_a_leak(installed: Setup) -> None:
    play = installed.instances / "GTNH-Play"
    grant = powershell(
        f"icacls '{play}' /grant '*S-1-5-32-545:(OI)(CI)M' | Out-Null; exit $LASTEXITCODE"
    )
    assert grant.returncode == 0, grant.stdout + grant.stderr
    try:
        leaked = run_setup(installed)
        assert leaked.returncode == 1
        assert "FAIL" in leaked.stdout
    finally:
        powershell(f"icacls '{play}' /remove:g '*S-1-5-32-545' | Out-Null")
