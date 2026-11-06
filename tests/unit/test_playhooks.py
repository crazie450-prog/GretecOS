"""Play's hooks, the ask-on-quit export and the pipeline (WO-1.4) on fake Prism instances.

Prism and the game are fakes: ``launch`` writes an export run into Export's exports folder from a
thread and ``clients`` says whether that "Export" is running. The Linux end-to-end test at the
bottom runs the real ``gos hook`` commands with a fake ``prismlauncher`` and a fake Java process.
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from gregtec import config, locks, playhooks
from gregtec.ingest import fingerprint

REPO = Path(__file__).resolve().parents[2]
PLAY_FILES = {
    "mods/gregtech-5.09.54.265.jar": b"gt",
    "mods/tst-0.8.0.jar": b"tst",
    "mods/NotEnoughItems-2.8.171-GTNH.jar": b"nei 171",
    "config/GTNewHorizons/dreamcraft.cfg": b"S:ModPackVersion=2.9.0\n",
    "scripts/x.zs": b"// zs",
}


def instance(root: Path, name: str, files: dict[str, bytes]) -> Path:
    inst = root / "instances" / name
    (inst / ".minecraft").mkdir(parents=True)
    (inst / "instance.cfg").write_text(f"[General]\nname={name}\n")
    (inst / "mmc-pack.json").write_text("{}\n")
    for rel, data in files.items():
        path = inst / ".minecraft" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return inst


def manifest(run_id: str, complete: bool = True, **extra: object) -> dict[str, Any]:
    return {
        "format": "gregteclink-export/1",
        "run_id": run_id,
        "created_utc": "2026-11-06T12:00:00Z",
        "complete": complete,
        "mode": "autoexport",
        "side": "client",
        "mods": [
            {"modid": "gregtech", "version": "5.09.54.265", "file": "x", "sha256": None}
        ],
        "files": {"gt_recipes.jsonl": {"sha256": "a" * 64, "bytes": 1, "lines": 1}},
        "counts": {"recipes": 1},
        "parts": {"gt_dump": "ok", "nei": "ok", "autoexport": "ok"},
        **extra,
    }


def write_run(exports: Path, m: dict[str, Any]) -> None:
    partial = exports / f"{m['run_id']}.partial"
    partial.mkdir(parents=True)
    (partial / "manifest.json").write_text(json.dumps(m))
    partial.rename(exports / m["run_id"])


class Env:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.play = instance(tmp, "GTNH-Play", PLAY_FILES)
        self.export = instance(
            tmp, "GTNH-Export", {"mods/NotEnoughItems-2.8.172-GTNH.jar": b"nei 172"}
        )
        self.lab = instance(tmp, "GTNH-Lab", {})
        self.exports = self.export / ".minecraft" / "gregteclink" / "exports"
        self.home = tmp / "home"
        self.config_path = tmp / "gos.toml"
        self.config_path.write_text(
            "[paths]\n"
            f'prism_instances = "{(tmp / "instances").as_posix()}"\n'
            f'gos_home = "{self.home.as_posix()}"\n'
            f'gos_app = "{(tmp / "app").as_posix()}"\n'
            f'prism_root = "{(tmp / "prism").as_posix()}"\n'
        )
        self.cfg = config.load(self.config_path)
        self.running = False
        self.launched: list[list[str]] = []
        self.next_run: dict[str, Any] | None = manifest("20261106T120000Z")
        self.threads: list[threading.Thread] = []

    def clients(self, cfg: config.Config) -> list[str]:
        return ["export"] if self.running else []

    def launch(self, argv: Sequence[str]) -> None:
        self.launched.append(list(argv))

        def export() -> None:
            self.running = True
            time.sleep(0.1)
            if self.next_run is not None:
                write_run(self.exports, self.next_run)
            time.sleep(0.1)
            self.running = False

        t = threading.Thread(target=export)
        self.threads.append(t)
        t.start()

    def run_export(self, **kwargs: object) -> tuple[int, str]:
        out = io.StringIO()
        defaults: dict[str, Any] = {
            "launch": self.launch,
            "clients": self.clients,
            "poll_s": 0.02,
        }
        code = playhooks.run_export(self.cfg, out, **{**defaults, **kwargs})
        for t in self.threads:
            t.join()
        return code, out.getvalue()

    def pre_launch(self, role: str | None = "play") -> tuple[int, str]:
        out = io.StringIO()
        return playhooks.pre_launch(
            self.cfg, role, out, clients=self.clients
        ), out.getvalue()


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


# --- the pipeline ---------------------------------------------------------------------------


def test_export_records_the_fingerprint_and_clears_stale(env: Env) -> None:
    code, text = env.run_export()
    assert code == 0, text
    assert env.launched == [
        [str(env.tmp / "prism" / playhooks.prism.PRISM_EXE), "--launch", "GTNH-Export"]
    ]
    # synced first: Play's mods are in Export, Export's own NEI kept (sync exclude)
    mods = sorted(p.name for p in (env.export / ".minecraft/mods").iterdir())
    assert mods == [
        "NotEnoughItems-2.8.172-GTNH.jar",
        "gregtech-5.09.54.265.jar",
        "tst-0.8.0.jar",
    ]
    last = fingerprint.last_export(env.home)
    assert last is not None and last["run_id"] == "20261106T120000Z"
    assert last["fingerprint"] == fingerprint.compute_play(env.cfg).digest
    assert fingerprint.load_state(env.home)["stale"] is False
    assert "no earlier export is recorded: nothing to diff against" in text
    assert locks.held(env.home) is None
    code, text = env.pre_launch()
    assert code == 0 and "recipe data matches Play (export 20261106T120000Z" in text


def test_second_export_writes_the_diff(env: Env) -> None:
    assert env.run_export()[0] == 0
    (env.play / ".minecraft/mods/tst-0.8.0.jar").unlink()
    (env.play / ".minecraft/mods/tst-0.8.1.jar").write_bytes(b"tst new")
    m = manifest("20261107T120000Z")
    m["mods"].append({"modid": "tst", "version": "0.8.1", "file": "y", "sha256": None})
    env.next_run = m
    code, text = env.run_export()
    assert code == 0, text
    report = env.home / "data/reports/export-diff-20261106T120000Z-20261107T120000Z.txt"
    body = report.read_text()
    assert "Play: 1 added, 1 removed, 0 changed" in body
    assert "  + mods/tst-0.8.1.jar" in body and "  + tst 0.8.1 (y)" in body
    assert str(report) in text


def test_the_message_box_says_what_the_log_says(env: Env) -> None:
    told: list[str] = []
    (env.export / "mmc-pack.json").write_text('{"components": []}\n')
    env.next_run = manifest(
        "20261106T120000Z", part_warnings={"nei": "2 handlers failed to load"}
    )
    code, text = env.run_export(notify=lambda title, body: told.append(body))
    assert code == 0, text
    assert told[0].startswith("Export 20261106T120000Z done. recipe data matches Play")
    assert "First recorded export: nothing to diff against." in told[0]
    assert "warning: GTNH-Export's mmc-pack.json differs from Play's" in text
    assert "warning: nei left something out: 2 handlers failed to load" in text
    assert "Warnings:\n- GTNH-Export's mmc-pack.json differs from Play's" in told[0]
    assert "- nei left something out: 2 handlers failed to load" in told[0]


@pytest.mark.parametrize(
    ("parts", "errors", "said"),
    [
        (
            {"nei": "failed"},
            {"nei": "NEI wasn't ready"},
            "NEI's part is failed (NEI wasn't ready)",
        ),
        ({"nei": "absent"}, {}, "NEI's part is absent"),
        (
            {"autoexport": "failed"},
            {"autoexport": "deadline"},
            "autoexport failed (deadline)",
        ),
    ],
)
def test_a_run_without_nei_or_a_finished_autoexport_keeps_stale(
    env: Env, parts: dict[str, str], errors: dict[str, str], said: str
) -> None:
    """``complete`` covers only the GT parts: such a run is not recorded and Play stays stale."""
    assert env.run_export()[0] == 0  # a whole earlier export
    (env.play / ".minecraft/mods/tst-0.8.0.jar").write_bytes(b"tst rebuilt")
    m = manifest("20261107T120000Z", part_errors=errors)
    m["parts"].update(parts)
    env.next_run = m
    told: list[str] = []
    code, text = env.run_export(notify=lambda title, body: told.append(body))
    assert code == 1 and "not recording export 20261107T120000Z" in text
    assert (
        told[0].startswith("Export 20261107T120000Z is incomplete: ")
        and said in told[0]
    )
    assert "you'll be asked again" in told[0]
    last = fingerprint.last_export(env.home)
    assert last is not None and last["run_id"] == "20261106T120000Z"
    code, text = env.pre_launch()
    assert "recipe data is stale: Play changed since export 20261106T120000Z" in text
    # The diff against the earlier export is written all the same, with the parts.
    report = env.home / "data/reports/export-diff-20261106T120000Z-20261107T120000Z.txt"
    assert "Play: 0 added, 0 removed, 1 changed" in report.read_text()
    assert "Report:" in told[0] and "-> " + next(iter(parts.values())) in told[0]


def test_run_problems() -> None:
    ok = manifest("x")
    assert playhooks.run_problems(ok) == []
    server = {**ok, "side": "server", "parts": {"nei": "skipped_server"}}
    assert playhooks.run_problems(server) == []
    client = {**ok, "parts": {"nei": "skipped_server"}}
    assert playhooks.run_problems(client) == ["NEI's part is skipped_server"]
    assert playhooks.run_problems({"parts": "junk"}) == ["NEI's part is None"]


def test_export_waits_for_a_launch_to_start_its_client(env: Env) -> None:
    """Prism starts Play's Java a while after pre-launch passed: an export started meanwhile
    would run beside it, so it refuses until post-exit or LAUNCH_GRACE_S."""
    assert env.pre_launch()[0] == 0
    code, text = env.run_export()
    assert (
        code == 1 and "GTNH-Play was launched 0 s ago and may still be starting" in text
    )
    assert env.launched == []
    assert post_exit(env, Asker(False))[0] == 0  # Play quit: the mark goes
    assert playhooks.launching(env.home) is None
    assert env.run_export()[0] == 0
    # A launch that crashed (no post-exit) stops counting after LAUNCH_GRACE_S.
    env.pre_launch()
    record = json.loads((env.home / playhooks.LAUNCHING).read_text())
    record["time"] -= playhooks.LAUNCH_GRACE_S
    (env.home / playhooks.LAUNCHING).write_text(json.dumps(record))
    assert playhooks.launching(env.home) is None
    # A refused launch leaves no mark.
    with locks.hold(env.home, "export", "gos export run"):
        assert env.pre_launch()[0] == playhooks.EXIT_REFUSE
    assert not (env.home / playhooks.LAUNCHING).exists()


def test_spawned_export_runs_outside_plays_folder(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prism runs the hook in Play's game folder: the export must not have it on sys.path or
    as its working directory."""
    seen: list[tuple[list[str], dict[str, object]]] = []

    class Popen:
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            seen.append((argv, kwargs))

    monkeypatch.setattr(playhooks.subprocess, "Popen", Popen)
    started = playhooks.spawn_export(env.cfg, env.config_path)
    argv, kwargs = seen[0]
    assert argv[:4] == [sys.executable, "-P", "-m", "gregtec.cli"]
    assert kwargs["cwd"] == env.home and started.log.parent == env.home / "logs"


