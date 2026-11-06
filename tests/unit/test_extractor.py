"""WO-1.8: `gos structures extract`, the gtnh-extractor driver (D124).

Nothing here runs Gradle or the extractor: the Gradle step is a stub that copies the
self-authored fixture dataset into place, and the upstream checkout is a local git repository
holding a few self-authored files in the extractor's layout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from gregtec import cli
from gregtec.ingest import extractor
from gregtec.ingest.extractor import ExtractError

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "gtnh-extractor"
# Self-authored, in the shape of the extractor's dependencies.gradle at 2.9.0-beta-3.
UPSTREAM_DEPS = """dependencies {
    implementation("com.github.GTNewHorizons:GT5-Unofficial:5.09.54.133:dev") {
        exclude group: "com.github.GTNewHorizons", module: "ThaumicTinkerer"
    }
    implementation("com.github.GTNewHorizons:StructureLib:1.4.42:dev")
    implementation("com.github.GTNewHorizons:CropsNH:2.0.114:dev") {
        exclude group: "com.github.GTNewHorizons", module: "ThaumicTinkerer"
    }
    runtimeOnlyNonPublishable("com.github.GTNewHorizons:BetterBuildersWands:0.13.12-GTNH:dev") {
        transitive = false
    }
    testImplementation("junit:junit:4.13.2")
}
"""
SETTINGS = (
    'plugins {{\n    id("com.gtnewhorizons.gtnhsettingsconvention") version("{}")\n}}\n'
)


def upstream_tree(root: Path) -> Path:
    """A fake upstream checkout: the tool folder with its own wrapper and leftovers."""
    tool = root / extractor.TOOL
    (tool / "src" / "main" / "java").mkdir(parents=True)
    (tool / "src" / "main" / "java" / "DumperMod.java").write_text(
        "class DumperMod {}\n"
    )
    (tool / "dependencies.gradle").write_text(UPSTREAM_DEPS)
    (tool / "settings.gradle.kts").write_text(SETTINGS.format("2.0.20"))
    (tool / "build.gradle.kts").write_text("// build\n")
    (tool / "gradlew").write_text("upstream wrapper\n")
    (tool / "gradle" / "wrapper").mkdir(parents=True)
    (tool / "gradle" / "wrapper" / "gradle-wrapper.jar").write_bytes(b"upstream jar")
    (tool / "run" / "server").mkdir(parents=True)
    (tool / "run" / "server" / "world.dat").write_text("stale\n")
    return root


def fake_repo(root: Path) -> Path:
    mod = root / "mod"
    for rel in extractor.WRAPPER:
        (mod / rel).parent.mkdir(parents=True, exist_ok=True)
        (mod / rel).write_text(f"ours: {rel.as_posix()}\n")
    (mod / "settings.gradle.kts").write_text(SETTINGS.format("2.0.33"))
    return root


# --- re-pin -------------------------------------------------------------------------------


def test_repin_sets_every_coordinate() -> None:
    text = extractor.repin(UPSTREAM_DEPS)
    assert "GT5-Unofficial:5.09.54.265:dev" in text
    assert "StructureLib:1.4.47:dev" in text
    assert "CropsNH:2.0.140:dev" in text
    assert "BetterBuildersWands:0.13.12-GTNH:dev" in text
    assert "5.09.54.133" not in text and "1.4.42" not in text and "2.0.114" not in text
    assert 'module: "ThaumicTinkerer"' in text  # the rest is untouched
    assert extractor.repin(text) == text


@pytest.mark.parametrize(
    "broken",
    [
        UPSTREAM_DEPS.replace("StructureLib:1.4.42:dev", "StructureLib:1.4.42"),
        UPSTREAM_DEPS.replace(
            "    testImplementation",
            '    implementation("com.github.GTNewHorizons:CropsNH:2.0.1:dev")\n    testImplementation',
        ),
    ],
    ids=["missing", "twice"],
)
def test_repin_refuses_a_changed_upstream(broken: str) -> None:
    with pytest.raises(ExtractError, match="re-check the pin"):
        extractor.repin(broken)


# --- prepare ------------------------------------------------------------------------------


def test_prepare_swaps_the_wrapper_and_pins(tmp_path: Path) -> None:
    up = upstream_tree(tmp_path / "upstream")
    repo = fake_repo(tmp_path / "repo")
    build = tmp_path / "work" / "build"
    build.mkdir(parents=True)
    (build / "old.txt").write_text("from the last run\n")
    extractor.prepare(up, build, repo=repo)
    assert not (build / "old.txt").exists()
    assert (build / "src" / "main" / "java" / "DumperMod.java").is_file()
    for rel in extractor.WRAPPER:
        assert (build / rel).read_text() == f"ours: {rel.as_posix()}\n"
    assert "5.09.54.265" in (build / "dependencies.gradle").read_text()
    assert 'version("2.0.33")' in (build / "settings.gradle.kts").read_text()
    server = build / "run" / "server"
    assert sorted(p.name for p in server.iterdir()) == ["eula.txt", "server.properties"]
    assert (server / "eula.txt").read_text() == "eula=true\n"
    assert "online-mode=false" in (server / "server.properties").read_text()
    # The checkout is only read.
    assert (up / extractor.TOOL / "gradlew").read_text() == "upstream wrapper\n"


def test_prepare_refuses_something_else(tmp_path: Path) -> None:
    with pytest.raises(ExtractError, match="not gtnh-extractor"):
        extractor.prepare(
            tmp_path, tmp_path / "build", repo=fake_repo(tmp_path / "repo")
        )


def test_prepare_needs_our_wrapper(tmp_path: Path) -> None:
    up = upstream_tree(tmp_path / "upstream")
    repo = fake_repo(tmp_path / "repo")
    (repo / "mod" / "gradle" / "wrapper" / "gradle-wrapper.jar").unlink()
    with pytest.raises(ExtractError, match="gradle-wrapper.jar is missing"):
        extractor.prepare(up, tmp_path / "build", repo=repo)


def test_the_real_mod_wrapper_is_there() -> None:
    for rel in extractor.WRAPPER:
        assert (extractor.REPO_ROOT / "mod" / rel).is_file(), rel
    settings = (extractor.REPO_ROOT / "mod" / "settings.gradle.kts").read_text()
    assert extractor._SETTINGS_PLUGIN.search(settings)


# --- fetch (a local git repository stands in for GitHub) ----------------------------------


def git(*args: str, cwd: Path) -> str:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> tuple[Path, str]:
    repo = upstream_tree(tmp_path / "origin")
    git("init", "-q", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "extractor", cwd=repo)
    return repo, git("rev-parse", "HEAD", cwd=repo)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_fetch_checks_out_the_pinned_commit(
    tmp_path: Path, origin: tuple[Path, str]
) -> None:
    repo, commit = origin
    work = tmp_path / "work"
    up = extractor.fetch(work, url=repo.as_uri(), commit=commit)
    assert up == work / "upstream"
    assert (up / extractor.TOOL / "dependencies.gradle").read_text() == UPSTREAM_DEPS
    # A later run puts back anything changed or added in the checkout.
    (up / extractor.TOOL / "dependencies.gradle").write_text("changed\n")
    (up / "extra.txt").write_text("added\n")
    git(
        "commit", "-q", "--allow-empty", "-m", "newer upstream", cwd=repo
    )  # not fetched
    extractor.fetch(work, url=repo.as_uri(), commit=commit)
    assert (up / extractor.TOOL / "dependencies.gradle").read_text() == UPSTREAM_DEPS
    assert not (up / "extra.txt").exists()
    assert git("rev-parse", "HEAD", cwd=up) == commit


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_fetch_fails_on_an_unknown_commit(
    tmp_path: Path, origin: tuple[Path, str]
) -> None:
    repo, _ = origin
    with pytest.raises(ExtractError, match="git fetch"):
        extractor.fetch(tmp_path / "work", url=repo.as_uri(), commit="0" * 40)


# --- run and check ------------------------------------------------------------------------


def test_gradle_command_passes_the_pins(tmp_path: Path) -> None:
    command = extractor.gradle_command(tmp_path / "build", tmp_path / "out")
    assert Path(command[0]).name in ("gradlew", "gradlew.bat")
    assert command[1:3] == ["--no-daemon", "runServer"]
    assert f"-PdatasetOut={tmp_path / 'out'}" in command
    assert "-PpackVersion=2.9.0" in command
    assert (
        "-PmodVersions=GT5-Unofficial=5.09.54.265,StructureLib=1.4.47,CropsNH=2.0.140"
        in command
    )
    assert f"-PextractorSha={extractor.COMMIT}+gos-2.9.0" in command


def test_run_gradle_sets_the_version_and_closes_stdin(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def runner(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs, command=command)
        return subprocess.CompletedProcess(command, 3)

    assert extractor.run_gradle(tmp_path, tmp_path / "out", runner=runner) == 3
    assert seen["cwd"] == tmp_path
    assert seen["env"]["VERSION"] == extractor.COMMIT[:12]
    assert seen["stdin"] == subprocess.DEVNULL


def test_check_accepts_a_fresh_dump(tmp_path: Path) -> None:
    started = time.time()
    shutil.copytree(
        FIXTURE, tmp_path / "out", copy_function=shutil.copy
    )  # fresh mtimes
    data = extractor.check(tmp_path / "out", started)
    assert len(data.controllers) == 3


def test_check_refuses_a_missing_or_stale_dump(tmp_path: Path) -> None:
    with pytest.raises(ExtractError, match="never reached the dump"):
        extractor.check(tmp_path, time.time())
    shutil.copytree(FIXTURE, tmp_path / "out")
    meta = tmp_path / "out" / "multiblocks" / "_meta.json"
    os.utime(meta, (time.time() - 3600, time.time() - 3600))
    with pytest.raises(ExtractError, match="older than this run"):
        extractor.check(tmp_path / "out", time.time())


def test_check_refuses_another_pack(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE, tmp_path / "out")
    meta = tmp_path / "out" / "multiblocks" / "_meta.json"
    meta.write_text(meta.read_text().replace('"2.9.0"', '"2.9.1"'))
    with pytest.raises(ExtractError, match="not 2.9.0"):
        extractor.check(tmp_path / "out", time.time() - 5)


def test_default_paths_are_in_gos_home(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    work, out = extractor.default_paths(
        tmp_path, datetime(2026, 11, 6, 7, 8, 9, tzinfo=UTC)
    )
    assert work == tmp_path / "data" / "cache" / "gtnh-extractor"
    assert out == tmp_path / "data" / "structures" / "2.9.0-20261106T070809Z"


# --- gos structures extract ---------------------------------------------------------------


@pytest.fixture
def no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """fetch returns a local fake checkout; prepare uses this repo's real mod/ wrapper."""
    up = upstream_tree(tmp_path / "upstream")

    def fetch(work: Path, **kwargs: object) -> Path:
        return up

    monkeypatch.setattr(extractor, "fetch", fetch)
    return up


