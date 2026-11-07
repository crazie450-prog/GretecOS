"""WO-1.9's playbook on temp folders: three fake Prism instances (Play, Lab, Export), a fake Prism
launch that "exports" whatever GT jar Export has, and fixture plans.

The acceptance in miniature: a changed GT jar in Lab gives the "adopt?" prompt, the whole
checklist runs, and exactly the plans citing a changed or removed recipe are flagged. Play here
is a temp folder; nothing outside tmp_path is read or written.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import time
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from gregtec import config, locks, playhooks
from gregtec.db import access, build
from gregtec.ingest import collect, fingerprint
from gregtec.packupdate import diff, gtpin, plans, playbook, playupdate, releases
from gregtec.vault import deploy as vault_deploy

NEW_GT = "5.09.54.266"
OLD_JAR = "gregtech-5.09.54.265.jar"
NEW_JAR = "gregtech-5.09.54.266.jar"


# --- a tiny GT-only export run ----------------------------------------------------------------


def _gt(idx: int, dust: int, ingot: int, duration: int) -> dict[str, Any]:
    return {
        "map": "gt.recipe.blastfurnace",
        "idx": idx,
        "category": "gt.recipe.blastfurnace",
        "eut": 120,
        "duration": duration,
        "special": 1000,
        "in": [{"item": "test:dust", "meta": dust, "n": 1}],
        "out": [{"item": "test:ingot", "meta": ingot, "n": 1}],
        "fin": [],
        "fout": [],
        "metadata": {"coil_heat": 1000},
        "enabled": True,
        "hidden": False,
        "fake": False,
        "can_be_buffered": True,
        "needs_empty_output": False,
        "nbt_sensitive": False,
        "owners": ["gregtech"],
    }


def recipes(gt: str) -> list[dict[str, Any]]:
    """GT .265: ingots 1, 2 and 3. A "GT .266": 1 rebalanced, 2 gone, 4 new."""
    if gt != NEW_GT:
        return [_gt(0, 1, 1, 1000), _gt(1, 2, 2, 500), _gt(2, 3, 3, 400)]
    return [_gt(0, 1, 1, 800), _gt(1, 3, 3, 400), _gt(2, 4, 4, 300)]


def _jsonl(lines: Sequence[object]) -> bytes:
    return "".join(json.dumps(x) + "\n" for x in lines).encode("utf-8")


def write_run(exports: Path, run_id: str, created: str, gt: str) -> Path:
    lines = recipes(gt)
    items = [
        {"item": f"test:{k}", "meta": m, "unlocalized": f"{k}.{m}", "name": f"{k} {m}"}
        for k in ("dust", "ingot")
        for m in range(1, 5)
    ]
    maps = [
        {
            "name": "gt.recipe.blastfurnace",
            "amperage": 1,
            "backend": "gregtech.api.recipe.RecipeMapBackend",
            "frontend": "gregtech.api.recipe.RecipeMapFrontend",
            "ui": {
                "max_item_in": 6,
                "max_item_out": 6,
                "max_fluid_in": 2,
                "max_fluid_out": 2,
            },
            "nei_register": True,
            "owner_mod": "gregtech",
            "categories": [{"name": "gt.recipe.blastfurnace", "owner_mod": "gregtech"}],
            "count": len(lines),
        }
    ]
    files = {
        "gt_recipe_maps.json": json.dumps(maps).encode("utf-8") + b"\n",
        "gt_recipes.jsonl": _jsonl(lines),
        "gt_machines.jsonl": b"",
        "items.jsonl": _jsonl(items),
        "fluids.jsonl": b"",
    }
    run = exports / run_id
    run.mkdir(parents=True)
    for rel, data in files.items():
        (run / rel).write_bytes(data)
    manifest = {
        "format": "gregteclink-export/1",
        "run_id": run_id,
        "created_utc": created,
        "complete": True,
        "mode": "autoexport",
        "side": "server",
        "pack_version": "2.9.0",
        "gt_version": gt,
        "nei_version": None,
        "gregteclink": {"version": "test"},
        "mods": [
            {"modid": "gregtech", "version": gt, "file": "gt.jar", "sha256": None}
        ],
        "files": {
            rel: {
                "sha256": hashlib.sha256(d).hexdigest(),
                "bytes": len(d),
                "lines": d.count(b"\n"),
            }
            for rel, d in files.items()
        },
        "counts": {"recipes": len(lines), "recipe_maps": 1, "machines": 0},
        "parts": {
            "gt_dump": "ok",
            "machines": "ok",
            "refs": "ok",
            "nei": "skipped_server",
            "autoexport": "ok",
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run


# --- three fake instances ---------------------------------------------------------------------


def make_instance(root: Path, name: str, jar: str) -> Path:
    inst = root / name
    game = inst / ".minecraft"
    for d in ("mods", "scripts", "config/GTNewHorizons"):
        (game / d).mkdir(parents=True)
    (inst / "instance.cfg").write_text("InstanceType=OneSix\n", encoding="utf-8")
    (inst / "mmc-pack.json").write_text('{"components": []}', encoding="utf-8")
    (game / "config/GTNewHorizons/dreamcraft.cfg").write_text(
        "S:ModPackVersion=2.9.0\n", encoding="utf-8"
    )
    (game / "mods" / jar).write_bytes(jar.encode("utf-8"))
    (game / "scripts" / "pack.zs").write_text("// pack\n", encoding="utf-8")
    (game / "config" / "play-only.cfg").write_text("mine\n", encoding="utf-8")
    return inst


class FakePrism:
    """``launch``: Export "exports" the recipes of the GT jar it holds."""

    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self.n = 0

    def launch(self, argv: Sequence[str]) -> None:
        assert argv[-1] == self.cfg.export, "only Export is ever launched"
        game = self.cfg.instance("export") / ".minecraft"
        gt = NEW_GT if (game / "mods" / NEW_JAR).is_file() else "5.09.54.265"
        self.n += 1
        write_run(
            game / "gregteclink" / "exports",
            f"20261107T0{self.n}0000Z",
            f"2026-11-07T0{self.n}:00:00Z",
            gt,
        )

    def clients(self, cfg: config.Config) -> list[str]:
        return []


def fake_fetch(url: str, headers: object) -> releases.Response:
    """DAXXL's manifests for 2.9.0 and 2.9.1 (GT .265 -> .266); 404 for anything else."""
    gts = {"2.9.0": "5.09.54.265", "2.9.1": NEW_GT}
    for v, gt in gts.items():
        if url == releases.MANIFEST_URL.format(version=v):
            body = {
                "version": v,
                "github_mods": {
                    "GT5-Unofficial": {"version": gt, "side": "BOTH"},
                    "NotEnoughItems": {"version": "2.8.171-GTNH", "side": "BOTH"},
                },
                "external_mods": {},
            }
            return releases.Response(200, json.dumps(body).encode("utf-8"))
    return releases.Response(404)


