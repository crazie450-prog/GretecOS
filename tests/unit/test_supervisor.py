"""WO-2.1: gos-supervisor restarts, health probes, stop/start, the control port and gos launch.

The acceptance test runs a real supervisor process (``python -m gregtec.supervisor``) with the real
core on free loopback ports in a temporary GOS_HOME, kills the core and times the restart.

On Windows a venv's python.exe is a launcher that runs the interpreter as its child, so a Popen
pid isn't the pid a process reports (run files, /health): the tests compare reported pids.
"""

from __future__ import annotations

import http.client
import http.server
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

import pytest

from gregtec import applog, cli, config, control, servicecli, supervisor
from gregtec import deploy as app_deploy
from gregtec.api import server
from gregtec.supervisor import ProcessSpec, Supervisor

T = TypeVar("T")
PY = sys.executable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for[T](check: Callable[[], T | None], timeout: float, step: float = 0.1) -> T:
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout} s")
        time.sleep(step)


def write_config(tmp_path: Path, **extra: str) -> tuple[config.Config, Path]:
    home = tmp_path / "home"
    instances = tmp_path / "instances"
    lines = [
        "[paths]",
        f'gos_home = "{home.as_posix()}"',
        f'prism_root = "{(tmp_path / "prism").as_posix()}"',
        f'prism_instances = "{instances.as_posix()}"',
        *[f'{k} = "{v}"' for k, v in extra.items()],
        "[core]",
        f"port = {free_port()}",
        f"supervisor_port = {free_port()}",
    ]
    path = tmp_path / "gos.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config.load(path), path


