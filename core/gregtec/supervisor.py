r"""``gos-supervisor`` (plan 4.2, D77, QA:A5, D115): starts, watches and restarts GOS's processes.

It starts at Windows login from the HKCU Run key that ``scripts/install_login_start.ps1`` writes
(D116), as ``GOS_APP\current\.venv\Scripts\gos-supervisor.exe --login``, a windowless launcher.
``gos serve`` runs it in the foreground instead. Today it manages one process, the core; the
Discord bot (WO-9.1) joins later, and so do, started with Play by its pre-launch hook (WO-1.4),
voice (WO-5.7, D179; until then ``gos voice run``) and the game watchers (WO-6.1).

**Restarts.** A process that exits, or whose ``/health`` stops answering for 20 s, is restarted
after a back-off of 1, 2, 4, 8, 16, 30, then 60 s between tries. A run that lasted 60 s resets the
back-off, so a crash after a good run is restarted after 1 s. Every exit and restart shows in the
status table, which the core turns into the dashboard's health strip.

**Stopping.** A managed process is asked to stop through its stdin (a ``stop`` line, then EOF),
and killed after 5 s. Its stdin is a pipe from the supervisor, so it also stops if the supervisor
dies. ``gos stop`` stops the managed processes and leaves the supervisor running, so ``gos serve``
(or later the phone, §7.16) can start them again. ``gos stop --all`` stops the supervisor too.

**Control port.** ``127.0.0.1:<[core] supervisor_port>`` (8766) speaks JSON:

| request          | token        | does                                                   |
|------------------|--------------|--------------------------------------------------------|
| GET  /health     | none         | liveness: ``{"service": "gos-supervisor", "pid": ...}``|
| GET  /status     | admin/status | the process table                                      |
| POST /stop       | admin        | stop the managed processes (they stay stopped)         |
| POST /start      | admin        | start the stopped ones                                 |
| POST /restart    | admin        | ``{"name": "core"}``: stop and start one               |
| POST /shutdown   | admin        | stop everything, then the supervisor                   |
| POST /launch     | admin/launch | ``{"instance": "lab"\|"export"}``: Prism's CLI launch  |

The admin token is in ``GOS_HOME\run\supervisor.json`` (Clayton only), the status token goes to
the core in its environment, and the launch token is in ``GOS_HOME\data\launch.json``, which
gos-dev can read (D82), so Claude Code can ask for Lab or Export (``gos launch``). Play is never
launched: ``/launch`` takes only ``lab`` or ``export``, maps them through this supervisor's own
gos.toml, and refuses an instance that is Play's folder. It also refuses while a GTNH client
(Play, Export or Lab) runs [A§5.5], and launches at most once per 30 s. Every request needs a
loopback ``Host``, and any ``Origin`` or ``Sec-Fetch-*`` header (a browser) is refused.

**The dashboard window.** The first time the core answers ``/health`` after the supervisor
starts, it opens the dashboard's Chrome app window on monitor 2 (D52, ``gregtec.appwindow``),
unless ``[startup] dashboard_window = false`` or it runs as gos-dev. A core restart doesn't open
another one.

**Refusing to start.** As gos-dev with Clayton's ports or dashboard name, with a bad service key
in gos.toml (``config.service_problems``, exit 2), or elevated (exit 4): everything the
supervisor starts would inherit an elevated token. Each refusal is in ``logs\supervisor.log``.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import re
import secrets
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Any

from gregtec import applog, appwindow, config, control
from gregtec.config import LOOPBACK, Config

log = logging.getLogger("gregtec.supervisor")

SERVICE = control.SUPERVISOR_SERVICE
TICK_S = 0.2
PROBE_EVERY_S = 2.0
PROBE_TIMEOUT_S = 1.5
START_GRACE_S = 30.0  # time to the first good /health before a start counts as failed
HANG_AFTER_S = 20.0  # /health failing this long while running: kill and restart
STABLE_AFTER_S = 60.0  # a run this long resets the back-off
BACKOFF_S = (1, 2, 4, 8, 16, 30, 60)
STOP_TIMEOUT_S = 5.0
LAUNCH_EVERY_S = 30.0  # at most one launch, of any instance, this often
MAX_BODY = 4096
STDERR_MAX = 5 * 1024 * 1024
STDERR_TAIL = 64 * 1024
# Exit codes a managed process uses to say why it stopped (gregtec.api.server).
EXIT_DETAIL = {3: "its port is in use by another program"}
CREATE_NO_WINDOW = 0x0800_0000
CREATE_NEW_PROCESS_GROUP = 0x0000_0200
DETACHED_PROCESS = 0x0000_0008


def python_exe() -> str:
    """The interpreter for children: python.exe, not pythonw.exe, so they have std handles."""
    exe = Path(sys.executable)
    if (
        exe.name.lower() == "pythonw.exe"
        and (console := exe.with_name("python.exe")).is_file()
    ):
        return str(console)
    return str(exe)


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    label: str
    argv: tuple[str, ...]
    health_port: int | None = None  # GET /health on 127.0.0.1:<port>
    health_service: str | None = None  # the "service" /health must report
    env: tuple[tuple[str, str], ...] = ()


@dataclass
class Managed:
    spec: ProcessSpec
    proc: subprocess.Popen[bytes] | None = None
    # The pid the process itself reports on /health. On Windows a venv's python.exe is a
    # launcher that runs the interpreter as its child, so proc.pid (kept as ``launcher_pid``)
    # isn't the pid in the process's run file or /health. None until /health answers; for a
    # process without /health, proc.pid.
    pid: int | None = None
    state: str = "stopped"  # starting | running | unresponsive | backoff | stopped
    detail: str = "not started"
    started_at: float | None = None  # monotonic
    started_wall: float | None = None
    last_ok: float | None = None
    next_probe: float = 0.0
    restarts: int = 0
    fails: int = 0  # back-to-back runs shorter than STABLE_AFTER_S
    restart_at: float | None = None
    last_exit: dict[str, Any] | None = None
    last_restart_ts: float | None = None
    wanted: bool = False  # False once Clayton stopped it
    stopping: bool = False  # stop() is waiting for it; the monitor leaves it alone
    stderr: IO[bytes] | None = field(default=None, repr=False)


def core_spec(cfg: Config, config_path: Path | None) -> ProcessSpec:
    argv = [python_exe(), "-m", "gregtec.api", "--supervised"]
    if config_path is not None:
        argv += ["--config", str(config_path)]
    return ProcessSpec(
        name="core",
        label="Core",
        argv=tuple(argv),
        health_port=cfg.core_port,
        health_service="gregtec-core",
    )


def prism_command(cfg: Config, instance: str) -> list[str]:
    """Prism Launcher's CLI launch of ``instance`` (a folder name in ``instances``) [A§5.5]."""
    exe = cfg.prism_root / (
        "prismlauncher.exe" if sys.platform == "win32" else "prismlauncher"
    )
    return [str(exe), "--launch", instance]