@pytest.fixture
def world(tmp_path: Path) -> tuple[config.Config, FakePrism]:
    root = tmp_path / "instances"
    for name in ("GTNH-Play", "GTNH-Lab", "GTNH-Export"):
        make_instance(root, name, OLD_JAR)
    cfg = config.Config(
        prism_instances=root,
        gos_home=tmp_path / "home",
        gos_app=tmp_path / "app",
        prism_root=tmp_path / "prism",
    )
    return cfg, FakePrism(cfg)


def ctx(cfg: config.Config, prism: FakePrism, out: io.StringIO) -> playbook.Ctx:
    return playbook.Ctx(
        cfg,
        out,
        fetch=fake_fetch,
        launch=prism.launch,
        clients=prism.clients,
        tweak_problems=lambda inst: [],
        sleep=lambda s: None,
        poll_s=0.0,
    )


def baseline(cfg: config.Config, prism: FakePrism) -> None:
    """Play's first export, the normal way: recorded, collected, built into the main DB."""
    out = io.StringIO()
    assert (
        playhooks.run_export(
            cfg,
            out,
            launch=prism.launch,
            clients=prism.clients,
            sleep=lambda s: None,
            poll_s=0.0,
        )
        == 0
    ), out.getvalue()
    game = cfg.instance("export") / ".minecraft"
    collect.collect(game, cfg.gos_home, 5)
    last = fingerprint.last_export(cfg.gos_home)
    assert last is not None
    build.build(cfg.gos_home, fingerprint=last["fingerprint"])


def ref_of(db: Path, ingot: int, run_key: str | None = None) -> str:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT r.ref FROM recipe r JOIN recipe_output o ON o.recipe_id = r.id"
            " JOIN item i ON i.id = o.item_id JOIN export_run e ON e.id = r.run_id"
            " WHERE i.registry_name = 'test:ingot' AND i.meta = ?"
            " AND (? IS NULL OR e.run_key = ?) ORDER BY e.created_at LIMIT 1",
            (ingot, run_key, run_key),
        ).fetchone()
    finally:
        conn.close()
    return str(row[0])


