"""``gos export collect`` (WO-1.2) on small self-authored export runs in fake Prism instances.

The runs follow docs/contracts/gregteclink-export-v1.md in shape only: a few made-up lines,
never pack data.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

import pytest

from gregtec import cli, config
from gregtec.gamestate import safe_open
from gregtec.ingest import collect

OLD = (
    1_600_000_000_000_000_000  # a fixed mtime in ns, so an unchanged file is checkable
)
GT_FILES = {
    "gt_recipe_maps.json": b'[{"name":"gt.recipe.test","count":1}]\n',
    "gt_recipes.jsonl": b'{"map":"gt.recipe.test","idx":0,"eut":30,"duration":20}\n',
    "gt_machines.jsonl": b'{"mte_id":1,"item":"gregtech:gt.blockmachines","meta":1}\n',
    "items.jsonl": b'{"item":"test:ingot","meta":0,"name":"Test Ingot"}\n'
    b'{"item":"test:dust","meta":0,"name":"Test Dust"}\n',
    "fluids.jsonl": b'{"fluid":"water","name":"Water"}\n',
}
NEI_FILES = {  # NEI's names have spaces and _2 suffixes; Windows line ends (contract)
    "nei/Blast Furnace.jsonl": b'{"handler":"gt.recipe.blastfurnace","ingredients":[]}\r\n',
    "nei/Blast Furnace_2.jsonl": b'{"handler":"other.blast","ingredients":[]}\r\n'
    b'{"handler":"other.blast","results":[]}\r\n',
}


RENAME_DELAY = collect.RENAME_DELAY  # before _fast_retries sets it to 0


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_open, "BACKOFF_S", 0)
    monkeypatch.setattr(collect, "RENAME_DELAY", 0)


def entry(data: bytes) -> dict[str, object]:
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "lines": data.count(b"\n"),
    }


def make_run(
    exports: Path,
    name: str,
    created: str,
    files: dict[str, bytes] | None = None,
    **manifest: object,
) -> Path:
    """A run folder as the mod leaves it: the files, then manifest.json listing them."""
    files = {**GT_FILES, **NEI_FILES} if files is None else files
    run = exports / name
    for rel, data in files.items():
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_bytes(data)
    m: dict[str, object] = {
        "format": "gregteclink-export/1",
        "run_id": name.removesuffix(".partial"),
        "created_utc": created,
        "complete": True,
        "mode": "autoexport",
        "gt_version": "5.09.54.265",
        "nei_version": "2.8.172-GTNH",
        "gregteclink": {"version": "cf98895"},
        "files": {rel: entry(data) for rel, data in files.items()},
        "counts": {"recipe_maps": 1, "recipes": 1, "nei_files": 2, "nei_recipes": 3},
        "parts": {"gt_dump": "ok", "nei": "ok", "autoexport": "ok"},
        **manifest,
    }
    (run / collect.MANIFEST).write_bytes(json.dumps(m).encode())
    for path in [run, *run.rglob("*")]:
        os.utime(path, ns=(OLD, OLD))
    return run


def snapshot(root: Path) -> dict[str, tuple[bytes, int] | None]:
    """Every file's bytes and mtime, and every folder (None), under ``root``."""
    out: dict[str, tuple[bytes, int] | None] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = None if p.is_dir() else (p.read_bytes(), p.stat().st_mtime_ns)
    return out