@pytest.fixture(autouse=True)
def allow_elevated(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub's Windows runners run elevated; GOS refuses that (review finding 5)."""
    monkeypatch.setenv(supervisor.ALLOW_ELEVATED_ENV, "1")


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def kill(pid: int) -> None:
    # Windows: TerminateProcess. POSIX: SIGKILL, so nothing runs on the way out.
    os.kill(pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)


def proc_row(status: dict[str, Any], name: str = "core") -> dict[str, Any]:
    return next(p for p in status["processes"] if p["name"] == name)


# --- acceptance: a killed core restarts within 10 s and shows on the strip --------------------


def test_killed_core_restarts_within_10_s_and_shows_on_the_strip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, path = write_config(tmp_path)
    logs = tmp_path / "supervisor.out"
    with open(logs, "wb") as out:
        sup_proc = subprocess.Popen(
            [PY, "-m", "gregtec.supervisor", "--config", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            cwd=tmp_path,
        )
    try:
        run_file = cfg.run_dir / control.SUPERVISOR_FILE
        sup = wait_for(
            lambda: control.running(run_file, control.SUPERVISOR_SERVICE), 30
        )
        if sys.platform != "win32":  # Windows: sup_proc is the venv's launcher
            assert sup["pid"] == sup_proc.pid
        assert os.path.samefile(sup["config"], path)

        def status() -> dict[str, Any]:
            return control.call(sup["port"], "GET", "/status", token=sup["token"])

        def core_up(not_pid: int | None = None) -> dict[str, Any] | None:
            row = proc_row(status())
            ok = row["state"] == "running" and row["pid"] not in (None, not_pid)
            return row if ok else None

        first = wait_for(core_up, 30)
        assert first["restarts"] == 0 and first["last_exit"] is None
        # The pid the core reports, which is the one to kill (gos status shows it).
        assert first["pid"] == control.call(cfg.core_port, "GET", "/health")["pid"]
        assert first["launcher_pid"] is not None
        if sys.platform != "win32":
            assert first["launcher_pid"] == first["pid"]

        kill(first["pid"])
        killed_at = time.monotonic()
        second = wait_for(lambda: core_up(not_pid=first["pid"]), 10, step=0.05)
        took = time.monotonic() - killed_at
        with capsys.disabled():  # shown with pytest -s: the evidence for the PR
            print(f"core restarted {took:.2f} s after the kill")
        assert took < 10
        assert second["restarts"] == 1
        assert second["last_exit"]["code"] != 0

        # The health strip data, from the restarted core.
        core_file = cfg.run_dir / control.CORE_FILE
        core = wait_for(lambda: control.running(core_file, control.CORE_SERVICE), 10)
        assert core["pid"] == second["pid"]

        def strip_shows_restart() -> dict[str, Any] | None:
            health = control.call(
                core["port"], "GET", "/api/health", token=core["token"]
            )
            item = next(i for i in health["items"] if i["id"] == "core")
            # The core refreshes the supervisor's table every second.
            return item if item["restarts"] == 1 and item["status"] == "ok" else None

        item = wait_for(strip_shows_restart, 10)
        assert item["state"] == "running" and item["pid"] == second["pid"]
        assert item["launcher_pid"] == second["launcher_pid"]
        assert item["last_exit"]["code"] == second["last_exit"]["code"]
        health = control.call(core["port"], "GET", "/api/health", token=core["token"])
        sup_item = next(i for i in health["items"] if i["id"] == "supervisor")
        assert sup_item["status"] == "ok" and sup_item["pid"] == sup["pid"]

        capsys.readouterr()
        assert cli.main(["status", "--config", str(path)]) == 0
        printed = capsys.readouterr().out
        assert "restarts: 1" in printed and "Core" in printed
        assert f"(pid {second['pid']})" in printed

        # gos stop: the core stops through its stdin and stays stopped; the supervisor stays.
        assert cli.main(["stop", "--config", str(path)]) == 0
        row = proc_row(status())
        assert row["state"] == "stopped" and row["pid"] is None
        assert row["last_exit"]["code"] == 0  # a clean exit, not a kill
        time.sleep(2.5)
        assert proc_row(status())["state"] == "stopped"
        assert not core_file.exists()  # the core removed its run file on the way out

        # gos serve with a supervisor running: start what was stopped.
        assert cli.main(["serve", "--config", str(path)]) == 0
        wait_for(core_up, 30)

        # gos stop --all: everything, the supervisor too.
        assert cli.main(["stop", "--all", "--config", str(path)]) == 0
        assert sup_proc.wait(timeout=20) == 0
        assert not run_file.exists()
        assert not (cfg.data_dir / control.LAUNCH_FILE).exists()
        log_lines = (
            (cfg.logs_dir / "supervisor.log").read_text(encoding="utf-8").splitlines()
        )
        events = [json.loads(line)["msg"] for line in log_lines]
        assert "process exited" in events and "supervisor stopped" in events
    finally:
        if sup_proc.poll() is None:
            sup_proc.kill()
            sup_proc.wait(timeout=10)


# --- restarts and back-off ---------------------------------------------------------------------


def test_backoff_doubles_and_resets_after_a_stable_run(tmp_path: Path) -> None:
    cfg, _ = write_config(tmp_path)
    now = [1000.0]
    spec = ProcessSpec("crash", "Crash", (PY, "-c", "import sys; sys.exit(7)"))
    sup = Supervisor(cfg, [spec], clock=lambda: now[0])
    managed = sup.procs["crash"]

    def exit_and_tick() -> None:
        assert managed.proc is not None
        managed.proc.wait(timeout=30)
        sup.tick()

    sup.start_all()
    delays = []
    for _ in range(4):
        exit_and_tick()
        assert managed.state == "backoff"
        assert managed.last_exit is not None and managed.last_exit["code"] == 7
        delay = managed.restart_at - now[0]  # type: ignore[operator]
        delays.append(delay)
        assert managed.detail == f"exited with code 7; restarting in {int(delay)} s"
        now[0] += delay - 0.01
        sup.tick()
        assert managed.proc is None  # not yet
        now[0] += 0.01
        sup.tick()
        assert managed.proc is not None
    assert delays == [1, 2, 4, 8]
    assert managed.restarts == 4
    # A run that lasted STABLE_AFTER_S resets the back-off.
    now[0] += supervisor.STABLE_AFTER_S
    exit_and_tick()
    assert managed.restart_at - now[0] == 1  # type: ignore[operator]
    sup.stop_all()


def test_port_in_use_exit_is_explained(tmp_path: Path) -> None:
    cfg, _ = write_config(tmp_path)
    spec = ProcessSpec("core", "Core", (PY, "-c", "import sys; sys.exit(3)"))
    sup = Supervisor(cfg, [spec])
    sup.start_all()
    sup.procs["core"].proc.wait(timeout=30)  # type: ignore[union-attr]
    sup.tick()
    assert sup.procs["core"].detail.startswith("its port is in use by another program")
    sup.stop_all()


def test_core_exits_3_when_its_port_is_taken(tmp_path: Path) -> None:
    cfg, _ = write_config(tmp_path)
    holder = server.bind(cfg.core_port)
    try:
        with pytest.raises(OSError):
            server.bind(cfg.core_port)  # nobody can bind beside it
        assert server.run(cfg) == server.EXIT_PORT_IN_USE
    finally:
        holder.close()


FAKE = """
import http.server, json, os, sys, time
port, flag = int(sys.argv[1]), sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if os.path.exists(flag):
            time.sleep(5)
            return
        body = json.dumps({"service": "fake", "pid": os.getpid()}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
"""


def test_hung_process_is_killed_and_restarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor, "PROBE_EVERY_S", 0.2)
    monkeypatch.setattr(supervisor, "PROBE_TIMEOUT_S", 0.5)
    monkeypatch.setattr(supervisor, "HANG_AFTER_S", 1.0)
    cfg, _ = write_config(tmp_path)
    script = tmp_path / "fake.py"
    script.write_text(FAKE, encoding="utf-8")
    flag = tmp_path / "hang"
    port = free_port()
    spec = ProcessSpec(
        "fake",
        "Fake",
        (PY, "-I", str(script), str(port), str(flag)),
        health_port=port,
        health_service="fake",
    )
    sup = Supervisor(cfg, [spec])
    managed = sup.procs["fake"]

    def ticking(check: Callable[[], bool], timeout: float) -> None:
        def step() -> bool:
            sup.tick()
            return check()

        wait_for(step, timeout, step=0.05)

    try:
        sup.start_all()
        assert managed.state == "starting"
        ticking(lambda: managed.state == "running", 20)
        first_pid = managed.proc.pid  # type: ignore[union-attr]
        flag.write_text("")
        ticking(lambda: managed.state == "unresponsive", 10)
        ticking(lambda: managed.state == "backoff", 10)
        assert "didn't answer" in managed.detail
        flag.unlink()
        ticking(lambda: managed.state == "running" and managed.restarts == 1, 20)
        assert managed.proc.pid != first_pid  # type: ignore[union-attr]
        assert managed.last_exit is not None and managed.last_exit["code"] != 0
    finally:
        flag.unlink(missing_ok=True)
        sup.stop_all()


def test_start_that_never_answers_counts_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor, "START_GRACE_S", 0.5)
    monkeypatch.setattr(supervisor, "PROBE_EVERY_S", 0.1)
    cfg, _ = write_config(tmp_path)
    spec = ProcessSpec(
        "mute",
        "Mute",
        (PY, "-c", "import time; time.sleep(60)"),
        health_port=free_port(),  # nothing listens
    )
    sup = Supervisor(cfg, [spec])
    try:
        sup.start_all()

        def step() -> bool:
            sup.tick()
            return sup.procs["mute"].state == "backoff"

        wait_for(step, 15, step=0.05)
        assert "after starting" in sup.procs["mute"].detail
    finally:
        sup.stop_all()