def add_plans(cfg: config.Config) -> dict[str, str]:
    """Four plans on the baseline run: citing ingot 1 (rebalanced), 2 (removed), 3 (unchanged),
    and a quest only. Returns {plan id: what it cites}."""
    db = access.db_path(cfg.gos_home)
    conn = sqlite3.connect(db)
    run_id, run_key = conn.execute("SELECT id, run_key FROM export_run").fetchone()
    cites = {
        "pl-changed": ref_of(db, 1),
        "pl-removed": ref_of(db, 2),
        "pl-same": ref_of(db, 3),
        "pl-quest": "q:abc",
    }
    folder = cfg.gos_home / "data" / "plans"
    folder.mkdir(parents=True)
    for pid, cite in cites.items():
        (folder / f"{pid}.json").write_text(
            json.dumps(
                {
                    "format": plans.FORMAT,
                    "id": pid,
                    "name": pid,
                    "status": "accepted",
                    "run": run_key,
                    "cites": [cite],
                }
            ),
            encoding="utf-8",
        )
        conn.execute(
            "INSERT INTO plan(id, name, status, json_path, run_id) VALUES (?,?,?,?,?)",
            (pid, pid, "accepted", f"data/plans/{pid}.json", run_id),
        )
    conn.commit()
    conn.close()
    return cites


def backup_zip(cfg: config.Config) -> None:
    folder = cfg.gos_home / playbook.WORLD_BACKUPS
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "2026-11-07-01-00-00.zip", "w") as z:
        z.writestr("level.dat", b"x")


def update_lab(cfg: config.Config) -> None:
    """Clayton's part in Prism: Lab gets the new GT jar."""
    mods = cfg.instance("lab") / ".minecraft" / "mods"
    (mods / OLD_JAR).unlink()
    (mods / NEW_JAR).write_bytes(NEW_JAR.encode("utf-8"))


def affected(cfg: config.Config) -> dict[str, int | None]:
    conn = sqlite3.connect(access.db_path(cfg.gos_home))
    try:
        return dict(conn.execute("SELECT id, affected_run FROM plan").fetchall())
    finally:
        conn.close()


# --- the tests --------------------------------------------------------------------------------