class Env:
    def __init__(self, tmp: Path, keep: int | None = None) -> None:
        self.instances = tmp / "instances"
        games = {}
        for name, game_dir in (
            ("GTNH-Play", ".minecraft"),
            ("GTNH-Export", ".minecraft"),
            ("GTNH-Lab", "minecraft"),
        ):
            (self.instances / name / game_dir).mkdir(parents=True)
            (self.instances / name / "instance.cfg").write_text(f"name={name}\n")
            games[name] = self.instances / name / game_dir
        self.play, self.export, self.lab = games.values()
        self.exports = self.export / "gregteclink" / "exports"
        self.exports.mkdir(parents=True)
        for game in (self.play, self.lab):  # their own exports must never be read
            make_run(game / "gregteclink" / "exports", "29990101T000000Z", "2999")
        self.home = tmp / "GregtecOS"
        self.dest = self.home / "data" / "exports"
        self.config = tmp / "gos.toml"
        export = "" if keep is None else f"[export]\nkeep_collected = {keep}\n"
        self.write_config(
            f'[paths]\nprism_instances = "{self.instances.as_posix()}"\n'
            f'gos_home = "{self.home.as_posix()}"\n{export}'
        )
        self.before: dict[str, dict[str, tuple[bytes, int] | None]] = {}

    def write_config(self, text: str) -> None:
        self.config.write_text(text)

    def collect(self, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
        self.before = {
            "play": snapshot(self.play.parent),
            "lab": snapshot(self.lab.parent),
        }
        code = cli.main(["export", "collect", "--config", str(self.config)])
        out = capsys.readouterr().out
        assert snapshot(self.play.parent) == self.before["play"]
        assert snapshot(self.lab.parent) == self.before["lab"]
        return code, out

    def collected(self) -> list[str]:
        return sorted(p.name for p in self.dest.iterdir()) if self.dest.is_dir() else []


def test_collects_the_newest_complete_run_and_a_second_collect_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261101T000000Z", "2026-11-01T00:00:00Z")
    newest = make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    # Newer, but not complete runs: never collected.
    make_run(env.exports, "20261103T000000Z.partial", "2026-11-03T00:00:00Z")
    make_run(env.exports, "20261104T000000Z", "2026-11-04T00:00:00Z", complete=False)
    make_run(env.exports, "20261105T000000Z", "2026-11-05T00:00:00Z", format="x/2")
    (env.exports / "20261106T000000Z").mkdir()  # no manifest
    export_before = snapshot(env.export.parent)

    code, out = env.collect(capsys)
    assert code == 0, out
    assert env.collected() == ["20261102T000000Z"]
    assert snapshot(env.dest / "20261102T000000Z").keys() == snapshot(newest).keys()
    for rel, data in {**GT_FILES, **NEI_FILES}.items():
        assert (env.dest / "20261102T000000Z" / rel).read_bytes() == data
    manifest = (newest / collect.MANIFEST).read_bytes()
    assert (env.dest / "20261102T000000Z" / collect.MANIFEST).read_bytes() == manifest
    assert snapshot(env.export.parent) == export_before  # Export is only read
    assert "skipped 20261103T000000Z.partial: still being written" in out
    assert 'skipped 20261104T000000Z: incomplete (manifest "complete"' in out
    assert "skipped 20261105T000000Z: format 'x/2', not gregteclink-export/1" in out
    assert "skipped 20261106T000000Z: no manifest.json" in out
    assert "NEI: ok, 2 files, 3 recipes" in out
    assert f"Collected into {env.dest / '20261102T000000Z'}" in out
    assert "7 files" in out

    copied = snapshot(env.dest)
    code, out = env.collect(capsys)
    assert code == 0, out
    assert "Already collected" in out and "nothing written" in out
    assert snapshot(env.dest) == copied  # mtimes too: nothing rewritten


def test_ties_on_created_utc_go_to_the_later_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    make_run(env.exports, "20261102T000000Z-2", "2026-11-02T00:00:00Z")
    code, out = env.collect(capsys)
    assert code == 0, out
    assert env.collected() == ["20261102T000000Z-2"]


def _damage(run: Path, how: str) -> None:
    path = run / "gt_recipes.jsonl"
    data = path.read_bytes()
    if how == "same size":
        path.write_bytes(data.replace(b"30", b"31"))
    elif how == "extra line":
        path.write_bytes(data + b"\n")
    elif how == "truncated":
        path.write_bytes(data[:10])
    else:
        path.unlink()


@pytest.mark.parametrize("how", ["same size", "extra line", "truncated", "missing"])
def test_a_run_that_doesnt_match_its_manifest_is_not_collected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], how: str
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261101T000000Z", "2026-11-01T00:00:00Z")
    _damage(make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z"), how)
    code, out = env.collect(capsys)
    assert code == 1
    assert "Not collected: run 20261102T000000Z: gt_recipes.jsonl" in out
    expect = {
        "same size": "doesn't match the manifest in 3 reads",
        "extra line": "is 57 bytes; the manifest says 56; not collected",
        "truncated": "is 10 bytes; the manifest says 56; not collected",
        "missing": "is missing",
    }[how]
    assert expect in out
    # Nothing of it reached GOS_HOME, and no older run was taken in its place.
    assert env.collected() == []


def test_a_partial_read_is_retried(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    real = safe_open.safe_open
    reads: list[str] = []

    @contextlib.contextmanager
    def flaky(path: safe_open.StrPath) -> Iterator[BinaryIO]:
        reads.append(Path(path).name)
        with real(path) as f:
            if reads[-1] == "gt_recipes.jsonl" and reads.count(reads[-1]) == 1:
                yield io.BytesIO(f.read()[:5])  # the first read sees half a file
            else:
                yield f

    monkeypatch.setattr(safe_open, "safe_open", flaky)
    code, out = env.collect(capsys)
    assert code == 0, out
    assert reads.count("gt_recipes.jsonl") == 2
    data = (env.dest / "20261102T000000Z" / "gt_recipes.jsonl").read_bytes()
    assert data == GT_FILES["gt_recipes.jsonl"]


class Recording(io.BytesIO):
    """A file whose reads are recorded, for checking how much collect asks for."""

    def __init__(self, data: bytes, sizes: list[int | None]) -> None:
        super().__init__(data)
        self.sizes = sizes

    def read(self, size: int | None = -1) -> bytes:
        self.sizes.append(size)
        return super().read(size)


def test_a_file_of_the_wrong_size_is_refused_unread(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    run = make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    with open(run / "gt_recipes.jsonl", "r+b") as f:
        f.truncate(50 << 20)  # sparse on most file systems; gos-dev can write Export
    real = safe_open.safe_open
    reads: list[str] = []

    @contextlib.contextmanager
    def counted(path: safe_open.StrPath) -> Iterator[BinaryIO]:
        reads.append(Path(path).name)
        with real(path) as f:
            yield f

    monkeypatch.setattr(safe_open, "safe_open", counted)
    code, out = env.collect(capsys)
    assert code == 1
    assert "gt_recipes.jsonl is 52,428,800 bytes; the manifest says 56" in out
    assert "gt_recipes.jsonl" not in reads
    assert env.collected() == []


def test_a_file_that_grows_after_its_size_check_is_read_only_one_byte_past_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    real = safe_open.safe_open
    sizes: list[int | None] = []

    @contextlib.contextmanager
    def growing(path: safe_open.StrPath) -> Iterator[BinaryIO]:
        if Path(path).name != "gt_recipes.jsonl":
            with real(path) as f:
                yield f
            return
        yield Recording(GT_FILES["gt_recipes.jsonl"] + b"x" * 1_000_000, sizes)

    monkeypatch.setattr(safe_open, "safe_open", growing)
    code, out = env.collect(capsys)
    assert code == 1
    assert "gt_recipes.jsonl doesn't match the manifest in 3 reads (57 bytes" in out
    assert sizes == [57] * 3  # never the whole million
    assert env.collected() == []


@pytest.mark.parametrize("how", ["nested", "huge"])
def test_a_deep_or_huge_manifest_is_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], how: str
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261101T000000Z", "2026-11-01T00:00:00Z")
    run = make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    with open(run / collect.MANIFEST, "wb") as f:
        if how == "nested":
            f.write(b"[" * 200_000 + b"]" * 200_000)  # RecursionError in json.loads
        else:
            f.truncate(collect.MANIFEST_LIMIT + 1)
    code, out = env.collect(capsys)
    assert code == 0, out
    expect = (
        "manifest.json unreadable: maximum recursion depth"
        if how == "nested"
        else "its manifest.json is over 16,777,216 bytes"
    )
    assert f"skipped 20261102T000000Z: {expect}" in out
    assert env.collected() == ["20261101T000000Z"]


@pytest.mark.parametrize(
    "rel",
    [
        "../outside.jsonl",
        "nei/../../outside.jsonl",
        "/outside.jsonl",
        "nei//x.jsonl",
        "nei\\x.jsonl",
        "C:outside.jsonl",
        "gt_recipes.jsonl:stream",
        "nei/bad\x1bname.jsonl",
        "manifest.json",
    ],
)
def test_a_name_outside_the_run_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], rel: str
) -> None:
    env = Env(tmp_path)
    run = make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    data = b"bait\n"
    (env.exports / "outside.jsonl").write_bytes(data)  # matches, but isn't in the run
    m = json.loads((run / collect.MANIFEST).read_bytes())
    m["files"][rel] = entry(data)
    (run / collect.MANIFEST).write_text(json.dumps(m))
    code, out = env.collect(capsys)
    assert code == 1
    assert "which is not a file name inside the run" in out
    assert "\x1b" not in out  # Export's text reaches the terminal without control codes
    assert env.collected() == []