class LaunchError(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class Supervisor:
    def __init__(
        self,
        cfg: Config,
        specs: Sequence[ProcessSpec],
        *,
        status_token: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_ready: Callable[[], object] | None = None,
    ) -> None:
        self.cfg = cfg
        # Called once, in its own thread, when the core first answers /health (D163).
        self.on_ready = on_ready
        self._ready = False
        self.admin_token = secrets.token_urlsafe(32)
        self.status_token = status_token or secrets.token_urlsafe(32)
        self.launch_token = secrets.token_urlsafe(32)
        self.procs = {s.name: Managed(s) for s in specs}
        self.clock = clock
        self.started = time.time()
        self._lock = threading.RLock()
        # One start, stop or restart at a time, so a start can't land in the middle of a stop.
        self._ops = threading.RLock()
        self._stop = threading.Event()
        # When the last launch was asked for (self.clock), if any.
        self._launched: float | None = None
        self._server: _ControlServer | None = None

    # --- process control (callers hold no lock; these take it) --------------------------------

    def start(self, name: str) -> None:
        with self._ops, self._lock:
            managed = self.procs[name]
            managed.wanted = True
            if managed.proc is None:
                self._spawn(managed)

    def stop(self, name: str, *, detail: str = "stopped by gos stop") -> None:
        with self._ops:
            with self._lock:
                managed = self.procs[name]
                managed.wanted = False
                managed.stopping = True
                managed.restart_at = None
                proc = managed.proc
            try:
                if proc is not None:
                    _stop_process(proc)
            finally:
                with self._lock:
                    self._reap(managed, expected=True)
                    managed.stopping = False
                    managed.state, managed.detail = "stopped", detail

    def restart(self, name: str) -> None:
        with self._ops:
            self.stop(name, detail="restarting")
            with self._lock:
                self.procs[name].restarts += 1
                self.procs[name].last_restart_ts = time.time()
            self.start(name)

    def start_all(self) -> None:
        for name in self.procs:
            self.start(name)

    def stop_all(self, *, detail: str = "stopped by gos stop") -> None:
        for name in self.procs:
            self.stop(name, detail=detail)

    def _spawn(self, managed: Managed) -> None:
        cfg, spec = self.cfg, managed.spec
        env = dict(os.environ)
        env.update(dict(spec.env))
        env["GOS_SUPERVISOR_TOKEN"] = self.status_token
        flags = (
            CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0
        )
        cfg.logs_dir.mkdir(parents=True, exist_ok=True)
        managed.stderr = _open_stderr_log(cfg.logs_dir / f"{spec.name}.stderr.log")
        try:
            managed.proc = subprocess.Popen(
                list(spec.argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=managed.stderr,
                cwd=cfg.gos_home,  # never inside GOS_APP\current, which deploy renames
                env=env,
                creationflags=flags,
                start_new_session=sys.platform != "win32",
            )
        except OSError as exc:
            managed.stderr.close()
            managed.stderr = None
            managed.fails += 1
            log.error("start failed", extra={"proc": spec.name, "error": str(exc)})
            self._schedule_restart(managed, f"couldn't start: {exc}")
            return
        now = self.clock()
        managed.started_at, managed.started_wall = now, time.time()
        managed.pid = None if spec.health_port is not None else managed.proc.pid
        managed.last_ok = None
        managed.next_probe = now + 0.5
        managed.restart_at = None
        if spec.health_port is None:
            managed.state, managed.detail = "running", "running"
        else:
            managed.state, managed.detail = "starting", "starting"
        log.info(
            "process started",
            extra={"proc": spec.name, "launcher_pid": managed.proc.pid},
        )

    def _reap(self, managed: Managed, *, expected: bool) -> int | None:
        """Record a finished process. Returns its exit code (None if there was none)."""
        proc = managed.proc
        if proc is None:
            return None
        code = proc.poll()
        if code is None:
            return None
        ran = self.clock() - (managed.started_at or self.clock())
        managed.proc = None
        managed.pid = None
        if managed.stderr is not None:
            managed.stderr.close()
            managed.stderr = None
        managed.last_exit = {
            "code": code,
            "ts": int(time.time()),
            "ran_s": round(ran, 1),
        }
        if not expected:
            managed.fails = managed.fails + 1 if ran < STABLE_AFTER_S else 1
            log.warning(
                "process exited",
                extra={"proc": managed.spec.name, "code": code, "ran_s": round(ran, 1)},
            )
        return code

    def _schedule_restart(self, managed: Managed, why: str) -> None:
        delay = BACKOFF_S[min(max(managed.fails, 1), len(BACKOFF_S)) - 1]
        managed.restart_at = self.clock() + delay
        managed.state = "backoff"
        managed.detail = f"{why}; restarting in {delay} s"

    # --- the monitor loop ---------------------------------------------------------------------

    def tick(self) -> None:
        """One pass: reap exits, start due restarts, probe health. Called every TICK_S."""
        to_probe: list[Managed] = []
        to_kill: list[tuple[Managed, str]] = []
        ready: list[Callable[[], object]] = []
        with self._lock:
            now = self.clock()
            for managed in self.procs.values():
                if managed.stopping:
                    continue
                if managed.proc is not None and managed.proc.poll() is not None:
                    code = self._reap(managed, expected=False)
                    if managed.wanted:
                        why = EXIT_DETAIL.get(code or 0, f"exited with code {code}")
                        self._schedule_restart(managed, why)
                    else:
                        managed.state, managed.detail = "stopped", "stopped"
                if (
                    managed.wanted
                    and managed.proc is None
                    and managed.restart_at is not None
                    and now >= managed.restart_at
                ):
                    managed.restarts += 1
                    managed.last_restart_ts = time.time()
                    self._spawn(managed)
                if (
                    managed.proc is not None
                    and managed.spec.health_port is not None
                    and now >= managed.next_probe
                ):
                    managed.next_probe = now + PROBE_EVERY_S
                    to_probe.append(managed)
        for managed in to_probe:
            health = _probe(managed.spec)
            with self._lock:
                now = self.clock()
                if managed.proc is None or managed.stopping:
                    continue
                if health is not None:
                    managed.last_ok = now
                    reported = health.get("pid")
                    if isinstance(reported, int) and not isinstance(reported, bool):
                        managed.pid = reported
                    if managed.state != "running":
                        managed.state, managed.detail = "running", "running"
                    if managed.spec.name == "core" and not self._ready:
                        self._ready = True
                        if self.on_ready is not None:
                            ready.append(self.on_ready)
                    if (
                        managed.started_at is not None
                        and now - managed.started_at >= STABLE_AFTER_S
                    ):
                        managed.fails = 0
                    continue
                if managed.state == "starting":
                    if now - (managed.started_at or now) >= START_GRACE_S:
                        to_kill.append(
                            (managed, "didn't answer /health after starting")
                        )
                    continue
                silent = now - (managed.last_ok or now)
                managed.state = "unresponsive"
                managed.detail = f"/health not answering for {int(silent)} s"
                if silent >= HANG_AFTER_S:
                    to_kill.append(
                        (managed, f"/health didn't answer for {int(silent)} s")
                    )
        for hook in ready:
            threading.Thread(target=_quietly, args=(hook,), daemon=True).start()
        for managed, why in to_kill:
            proc = managed.proc
            if proc is None or managed.stopping:
                continue
            log.warning(
                "killing unresponsive process", extra={"proc": managed.spec.name}
            )
            proc.kill()
            try:
                proc.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                continue
            with self._lock:
                self._reap(managed, expected=False)
                if managed.wanted:
                    self._schedule_restart(managed, why)

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = self.clock()
            rows = []
            for m in self.procs.values():
                restart_in = (
                    max(0.0, round(m.restart_at - now, 1))
                    if m.restart_at is not None
                    else None
                )
                rows.append(
                    {
                        "name": m.spec.name,
                        "label": m.spec.label,
                        "state": m.state,
                        "detail": m.detail,
                        "pid": m.pid if m.proc is not None else None,
                        "launcher_pid": m.proc.pid if m.proc is not None else None,
                        "started": int(m.started_wall)
                        if m.proc is not None and m.started_wall
                        else None,
                        "uptime_s": int(now - m.started_at)
                        if m.proc is not None and m.started_at is not None
                        else None,
                        "restarts": m.restarts,
                        "last_exit": m.last_exit,
                        "last_restart_ts": int(m.last_restart_ts)
                        if m.last_restart_ts
                        else None,
                        "restart_in_s": restart_in,
                    }
                )
        return {
            "service": SERVICE,
            "pid": os.getpid(),
            "uptime_s": int(time.time() - self.started),
            "processes": rows,
        }

    # --- Lab and Export launches (V24) --------------------------------------------------------

    def launch(self, role: object) -> dict[str, Any]:
        if role not in ("lab", "export"):
            raise LaunchError(400, 'instance must be "lab" or "export"')
        assert isinstance(role, str)
        cfg = self.cfg
        instance = cfg.instance(role)
        play = cfg.instance("play")
        if instance.name.lower() == play.name.lower() or _same_path(instance, play):
            raise LaunchError(409, f"refused: {role} is configured as Play's folder")
        if not instance.is_dir():
            raise LaunchError(404, f"no {role} instance at {instance}")
        with self._lock:
            # One launch per LAUNCH_EVERY_S of any instance: Prism takes seconds to start Java,
            # so a second launch right after the first wouldn't see it in running_client().
            now, last = self.clock(), self._launched
            if last is not None and now - last < LAUNCH_EVERY_S:
                raise LaunchError(
                    429, f"a launch was asked for {int(now - last)} s ago; wait"
                )
            self._launched = now
        try:
            busy = running_client(cfg)
        except OSError as exc:
            with self._lock:
                self._launched = last
            raise LaunchError(
                503, f"couldn't check whether a GTNH client is running: {exc}"
            ) from None
        if busy is not None:
            with self._lock:
                self._launched = last
            raise LaunchError(
                409,
                f"refused: {busy} is running, and two GTNH clients never run at once "
                "[A§5.5]; try again once it has quit",
            )
        argv = prism_command(cfg, instance.name)
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=cfg.prism_root if cfg.prism_root.is_dir() else None,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                if sys.platform == "win32"
                else 0,
                start_new_session=sys.platform != "win32",
            )
        except OSError as exc:
            with self._lock:
                self._launched = last
            raise LaunchError(
                503, f"couldn't run Prism Launcher ({argv[0]}): {exc}"
            ) from None
        threading.Thread(target=proc.wait, daemon=True).start()  # reap it
        log.info(
            "launched", extra={"role": role, "instance": instance.name, "pid": proc.pid}
        )
        return {"launched": instance.name, "role": role, "pid": proc.pid}

    # --- serving ------------------------------------------------------------------------------

    def bind(self) -> None:
        """Take the control port. Raises OSError if it's in use (another supervisor?)."""
        self._server = _ControlServer(
            (LOOPBACK, self.cfg.supervisor_port), _Handler, self
        )

    def serve(self) -> None:
        """Run the control server and the monitor loop until :meth:`request_shutdown`."""
        assert self._server is not None, "bind() first"
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        try:
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("monitor tick failed")
                self._stop.wait(TICK_S)
        finally:
            self._server.shutdown()
            self._server.server_close()

    def request_shutdown(self) -> None:
        self._stop.set()


def _quietly(hook: Callable[[], object]) -> None:
    try:
        hook()
    except Exception:  # a failed window must never stop the supervisor
        log.exception("startup hook failed")


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


# --- is a GTNH client running? -----------------------------------------------------------------

JAVA_NAMES = ("java.exe", "javaw.exe") if sys.platform == "win32" else ("java",)
# Win32_Process command lines of the named images, as a JSON list (null for ones it can't read).
_PS_COMMAND_LINES = (
    "$ErrorActionPreference = 'Stop'; "
    "try {{ [Console]::OutputEncoding = [Text.Encoding]::UTF8 }} catch {{ }}; "
    "ConvertTo-Json -Compress -InputObject @(Get-CimInstance Win32_Process -Filter '{filter}' "
    "| ForEach-Object {{ $_.CommandLine }})"
)


def command_lines(names: Sequence[str] = JAVA_NAMES) -> list[str]:
    """The command lines of this PC's running processes whose program is one of ``names``.

    Windows: Win32_Process through PowerShell (about a second; the supervisor runs as Clayton, so
    it sees his processes' command lines without reading any of their files). Linux: /proc.
    Raises OSError if it can't tell.
    """
    if sys.platform == "win32":
        # '' is a quote inside PowerShell's '...'.
        flt = " OR ".join(f"Name = ''{n}''" for n in names)
        try:
            done = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _PS_COMMAND_LINES.format(filter=flt),
                ],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,  # no console flash on Clayton's screen
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise OSError("PowerShell didn't list the processes within 30 s") from None
        if done.returncode != 0:
            raise OSError(
                f"PowerShell exited {done.returncode}: "
                f"{done.stderr.decode('utf-8', 'replace').strip()[:200]}"
            )
        try:
            found = json.loads(done.stdout.decode("utf-8-sig", "replace") or "[]")
        except ValueError as exc:
            raise OSError(f"PowerShell's process list isn't JSON ({exc})") from None
        if not isinstance(found, list):
            raise OSError("PowerShell's process list isn't a list")
        return [c for c in found if isinstance(c, str)]
    proc_dir = Path("/proc")
    if not proc_dir.is_dir():
        return []  # no Prism clients outside Windows and Linux
    wanted = {n.lower() for n in names}
    lines = []
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:  # gone, or not ours to read
            continue
        if argv and Path(os.fsdecode(argv[0])).name.lower() in wanted:
            lines.append(" ".join(os.fsdecode(a) for a in argv if a))
    return lines