def test_a_real_update_runs_the_whole_checklist_and_flags_the_right_plans(
    world: tuple[config.Config, FakePrism], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, prism = world
    baseline(cfg, prism)
    add_plans(cfg)
    home = cfg.gos_home
    with pytest.raises(playbook.UpdateError, match="newer than the adopted"):
        playbook.start(home, "2.9.0", simulate=False)
    u = playbook.start(home, "2.9.1", simulate=False)
    out = io.StringIO()
    c = ctx(cfg, prism, out)

    # No world backup yet: the walk stops there, before anything changes.
    assert playbook.walk(c, u) == 3
    assert u.status("release") == "done" and u.status("backup") == "waiting"
    assert "GT 5.09.54.265 -> 5.09.54.266" in out.getvalue()
    backup_zip(cfg)
    # Lab still equals Play: waits for Clayton to update Lab.
    assert playbook.walk(c, u) == 3 and u.status("lab") == "waiting"
    update_lab(cfg)
    vault_deploy.deploy(home / "vault")

    out.seek(0)
    out.truncate()
    assert playbook.walk(c, u) == 3  # lab, lab_export, diff, then "adopt?"
    notes = list((home / "vault" / "inbox").glob("ib-*.md"))
    assert len(notes) == 1, out.getvalue()
    note = notes[0].read_text(encoding="utf-8")
    assert "title: Pack update 2.9.1: adopt?" in note and "recipes_removed: 1" in note
    text = out.getvalue()
    assert [u.status(s) for s in ("lab", "lab_export", "diff", "adopt")] == [
        "done",
        "done",
        "done",
        "waiting",
    ]
    assert "ADOPT? Adopt GTNH 2.9.1?" in text and "GT PIN MISMATCH" in text
    assert "1 added, 1 removed, 1 changed" in text
    assert sorted(u.step("diff")["data"]["flagged_preview"]) == [
        "pl-changed",
        "pl-removed",
    ]
    report = (u.folder / "diff.txt").read_text(encoding="utf-8")
    assert "ticks 1000 -> 800" in report and "plan:pl-removed" in report
    # The Lab run is in the sandbox DB only: the main DB still has Play's one run.
    conn = diff.connect(access.db_path(home))
    assert len(conn.execute("SELECT id FROM export_run").fetchall()) == 1
    conn.close()
    assert (u.sandbox / access.DB_FILE).is_file()
    assert all(v is None for v in affected(cfg).values())

    # Never adopted by itself, never as gos-dev, never while the GT pin differs.
    assert playbook.walk(c, u) == 3
    monkeypatch.setattr(config, "running_as_dev", lambda: True)
    with pytest.raises(playbook.UpdateError, match="Clayton decides"):
        playbook.answer(home, u, True)
    monkeypatch.setattr(config, "running_as_dev", lambda: False)
    with pytest.raises(playbook.UpdateError, match="GT PIN MISMATCH"):
        playbook.answer(home, u, True)
    monkeypatch.setattr(config, "GT_PIN", NEW_GT)  # the pin bump, deployed
    playbook.answer(home, u, True, "tested in Lab")

    # Play waits for Clayton's script; its dry run changes nothing.
    assert playbook.walk(c, u) == 3 and u.status("play") == "waiting"
    play_jar = cfg.instance("play") / ".minecraft" / "mods" / OLD_JAR
    assert playupdate.run(cfg, None, False, io.StringIO(), prism.clients) == 0
    assert play_jar.is_file()
    log = io.StringIO()
    assert playupdate.run(cfg, None, True, log, prism.clients) == 0, log.getvalue()
    assert not play_jar.exists()
    assert (u.folder / "play-before" / "mods" / OLD_JAR).is_file()  # the way back
    assert (cfg.instance("play") / ".minecraft" / "config" / "play-only.cfg").is_file()

    u = playbook.find(home, u.id)  # as the next gos pack update reads it
    assert u.step("play")["data"]["backup"].endswith("play-before")
    out.seek(0)
    out.truncate()
    assert playbook.walk(c, u) == 0, out.getvalue()
    assert u.state["outcome"] == "adopted"
    flags = affected(cfg)
    new_run = diff.side(access.db_path(home), None, home)
    assert new_run.gt_version == NEW_GT
    assert flags == {
        "pl-changed": new_run.run_id,
        "pl-removed": new_run.run_id,
        "pl-same": None,
        "pl-quest": None,
    }
    assert playbook.adopted(home)["version"] == "2.9.1"
    assert playbook.active(home) is None


def test_a_simulated_update_never_touches_play_or_adopts(
    world: tuple[config.Config, FakePrism], tmp_path: Path
) -> None:
    cfg, prism = world
    baseline(cfg, prism)
    db = access.db_path(cfg.gos_home)
    run_key = diff.side(db, None, cfg.gos_home).run_key
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for pid, ingot in (("pl-fx-changed", 1), ("pl-fx-same", 3)):
        (fixtures / f"{pid}.json").write_text(
            json.dumps(
                {
                    "format": plans.FORMAT,
                    "id": pid,
                    "run": run_key,
                    "cites": [ref_of(db, ingot)],
                }
            ),
            encoding="utf-8",
        )
    u = playbook.start(cfg.gos_home, "2.9.1-sim", simulate=True)
    assert playbook.copy_fixture_plans(u, fixtures)[0].startswith("fixture plan")
    backup_zip(cfg)
    update_lab(cfg)
    c = ctx(cfg, prism, io.StringIO())
    assert playbook.walk(c, u) == 3 and u.status("adopt") == "waiting"
    assert u.step("diff")["data"]["flagged_preview"] == ["pl-fx-changed"]
    lines = playbook.answer(cfg.gos_home, u, True)
    assert "a real update would be refused here" in lines[0]
    assert playbook.walk(c, u) == 3 and u.status("play") == "waiting"
    before = fingerprint.compute_play(cfg, use_cache=False).digest
    refused = io.StringIO()
    assert playupdate.run(cfg, None, True, refused, prism.clients) == 1
    assert "simulated" in refused.getvalue()
    assert playupdate.run(cfg, None, False, io.StringIO(), prism.clients) == 0
    u = playbook.find(cfg.gos_home, u.id)  # as the next gos pack update reads it
    assert playbook.walk(c, u) == 0
    assert u.state["outcome"] == "simulated"
    assert u.status("play_export") == "skipped"
    assert fingerprint.compute_play(cfg, use_cache=False).digest == before
    assert playbook.adopted(cfg.gos_home)["version"] == config.PACK_PIN
    assert not (cfg.gos_home / playbook.ADOPTED).exists()


def test_a_declined_update_ends_and_play_stays(
    world: tuple[config.Config, FakePrism],
) -> None:
    cfg, prism = world
    baseline(cfg, prism)
    u = playbook.start(cfg.gos_home, "2.9.1", simulate=False)
    backup_zip(cfg)
    update_lab(cfg)
    out = io.StringIO()
    c = ctx(cfg, prism, out)
    assert playbook.walk(c, u) == 3
    u.decision()["status"] = None
    with pytest.raises(playbook.UpdateError, match="no pending question"):
        playbook.answer(cfg.gos_home, u, True)
    u.decision()["status"] = playbook.PENDING_DECISION
    playbook.answer(cfg.gos_home, u, False, "wait for 2.9.2")
    assert playbook.walk(c, u) == 0
    assert u.state["outcome"] == "declined"
    assert "Play stays on 2.9.0" in out.getvalue()
    assert (cfg.instance("play") / ".minecraft" / "mods" / OLD_JAR).is_file()
    # The next update can start.
    assert playbook.start(cfg.gos_home, "2.9.2", simulate=False).version == "2.9.2"


def test_lab_changed_after_its_check_stops_the_export(
    world: tuple[config.Config, FakePrism],
) -> None:
    cfg, prism = world
    baseline(cfg, prism)
    u = playbook.start(cfg.gos_home, "2.9.1", simulate=False)
    backup_zip(cfg)
    update_lab(cfg)
    c = ctx(cfg, prism, io.StringIO())
    for name in ("release", "backup", "lab"):
        playbook._record(u, name, playbook.STEP_FUNCS[name](c, u))
    (cfg.instance("lab") / ".minecraft" / "mods" / "extra.jar").write_bytes(b"x")
    o = playbook.step_lab_export(c, u)
    assert o.status == "failed" and "--redo lab" in o.lines[0]
    playbook.redo(u, "lab")
    assert u.status("lab") == "pending" and u.status("backup") == "done"


def test_one_update_at_a_time_and_abandon(tmp_path: Path) -> None:
    home = tmp_path / "home"
    u = playbook.start(home, "2.9.1-sim", simulate=True)
    with pytest.raises(playbook.UpdateError, match="still open"):
        playbook.start(home, "2.9.2", simulate=False)
    playbook.abandon(u, "test")
    assert playbook.active(home) is None
    assert playbook.find(home, u.id).state["outcome"] == "abandoned: test"
    with pytest.raises(playbook.UpdateError, match="not an update id"):
        playbook.find(home, "../x")


def test_the_gt_pin_check_is_loud() -> None:
    assert gtpin.problem(config.GT_PIN) is None
    msg = gtpin.problem("5.09.54.266")
    assert msg is not None and msg.startswith("GT PIN MISMATCH")
    assert "no GT version" in (gtpin.problem(None) or "")


def test_gos_pack_commands_refuse_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from gregtec import cli

    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n', encoding="utf-8"
    )
    c = ["--config", str(toml)]
    assert cli.main(["pack", "status", *c]) == 1
    assert cli.main(["pack", "adopt", "yes", *c]) == 1
    assert cli.main(["pack", "update", "--start", "2.9.0", *c]) == 1
    assert cli.main(["pack", "pin", *c]) == 1
    text = capsys.readouterr().out
    assert "no pack update is open" in text and "newer than the adopted" in text
    assert "there is no DB" in text