@pytest.mark.parametrize(
    "bad",
    [
        {"sha256": "ab", "bytes": 1, "lines": 0},
        {"sha256": "A" * 64, "bytes": 1, "lines": 0},
        {"sha256": "a" * 64, "bytes": True, "lines": 0},
        {"sha256": "a" * 64, "bytes": 1, "lines": -1},
        "a" * 64,
    ],
)
def test_a_malformed_file_entry_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad: object
) -> None:
    env = Env(tmp_path)
    run = make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    m = json.loads((run / collect.MANIFEST).read_bytes())
    m["files"]["items.jsonl"] = bad
    (run / collect.MANIFEST).write_text(json.dumps(m))
    code, out = env.collect(capsys)
    assert code == 1
    assert "the manifest entry of items.jsonl has no valid sha256" in out
    assert env.collected() == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege")
@pytest.mark.parametrize("where", ["gregteclink", "exports", "nei", "file"])
def test_a_link_on_the_way_to_a_file_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    env = Env(tmp_path)
    run = make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    elsewhere = tmp_path / "elsewhere"
    if where in ("gregteclink", "exports"):
        link = env.exports.parent if where == "gregteclink" else env.exports
        link.rename(elsewhere)
        link.symlink_to(elsewhere, target_is_directory=True)
        expect = f"{link} is a link or junction"
    else:
        rel = "nei" if where == "nei" else "nei/Blast Furnace.jsonl"
        link = run / rel
        link.rename(elsewhere)
        link.symlink_to(elsewhere, target_is_directory=where == "nei")
        expect = f"run 20261102T000000Z: {link} is a link or junction"
    code, out = env.collect(capsys)
    assert code == 1
    assert expect in out
    assert env.collected() == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege")