def test_stop_asks_through_stdin_then_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = write_config(tmp_path)
    polite = ProcessSpec("polite", "Polite", (PY, "-c", "import sys; sys.stdin.read()"))
    stubborn = ProcessSpec(
        "stubborn",
        "Stubborn",
        (PY, "-c", "import time\nwhile True: time.sleep(1)"),
    )
    sup = Supervisor(cfg, [polite, stubborn])
    sup.start_all()
    time.sleep(0.5)
    started = time.monotonic()
    sup.stop("polite")  # within the full STOP_TIMEOUT_S: slow Windows runners
    assert time.monotonic() - started < supervisor.STOP_TIMEOUT_S
    assert sup.procs["polite"].last_exit["code"] == 0  # type: ignore[index]
    monkeypatch.setattr(supervisor, "STOP_TIMEOUT_S", 1.0)
    sup.stop("stubborn")
    assert sup.procs["stubborn"].last_exit["code"] != 0  # type: ignore[index]
    for managed in sup.procs.values():
        assert managed.state == "stopped" and managed.proc is None
    sup.tick()
    assert all(
        m.proc is None for m in sup.procs.values()
    )  # stopped means not restarted
    status = sup.status()
    assert [p["state"] for p in status["processes"]] == ["stopped", "stopped"]


def test_child_gets_status_token_and_runs_in_gos_home(tmp_path: Path) -> None:
    cfg, _ = write_config(tmp_path)
    out = tmp_path / "env.json"
    code = (
        "import json, os, sys; "
        f"json.dump({{'tok': os.environ.get('GOS_SUPERVISOR_TOKEN'), 'cwd': os.getcwd()}}, "
        f"open({str(out)!r}, 'w'))"
    )
    cfg.gos_home.mkdir(parents=True)
    sup = Supervisor(cfg, [ProcessSpec("env", "Env", (PY, "-c", code))])
    sup.start_all()
    sup.procs["env"].proc.wait(timeout=30)  # type: ignore[union-attr]
    sup.stop_all()
    seen = json.loads(out.read_text())
    assert seen["tok"] == sup.status_token
    assert Path(seen["cwd"]).resolve() == cfg.gos_home.resolve()
    assert (cfg.logs_dir / "env.stderr.log").exists()


# --- the control port and launches -------------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Supervisor]:
    cfg, _ = write_config(tmp_path)
    for name in ("GTNH-Play", "GTNH-Lab"):
        (cfg.prism_instances / name).mkdir(parents=True)
    script = tmp_path / "fake_prism.py"
    script.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(' '.join(sys.argv[2:]))\n",
        encoding="utf-8",
    )
    out = tmp_path / "launched.txt"

    def fake_prism(cfg: config.Config, instance: str) -> list[str]:
        return [PY, "-I", str(script), str(out), "--launch", instance]

    monkeypatch.setattr(supervisor, "prism_command", fake_prism)
    sup = Supervisor(cfg, [])
    sup.bind()
    thread = threading.Thread(target=sup.serve, daemon=True)
    thread.start()
    yield sup
    sup.request_shutdown()
    thread.join(timeout=10)


def raw(
    sup: Supervisor,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes | None = None,
) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", sup.cfg.supervisor_port, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        conn.close()


def test_control_port_refuses_browsers_and_foreign_hosts(served: Supervisor) -> None:
    port = served.cfg.supervisor_port
    host = f"127.0.0.1:{port}"
    auth = f"Bearer {served.admin_token}"
    assert (
        raw(served, "GET", "/status", {"Host": host, "Authorization": auth})[0] == 200
    )
    for headers in (
        {"Host": "evil.example", "Authorization": auth},
        {"Host": f"127.0.0.1:{port + 1}", "Authorization": auth},
        {"Authorization": auth},  # no Host at all
        {"Host": host, "Authorization": auth, "Origin": f"http://{host}"},
        {"Host": host, "Authorization": auth, "Sec-Fetch-Mode": "cors"},
    ):
        status, _body = raw(served, "GET", "/status", headers)
        assert status == 403, headers
    status, _ = raw(served, "GET", "/health", {"Host": f"localhost:{port}"})
    assert status == 200


def test_control_port_tokens_and_roles(served: Supervisor) -> None:
    port = served.cfg.supervisor_port
    health = control.call(port, "GET", "/health")
    assert health["service"] == "gos-supervisor" and health["pid"] == os.getpid()
    with pytest.raises(control.ControlError) as caught:
        control.call(port, "GET", "/status")
    assert caught.value.status == 401
    with pytest.raises(control.ControlError) as caught:
        control.call(port, "GET", "/status", token="wrong")
    assert caught.value.status == 401
    assert "processes" in control.call(
        port, "GET", "/status", token=served.status_token
    )
    for token, path in (
        (served.status_token, "/stop"),
        (served.status_token, "/shutdown"),
        (served.launch_token, "/stop"),
        (served.launch_token, "/start"),
    ):
        with pytest.raises(control.ControlError) as caught:
            control.call(port, "POST", path, token=token, body={})
        assert caught.value.status == 403
    with pytest.raises(control.ControlError) as caught:
        control.call(port, "GET", "/status", token=served.launch_token)
    assert caught.value.status == 403
    with pytest.raises(control.ControlError) as caught:
        control.call(
            port, "POST", "/restart", token=served.admin_token, body={"name": "x"}
        )
    assert caught.value.status == 404
    with pytest.raises(control.ControlError) as caught:
        control.call(port, "POST", "/nope", token=served.admin_token, body={})
    assert caught.value.status == 404
    status, _ = raw(
        served,
        "POST",
        "/launch",
        {
            "Host": f"127.0.0.1:{port}",
            "Authorization": f"Bearer {served.launch_token}",
            "Content-Type": "application/json",
        },
        b"not json",
    )
    assert status == 400