def _folder_in(folder: Path, command_line: str) -> bool:
    """Whether ``command_line`` names ``folder`` (or a path inside it), ignoring case and slashes."""
    norm = command_line.replace("\\", "/").lower()
    for form in {str(folder), str(_resolved(folder))}:
        path = re.escape(form.replace("\\", "/").rstrip("/").lower())
        if re.search(path + r'(?=[/"\s;:]|$)', norm):
            return True
    return False


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def running_client(cfg: Config) -> str | None:
    """The instance (Play, Export or Lab) a running Java process was started from, else None.

    Prism starts each client's Java with paths inside its instance folder on the command line
    (its natives and libraries). Raises OSError if the processes can't be listed.
    """
    lines = command_lines()
    for role in ("play", "export", "lab"):
        folder = cfg.instance(role)
        if any(_folder_in(folder, line) for line in lines):
            return folder.name
    return None


# --- elevation ---------------------------------------------------------------------------------

# GitHub's Windows runners run every job elevated, so CI sets this; never set it on a real PC.
ALLOW_ELEVATED_ENV = "GOS_ALLOW_ELEVATED"
EXIT_ELEVATED = 4


def is_elevated() -> bool:
    """Whether this process runs with an elevated (administrator) token. Windows only."""
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def elevation_problem() -> str | None:
    """Why GOS must not start here, or None. Everything GOS starts (Prism, Lab and Export with
    gos-dev's mods) would inherit an elevated token, beyond plan 4.6's "runs as Clayton"."""
    if is_elevated() and os.environ.get(ALLOW_ELEVATED_ENV) != "1":
        return (
            "GOS doesn't run elevated (as administrator): everything it starts would be "
            "elevated too. Start it from a normal window, or log off and on."
        )
    return None