def test_a_linked_run_folder_is_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261101T000000Z", "2026-11-01T00:00:00Z")
    (env.exports / "20261102T000000Z").symlink_to(
        env.play / "gregteclink" / "exports" / "29990101T000000Z",
        target_is_directory=True,
    )
    code, out = env.collect(capsys)
    assert code == 0, out
    assert "skipped 20261102T000000Z: a link or junction" in out
    assert env.collected() == ["20261101T000000Z"]


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions")
@pytest.mark.parametrize("where", ["gregteclink", "exports", "nei", "data/exports"])
def test_a_junction_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """mklink /J needs no privilege, so unlike the symlink tests this runs on Windows CI."""
    env = Env(tmp_path)
    run = make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    elsewhere = tmp_path / "elsewhere"
    link = {
        "gregteclink": env.exports.parent,
        "exports": env.exports,
        "nei": run / "nei",
        "data/exports": env.dest,
    }[where]
    if where == "data/exports":
        env.dest.parent.mkdir(parents=True)
        elsewhere.mkdir()
    else:
        link.rename(elsewhere)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(elsewhere)],
        check=True,
        capture_output=True,
    )
    assert link.is_junction()
    code, out = env.collect(capsys)
    assert code == 1
    if where == "data/exports":
        assert f"{link} is a link, a junction or not a folder" in out
        assert not any(elsewhere.iterdir())  # nothing written through it
    else:
        assert f"{link} is a link or junction" in out
        assert env.collected() == []


