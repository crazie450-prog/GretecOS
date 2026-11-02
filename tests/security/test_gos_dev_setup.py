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
import shutil
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
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

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
MANAGED = Path(r"C:\Program Files\ClaudeCode\managed-settings.d")
LOGON32_LOGON_INTERACTIVE = 2
LOGON32_LOGON_NETWORK = 3


@dataclass(frozen=True)
class Setup:
    user: str
    password: str = field(repr=False)
    base: Path  # stands in for C:\ (it inherits "Authenticated Users: Modify" from it)
    source: Path  # Clayton's own copy of the scripts, in the admin profile
    scan: Path  # where the setup looks for other Minecraft installs
    app: Path
    dev: Path
    prism: Path
    home: Path
    task: str

    @property
    def instances(self) -> Path:
        return self.prism / "instances"

    def args(self, *, download: bool, scan: Path | None) -> str:
        args = (
            f"-DevUser '{self.user}' -AppRoot '{self.app}' -DevRoot '{self.dev}' "
            f"-PrismRoot '{self.prism}' -GosHome '{self.home}' -TaskName '{self.task}' "
            f"-InstallScanRoots '{scan or self.scan}' -Yes "
            "-DevPassword (ConvertTo-SecureString $env:GOS_CI_PASSWORD -AsPlainText -Force)"
        )
        return args if download else args + " -SkipPrismDownload"


def windows_powershell_env() -> dict[str, str]:
    """os.environ without PSModulePath.

    CI runs pytest from PowerShell 7, whose PSModulePath stops Windows PowerShell 5.1 loading its
    own modules (ConvertTo-SecureString, Get-Acl).
    """
    return {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}


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
        env={**windows_powershell_env(), "GOS_CI_PASSWORD": password},
        check=False,
        timeout=900,
    )