def test_lab_to_export_and_lab_to_play_refuse_overlaps(
    world: tuple[config.Config, FakePrism],
) -> None:
    from dataclasses import replace

    from gregtec import sync

    cfg, _ = world
    plan, refusals = sync.plan_lab_to_export(cfg)
    assert plan is not None and not refusals
    assert plan.source == cfg.instance("lab") / ".minecraft"
    same = replace(cfg, lab=cfg.play)
    plan, refusals = sync.plan_lab_to_export(same)
    assert plan is None and any("is Play" in r for r in refusals)
    plan, refusals = playupdate.plan_update(same)
    assert plan is None and any("is Play" in r for r in refusals)


# --- review fixes: Lab runs stay out of the main DB; the guards around writing Play ------------


def adopted_update(
    cfg: config.Config, prism: FakePrism, monkeypatch: pytest.MonkeyPatch
) -> playbook.Update:
    """A real update to 2.9.1, adopted (after the pin bump), waiting on the Play step."""
    baseline(cfg, prism)
    u = playbook.start(cfg.gos_home, "2.9.1", simulate=False)
    backup_zip(cfg)
    update_lab(cfg)
    assert playbook.walk(ctx(cfg, prism, io.StringIO()), u) == 3
    monkeypatch.setattr(config, "GT_PIN", NEW_GT)
    playbook.answer(cfg.gos_home, u, True)
    return playbook.find(cfg.gos_home, u.id)


def main_runs(cfg: config.Config) -> list[str]:
    conn = sqlite3.connect(access.db_path(cfg.gos_home))
    try:
        return [r[0] for r in conn.execute("SELECT run_key FROM export_run")]
    finally:
        conn.close()


