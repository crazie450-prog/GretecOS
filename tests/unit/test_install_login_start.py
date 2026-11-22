"""WO-2.1, D116, D380: scripts/install_login_start.ps1 against a throwaway HKCU key (Windows only).

The script runs from a copy in a temporary GOS_APP, as it would from GOS_APP\\scripts, and writes
to ``HKCU\\Software\\GregtecOS-test-<random>``, never the real Run key. The value it writes is
``conhost.exe --headless <venv python.exe> -P -m gregtec.supervisor --login``: Smart App Control
blocks gos-supervisor.exe and pythonw.exe (D380). One test runs that command, and -StartNow,
against a stub ``gregtec.supervisor`` that only records how it was started.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install_login_start.ps1"
CONHOST = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "conhost.exe"


@pytest.fixture
def run_key() -> Iterator[str]:
    import winreg

    name = f"Software\\GregtecOS-test-{secrets.token_hex(4)}"
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, name).Close()
    yield name
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, name)


def run(
    script: Path,
    *args: str,
    allow_elevated: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    # CI runs pytest from PowerShell 7; its PSModulePath breaks Windows PowerShell 5.1's modules.
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    env.update(extra_env or {})
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


def make_app(tmp_path: Path, *, venv: bool = False) -> tuple[Path, Path, Path]:
    app = tmp_path / "Gregtec OS-app"  # a space: the paths must be quoted
    exe = app / "current" / ".venv" / "Scripts" / "python.exe"
    if venv:  # a real venv python.exe (the launcher), as a release has
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(exe.parents[1])],
            check=True,
            timeout=120,
        )
    else:
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
    login = f'"{CONHOST}" --headless "{exe}" -P -m gregtec.supervisor --login'
    assert value(run_key) == login
    assert "gos-supervisor.exe" not in login and "pythonw" not in login
    # Idempotent, and --config is passed on quoted.
    cfg = tmp_path / "my config" / "gos.toml"
    done = run(script, "-AppRoot", str(app), "-RunKey", key, "-Config", str(cfg))
    assert done.returncode == 0, done.stdout + done.stderr
    assert value(run_key) == f'{login} --config "{cfg}"'
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


def test_refuses_without_the_venv_python(tmp_path: Path, run_key: str) -> None:
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


STUB = textwrap.dedent(
    """
    import ctypes, json, os, sys
    k32 = ctypes.windll.kernel32
    hwnd = k32.GetConsoleWindow()
    data = {
        "argv": sys.argv[1:],
        "safe_path": sys.flags.safe_path,
        "executable": sys.executable,
        "window_visible": bool(hwnd) and bool(ctypes.windll.user32.IsWindowVisible(hwnd)),
    }
    with open(os.environ["GOS_TEST_OUT"], "w", encoding="utf-8") as f:
        json.dump(data, f)
    """
)


def wait_for(path: Path) -> dict[str, object]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            time.sleep(0.2)
            return dict(json.loads(path.read_text(encoding="utf-8")))
        time.sleep(0.2)
    raise AssertionError(f"the stub supervisor never wrote {path}")


def test_the_login_command_runs_python_m_supervisor_headless(
    tmp_path: Path, run_key: str
) -> None:
    """The Run key's command, as Windows runs it at login, and -StartNow's (D380)."""
    assert CONHOST.is_file()
    app, exe, script = make_app(tmp_path, venv=True)
    stub = tmp_path / "stub"
    (stub / "gregtec").mkdir(parents=True)
    (stub / "gregtec" / "__init__.py").write_text("")
    (stub / "gregtec" / "supervisor.py").write_text(STUB)
    cfg = tmp_path / "my config" / "gos.toml"
    key = f"HKCU:\\{run_key}"
    done = run(script, "-AppRoot", str(app), "-RunKey", key, "-Config", str(cfg))
    assert done.returncode == 0, done.stdout + done.stderr
    command = value(run_key)
    assert command is not None
    want = ["--login", "--config", str(cfg)]

    out = tmp_path / "run-key.json"
    env = {**os.environ, "PYTHONPATH": str(stub), "GOS_TEST_OUT": str(out)}
    # As the Run key does: the value is a command line, handed to CreateProcess as it is.
    subprocess.run(command, env=env, check=False, timeout=120)
    got = wait_for(out)
    assert got["argv"] == want
    assert got["safe_path"] is True  # -P: the start folder isn't on the import path
    assert got["window_visible"] is False  # conhost --headless: no console window

    out = tmp_path / "start-now.json"
    done = run(
        script,
        *("-AppRoot", str(app), "-RunKey", key, "-Config", str(cfg), "-StartNow"),
        extra_env={"PYTHONPATH": str(stub), "GOS_TEST_OUT": str(out)},
    )
    assert done.returncode == 0, done.stdout + done.stderr
    got = wait_for(out)
    assert got["argv"] == want and got["window_visible"] is False
    # The release's venv python.exe (its launcher starts the base python.exe for it).
    assert os.path.normcase(str(got["executable"])) == os.path.normcase(str(exe))
