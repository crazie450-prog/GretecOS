"""The service commands of ``gos`` (WO-2.1): serve, stop, status, launch, token, pair, devices.

They find the supervisor and the core through their run files (``gregtec.control``) and talk to
them over loopback. ``gos launch`` is the one gos-dev uses: it reads the launch token from
``<[launch] home>/data/launch.json``, which gos-dev can read (D82, D115).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import keyring.errors

from gregtec import config, control, supervisor
from gregtec.config import Config
from gregtec.llm import keys

WAIT_S = 15.0  # how long stop/serve wait for a process to go or come
# /stop waits for each process: up to STOP_TIMEOUT_S for it to stop, then as long for the kill.
STOP_CALL_S = 25.0


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    def with_config(
        parser: argparse.ArgumentParser, *, nested: bool = False
    ) -> argparse.ArgumentParser:
        parser.add_argument(
            "--config",
            type=Path,
            # A nested parser's default would overwrite a --config given before its name.
            default=argparse.SUPPRESS if nested else None,
            help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
            "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
        )
        return parser

    serve = with_config(
        commands.add_parser(
            "serve",
            help="start GOS: the supervisor, which starts and watches the core",
            description=(
                "With a supervisor already running (login start), starts the processes "
                "`gos stop` stopped. Otherwise runs the supervisor in this window until Ctrl+C, "
                "or in the background with --detach."
            ),
        )
    )
    mode = serve.add_mutually_exclusive_group()
    mode.add_argument(
        "--detach", action="store_true", help="start the supervisor in the background"
    )
    mode.add_argument(
        "--core-only",
        action="store_true",
        help="run just the core in this window, with no supervisor (development)",
    )

    stop = with_config(
        commands.add_parser(
            "stop",
            help="stop GOS's processes; the supervisor stays so `gos serve` can resume",
        )
    )
    stop.add_argument("--all", action="store_true", help="stop the supervisor too")

    status = with_config(
        commands.add_parser(
            "status", help="show the health strip: processes, restarts and checks"
        )
    )
    status.add_argument("--json", action="store_true", help="print the raw JSON")

    launch = with_config(
        commands.add_parser(
            "launch",
            help="ask the supervisor to launch Lab or Export through Prism (never Play)",
        )
    )
    launch.add_argument("instance", choices=("lab", "export"))
    launch.add_argument(
        "--home",
        type=Path,
        default=None,
        help="GOS_HOME of the supervisor to ask. Default: [launch] home, else this "
        "config's GOS_HOME.",
    )

    token = with_config(
        commands.add_parser("token", help="client bearer tokens (plan 4.6)")
    )
    token_cmds = token.add_subparsers(dest="token_command", required=True)
    new = token_cmds.add_parser(
        "new",
        help="issue a token for a client (replaces its old one)",
        description=(
            "Stores the token in this account's keyring as gregtec-os/token-<name>, where "
            "GOS's own processes read it. --show prints it once instead, for a client "
            "configured by hand. The core keeps only its hash."
        ),
    )
    with_config(new, nested=True)
    new.add_argument("name", help="client name, e.g. voice, discord, mod, oc")
    new.add_argument("--show", action="store_true", help="print the token instead")
    with_config(token_cmds.add_parser("list", help="list client tokens"), nested=True)
    revoke = with_config(
        token_cmds.add_parser("revoke", help="revoke a client's token"), nested=True
    )
    revoke.add_argument("name")

    with_config(
        commands.add_parser(
            "pair",
            help="show the codes of devices waiting to pair",
            description=(
                "A browser that opens the dashboard without a cookie asks to pair. Its code is "
                "shown here and on paired dashboards, never on the device that asked: type it "
                "there."
            ),
        )
    )

    devices = with_config(
        commands.add_parser("devices", help="list or revoke paired devices")
    )
    device_cmds = devices.add_subparsers(dest="devices_command")
    revoke_device = with_config(
        device_cmds.add_parser("revoke", help="revoke a paired device"), nested=True
    )
    revoke_device.add_argument("device_id")


COMMANDS = ("serve", "stop", "status", "launch", "token", "pair", "devices")


def run(args: argparse.Namespace) -> int:
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=sys.stderr)
        return 2
    if args.command == "serve":
        # The core and the supervisor refuse these too; saying so here is quicker and clearer.
        if problems := config.service_problems(cfg):
            for problem in problems:
                print(f"Not starting GOS: {problem}", file=sys.stderr)
            return 2
    else:
        for problem in cfg.service_errors:
            print(
                f"Warning: {problem} (GOS won't start until it's fixed)",
                file=sys.stderr,
            )
    try:
        return _HANDLERS[args.command](cfg, args)
    except control.ControlError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1


def _supervisor(cfg: Config) -> dict[str, Any] | None:
    return control.running(
        cfg.run_dir / control.SUPERVISOR_FILE, control.SUPERVISOR_SERVICE
    )


def _core(cfg: Config) -> dict[str, Any] | None:
    return control.running(cfg.run_dir / control.CORE_FILE, control.CORE_SERVICE)


def _need_core(cfg: Config) -> dict[str, Any]:
    core = _core(cfg)
    if core is None:
        raise control.ControlError("the core isn't running (start it with: gos serve)")
    return core


def _core_call(cfg: Config, method: str, path: str) -> Any:  # noqa: ANN401 - JSON
    core = _need_core(cfg)
    result = control.call(core["port"], method, path, token=core["token"])
    return result.get("result", result)


def wait_until(check: Callable[[], object], want: bool) -> bool:
    """Wait up to WAIT_S for ``check()`` to return something (``want``) or None (not ``want``)."""
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if (check() is not None) == want:
            return True
        time.sleep(0.25)
    return False


def supervisor_command(cfg: Config, config_path: Path | None) -> list[str]:
    """How to start a supervisor in the background: the windowless launcher if installed."""
    name = "gos-supervisor.exe" if sys.platform == "win32" else "gos-supervisor"
    exe = Path(sys.executable).with_name(name)
    argv = (
        [str(exe)]
        if exe.is_file()
        else [supervisor.python_exe(), "-m", "gregtec.supervisor"]
    )
    if config_path is not None:  # absolute: the supervisor starts in GOS_HOME
        argv += ["--config", str(config_path.absolute())]
    return argv


def spawn_detached(argv: list[str], cwd: Path) -> None:
    """Start ``argv`` with no console and no tie to this window."""
    cwd.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=cwd,
        creationflags=supervisor.DETACHED_PROCESS | supervisor.CREATE_NEW_PROCESS_GROUP
        if sys.platform == "win32"
        else 0,
        start_new_session=sys.platform != "win32",
    )


def _print_processes(status: dict[str, Any]) -> None:
    for proc in status.get("processes", []):
        line = f"  {proc['name']:<10} {proc['state']:<12} {proc.get('detail', '')}"
        if proc.get("pid"):
            line += f"  (pid {proc['pid']})"
        if proc.get("restarts"):
            line += f"  restarts: {proc['restarts']}"
        print(line)


def _serve(cfg: Config, args: argparse.Namespace) -> int:
    if args.core_only:
        if _core(cfg) is not None:
            print(
                f"A core is already running on port {cfg.core_port}.", file=sys.stderr
            )
            return 1
        # Imported here: FastAPI takes a moment to import.
        from gregtec.api import server

        return server.run(cfg, config_path=getattr(args, "config", None))
    sup = _supervisor(cfg)
    if sup is not None:
        status = control.call(sup["port"], "POST", "/start", token=sup["token"])
        print("The supervisor is running; started what was stopped:")
        _print_processes(status)
        return 0
    if (why := supervisor.elevation_problem()) is not None:
        print(f"Not starting GOS: {why}", file=sys.stderr)
        return supervisor.EXIT_ELEVATED
    if args.detach:
        spawn_detached(supervisor_command(cfg, args.config), cfg.gos_home)
        if not wait_until(lambda: _supervisor(cfg), True):
            print(
                f"The supervisor didn't answer within {WAIT_S:.0f} s; see "
                f"{cfg.logs_dir / 'supervisor.log'}",
                file=sys.stderr,
            )
            return 1
        print(f"GOS started in the background (logs in {cfg.logs_dir}).")
        return 0
    print("Running the supervisor in this window; Ctrl+C stops GOS.")
    return supervisor.run(cfg, args.config)


def _stop(cfg: Config, args: argparse.Namespace) -> int:
    sup = _supervisor(cfg)
    if sup is not None:
        if args.all:
            control.call(sup["port"], "POST", "/shutdown", token=sup["token"])
            gone = wait_until(lambda: _supervisor(cfg), False)
            print("GOS and its supervisor stopped." if gone else "Asked GOS to stop.")
            return 0 if gone else 1
        status = control.call(
            sup["port"], "POST", "/stop", token=sup["token"], timeout=STOP_CALL_S
        )
        print("Stopped (the supervisor keeps running; `gos serve` starts them again):")
        _print_processes(status)
        return 0
    core = _core(cfg)
    if core is not None:
        control.call(core["port"], "POST", "/api/admin/shutdown", token=core["token"])
        gone = wait_until(lambda: _core(cfg), False)
        print("The core stopped." if gone else "Asked the core to stop.")
        return 0 if gone else 1
    print("GOS isn't running.")
    return 0


def _status(cfg: Config, args: argparse.Namespace) -> int:
    core = _core(cfg)
    if core is not None:
        health = control.call(core["port"], "GET", "/api/health", token=core["token"])
        if args.json:
            print(json.dumps(health, indent=2))
            return 0
        print(f"overall: {health['overall']}")
        for item in health["items"]:
            line = f"  {item['label']:<14} {item['status']:<6} {item['detail']}"
            if item.get("pid"):
                line += f"  (pid {item['pid']})"
            if item.get("restarts"):
                line += f"  restarts: {item['restarts']}"
            if item.get("last_exit"):
                line += f"  last exit: code {item['last_exit']['code']}"
            print(line)
        return 0
    sup = _supervisor(cfg)
    if sup is not None:
        status = control.call(sup["port"], "GET", "/status", token=sup["token"])
        if args.json:
            print(json.dumps(status, indent=2))
            return 0
        print("The core isn't answering; the supervisor says:")
        _print_processes(status)
        return 1
    print(
        "GOS isn't running. If it should be, see "
        f"{cfg.logs_dir / 'supervisor.log'} (one JSON object per line)."
    )
    return 1


def _launch(cfg: Config, args: argparse.Namespace) -> int:
    home = args.home or cfg.launch_home or cfg.gos_home
    # The token goes only to a supervisor that answers with the file's pid (control.running).
    info = control.running(
        home / "data" / control.LAUNCH_FILE, control.SUPERVISOR_SERVICE
    )
    if info is None or "token" not in info:
        print(
            f"No supervisor is running for {home} (no live data/{control.LAUNCH_FILE}). "
            "Set [launch] home to Clayton's GOS_HOME, or pass --home.",
            file=sys.stderr,
        )
        return 1
    result = control.call(
        info["port"],
        "POST",
        "/launch",
        token=info["token"],
        body={"instance": args.instance},
    )
    print(f"Asked Prism Launcher to launch {result['launched']} (pid {result['pid']}).")
    return 0


def _token(cfg: Config, args: argparse.Namespace) -> int:
    if args.token_command == "list":
        rows = _core_call(cfg, "GET", "/api/tokens")
        if not rows:
            print("No client tokens.")
        for row in rows:
            state = "revoked" if row["revoked"] else "active"
            used = (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(row["last_used"]))
                if row["last_used"]
                else "never"
            )
            print(f"  {row['name']:<12} {state:<8} last used: {used}")
        return 0
    if args.token_command == "revoke":
        _core_call(cfg, "DELETE", f"/api/tokens/{quote(args.name, safe='')}")
        print(f"Revoked {args.name}'s token.")
        return 0
    result = _core_call(cfg, "POST", f"/api/tokens/{quote(args.name, safe='')}")
    if args.show:
        print(f"{args.name}: {result['token']}")
        print("Shown once; the core keeps only its hash.")
        return 0
    try:
        keys.set_key(f"token-{args.name}", result["token"])
    except keyring.errors.KeyringError as exc:
        print(f"Issued, but the keyring refused it ({exc}); revoke it and use --show.")
        return 1
    print(
        f"Stored {args.name}'s token in the keyring as {keys.target('token-' + args.name)}."
    )
    return 0


def _pair(cfg: Config, args: argparse.Namespace) -> int:
    pending = _core_call(cfg, "GET", "/api/pair/pending")
    if not pending:
        print("No device is waiting to pair.")
        return 0
    print("Type the code on the device that asked:")
    for row in pending:
        code = f"{row['code'][:4]} {row['code'][4:]}"
        print(f"  {code}  {row['name']}  (expires in {row['expires_in']} s)")
    return 0


def _devices(cfg: Config, args: argparse.Namespace) -> int:
    if args.devices_command == "revoke":
        _core_call(cfg, "DELETE", f"/api/devices/{quote(args.device_id, safe='')}")
        print(f"Revoked device {args.device_id}.")
        return 0
    rows = _core_call(cfg, "GET", "/api/devices")
    if not rows:
        print("No paired devices.")
    for row in rows:
        seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["last_seen"] or 0))
        state = "revoked" if row["revoked"] else "paired"
        print(f"  {row['id']}  {row['name']:<20} {state:<8} last seen: {seen}")
    return 0


_HANDLERS = {
    "serve": _serve,
    "stop": _stop,
    "status": _status,
    "launch": _launch,
    "token": _token,
    "pair": _pair,
    "devices": _devices,
}
