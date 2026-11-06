"""The export-run diff (WO-1.4, D122) on small self-written manifests in the contract's shape."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from gregtec import config
from gregtec.ingest import fingerprint, rundiff

SHA = {c: c * 64 for c in "abcdef"}
BASE: dict[str, Any] = {
    "format": "gregteclink-export/1",
    "run_id": "20261101T000000Z",
    "created_utc": "2026-11-01T00:00:00Z",
    "complete": True,
    "mode": "autoexport",
    "world": "gregteclink-autoexport",
    "side": "client",
    "minecraft": "1.7.10",
    "forge": "10.13.4.1614",
    "java": {"version": "25.0.1", "vendor": "x"},
    "pack_version": "2.9.0",
    "gt_version": "5.09.54.265",
    "nei_version": "2.8.172-GTNH",
    "gregteclink": {"version": "362a22e5cbb7"},
    "mods": [
        {
            "modid": "gregtech",
            "version": "5.09.54.265",
            "file": "mods/gt.jar",
            "sha256": SHA["a"],
        },
        {
            "modid": "tst",
            "version": "0.8.0",
            "file": "mods/tst.jar",
            "sha256": SHA["b"],
        },
        {
            "modid": "ph",
            "version": "0.2.1p0",
            "file": "mods/ph.jar",
            "sha256": SHA["c"],
        },
    ],
    "files": {
        "gt_recipes.jsonl": {"sha256": SHA["a"], "bytes": 100, "lines": 10},
        "items.jsonl": {"sha256": SHA["b"], "bytes": 50, "lines": 5},
        "nei/Assembler.jsonl": {"sha256": SHA["c"], "bytes": 20, "lines": 2},
    },
    "counts": {"recipes": 10, "items": 5, "nei_recipes": 2},
    "parts": {
        "gt_dump": "ok",
        "machines": "ok",
        "refs": "ok",
        "nei": "ok",
        "autoexport": "ok",
    },
}


def write_run(root: Path, manifest: dict[str, Any]) -> Path:
    folder = root / manifest["run_id"]
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def changed_manifest() -> dict[str, Any]:
    m = copy.deepcopy(BASE)
    m["run_id"], m["created_utc"] = "20261106T000000Z", "2026-11-06T00:00:00Z"
    m["gregteclink"]["version"] = "cf98895e7d61"
    m["mods"][1]["version"] = "0.8.1"  # tst updated
    m["mods"][2]["sha256"] = SHA["d"]  # ph re-jarred, same version
    m["mods"].append(
        {
            "modid": "gtnl",
            "version": "0.2.8",
            "file": "mods/gtnl.jar",
            "sha256": SHA["e"],
        }
    )
    del m["mods"][0]  # gregtech gone (only to test removal)
    m["files"]["gt_recipes.jsonl"] = {"sha256": SHA["f"], "bytes": 120, "lines": 12}
    del m["files"]["nei/Assembler.jsonl"]
    m["files"]["nei/Assembler_2.jsonl"] = {"sha256": SHA["c"], "bytes": 20, "lines": 2}
    m["counts"]["recipes"] = 12
    m["parts"]["nei"] = "failed"
    m["part_errors"] = {"nei": "NEI was not ready"}
    return m


def test_diff_reports_each_kind_of_change(tmp_path: Path) -> None:
    old = rundiff.load_run(write_run(tmp_path, BASE))
    new = rundiff.load_run(write_run(tmp_path, changed_manifest()))
    play_old = (("mods/a.jar", SHA["a"]), ("mods/b.jar", SHA["b"]))
    play_new = (("mods/a.jar", SHA["c"]), ("mods/c.jar", SHA["d"]))
    d = rundiff.diff(old, new, play_old, play_new)
    assert d.versions == [("gregteclink", "362a22e5cbb7", "cf98895e7d61")]
    assert d.mods_added == ["gtnl 0.2.8 (mods/gtnl.jar)"]
    assert d.mods_removed == ["gregtech 5.09.54.265 (mods/gt.jar)"]
    assert d.mods_changed == [
        "ph 0.2.1p0: jar changed (mods/ph.jar)",
        "tst 0.8.0 -> 0.8.1",
    ]
    assert d.counts == [("recipes", 10, 12)]
    assert d.parts == [("nei", "ok", "failed")]
    assert d.files_changed == ["gt_recipes.jsonl: lines 10 -> 12, bytes 100 -> 120"]
    assert d.files_added == ["nei/Assembler_2.jsonl"]
    assert d.files_removed == ["nei/Assembler.jsonl"]
    assert d.files_same == 1
    assert d.play == fingerprint.Changes(
        ("mods/c.jar",), ("mods/b.jar",), ("mods/a.jar",)
    )
    assert "new run's error for nei: NEI was not ready" in d.notes
    text = rundiff.report(d)
    for line in [
        "Play: 1 added, 1 removed, 1 changed",
        "  + mods/c.jar",
        "  ~ tst 0.8.0 -> 0.8.1",
        "  recipes: 10 -> 12 (+2)",
        "  nei: ok -> failed",
        "Files: 1 unchanged, 1 changed, 1 added, 1 removed",
    ]:
        assert line in text.splitlines()
    assert d.summary().startswith("20261101T000000Z -> 20261106T000000Z: Play 1 added")


def test_same_counts_only_hashes_is_flagged(tmp_path: Path) -> None:
    other = copy.deepcopy(BASE)
    other["run_id"] = "20261102T000000Z"
    other["files"]["gt_recipes.jsonl"]["sha256"] = SHA["f"]
    d = rundiff.diff(
        rundiff.load_run(write_run(tmp_path, BASE)),
        rundiff.load_run(write_run(tmp_path, other)),
    )
    assert d.play is None
    assert any("different order" in n for n in d.notes)
    text = rundiff.report(d)
    assert "Versions: unchanged" in text and "Counts: unchanged" in text
    assert "no fingerprint listing recorded" in text


def test_long_file_lists_are_cut(tmp_path: Path) -> None:
    other = copy.deepcopy(BASE)
    other["run_id"] = "20261102T000000Z"
    for i in range(rundiff.FILES_LISTED + 5):
        other["files"][f"nei/h{i:03d}.jsonl"] = {
            "sha256": SHA["d"],
            "bytes": 1,
            "lines": 1,
        }
    d = rundiff.diff(
        rundiff.load_run(write_run(tmp_path, BASE)),
        rundiff.load_run(write_run(tmp_path, other)),
    )
    assert "  + ... and 5 more (see the manifests)" in rundiff.report(d).splitlines()


def test_load_run_refuses_what_isnt_a_run(tmp_path: Path) -> None:
    folder = tmp_path / "x"
    folder.mkdir()
    with pytest.raises(rundiff.RunError):
        rundiff.load_run(folder)
    (folder / "manifest.json").write_text("{bad")
    with pytest.raises(rundiff.RunError):
        rundiff.load_run(folder)
    (folder / "manifest.json").write_text('{"format": "other/1"}')
    with pytest.raises(rundiff.RunError):
        rundiff.load_run(folder)


def test_find_run_in_gos_home_then_export(tmp_path: Path) -> None:
    export_game = tmp_path / "instances" / "GTNH-Export" / ".minecraft"
    in_export = write_run(export_game / "gregteclink" / "exports", BASE)
    cfg = config.Config(
        prism_instances=tmp_path / "instances", gos_home=tmp_path / "home"
    )
    assert rundiff.find_run(cfg, BASE["run_id"]) == in_export
    collected = write_run(tmp_path / "home" / "data" / "exports", BASE)
    assert rundiff.find_run(cfg, BASE["run_id"]) == collected
    assert rundiff.find_run(cfg, str(in_export)) == in_export
    with pytest.raises(rundiff.RunError):
        rundiff.find_run(cfg, "20200101T000000Z")
    with pytest.raises(rundiff.RunError):
        rundiff.find_run(cfg, "../../etc")


def test_diff_runs_uses_the_recorded_listings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    a = fingerprint.Fingerprint.of((("mods/a.jar", SHA["a"]),))
    b = fingerprint.Fingerprint.of((("mods/a.jar", SHA["b"]),))
    fingerprint.record_export(home, BASE["run_id"], a, a)
    new = changed_manifest()
    fingerprint.record_export(home, new["run_id"], b, b)
    d = rundiff.diff_runs(
        home,
        rundiff.load_run(write_run(tmp_path, BASE)),
        rundiff.load_run(write_run(tmp_path, new)),
    )
    assert d.play == fingerprint.Changes(changed=("mods/a.jar",))