def test_launch_lab_and_export_never_play(served: Supervisor, tmp_path: Path) -> None:
    port, token = served.cfg.supervisor_port, served.launch_token
    out = tmp_path / "launched.txt"
    result = control.call(
        port, "POST", "/launch", token=token, body={"instance": "lab"}
    )
    assert result["launched"] == "GTNH-Lab" and result["role"] == "lab"
    assert wait_for(lambda: out.exists() and out.read_text(), 20) == "--launch GTNH-Lab"
    with pytest.raises(control.ControlError) as caught:
        control.call(port, "POST", "/launch", token=token, body={"instance": "lab"})
    assert caught.value.status == 429  # once per 30 s
    (served.cfg.prism_instances / "GTNH-Export").mkdir()
    with pytest.raises(control.ControlError) as caught:
        control.call(port, "POST", "/launch", token=token, body={"instance": "export"})
    assert caught.value.status == 429  # of any instance: Lab's Java may not be up yet
    (served.cfg.prism_instances / "GTNH-Export").rmdir()
    for instance in ("play", "GTNH-Play", "Play", "../GTNH-Play", None, 1):
        with pytest.raises(control.ControlError) as caught:
            control.call(
                port, "POST", "/launch", token=token, body={"instance": instance}
            )
        assert caught.value.status == 400, instance
    with pytest.raises(control.ControlError) as caught:
        control.call(port, "POST", "/launch", token=token, body={"instance": "export"})
    assert caught.value.status == 404  # no GTNH-Export folder in this test


def test_launch_refuses_an_instance_configured_as_play(tmp_path: Path) -> None:
    cfg, _ = write_config(tmp_path, export="GTNH-Play")
    (cfg.prism_instances / "GTNH-Play").mkdir(parents=True)
    sup = Supervisor(cfg, [])
    with pytest.raises(supervisor.LaunchError) as caught:
        sup.launch("export")
    assert caught.value.status == 409
    cfg2, _ = write_config(
        tmp_path, lab="gtnh-play"
    )  # case differs: still Play on Windows
    with pytest.raises(supervisor.LaunchError) as caught:
        Supervisor(cfg2, []).launch("lab")
    assert caught.value.status == 409


def test_launch_without_prism_says_so(tmp_path: Path) -> None:
    cfg, _ = write_config(tmp_path)
    (cfg.prism_instances / "GTNH-Lab").mkdir(parents=True)
    with pytest.raises(supervisor.LaunchError) as caught:
        Supervisor(cfg, []).launch("lab")
    assert caught.value.status == 503 and "Prism" in caught.value.reason


@pytest.mark.skipif(
    sys.platform == "win32", reason="the fake prismlauncher is a script"
)
def test_gos_launch_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """As gos-dev would: its own config, Clayton's GOS_HOME through [launch] home."""
    cfg, path = write_config(tmp_path)
    (cfg.prism_instances / "GTNH-Lab").mkdir(parents=True)
    prism = cfg.prism_root
    prism.mkdir()
    fake = prism / "prismlauncher"
    fake.write_text(
        f"#!{PY} -I\nimport pathlib, sys\n"
        "pathlib.Path(__file__).with_name('launched.txt').write_text(' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    sup_proc = subprocess.Popen(
        [PY, "-m", "gregtec.supervisor", "--config", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=tmp_path,
    )
    try:
        run_file = cfg.run_dir / control.SUPERVISOR_FILE
        wait_for(lambda: control.running(run_file, control.SUPERVISOR_SERVICE), 30)
        dev = tmp_path / "dev"
        dev.mkdir()
        dev_cfg = dev / "gos.toml"
        dev_cfg.write_text(
            f'[paths]\ngos_home = "{dev.as_posix()}"\n'
            f'[launch]\nhome = "{cfg.gos_home.as_posix()}"\n',
            encoding="utf-8",
        )
        assert cli.main(["launch", "lab", "--config", str(dev_cfg)]) == 0
        assert "GTNH-Lab" in capsys.readouterr().out
        launched = prism / "launched.txt"
        assert wait_for(lambda: launched.exists() and launched.read_text(), 20) == (
            "--launch GTNH-Lab"
        )
        assert cli.main(["stop", "--all", "--config", str(path)]) == 0
        assert sup_proc.wait(timeout=20) == 0
    finally:
        if sup_proc.poll() is None:
            sup_proc.kill()
            sup_proc.wait(timeout=10)


# --- entry points ------------------------------------------------------------------------------


def test_login_start_respects_core_at_login(tmp_path: Path) -> None:
    path = tmp_path / "gos.toml"
    path.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n'
        "[startup]\ncore_at_login = false\n",
        encoding="utf-8",
    )
    cfg = config.load(path)
    assert supervisor.run(cfg, path, login=True) == 0
    assert not cfg.run_dir.exists()
    assert "login start is off" in (cfg.logs_dir / "supervisor.log").read_text()


def test_second_supervisor_leaves_the_first_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, path = write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        control, "running", lambda run_file, service: {"pid": 1, "port": 1}
    )
    assert supervisor.run(cfg, path) == 0
    assert not (cfg.run_dir / control.SUPERVISOR_FILE).exists()


def test_children_use_the_console_python() -> None:
    exe = supervisor.python_exe()
    assert not exe.lower().endswith("pythonw.exe")


def test_core_spec_passes_the_config(tmp_path: Path) -> None:
    cfg, path = write_config(tmp_path)
    spec = supervisor.core_spec(cfg, path)
    assert spec.argv[1:] == ("-m", "gregtec.api", "--supervised", "--config", str(path))
    assert (spec.health_port, spec.health_service) == (cfg.core_port, "gregtec-core")
    assert "--config" not in supervisor.core_spec(cfg, None).argv


# --- CLI wiring (no processes) -----------------------------------------------------------------


def test_gos_launch_without_a_supervisor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, path = write_config(tmp_path)
    assert cli.main(["launch", "export", "--config", str(path)]) == 1
    assert "No supervisor is running" in capsys.readouterr().err


def test_gos_stop_and_status_with_nothing_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, path = write_config(tmp_path)
    assert cli.main(["stop", "--config", str(path)]) == 0
    assert "isn't running" in capsys.readouterr().out
    assert cli.main(["status", "--config", str(path)]) == 1
    assert cli.main(["pair", "--config", str(path)]) == 1
    assert "core isn't running" in capsys.readouterr().err