def _probe(spec: ProcessSpec) -> dict[str, Any] | None:
    """The process's /health answer, or None if it didn't give a good one."""
    assert spec.health_port is not None
    try:
        health = control.call(
            spec.health_port, "GET", "/health", timeout=PROBE_TIMEOUT_S
        )
    except control.ControlError:
        return None
    if spec.health_service is not None and health.get("service") != spec.health_service:
        return None
    return health


def _stop_process(proc: subprocess.Popen[bytes]) -> None:
    """Ask through stdin, then kill after STOP_TIMEOUT_S."""
    if proc.poll() is not None:
        return
    try:
        if proc.stdin is not None:
            proc.stdin.write(b"stop\n")
            proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=STOP_TIMEOUT_S)


def _open_stderr_log(path: Path) -> IO[bytes]:
    """Append to ``path``; past STDERR_MAX it is moved to ``<name>.1`` first.

    If the move fails (Windows: someone has the file open without share-delete, an editor or a
    tail), the file starts again with its last STDERR_TAIL bytes, which hold the last crash, so
    it never grows past about STDERR_MAX either way.
    """
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size > STDERR_MAX:
        try:
            os.replace(path, path.with_name(path.name + ".1"))
        except OSError:
            try:
                with open(path, "rb") as f:
                    f.seek(-STDERR_TAIL, os.SEEK_END)
                    tail = f.read()
                out = open(path, "wb")  # noqa: SIM115 - returned, closed on reap
            except OSError:
                return open(path, "ab")
            out.write(
                b"[gos-supervisor: couldn't rotate this file; kept its end]\n" + tail
            )
            return out  # closed when the process is reaped
    return open(path, "ab")  # closed when the process is reaped