def test_export_refusals(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    with locks.hold(env.home, "export", "another export"):
        code, text = env.run_export()
    assert code == 1 and "another export" in text
    env.running = True  # Export (or any client) already running
    code, text = env.run_export(launch=lambda argv: None)
    assert code == 1 and "GTNH-Export is running" in text
    env.running = False
    monkeypatch.setattr(playhooks.getpass, "getuser", lambda: "gos-dev")
    code, text = env.run_export()
    assert code == 1 and "D75" in text
    assert env.launched == [] and fingerprint.last_export(env.home) is None


def test_export_that_quits_without_a_run(env: Env) -> None:
    env.next_run = None
    log = env.export / ".minecraft/logs/fml-client-latest.log"
    log.parent.mkdir(parents=True)
    log.write_text("[12:00:00] [ERROR] autoexport: no run: world not prepared\n")
    code, text = env.run_export()
    assert code == 1
    assert "Export quit without a complete run: world not prepared" in text
    assert fingerprint.last_export(env.home) is None
    assert locks.held(env.home) is None


def test_export_that_quits_after_an_incomplete_run(env: Env) -> None:
    env.next_run = manifest(
        "20261106T120000Z", complete=False, part_errors={"gt_dump": "boom"}
    )
    code, text = env.run_export()
    assert code == 1 and "incomplete run 20261106T120000Z" in text and "boom" in text


def test_an_unexpected_error_still_releases_and_tells(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(*args: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fingerprint, "record_export", full)
    told: list[str] = []
    code, text = env.run_export(notify=lambda title, body: told.append(body))
    assert code == 1 and "No space left on device" in text
    assert "instance lock released" in text and locks.held(env.home) is None
    assert told and told[0].startswith("Export failed: OSError")


def test_export_that_never_starts(env: Env) -> None:
    now = [0.0]

    def clock() -> float:
        now[0] += 60.0
        return now[0]

    code, text = env.run_export(
        launch=lambda argv: None, clock=clock, sleep=lambda s: None
    )
    assert code == 1 and "didn't start within 10 minutes" in text


def test_unlistable_processes_wait_a_grace_period(env: Env) -> None:
    calls = [0]

    def clients(cfg: config.Config) -> list[str]:
        calls[0] += 1
        if calls[0] == 1:
            return []  # before the launch
        raise OSError("no PowerShell")

    now = [0.0]

    def clock() -> float:
        now[0] += 30.0
        return now[0]

    def launch(argv: Sequence[str]) -> None:
        write_run(env.exports, manifest("20261106T120000Z"))

    code, text = env.run_export(
        clients=clients, launch=launch, clock=clock, sleep=lambda s: None
    )
    assert code == 0, text
    assert "can't list processes" not in text  # the run was already there


def test_never_listable_processes_wait_for_the_run_not_the_start_timeout(
    env: Env,
) -> None:
    """Without a process list there is no start timeout: a run at 20 minutes is fine."""
    calls = [0]

    def clients(cfg: config.Config) -> list[str]:
        calls[0] += 1
        if calls[0] == 1:
            return []  # the check before the launch
        raise OSError("no PowerShell")

    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(s: float) -> None:
        now[0] += 60.0
        if now[0] >= 20 * 60 and not (env.exports / "20261106T120000Z").exists():
            write_run(env.exports, manifest("20261106T120000Z"))

    code, text = env.run_export(
        clients=clients, launch=lambda argv: None, clock=clock, sleep=sleep
    )
    assert code == 0, text
    assert text.count("can't list processes") == 1


def test_a_run_found_near_the_deadline_is_kept(env: Env) -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(s: float) -> None:
        now[0] += 60.0
        # Export quits 2 minutes after the deadline.
        env.running = now[0] < playhooks.DEADLINE_S + 120
        if (
            now[0] >= playhooks.DEADLINE_S - 60
            and not (env.exports / "20261106T120000Z").exists()
        ):
            write_run(env.exports, manifest("20261106T120000Z"))

    code, text = env.run_export(launch=lambda argv: None, clock=clock, sleep=sleep)
    assert code == 0, text


def test_old_runs_are_not_taken_for_the_new_one(env: Env) -> None:
    write_run(env.exports, manifest("20261101T000000Z"))
    env.next_run = None
    code, text = env.run_export()
    assert code == 1 and "without a complete run" in text


# --- pre-launch -----------------------------------------------------------------------------


def test_pre_launch_refuses_while_the_lock_is_held(env: Env) -> None:
    with locks.hold(env.home, "export", "gos export run"):
        code, text = env.pre_launch()
        assert code == playhooks.EXIT_REFUSE
        assert "not launching: the instance lock is held by export" in text
        code, _ = env.pre_launch("lab")  # no client starts while Export runs
        assert code == playhooks.EXIT_REFUSE
        code, text = env.pre_launch("export")  # Export itself is what holds it
        assert code == 0
    assert env.pre_launch()[0] == 0


def test_pre_launch_refuses_while_export_runs(env: Env) -> None:
    env.running = True
    code, text = env.pre_launch()
    assert code == playhooks.EXIT_REFUSE and "GTNH-Export is running" in text


def test_pre_launch_reports_stale_and_degrades(env: Env) -> None:
    code, text = env.pre_launch()
    assert code == 0 and "recipe data is stale: no export is recorded yet" in text
    assert fingerprint.load_state(env.home)["play"]["by"] == "pre-launch"

    def broken(cfg: config.Config) -> list[str]:
        raise OSError("no PowerShell")

    out = io.StringIO()
    assert playhooks.pre_launch(env.cfg, "play", out, clients=broken) == 0
    assert "can't tell whether Export is running" in out.getvalue()
    # Play can't be fingerprinted: it still launches.
    (env.play / ".minecraft").rename(env.play / "gone")
    code, text = env.pre_launch()
    assert code == 0 and "couldn't check whether recipe data is stale" in text
    code, text = env.pre_launch(None)
    assert code == 0 and "not Play" in text


# --- post-exit -------------------------------------------------------------------------------


class Asker:
    def __init__(self, answer: bool | None) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def __call__(self, title: str, question: str, timeout_s: int) -> bool | None:
        assert timeout_s == 300
        self.asked.append(question)
        return self.answer


def post_exit(
    env: Env,
    ask: Asker,
    spawn: Callable[[], playhooks.Started] | None = None,
    role: str = "play",
) -> tuple[int, str]:
    out = io.StringIO()
    spawned: list[str] = []

    def no_spawn() -> playhooks.Started:
        spawned.append("x")
        raise AssertionError("must not start an export")

    code = playhooks.post_exit(
        env.cfg, role, out, ask=ask, spawn=spawn or no_spawn, wait_s=5
    )
    return code, out.getvalue()


def test_unchanged_quit_runs_nothing(env: Env) -> None:
    assert env.run_export()[0] == 0
    ask = Asker(True)
    code, text = post_exit(env, ask)
    assert (
        code == 0
        and "Play unchanged since export 20261106T120000Z: nothing to do" in text
    )
    assert ask.asked == []


@pytest.mark.parametrize(
    ("answer", "said"), [(False, "not exporting (No)"), (None, "no answer")]
)
def test_no_or_no_answer_exports_nothing(
    env: Env, answer: bool | None, said: str
) -> None:
    ask = Asker(answer)
    code, text = post_exit(env, ask)
    assert code == 0 and said in text
    assert "No export is recorded yet." in ask.asked[0]
    assert fingerprint.load_state(env.home)["stale"] is True


def test_never_policy_and_running_export_dont_ask(env: Env) -> None:
    ask = Asker(True)
    env.config_path.write_text(
        env.config_path.read_text() + '[policy]\nexport_on_quit = "never"\n'
    )
    env.cfg = config.load(env.config_path)
    code, text = post_exit(env, ask)
    assert code == 0 and "never" in text and ask.asked == []
    env.cfg = dataclasses.replace(env.cfg, export_on_quit="ask")
    with locks.hold(env.home, "export", "gos export run"):
        code, text = post_exit(env, ask)
    assert code == 0 and "already running" in text and ask.asked == []
    code, text = post_exit(env, ask, role="lab")
    assert code == 0 and "not Play" in text and ask.asked == []


def test_yes_starts_the_export_and_waits_for_its_lock(env: Env) -> None:
    (env.home).mkdir(parents=True)
    ready = threading.Event()
    stop = threading.Event()

    def export_process() -> None:
        with locks.InstanceLock(env.home).acquire(
            "export", "gos export run", ticket="t1"
        ):
            ready.set()
            stop.wait(10)

    def spawn() -> playhooks.Started:
        threading.Thread(target=export_process).start()
        return playhooks.Started("t1", env.home / "logs/export.log")

    ask = Asker(True)
    try:
        code, text = post_exit(env, ask, spawn)
        assert ready.is_set()
        assert code == 0 and "export started" in text
        # Play can't start meanwhile.
        assert env.pre_launch()[0] == playhooks.EXIT_REFUSE
    finally:
        stop.set()


def test_yes_but_the_export_dies(env: Env) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "raise SystemExit(5)"])
    proc.wait()

    def spawn() -> playhooks.Started:
        return playhooks.Started("t2", env.home / "logs/export.log", proc)

    code, text = post_exit(env, Asker(True), spawn)
    assert code == 1 and "stopped at once (exit 5)" in text