def test_gos_token_new_stores_in_keyring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, path = write_config(tmp_path)
    calls: list[tuple[str, str]] = []
    stored: dict[str, str] = {}

    def fake_call(cfg: config.Config, method: str, url: str) -> Any:  # noqa: ANN401
        calls.append((method, url))
        return {"name": "voice", "token": "T" * 43}

    monkeypatch.setattr(servicecli, "_core_call", fake_call)
    monkeypatch.setattr(servicecli.keys, "set_key", lambda n, v: stored.update({n: v}))
    assert cli.main(["token", "new", "voice", "--config", str(path)]) == 0
    assert stored == {"token-voice": "T" * 43}
    assert "T" * 43 not in capsys.readouterr().out  # stored, not shown
    assert cli.main(["token", "new", "mod", "--show", "--config", str(path)]) == 0
    assert "T" * 43 in capsys.readouterr().out
    assert calls == [("POST", "/api/tokens/voice"), ("POST", "/api/tokens/mod")]


def test_deploy_stops_a_supervisor_running_from_gos_app_only_for_the_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app = tmp_path / "app"
    _, path = write_config(tmp_path, gos_app=app.as_posix())
    exe = app_deploy.venv_exe(app / app_deploy.CURRENT, "gos-supervisor")
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    ran_with = str(tmp_path / "run key's" / "gos.toml")  # the Run key's --config
    running = {"pid": 1, "port": 1, "token": "t", "exe": str(exe), "config": ran_with}
    state = {"up": True}
    posted: list[str] = []
    spawned: list[list[str]] = []

    def fake_running(run_file: Path, service: str) -> dict[str, Any] | None:
        return running if state["up"] else None

    def fake_call(port: int, method: str, url: str, **kw: Any) -> dict[str, Any]:  # noqa: ANN401
        posted.append(url)
        state["up"] = False
        return {}

    def swap_fails(*args: Any, before_swap: Callable[[], None], **kw: Any) -> Any:  # noqa: ANN401
        assert state["up"], "GOS keeps running while the release is staged and built"
        before_swap()
        assert not state["up"], "GOS must be stopped before the swap"
        raise app_deploy.DeployError("simulated swap failure")

    def refused(*args: Any, **kw: Any) -> Any:  # noqa: ANN401
        raise app_deploy.DeployError("simulated: uncommitted changes")

    monkeypatch.setattr(control, "running", fake_running)
    monkeypatch.setattr(control, "call", fake_call)
    monkeypatch.setattr(
        servicecli, "spawn_detached", lambda argv, cwd: spawned.append(argv)
    )
    monkeypatch.setattr(app_deploy, "deploy", swap_fails)
    assert cli.main(["deploy", "--config", str(path)]) == 1
    assert posted == ["/shutdown"]
    # The old release, restarted with the gos.toml it ran with, not deploy's --config.
    assert spawned == [[str(exe), "--config", ran_with]]
    assert "Restarted GOS" in capsys.readouterr().out

    # A deploy refused before the swap never stops GOS.
    state["up"], posted[:], spawned[:] = True, [], []
    monkeypatch.setattr(app_deploy, "deploy", refused)
    assert cli.main(["deploy", "--config", str(path)]) == 1
    assert posted == [] and spawned == [] and state["up"]

    # A supervisor that doesn't run from GOS_APP (a dev run) is left alone.
    def leaves_it(*args: Any, before_swap: Callable[[], None], **kw: Any) -> Any:  # noqa: ANN401
        before_swap()
        assert state["up"]
        raise app_deploy.DeployError("simulated")

    running["exe"] = str(tmp_path / "elsewhere" / "python")
    monkeypatch.setattr(app_deploy, "deploy", leaves_it)
    assert cli.main(["deploy", "--config", str(path)]) == 1
    assert posted == [] and spawned == []

    # One started without a config restarts without one.
    running.update(exe=str(exe), config=None)
    monkeypatch.setattr(app_deploy, "deploy", swap_fails)
    assert cli.main(["deploy", "--config", str(path)]) == 1
    assert spawned == [[str(exe)]]


def test_supervisor_command_prefers_the_windowless_launcher(tmp_path: Path) -> None:
    cfg, path = write_config(tmp_path)
    argv = servicecli.supervisor_command(cfg, path)
    assert argv[-2:] == ["--config", str(path)]
    assert Path(argv[0]).name.startswith("gos-supervisor") or argv[1:3] == [
        "-m",
        "gregtec.supervisor",
    ]


def test_applog_handles_a_missing_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stderr", None)
    path = applog.setup("nostderr", tmp_path)
    logging.getLogger("x").warning("still logged")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "still logged" in path.read_text()


# --- the real server: uvicorn's HTTP and WebSocket stack ---------------------------------------


