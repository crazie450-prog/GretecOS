"""WO-2.1, D116: scripts/install_login_start.ps1 against a throwaway HKCU key (Windows only).

The script runs from a copy in a temporary GOS_APP, as it would from GOS_APP\\scripts, and writes
to ``HKCU\\Software\\GregtecOS-test-<random>``, never the real Run key.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install_login_start.ps1"


@pytest.fixture
def run_key() -> Iterator[str]:
    import winreg

    name = f"Software\\GregtecOS-test-{secrets.token_hex(4)}"
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, name).Close()
    yield name
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, name)


def run(
    script: Path, *args: str, allow_elevated: bool = True
) -> subprocess.CompletedProcess[str]:
    # CI runs pytest from PowerShell 7; its PSModulePath breaks Windows PowerShell 5.1's modules.
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    # GitHub's Windows runners run elevated, which the script refuses (review finding 5).
    env.pop("GOS_ALLOW_ELEVATED", None)
    if allow_elevated:
        env["GOS_ALLOW_ELEVATED"] = "1"
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )


def value(run_key: str) -> str | None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as key:
        try:
            return str(winreg.QueryValueEx(key, "GregtecOS")[0])
        except FileNotFoundError:
            return None


def make_app(tmp_path: Path) -> tuple[Path, Path, Path]:
    app = tmp_path / "GregtecOS-app"
    exe = app / "current" / ".venv" / "Scripts" / "gos-supervisor.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    (app / "scripts").mkdir()
    script = app / "scripts" / SCRIPT.name
    shutil.copy2(SCRIPT, script)
    return app, exe, script


def test_writes_and_removes_the_run_value(tmp_path: Path, run_key: str) -> None:
    app, exe, script = make_app(tmp_path)
    key = f"HKCU:\\{run_key}"
    done = run(script, "-AppRoot", str(app), "-RunKey", key)
    assert done.returncode == 0, done.stdout + done.stderr
    assert value(run_key) == f'"{exe}" --login'
    # Idempotent, and --config is passed on quoted.
    cfg = tmp_path / "my config" / "gos.toml"
    done = run(script, "-AppRoot", str(app), "-RunKey", key, "-Config", str(cfg))
    assert done.returncode == 0, done.stdout + done.stderr
    assert value(run_key) == f'"{exe}" --login --config "{cfg}"'
    done = run(script, "-AppRoot", str(app), "-RunKey", key, "-Remove")
    assert done.returncode == 0 and value(run_key) is None
    done = run(script, "-AppRoot", str(app), "-RunKey", key, "-Remove")
    assert done.returncode == 0 and "nothing to remove" in done.stdout


def test_refuses_to_run_outside_gos_app(tmp_path: Path, run_key: str) -> None:
    app, _, _ = make_app(tmp_path)
    # The repo's own copy: gos-dev can change it, so it must never set up Clayton's login.
    done = run(SCRIPT, "-AppRoot", str(app), "-RunKey", f"HKCU:\\{run_key}")
    assert done.returncode != 0
    assert "Run this from GOS_APP" in done.stderr
    assert value(run_key) is None


def test_refuses_without_the_supervisor_launcher(tmp_path: Path, run_key: str) -> None:
    app, exe, script = make_app(tmp_path)
    exe.unlink()
    done = run(script, "-AppRoot", str(app), "-RunKey", f"HKCU:\\{run_key}")
    assert done.returncode != 0 and "is missing" in done.stderr
    assert value(run_key) is None


def test_refuses_as_gos_dev(tmp_path: Path, run_key: str) -> None:
    app, _, script = make_app(tmp_path)
    me = os.environ["USERNAME"]
    done = run(
        script, "-AppRoot", str(app), "-RunKey", f"HKCU:\\{run_key}", "-DevUser", me
    )
    assert done.returncode != 0 and "not as" in done.stderr
    assert value(run_key) is None


def test_refuses_to_run_elevated(tmp_path: Path, run_key: str) -> None:
    """GOS started from an elevated window would start Prism, Lab and Export elevated."""
    from gregtec import supervisor

    if not supervisor.is_elevated():
        pytest.skip("needs an elevated runner, as GitHub's Windows runners are")
    app, _, script = make_app(tmp_path)
    key = f"HKCU:\\{run_key}"
    for extra in ((), ("-StartNow",), ("-Remove",)):
        done = run(
            script, "-AppRoot", str(app), "-RunKey", key, *extra, allow_elevated=False
        )
        assert done.returncode != 0 and "not as administrator" in done.stderr, extra
        assert value(run_key) is None
