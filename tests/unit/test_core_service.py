"""WO-2.1: gos.toml's core keys, the llm_usage meter, structured logs and the health strip data."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gregtec import applog, config, control, store
from gregtec.api.app import Core
from gregtec.api.security import Auth
from gregtec.llm import keys, prices
from gregtec.llm.prices import Usage
from gregtec.llm.usage import UnpricedModelError, UsageMeter, cycle_bounds

ADMIN = "x" * 43


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Detach the handlers applog.setup adds, so no test leaves a log file open."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "gos.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- config ------------------------------------------------------------------------------------


def test_core_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOS_HOME", str(tmp_path))
    cfg = config.load(write(tmp_path, ""))
    assert cfg.core_host == "127.0.0.1"
    assert (cfg.core_port, cfg.supervisor_port) == (8765, 8766)
    assert cfg.allowed_hosts == ("127.0.0.1:8765", "localhost:8765")
    assert cfg.gos_home == tmp_path
    assert cfg.run_dir == tmp_path / "run"
    assert cfg.core_at_login is True
    assert (cfg.monthly_included_usd, cfg.credit_alerts_pct, cfg.cycle_day) == (
        100.0,
        (80, 100),
        1,
    )
    assert cfg.launch_home is None


def test_core_keys_are_read(tmp_path: Path) -> None:
    cfg = config.load(
        write(
            tmp_path,
            f"""