def test_role_of(env: Env) -> None:
    assert playhooks.role_of(env.cfg, str(env.play)) == "play"
    assert playhooks.role_of(env.cfg, str(env.export) + os.sep) == "export"
    assert playhooks.role_of(env.cfg, str(env.tmp)) is None
    assert playhooks.role_of(env.cfg, None) is None


def test_acceptance_flow(env: Env) -> None:
    """Plan 8, WO-1.4: a changed mod jar -> stale -> prompt on quit -> yes -> export -> diff;
    after the export Play's next pre-launch is not stale; Play won't launch while Export holds
    the lock; an unchanged quit runs nothing."""
    assert env.run_export()[0] == 0  # an earlier export
    (env.play / ".minecraft/mods/tst-0.8.0.jar").write_bytes(b"tst rebuilt")
    code, text = env.pre_launch()
    assert code == 0 and "recipe data is stale: Play changed since export" in text
    assert "0 added, 0 removed, 1 changed" in text

    env.next_run = manifest("20261107T120000Z")
    results: list[tuple[int, str]] = []
    refused: list[int] = []

    def spawn() -> playhooks.Started:
        def export_process() -> None:
            def launch(argv: Sequence[str]) -> None:
                refused.append(env.pre_launch()[0])  # Play launched during the export
                env.launch(argv)

            results.append(env.run_export(launch=launch, ticket="t3"))

        threading.Thread(target=export_process).start()
        return playhooks.Started("t3", env.home / "logs/export.log")

    ask = Asker(True)
    code, text = post_exit(env, ask, spawn)
    assert code == 0 and "1 changed" in ask.asked[0]
    deadline = time.monotonic() + 10
    while not results and time.monotonic() < deadline:
        time.sleep(0.05)
    assert results and results[0][0] == 0, results
    assert refused == [playhooks.EXIT_REFUSE]
    assert (
        env.home / "data/reports/export-diff-20261106T120000Z-20261107T120000Z.txt"
    ).is_file()

    code, text = env.pre_launch()
    assert code == 0 and "recipe data matches Play (export 20261107T120000Z" in text
    ask = Asker(True)
    code, text = post_exit(env, ask)
    assert code == 0 and "nothing to do" in text and ask.asked == []


