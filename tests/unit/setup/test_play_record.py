"""Play safety at the root (final check round 4, D223): one gate in front of every file of GOS's
own that the wizard and ``gos doctor`` touch, and a record of every folder that has been Play.

The routes the round-4 attacker reproduced are here with the outcome the right way round: a
GOS_HOME whose data folder is a link into Play is refused and nothing is made in Play, and a hand
edit of ``[paths] play`` (with or without a restart, or after deleting gos.toml) never lets the
old Play be saved or finished as Lab.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient

from gregtec import config, doctor, sync
from gregtec.config import Config
from gregtec.packupdate import gtpin
from gregtec.setup import tomledit
from gregtec.setup.devices import FakeDevices
from gregtec.setup.service import SetupError, SetupService, record_plays

from .conftest import path_values
from .fakes import MemoryKeyring, link_folder, make_instance
from .test_routes import admin, client, core  # noqa: F401 - the route fixtures


def snapshot(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*")}


def wal_db(db: Path) -> None:
    """A WAL-mode DB with an export_run table, as GOS leaves gos.sqlite."""
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE export_run (run_key TEXT, gt_version TEXT, created_at TEXT)"
    )
    conn.commit()
    conn.close()


def home_with_data_in_play(prism: Path, tmp_path: Path) -> tuple[Path, Path]:
    """(Play, a GOS_HOME outside Play whose data folder is a link into Play with a WAL DB)."""
    play = prism / "instances" / "GTNH-Play"
    wal_db(play / "gosdata" / "gos.sqlite")
    other = tmp_path / "OtherHome"
    other.mkdir()
    link_folder(other / "data", play / "gosdata")
    return play, other


def hand_edit(path: Path, old: str, new: str) -> None:
    text = path.read_text("utf-8")
    assert old in text
    path.write_text(text.replace(old, new), "utf-8")


def hand_set(path: Path, key: str, value: str) -> None:
    """Clayton's hand edit of one ``[paths]`` key (any OS's path spelling)."""
    text = path.read_text("utf-8")
    new, n = re.subn(
        rf"^{key} = .*$",
        # A function, so re never reads a Windows path's backslashes as escapes.
        lambda _: f"{key} = {tomledit.format_value(value)}",
        text,
        flags=re.MULTILINE,
    )
    assert n == 1
    path.write_text(new, "utf-8")


# --- (1) one gate in front of every open


def test_a_home_whose_data_links_into_play_is_refused_and_untouched(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    play, other = home_with_data_in_play(prism, tmp_path)
    (tmp_path / "app").mkdir()
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    before = snapshot(play)
    outcome, _ = svc.check("paths", {"gos_home": str(other)})
    assert outcome.blocked
    assert "data folder overlaps Play" in outcome.problems["gos_home"]
    rows = {c.id: c for c in outcome.checks}
    assert rows["paths.gos_home"].status == "error"
    assert rows["db.gt_pin"].detail.startswith("not read")
    with pytest.raises(SetupError):
        svc.save("paths", {"gos_home": str(other)})
    assert snapshot(play) == before  # no -wal, no -shm, no probe file


def test_a_hand_edited_home_linking_into_play_never_finishes(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    play, other = home_with_data_in_play(prism, tmp_path)
    (tmp_path / "app").mkdir()
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    hand_set(svc.path, "gos_home", str(other))
    assert (
        config.load(svc.path).gos_home == other
    )  # the hand edit reads back on every OS
    before = snapshot(play)
    with pytest.raises(SetupError) as refused:
        svc.finish()
    rows = {c["id"]: c for c in refused.value.body["checks"]}
    assert rows["paths.gos_home"]["status"] == "error"
    assert snapshot(play) == before


def test_the_route_check_makes_nothing_in_play(
    client: TestClient,  # noqa: F811
    prism: Path,
    home: Path,
    tmp_path: Path,
) -> None:
    play, other = home_with_data_in_play(prism, tmp_path)
    r = client.post(
        "/api/setup/steps/paths",
        json={"values": path_values(prism, home, tmp_path / "app")},
        headers=admin(),
    )
    assert r.status_code == 200, r.text
    before = snapshot(play)
    r = client.post(
        "/api/setup/steps/paths/check",
        json={"values": {"gos_home": str(other)}},
        headers=admin(),
    )
    assert r.json()["blocked"] and "gos_home" in r.json()["problems"]
    assert snapshot(play) == before


@pytest.mark.parametrize("sub", ["logs", "vault", "backups", "run"])
def test_every_home_folder_is_checked(prism: Path, tmp_path: Path, sub: str) -> None:
    other = tmp_path / "OtherHome"
    other.mkdir()
    link_folder(other / sub, prism / "instances" / "GTNH-Lab")
    cfg = Config(gos_home=other, prism_instances=prism / "instances")
    rows = {c.id: c for c in doctor.checklist(cfg)}
    assert rows["paths.gos_home"].status == "error"
    assert f"{sub} folder overlaps" in rows["paths.gos_home"].detail


def test_a_link_loop_is_an_error_not_a_crash(prism: Path, tmp_path: Path) -> None:
    """The gate's low: a link loop is an error row, never a crash, and nothing is made. On
    POSIX resolve() raises on the loop, which counts as an overlap, so the DB isn't even looked
    at; Windows' non-strict resolve() doesn't raise, and there's no DB there to open."""
    loop = tmp_path / "loop"
    try:
        loop.symlink_to(tmp_path / "loop2")
        (tmp_path / "loop2").symlink_to(loop)
    except OSError:
        pytest.skip("symlinks need a privilege here")
    before = sorted(os.listdir(tmp_path))
    cfg = Config(gos_home=loop / "home", prism_instances=prism / "instances")
    rows = {c.id: c for c in doctor.checklist(cfg)}
    assert rows["paths.gos_home"].status == "error"
    if sys.platform != "win32":
        assert rows["db.gt_pin"].detail.startswith("not read")
    assert sorted(os.listdir(tmp_path)) == before


