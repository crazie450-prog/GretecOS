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
import sqlite3
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from gregtec import config, playhooks
from gregtec.db import access, build
from gregtec.ingest import collect, fingerprint
from gregtec.packupdate import diff, gtpin, plans, playbook, playupdate, releases

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

    out.truncate(0)
    assert playbook.walk(c, u) == 3  # lab, lab_export, diff, then "adopt?"
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
    out.truncate(0)
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