def test_the_lab_run_never_reaches_the_main_db(
    world: tuple[config.Config, FakePrism],
) -> None:
    cfg, prism = world
    baseline(cfg, prism)
    [play_run] = main_runs(cfg)
    u = playbook.start(cfg.gos_home, "2.9.1", simulate=False)
    backup_zip(cfg)
    update_lab(cfg)
    out = io.StringIO()
    assert playbook.walk(ctx(cfg, prism, out), u) == 3
    lab_run = u.step("lab_export")["data"]["run"]
    assert collect.lab_runs(cfg.gos_home) == {lab_run}
    # Declined: the Lab run is still Export's newest, as in the review's probe.
    playbook.answer(cfg.gos_home, u, False)
    assert playbook.walk(ctx(cfg, prism, out), u) == 0
    game = cfg.instance("export") / ".minecraft"
    assert (game / collect.EXPORTS / lab_run).is_dir()
    # The runbook's pipeline: collect takes Play's run again, build loads Play's run.
    got = collect.collect(game, cfg.gos_home, 5)
    assert got.run.run_id == play_run
    assert (lab_run, collect.LAB_RUN) in got.skipped
    assert not (cfg.gos_home / collect.DATA_EXPORTS / lab_run).exists()
    build.build(cfg.gos_home)
    assert main_runs(cfg) == [play_run]
    # A Lab run copied into the main GOS_HOME by hand is still never built.
    shutil.copytree(
        u.sandbox / collect.DATA_EXPORTS / lab_run,
        cfg.gos_home / collect.DATA_EXPORTS / lab_run,
    )
    build.build(cfg.gos_home)
    assert main_runs(cfg) == [play_run]
    with pytest.raises(build.BuildError, match="Lab run"):
        build.build(cfg.gos_home, lab_run)
    # The next update diffs against Play's run, not the Lab one.
    v = playbook.start(cfg.gos_home, "2.9.2", simulate=False)
    assert playbook.walk(ctx(cfg, prism, io.StringIO()), v) == 3
    assert v.step("diff")["data"]["old_run"] == play_run


GUARDS = [
    ("gos-dev", "never as gos-dev"),
    ("not-adopted", "isn't adopted"),
    ("backup-step", "backup step isn't done"),
    ("old-backup", "no world backup from the last 24 h"),
    ("old-confirm", "no world backup from the last 24 h"),
    ("lab-changed", "Lab changed since its export was tested"),
    ("client", "is running; close it"),
    ("lock", "the instance lock is held"),
    ("abandoned", "is finished"),
    ("applied", "Play was already updated"),
]