@pytest.mark.parametrize("what", ["file", "manifest"])
def test_a_hard_link_in_a_run_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], what: str
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261101T000000Z", "2026-11-01T00:00:00Z")
    run = make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    name = "items.jsonl" if what == "file" else collect.MANIFEST
    os.link(run / name, tmp_path / "elsewhere")  # no privilege needed, NTFS included
    code, out = env.collect(capsys)
    if what == "file":
        assert code == 1
        assert f"{run / 'items.jsonl'} is a hard link; refusing the run" in out
        assert env.collected() == []
    else:  # a run with a linked manifest is no candidate, as with a symlinked one
        assert code == 0, out
        assert "skipped 20261102T000000Z: its manifest.json is a link, a hard" in out
        assert env.collected() == ["20261101T000000Z"]


def test_unlisted_files_are_not_copied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    run = make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    (run / "Thumbs.db").write_bytes(b"x")
    (run / "nei" / "extra.jsonl").write_bytes(b"x\n")
    (run / "other").mkdir()
    code, out = env.collect(capsys)
    assert code == 0, out
    assert "not copied (not in the manifest): Thumbs.db" in out
    assert "not copied (not in the manifest): nei/extra.jsonl" in out
    assert "not copied (not in the manifest): other" in out
    dest = env.dest / "20261102T000000Z"
    assert not (dest / "Thumbs.db").exists()
    assert not (dest / "nei" / "extra.jsonl").exists()


def test_harness_runs_are_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261101T000000Z", "2026-11-01T00:00:00Z")
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z", mode="harness")
    code, out = env.collect(capsys)
    assert code == 0, out
    assert "skipped 20261102T000000Z: a harness run" in out
    assert env.collected() == ["20261101T000000Z"]


def test_a_manifest_naming_another_run_is_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261101T000000Z", "2026-11-01T00:00:00Z")
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z", run_id="renamed")
    code, out = env.collect(capsys)
    assert code == 0, out
    assert "manifest run_id 'renamed' isn't the folder's name" in out
    assert env.collected() == ["20261101T000000Z"]


def test_no_run_yet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = Env(tmp_path)
    code, out = env.collect(capsys)
    assert code == 1
    assert f"no complete run in {env.exports} (empty)" in out
    (env.exports.parent).rename(tmp_path / "gone")
    code, out = env.collect(capsys)
    assert code == 1
    assert "doesn't exist, so Export has made no run yet" in out


def test_nei_status_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(
        env.exports,
        "20261102T000000Z",
        "2026-11-02T00:00:00Z",
        files=GT_FILES,
        parts={"gt_dump": "ok", "nei": "failed"},
        part_errors={"nei": "NEI logged 'Error dumping recipes'\x1b[31m\x9b0m"},
    )
    code, out = env.collect(capsys)
    assert code == 0, out
    assert "NEI: failed: NEI logged 'Error dumping recipes'?[31m?0m" in out  # C0 and C1
    assert "5 files" in out


def test_another_run_under_the_same_id_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    other = make_run(env.dest, "20261102T000000Z", "2026-11-02T00:00:00Z", mode="x")
    before = snapshot(other)
    code, out = env.collect(capsys)
    assert code == 1
    assert f"{other} holds another run with the id 20261102T000000Z" in out
    assert snapshot(other) == before
    assert env.collected() == ["20261102T000000Z"]


