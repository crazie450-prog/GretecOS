"""WO-2.5: when idle maintenance runs (plan 6.6, QA:A9) and the vault's commit batches (plan
6.5), and the core running both."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gregtec import config, locks, playhooks, prism, supervisor
from gregtec.agents.no_ai import AiMode
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.api.server import maintenance as core_maintenance
from gregtec.llm.summarise import SummariserOff
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec.vault import deploy, scheduler
from gregtec.vault.curator import Curator, JobResult

TEMPLATE = Path(__file__).resolve().parents[2] / "vault-template"
NOW = dt.datetime(2026, 11, 6, 15, 0)


def git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=vault, capture_output=True, text=True, check=True
    ).stdout


class Clock:
    def __init__(self) -> None:
        self.now = NOW
        self.mono = 1000.0

    def advance(self, seconds: float) -> None:
        self.now += dt.timedelta(seconds=seconds)
        self.mono += seconds


class FakeCurator(Curator):
    def __init__(self, vault: Path) -> None:
        super().__init__(vault, vault / "x.sqlite", vault / "b")
        self.days: list[dt.date] = []
        self.during: Callable[[], None] | None = None

    def run(  # type: ignore[override]
        self, today: dt.date, jobs: object = None, stop: object = None
    ) -> list[JobResult]:
        self.days.append(today)
        if self.during is not None:
            self.during()
        return [JobResult("index", "ok", "rebuilt"), JobResult("backup", "failed", "x")]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    db = Store(tmp_path / "gos.sqlite")
    yield db
    db.close()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    deploy.deploy(root, TEMPLATE)
    return root


def make(
    vault: Path,
    store: Store,
    clock: Clock,
    idle: list[float | None],
    game: list[bool | Exception],
) -> tuple[scheduler.Maintenance, FakeCurator]:
    cur = FakeCurator(vault)

    def running() -> bool:
        value = game[0]
        if isinstance(value, Exception):
            raise value
        return value

    m = scheduler.Maintenance(
        cur,
        store,
        idle_s=lambda: idle[0],
        game=running,
        idle_minutes=30,
        now=lambda: clock.now,
        monotonic=lambda: clock.mono,
    )
    return m, cur


# --- the idle rule -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("idle", "game", "why"),
    [
        (None, False, "input can't be read here"),
        (29 * 60, False, "input 29 min ago (waits for 30 min)"),
        (31 * 60, True, "the game is running"),
        (
            31 * 60,
            OSError("no PowerShell"),
            "can't tell whether the game runs (no PowerShell)",
        ),
        (30 * 60, False, None),
    ],
)
def test_the_idle_rule(
    vault: Path,
    store: Store,
    idle: float | None,
    game: bool | Exception,
    why: str | None,
) -> None:
    m, _ = make(vault, store, Clock(), [idle], [game])
    reason = m.waiting_for()
    assert reason == why if why is None else (reason is not None and why in reason)


def test_daily_maintenance_runs_once_a_day_when_idle(vault: Path, store: Store) -> None:
    clock, idle, game = Clock(), [60.0], [False]
    m, cur = make(vault, store, clock, idle, game)
    assert m.tick() is None and cur.days == []  # input a minute ago
    assert "input 1 min ago" in m.last_wait
    idle[0] = 31 * 60
    game[0] = True
    assert m.tick() is None and m.last_wait == "the game is running"
    game[0] = False
    results = m.tick()
    assert results is not None and cur.days == [NOW.date()]
    assert m.tick() is None and m.last_wait == "done for 2026-11-06"  # once a day
    clock.advance(9 * 3600)  # 00:00 the next day
    assert m.tick() is not None and cur.days == [
        NOW.date(),
        NOW.date() + dt.timedelta(1),
    ]
    record = m.last_run()
    assert record is not None and record["day"] == "2026-11-07"
    assert record["jobs"][1] == {"job": "backup", "status": "failed", "detail": "x"}
    assert m.health() == (
        "warn",
        "last run 2026-11-07; done for 2026-11-07; failed: backup",
    )


def test_the_last_day_survives_a_restart(vault: Path, store: Store) -> None:
    m, _ = make(vault, store, Clock(), [3600.0], [False])
    m.tick()
    again, cur2 = make(vault, store, Clock(), [3600.0], [False])
    assert again.tick() is None and cur2.days == []
    assert again.health()[0] == "warn"  # the stored run's failed backup


def test_health_before_any_run(vault: Path, store: Store) -> None:
    m, _ = make(vault, store, Clock(), [None], [False])
    assert m.health() == ("ok", "not run yet; not checked yet")


# --- commit batches ------------------------------------------------------------------------


def write_plan(vault: Path, n: int) -> None:
    (vault / "plans" / f"pl-{n}.md").write_text(
        f"---\nid: pl-{n}\nt: plan\nupd: 2026-11-06\n---\nst: draft\n", encoding="utf-8"
    )


def test_agent_writes_are_committed_every_10_minutes_and_at_session_end(
    vault: Path, store: Store
) -> None:
    clock = Clock()
    m, _ = make(vault, store, clock, [0.0], [False])
    m.tick()  # starts the clock
    write_plan(vault, 1)
    clock.advance(9 * 60)
    m.tick()
    assert "plans/pl-1.md" in git(vault, "status", "--porcelain")
    clock.advance(60)
    m.tick()
    assert git(vault, "status", "--porcelain") == ""
    assert git(vault, "log", "-1", "--format=%s").strip() == "GOS: agent writes"
    write_plan(vault, 2)
    assert m.session_end() is not None
    assert git(vault, "log", "-1", "--format=%s").strip() == "GOS: session end"
    assert m.session_end() is None  # nothing new


def test_a_failed_commit_is_logged_not_raised(
    tmp_path: Path, store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "plain"
    (root / ".git").mkdir(parents=True)  # not a real repo
    m, _ = make(root, store, Clock(), [0.0], [False])
    with caplog.at_level("WARNING", logger="gregtec.vault.scheduler"):
        assert m.session_end() is None
    assert any(r.getMessage() == "vault commit failed" for r in caplog.records)


# --- the game and input probes -------------------------------------------------------------


def test_game_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config.Config(gos_home=tmp_path)
    clients: list[str] = []
    monkeypatch.setattr(prism, "running_clients", lambda c: clients)
    assert scheduler.game_running(cfg) is False
    clients.append("lab")
    assert scheduler.game_running(cfg) is True
    clients.clear()
    with locks.hold(tmp_path, "export", "test"):
        assert scheduler.game_running(cfg) is True
    launching = tmp_path / playhooks.LAUNCHING
    launching.parent.mkdir(parents=True, exist_ok=True)
    launching.write_text(
        json.dumps({"role": "play", "time": time.time()}), encoding="utf-8"
    )
    assert scheduler.game_running(cfg) is True
    launching.unlink()

    def no_list(c: config.Config) -> list[str]:
        raise OSError("can't list")

    monkeypatch.setattr(prism, "running_clients", no_list)
    with pytest.raises(OSError):
        scheduler.game_running(cfg)


def test_seconds_since_input() -> None:
    got = scheduler.seconds_since_input()
    if sys.platform == "win32":
        assert got is None or got >= 0
    else:
        assert got is None


# --- the core ------------------------------------------------------------------------------


def test_the_core_builds_its_maintenance_from_gos_toml(
    tmp_path: Path, store: Store
) -> None:
    cfg = config.Config(gos_home=tmp_path, idle_minutes=45, keep_backups=3)
    m = core_maintenance(cfg, store, UsageMeter(store, 100.0, (80, 100)))
    assert m.idle_minutes == 45
    assert m.curator.vault == tmp_path / "vault"
    assert m.curator.db == tmp_path / "data" / "gos.sqlite"
    assert m.curator.backups == tmp_path / "data" / "backups"
    assert m.curator.keep_backups == 3


def test_the_cores_maintenance_asks_the_ai_mode_first(
    tmp_path: Path, store: Store
) -> None:
    ai = AiMode(store)
    ai.set_credit_pause(True)  # D48: paused at 100%
    cfg = config.Config(gos_home=tmp_path)
    m = core_maintenance(cfg, store, UsageMeter(store, 100.0, (80, 100)), ai)
    with pytest.raises(SummariserOff, match="paused for credits"):
        m.curator.summariser()  # before the keyring is read, so no Haiku call either


def test_on_credits_out_can_only_be_no_ai(tmp_path: Path) -> None:
    path = tmp_path / "gos.toml"
    path.write_text('[budget]\non_credits_out = "no_ai"\n', encoding="utf-8")
    assert config.load(path).service_errors == ()
    path.write_text('[budget]\non_credits_out = "stop"\n', encoding="utf-8")
    [problem] = config.load(path).service_errors
    assert problem.startswith('gos.toml: [budget] on_credits_out can only be "no_ai"')


def test_maintenance_keys(tmp_path: Path) -> None:
    path = tmp_path / "gos.toml"
    path.write_text(
        "[maintenance]\nidle_minutes = 20\nkeep_backups = 14\n", encoding="utf-8"
    )
    cfg = config.load(path)
    assert (cfg.idle_minutes, cfg.keep_backups, cfg.service_errors) == (20, 14, ())
    path.write_text("[maintenance]\nidle_minutes = 0\n", encoding="utf-8")
    cfg = config.load(path)
    assert cfg.idle_minutes == 30
    assert cfg.service_errors == (
        "gos.toml: [maintenance] idle_minutes must be a whole number from 1 to 1440",
    )


def test_the_core_shows_maintenance_and_commits_when_it_stops(
    tmp_path: Path, vault: Path, store: Store
) -> None:
    m, _ = make(vault, store, Clock(), [None], [False])
    core = Core(
        cfg=config.Config(gos_home=tmp_path),
        store=store,
        auth=Auth(store, "a" * 43),
        meter=UsageMeter(store, 100.0, (80, 100)),
        maintenance=m,
    )
    item = {i["id"]: i for i in core.health()["items"]}["maintenance"]
    assert (item["status"], item["label"]) == ("ok", "Maintenance")
    write_plan(vault, 3)
    with TestClient(create_app(core), base_url="http://127.0.0.1:8765"):
        pass  # start-up runs a first tick; shutdown is the session end
    assert git(vault, "log", "-1", "--format=%s").strip() == "GOS: session end"


# --- review fixes: a failed kv write, a stop mid-run, the stale warning --------------------


def test_a_failed_kv_write_doesnt_repeat_the_run(
    vault: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    m, cur = make(vault, store, Clock(), [3600.0], [False])
    real = m._set_kv

    def locked(key: str, value: str) -> None:  # a long gos db build holds the DB
        if key in (scheduler.KV_LAST_RUN, scheduler.KV_LAST_DAY):
            raise sqlite3.OperationalError("database is locked")
        real(key, value)

    monkeypatch.setattr(m, "_set_kv", locked)
    assert m.tick() is not None
    assert m._kv(scheduler.KV_LAST_DAY) is None
    assert m.tick() is None and m.tick() is None  # not again every minute
    assert len(cur.days) == 1


def test_a_stop_mid_run_isnt_recorded_and_ends_the_ticks(
    vault: Path, store: Store
) -> None:
    m, cur = make(vault, store, Clock(), [3600.0], [False])
    cur.during = m.stop  # the core stops while the jobs run
    assert m.tick() is not None
    assert m._kv(scheduler.KV_LAST_DAY) is None  # the next start runs the day again
    assert m.tick() is None and len(cur.days) == 1


def test_curator_run_gets_the_stop_check(vault: Path, store: Store) -> None:
    seen: list[object] = []

    class Recording(FakeCurator):
        def run(  # type: ignore[override]
            self, today: dt.date, jobs: object = None, stop: object = None
        ) -> list[JobResult]:
            seen.append(stop)
            return []

    m = scheduler.Maintenance(
        Recording(vault), store, idle_s=lambda: 3600.0, game=lambda: False
    )
    m.tick()
    [stop] = seen
    assert callable(stop) and stop() is False
    m.stop()
    assert stop() is True


def test_session_end_doesnt_wait_for_a_running_job(vault: Path, store: Store) -> None:
    m, _ = make(vault, store, Clock(), [0.0], [False])
    write_plan(vault, 4)
    with m._lock:  # a daily run holds it
        assert m.session_end(wait_s=0.05) is None
    assert m.session_end() is not None


def test_health_warns_when_maintenance_hasnt_run_for_days(
    vault: Path, store: Store
) -> None:
    clock = Clock()
    m, _ = make(vault, store, clock, [60.0], [False])
    m.tick()  # input a minute ago: waits; since = 2026-11-06
    clock.advance(3 * 86400)
    m.tick()
    assert m.health()[0] == "ok"
    clock.advance(86400)
    m.tick()
    status, detail = m.health()
    assert status == "warn"
    assert "no run for 4 days" in detail and "does the PC sleep sooner?" in detail
    m.idle_s = lambda: 3600.0
    m.tick()  # runs; the fake's backup failed, so it warns for that, not staleness
    assert "no run for" not in m.health()[1]


# --- D142 (g): the DB backup at session end once the daily run is 2 days late ----------------


def real_curator(vault: Path, tmp_path: Path) -> Curator:
    db = tmp_path / "data" / "gos.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE t(x)")
        conn.execute("INSERT INTO t VALUES (7)")
        conn.commit()
    finally:
        conn.close()
    return Curator(vault, db, tmp_path / "data" / "backups")


@pytest.mark.parametrize(
    ("last_day", "since", "backed_up"),
    [
        ("2026-11-04", None, True),  # yesterday and the day before missed
        ("2026-11-05", None, False),  # ran yesterday
        ("2026-11-06", None, False),  # ran today
        (None, "2026-11-04", True),  # never ran: counted from the first check
        (None, "2026-11-05", False),
        (None, None, False),  # nothing recorded yet
    ],
)
def test_session_end_backs_up_the_db_once_the_daily_run_is_2_days_late(
    vault: Path,
    store: Store,
    tmp_path: Path,
    last_day: str | None,
    since: str | None,
    backed_up: bool,
) -> None:
    clock = Clock()
    cur = real_curator(vault, tmp_path)
    m = scheduler.Maintenance(cur, store, now=lambda: clock.now)
    if last_day is not None:
        m._set_kv(scheduler.KV_LAST_DAY, last_day)
    if since is not None:
        m._set_kv(scheduler.KV_SINCE, since)
    write_plan(vault, 5)
    assert m.session_end() is not None  # the commit comes first, as before
    target = cur.backups / "gos-20261106.sqlite"
    assert target.exists() is backed_up
    if backed_up:  # a later session end the same day finds it and writes nothing
        stamp = target.stat().st_mtime_ns
        m.session_end()
        assert target.stat().st_mtime_ns == stamp
        assert [p.name for p in cur.backups.iterdir()] == ["gos-20261106.sqlite"]


def test_the_session_end_backup_needs_no_git_vault_and_never_raises(
    tmp_path: Path,
    store: Store,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    cur = real_curator(plain, tmp_path)
    m = scheduler.Maintenance(cur, store, now=lambda: NOW)
    m._set_kv(scheduler.KV_LAST_DAY, "2026-11-01")
    assert m.session_end() is None  # nothing to commit: no git
    assert (cur.backups / "gos-20261106.sqlite").exists()

    def full(*args: object) -> JobResult:
        raise OSError("disk full")

    monkeypatch.setattr(scheduler, "backup", full)
    with caplog.at_level("INFO", logger="gregtec.vault.scheduler"):
        assert m.session_end() is None
    [rec] = [r for r in caplog.records if r.getMessage() == "session-end backup"]
    assert rec.status == "failed" and "disk full" in rec.detail  # type: ignore[attr-defined]


def test_the_core_stop_gives_the_backup_a_deadline(
    vault: Path, store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m, _ = make(vault, store, Clock(), [None], [False])
    seen: list[float | None] = []
    monkeypatch.setattr(m, "_overdue_backup", seen.append)
    core = Core(
        cfg=config.Config(gos_home=tmp_path),
        store=store,
        auth=Auth(store, "a" * 43),
        meter=UsageMeter(store, 100.0, (80, 100)),
        maintenance=m,
    )
    # No stop request recorded: the deadline counts from the stop itself.
    with TestClient(create_app(core), base_url="http://127.0.0.1:8765"):
        before = time.monotonic()
    after = time.monotonic()
    [deadline] = seen
    assert deadline is not None
    assert (
        before + scheduler.STOP_DEADLINE_S
        <= deadline
        <= after + scheduler.STOP_DEADLINE_S
    )
    assert scheduler.STOP_DEADLINE_S < supervisor.STOP_TIMEOUT_S  # ends before the kill
    asked = time.monotonic() - 3.0  # uvicorn's graceful shutdown took 3 s of the 5
    core.stop_asked = asked
    with TestClient(create_app(core), base_url="http://127.0.0.1:8765"):
        pass
    assert seen[-1] == asked + scheduler.STOP_DEADLINE_S  # counted from the request
    assert m.session_end() is None and seen[-1] is None  # WO-2.4's: no limit


def test_the_stop_deadline_bounds_the_session_end_lock_wait(
    vault: Path, store: Store
) -> None:
    m, _ = make(vault, store, Clock(), [None], [False])
    with m._lock:
        start = time.monotonic()
        assert m.session_end(wait_s=2.0, deadline=start + 0.1) is None
        assert time.monotonic() - start < 1.5  # not the full 2 s
