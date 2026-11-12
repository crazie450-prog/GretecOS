"""The watchers and voice start and stop with Play (D77, D249, D259): the supervisor's with-Play
processes, its ``POST /play``, the crash fallback, the Prism hooks' notices, and ``gos watch
run`` itself. Fake instances and fake processes only; Play is never touched."""

from __future__ import annotations

import io
import json
import logging
import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from gregtec import cli, config, control, playhooks, supervisor
from gregtec.llm import keys
from gregtec.supervisor import ProcessSpec, Supervisor
from gregtec.watchers import runner

PY = sys.executable


@pytest.fixture(autouse=True)
def allow_elevated(monkeypatch: pytest.MonkeyPatch) -> None:
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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def make_cfg(tmp_path: Path, **extra: str) -> tuple[config.Config, Path]:
    lines = [
        "[paths]",
        f'gos_home = "{(tmp_path / "home").as_posix()}"',
        f'prism_root = "{(tmp_path / "prism").as_posix()}"',
        f'prism_instances = "{(tmp_path / "prism" / "instances").as_posix()}"',
        *[f'{k} = "{v}"' for k, v in extra.items()],
        "[core]",
        f"port = {free_port()}",
        f"supervisor_port = {free_port()}",
    ]
    path = tmp_path / "gos.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config.load(path), path


def waiter(out: Path) -> tuple[str, ...]:
    """A with-Play stand-in: writes its GOS_PLAY_INSTANCE, then waits for 'stop' on stdin."""
    code = (
        "import os, sys, pathlib; "
        f"pathlib.Path({str(out)!r}).write_text(os.environ.get('GOS_PLAY_INSTANCE', '')); "
        "sys.stdin.read()"
    )
    return (PY, "-c", code)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_with_play_processes_start_and_stop_with_the_hooks(tmp_path: Path) -> None:
    cfg, _ = make_cfg(tmp_path)
    seen = tmp_path / "instance.txt"
    core = ProcessSpec("core", "Core", (PY, "-c", "import sys; sys.stdin.read()"))
    watch = ProcessSpec("watchers", "Game watchers", waiter(seen), with_play=True)
    sup = Supervisor(cfg, [core, watch])
    try:
        sup.start_all()  # at login: the core only
        row = {p["name"]: p for p in sup.status()["processes"]}
        assert (
            row["core"]["state"] == "running" and row["watchers"]["state"] == "stopped"
        )
        assert row["watchers"]["detail"] == "starts when Play launches"
        sup.play_event("launch", "lab")
        assert sup.procs["watchers"].proc is not None
        deadline = time.monotonic() + 30
        while not seen.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (
            seen.read_text() == "lab"
        )  # the watchers watch the instance that launched
        assert sup.play_event("exit", "play") == {"playing": "lab", "ignored": True}
        sup.play_event("exit", "lab")
        managed = sup.procs["watchers"]
        assert managed.proc is None and managed.state == "stopped"
        assert managed.detail == "stopped: lab exited"
        assert sup.procs["core"].proc is not None  # the core stays
        sup.start_all()  # gos serve while not playing: still not the watchers
        assert sup.procs["watchers"].proc is None
        with pytest.raises(ValueError):
            sup.play_event("launch", "export")
        with pytest.raises(ValueError):
            sup.play_event("crash", "play")
    finally:
        sup.stop_all()


def test_a_with_play_process_that_cannot_run_stays_stopped(tmp_path: Path) -> None:
    cfg, _ = make_cfg(tmp_path)
    spec = ProcessSpec(
        "voice",
        "Voice",
        (PY, "-c", "import sys; sys.exit(2)"),
        with_play=True,
        stay_stopped=((2, "can't start: no voice token"),),
    )
    sup = Supervisor(cfg, [spec])
    sup.play_event("launch", "play")
    sup.procs["voice"].proc.wait(timeout=30)  # type: ignore[union-attr]
    sup.tick()
    voice = sup.procs["voice"]
    assert voice.state == "stopped" and not voice.wanted and voice.restart_at is None
    assert voice.detail == "can't start: no voice token"
    sup.stop_all()


def test_a_client_that_goes_away_without_post_exit_stops_them(tmp_path: Path) -> None:
    cfg, _ = make_cfg(tmp_path)
    clock = Clock()
    running: list[str] = []
    spec = ProcessSpec(
        "watchers", "W", (PY, "-c", "import sys; sys.stdin.read()"), with_play=True
    )
    sup = Supervisor(cfg, [spec], clock=clock, clients=lambda _: list(running))
    try:
        sup.play_event("launch", "play")
        clock.now += 60
        sup.check_play()  # Prism is still logging in: not seen yet, within the grace
        assert sup.play is not None and sup.play["gone_since"] is None
        running.append("play")
        clock.now += 60
        sup.check_play()
        assert sup.play["seen"]
        running.clear()  # crashed: Prism runs no post-exit after a crash (D121)
        clock.now += 15
        sup.check_play()
        clock.now += supervisor.PLAY_GONE_S - 1
        sup.check_play()
        assert (
            sup.procs["watchers"].proc is not None
        )  # the post-exit question may be up
        clock.now += 2
        sup.check_play()
        assert sup.play is None and sup.procs["watchers"].proc is None
        assert "went away" in sup.procs["watchers"].detail
        # never seen at all: stopped after the start grace and the gone time
        sup.play_event("launch", "play")
        clock.now += supervisor.PLAY_START_GRACE_S
        sup.check_play()
        clock.now += supervisor.PLAY_GONE_S
        sup.check_play()
        assert sup.play is None
    finally:
        sup.stop_all()