def test_a_copy_with_no_manifest_is_called_damaged_and_left_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    assert env.collect(capsys)[0] == 0
    dest = env.dest / "20261102T000000Z"
    (dest / collect.MANIFEST).unlink()
    before = snapshot(dest)
    code, out = env.collect(capsys)
    assert code == 1
    assert f"{dest} is there but has no readable manifest.json" in out
    assert "so it is damaged or no collected run" in out
    assert snapshot(dest) == before


def test_a_damaged_copy_is_collected_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    assert env.collect(capsys)[0] == 0
    dest = env.dest / "20261102T000000Z"
    (dest / "nei" / "Blast Furnace.jsonl").write_bytes(b"damaged\r\n")
    (dest / "items.jsonl").unlink()
    code, out = env.collect(capsys)
    assert code == 0, out
    assert "Collected again into" in out
    for rel, data in {**GT_FILES, **NEI_FILES}.items():
        assert (dest / rel).read_bytes() == data
    assert env.collected() == ["20261102T000000Z"]  # no trash or staging left


def test_keeps_the_newest_collected_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path, keep=2)
    env.dest.mkdir(parents=True)
    (env.dest / "notes").mkdir()  # not a collected run: never deleted
    (env.dest / "20200101T000000Z").mkdir()  # no manifest: never deleted
    (env.dest / ".gos-staging-x-1234").mkdir()  # left by an interrupted collect
    (env.dest / ".gos-trash-y-1234").mkdir()
    for day in range(1, 5):
        make_run(env.exports, f"2026110{day}T000000Z", f"2026-11-0{day}T00:00:00Z")
        code, out = env.collect(capsys)
        assert code == 0, out
    assert "pruned 20261102T000000Z" in out
    assert env.collected() == [
        "20200101T000000Z",
        "20261103T000000Z",
        "20261104T000000Z",
        "notes",
    ]


def test_prune_never_deletes_the_run_just_collected(tmp_path: Path) -> None:
    for day in range(1, 4):
        make_run(tmp_path, f"2026110{day}T000000Z", f"2026-11-0{day}T00:00:00Z")
    pruned, left = collect.prune(tmp_path, 1, "20261101T000000Z")
    assert (pruned, left) == (["20261102T000000Z"], [])
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "20261101T000000Z",
        "20261103T000000Z",
    ]


def test_an_interrupted_collect_leaves_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    real = collect._write_new
    calls: list[Path] = []

    def interrupted(staging: Path, rel: str, data: bytes) -> None:
        calls.append(staging / rel)
        if len(calls) == 3:
            raise KeyboardInterrupt
        real(staging, rel, data)

    monkeypatch.setattr(collect, "_write_new", interrupted)
    with pytest.raises(KeyboardInterrupt):
        env.collect(capsys)
    assert env.collected() == []
    monkeypatch.setattr(collect, "_write_new", real)
    assert env.collect(capsys)[0] == 0
    assert env.collected() == ["20261102T000000Z"]


def test_a_second_collect_is_refused_while_one_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    env.dest.mkdir(parents=True)
    staging = env.dest / ".gos-staging-20261102T000000Z-abcd"
    staging.mkdir()  # the running collect's, half written
    (staging / "items.jsonl").write_bytes(GT_FILES["items.jsonl"])
    with collect.lock(env.home):  # an OS lock: a second open of the file can't take it
        code, out = env.collect(capsys)
        assert code == 1
        lock = env.home / "data" / "locks" / "collect.lock"
        assert f"another gos export collect is running (it holds {lock})" in out
        assert (staging / "items.jsonl").is_file()  # its staging folder is untouched
        assert env.collected() == [staging.name]
    code, out = env.collect(capsys)  # released, so the next collect runs
    assert code == 0, out
    assert env.collected() == ["20261102T000000Z"]