def run_setup(
    cfg: Setup,
    *,
    download: bool = False,
    source: Path | None = None,
    scan: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    setup = (source or cfg.source) / "setup_gos_dev.ps1"
    args = cfg.args(download=download, scan=scan)
    script = f"try {{ & '{setup}' {args}; exit $LASTEXITCODE }} "
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


def can_rename(path: Path) -> bool:
    moved = path.with_name(path.name + "-moved")
    try:
        path.rename(moved)
    except PermissionError:
        return False
    moved.rename(path)
    return True


def copy_scripts(to: Path) -> Path:
    to.mkdir(parents=True)
    for name in ("setup_gos_dev.ps1", "backup_world.ps1"):
        shutil.copyfile(SCRIPTS / name, to / name)
    return to


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
    base = Path(f"C:/gos-ci-{tag}")
    setup = Setup(
        user=f"gosci{tag}",
        password=f"Gos-{secrets.token_urlsafe(18)}-9a",
        base=base,
        source=copy_scripts(Path.home() / f"gos-ci-src-{tag}"),
        scan=Path(f"C:/gos-ci-{tag}-scan"),
        app=base / "app",
        dev=base / "dev",
        prism=base / "games" / "PrismLauncher",
        home=Path.home() / f"gos-ci-home-{tag}",
        task=f"GregtecOS CI {tag}",
    )
    setup.scan.mkdir()
    (base / "games").mkdir(
        parents=True
    )  # like an existing C:\Games: inherited permissions
    yield setup
    leftovers = [
        base,
        setup.home,
        setup.source,
        setup.scan,
        Path(f"C:/gos-ci-{tag}-old"),
    ]
    powershell(
        f"Unregister-ScheduledTask -TaskPath '\\GregtecOS\\' -TaskName '{setup.task}' -Confirm:$false -ErrorAction SilentlyContinue; "
        f"Remove-LocalUser -Name '{setup.user}' -ErrorAction SilentlyContinue; "
        f"Remove-Item -LiteralPath '{MANAGED / 'gregtec-os.json'}' -Force -ErrorAction SilentlyContinue; "
        f"foreach ($p in {', '.join(f"'{p}'" for p in leftovers)}) {{ if (Test-Path -LiteralPath $p) {{ "
        "icacls $p /reset /T /C /Q | Out-Null; Remove-Item -LiteralPath $p -Recurse -Force } }"
    )


@pytest.fixture(scope="module")
def installed(cfg: Setup) -> Setup:
    first = run_setup(cfg, download=True)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "FAIL" not in first.stdout
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
        installed.base,
        installed.prism.parent,
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
        # Pre-existing like a real C:\Games: the script doesn't change who can add files there,
        # only that nobody but Clayton can rename it (test_dev_user_cannot_swap_folders).
        "prism parent": True,
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


def test_dev_user_cannot_swap_folders(installed: Setup) -> None:
    """gos-dev can't move a protected folder, or any folder above one, aside to plant its own."""
    c = installed
    with impersonate(c.user, c.password):
        renames = {
            "base (like C:\\ children)": can_rename(c.base),
            "prism parent (pre-existing)": can_rename(c.prism.parent),
            "prism root": can_rename(c.prism),
            "app": can_rename(c.app),
            "app/scripts": can_rename(c.app / "scripts"),
            "instances": can_rename(c.instances),
            "Play": can_rename(c.instances / "GTNH-Play"),
            "GOS_HOME": can_rename(c.home),
            "dev (its own area)": can_rename(c.dev),
        }
    assert renames == {
        "base (like C:\\ children)": False,
        "prism parent (pre-existing)": False,
        "prism root": False,
        "app": False,
        "app/scripts": False,
        "instances": False,
        "Play": False,
        "GOS_HOME": False,
        "dev (its own area)": True,
    }


def test_claude_code_deny_rules(installed: Setup) -> None:
    raw = (MANAGED / "gregtec-os.json").read_bytes()
    assert not raw.startswith(
        b"\xef\xbb\xbf"
    )  # a BOM would stop Claude Code starting at all
    rules = json.loads(raw)["permissions"]["deny"]
    prism = "//c/" + installed.prism.as_posix()[3:]
    home = "//c/" + installed.home.as_posix()[3:]
    assert set(rules) == {
        f"Edit({prism}/instances/GTNH-Play/**)",
        f"Edit(//c/{installed.app.as_posix()[3:]}/**)",
        f"Edit({home}/**)",
        f"Read({home}/backups/world/**)",
        f"Read({prism}/accounts.json)",
        f"Edit({prism}/accounts.json)",
    }
    # Only Read/Edit rules match paths; Windows paths must be //<lowercase drive>/ with forward slashes.
    for rule in rules:
        assert rule.startswith(("Read(//c/", "Edit(//c/")), rule
        assert "\\" not in rule, rule


def test_backup_task_copies_play_backup(installed: Setup) -> None:
    info = powershell(
        f"$t = Get-ScheduledTask -TaskPath '\\GregtecOS\\' -TaskName '{installed.task}'; "
        "@{ logon = [string]$t.Principal.LogonType; exe = $t.Actions[0].Execute; "
        "args = $t.Actions[0].Arguments; cwd = $t.Actions[0].WorkingDirectory; "
        "interval = $t.Triggers[0].Repetition.Interval; duration = [string]$t.Triggers[0].Repetition.Duration } | ConvertTo-Json"
    )
    assert info.returncode == 0, info.stdout + info.stderr
    task = json.loads(info.stdout)
    assert task["logon"] == "S4U"
    # An absolute path: a bare powershell.exe would be found through PATH.
    assert (
        task["exe"].lower()
        == r"c:\windows\system32\windowspowershell\v1.0\powershell.exe"
    )
    assert task["cwd"] == str(installed.app / "scripts")
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


def test_self_check_finds_a_nested_leak(installed: Setup) -> None:
    saves = installed.instances / "GTNH-Play" / "minecraft" / "saves"
    saves.mkdir(exist_ok=True)
    grant = powershell(
        f"icacls '{saves}' /grant '*S-1-5-32-545:(OI)(CI)M' | Out-Null; exit $LASTEXITCODE"
    )
    assert grant.returncode == 0, grant.stdout + grant.stderr
    try:
        leaked = run_setup(installed)
        assert leaked.returncode == 1, leaked.stdout
        assert f"can change {saves}" in leaked.stdout
    finally:
        powershell(f"icacls '{saves}' /remove:g '*S-1-5-32-545' | Out-Null")
    assert run_setup(installed).returncode == 0


def test_refuses_to_run_from_a_copy_the_dev_user_can_change(installed: Setup) -> None:
    writable = copy_scripts(
        installed.base / "writable"
    )  # inherits Authenticated Users: Modify
    before = sddl([installed.app, installed.prism])
    refused = run_setup(installed, source=writable)
    assert refused.returncode == 1
    assert "Refusing to run from" in refused.stdout
    assert sddl([installed.app, installed.prism]) == before


def test_reports_an_existing_install_the_dev_user_can_write(installed: Setup) -> None:
    old = Path(str(installed.base) + "-old")
    instance = old / "MultiMC" / "instances" / "GTNH"
    instance.mkdir(parents=True)
    (instance / "instance.cfg").write_text("name=GTNH\n")
    before = sddl([instance])
    found = run_setup(installed, scan=old)
    assert found.returncode == 1, found.stdout
    assert f"FAIL  read   {instance}" in found.stdout
    assert "existing install, not changed by this script" in found.stdout
    assert sddl([instance]) == before  # reported, never changed