def test_the_pin_probe_leaves_no_wal_or_shm(tmp_path: Path) -> None:
    """The second low: the GT pin's read-only open is immutable, so SQLite makes no files."""
    db = tmp_path / "data" / "gos.sqlite"
    wal_db(db)
    before = snapshot(tmp_path)
    ok, line = gtpin.check_db(tmp_path)
    assert ok and "no export run yet" in line
    assert snapshot(tmp_path) == before


def test_doctor_reads_and_writes_nothing_through_a_home_in_an_instance(
    prism: Path, tmp_path: Path
) -> None:
    play, other = home_with_data_in_play(prism, tmp_path)
    before = snapshot(play)
    out = io.StringIO()
    assert doctor.main([prism], other, False, out) == 1
    assert "data folder overlaps Prism's instances folder" in out.getvalue()
    assert snapshot(play) == before  # no env.json, no -wal, no -shm


# --- (2) every Play remembered


def test_a_save_records_every_play_in_gos_toml_and_beside_it(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    play = str((prism / "instances" / "GTNH-Play").resolve())
    cfg = config.load(svc.path)
    assert Path(play) in cfg.former_plays
    record = (svc.path.parent / config.FORMER_PLAYS_FILE).read_text("utf-8")
    assert play in record.splitlines()
    assert all(Path(x).is_absolute() for x in record.splitlines() if x[:1] != "#")
    # Gone from gos.toml by hand: the file beside it still has it, and vice versa.
    hand_edit(svc.path, "former_plays", "former_plays_gone")
    assert Path(play) in config.load(svc.path).former_plays


def test_a_first_run_check_makes_no_file(
    svc: SetupService, prism: Path, home: Path
) -> None:
    svc.check("paths", path_values(prism, home, home / "app"))
    assert not home.exists()


def first_save_then_move_play(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    (tmp_path / "app").mkdir()
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    make_instance(prism / "instances", "Play2")
    # What the wizard's own refusal tells him to do.
    hand_edit(svc.path, 'play = "GTNH-Play"', 'play = "Play2"')


def test_a_hand_moved_play_never_frees_the_old_one_on_a_first_run_core(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    """Round 4 (a): the core started on a first run, so its Play is the default."""
    first_save_then_move_play(svc, prism, home, tmp_path)
    with pytest.raises(SetupError) as refused:
        svc.save("paths", {"lab": "GTNH-Play"})
    assert "lab" in refused.value.body["problems"]
    hand_edit(svc.path, 'lab = "GTNH-Lab"', 'lab = "GTNH-Play"')
    with pytest.raises(SetupError):
        svc.finish()
    assert "completed_utc" not in svc.path.read_text("utf-8")


def test_a_hand_moved_play_never_frees_the_old_one_after_a_restart(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path, ring: MemoryKeyring
) -> None:
    first_save_then_move_play(svc, prism, home, tmp_path)
    restarted = SetupService(svc.path, config.load(svc.path), provider=FakeDevices())
    with pytest.raises(SetupError):
        restarted.save("paths", {"lab": "GTNH-Play"})
    with pytest.raises(SetupError):
        restarted.save("paths", {"export": "GTNH-Play"})


def test_a_hand_swap_never_finishes(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    """Round 4 (b): play and lab swapped in one hand edit, on a first-run core."""
    (tmp_path / "app").mkdir()
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    hand_edit(svc.path, 'play = "GTNH-Play"', 'play = "GTNH-Lab-new"')
    hand_edit(svc.path, 'lab = "GTNH-Lab"', 'lab = "GTNH-Play"')
    hand_edit(svc.path, 'play = "GTNH-Lab-new"', 'play = "GTNH-Lab"')
    with pytest.raises(SetupError):
        svc.finish()


def test_deleting_gos_toml_keeps_the_record(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path, ring: MemoryKeyring
) -> None:
    """The fourth low: a deleted gos.toml and a restart bring back first-run mode, but the
    record beside it still holds the old Play."""
    first_save_then_move_play(svc, prism, home, tmp_path)
    svc.path.unlink()
    fresh = SetupService(svc.path, Config(gos_home=home), provider=FakeDevices())
    values = {**path_values(prism, home, tmp_path / "app"), "play": "Play2"}
    with pytest.raises(SetupError):
        fresh.save("paths", {**values, "lab": "GTNH-Play"})
    fresh.save("paths", values)  # a new Play on a first run is still fine
    assert config.load(svc.path).play == "Play2"


def test_a_case_only_move_of_play_is_refused_everywhere(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    """Folder names are compared case-folded on every OS: on a case-sensitive file system a
    folder that differs only in case counts as Play too, which errs on the safe side. NTFS
    ignores case, so on Windows it is Play's own folder: the save moves nothing and stands."""
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    if sys.platform == "win32":
        svc.save("paths", {"play": "gtnh-play"})
        return
    with pytest.raises(SetupError):
        svc.save("paths", {"play": "gtnh-play"})


def test_the_route_refuses_the_old_play_as_lab(
    client: TestClient,  # noqa: F811
    prism: Path,
    home: Path,
    tmp_path: Path,
    svc: SetupService,
) -> None:
    (tmp_path / "app").mkdir()
    r = client.post(
        "/api/setup/steps/paths",
        json={"values": path_values(prism, home, tmp_path / "app")},
        headers=admin(),
    )
    assert r.status_code == 200, r.text
    make_instance(prism / "instances", "Play2")
    hand_edit(svc.path, 'play = "GTNH-Play"', 'play = "Play2"')
    r = client.post(
        "/api/setup/steps/paths", json={"values": {"lab": "GTNH-Play"}}, headers=admin()
    )
    assert r.status_code == 400 and "lab" in r.json()["problems"]
    hand_edit(svc.path, 'lab = "GTNH-Lab"', 'lab = "GTNH-Play"')
    assert client.post("/api/setup/finish", headers=admin()).status_code == 400


# --- (3) sync refuses at the point of damage


def test_sync_refuses_a_target_that_was_play(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    first_save_then_move_play(svc, prism, home, tmp_path)
    hand_edit(svc.path, 'lab = "GTNH-Lab"', 'lab = "GTNH-Play"')
    cfg = config.load(svc.path)
    plans, refusals = sync.plan_all(cfg, ["lab"])
    assert plans == [] or not any(p.role == "lab" for p in plans)
    assert any("was Play" in r and "former_plays" in r for r in refusals)


def test_a_hand_edit_with_a_windows_path_reads_back(svc: SetupService) -> None:
    """The helper above, on any OS: a backslash path survives the hand edit."""
    svc.path.parent.mkdir(parents=True)
    svc.path.write_text('[paths]\ngos_home = "x"\n', "utf-8")
    hand_set(svc.path, "gos_home", "C:\\Users\\clayton\\GregtecOS")
    assert str(config.load(svc.path).gos_home).endswith("GregtecOS")


# --- (4) every Play GOS starts or syncs with is on record, wizard or not (final checks)


def restart(svc: SetupService) -> SetupService:
    """A core restart: the core records as it starts, and the dashboard asks for the state."""
    cfg = config.load(svc.path)
    assert record_plays(svc.path, cfg) is None
    again = SetupService(svc.path, cfg, provider=FakeDevices())
    again.describe()
    return again


def hand_play(svc: SetupService, prism: Path, name: str) -> None:
    """A new instance with a file only it has, made Play by a hand edit of gos.toml."""
    make_instance(prism / "instances", name)
    marker = (
        prism / "instances" / name / ".minecraft" / "config" / "only_in_old_play.cfg"
    )
    marker.write_text("mine\n", "utf-8")
    hand_set(svc.path, "play", name)


def test_a_play_set_by_hand_between_restarts_never_becomes_lab(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    (tmp_path / "app").mkdir()
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    hand_play(svc, prism, "Play2")
    svc = restart(svc)
    hand_play(svc, prism, "Play3")
    svc = restart(svc)
    with pytest.raises(SetupError) as refused:
        svc.save("paths", {"lab": "Play2"})
    assert "lab" in refused.value.body["problems"]
    hand_set(svc.path, "lab", "Play2")
    with pytest.raises(SetupError):
        svc.finish()
    plans, refusals = sync.plan_all(config.load(svc.path), ["lab"])
    assert not plans and any("was Play" in r for r in refusals)
    assert (prism / "instances/Play2/.minecraft/config/only_in_old_play.cfg").is_file()


def test_an_install_from_before_the_wizard_records_its_play_on_start(
    prism: Path, home: Path, ring: MemoryKeyring
) -> None:
    """The upgrade path: a gos.toml with no record. The first start records its Play."""
    path = home / "config" / "gos.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "[paths]\n"
        f"prism_instances = {tomledit.format_value(str(prism / 'instances'))}\n"
        f"gos_home = {tomledit.format_value(str(home))}\n"
        'play = "GTNH-Play"\nexport = "GTNH-Export"\nlab = "GTNH-Lab"\n',
        "utf-8",
    )
    svc = restart(SetupService(path, config.load(path), provider=FakeDevices()))
    hand_play(svc, prism, "Play2")
    svc = restart(svc)
    with pytest.raises(SetupError):
        svc.save("paths", {"lab": "GTNH-Play"})
    hand_set(path, "lab", "GTNH-Play")
    plans, refusals = sync.plan_all(config.load(path), ["lab"])
    assert not plans and any("was Play" in r for r in refusals)


def test_describe_alone_records_the_play(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    """The dashboard's first request after a start records, whatever started the core."""
    (tmp_path / "app").mkdir()
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    hand_play(svc, prism, "Play2")
    SetupService(svc.path, config.load(svc.path), provider=FakeDevices()).describe()
    play2 = (prism / "instances" / "Play2").resolve()
    assert play2 in config.load(svc.path).former_plays


def test_a_first_run_records_nothing(home: Path) -> None:
    assert record_plays(home / "config" / "gos.toml", Config(gos_home=home)) is None
    assert not home.exists()


def sync_args(path: Path, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(config=path, only="lab", dry_run=dry_run)


def test_sync_records_its_play_before_it_plans(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    (tmp_path / "app").mkdir()
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    hand_play(svc, prism, "Play2")
    play2 = (prism / "instances" / "Play2").resolve()
    sync.run(sync_args(svc.path, dry_run=True), io.StringIO())
    assert play2 not in config.load(svc.path).former_plays  # a dry run writes nothing
    sync.run(sync_args(svc.path), io.StringIO())
    assert play2 in config.load(svc.path).former_plays
    hand_set(svc.path, "play", "GTNH-Play")
    hand_set(svc.path, "lab", "Play2")
    out = io.StringIO()
    assert sync.run(sync_args(svc.path), out) == 1
    assert "was Play" in out.getvalue()
    assert (prism / "instances/Play2/.minecraft/config/only_in_old_play.cfg").is_file()


def test_sync_refuses_when_the_record_cant_be_written(
    svc: SetupService,
    prism: Path,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "app").mkdir()
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    hand_play(svc, prism, "Play2")  # a Play not on record yet
    lab = snapshot(prism / "instances" / "GTNH-Lab")

    def disk_full(target: Path, text: str, verify: object = None) -> NoReturn:
        raise OSError("no space left")

    monkeypatch.setattr(tomledit, "write_atomic", disk_full)
    out = io.StringIO()
    assert sync.run(sync_args(svc.path), out) == 1
    assert "Refused, nothing written" in out.getvalue()
    assert snapshot(prism / "instances" / "GTNH-Lab") == lab


def test_the_core_records_its_play_as_it_starts(
    svc: SetupService,
    prism: Path,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gregtec.api import server

    (tmp_path / "app").mkdir()
    svc.save("paths", path_values(prism, home, tmp_path / "app"))
    hand_play(svc, prism, "Play2")

    def no_port(port: int) -> NoReturn:
        raise OSError("taken")

    monkeypatch.setattr(server, "bind", no_port)
    # applog's handlers are process-wide; other tests' caplog counts on the defaults.
    monkeypatch.setattr(server.applog, "setup", lambda *args, **kwargs: None)
    cfg = config.load(svc.path)
    assert server.run(cfg, config_path=svc.path) == server.EXIT_PORT_IN_USE
    play2 = (prism / "instances" / "Play2").resolve()
    assert play2 in config.load(svc.path).former_plays