def test_the_real_specs(tmp_path: Path) -> None:
    w = supervisor.watchers_spec(tmp_path / "gos.toml")
    v = supervisor.voice_spec(None)
    assert w.with_play and v.with_play
    assert w.argv[1:4] == ("-m", "gregtec.watchers", "--supervised")
    assert w.argv[-2:] == ("--config", str(tmp_path / "gos.toml"))
    assert v.argv[1:] == ("-m", "gregtec_voice", "run", "--supervised")
    assert dict(w.stay_stopped)[2] and dict(v.stay_stopped)[2]
    cfg, _ = make_cfg(tmp_path)
    assert cfg.voice_with_play is True
    (tmp_path / "off.toml").write_text(
        "[startup]\nvoice_with_play = false\n", encoding="utf-8"
    )
    assert config.load(tmp_path / "off.toml").voice_with_play is False


def test_the_control_port_and_the_hook_notice(tmp_path: Path) -> None:
    cfg, _ = make_cfg(tmp_path)
    seen = tmp_path / "instance.txt"
    sup = Supervisor(cfg, [ProcessSpec("watchers", "W", waiter(seen), with_play=True)])
    sup.bind()
    thread = threading.Thread(target=sup.serve, daemon=True)
    thread.start()
    port = cfg.supervisor_port
    try:
        with pytest.raises(control.ControlError) as caught:
            control.call(port, "POST", "/play", token=sup.launch_token, body={})
        assert caught.value.status == 403  # gos-dev's launch token can't
        with pytest.raises(control.ControlError) as caught:
            control.call(
                port, "POST", "/play", token=sup.admin_token, body={"event": "x"}
            )
        assert caught.value.status == 400
        out = io.StringIO()
        # no run file: GOS isn't running, the game still launches
        assert playhooks.tell_supervisor(cfg, "launch", "lab", out) is False
        assert "GOS isn't running" in out.getvalue()
        control.write_run_file(
            cfg.run_dir / control.SUPERVISOR_FILE,
            {"pid": os.getpid(), "port": port, "token": sup.admin_token},
        )
        assert playhooks.tell_supervisor(cfg, "launch", "lab", out) is True
        assert "watchers and voice start (lab)" in out.getvalue()
        assert sup.play is not None and sup.play["instance"] == "lab"
        assert playhooks.tell_supervisor(cfg, "exit", "lab", out) is True
        assert sup.play is None and sup.procs["watchers"].proc is None
    finally:
        sup.request_shutdown()
        thread.join(timeout=15)
        sup.stop_all()


def test_the_hooks_tell_the_supervisor_after_the_export_question(
    tmp_path: Path,
) -> None:
    cfg, _ = make_cfg(tmp_path)
    for name in ("GTNH-Play", "GTNH-Lab"):
        mods = cfg.prism_instances / name / ".minecraft" / "mods"
        mods.mkdir(parents=True)
        (mods / "gregtech-5.09.54.265.jar").write_bytes(b"gt")
    order: list[str] = []

    def tell(c: config.Config, event: str, role: str, out: object) -> bool:
        order.append(f"{event}:{role}")
        return True

    def ask(title: str, question: str, timeout_s: int) -> bool | None:
        order.append("asked")
        return False

    out = io.StringIO()
    assert playhooks.pre_launch(cfg, "lab", out, clients=lambda _: [], tell=tell) == 0
    assert playhooks.pre_launch(cfg, "play", out, clients=lambda _: [], tell=tell) == 0
    assert (
        playhooks.pre_launch(cfg, "export", out, clients=lambda _: [], tell=tell) == 0
    )
    assert order == ["launch:lab", "launch:play"]  # Export never starts them
    order.clear()
    assert playhooks.pre_launch(
        cfg, "play", out, clients=lambda _: ["export"], tell=tell
    ) == (playhooks.EXIT_REFUSE)
    assert order == []  # refused: nothing starts
    assert playhooks.post_exit(cfg, "play", out, ask=ask, tell=tell) == 0
    assert order == ["asked", "exit:play"]  # plan 4.2: once the question is answered
    order.clear()
    assert playhooks.post_exit(cfg, "lab", out, ask=ask, tell=tell) == 0
    assert order == ["exit:lab"]


def test_gos_watch_run_spools_events(
    tmp_path: Path, fg: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "prism"
    tree = fg.Tree(root)
    tree.log("")
    cfg_path = tmp_path / "gos.toml"
    cfg_path.write_text(
        "[paths]\n"
        f'gos_home = "{(tmp_path / "home").as_posix()}"\n'
        f'prism_instances = "{(root / "instances").as_posix()}"\n'
        'lab_world = "lab-world"\n',
        encoding="utf-8",
    )

    def no_token(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", no_token)

    def game() -> None:
        time.sleep(1.5)
        tree.log(fg.stamp_line("Saving and pausing game...", "Server thread"))

    threading.Thread(target=game, daemon=True).start()
    code = cli.main(
        [
            "watch",
            "run",
            "--instance",
            "lab",
            "--config",
            str(cfg_path),
            "--seconds",
            "4",
        ]
    )
    assert code == 0
    spool = list((tmp_path / "home" / "data" / "events").glob("*.jsonl"))
    events = [json.loads(line) for line in spool[0].read_text("utf-8").splitlines()]
    assert [e["kind"] for e in events] == ["game_paused"]
    assert (tmp_path / "home" / "data" / "watchers" / "tails.json").is_file()


def test_gos_dev_never_watches_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, path = make_cfg(tmp_path)
    monkeypatch.setattr(config, "running_as_dev", lambda: True)
    assert cli.main(["watch", "run", "--instance", "play", "--config", str(path)]) == 2
    # and a Lab without a game folder is a setup problem, not a crash loop
    monkeypatch.setattr(config, "running_as_dev", lambda: False)
    args = ["watch", "run", "--instance", "lab", "--config", str(path)]
    assert cli.main(args) == runner.EXIT_SETUP
