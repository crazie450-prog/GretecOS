r"""The dashboard's Chrome app window on monitor 2 (D52, plan 7.14 rule 7; WO-2.6, D163).

The supervisor opens it once, when the core first answers ``/health`` after the supervisor
started (a login start or ``gos serve``), unless ``[startup] dashboard_window = false`` or it runs
as gos-dev (Claude Code's account must not open windows on Clayton's desktop). ``gos dashboard``
opens it on demand; ``--print`` only shows the command.

    chrome.exe --app=http://gos.localhost:8765/ --user-data-dir=<GOS_HOME>\browser\dashboard
               --window-position=X,Y --window-size=W,H  (monitor 2's work area) ...

- **Its own Chrome profile**, in ``GOS_HOME\browser\dashboard``: a separate Chrome process, so
  the window flags apply even while Clayton's own Chrome runs, and the dashboard's pairing cookie
  lives only there. That folder inherits GOS_HOME's ACL, which gives gos-dev nothing (D115 (4)),
  unlike ``data\``, which gos-dev reads (D82).
- **Monitor 2**: the first monitor that isn't the primary and is 2560x1440 (D50), else any other
  monitor, else the primary; the window fills that monitor's work area (the taskbar stays
  visible). F11 makes it full screen.
- **No throttling**: Chrome's background timer, renderer and occluded-window throttling are off,
  so the glance view keeps updating while GTNH has focus on monitor 1.
- **Windows only.** Elsewhere nothing is started: the command is logged (the Linux fake for
  development and CI); tests inject the platform, the monitors and the starter.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from gregtec import config, prism
from gregtec.config import Config

log = logging.getLogger("gregtec.appwindow")

PROFILE = Path("browser", "dashboard")  # in GOS_HOME
WANTED = (2560, 1440)  # monitor 2 (D50)
FLAGS = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
)
DETACHED_PROCESS = 0x0000_0008
CREATE_NEW_PROCESS_GROUP = 0x0000_0200
COMMANDS = ("dashboard",)


@dataclass(frozen=True)
class Monitor:
    """A monitor's work area in virtual-desktop pixels."""

    x: int
    y: int
    width: int
    height: int
    primary: bool
    full: tuple[int, int]  # the whole monitor's size, to recognise monitor 2


def url(cfg: Config) -> str:
    """The dashboard's address: its own name, the one pairing works on (D117)."""
    return f"http://{cfg.dashboard_host}:{cfg.core_port}/"


def chrome_path(env: dict[str, str] | None = None) -> Path | None:
    """Chrome's chrome.exe where its installers put it, or None."""
    env = dict(os.environ) if env is None else env
    roots = [
        env.get("ProgramFiles"),
        env.get("ProgramFiles(x86)"),
        env.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if root:
            exe = Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if exe.is_file():
                return exe
    return None


def monitors() -> list[Monitor]:
    """The monitors Windows reports (EnumDisplayMonitors); [] elsewhere or on failure."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found: list[Monitor] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def one(handle: int, _dc: int, _rect: object, _data: int) -> bool:
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            work, whole = info.rcWork, info.rcMonitor
            found.append(
                Monitor(
                    work.left,
                    work.top,
                    work.right - work.left,
                    work.bottom - work.top,
                    bool(info.dwFlags & 1),  # MONITORINFOF_PRIMARY
                    (whole.right - whole.left, whole.bottom - whole.top),
                )
            )
        return True

    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    if not user32.EnumDisplayMonitors(None, None, callback_type(one), 0):
        return []
    return found


def pick(found: Sequence[Monitor]) -> Monitor | None:
    """Monitor 2: a non-primary 2560x1440 one, else any non-primary one, else the primary."""
    others = [m for m in found if not m.primary]
    for m in others:
        if m.full == WANTED:
            return m
    if others:
        return others[0]
    return next((m for m in found if m.primary), None)


def command(cfg: Config, chrome: Path, monitor: Monitor | None) -> list[str]:
    argv = [
        str(chrome),
        f"--app={url(cfg)}",
        f"--user-data-dir={cfg.gos_home / PROFILE}",
        *FLAGS,
    ]
    if monitor is not None:
        argv += [
            f"--window-position={monitor.x},{monitor.y}",
            f"--window-size={monitor.width},{monitor.height}",
        ]
    return argv


def already_open(cfg: Config, lines: Sequence[str] | None = None) -> bool:
    """Whether a Chrome with the dashboard's own profile runs, so its window is open (Chrome runs
    one browser per profile and quits with its last window). Raises OSError if the processes
    can't be listed (Win32_Process, as :func:`gregtec.prism.command_lines`)."""
    found = prism.command_lines(("chrome.exe",)) if lines is None else lines
    flag = f"--user-data-dir={cfg.gos_home / PROFILE}".replace("\\", "/").lower()
    return any(
        flag in line.replace('"', "").replace("\\", "/").lower() for line in found
    )