@pytest.mark.parametrize("at", ["items.jsonl", "nei/Blast Furnace.jsonl"])
def test_a_staging_folder_deleted_mid_copy_fails_the_collect(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    at: str,
) -> None:
    """What a second collect's cleanup did before the lock: the copy must not carry on into a
    folder it makes anew, then rename a run with files missing into place."""
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    real = collect._write_new
    calls: list[str] = []

    def raced(staging: Path, rel: str, data: bytes) -> None:
        calls.append(rel)
        if rel == at:  # a file at the top, or the first in a new subfolder
            assert collect._cleanup(env.dest) == []  # deletes the staging folder
        real(staging, rel, data)

    monkeypatch.setattr(collect, "_write_new", raced)
    code, out = env.collect(capsys)
    assert code == 1
    assert "Not collected: [Errno 2]" in out
    assert env.collected() == []  # no run with files missing, and no staging folder


def test_a_held_folder_is_renamed_on_a_later_try(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    real = os.rename
    tries: list[str] = []

    def held(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        tries.append(Path(dst).name)
        if len(tries) < 3:
            raise PermissionError("a scanner holds a file in it")
        real(src, dst)

    monkeypatch.setattr(os, "rename", held)
    monkeypatch.setattr(collect, "RENAME_DELAY", RENAME_DELAY)
    sleeps: list[float] = []
    monkeypatch.setattr(collect.time, "sleep", sleeps.append)
    code, out = env.collect(capsys)
    assert code == 0, out
    assert tries == ["20261102T000000Z"] * 3
    assert sleeps == [1.0, 2.0]  # as the mod waits for the same scanner holds


@pytest.mark.parametrize("role", ["play", "lab"])
def test_an_export_that_is_play_or_lab_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], role: str
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    name = {"play": "GTNH-Play", "lab": "GTNH-Lab"}[role]
    env.write_config(
        env.config.read_text().replace("[paths]\n", f'[paths]\nexport = "{name}"\n')
    )
    code, out = env.collect(capsys)
    assert code == 1
    assert f"is the {role} instance, is inside it or holds it" in out
    assert not env.home.exists()


@pytest.mark.parametrize("inside", ["play", "instances"])
def test_gos_home_inside_prism_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], inside: str
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    home = env.play / "GregtecOS" if inside == "play" else env.instances / "GregtecOS"
    env.write_config(
        env.config.read_text().replace(env.home.as_posix(), home.as_posix())
    )
    code, out = env.collect(capsys)
    assert code == 1
    assert "which collect never writes" in out
    assert not home.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege")
def test_a_linked_destination_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    make_run(env.exports, "20261102T000000Z", "2026-11-02T00:00:00Z")
    (env.home / "data").mkdir(parents=True)
    env.dest.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    code, out = env.collect(capsys)
    assert code == 1
    assert f"{env.dest} is a link, a junction or not a folder" in out


def test_a_missing_export_instance_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    env.write_config(
        env.config.read_text().replace("[paths]\n", '[paths]\nexport = "Nope"\n')
    )
    code, out = env.collect(capsys)
    assert code == 1
    assert "Export: " in out and "doesn't exist" in out


def test_config_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOS_HOME", str(tmp_path / "home"))
    assert config.Config().gos_home == tmp_path / "home"
    assert config.Config().keep_collected == 5
    toml = tmp_path / "gos.toml"
    toml.write_text('[paths]\ngos_home = "D:/GOS"\n[export]\nkeep_collected = 2\n')
    cfg = config.load(toml)
    assert (cfg.gos_home, cfg.keep_collected) == (Path("D:/GOS"), 2)
    for bad in ("0", '"5"', "true", "1.5"):
        toml.write_text(f"[export]\nkeep_collected = {bad}\n")
        with pytest.raises(config.ConfigError, match="keep_collected"):
            config.load(toml)


def test_a_bad_config_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    toml = tmp_path / "gos.toml"
    toml.write_text("[export]\nkeep_collected = 0\n")
    assert cli.main(["export", "collect", "--config", str(toml)]) == 2
    assert "Can't read gos.toml" in capsys.readouterr().out