# --- end to end on Linux: the real commands, a fake Prism and a fake Java ------------------------

FAKE_PRISM = """#!{python}
import os, subprocess, sys
# A fake prismlauncher: --launch GTNH-Export starts a fake Java for Export and returns at once.
if sys.argv[1:3] == ["--launch", "GTNH-Export"]:
    subprocess.Popen(
        [{java!r}, {fake_java!r}, "-Djava.library.path=" + {natives!r}],
        start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
"""
FAKE_JAVA = """
import json, sys, time
from pathlib import Path
exports = Path({exports!r})
exports.mkdir(parents=True, exist_ok=True)
run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
while (exports / run_id).exists():
    run_id += "-2"
partial = exports / (run_id + ".partial")
partial.mkdir()
m = {manifest!r}
m["run_id"] = run_id
(partial / "manifest.json").write_text(json.dumps(m))
time.sleep(0.5)
partial.rename(exports / run_id)
time.sleep(0.5)
"""


@pytest.mark.skipif(
    sys.platform == "win32", reason="fake Prism and Java are POSIX scripts"
)
def test_end_to_end_with_the_real_commands(env: Env) -> None:
    natives = env.export / "natives"
    java = env.tmp / "bin" / "java"
    java.parent.mkdir()
    java.symlink_to(sys.executable)
    fake_java = env.tmp / "fake_java.py"
    fake_java.write_text(
        FAKE_JAVA.format(exports=str(env.exports), manifest=manifest("x"))
    )
    prism_exe = env.tmp / "prism" / "prismlauncher"
    prism_exe.parent.mkdir()
    prism_exe.write_text(
        FAKE_PRISM.format(
            python=sys.executable,
            java=str(java),
            fake_java=str(fake_java),
            natives=str(natives),
        )
    )
    prism_exe.chmod(0o755)
    base_env = {**os.environ, "GOS_HOME": str(env.home), "INST_DIR": str(env.play)}
    # Prism runs the hooks in Play's game folder: a module there must not shadow one of GOS's.
    planted = env.tmp / "planted-ran"
    game = env.play / ".minecraft"
    (game / "secrets.py").write_text(f"open({str(planted)!r}, 'w').close()\n")

    def gos(*args: str) -> tuple[int, str]:
        done = subprocess.run(
            [
                sys.executable,
                "-P",  # as gos.exe, which doesn't put its working directory on sys.path
                "-m",
                "gregtec.cli",
                *args,
                "--config",
                str(env.config_path),
            ],
            cwd=game,
            env=base_env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return done.returncode, done.stdout + done.stderr

    def wait_for_the_export() -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if locks.held(env.home) is None:
                return
            time.sleep(0.2)
        raise AssertionError("the export didn't finish")

    code, text = gos("hook", "pre-launch")
    assert code == 0 and "no export is recorded yet" in text, text
    code, text = gos("hook", "post-exit", "--answer", "yes")
    assert code == 0 and "export started" in text, text
    code, text = gos("hook", "pre-launch")
    assert code == playhooks.EXIT_REFUSE and "instance lock is held" in text, text
    wait_for_the_export()
    logs = sorted((env.home / "logs").glob("export-*.log"))
    assert len(logs) == 1
    log = logs[0].read_text()
    assert "recorded export" in log and "instance lock released" in log, log
    code, text = gos("hook", "pre-launch")
    assert code == 0 and "recipe data matches Play" in text, text

    (env.play / ".minecraft/mods/tst-0.8.0.jar").write_bytes(b"tst rebuilt")
    code, text = gos("hook", "pre-launch")
    assert code == 0 and "0 added, 0 removed, 1 changed" in text, text
    time.sleep(1.1)  # a new run id (the fake names runs by the second)
    code, text = gos("hook", "post-exit", "--answer", "yes")
    assert code == 0 and "export started" in text, text
    wait_for_the_export()
    reports = list((env.home / "data/reports").glob("export-diff-*.txt"))
    assert len(reports) == 1
    assert "Play: 0 added, 0 removed, 1 changed" in reports[0].read_text()
    code, text = gos("hook", "pre-launch")
    assert code == 0 and "recipe data matches Play" in text, text

    code, text = gos("hook", "post-exit", "--answer", "yes")
    assert code == 0 and "nothing to do" in text, text
    assert len(list((env.home / "logs").glob("export-*.log"))) == 2  # no third export
    code, text = gos("export", "diff")
    assert code == 0 and "Play: 0 added, 0 removed, 1 changed" in text, text
    assert not planted.exists()  # neither the hooks nor the exports they started ran it