@pytest.mark.parametrize(("case", "why"), GUARDS, ids=[c for c, _ in GUARDS])
def test_update_play_apply_refuses(
    case: str,
    why: str,
    world: tuple[config.Config, FakePrism],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, prism = world
    u = adopted_update(cfg, prism, monkeypatch)
    clients: playhooks.Clients = prism.clients
    stale = time.time() - 10 * 24 * 3600
    zips = list((cfg.gos_home / playbook.WORLD_BACKUPS).glob("*.zip"))
    if case == "gos-dev":
        monkeypatch.setattr(config, "running_as_dev", lambda: True)
    elif case == "not-adopted":
        u.decision()["status"] = playbook.PENDING_DECISION
    elif case == "backup-step":
        u.step("backup")["status"] = playbook.WAITING
    elif case == "old-backup":
        for z in zips:
            os.utime(z, (stale, stale))
    elif case == "old-confirm":
        for z in zips:
            z.unlink()
        playbook.confirm(u, "backup", "copied by hand", now=stale)
    elif case == "lab-changed":
        (cfg.instance("lab") / ".minecraft" / "mods" / "extra.jar").write_bytes(b"x")
    elif case == "client":

        def clients(c: config.Config) -> list[str]:
            return ["play"]

    elif case == "abandoned":
        playbook.abandon(u, "test")
    elif case == "applied":
        (u.folder / playupdate.BEFORE).mkdir()
        (u.folder / playupdate.BEFORE / playupdate.MANIFEST).write_text(
            "{}", encoding="utf-8"
        )
    u.save()
    held = (
        locks.InstanceLock(cfg.gos_home).acquire("export", "test")
        if case == "lock"
        else None
    )
    out = io.StringIO()
    try:
        assert playupdate.run(cfg, u.id, True, out, clients) == 1, out.getvalue()
    finally:
        if held is not None:
            held.release()
    assert "refused: " in out.getvalue() and why in out.getvalue()
    assert (cfg.instance("play") / ".minecraft" / "mods" / OLD_JAR).is_file()
    assert not (cfg.instance("play") / ".minecraft" / "mods" / NEW_JAR).exists()


def test_a_fresh_hand_confirm_counts_as_a_backup(
    world: tuple[config.Config, FakePrism], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, prism = world
    u = adopted_update(cfg, prism, monkeypatch)
    for z in (cfg.gos_home / playbook.WORLD_BACKUPS).glob("*.zip"):
        z.unlink()
    now = time.time()
    assert "no world backup" in (playbook.backup_problem(cfg.gos_home, u, now) or "")
    playbook.confirm(u, "backup", "copied by hand", now=now - 3600)
    assert playbook.backup_problem(cfg.gos_home, u, now) is None
    assert playbook.backup_problem(cfg.gos_home, u, now + 24 * 3600) is not None


def test_rollback_puts_play_back_exactly(
    world: tuple[config.Config, FakePrism], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, prism = world
    play = cfg.instance("play") / ".minecraft"
    lab = cfg.instance("lab") / ".minecraft"
    (play / "mods" / "old-addon.jar").write_bytes(b"addon")  # Lab doesn't have it
    (play / "scripts" / "gone").mkdir()
    (play / "scripts" / "gone" / "a.zs").write_text("// a\n", encoding="utf-8")
    (lab / "scripts" / "new").mkdir()
    (lab / "scripts" / "new" / "b.zs").write_text("// b\n", encoding="utf-8")
    (lab / "scripts" / "pack.zs").write_text("// pack, new\n", encoding="utf-8")
    before = fingerprint.compute_play(cfg, use_cache=False)
    u = adopted_update(cfg, prism, monkeypatch)
    log = io.StringIO()
    assert playupdate.run(cfg, None, True, log, prism.clients) == 0, log.getvalue()
    assert (play / "mods" / NEW_JAR).is_file() and (play / "scripts/new/b.zs").is_file()
    assert not (play / "mods" / "old-addon.jar").exists()
    assert "--rollback" in log.getvalue()
    m = json.loads((u.folder / "play-before" / "manifest.json").read_bytes())
    assert sorted(m["added"]) == [f"mods/{NEW_JAR}", "scripts/new/b.zs"]
    assert m["made_dirs"] == ["scripts/new"] and m["removed_dirs"] == ["scripts/gone"]
    assert sorted(m["deleted"]) == [
        f"mods/{OLD_JAR}",
        "mods/old-addon.jar",
        "scripts/gone/a.zs",
    ]
    assert m["replaced"] == ["scripts/pack.zs"]

    # An added file changed since: refused, nothing written.
    b = play / "scripts" / "new" / "b.zs"
    b.write_text("// mine now\n", encoding="utf-8")
    out = io.StringIO()
    assert playupdate.rollback(cfg, None, out, prism.clients) == 1
    assert "was changed since the update" in out.getvalue()
    assert (play / "mods" / NEW_JAR).is_file()
    b.write_text("// b\n", encoding="utf-8")

    monkeypatch.setattr(config, "running_as_dev", lambda: True)
    assert playupdate.rollback(cfg, None, io.StringIO(), prism.clients) == 1
    monkeypatch.setattr(config, "running_as_dev", lambda: False)
    out = io.StringIO()
    assert playupdate.rollback(cfg, None, out, prism.clients) == 0, out.getvalue()
    after = fingerprint.compute_play(cfg, use_cache=False)
    assert after.digest == before.digest  # mods, scripts and configs exactly as before
    assert sorted(p.name for p in (play / "mods").iterdir()) == [
        OLD_JAR,
        "old-addon.jar",
    ]
    assert not (play / "scripts" / "new").exists()
    assert (play / "scripts" / "gone" / "a.zs").is_file()
    assert not (u.folder / "play-before").exists()
    assert len(list(u.folder.glob("play-before-rolled-back-*"))) == 1
    u = playbook.find(cfg.gos_home, u.id)
    assert u.status("play") == "pending" and u.decision()["status"] == "adopted"
    assert playupdate.rollback(cfg, None, io.StringIO(), prism.clients) == 1
    # And it can be applied again.
    assert playupdate.run(cfg, None, True, io.StringIO(), prism.clients) == 0
    assert (play / "mods" / NEW_JAR).is_file()


def test_a_finished_update_takes_no_more_answers_or_steps(
    world: tuple[config.Config, FakePrism],
) -> None:
    cfg, prism = world
    baseline(cfg, prism)
    u = playbook.start(cfg.gos_home, "2.9.1", simulate=False)
    backup_zip(cfg)
    update_lab(cfg)
    assert playbook.walk(ctx(cfg, prism, io.StringIO()), u) == 3
    playbook.abandon(u, "changed my mind")
    calls = (
        lambda: playbook.answer(cfg.gos_home, u, True),
        lambda: playbook.answer(cfg.gos_home, u, False),
        lambda: playbook.redo(u, "diff"),
        lambda: playbook.confirm(u, "backup", ""),
        lambda: playbook.abandon(u, "again"),
    )
    for call in calls:
        with pytest.raises(playbook.UpdateError, match="is finished"):
            call()
    outcome = playbook.find(cfg.gos_home, u.id).state["outcome"]
    assert outcome == "abandoned: changed my mind"


def test_adopt_yes_needs_a_gt_version_that_is_the_pin(
    world: tuple[config.Config, FakePrism],
) -> None:
    cfg, prism = world
    baseline(cfg, prism)
    u = playbook.start(cfg.gos_home, "2.9.1", simulate=False)
    backup_zip(cfg)
    update_lab(cfg)
    assert playbook.walk(ctx(cfg, prism, io.StringIO()), u) == 3
    data = u.step("diff")["data"]
    data.update(gt_new=None, gt_problem=None)  # a diff that recorded no GT version
    with pytest.raises(playbook.UpdateError, match="no GT version"):
        playbook.answer(cfg.gos_home, u, True)
    data.update(gt_new=NEW_GT, gt_problem=None)  # recorded before a pin change
    with pytest.raises(playbook.UpdateError, match="GT PIN MISMATCH"):
        playbook.answer(cfg.gos_home, u, True)
    playbook.answer(cfg.gos_home, u, False)  # no is always taken


def test_db_build_and_doctor_are_loud_on_a_gt_pin_mismatch(
    world: tuple[config.Config, FakePrism],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg, prism = world
    assert gtpin.check_db(cfg.gos_home)[0]  # no DB yet: a note
    baseline(cfg, prism)
    ok, line = gtpin.check_db(cfg.gos_home)
    assert ok and "matches" in line
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{cfg.gos_home.as_posix()}"\n', encoding="utf-8"
    )
    out = io.StringIO()
    assert build.main(toml, None, out) == 0, out.getvalue()
    monkeypatch.setattr(config, "GT_PIN", NEW_GT)  # Play's GT is no longer the pin
    out = io.StringIO()
    assert build.main(toml, None, out) == 1
    text = out.getvalue()
    assert "!!! GT PIN MISMATCH" in text and "the DB was built" in text
    ok, line = gtpin.check_db(cfg.gos_home)
    assert not ok and "GT PIN MISMATCH" in line


def test_finished_sandboxes_are_pruned_when_the_next_update_starts(
    world: tuple[config.Config, FakePrism],
) -> None:
    cfg, prism = world
    baseline(cfg, prism)
    u = playbook.start(cfg.gos_home, "2.9.1", simulate=False)
    backup_zip(cfg)
    update_lab(cfg)
    assert playbook.walk(ctx(cfg, prism, io.StringIO()), u) == 3
    assert u.sandbox.is_dir()
    assert playbook.prune_sandboxes(cfg.gos_home) == []  # still open: kept
    playbook.abandon(u, "test")
    assert playbook.prune_sandboxes(cfg.gos_home)
    assert not u.sandbox.exists() and (u.folder / "diff.txt").is_file()


def test_play_update_messages_name_lab_as_the_source(
    world: tuple[config.Config, FakePrism],
) -> None:
    cfg, _ = world
    shutil.rmtree(cfg.instance("lab") / ".minecraft" / "scripts")
    plan, refusals = playupdate.plan_update(cfg)
    assert plan is not None and not refusals
    assert plan.notes == ["Lab has no scripts/ folder, so scripts/ is left as it is"]


def test_the_inbox_note_is_searchable_at_once(
    world: tuple[config.Config, FakePrism],
) -> None:
    cfg, prism = world
    baseline(cfg, prism)
    vault_deploy.deploy(cfg.gos_home / "vault")
    u = playbook.start(cfg.gos_home, "2.9.1", simulate=False)
    backup_zip(cfg)
    update_lab(cfg)
    assert playbook.walk(ctx(cfg, prism, io.StringIO()), u) == 3
    conn = sqlite3.connect(access.db_path(cfg.gos_home))
    try:
        rows = conn.execute(
            "SELECT path FROM note_fts WHERE note_fts MATCH 'adopt'"
        ).fetchall()
    finally:
        conn.close()
    assert [r[0][:9] for r in rows] == ["inbox/ib-"]


def test_the_pin_has_no_stale_copies() -> None:
    """The GT pin-bump procedure's other copies (docs/runbooks/pack_update.md) agree."""
    from gregtec.ingest import extractor

    assert extractor.PINS[extractor.GT] == config.GT_PIN
    assert extractor.PACK_VERSION == config.PACK_PIN
    gradle = (extractor.REPO_ROOT / "mod" / "dependencies.gradle").read_text(
        encoding="utf-8"
    )
    assert f"GT5-Unofficial:{config.GT_PIN}:dev" in gradle
    claude = (extractor.REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert f"GT pin {config.GT_PIN} (GTNH {config.PACK_PIN})" in claude