def test_standalone_core_over_real_sockets(tmp_path: Path) -> None:
    """Host/Origin checks and auth on uvicorn itself, WebSocket handshake included."""
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect

    cfg, path = write_config(tmp_path)
    core_proc = subprocess.Popen(
        [PY, "-m", "gregtec.api", "--config", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=tmp_path,
    )
    try:
        core_file = cfg.run_dir / control.CORE_FILE
        core = wait_for(lambda: control.running(core_file, control.CORE_SERVICE), 30)
        port, token = core["port"], core["token"]
        host = f"127.0.0.1:{port}"

        def get(headers: dict[str, str], path: str = "/api/health") -> tuple[int, str]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.putrequest("GET", path, skip_host=True)
                for key, value in headers.items():
                    conn.putheader(key, value)
                conn.endheaders()
                response = conn.getresponse()
                response.read()
                return response.status, response.getheader("server") or ""
            finally:
                conn.close()

        bearer = {"Authorization": f"Bearer {token}"}
        assert get({"Host": host, **bearer}) == (200, "")  # no server banner
        assert get({"Host": "evil.example", **bearer})[0] == 403
        assert get(bearer)[0] in (400, 403)  # no Host: HTTP/1.1 itself refuses it
        assert get({"Host": host, "Origin": "http://evil.example", **bearer})[0] == 403
        assert get({"Host": host})[0] == 401

        url = f"ws://{host}/ws"
        with connect(url, additional_headers=bearer, proxy=None) as ws:
            assert json.loads(ws.recv(timeout=10))["type"] == "hello"
            assert json.loads(ws.recv(timeout=10))["type"] == "health"
            ws.send(json.dumps({"type": "ping"}))
            while json.loads(ws.recv(timeout=10))["type"] != "pong":
                pass
        refused: list[dict[str, Any]] = [
            {"additional_headers": bearer, "origin": "http://evil.example"},
            {},  # unpaired
            {"additional_headers": {"Authorization": "Bearer wrong"}},
        ]
        for kwargs in refused:
            with pytest.raises(InvalidStatus) as caught:
                connect(url, proxy=None, **kwargs)
            assert caught.value.response.status_code == 403

        # A frame past MAX_MESSAGE is refused by the protocol layer (ws_max_size).
        from websockets.exceptions import ConnectionClosed

        with connect(url, additional_headers=bearer, proxy=None) as ws:
            ws.recv(timeout=10)
            ws.send("x" * 5000)
            with pytest.raises(ConnectionClosed) as closed:
                while True:
                    ws.recv(timeout=10)
            assert closed.value.rcvd is not None and closed.value.rcvd.code == 1009

        # No access log: query strings and the probes stay out of core.log (D82).
        get({"Host": host, **bearer}, "/api/health?token=secret-in-query")
        for line in (cfg.logs_dir / "core.log").read_text("utf-8").splitlines():
            entry = json.loads(line)
            assert not entry["logger"].startswith("uvicorn.access"), entry
            assert "secret-in-query" not in line

        # gos stop on a core with no supervisor: the admin shutdown route.
        assert cli.main(["stop", "--config", str(path)]) == 0
        assert core_proc.wait(timeout=20) == 0
        assert not core_file.exists()
    finally:
        if core_proc.poll() is None:
            core_proc.kill()
            core_proc.wait(timeout=10)


# --- review fixes ------------------------------------------------------------------------------


def test_start_during_a_stop_waits_and_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor, "STOP_TIMEOUT_S", 1.0)
    cfg, _ = write_config(tmp_path)
    spec = ProcessSpec(
        "stubborn", "Stubborn", (PY, "-c", "import time\nwhile True: time.sleep(1)")
    )
    sup = Supervisor(cfg, [spec])
    sup.start_all()
    time.sleep(0.5)
    first = sup.procs["stubborn"].proc
    stopper = threading.Thread(target=sup.stop, args=("stubborn",))
    stopper.start()
    time.sleep(0.2)  # the stop is now waiting out STOP_TIMEOUT_S
    sup.start("stubborn")  # as a /start that overlaps a /stop
    stopper.join(timeout=10)
    try:
        managed = sup.procs["stubborn"]
        assert managed.wanted and managed.proc is not None and managed.proc is not first
        assert managed.state == "running"
    finally:
        sup.stop_all()


def test_restart_route_checks_the_name(served: Supervisor) -> None:
    port, token = served.cfg.supervisor_port, served.admin_token
    for name in ([], {}, 5, None):
        with pytest.raises(control.ControlError) as caught:
            control.call(port, "POST", "/restart", token=token, body={"name": name})
        assert caught.value.status == 404, name


def test_gos_launch_sends_no_token_to_a_stranger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale launch.json whose port another program now answers on: no token goes out."""
    seen: list[str | None] = []

    class Stranger(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # http.server naming
            seen.append(self.headers.get("Authorization"))
            body = json.dumps({"service": "gos-supervisor", "pid": 999_999}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

        def log_message(
            self, format: str, *args: object
        ) -> None:  # base class signature
            pass

    stranger = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stranger)
    thread = threading.Thread(target=stranger.serve_forever, daemon=True)
    thread.start()
    try:
        cfg, path = write_config(tmp_path)
        launch = cfg.data_dir / control.LAUNCH_FILE
        port = stranger.server_address[1]
        control.write_run_file(launch, {"pid": 1, "port": port, "token": "SECRET"})
        assert cli.main(["launch", "lab", "--config", str(path)]) == 1
        assert "No supervisor is running" in capsys.readouterr().err
        assert seen == [None]  # only the token-free /health probe
    finally:
        stranger.shutdown()
        stranger.server_close()


# --- review fixes (2): Windows pids, clients running, elevation, config, logs ------------------


def test_status_pid_is_the_one_the_process_reports(tmp_path: Path) -> None:
    """Windows: Popen's pid is the venv launcher's; /health and run files have the child's."""
    cfg, _ = write_config(tmp_path)
    script = tmp_path / "fake.py"
    script.write_text(FAKE, encoding="utf-8")
    port = free_port()
    spec = ProcessSpec(
        "fake",
        "Fake",
        (PY, "-I", str(script), str(port), str(tmp_path / "never")),
        health_port=port,
        health_service="fake",
    )
    sup = Supervisor(cfg, [spec])
    managed = sup.procs["fake"]
    try:
        sup.start_all()
        assert proc_row(sup.status(), "fake")["pid"] is None  # not answered yet
        launcher = managed.proc.pid  # type: ignore[union-attr]

        def step() -> bool:
            sup.tick()
            return managed.state == "running"

        wait_for(step, 20, step=0.05)
        reported = control.call(port, "GET", "/health")["pid"]
        row = proc_row(sup.status(), "fake")
        assert (row["pid"], row["launcher_pid"]) == (reported, launcher)
    finally:
        sup.stop_all()
    row = proc_row(sup.status(), "fake")
    assert (row["pid"], row["launcher_pid"]) == (None, None)
    sleeper = ProcessSpec("x", "X", (PY, "-c", "import time; time.sleep(30)"))
    no_health = Supervisor(cfg, [sleeper])
    try:
        no_health.start_all()
        row = proc_row(no_health.status(), "x")
        assert row["pid"] is not None and row["pid"] == row["launcher_pid"]
    finally:
        no_health.stop_all()