[paths]
gos_home = "{tmp_path.as_posix()}/home"
prism_root = "D:/Prism"
[core]
host = "127.0.0.1"
port = 9000
supervisor_port = 9001
allowed_hosts = ["127.0.0.1:9000", "gaming-pc.tail1234.ts.net"]
[startup]
core_at_login = false
[budget]
monthly_included_usd = 150
credit_alerts_pct = [100, 50, 80]
cycle_day = 14
[launch]
home = "C:/Users/Clayton/GregtecOS"
""",
        )
    )
    assert cfg.gos_home == tmp_path / "home"
    assert cfg.prism_root == Path("D:/Prism")
    # Defaults first, no repeats: the port's loopback names are always allowed.
    assert cfg.allowed_hosts == (
        "127.0.0.1:9000",
        "localhost:9000",
        "gaming-pc.tail1234.ts.net",
    )
    assert cfg.core_at_login is False
    assert cfg.monthly_included_usd == 150.0
    assert cfg.credit_alerts_pct == (50, 80, 100)
    assert cfg.cycle_day == 14
    assert cfg.launch_home == Path("C:/Users/Clayton/GregtecOS")


@pytest.mark.parametrize(
    "text",
    [
        '[core]\nhost = "0.0.0.0"',  # never off loopback (plan 4.6)
        '[core]\nhost = "localhost"',
        '[core]\nhost = "192.168.1.5"',
        "[core]\nport = 80",
        "[core]\nport = 70000",
        '[core]\nport = "8765"',
        "[core]\nport = true",
        "[core]\nsupervisor_port = 8765",  # same as port
        '[core]\nallowed_hosts = ["<pc>.<tailnet>.ts.net"]',  # Appendix C's placeholder
        '[core]\nallowed_hosts = ["https://pc.ts.net"]',
        '[core]\nallowed_hosts = ["*.ts.net"]',
        '[core]\nallowed_hosts = ["PC.ts.net"]',
        '[core]\nallowed_hosts = "pc.ts.net"',
        "[startup]\ncore_at_login = 1",
        "[budget]\nmonthly_included_usd = 0",
        '[budget]\nmonthly_included_usd = "100"',
        "[budget]\ncredit_alerts_pct = []",
        "[budget]\ncredit_alerts_pct = [80.5]",
        "[budget]\ncycle_day = 29",  # every month has the day
        "[budget]\ncycle_day = 0",
        '[launch]\nhome = ""',
    ],
)
def test_bad_core_keys_are_refused(tmp_path: Path, text: str) -> None:
    with pytest.raises(config.ConfigError):
        config.load(write(tmp_path, text))


# --- prices and the usage meter ----------------------------------------------------------------


def test_every_plan_model_has_a_price() -> None:
    # Appendix C [models] and section 4.4: the meter refuses a model it can't price.
    for model in (
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-fable-5-1",
    ):
        assert model in prices.PRICES
    million = Usage(input=1_000_000, output=1_000_000)
    assert prices.estimate_cost("claude-opus-5-5", million) == pytest.approx(24.0)
    assert prices.estimate_cost("claude-fable-5-1", million) == pytest.approx(60.0)


@pytest.fixture
def db(tmp_path: Path) -> store.Store:
    s = store.Store(tmp_path / "data" / store.DB_NAME)
    yield s  # type: ignore[misc]
    s.close()


def test_meter_records_and_sums_a_cycle(db: store.Store) -> None:
    meter = UsageMeter(db, monthly_usd=100.0, alerts_pct=(80, 100), cycle_day=1)
    now = datetime(2026, 11, 15, 12, 0).astimezone()
    ts = now.timestamp()
    cost = meter.record(
        "fast", "claude-haiku-5-5", Usage(input=50_000), task="q1", ts=ts
    )
    assert cost == pytest.approx(0.005)
    assert (
        meter.record("deep", "claude-sonnet-5-5", Usage(output=100), usd=50.0, ts=ts)
        == 50.0
    )
    meter.record("deep", "claude-opus-5-5", Usage(input=1_000_000), ts=ts)
    # Outside this cycle: not counted.
    meter.record("fast", "claude-haiku-5-5", Usage(input=50_000), ts=ts - 40 * 86400)
    cycle = meter.cycle(now)
    assert cycle["spent_usd"] == pytest.approx(54.005)
    assert cycle["pct"] == pytest.approx(54.0, abs=0.01)
    assert cycle["crossed_pct"] == []
    assert cycle["calls"] == 3
    lanes = {row["lane"]: row for row in cycle["by_lane"]}
    assert lanes["deep"]["usd"] == pytest.approx(54.0) and lanes["deep"]["calls"] == 2
    assert lanes["fast"]["in_tok"] == 50_000
    assert next(row["model"] for row in cycle["by_model"]) == "claude-sonnet-5-5"
    assert cycle["cycle_start"].startswith("2026-11-01T00:00:00")
    assert cycle["cycle_end"].startswith("2026-12-01T00:00:00")
    meter.record("deep", "claude-sonnet-5-5", Usage(), usd=30.0, ts=ts)
    assert meter.cycle(now)["crossed_pct"] == [80]
    meter.record("deep", "claude-sonnet-5-5", Usage(), usd=20.0, ts=ts)
    assert meter.cycle(now)["crossed_pct"] == [80, 100]
    rows = sqlite3.connect(db.path).execute("SELECT task FROM llm_usage").fetchall()
    assert ("q1",) in rows


def test_meter_never_guesses_a_price(db: store.Store) -> None:
    meter = UsageMeter(db, 100.0, (80, 100))
    with pytest.raises(UnpricedModelError):
        meter.record("fast", "claude-unknown-9", Usage(input=10))
    assert (
        meter.record("deep", "claude-unknown-9", Usage(), usd=0.5) == 0.5
    )  # SDK's own cost
    with pytest.raises(ValueError):
        meter.record("", "claude-haiku-5-5", Usage())
    with pytest.raises(ValueError):
        meter.record("deep", "claude-haiku-5-5", Usage(), usd=-1.0)


@pytest.mark.parametrize(
    ("now", "day", "start", "end"),
    [
        (datetime(2026, 11, 6, 9), 1, datetime(2026, 11, 1), datetime(2026, 12, 1)),
        (datetime(2026, 11, 6, 9), 14, datetime(2026, 10, 14), datetime(2026, 11, 14)),
        (datetime(2026, 11, 14, 0), 14, datetime(2026, 11, 14), datetime(2026, 12, 14)),
        (datetime(2026, 12, 20), 14, datetime(2026, 12, 14), datetime(2027, 1, 14)),
        (datetime(2027, 1, 3), 14, datetime(2026, 12, 14), datetime(2027, 1, 14)),
    ],
)
def test_cycle_bounds(now: datetime, day: int, start: datetime, end: datetime) -> None:
    got_start, got_end = cycle_bounds(now.astimezone(), day)
    assert got_start.replace(tzinfo=None) == start
    assert got_end.replace(tzinfo=None) == end
    assert got_start <= now.astimezone() < got_end


def test_store_adds_token_column_to_an_existing_device_table(tmp_path: Path) -> None:
    path = tmp_path / "gos.sqlite"
    conn = sqlite3.connect(path)
    # Appendix D's device table, as WO-1.3's schema may create it first.
    conn.execute(
        "CREATE TABLE device(id TEXT PRIMARY KEY, name TEXT, paired INT, last_seen INT, "
        "revoked INT)"
    )
    conn.commit()
    conn.close()
    s = store.Store(path)
    cols = {r["name"] for r in s.rows("PRAGMA table_info(device)")}
    assert "token_sha256" in cols
    assert s.rows("PRAGMA journal_mode")[0][0] == "wal"
    s.close()
    store.Store(path).close()  # idempotent


def test_store_transaction_rolls_back(db: store.Store) -> None:
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("INSERT INTO kv(key, value, upd) VALUES ('a', 'b', 1)")
        raise RuntimeError
    assert db.rows("SELECT * FROM kv") == []


# --- structured logs ---------------------------------------------------------------------------


def test_json_log_lines(tmp_path: Path) -> None:
    path = applog.setup("testproc", tmp_path / "logs")
    log = logging.getLogger("gregtec.test")
    try:
        log.info("process exited", extra={"proc": "core", "code": 1})
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("failed")
        applog.setup("testproc", tmp_path / "logs")  # again: no duplicate handlers
        log.warning("once")
    finally:
        for handler in logging.getLogger().handlers:
            handler.flush()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["msg"] for line in lines] == ["process exited", "failed", "once"]
    first = lines[0]
    assert first["process"] == "testproc" and first["level"] == "info"
    assert first["logger"] == "gregtec.test"
    assert (first["proc"], first["code"]) == ("core", 1)
    assert first["ts"].endswith("+00:00")
    assert "ValueError: boom" in lines[1]["exc"]


# --- the health strip --------------------------------------------------------------------------


def make_core(tmp_path: Path, db: store.Store, token: str | None = None) -> Core:
    return Core(
        cfg=config.Config(gos_home=tmp_path),
        store=db,
        auth=Auth(db, ADMIN),
        meter=UsageMeter(db, 100.0, (80, 100)),
        supervisor_token=token,
    )


def items(health: dict[str, object]) -> dict[str, dict[str, object]]:
    return {i["id"]: i for i in health["items"]}  # type: ignore[union-attr,index]


def test_health_unsupervised(tmp_path: Path, db: store.Store) -> None:
    core = make_core(tmp_path, db)
    core.key_status = ("ok", "stored")
    health = core.health()
    by_id = items(health)
    assert by_id["supervisor"]["status"] == "off"
    assert by_id["core"]["status"] == "ok" and by_id["core"]["restarts"] == 0
    assert by_id["db"]["status"] == "ok"
    assert by_id["credits"]["status"] == "ok"
    assert by_id["credits"]["detail"] == "$0.00 of $100.00 this cycle (0%)"
    assert health["overall"] == "ok"
    for item in health["items"]:  # type: ignore[union-attr]
        assert {"id", "kind", "label", "status", "detail"} <= set(item)
        assert item["status"] in ("ok", "warn", "error", "off")


def test_health_shows_supervised_processes(tmp_path: Path, db: store.Store) -> None:
    core = make_core(tmp_path, db, token="t" * 43)
    core.key_status = ("ok", "stored")
    assert items(core.health())["supervisor"]["status"] == "error"  # no answer yet
    core.supervisor_status = {
        "service": "gos-supervisor",
        "pid": 10,
        "uptime_s": 99,
        "processes": [
            {
                "name": "core",
                "label": "Core",
                "state": "running",
                "detail": "running",
                "pid": 11,
                "uptime_s": 5,
                "restarts": 1,
                "last_exit": {"code": -9, "ts": 1, "ran_s": 3.0},
                "last_restart_ts": 2,
            }
        ],
    }
    health = core.health()
    by_id = items(health)
    assert by_id["supervisor"]["status"] == "ok"
    assert by_id["core"]["restarts"] == 1
    assert by_id["core"]["last_exit"] == {"code": -9, "ts": 1, "ran_s": 3.0}
    assert health["overall"] == "ok"
    core.supervisor_status["processes"][0]["state"] = "backoff"  # type: ignore[index]
    assert items(core.health())["core"]["status"] == "error"
    assert core.health()["overall"] == "error"


def test_health_credit_levels(tmp_path: Path, db: store.Store) -> None:
    core = make_core(tmp_path, db)
    core.key_status = ("ok", "stored")
    core.meter.record("deep", "claude-sonnet-5-5", Usage(), usd=85.0)
    assert items(core.health())["credits"]["status"] == "warn"
    core.meter.record("deep", "claude-sonnet-5-5", Usage(), usd=15.0)
    credits = items(core.health())["credits"]
    assert credits["status"] == "error" and credits["pct"] == 100.0


def test_health_key_check(
    tmp_path: Path, db: store.Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = make_core(tmp_path, db)
    monkeypatch.setattr(keys, "has_key", lambda name: name == keys.ANTHROPIC)
    core.refresh_key()
    assert core.key_status == ("ok", "stored")
    monkeypatch.setattr(keys, "has_key", lambda name: False)
    core.refresh_key()
    assert core.key_status[0] == "warn"
    assert items(core.health())["anthropic_key"]["status"] == "warn"


def test_supervisor_refresh_records_errors(tmp_path: Path, db: store.Store) -> None:
    core = make_core(tmp_path, db, token="t" * 43)
    core.cfg = config.Config(
        gos_home=tmp_path, supervisor_port=1
    )  # nothing listens there
    core.refresh_supervisor()
    assert core.supervisor_status is None
    assert core.supervisor_error is not None
    assert items(core.health())["supervisor"]["status"] == "error"


# --- run files ---------------------------------------------------------------------------------


def test_run_files(tmp_path: Path) -> None:
    path = tmp_path / "run" / "x.json"
    control.write_run_file(path, {"pid": 5, "port": 1})
    assert control.read_run_file(path) == {"pid": 5, "port": 1}
    assert list(path.parent.iterdir()) == [path]  # no temp file left
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0
    control.remove_run_file(path, pid=6)  # someone else's: kept
    assert path.exists()
    control.remove_run_file(path, pid=5)
    assert not path.exists()
    assert control.read_run_file(path) is None
    path.write_text("not json")
    assert control.read_run_file(path) is None
    assert control.running(path, "x") is None


def test_cycle_bounds_cross_dst() -> None:
    # Whatever the local zone, a cycle is whole local days from midnight to midnight.
    start, end = cycle_bounds(datetime(2026, 3, 20, 12).astimezone(), 1)
    assert (start.hour, end.hour) == (0, 0)
    assert end - start >= timedelta(days=30)
