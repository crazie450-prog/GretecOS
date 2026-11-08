"""WO-2.6: the dashboard's Chrome app window on monitor 2 (D52, D163), opened by the supervisor
once the core is up, and ``gos dashboard``. The Windows path runs here with fakes."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from gregtec import appwindow, cli, config, desktop, supervisor
from gregtec.appwindow import Monitor
from gregtec.config import Config

PRIMARY = Monitor(0, 0, 2560, 1400, True, (2560, 1440))
SECOND = Monitor(2560, 0, 2560, 1400, False, (2560, 1440))
SMALL = Monitor(-1920, 0, 1920, 1040, False, (1920, 1080))


def test_monitor_2_is_the_other_2560x1440_one() -> None:
    assert appwindow.pick([PRIMARY, SMALL, SECOND]) == SECOND
    assert appwindow.pick([PRIMARY, SMALL]) == SMALL
    assert appwindow.pick([PRIMARY]) == PRIMARY
    assert appwindow.pick([]) is None


def test_the_command(tmp_path: Path) -> None:
    cfg = Config(gos_home=tmp_path, core_port=8765, dashboard_host="gos.localhost")
    argv = appwindow.command(cfg, Path("C:/chrome.exe"), SECOND)
    assert argv[0] == str(Path("C:/chrome.exe"))
    assert "--app=http://gos.localhost:8765/" in argv
    assert f"--user-data-dir={tmp_path / 'browser' / 'dashboard'}" in argv
    assert "--window-position=2560,0" in argv and "--window-size=2560,1400" in argv
    assert "--disable-background-timer-throttling" in argv
    assert "--disable-backgrounding-occluded-windows" in argv
    assert not any("position" in a for a in appwindow.command(cfg, Path("c"), None))


def test_it_opens_on_windows(tmp_path: Path) -> None:
    started: list[list[str]] = []
    cfg = Config(gos_home=tmp_path)
    argv = appwindow.open_window(
        cfg,
        platform="win32",
        find=lambda: Path("C:/Chrome/chrome.exe"),
        screens=lambda: [PRIMARY, SECOND],
        start=started.append,
    )
    assert started == [argv] and argv is not None
    assert (
        tmp_path / "browser" / "dashboard"
    ).is_dir()  # its own profile, gos-dev can't read


def test_no_chrome_opens_nothing(tmp_path: Path) -> None:
    started: list[list[str]] = []
    result = appwindow.open_window(
        Config(gos_home=tmp_path),
        platform="win32",
        find=lambda: None,
        start=started.append,
    )
    assert result is None and started == []


def test_a_launch_that_fails_is_logged_not_raised(tmp_path: Path) -> None:
    def broken(argv: list[str]) -> None:
        raise OSError("access denied")

    argv = appwindow.open_window(
        Config(gos_home=tmp_path),
        platform="win32",
        find=lambda: Path("chrome.exe"),
        screens=list,
        start=broken,
    )
    assert argv is not None


def test_an_open_window_is_found_by_its_profile(tmp_path: Path) -> None:
    """Chrome's processes carry the profile on their command lines, quoted or not."""
    cfg = Config(gos_home=tmp_path / "Gregtec OS")
    profile = cfg.gos_home / "browser" / "dashboard"
    ours = f'"C:\\Chrome\\chrome.exe" "--user-data-dir={profile}" --app=http://gos.localhost:8765/'
    renderer = f'chrome.exe --type=renderer --user-data-dir="{profile}" --lang=en'
    own_chrome = (
        'chrome.exe --profile-directory=Default --user-data-dir="C:\\Users\\c\\Chrome"'
    )
    assert appwindow.already_open(cfg, [own_chrome, ours])
    assert appwindow.already_open(cfg, [renderer.upper()])  # Windows paths ignore case
    assert not appwindow.already_open(cfg, [own_chrome, ""])
    assert not appwindow.already_open(cfg, [])


def test_the_startup_window_isnt_opened_twice(tmp_path: Path) -> None:
    """A deploy or gos serve restarts the supervisor: Chrome would add a second window."""
    started: list[list[str]] = []

    def opener(is_open: bool | Exception) -> list[str] | None:
        def check(cfg: Config) -> bool:
            if isinstance(is_open, Exception):
                raise is_open
            return is_open

        return appwindow.open_window(
            Config(gos_home=tmp_path),
            platform="win32",
            find=lambda: Path("chrome.exe"),
            screens=list,
            start=started.append,
            is_open=check,
        )

    assert opener(True) is None and started == []
    assert opener(False) is not None and len(started) == 1
    # Can't tell (the process list failed): a second window beats none.
    assert opener(OSError("PowerShell exited 1")) is not None and len(started) == 2