# --- the control server -----------------------------------------------------------------------


class _ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    # As gregtec.api.server.bind: no SO_REUSEADDR on Windows, where it would let another
    # socket bind the same address.
    allow_reuse_address = sys.platform != "win32"

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        supervisor: Supervisor,
    ) -> None:
        self.supervisor = supervisor
        super().__init__(address, handler)

    def server_bind(self) -> None:
        # Not HTTPServer.server_bind: its getfqdn() can wait seconds on reverse DNS.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = LOOPBACK, self.server_address[1]


class _Handler(BaseHTTPRequestHandler):
    server: _ControlServer
    server_version = SERVICE
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:  # base class signature
        log.debug("control request", extra={"line": format % args})

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _role(self) -> str | None:
        """``admin``, ``status`` or ``launch`` from the bearer token, else None."""
        scheme, _, token = (self.headers.get("Authorization") or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        sup = self.server.supervisor
        for role, expected in (
            ("admin", sup.admin_token),
            ("status", sup.status_token),
            ("launch", sup.launch_token),
        ):
            if hmac.compare_digest(token.encode(), expected.encode()):
                return role
        return None

    def _refused(self) -> str | None:
        port = self.server.supervisor.cfg.supervisor_port
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"{LOOPBACK}:{port}", f"localhost:{port}"):
            return "host not allowed"
        if any(
            h.lower() == "origin" or h.lower().startswith("sec-fetch-")
            for h in self.headers
        ):
            return "browsers may not use the supervisor"
        return None

    def _body(self) -> dict[str, Any] | None:
        length = self.headers.get("Content-Length") or "0"
        if not length.isdigit() or int(length) > MAX_BODY:
            return None
        raw = self.rfile.read(int(length)) if int(length) else b"{}"
        try:
            body = json.loads(raw)
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def do_GET(self) -> None:  # http.server naming
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        if (why := self._refused()) is not None:
            self._reply(403, {"error": why})
            return
        sup = self.server.supervisor
        path = self.path.split("?")[0]
        if method == "GET" and path == "/health":
            self._reply(200, {"status": "ok", "service": SERVICE, "pid": os.getpid()})
            return
        role = self._role()
        if role is None:
            self._reply(401, {"error": "a supervisor token is needed"})
            return
        allowed = {
            ("GET", "/status"): ("admin", "status"),
            ("POST", "/stop"): ("admin",),
            ("POST", "/start"): ("admin",),
            ("POST", "/restart"): ("admin",),
            ("POST", "/shutdown"): ("admin",),
            ("POST", "/launch"): ("admin", "launch"),
        }.get((method, path))
        if allowed is None:
            self._reply(404, {"error": "no such request"})
            return
        if role not in allowed:
            self._reply(403, {"error": f"the {role} token can't do that"})
            return
        body = self._body() if method == "POST" else {}
        if body is None:
            self._reply(400, {"error": "send a small JSON object"})
            return
        if path == "/status":
            self._reply(200, sup.status())
        elif path == "/stop":
            log.info("stop requested")
            sup.stop_all()
            self._reply(200, sup.status())
        elif path == "/start":
            log.info("start requested")
            sup.start_all()
            self._reply(200, sup.status())
        elif path == "/restart":
            name = body.get("name")
            if not isinstance(name, str) or name not in sup.procs:
                self._reply(404, {"error": f"no managed process {name!r}"})
                return
            log.info("restart requested", extra={"proc": name})
            sup.restart(name)
            self._reply(200, sup.status())
        elif path == "/shutdown":
            log.info("shutdown requested")
            self._reply(200, {"stopping": True})
            sup.request_shutdown()
        elif path == "/launch":
            try:
                self._reply(200, sup.launch(body.get("instance")))
            except LaunchError as exc:
                log.warning("launch refused", extra={"why": exc.reason})
                self._reply(exc.status, {"error": exc.reason})