def gradle_stub(code: int, *, dump: bool = True) -> extractor.Runner:
    def runner(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        out = Path(next(a for a in command if a.startswith("-PdatasetOut="))[13:])
        if dump:
            shutil.copytree(
                FIXTURE / "multiblocks", out / "multiblocks", copy_function=shutil.copy
            )
        return subprocess.CompletedProcess(command, code)

    return runner


def test_extract_end_to_end(
    tmp_path: Path, no_network: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "home" / "data" / "structures" / "run"
    code = extractor.main(
        tmp_path / "home", tmp_path / "work", out, runner=gradle_stub(0)
    )
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "Dumped 3 controllers (4 variants)" in printed
    assert f"Next: gos structures load {out.resolve()}" in printed
    build = tmp_path / "work" / "build"
    assert (build / "gradlew").read_bytes() == (
        extractor.REPO_ROOT / "mod" / "gradlew"
    ).read_bytes()


def test_extract_reports_a_failed_server(
    tmp_path: Path, no_network: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = extractor.main(
        tmp_path, tmp_path / "work", tmp_path / "out", runner=gradle_stub(1, dump=False)
    )
    assert code == 1
    assert "exited 1" in capsys.readouterr().err


def test_extract_needs_the_dump_not_just_a_good_exit(
    tmp_path: Path, no_network: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = extractor.main(
        tmp_path, tmp_path / "work", tmp_path / "out", runner=gradle_stub(0, dump=False)
    )
    assert code == 1
    assert "never reached the dump" in capsys.readouterr().err


def test_extract_refuses_a_foreign_work_folder(
    tmp_path: Path, no_network: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "someone"
    work.mkdir()
    (work / "notes.txt").write_text("keep me\n")
    code = extractor.main(tmp_path, work, tmp_path / "out", runner=gradle_stub(0))
    assert code == 1
    assert "not an extractor work folder" in capsys.readouterr().err
    assert (work / "notes.txt").read_text() == "keep me\n"


def test_claim_marks_a_new_or_own_work_folder(tmp_path: Path) -> None:
    extractor.claim(tmp_path / "work")
    (tmp_path / "work" / "build").mkdir()
    extractor.claim(tmp_path / "work")  # its own folder, from an earlier run
    assert (tmp_path / "work" / extractor.MARKER).is_file()


def test_extract_refuses_a_used_out_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("x")
    argv = [
        "structures",
        "extract",
        "--gos-home",
        str(tmp_path),
        "--out",
        str(tmp_path / "out"),
    ]
    assert cli.main(argv) == 2
    assert "not empty" in capsys.readouterr().err