def test_launch_is_refused_while_a_gtnh_client_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = write_config(tmp_path)
    for name in ("GTNH-Play", "GTNH-Lab", "GTNH-Export"):
        (cfg.prism_instances / name).mkdir(parents=True)
    launched: list[list[str]] = []

    class Proc:
        pid = 4242

        def wait(self) -> int:
            return 0

    def popen(argv: list[str], **kw: Any) -> Proc:  # noqa: ANN401
        launched.append(argv)
        return Proc()

    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    play = (
        str(cfg.instance("play")).replace("/", "\\").upper()
    )  # as Windows may show it
    java = [f'"C:\\Java\\bin\\javaw.exe" -Djava.library.path={play}\\natives -cp x']
    monkeypatch.setattr(supervisor, "command_lines", lambda: java)
    sup = Supervisor(cfg, [])
    with pytest.raises(supervisor.LaunchError) as caught:
        sup.launch("lab")
    assert caught.value.status == 409 and "GTNH-Play is running" in caught.value.reason
    # Lab running: Export is refused too (never two clients at once).
    lab = cfg.instance("lab").as_posix()
    java[:] = [f"/usr/bin/java -cp {lab}/libraries/a.jar:{lab}/libraries/b.jar Main"]
    with pytest.raises(supervisor.LaunchError) as caught:
        sup.launch("export")
    assert caught.value.status == 409 and "GTNH-Lab" in caught.value.reason
    # A refusal doesn't use up the 30 s: once it has quit, the launch goes ahead.
    java[:] = []
    assert sup.launch("export")["launched"] == "GTNH-Export"
    assert launched[-1][-2:] == ["--launch", "GTNH-Export"]

    def cannot_tell() -> list[str]:
        raise OSError("PowerShell exited 1")

    monkeypatch.setattr(supervisor, "command_lines", cannot_tell)
    with pytest.raises(supervisor.LaunchError) as caught:
        Supervisor(cfg, []).launch("lab")
    assert caught.value.status == 503 and "couldn't check" in caught.value.reason
    assert len(launched) == 1


@pytest.mark.parametrize(
    ("line", "found"),
    [
        ("java -Djava.library.path=C:/Games/Prism/instances/GTNH-Play/natives", True),
        (
            '"javaw.exe" -cp "C:\\Games\\Prism\\instances\\gtnh-play\\libraries\\x.jar"',
            True,
        ),
        ("java -jar C:/Games/Prism/instances/GTNH-Play", True),
        ("java -cp C:/Games/Prism/instances/GTNH-Play;C:/other", True),
        ("java -cp C:/Games/Prism/instances/GTNH-Play2/libraries/x.jar", False),
        ("java -cp C:/Games/Prism/instances/GTNH-Lab/libraries/x.jar", False),
        ("java -cp D:/Games/Prism/instances/GTNH-Play/libraries/x.jar", False),
    ],
)
def test_folder_in_a_command_line(line: str, found: bool) -> None:
    folder = Path("C:/Games/Prism/instances/GTNH-Play")
    assert supervisor._folder_in(folder, line) is found


def test_command_lines_lists_real_processes(tmp_path: Path) -> None:
    """The real lister (PowerShell's Win32_Process on Windows, /proc on Linux)."""
    folder = tmp_path / "GTNH-Marker"
    marker = str(folder / "natives")
    child = subprocess.Popen(
        [PY, "-c", "import time; time.sleep(60)", marker],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.5)
        lines = supervisor.command_lines(names=(Path(PY).name,))
        mine = [line for line in lines if marker in line]
        assert mine, lines[:5]
        assert supervisor._folder_in(folder, mine[0])
        assert not any(
            marker in line for line in supervisor.command_lines()
        )  # not java
    finally:
        child.kill()
        child.wait(timeout=10)


def test_gos_refuses_to_run_elevated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, path = write_config(tmp_path)
    monkeypatch.setattr(supervisor, "is_elevated", lambda: True)
    monkeypatch.delenv(supervisor.ALLOW_ELEVATED_ENV)
    monkeypatch.chdir(tmp_path)
    assert supervisor.run(cfg, path) == supervisor.EXIT_ELEVATED
    assert not (cfg.run_dir / control.SUPERVISOR_FILE).exists()
    logged = (cfg.logs_dir / "supervisor.log").read_text("utf-8")
    assert "refusing to start" in logged and "elevated" in logged
    assert server.run(cfg) == supervisor.EXIT_ELEVATED
    assert cli.main(["serve", "--detach", "--config", str(path)]) == 4
    assert "elevated" in capsys.readouterr().err
    monkeypatch.setenv(supervisor.ALLOW_ELEVATED_ENV, "1")  # CI only
    assert supervisor.elevation_problem() is None


def test_is_elevated_answers() -> None:
    assert isinstance(supervisor.is_elevated(), bool)
    if sys.platform != "win32":
        assert supervisor.is_elevated() is False


def test_bad_service_keys_stop_gos_starting_but_not_other_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, path = write_config(tmp_path)
    path.write_text(
        path.read_text("utf-8") + 'allowed_hosts = ["<pc>.<tailnet>.ts.net"]\n', "utf-8"
    )
    cfg = config.load(path)
    monkeypatch.chdir(tmp_path)
    assert supervisor.run(cfg, path) == 2
    assert "allowed_hosts" in (cfg.logs_dir / "supervisor.log").read_text("utf-8")
    assert server.run(cfg) == 2
    assert not cfg.run_dir.exists()
    capsys.readouterr()
    assert cli.main(["serve", "--config", str(path)]) == 2
    assert "Not starting GOS" in capsys.readouterr().err
    # Other commands only warn: gos stop still works.
    assert cli.main(["stop", "--config", str(path)]) == 0
    assert "Warning: gos.toml: [core] allowed_hosts" in capsys.readouterr().err