# --- entry point ------------------------------------------------------------------------------


def run(cfg: Config, config_path: Path | None, *, login: bool = False) -> int:
    """Run a supervisor in this process until it is shut down. Returns an exit code."""
    applog.setup("supervisor", cfg.logs_dir)
    if login and not cfg.core_at_login:
        log.info("login start is off ([startup] core_at_login = false)")
        return 0
    if (why := elevation_problem()) is not None:
        log.error("refusing to start", extra={"why": why})
        return EXIT_ELEVATED
    if problems := config.service_problems(cfg):
        for problem in problems:
            log.error("refusing to start: fix gos.toml", extra={"why": problem})
        return 2
    # Absolute: the core starts in GOS_HOME, and so does this process from here on.
    if config_path is not None:
        config_path = Path(os.path.abspath(config_path))
    cfg.gos_home.mkdir(parents=True, exist_ok=True)
    os.chdir(cfg.gos_home)  # a Run-key start begins in System32; never sit in GOS_APP
    run_file = cfg.run_dir / control.SUPERVISOR_FILE
    if control.running(run_file, SERVICE) is not None:
        log.info("a supervisor is already running")
        return 0
    window = appwindow.at_startup(cfg)
    sup = Supervisor(
        cfg,
        [core_spec(cfg, config_path)],
        on_ready=(
            (lambda: appwindow.open_window(cfg, is_open=appwindow.already_open))
            if window
            else None
        ),
    )
    try:
        sup.bind()
    except OSError as exc:
        log.error(
            "control port in use",
            extra={"port": cfg.supervisor_port, "error": str(exc)},
        )
        return 3
    launch_file = cfg.data_dir / control.LAUNCH_FILE
    pid = os.getpid()
    control.write_run_file(
        run_file,
        {
            "pid": pid,
            "port": cfg.supervisor_port,
            "token": sup.admin_token,
            "exe": sys.executable,  # gos deploy restarts it only if it ran from GOS_APP
            # ... with the same gos.toml (the Run key's --config, if it has one).
            "config": str(config_path) if config_path is not None else None,
            "started": int(time.time()),
        },
    )
    control.write_run_file(
        launch_file,
        {"pid": pid, "port": cfg.supervisor_port, "token": sup.launch_token},
        private=False,  # gos-dev reads it (D82); it can only launch Lab or Export
    )
    log.info("supervisor started", extra={"pid": pid, "port": cfg.supervisor_port})
    try:
        sup.start_all()
        sup.serve()
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        sup.stop_all(detail="supervisor stopped")
        control.remove_run_file(run_file, pid)
        control.remove_run_file(launch_file, pid)
        log.info("supervisor stopped")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """``gos-supervisor [--login] [--config PATH]``."""
    parser = argparse.ArgumentParser(prog="gos-supervisor")
    parser.add_argument(
        "--login", action="store_true", help="started by the login Run key"
    )
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    path = args.config or (
        config.default_path() if config.default_path().is_file() else None
    )
    try:
        cfg = config.load(path)
    except (OSError, ValueError) as exc:
        # No config, so no [paths] gos_home: log where GOS_HOME is by default. A login start
        # runs under pythonw, which has no stderr, so the log file is the only trace.
        applog.setup("supervisor", config.default_home() / "logs")
        log.error("can't read gos.toml", extra={"path": str(path), "error": str(exc)})
        return 2
    return run(cfg, path, login=args.login)


if __name__ == "__main__":
    raise SystemExit(main())
