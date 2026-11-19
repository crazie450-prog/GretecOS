"""The automated "fake day one" (WO-11.3, plan 8 row 11.3, plan 9 L1-L10, plan 10; D336, D337).

A real supervisor (:class:`gregtec.supervisor.Supervisor`, its monitor loop and control port)
runs the real core (``run_core.py``: :func:`gregtec.api.server.run` with the fake model), the
real game watchers (``python -m gregtec.watchers``, started when the Lab "launches", D259), the
real voice client (``run_voice.py``: fake microphone, keys and speech) and the real Discord bot
(``run_discord.py``: a fake gateway), against a fake Prism tree (Play, Export, Lab) and a GOS_HOME
whose DB is built from a real harness export (or, for the short smoke, the tool tests' made-up
export). :class:`Rehearsal` then drives one scripted session through every subsystem and records
a :class:`day_one_checklist.Result` per step; :func:`day_one_checklist.report` turns them into the plan-10 report.

Nothing outside the rehearsal's own folder is written; Play is the fake tree's ``GTNH-Play``,
hashed before and after (it must not change). No network beyond loopback, no key, no cost.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import rfakes
from day_one_checklist import GREEN, NOT_RUN, RED, Result

from gregtec import cli, config, control
from gregtec.config import Config
from gregtec.db import access
from gregtec.supervisor import ProcessSpec, Supervisor, python_exe
from gregtec.vault import curator

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FAKEGAME = REPO / "tests" / "unit" / "watchers" / "fakegame.py"
OC = REPO / "tests" / "fixtures" / "oc"
CLAYTON = 111_111_111_111_111_111
STRANGER = 222_222_222_222_222_222
TAILNET = "pc.tail1.ts.net"
STEEL = "gregtech:gt.metaitem.01@11305"
WORLD = "lab-world"
PC_LINES = ("Play", "Export", "Lab")
# What the children import (the core, watchers, voice client and bot), compiled before the start.
WARM = (
    "run_core",
    "run_voice",
    "run_discord",
    "gregtec.watchers.runner",
)


class StepFailed(AssertionError):
    pass


def check(ok: object, why: str) -> None:
    if not ok:
        raise StepFailed(why)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for[T](
    check_: Callable[[], T | None], timeout: float, what: str, step: float = 0.2
) -> T:
    deadline = time.monotonic() + timeout
    while True:
        value = check_()
        if value:
            return value
        if time.monotonic() > deadline:
            raise StepFailed(f"timed out after {timeout:.0f} s waiting for {what}")
        time.sleep(step)


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        h.update(str(path.relative_to(root)).encode())
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


def kill_pid(pid: int) -> None:
    """A crash: SIGKILL (TerminateProcess on Windows), nothing runs on the way out."""
    with contextlib.suppress(ProcessLookupError, OSError):
        os.kill(pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)


@dataclass
class Options:
    """``db``: ``"harness"`` (a real export, every step) or ``"fixture"`` (the made-up one: the
    steps that need real game data are left not run). ``soak_s``: how long everything runs with
    the fake game writing its files (time-compressed: a save every 0.5 s, not 45 s).
    ``suites``: other harness tests the report includes (run during the soak), by step name."""

    db: str = "fixture"
    soak_s: float = 30.0
    suites: dict[str, list[str]] = field(default_factory=dict)
    listener_events: Path | None = (
        None  # GregtecLink's machines.jsonl from the listener harness
    )
    only: frozenset[str] = frozenset()  # while developing: run just these steps


class Rehearsal:
    def __init__(
        self, root: Path, build_home: Callable[[Path], None], options: Options
    ) -> None:
        self.root = root
        self.opt = options
        self.results: dict[str, Result] = {}
        self.home = root / "GregtecOS"
        self.prism = root / "PrismLauncher"
        self.control = root / "control"
        self.keyring = root / "keyring.json"
        self.config_path = self.home / "config" / "gos.toml"
        self.build_home = build_home
        self.sup: Supervisor | None = None
        self._sup_thread: threading.Thread | None = None
        self.voice_box = rfakes.Mailbox(self.control, "voice")
        self.discord_box = rfakes.Mailbox(self.control, "discord")
        self.oc_token = ""
        self.oc_seq = 0
        self.oc_lock = threading.Lock()
        self.oc_low = False
        self.feeding = threading.Event()
        self.background: list[threading.Thread] = []
        self.suite_procs: dict[str, subprocess.Popen[bytes]] = {}
        self.play_hash = ""
        self.expiring: dict[str, Any] | None = None
        self.log: list[str] = []

    # --- running a step ------------------------------------------------------------------

    def step(self, name: str, fn: Callable[[], str | None], *, needs: str = "") -> None:
        if self.opt.only and name not in self.opt.only:
            self.results[name] = Result(NOT_RUN, "not in Options.only")
            return
        if needs == "harness" and self.opt.db != "harness":
            self.results[name] = Result(
                NOT_RUN, "needs a harness DB (the smoke runs on fixtures)"
            )
            return
        started = time.monotonic()
        try:
            detail = fn() or ""
            self.results[name] = Result(GREEN, detail)
        except Exception as exc:  # noqa: BLE001 - every failure is a red step, reported
            tb = traceback.format_exception_only(type(exc), exc)[-1].strip()
            self.results[name] = Result(RED, tb[:1200])
        self.log.append(
            f"{name}: {self.results[name].status} ({time.monotonic() - started:.1f} s) "
            f"{self.results[name].detail}"
        )

    # --- setup -----------------------------------------------------------------------------

    @property
    def cfg(self) -> Config:
        return config.load(self.config_path)

    def setup(self) -> None:
        import keyring

        self.root.mkdir(parents=True, exist_ok=True)
        self.control.mkdir(parents=True, exist_ok=True)
        fakes = _load(
            REPO / "tests" / "unit" / "setup" / "fakes.py", "gos_rehearsal_setup_fakes"
        )
        fakes.make_prism(self.prism)
        fg = _load(FAKEGAME, "gos_rehearsal_fakegame")
        self.fg = fg
        self.tree = fg.Tree(self.prism, WORLD)  # writes instances/GTNH-Lab/.minecraft
        self.tree.save_level(fg.level_dat())
        # The session's first lines, before the watchers start (a log is tailed from its end):
        # the login names the player, so his death lines read as deaths.
        self.tree.log(
            fg.stamp_line(
                f"{fg.PLAYER}[local:E:1a2b3c4d] logged in with entity id 7 at "
                "(100.5, 64.0, -200.5)",
                "Server thread",
            )
        )
        self.build_home(self.home)
        (self.home / "config").mkdir(parents=True, exist_ok=True)
        port, sport = free_port(), free_port()
        self.config_path.write_text(
            "\n".join(
                [
                    "[paths]",
                    f'gos_home = "{self.home.as_posix()}"',
                    f'prism_root = "{self.prism.as_posix()}"',
                    f'prism_instances = "{(self.prism / "instances").as_posix()}"',
                    f'gos_app = "{(self.root / "GregtecOS-app").as_posix()}"',
                    f'lab_world = "{WORLD}"',
                    "[core]",
                    f"port = {port}",
                    f"supervisor_port = {sport}",
                    'dashboard_host = "gos-rehearsal.localhost"',
                    f'allowed_hosts = ["127.0.0.1:{port}", "localhost:{port}", "{TAILNET}"]',
                    "[startup]",
                    "core_at_login = false",
                    "voice_with_play = true",
                    'watchers_with_play = "lab"',
                    "dashboard_window = false",
                    "[alerts]",
                    "power_low_pct = 20",
                    "power_low_s = 2",
                    "clear_s = 2",
                    "[notify]",
                    "cooldown_s = 60",
                    "[discord]",
                    f"owner_id = {CLAYTON}",
                    'dashboard_url = "https://pc.tail1.ts.net"',
                    "[setup]",
                    'completed_utc = "2026-11-11T00:00:00+00:00"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (self.root / "GregtecOS-app").mkdir(exist_ok=True)
        self.play_hash = tree_hash(self.prism / "instances" / "GTNH-Play")
        # The keyring: a stand-in Anthropic key (never sent anywhere: the lanes are fake).
        keyring.set_keyring(rfakes.FileKeyring(self.keyring))
        rfakes.write_atomic(self.keyring, json.dumps({}))
        from gregtec.llm import keys

        keys.set_key(keys.ANTHROPIC, "sk-ant-rehearsal-not-a-key")
        rfakes.write_atomic(self.control / "foreground", json.dumps("javaw.exe"))
        rfakes.write_atomic(self.control / "idle_s", json.dumps(0))
        rfakes.write_atomic(self.control / "running", json.dumps([]))

    def env(self) -> tuple[tuple[str, str], ...]:
        paths = [
            str(HERE),
            *[p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p],
        ]
        return (
            ("PYTHONPATH", os.pathsep.join(paths)),
            ("PYTHON_KEYRING_BACKEND", "rfakes.FileKeyring"),
            (rfakes.KEYRING_ENV, str(self.keyring)),
            (rfakes.CONTROL_ENV, str(self.control)),
            ("PYTHONUTF8", "1"),
        )

    def specs(self) -> list[ProcessSpec]:
        cfg, conf = self.cfg, str(self.config_path)
        env = self.env()

        def script(name: str) -> tuple[str, ...]:
            return (python_exe(), str(HERE / name), "--config", conf, "--supervised")

        return [
            ProcessSpec(
                "core",
                "Core",
                script("run_core.py"),
                health_port=cfg.core_port,
                health_service="gregtec-core",
                env=env,
            ),
            ProcessSpec(
                "discord",
                "Discord bot",
                script("run_discord.py"),
                env=env,
                stay_stopped=((2, "no core token"),),
            ),
            ProcessSpec(
                "watchers",
                "Game watchers",
                (
                    python_exe(),
                    "-m",
                    "gregtec.watchers",
                    "--supervised",
                    "--config",
                    conf,
                ),
                env=env,
                with_play=True,
                stay_stopped=((2, "can't watch this instance"),),
            ),
            ProcessSpec(
                "voice",
                "Voice",
                script("run_voice.py"),
                env=env,
                with_play=True,
                stay_stopped=((2, "no voice token"),),
            ),
        ]

    # --- the core's API --------------------------------------------------------------------

    def admin(self) -> str:
        data = control.read_run_file(self.cfg.run_dir / control.CORE_FILE)
        check(data is not None, "no core run file")
        assert data is not None
        return str(data["token"])

    def api(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        token: str | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        return control.call(
            self.cfg.core_port,
            method,
            path,
            token=token or self.admin(),
            body=body,
            timeout=timeout,
        )

    def ask(self, text: str, token: str | None = None) -> dict[str, Any]:
        return self.api("POST", "/api/ask", {"text": text}, token=token)

    def health(self) -> dict[str, Any] | None:
        try:
            return control.call(self.cfg.core_port, "GET", "/health", timeout=2.0)
        except control.ControlError:
            return None

    def status(self) -> dict[str, dict[str, Any]]:
        assert self.sup is not None
        return {p["name"]: p for p in self.sup.status()["processes"]}

    def running(self, name: str) -> dict[str, Any] | None:
        p = self.status().get(name)
        return p if p and p.get("state") == "running" else None

    # --- the supervisor ----------------------------------------------------------------------

    def start(self) -> None:
        cfg = self.cfg
        self.sup = Supervisor(
            cfg,
            self.specs(),
            clients=lambda c: [
                str(r) for r in rfakes.read_json(self.control / "running", [])
            ],
        )
        # A fresh venv has no .pyc yet: compile what the children import once, here, so the core's
        # first start on a busy machine isn't killed by the supervisor's 30 s start grace (seen
        # on the first run in a new worktree: three cores killed before /health answered).
        subprocess.run(
            [python_exe(), "-c", f"import {', '.join(WARM)}"],
            env={**os.environ, **dict(self.env())},
            timeout=600,
            check=True,
        )
        self.sup.bind()
        self._sup_thread = threading.Thread(target=self.sup.serve, daemon=True)
        self._sup_thread.start()
        self.sup.start("core")
        wait_for(self.health, 120, "the core's /health")
        for name in ("voice", "watchers", "discord", "oc-lab"):
            out = self.api("POST", f"/api/tokens/{name}")
            if name == "oc-lab":
                self.oc_token = out["token"]
            else:
                from gregtec.llm import keys

                keys.set_key(f"token-{name}", out["token"])
        self.sup.start("discord")

    def stop(self) -> None:
        self.feeding.clear()
        for proc in self.suite_procs.values():
            if proc.poll() is None:
                proc.kill()
        if self.sup is not None:
            self.sup.stop_all(detail="rehearsal over")
            self.sup.request_shutdown()
        if self._sup_thread is not None:
            self._sup_thread.join(15)

    # --- OC: the agent's telemetry every 5 s (M2) ----------------------------------------------

    def telemetry(self) -> dict[str, Any]:
        doc = json.loads((OC / "telemetry-first.json").read_text(encoding="utf-8"))
        with self.oc_lock:
            self.oc_seq += 1
            doc["seq"] = self.oc_seq
            low = self.oc_low
        # Every store at 80 % (the fixture's supercapacitor reads 12 %), or at 5 % for the alert
        # step: power_low (plan 7.5). The stores' sensor lines would override eu, so they go.
        for p in doc.get("power", []):
            if p.get("eu_max"):
                p["eu"] = p["eu_max"] // 20 if low else p["eu_max"] * 4 // 5
            if p.get("eu_max_str"):  # an LSC: past 2^53, so strings (the OC contract)
                big = int(p["eu_max_str"])
                p["eu_str"] = str(big // 20 if low else big * 4 // 5)
            p.pop("sensor", None)
        return doc

    def post_telemetry(self) -> dict[str, Any]:
        doc = self.telemetry()
        data = json.dumps(doc).encode()
        return control.call(
            self.cfg.core_port,
            "POST",
            "/oc/telemetry",
            token=self.oc_token,
            raw=("application/json", data),
            timeout=10,
        )

    def feed_oc(self) -> None:
        def run() -> None:
            while self.feeding.is_set():
                with contextlib.suppress(control.ControlError):
                    self.replies.append(self.post_telemetry())
                time.sleep(5.0)

        self.replies: list[dict[str, Any]] = []
        self.feeding.set()
        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.background.append(t)

    # --- the session ------------------------------------------------------------------------

    def run(self) -> dict[str, Result]:
        self.setup()
        try:
            self.start()
            self.session()
        except Exception as exc:  # noqa: BLE001 - setup failed: every step is red
            self.results.setdefault(
                "processes_up", Result(RED, f"setup: {exc!r}"[:600])
            )
        finally:
            self.step("shutdown", self.shutdown)
            self.step("play_untouched", self.play_untouched)
        return self.results

    def session(self) -> None:
        h = "harness"
        self.step("processes_up", self.processes_up)
        self.step("play_linked", self.play_linked)
        self.feed_oc()
        self.step("ask_recipe", self.ask_recipe)
        self.step("ask_quest", self.ask_quest)
        self.step("ask_deep", self.ask_deep)
        self.step("design_accept", self.design_accept, needs=h)
        self.step("design_pin_rank", self.design_pin_rank, needs=h)
        self.step("design_batch", self.design_batch, needs=h)
        self.step("design_export", self.design_export, needs=h)
        self.step("power_design", self.power_design, needs=h)
        self.step("voice_talk", self.voice_talk)
        self.step("voice_wake", self.voice_wake)
        self.step("persona_switch", self.persona_switch)
        self.step("game_events", self.game_events)
        self.step("my_base", self.my_base)
        self.step("oc_action", self.oc_action)
        self.step("alert_discord", self.alert_discord)
        self.step("discord_commands", self.discord_commands)
        self.step("phone", self.phone)
        self.step("crop_step", self.crop_step, needs=h)
        self.step("build_guide", self.build_guide)
        self.step("l9_key", self.l9_key)
        self.start_suites()
        self.step("soak", self.soak)
        self.step("oc_expiry", self.oc_expiry)
        for name in ("core", "watchers", "voice", "discord"):
            self.step(f"crash_{name}", lambda n=name: self.crash(n))
        self.step("journal", self.journal)
        self.step("costs", self.costs)
        self.step("backup_restore", self.backup_restore)
        self.finish_suites()

    # --- steps -------------------------------------------------------------------------------

    def processes_up(self) -> str:
        wait_for(
            lambda: self.running("core") and self.running("discord"),
            60,
            "core and bot running",
        )
        st = self.status()
        check(
            st["watchers"]["state"] == "stopped", "the watchers ran before Lab launched"
        )
        check(st["voice"]["state"] == "stopped", "voice ran before Lab launched")
        wait_for(
            lambda: any(r.get("started") for r in self.discord_box.results()),
            30,
            "the bot's start",
        )
        return (
            "core and Discord bot running; watchers and voice wait for Play/Lab (D259)"
        )

    def play_linked(self) -> str:
        assert self.sup is not None
        rfakes.write_atomic(self.control / "running", json.dumps(["lab"]))
        out = self.sup.play_event("launch", "lab")
        check(out.get("playing") == "lab", f"launch refused: {out}")
        wait_for(
            lambda: self.running("watchers") and self.running("voice"),
            60,
            "watchers and voice",
        )
        wait_for(
            lambda: any(r.get("started") for r in self.voice_box.results()),
            30,
            "voice start",
        )
        wait_for(
            lambda: (
                (self.api("GET", "/api/game/events").get("watchers") or {}).get(
                    "instance"
                )
                or self.api("GET", "/api/game/events").get("watchers")
            ),
            30,
            "the watchers' first heartbeat",
        )
        return "Lab launch started the watchers and voice under the supervisor"

    def item_name(self, key: str = STEEL) -> str:
        registry, _, meta = key.rpartition("@")
        conn = access.connect(access.db_path(self.home), readonly=True)
        try:
            row = conn.execute(
                "SELECT name_en FROM item WHERE registry_name = ? AND meta = ? AND nbt_hash = ''",
                (registry, int(meta)),
            ).fetchone()
            if row is None:  # the made-up export: any item a recipe makes
                row = conn.execute(
                    "SELECT i.name_en FROM item i JOIN recipe_output o ON o.item_id = i.id "
                    "WHERE i.name_en IS NOT NULL LIMIT 1"
                ).fetchone()
        finally:
            conn.close()
        check(row is not None, "no item with a recipe in the DB")
        return str(row[0])

    def ask_recipe(self) -> str:
        name = self.item_name()
        r = self.ask(f"how do I make {name}")
        check(
            r.get("lane") in ("fast", "instant"),
            f"lane {r.get('lane')}: {r.get('text')}",
        )
        check(r.get("refs"), f"no citations: {r}")
        check(
            any(str(x).startswith("r:") for x in r["refs"]), f"no r: ref in {r['refs']}"
        )
        return f"{name}: lane {r['lane']}, {len(r['refs'])} refs"

    def ask_quest(self) -> str:
        r = self.ask("what quests can I do next")
        check(
            r.get("lane") in ("fast", "instant"),
            f"lane {r.get('lane')}: {r.get('text')}",
        )
        check(r.get("text"), "no answer")
        check(r.get("intent") == "frontier", f"intent {r.get('intent')}: {r['text']}")
        check("no quests loaded" not in r["text"], f"no quest book: {r['text']}")
        return f"lane {r['lane']}, intent {r.get('intent')}: {r['text'][:80]!r}"

    def ask_deep(self) -> str:
        r = self.ask(f"design a {self.item_name()} line at 2 per second")
        check(r.get("lane") == "deep" and r.get("handoff"), f"not handed off: {r}")
        return f"handoff {r['handoff'].get('id')}"

    def designed(self, spec: dict[str, Any]) -> dict[str, Any]:
        out = self.api("POST", "/api/designer/design", spec, timeout=300)
        check(out.get("ids"), f"no routes: {str(out)[:300]}")
        return out

    def design_accept(self) -> str:
        out = self.designed({"target": STEEL, "rate": "1/s", "tier": "MV"})
        pid = out["ids"][0]
        done = self.api("POST", f"/api/plans/{pid}/accept", timeout=120)
        check(not done.get("problems"), f"accept problems: {done.get('problems')}")
        check(done.get("line"), f"no line note: {done}")
        line = self.home / "vault" / str(done["line"])
        check(
            line.is_file() and "st: accepted" in line.read_text("utf-8"),
            f"{line} not accepted",
        )
        plan_note = self.home / "vault" / "plans" / f"{pid}.md"
        check(plan_note.is_file(), "no plan note in the vault")
        ledger = self.api("GET", "/api/ledger")
        check(ledger.get("card"), "no ledger card")
        self.accepted = pid
        return (
            f"{pid} accepted: {done['line']}, ledger card {ledger['card'].get('type')}"
        )

    def design_pin_rank(self) -> str:
        out = self.designed({"target": STEEL, "rate": "1/s", "tier": "HV"})
        again = self.designed({"target": STEEL, "rate": "1/s", "tier": "HV"})
        views = [self.api("GET", f"/api/plans/{i}/designer") for i in out["ids"]]
        views2 = [self.api("GET", f"/api/plans/{i}/designer") for i in again["ids"]]

        def order(vs: list[dict[str, Any]]) -> list[Any]:
            return [json.dumps(v.get("rank"), sort_keys=True) for v in vs]

        check(order(views) == order(views2), "the same request ranked differently")
        for pid in out["ids"]:
            view = self.api("GET", f"/api/plans/{pid}/designer")
            for route in view.get("routes", []):
                for node in route.get("nodes", []):
                    if not node.get("item"):
                        continue
                    alts = self.api(
                        "GET",
                        f"/api/plans/{route['id']}/alternatives?node={_q(node['item'])}",
                    )
                    free = [
                        a for a in alts.get("alternatives", []) if not a.get("used")
                    ]
                    if not free:
                        continue
                    pinned = self.api(
                        "POST",
                        f"/api/plans/{route['id']}/pin",
                        {"node": node["item"], "recipe": free[0]["recipe"]},
                        timeout=300,
                    )
                    want = {"node": node["item"], "recipe": free[0]["recipe"]}
                    re = self.api(
                        "POST", f"/api/plans/{pinned['ids'][0]}/resolve", timeout=300
                    )
                    plan = self.api("GET", f"/api/plans/{re['ids'][0]}/designer")
                    pins = [
                        p for r in plan.get("routes", []) for p in r.get("pins") or []
                    ]
                    check(want in pins, f"pin lost on re-solve: {pins}")
                    return f"ranking stable over 2 runs; pin {free[0]['recipe']} kept"
        raise StepFailed("no node with another recipe to pin")

    def design_batch(self) -> str:
        out = self.designed(
            {"target": STEEL, "batch": "640", "within": "1h", "tier": "MV"}
        )
        return f"{len(out['ids'])} routes for 640 in 1 h"

    def design_export(self) -> str:
        pid = getattr(self, "accepted", None)
        check(pid, "no accepted plan")
        out = self.api("GET", f"/api/plans/{pid}/export/gtnhplanner")
        check(out, "empty export")
        return f"gtnhplanner export of {pid}: {len(json.dumps(out))} bytes"

    def power_design(self) -> str:
        out = self.api(
            "POST", "/api/power/design", {"mode": "best", "tier": "HV"}, timeout=300
        )
        check(out.get("ids"), f"no power options: {str(out)[:300]}")
        pid = out["ids"][0]
        done = self.api("POST", f"/api/power/{pid}/accept", timeout=120)
        check(not done.get("problems"), f"accept problems: {done.get('problems')}")
        check(done.get("ledger") is not None, "no ledger after accept")
        view = done.get("view") or {}
        # WO-4.5's storage and distribution checks on the ledger (buffers need a peak window)
        led = self.api("GET", "/api/power?window=600")
        dist = json.dumps(led.get("ledger"))
        check('"buffers"' in dist, f"no storage checks: {dist[:300]}")
        check("no storage data" not in dist, f"no storage data: {dist[:300]}")
        check(view, "no view")
        return f"{pid} accepted into the ledger"

    def said_after(self, t0: float, timeout: float = 60) -> str:
        def found() -> str | None:
            said = [r for r in self.voice_box.results() if "said" in r and r["t"] >= t0]
            return said[-1]["said"] if said else None

        return wait_for(found, timeout, "a spoken answer")

    def voice_talk(self) -> str:
        t0 = time.time()
        self.voice_box.send({"op": "talk", "text": f"how do I make {self.item_name()}"})
        said = self.said_after(t0)
        pushes = {
            r.get("push") for r in self.voice_box.results() if r.get("t", 0) >= t0
        }
        return f"said {said[:80]!r}; pushes {sorted(p for p in pushes if p)}"

    def voice_wake(self) -> str:
        t0 = time.time()
        self.voice_box.send({"op": "wake", "text": "what tier am I"})
        said = self.said_after(t0)
        check("no quests loaded" not in said, f"no quest book: {said}")
        return f"wake -> said {said[:80]!r}"

    def persona_switch(self) -> str:
        before = self.api("GET", "/api/persona")
        check(
            "gt-veteran" in json.dumps(before),
            f"gt-veteran isn't the default: {before}",
        )
        self.api("POST", "/api/persona", {"id": "ship-ai"})
        after = self.api("GET", "/api/persona")
        check(
            "ship-ai" in json.dumps(after.get("current", after)),
            f"didn't switch: {after}",
        )
        self.api("POST", "/api/persona", {"id": "gt-veteran"})
        return "gt-veteran -> ship-ai -> gt-veteran"

    def game_events(self) -> str:
        stamp = time.strftime("%H:%M:%S")
        # the login names the player, so his death line reads as a death (logparse)
        self.tree.log(
            f"[{stamp}] [Server thread/INFO]: Clayton[local:E:1a2b3c4d] logged in with "
            "entity id 7 at (100.5, 64.0, -200.5)\n"
        )
        time.sleep(1.5)  # one log poll, so the name is known before the death line
        self.tree.log(
            f"[{stamp}] [Client thread/INFO]: [CHAT] <Clayton> hello from the rehearsal\n"
        )
        self.tree.log(
            f"[{stamp}] [Client thread/INFO]: [CHAT] Clayton was slain by Zombie\n"
        )
        machines = self.tree.game / "gregteclink" / "events" / "machines.jsonl"
        machines.parent.mkdir(parents=True, exist_ok=True)
        src = self.opt.listener_events
        if src is not None and src.is_file():
            lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
            origin = "the listener harness's real events"
        else:
            lines = [
                json.dumps(
                    {
                        "v": 1,
                        "kind": "machine_built",
                        "boot": 1700000000000,
                        "seq": i,
                        "ts_ms": 1700000000500 + i,
                        "dim": 0,
                        "x": 10 + i,
                        "y": 64,
                        "z": -5,
                        "mte_id": 1000,
                        "registry": "gregtech:gt.blockmachines",
                        "meta": 1000,
                    }
                )
                + "\n"
                for i in (1, 2)
            ]
            origin = "contract-shaped events"
        with open(machines, "a", encoding="utf-8") as f:
            f.writelines(lines)
        built = sum('"machine_built"' in line for line in lines)

        def seen() -> dict[str, Any] | None:
            events = self.api("GET", "/api/game/events?limit=500")["events"]
            kinds = [e.get("kind") for e in events]
            ok = (
                "chat" in kinds
                and "death" in kinds
                and kinds.count("machine_built") >= min(built, 1)
            )
            return {"kinds": kinds} if ok else None

        got = wait_for(seen, 60, "chat, death and machine events at the core")
        # the pause and the resume (D258): the dashboard's game state follows within 5 s
        self.tree.log(f"[{stamp}] [Server thread/INFO]: Saving and pausing game...\n")
        wait_for(
            lambda: (self.api("GET", "/api/state").get("game") or {}).get("paused"),
            15,
            "paused",
        )
        time.sleep(1.1)  # the resume's line must be stamped after the pause's
        later = time.strftime("%H:%M:%S")
        self.tree.log(f"[{later}] [Server thread/INFO]: <Clayton> back from the menu\n")
        resumed = wait_for(
            lambda: (
                not (self.api("GET", "/api/state").get("game") or {}).get("paused")
                or None
            ),
            30,
            "resumed",
            step=0.5,
        )
        return f"{len(got['kinds'])} events ({origin}); paused and resumed: {resumed}"

    def my_base(self) -> str:
        """Only Play's watchers feed My base (D261): the Lab's listener events just replayed must
        not be counted; a machine Clayton adds by hand is, and the designer reads it."""
        before = self.api("GET", "/api/base")
        check(
            '"event"' not in json.dumps(before.get("machines", [])),
            f"Lab's events were counted as his base: {str(before)[:300]}",
        )
        conn = access.connect(access.db_path(self.home), readonly=True)
        try:
            row = conn.execute(
                "SELECT m.mte_id FROM machine m JOIN machine_map mm ON mm.machine_id = m.id "
                "ORDER BY m.mte_id LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        check(row is not None, "no machine in the DB")
        mte = int(row[0])
        self.api("POST", "/api/base/machines", {"mte_id": mte, "count": 2})
        after = self.api("GET", "/api/base")
        check(f'"mte_id": {mte}' in json.dumps(after), f"mte:{mte} not in My base")
        return f"Lab events not counted (D261); mte:{mte} x2 added by hand"

    def actions(self) -> dict[str, Any]:
        return self.api("GET", "/api/actions")

    def oc_action(self) -> str:
        wait_for(lambda: self.replies, 30, "the first OC reply")
        check(all(r.get("ok") for r in self.replies), f"OC refused: {self.replies[-1]}")
        self.ask("automate the steel line: craft 64 steel ingots")
        first = wait_for(
            lambda: self.actions().get("pending"), 30, "the craft proposal"
        )
        check(
            str(first.get("readback", "")).startswith("Craft 64 "),
            f"no read-back: {first}",
        )
        dlog = (self.control / "deep.jsonl").read_text("utf-8")
        check(
            "Say confirm." in dlog, f"the tool's read-back lacks 'Say confirm.': {dlog}"
        )
        # one pending: a newer proposal replaces it
        self.ask("set up a chat line: say hello in the base")
        second = wait_for(
            lambda: (
                (p := self.actions().get("pending")) and p["id"] != first["id"] and p
            ),
            30,
            "the second proposal",
        )
        old = self.api("GET", f"/api/actions/{first['id']}")
        check(
            old.get("state") == "cancelled",
            f"the first wasn't cancelled: {old.get('state')}",
        )
        # confirm by voice (D59) after the settle time
        time.sleep(3.5)
        t0 = time.time()
        self.voice_box.send({"op": "talk", "text": "confirm"})
        self.said_after(t0)
        confirmed = wait_for(
            lambda: (
                (a := self.api("GET", f"/api/actions/{second['id']}")).get("state")
                in ("confirmed", "sent", "done")
                and a
            ),
            30,
            "the confirm",
        )
        check(
            confirmed.get("confirmed_via") == "voice",
            f"via {confirmed.get('confirmed_via')}",
        )
        n = len(self.replies)

        def sent() -> list[Any] | None:
            cmds = [c for r in self.replies[n:] for c in r.get("cmds", [])]
            return cmds or None

        cmds = wait_for(sent, 30, "the command in an OC reply")
        check([c["id"] for c in cmds] == [second["id"]], f"sent: {cmds}")
        time.sleep(11)
        later = [c for r in self.replies[n:] for c in r.get("cmds", [])]
        check(len(later) == 1, f"sent more than once: {later}")
        # one more, never confirmed: it must expire at 60 s (checked after the soak)
        self.ask("set up the base for a chat line: say goodbye")
        self.expiring = wait_for(
            lambda: self.actions().get("pending"), 30, "a third proposal"
        )
        self.expiring_at = time.monotonic()
        return f"read back {second.get('readback')!r}; confirmed by voice; sent once"

    def oc_expiry(self) -> str:
        check(self.expiring, "no action to expire")
        assert self.expiring is not None
        wait = 62 - (time.monotonic() - self.expiring_at)
        if wait > 0:
            time.sleep(wait)
        a = wait_for(
            lambda: (
                (x := self.api("GET", f"/api/actions/{self.expiring['id']}")).get(
                    "state"
                )
                == "expired"
                and x
            ),  # type: ignore[index]
            15,
            "expired",
        )
        return f"action {a['id']} expired"

    def alert_discord(self) -> str:
        t0 = time.time()
        rfakes.write_atomic(self.control / "idle_s", json.dumps(3600))  # away
        with self.oc_lock:
            self.oc_low = True

        def dm() -> dict[str, Any] | None:
            dms = [
                r
                for r in self.discord_box.results()
                if r.get("dm")
                and r["t"] >= t0
                and "power" in r.get("text", "").casefold()
            ]
            return dms[0] if dms else None

        try:
            got = wait_for(dm, 90, "an alert DM")
        except StepFailed as exc:
            live = self.api("GET", "/api/base/live")
            alerts = [
                (b.get("alerts"), b.get("held")) for b in live.get("bindings", [])
            ]
            routed = self.api("GET", "/api/alerts/routed")
            raise StepFailed(
                f"{exc}; alerts: {json.dumps(alerts)[:400]}; routed: {json.dumps(routed)[:600]}"
            ) from None
        finally:
            with self.oc_lock:
                self.oc_low = False
            rfakes.write_atomic(self.control / "idle_s", json.dumps(0))
        return f"DM: {got['text'][:100]!r}"

    def discord(
        self,
        name: str,
        options: dict[str, Any],
        user: int = CLAYTON,
        timeout: float = 60,
    ) -> list[dict[str, Any]]:
        ref = f"{name}-{time.time_ns()}"
        self.discord_box.send(
            {
                "op": "command",
                "name": name,
                "options": options,
                "user": user,
                "ref": ref,
            }
        )
        if user != CLAYTON:
            time.sleep(3)
            return [r for r in self.discord_box.results() if r.get("ref") == ref]
        return wait_for(
            lambda: [
                r
                for r in self.discord_box.results()
                if r.get("ref") == ref and r.get("text")
            ],
            timeout,
            f"/{name}'s answer",
        )

    def discord_commands(self) -> str:
        got = self.discord("ask", {"text": f"how do I make {self.item_name()}"})
        check("r:" in got[-1]["text"] or got[-1]["text"], f"/ask: {got}")
        status = self.discord("status", {})
        none = self.discord("ask", {"text": "hello"}, user=STRANGER)
        check(none == [], f"answered a stranger: {none}")
        return f"/ask -> {got[-1]['text'][:60]!r}; /status -> {status[-1]['text'][:40]!r}; stranger ignored"

    def phone(self) -> str:
        import http.client

        def get(host: str) -> tuple[int, str]:
            conn = http.client.HTTPConnection(
                "127.0.0.1", self.cfg.core_port, timeout=10
            )
            try:
                conn.request("GET", "/", headers={"Host": host})
                r = conn.getresponse()
                return r.status, r.read().decode("utf-8", "replace")
            finally:
                conn.close()

        code, body = get(TAILNET)
        dist = (REPO / "dashboard" / "dist" / "index.html").is_file()
        if dist:
            check(code == 200 and "viewport" in body, f"tailnet GET /: {code}")
        code_bad, _ = get("evil.example.com")
        check(code_bad in (400, 403, 421), f"a foreign Host got {code_bad}")
        return (
            f"tailnet name: {code}{' (dashboard served)' if dist else ' (no dist built)'}; "
            f"foreign Host: {code_bad}"
        )

    def crop_step(self) -> str:
        crops = self.api("GET", "/api/crops")
        found = json.dumps(crops)
        check("crop:" in found or crops.get("crops"), f"no crops: {found[:200]}")
        ids = [c["crop"] for c in crops.get("crops", []) if c.get("crop")]
        check(ids, "no crop ids")
        self.api("POST", "/api/crops/target", {"crop": ids[0]})
        r = self.ask("next crop step")
        check(r.get("text"), f"no step: {r}")
        return f"target {ids[0]}: {r['text'][:80]!r}"

    def build_guide(self) -> str:
        guides = self.api("GET", "/api/build/guides")
        found = guides.get("guides") or guides.get("items") or []
        check(found, f"no guides: {str(guides)[:200]}")
        first = found[0]
        ctrl = first.get("controller") or first.get("key")
        g = self.api("GET", f"/api/build/guide?controller={_q(str(ctrl))}")
        check("layers" in json.dumps(g), f"no layers: {str(g)[:200]}")
        return f"{len(found)} guides; {ctrl} opens"

    def ai_online(self) -> bool:
        return bool((self.api("GET", "/api/ai").get("ai") or {}).get("online"))

    def l9_key(self) -> str:
        from gregtec.llm import keys

        check(self.ai_online(), "AI offline before the revoke")
        keys.delete_key(keys.ANTHROPIC)
        try:
            wait_for(lambda: not self.ai_online(), 30, "AI offline")
            r = self.ask(f"how do I make {self.item_name()}")
            check(r.get("refs"), f"no lookup answer offline: {r}")
            deep = self.ask("design a steel line at 1 per second")
            check(deep.get("lane") == "no_ai", f"deep lane offline: {deep.get('lane')}")
        finally:
            keys.set_key(keys.ANTHROPIC, "sk-ant-rehearsal-not-a-key")
        wait_for(self.ai_online, 30, "AI back online")
        return f"offline lookup lane {r.get('lane')}; deep refused offline; back online"

    def start_suites(self) -> None:
        for name, args in self.opt.suites.items():
            if self.opt.only and name not in self.opt.only:
                continue
            out = open(self.root / f"{name}.log", "wb")  # noqa: SIM115 - closed in finish_suites
            env = dict(os.environ)
            env.pop("PYTEST_XDIST_WORKER", None)
            self.suite_procs[name] = subprocess.Popen(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
                cwd=REPO,
                stdout=out,
                stderr=subprocess.STDOUT,
                env=env,
            )

    def finish_suites(self) -> None:
        for name, proc in self.suite_procs.items():

            def run(p: subprocess.Popen[bytes] = proc, n: str = name) -> str:
                code = p.wait(timeout=1800)
                tail = (
                    (self.root / f"{n}.log")
                    .read_text("utf-8", "replace")
                    .strip()
                    .splitlines()
                )
                check(code == 0, f"exit {code}: {' | '.join(tail[-3:])}")
                return tail[-1] if tail else ""

            self.step(name, run)

    def soak(self) -> str:
        """Everything on while the fake game writes the Lab's files (fakegame.py, its own
        process), a question every 30 s; then: no blocked write, every process up, no restart."""
        before = {n: p.get("restarts", 0) for n, p in self.status().items()}
        result = self.root / "fakegame.json"
        game = subprocess.Popen(
            [
                sys.executable,
                str(FAKEGAME),
                str(self.prism),
                str(self.opt.soak_s),
                str(result),
                "0.5",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        asked = 0
        end = time.monotonic() + self.opt.soak_s
        while time.monotonic() < end:
            self.ask(f"how do I make {self.item_name()}")
            asked += 1
            time.sleep(min(30.0, max(0.0, end - time.monotonic())))
        _, err = game.communicate(timeout=120)
        check(game.returncode == 0, f"fake game failed: {err[-300:]!r}")
        res = json.loads(result.read_text("utf-8"))
        check(not res.get("errors"), f"writes blocked: {res.get('errors')}")
        st = self.status()
        for name in ("core", "watchers", "voice", "discord"):
            check(st[name]["state"] == "running", f"{name}: {st[name]['state']}")
            check(
                st[name].get("restarts", 0) == before.get(name, 0), f"{name} restarted"
            )
        problems = (self.api("GET", "/api/game/events").get("watchers") or {}).get(
            "problems"
        )
        check(not problems, f"watcher problems: {problems}")
        return f"{self.opt.soak_s:.0f} s, {res.get('writes', '?')} game writes, {asked} questions"

    def crash(self, name: str) -> str:
        before = self.running(name)
        check(before and before.get("pid"), f"{name} isn't running")
        assert before is not None
        kill_pid(int(before["pid"]))
        after = wait_for(
            lambda: (p := self.running(name)) and p.get("pid") != before["pid"] and p,
            90,
            f"{name} restarted",
        )
        if name == "core":
            wait_for(self.health, 60, "the core back")
            r = self.ask(f"how do I make {self.item_name()}")
            check(r.get("refs"), "no answer after the restart")
        elif name == "voice":
            t0 = time.time()
            self.voice_box.send({"op": "talk", "text": "what tier am I"})
            self.said_after(t0)
        elif name == "discord":
            self.discord("status", {})
        elif name == "watchers":
            # Once it has polled: a line logged while the watchers are down after a log
            # rotation isn't read (they start a new file's tail at its end; finding F1, D337).
            time.sleep(3)
            stamp = time.strftime("%H:%M:%S")
            self.tree.log(
                f"[{stamp}] [Client thread/INFO]: [CHAT] <Clayton> after the crash\n"
            )

            def chat() -> list[dict[str, Any]] | None:
                events = self.api("GET", "/api/game/events?limit=500&kind=chat")[
                    "events"
                ]
                return (
                    events
                    if any("after the crash" in json.dumps(e) for e in events)
                    else None
                )

            events = wait_for(chat, 60, "the chat line after the restart")
            for text in ("after the crash", "hello from the rehearsal"):
                n = sum(text in json.dumps(e) for e in events)
                check(n <= 1, f"{text!r} {n} times in the feed")
        return f"pid {before['pid']} -> {after['pid']}"

    def journal(self) -> str:
        today = dt.date.today().isoformat()
        logs = list((self.home / "vault").glob(f"log/{today}*.md"))
        check(logs, "no journal note for today")
        return f"{logs[0].name}: {logs[0].stat().st_size} bytes"

    def costs(self) -> str:
        u = self.api("GET", "/api/usage")
        check("caps" in u, f"no caps: {u}")
        return "usage and caps served"

    def backup_restore(self) -> str:
        db = access.db_path(self.home)
        res = curator.backup(
            db,
            self.home / "data" / "backups",
            dt.date.today(),
            vault=self.home / "vault",
        )
        check(res.status == "ok", f"backup: {res}")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(
                ["backup", "restore", "--verify", "--config", str(self.config_path)]
            )
        check(code == 0, f"restore --verify: {out.getvalue()[-400:]}")
        return "backup made during play, restored and checked"

    def shutdown(self) -> str:
        self.stop()
        assert self.sup is not None
        st = self.status()
        check(all(p["state"] == "stopped" for p in st.values()), f"left running: {st}")
        check(self.health() is None, "the core still answers")
        return "every process stopped"

    def play_untouched(self) -> str:
        check(
            tree_hash(self.prism / "instances" / "GTNH-Play") == self.play_hash,
            "Play changed",
        )
        return "the fake Play instance is byte-identical"


def _q(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")


def _load(path: Path, name: str) -> Any:  # noqa: ANN401 - a module
    import importlib.util

    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fixture_home(home: Path) -> None:
    """The smoke's GOS_HOME: the dashboard e2e core's made-up data (the tool tests' export and
    quest book, crops, structures, guide pages), and the vault from the template."""
    sc = _load(REPO / "dashboard" / "e2e" / "serve_core.py", "gos_rehearsal_serve_core")
    home.mkdir(parents=True, exist_ok=True)
    sc.build_db(home)
    sc.load_crops(home)
    sc.load_structures(home)
    from gregtec.vault import deploy as vault_deploy

    vault_deploy.deploy(home / "vault", REPO / "vault-template")


def harness_home(
    game: Path, built_home: Callable[[Path, Path], Path]
) -> Callable[[Path], None]:
    """The full run's GOS_HOME: a real harness export (collect + db build), WO-1.8's structure
    fixture for the footprints, the quest eval's self-authored book (the harness has no quest
    book; its items link to the export by registry name + meta) and the vault from the
    template."""

    def build(home: Path) -> None:
        from gregtec.db import quests as quest_db
        from gregtec.db import structures as structure_db
        from gregtec.quests import questeval
        from gregtec.store import Store
        from gregtec.vault import deploy as vault_deploy

        home.mkdir(parents=True, exist_ok=True)
        built_home(game, home)
        book = quest_db.read(questeval.pack().write_world(home / "quest-world"), None)
        conn = access.connect(access.db_path(home))
        try:
            structure_db.load(conn, REPO / "tests" / "fixtures" / "gtnh-extractor")
            quest_db.write(conn, book)
        finally:
            conn.close()
        Store(access.db_path(home)).close()
        vault_deploy.deploy(home / "vault", REPO / "vault-template")

    return build


def main_report(results: dict[str, Result], path: Path | None, title: str) -> str:
    import day_one_checklist as checklist

    text = checklist.report(results, title=title)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.with_suffix(".json").write_text(
            json.dumps({k: [v.status, v.detail] for k, v in results.items()}, indent=1),
            encoding="utf-8",
        )
    return text
