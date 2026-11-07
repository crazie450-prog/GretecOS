"""WO-1.9's acceptance on a real export: the pack-update playbook with a simulated 2.9.x.

The client harness's run (``./gradlew runClient25 -Pharness``, mod/README.md: GT 5.09.54.265's
dev runtime, NEI, crops and the full ore dictionary) is the pack Play has. :func:`alter` makes a
"GT 5.09.54.266" run from a copy of it, as a changed GT jar would: three GT recipes changed (one
removed, one rebalanced, one added), one crop's tier raised and one oredict name added, with the
manifest re-hashed so the run reads as a genuine export. Three fake Prism instances in tmp_path
stand in for Play, Lab and Export, and a fake Prism "launch" exports the altered run when Export
holds the new GT jar (Lab synced into it), else the original. Then the whole checklist runs:
the "adopt?" prompt, the GT pin refusal, Clayton's Play script on the temp Play, Play's export,
and the plan flags, checked against fixture plans that cite a changed recipe, a removed one, an
unchanged one and the changed crop.

As the other harness tests, this runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after
the harnesses). Nothing is written outside tmp_path; the harness's run is only read.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sqlite3
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from gregtec import config, playhooks
from gregtec.db import access, build
from gregtec.ingest import collect, fingerprint
from gregtec.packupdate import diff, plans, playbook, playupdate, releases

REPO = Path(__file__).resolve().parents[2]
NEW_GT = "5.09.54.266"
MAP = "gt.recipe.blastfurnace"
CROP = "cropsnh:allium"
ORE = "gosSimulatedOre"
OLD_JAR, NEW_JAR = "gregtech-5.09.54.265.jar", "gregtech-5.09.54.266.jar"


def harness_run() -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    root = REPO / "mod" / "run" / "client" / collect.EXPORTS
    runs, _ = collect.find_runs(root, allow_harness=True) if root.is_dir() else ([], [])
    if not runs:
        pytest.fail(f"no client harness run in {root} (mod/README.md)")
    return runs[0].path


def _rehash(run: Path, rel: str, m: dict[str, Any]) -> None:
    data = (run / rel).read_bytes()
    m["files"][rel] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "lines": data.count(b"\n"),
    }


def copy_run(src: Path, dst: Path, run_id: str, hours: int) -> dict[str, Any]:
    """``src`` as run ``run_id``, ``hours`` later; returns its manifest, still to be written."""
    shutil.copytree(src, dst)
    m: dict[str, Any] = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    created = dt.datetime.strptime(m["created_utc"], "%Y-%m-%dT%H:%M:%SZ")
    m["created_utc"] = (created + dt.timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    m["run_id"] = run_id
    return m


def write_manifest(dst: Path, m: dict[str, Any]) -> None:
    (dst / "manifest.json").write_text(
        json.dumps(m, indent=1), encoding="utf-8", newline="\n"
    )


def alter(src: Path, dst: Path, run_id: str, hours: int) -> None:
    """The simulated GT update (module docstring)."""
    m = copy_run(src, dst, run_id, hours)
    lines = (dst / "gt_recipes.jsonl").read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    done = set()
    for line in lines:
        d = json.loads(line)
        if d.get("map") == MAP and d.get("idx") == 0 and "removed" not in done:
            done.add("removed")
            continue
        if d.get("map") == MAP and d.get("idx") == 1 and "changed" not in done:
            done.add("changed")
            d["duration"] = int(d["duration"]) * 4 // 5
            new = json.loads(json.dumps(d))
            new["idx"] = 10_000
            new["out"][0]["n"] = int(new["out"][0]["n"]) + 1
            kept += [
                json.dumps(d, separators=(",", ":")),
                json.dumps(new, separators=(",", ":")),
            ]
            continue
        kept.append(line)
    assert done == {"removed", "changed"}, "no EBF recipes in the run"
    (dst / "gt_recipes.jsonl").write_text(
        "\n".join(kept) + "\n", encoding="utf-8", newline="\n"
    )
    _rehash(dst, "gt_recipes.jsonl", m)  # counts.recipes stays: one line out, one in
    path = dst / "crops" / "cropRegistry.csv"
    rows = path.read_text(encoding="utf-8").split("\n")
    for i, row in enumerate(rows):
        if row.startswith(CROP + ","):
            cells = row.split(",")
            cells[11] = str(int(cells[11]) + 1)  # Tier
            rows[i] = ",".join(cells)
    path.write_text("\n".join(rows), encoding="utf-8", newline="")
    _rehash(dst, "crops/cropRegistry.csv", m)
    path = dst / "oredict.jsonl"
    names = path.read_text(encoding="utf-8").splitlines()
    names.append(
        json.dumps({"ore": ORE, "members": [{"item": "minecraft:dirt", "meta": 0}]})
    )
    names.sort(key=lambda s: json.loads(s)["ore"])
    path.write_text("\n".join(names) + "\n", encoding="utf-8", newline="\n")
    _rehash(dst, "oredict.jsonl", m)
    m["counts"]["oredict"] = len(names)
    m["counts"]["oredict_members"] = int(m["counts"]["oredict_members"]) + 1
    m["gt_version"] = NEW_GT
    write_manifest(dst, m)


def make_instance(root: Path, name: str) -> Path:
    inst = root / name
    game = inst / ".minecraft"
    for d in ("mods", "scripts", "config/GTNewHorizons"):
        (game / d).mkdir(parents=True)
    (inst / "instance.cfg").write_text("InstanceType=OneSix\n", encoding="utf-8")
    (inst / "mmc-pack.json").write_text('{"components": []}', encoding="utf-8")
    (game / "config/GTNewHorizons/dreamcraft.cfg").write_text("x\n", encoding="utf-8")
    (game / "mods" / OLD_JAR).write_bytes(b"gt 265")
    return inst


class FakePrism:
    def __init__(self, cfg: config.Config, src: Path) -> None:
        self.cfg, self.src, self.n = cfg, src, 0

    def launch(self, argv: Sequence[str]) -> None:
        assert argv[-1] == self.cfg.export
        game = self.cfg.instance("export") / ".minecraft"
        exports = game / "gregteclink" / "exports"
        self.n += 1
        run_id = f"sim-{self.n}"
        if (game / "mods" / NEW_JAR).is_file():
            alter(self.src, exports / run_id, run_id, self.n)
        else:
            write_manifest(
                exports / run_id, copy_run(self.src, exports / run_id, run_id, 0)
            )

    def clients(self, cfg: config.Config) -> list[str]:
        return []


def no_network(url: str, headers: object) -> releases.Response:
    return releases.Response(404)


def ref_where(db: Path, run_key: str, sql: str, *args: object) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [
            str(r[0])
            for r in conn.execute(
                "SELECT r.ref FROM recipe r JOIN export_run e ON e.id = r.run_id"
                f" WHERE e.run_key = ? AND {sql} ORDER BY r.ref",
                (run_key, *args),
            )
        ]
    finally:
        conn.close()


def ebf_hashes(db: Path, run_key: str) -> dict[str, str]:
    conn = sqlite3.connect(db)
    try:
        return {
            str(r[0]): str(r[1])
            for r in conn.execute(
                "SELECT r.ref, r.content_hash FROM recipe r JOIN export_run e"
                " ON e.id = r.run_id WHERE e.run_key = ? AND r.ref LIKE ?",
                (run_key, f"r:{MAP}/%"),
            )
        }
    finally:
        conn.close()


def test_a_simulated_gt_update_through_the_whole_playbook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = harness_run()
    root = tmp_path / "instances"
    for name in ("GTNH-Play", "GTNH-Lab", "GTNH-Export"):
        make_instance(root, name)
    cfg = config.Config(
        prism_instances=root,
        gos_home=tmp_path / "home",
        gos_app=tmp_path / "app",
        prism_root=tmp_path / "prism",
    )
    home = cfg.gos_home
    prism = FakePrism(cfg, src)
    quiet: dict[str, Any] = {"sleep": lambda s: None, "poll_s": 0.0}

    # Play's export, the normal way, into the main DB.
    out = io.StringIO()
    assert (
        playhooks.run_export(
            cfg,
            out,
            launch=prism.launch,
            clients=prism.clients,
            allow_harness=True,
            **quiet,
        )
        == 0
    ), out.getvalue()
    export_game = cfg.instance("export") / ".minecraft"
    collect.collect(export_game, home, 5, allow_harness=True)
    last = fingerprint.last_export(home)
    assert last is not None
    build.build(home, fingerprint=last["fingerprint"], allow_harness=True)
    db = access.db_path(home)
    base = diff.side(db, None, home)

    # Fixture plans on that run: an EBF recipe the update rebalances, one it removes, one it
    # keeps, and the crop it changes.
    ebf = ref_where(db, base.run_key, "r.ref LIKE ?", f"r:{MAP}/%")
    plan_dir = home / "data" / "plans"
    plan_dir.mkdir(parents=True)

    def add(pid: str, cite: list[str]) -> None:
        (plan_dir / f"{pid}.json").write_text(
            json.dumps(
                {"format": plans.FORMAT, "id": pid, "run": base.run_key, "cites": cite}
            ),
            encoding="utf-8",
        )
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "INSERT INTO plan(id, status, json_path, run_id)"
                " VALUES (?, 'accepted', ?, ?)",
                (pid, f"data/plans/{pid}.json", base.run_id),
            )
            conn.commit()
        finally:
            conn.close()

    # Every EBF recipe in one plan, so whichever the update touches, it must be flagged; and a
    # plan citing recipes of another map, which the update leaves alone.
    other = ref_where(db, base.run_key, "r.ref LIKE 'r:gt.recipe.mixer/%'")[:20]
    add("pl-ebf", ebf)
    add("pl-crop", [f"crop:{CROP}"])
    add("pl-same", other)
    assert ebf and other

    u = playbook.start(home, "2.9.1", simulate=False)
    out = io.StringIO()
    c = playbook.Ctx(
        cfg,
        out,
        fetch=no_network,
        launch=prism.launch,
        clients=prism.clients,
        tweak_problems=lambda inst: [],
        allow_harness=True,
        **quiet,
    )
    (home / playbook.WORLD_BACKUPS).mkdir(parents=True)
    with zipfile.ZipFile(home / playbook.WORLD_BACKUPS / "backup.zip", "w") as z:
        z.writestr("level.dat", b"x")
    lab_mods = cfg.instance("lab") / ".minecraft" / "mods"
    (lab_mods / OLD_JAR).unlink()
    (lab_mods / NEW_JAR).write_bytes(b"gt 266")  # the changed GT jar in Lab

    assert playbook.walk(c, u) == 3, out.getvalue()
    assert u.status("adopt") == "waiting"
    text = out.getvalue()
    assert "ADOPT? Adopt GTNH 2.9.1?" in text and "GT PIN MISMATCH" in text
    report = (u.folder / "diff.txt").read_text(encoding="utf-8")
    assert "1 added, 1 removed, 1 changed (same key, other content)" in report
    assert "ticks 1000 -> 800" in report
    assert f"~ crop crop:{CROP} tier:" in report
    assert f"+ ore:{ORE}" in report
    assert sorted(u.step("diff")["data"]["flagged_preview"]) == ["pl-crop", "pl-ebf"]
    lab_run = u.step("lab_export")["data"]["run"]
    new_ebf = ref_where(
        u.sandbox / access.DB_FILE, lab_run, "r.ref LIKE ?", f"r:{MAP}/%"
    )
    assert len(set(ebf) - set(new_ebf)) == 1 and len(set(new_ebf) - set(ebf)) == 1
    # One plan per recipe the update touched, and one citing an EBF recipe it left alone:
    # the flags name exactly the touched ones, not only a plan citing the whole map.
    old_h = ebf_hashes(db, base.run_key)
    new_h = ebf_hashes(u.sandbox / access.DB_FILE, lab_run)
    [removed] = set(old_h) - set(new_h)
    [changed] = [k for k in old_h if k in new_h and old_h[k] != new_h[k]]
    kept = next(k for k in sorted(old_h) if new_h.get(k) == old_h[k])
    add("pl-removed", [removed])
    add("pl-rebalanced", [changed])
    add("pl-ebf-kept", [kept])
    playbook.redo(u, "diff")
    assert playbook.walk(c, u) == 3, out.getvalue()
    assert sorted(u.step("diff")["data"]["flagged_preview"]) == [
        "pl-crop",
        "pl-ebf",
        "pl-rebalanced",
        "pl-removed",
    ]

    with pytest.raises(playbook.UpdateError, match="GT PIN MISMATCH"):
        playbook.answer(home, u, True)
    monkeypatch.setattr(config, "GT_PIN", NEW_GT)  # the GT pin bump, deployed
    playbook.answer(home, u, True)
    assert playbook.walk(c, u) == 3 and u.status("play") == "waiting"
    log = io.StringIO()
    assert playupdate.run(cfg, None, True, log, prism.clients) == 0, log.getvalue()
    u = playbook.find(home, u.id)
    out.seek(0)
    out.truncate()
    assert playbook.walk(c, u) == 0, out.getvalue()
    new = diff.side(db, None, home)
    assert new.gt_version == NEW_GT and new.run_key != base.run_key
    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT id, affected_run FROM plan"))
    conn.close()
    assert rows == {
        "pl-ebf": new.run_id,
        "pl-crop": new.run_id,
        "pl-same": None,
        "pl-removed": new.run_id,
        "pl-rebalanced": new.run_id,
        "pl-ebf-kept": None,
    }
    # The Lab run never reached the main DB: only Play's two runs are there.
    conn = sqlite3.connect(db)
    keys = {str(r[0]) for r in conn.execute("SELECT run_key FROM export_run")}
    conn.close()
    assert lab_run not in keys and collect.lab_runs(home) == {lab_run}
    assert playbook.adopted(home)["version"] == "2.9.1"
