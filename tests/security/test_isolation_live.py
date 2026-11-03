"""WO-0.8 acceptance on the real PC: run as gos-dev, after WO-0.3 creates Export and Lab.

    # in a gos-dev PowerShell, from C:\\GregtecOS-dev\\GretecOS
    $env:GOS_LIVE_ISOLATION = '1'
    $env:GOS_CLAYTON_HOME = 'C:\\Users\\<you>\\GregtecOS'
    uv run pytest tests/security/test_isolation_live.py -v

Non-destructive: it only asks Windows for access rights and closes each handle at once. The one
file it creates (in Lab and Export) is made delete-on-close, so it is gone when the handle closes.
It refuses to run elevated, because an administrator would pass every check.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from conftest import Probe

pytestmark = pytest.mark.skipif(
    not (sys.platform == "win32" and os.environ.get("GOS_LIVE_ISOLATION") == "1"),
    reason="live check on the PC: set GOS_LIVE_ISOLATION=1 and run as gos-dev",
)

PRISM = Path(os.environ.get("GOS_PRISM_ROOT", r"C:\Games\PrismLauncher"))
APP = Path(os.environ.get("GOS_APP", r"C:\GregtecOS-app"))
MANAGED = Path(r"C:\Program Files\ClaudeCode\managed-settings.d\gregtec-os.json")


def instance(name: str) -> Path:
    folder = PRISM / "instances" / name
    return next(
        (folder / d for d in ("minecraft", ".minecraft") if (folder / d).is_dir()),
        folder,
    )


@pytest.fixture(scope="module", autouse=True)
def as_gos_dev() -> None:
    if ctypes.windll.shell32.IsUserAnAdmin():
        pytest.fail("running elevated: run this as gos-dev in a normal shell")


@pytest.fixture(scope="module")
def clayton_home() -> Path:
    home = os.environ.get("GOS_CLAYTON_HOME")
    if not home:
        pytest.fail("set GOS_CLAYTON_HOME to Clayton's GregtecOS folder")
    return Path(home)


def assert_read_only(probe: Probe, files: list[Path]) -> None:
    for f in files:
        assert probe.can(f, probe.FILE_READ_DATA), f"can't read {f}"
        for right in (probe.FILE_WRITE_DATA, probe.DELETE, probe.WRITE_DAC):
            assert not probe.can(f, right), (
                f"gos-dev can change {f} (access {right:#x})"
            )


def test_play_is_read_only(probe: Probe) -> None:
    game = instance("GTNH-Play")
    files = [PRISM / "instances" / "GTNH-Play" / "instance.cfg"]
    files += [p for p in (game / "options.txt",) if p.is_file()]
    files += sorted((game / "mods").glob("*.jar"))[:3]
    files += sorted((game / "saves").glob("*/level.dat"))[:1]
    assert_read_only(probe, files)
    assert not probe.can_create(game)
    assert not probe.can(PRISM / "instances" / "GTNH-Play", probe.DELETE, folder=True)
    assert not probe.can_create(PRISM / "instances")  # no new instance folders either


def test_gos_app_is_read_only(probe: Probe) -> None:
    scripts = APP / "scripts"
    assert_read_only(
        probe, [scripts / "setup_gos_dev.ps1", scripts / "backup_world.ps1"]
    )
    assert not probe.can_create(scripts)
    assert not probe.can(APP, probe.DELETE, folder=True)


def test_world_backups_are_closed(probe: Probe, clayton_home: Path) -> None:
    backups = clayton_home / "backups" / "world"
    assert not probe.can(backups, probe.FILE_READ_DATA, folder=True)
    assert not probe.can_create(backups)


def test_vault_and_logs_are_read_only(probe: Probe, clayton_home: Path) -> None:
    """D82: gos-dev may read Clayton's vault\\ and logs\\, never change them."""
    folders = [clayton_home / d for d in ("vault", "logs")]
    present = [f for f in folders if f.is_dir()]
    if not present:
        pytest.skip("no vault or logs folder yet")
    files = [
        p
        for f in present
        for p in sorted(f.rglob("*.md"))[:2] + sorted(f.glob("*.log"))[:2]
    ]
    assert_read_only(probe, files)
    for folder in present:
        assert probe.can(folder, probe.FILE_READ_DATA, folder=True), (
            f"can't list {folder}"
        )
        assert not probe.can_create(folder)


def test_prism_root_is_closed(probe: Probe) -> None:
    assert not probe.can(PRISM, probe.FILE_READ_DATA, folder=True)
    accounts = PRISM / "accounts.json"
    if accounts.exists():  # exists() needs no access to the file itself
        assert not probe.can(accounts, probe.FILE_READ_DATA)
    assert not probe.can(PRISM, probe.DELETE, folder=True)


def test_claude_code_policy_is_protected(probe: Probe) -> None:
    assert_read_only(probe, [MANAGED])


@pytest.mark.parametrize("name", ["GTNH-Lab", "GTNH-Export"])
def test_lab_and_export_are_writable(probe: Probe, name: str) -> None:
    assert probe.can_create(instance(name))