def test_gos_dev_may_not_serve_on_claytons_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """D115: a gos-dev core on 8765 would lock Clayton's GOS out and get his clients' tokens."""
    monkeypatch.setattr(config, "running_as_dev", lambda: True)
    path = tmp_path / "gos.toml"
    path.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n'
        '[core]\nport = 8765\nsupervisor_port = 8776\ndashboard_host = "gos-dev.localhost"\n',
        encoding="utf-8",
    )
    for argv in (["serve"], ["serve", "--core-only"], ["serve", "--detach"]):
        assert cli.main([*argv, "--config", str(path)]) == 2
        assert "Clayton's 8765 and 8766" in capsys.readouterr().err
    cfg = config.load(path)
    monkeypatch.chdir(tmp_path)
    assert supervisor.run(cfg, path) == 2
    assert server.run(cfg) == 2
    assert not cfg.run_dir.exists()


def test_a_broken_gos_toml_at_login_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pythonw has no stderr: the default GOS_HOME's supervisor.log is the only trace."""
    monkeypatch.setenv("GOS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "stderr", None)
    bad = tmp_path / "gos.toml"
    bad.write_text("[paths\n", encoding="utf-8")
    assert supervisor.main(["--login", "--config", str(bad)]) == 2
    for handler in logging.getLogger().handlers:
        handler.flush()
    log_file = tmp_path / "home" / "logs" / "supervisor.log"
    entry = json.loads(log_file.read_text("utf-8").splitlines()[-1])
    assert entry["msg"] == "can't read gos.toml" and entry["path"] == str(bad)


def test_stderr_log_that_cant_rotate_keeps_its_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor, "STDERR_MAX", 1000)
    monkeypatch.setattr(supervisor, "STDERR_TAIL", 100)
    log_path = tmp_path / "core.stderr.log"
    log_path.write_bytes(b"a" * 2000 + b"Traceback: the last crash\n")

    def held(src: object, dst: object) -> None:
        raise PermissionError(32, "in use")

    with monkeypatch.context() as m:
        m.setattr(supervisor.os, "replace", held)
        with supervisor._open_stderr_log(log_path) as f:
            f.write(b"next run\n")
    text = log_path.read_bytes()
    assert (
        len(text) < 300 and b"the last crash" in text and text.endswith(b"next run\n")
    )
    monkeypatch.setattr(supervisor, "STDERR_MAX", 10)
    with supervisor._open_stderr_log(log_path) as f:
        f.write(b"rotated\n")
    assert log_path.read_bytes() == b"rotated\n"
    assert b"next run" in (tmp_path / "core.stderr.log.1").read_bytes()


def test_gos_stop_waits_long_enough_for_a_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, path = write_config(tmp_path)
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(
        servicecli, "_supervisor", lambda cfg: {"port": 1, "token": "t"}
    )

    def fake_call(port: int, method: str, url: str, **kw: Any) -> dict[str, Any]:  # noqa: ANN401
        calls.append((url, kw.get("timeout", 5.0)))
        return {"processes": []}

    monkeypatch.setattr(control, "call", fake_call)
    assert cli.main(["stop", "--config", str(path)]) == 0
    # A stop can take STOP_TIMEOUT_S, then as long again for the kill.
    assert calls == [("/stop", servicecli.STOP_CALL_S)]
    assert servicecli.STOP_CALL_S > 2 * supervisor.STOP_TIMEOUT_S + 5


def test_gos_launch_through_a_supervisor_on_any_os(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """As gos-dev would, against an in-process supervisor.run: Windows too, unlike
    test_gos_launch_end_to_end's script prismlauncher. launch.json, its token, the port."""
    cfg, path = write_config(tmp_path)
    (cfg.prism_instances / "GTNH-Lab").mkdir(parents=True)
    out = tmp_path / "launched.txt"
    script = tmp_path / "fake_prism.py"
    script.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(' '.join(sys.argv[2:]))\n",
        encoding="utf-8",
    )

    def fake_prism(cfg: config.Config, instance: str) -> list[str]:
        return [PY, "-I", str(script), str(out), "--launch", instance]

    def idle_core(cfg: config.Config, config_path: Path | None) -> ProcessSpec:
        return ProcessSpec("idle", "Idle", (PY, "-c", "import sys; sys.stdin.read()"))

    monkeypatch.setattr(supervisor, "prism_command", fake_prism)
    monkeypatch.setattr(supervisor, "core_spec", idle_core)
    monkeypatch.chdir(tmp_path)  # run() moves into GOS_HOME; this puts it back
    relative = Path(os.path.relpath(path, tmp_path))
    done: list[int] = []
    thread = threading.Thread(target=lambda: done.append(supervisor.run(cfg, relative)))
    thread.start()
    run_file = cfg.run_dir / control.SUPERVISOR_FILE
    try:
        sup = wait_for(
            lambda: control.running(run_file, control.SUPERVISOR_SERVICE), 30
        )
        # Made absolute before the move into GOS_HOME.
        assert Path(sup["config"]).is_absolute() and os.path.samefile(
            sup["config"], path
        )
        dev = tmp_path / "dev"
        dev.mkdir()
        dev_cfg = dev / "gos.toml"
        dev_cfg.write_text(
            f'[paths]\ngos_home = "{dev.as_posix()}"\n'
            f'[launch]\nhome = "{cfg.gos_home.as_posix()}"\n',
            encoding="utf-8",
        )
        assert cli.main(["launch", "lab", "--config", str(dev_cfg)]) == 0
        assert "GTNH-Lab" in capsys.readouterr().out
        assert wait_for(lambda: out.exists() and out.read_text(), 20) == (
            "--launch GTNH-Lab"
        )
        assert cli.main(["stop", "--all", "--config", str(path)]) == 0
        thread.join(timeout=30)
        assert done == [0]
    finally:
        if thread.is_alive():
            info = control.read_run_file(run_file)
            if info is not None:
                control.call(info["port"], "POST", "/shutdown", token=info["token"])
            thread.join(timeout=30)


def test_supervisor_command_passes_an_absolute_config(tmp_path: Path) -> None:
    cfg, _ = write_config(tmp_path)
    argv = servicecli.supervisor_command(cfg, Path("rel") / "gos.toml")
    assert Path(argv[-1]).is_absolute() and argv[-1].endswith("gos.toml")