def open_window(
    cfg: Config,
    *,
    platform: str = sys.platform,
    find: Callable[[], Path | None] = chrome_path,
    screens: Callable[[], list[Monitor]] = monitors,
    start: Callable[[list[str]], object] | None = None,
    is_open: Callable[[Config], bool] | None = None,
) -> list[str] | None:
    """Open the app window. Returns the command it ran (or would run elsewhere), or None when
    Chrome isn't installed or ``is_open`` says the window already is (the supervisor passes
    :func:`already_open`: a deploy or ``gos serve`` restarts it, and Chrome would add a second
    window). Never raises for a launch that fails: it is logged."""
    chrome = find() if platform == "win32" else Path("chrome")
    if chrome is None:
        log.warning(
            "Chrome not found: the dashboard window wasn't opened",
            extra={"url": url(cfg)},
        )
        return None
    argv = command(cfg, chrome, pick(screens()))
    if platform != "win32":
        log.info("the dashboard window opens on Windows only", extra={"command": argv})
        return argv
    if is_open is not None:
        try:
            if is_open(cfg):
                log.info(
                    "the dashboard window is already open", extra={"url": url(cfg)}
                )
                return None
        except OSError as exc:  # can't tell: better a second window than none
            log.warning(
                "couldn't check for an open dashboard window", extra={"error": str(exc)}
            )
    (cfg.gos_home / PROFILE).mkdir(parents=True, exist_ok=True)
    try:
        (start or _start)(argv)
    except OSError as exc:
        log.warning("the dashboard window didn't open", extra={"error": str(exc)})
        return argv
    log.info("dashboard window opened", extra={"url": url(cfg)})
    return argv


def _start(argv: list[str]) -> None:
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )


def at_startup(cfg: Config) -> bool:
    """Whether the supervisor opens the window when the core comes up."""
    return cfg.dashboard_window and not config.running_as_dev()


# --- gos dashboard -----------------------------------------------------------------------------


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser(
        "dashboard",
        help="open the dashboard's Chrome app window on monitor 2 (D52)",
        description=(
            "Opens http://<[core] dashboard_host>:<[core] port>/ as a Chrome app window in its "
            "own profile (GOS_HOME\\browser\\dashboard) on monitor 2. The supervisor does this "
            "at startup unless [startup] dashboard_window = false. Windows only."
        ),
    )
    parser.add_argument("--print", action="store_true", help="show the command only")
    parser.add_argument("--config", type=Path, default=None, help="gos.toml to read")


def run(args: argparse.Namespace) -> int:
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=sys.stderr)
        return 2
    if args.print or sys.platform != "win32":
        chrome = chrome_path() or Path("chrome.exe")
        print(url(cfg))
        print(subprocess.list2cmdline(command(cfg, chrome, pick(monitors()))))
        return 0
    if open_window(cfg) is None:
        print("Chrome isn't installed where its installers put it.", file=sys.stderr)
        return 1
    return 0