def test_elsewhere_nothing_is_started(tmp_path: Path) -> None:
    """The Linux fake: the command is logged, never run."""
    started: list[list[str]] = []
    argv = appwindow.open_window(
        Config(gos_home=tmp_path), platform="linux", start=started.append
    )
    assert started == [] and argv is not None and argv[1].startswith("--app=")
    assert not (tmp_path / "browser").exists()


def test_chrome_is_found_where_its_installers_put_it(tmp_path: Path) -> None:
    exe = tmp_path / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    assert appwindow.chrome_path({"LOCALAPPDATA": str(tmp_path / "Local")}) == exe
    assert appwindow.chrome_path({"ProgramFiles": str(tmp_path / "none")}) is None


def test_the_supervisor_opens_it_at_startup_unless_turned_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "running_as_dev", lambda: False)
    assert appwindow.at_startup(Config())
    assert not appwindow.at_startup(Config(dashboard_window=False))
    monkeypatch.setattr(config, "running_as_dev", lambda: True)
    assert not appwindow.at_startup(Config())  # never on Clayton's desktop as gos-dev


def test_dashboard_window_is_a_startup_key(tmp_path: Path) -> None:
    toml = tmp_path / "gos.toml"
    toml.write_text("[startup]\ndashboard_window = false\n", "utf-8")
    assert config.load(toml).dashboard_window is False
    toml.write_text('[startup]\ndashboard_window = "yes"\n', "utf-8")
    cfg = config.load(toml)
    assert cfg.dashboard_window is True
    assert any("dashboard_window" in e for e in cfg.service_errors)


def test_the_hook_runs_once_when_the_core_first_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A core restart doesn't open a second window."""
    ran: list[str] = []
    cfg = Config(gos_home=tmp_path, core_port=8799)
    spec = supervisor.ProcessSpec(
        "core",
        "Core",
        (sys.executable, "-c", "import time; time.sleep(30)"),
        health_port=8799,
        health_service="gregtec-core",
    )
    sup = supervisor.Supervisor(cfg, [spec], on_ready=lambda: ran.append("open"))
    monkeypatch.setattr(supervisor, "_probe", lambda spec: {"pid": 1})
    sup.start("core")
    try:
        for _ in range(3):
            sup.procs["core"].next_probe = 0.0
            sup.tick()
        deadline = time.monotonic() + 5
        while not ran and time.monotonic() < deadline:
            time.sleep(0.05)
        sup.restart("core")
        sup.procs["core"].next_probe = 0.0
        sup.tick()
        time.sleep(0.2)
    finally:
        sup.stop_all()
    assert ran == ["open"]


def test_gos_dashboard_prints_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{tmp_path.as_posix()}"\n[core]\nport = 8775\n'
        'supervisor_port = 8776\ndashboard_host = "gos-dev.localhost"\n',
        "utf-8",
    )
    assert cli.main(["dashboard", "--print", "--config", str(toml)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "http://gos-dev.localhost:8775/"
    assert "--app=http://gos-dev.localhost:8775/" in out[1]


def test_game_focus_is_gtnhs_java_in_front() -> None:
    assert desktop.game_focused("javaw.exe") is True
    assert desktop.game_focused("java.exe") is True
    assert desktop.game_focused("chrome.exe") is False
    assert desktop.game_focused(None) is None
    if sys.platform != "win32":
        assert desktop.foreground_program() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 calls")
def test_the_foreground_program_on_windows() -> None:
    """On a runner with no desktop it may be None; when known it is a lower-case file name."""
    program = desktop.foreground_program()
    assert program is None or (program == program.lower() and program.endswith(".exe"))
    monitors = appwindow.monitors()
    assert all(m.width > 0 and m.height > 0 for m in monitors)


def test_the_launcher_starts_detached(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_popen(argv: list[str], **kw: object) -> None:
        seen.update(kw, argv=argv)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    appwindow._start(["chrome.exe", "--app=x"])
    assert seen["argv"] == ["chrome.exe", "--app=x"]
    assert (
        seen["creationflags"]
        == appwindow.DETACHED_PROCESS | appwindow.CREATE_NEW_PROCESS_GROUP
    )
