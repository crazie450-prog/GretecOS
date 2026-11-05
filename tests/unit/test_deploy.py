"""``gos deploy`` (gregtec.deploy): archive, staging, swap and refusals, on a throwaway repo.

Most tests replace the uv step with a stub; ``test_real_uv_release_runs_after_the_swap`` builds a
real venv once.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from gregtec import cli, config
from gregtec import deploy as dep

REPO = Path(__file__).resolve().parents[2]
RELOCATE = dep.relocate_editable  # the real one; the autouse stub replaces it
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        [*GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def commit(repo: Path, files: dict[str, str | None], *, merge: bool = True) -> str:
    """Write (or with None delete) files, commit, and optionally mark it merged."""
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, "utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c")
    sha = git(repo, "rev-parse", "HEAD")
    if merge:
        git(repo, "update-ref", "refs/remotes/origin/main", sha)
    return sha


def snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
        if ".git" not in p.relative_to(root).parts
    }


def icacls(folder: Path, *args: str, check: bool = True) -> int:
    run = subprocess.run(
        ["icacls", str(folder), *args], check=check, capture_output=True
    )
    return run.returncode


def protect(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows: give the folder setup_gos_dev.ps1's GOS_APP ACL (protected, owner Administrators,
    full control for SYSTEM, Administrators and you only)."""
    if sys.platform != "win32":
        return
    full = [f"*{sid}:(OI)(CI)F" for sid in (dep.SYSTEM, dep.ADMINS, dep.current_sid())]
    icacls(folder, "/grant:r", *full)
    icacls(folder, "/inheritance:r")
    if icacls(folder, "/setowner", f"*{dep.ADMINS}", check=False):
        # Only an elevated token may give a folder to Administrators (CI's is; a normal window's
        # isn't), so here only the owner deploy reads is faked; the ACEs are still real.
        real = dep.read_acl
        monkeypatch.setattr(
            dep, "read_acl", lambda p: dataclasses.replace(real(p), owner=dep.ADMINS)
        )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(
        repo,
        {
            "core/gregtec/__init__.py": "",
            "scripts/backup_world.ps1": "# v1\n",
            "scripts/setup_gos_dev.ps1": "# v1\n",
            "scripts/old_tool.py": "# v1\n",
        },
    )
    return repo


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """GOS_APP as setup_gos_dev.ps1 leaves it: its two scripts copied in."""
    app = tmp_path / "GregtecOS-app"
    app.mkdir()
    protect(app, monkeypatch)
    (app / "scripts").mkdir()
    for name in ("backup_world.ps1", "setup_gos_dev.ps1"):
        (app / "scripts" / name).write_text("# from setup\n", "utf-8")
    return app


def fake_env(release: Path) -> None:
    (release / ".venv").mkdir()
    (release / ".venv" / "marker").write_text("env\n", "utf-8")


@pytest.fixture(autouse=True)
def stub_uv(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    monkeypatch.setattr(dep, "RENAME_DELAY", 0)
    if "real_uv" in request.fixturenames:
        return
    monkeypatch.setattr(dep, "_build_env", fake_env)
    monkeypatch.setattr(dep, "relocate_editable", lambda release: None)
    monkeypatch.setattr(dep, "_check_env", lambda release: None)


def test_first_deploy_installs_and_records_the_commit(repo: Path, app: Path) -> None:
    sha = git(repo, "rev-parse", "HEAD")
    before = snapshot(repo)
    release = dep.deploy(repo, "HEAD", app)

    assert release.commit == sha and release.replaced is None
    assert release.path == app / "current" and not release.leftovers
    record = json.loads((app / "current" / "release.json").read_text("utf-8"))
    assert record["commit"] == sha and record["ref"] == "HEAD"
    assert record["deployed_at"] == release.deployed_at
    assert (app / "current" / "core" / "gregtec" / "__init__.py").is_file()
    assert (app / "current" / ".venv" / "marker").is_file()
    assert not (app / "current" / ".git").exists()
    # GOS_APP\scripts is now exactly the release's scripts/ (the setup copies replaced).
    assert snapshot(app / "scripts") == snapshot(app / "current" / "scripts")
    assert (app / "scripts" / "backup_world.ps1").read_text("utf-8") == "# v1\n"
    assert sorted(p.name for p in app.iterdir()) == ["current", "scripts"]
    assert snapshot(repo) == before and not git(repo, "status", "--porcelain")


def test_redeploy_replaces_both_folders_and_removes_the_old_ones(
    repo: Path, app: Path
) -> None:
    first = dep.deploy(repo, "HEAD", app)
    second_sha = commit(
        repo,
        {"scripts/backup_world.ps1": "# v2\n", "scripts/old_tool.py": None},
    )
    second = dep.deploy(repo, "HEAD", app)

    assert second.commit == second_sha and second.replaced == first.commit
    assert (app / "scripts" / "backup_world.ps1").read_text("utf-8") == "# v2\n"
    assert not (app / "scripts" / "old_tool.py").exists()
    assert not (app / "current" / "scripts" / "old_tool.py").exists()
    assert sorted(p.name for p in app.iterdir()) == ["current", "scripts"]


def test_deploys_an_older_merged_commit(repo: Path, app: Path) -> None:
    first = git(repo, "rev-parse", "HEAD")
    commit(repo, {"scripts/backup_world.ps1": "# v2\n"})
    release = dep.deploy(repo, first, app)
    assert release.commit == first and release.ref == first
    assert (app / "scripts" / "backup_world.ps1").read_text("utf-8") == "# v1\n"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("dirty", "uncommitted changes"),
        ("staged", "uncommitted changes"),
        ("unmerged", "isn't in origin/main"),
        ("no-origin", "can't check origin/main"),
        ("bad-ref", "isn't a commit"),
        ("option-ref", "isn't a ref"),
        ("no-backup-script", "has no scripts/backup_world.ps1"),
    ],
)
def test_refuses_and_changes_nothing(
    repo: Path, app: Path, change: str, message: str
) -> None:
    ref = "HEAD"
    if change == "dirty":
        (repo / "scripts" / "old_tool.py").write_text("# edited\n", "utf-8")
    elif change == "staged":
        (repo / "new.txt").write_text("x\n", "utf-8")
        git(repo, "add", "new.txt")
    elif change == "unmerged":
        commit(repo, {"x.txt": "x\n"}, merge=False)
    elif change == "no-origin":
        git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    elif change == "bad-ref":
        ref = "no-such-branch"
    elif change == "option-ref":
        ref = "--output=x"
    elif change == "no-backup-script":
        commit(repo, {"scripts/backup_world.ps1": None})
    before = snapshot(app)
    with pytest.raises(dep.DeployError, match=message):
        dep.deploy(repo, ref, app)
    assert snapshot(app) == before


def test_untracked_files_are_not_deployed_and_not_refused(
    repo: Path, app: Path
) -> None:
    (repo / "notes.txt").write_text("local\n", "utf-8")
    dep.deploy(repo, "HEAD", app)
    assert not (app / "current" / "notes.txt").exists()


def test_refuses_a_missing_gos_app(repo: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(dep.DeployError, match="setup_gos_dev.ps1"):
        dep.deploy(repo, "HEAD", missing)
    assert not missing.exists()  # never created


@pytest.mark.parametrize(
    "where", ["in-repo", "around-repo", "forbidden", "in-forbidden"]
)
def test_refuses_a_gos_app_that_overlaps(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    prism = tmp_path / "Prism"
    (prism / "instances" / "GTNH-Play").mkdir(parents=True)
    target = {
        "in-repo": repo / "app",
        "around-repo": tmp_path,
        "forbidden": prism,
        "in-forbidden": prism / "instances" / "GTNH-Play",
    }[where]
    target.mkdir(exist_ok=True)
    protect(target, monkeypatch)
    before = snapshot(tmp_path)
    with pytest.raises(dep.DeployError, match="overlaps"):
        dep.deploy(repo, "HEAD", target, forbidden=[prism])
    assert snapshot(tmp_path) == before


def test_refuses_a_gos_app_behind_a_link(repo: Path, app: Path, tmp_path: Path) -> None:
    link = tmp_path / "link"
    try:
        link.symlink_to(app, target_is_directory=True)
    except OSError:
        pytest.skip("no symlink privilege")
    with pytest.raises(dep.DeployError, match="isn't a folder"):
        dep.deploy(repo, "HEAD", link)
    (tmp_path / "parent").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(dep.DeployError, match="through a link"):
        dep.deploy(repo, "HEAD", tmp_path / "parent" / app.name)
    assert not (app / "current").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")
def test_refuses_a_gos_app_that_inherits_its_acl(repo: Path, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(dep.DeployError, match="inherits its permissions"):
        dep.deploy(repo, "HEAD", plain)
    assert not any(plain.iterdir())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")
def test_refuses_a_protected_gos_app_owned_by_its_maker(
    repo: Path, tmp_path: Path
) -> None:
    """What gos-dev could make in C:\\ while GOS_APP is missing: protected, owned by itself."""
    made = tmp_path / "made"
    made.mkdir()
    me = dep.current_sid()
    icacls(made, "/setowner", f"*{me}")
    icacls(made, "/inheritance:d")
    acl = dep.read_acl(made)
    assert acl.protected and acl.owner == me and acl.allows
    with pytest.raises(dep.DeployError, match="not Administrators"):
        dep.deploy(repo, "HEAD", made)
    assert not any(made.iterdir())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")
def test_reads_setup_acl_and_refuses_an_extra_writer(repo: Path, app: Path) -> None:
    assert dep.acl_problem(dep.read_acl(app), dep.current_sid()) is None
    icacls(app, "/grant", "*S-1-5-11:(OI)(CI)M")  # Authenticated Users: Modify
    before = snapshot(app)
    with pytest.raises(dep.DeployError, match="lets S-1-5-11 change it"):
        dep.deploy(repo, "HEAD", app)
    assert snapshot(app) == before


ME, DEV = "S-1-5-21-1-2-3-1001", "S-1-5-21-1-2-3-1002"
FULL, MODIFY, READ_EXEC = 0x1F01FF, 0x1301BF, 0x1200A9
SETUP_ACES = ((dep.SYSTEM, FULL), (dep.ADMINS, FULL), (ME, FULL), (DEV, READ_EXEC))


@pytest.mark.parametrize(
    ("acl", "problem"),
    [
        (dep.Acl(dep.ADMINS, True, SETUP_ACES), None),
        (dep.Acl(dep.ADMINS, False, SETUP_ACES), "inherits its permissions"),
        (dep.Acl(DEV, True, SETUP_ACES), f"is owned by {DEV}, not Administrators"),
        (dep.Acl(ME, True, SETUP_ACES), "not Administrators"),
        (dep.Acl(dep.ADMINS, True, (*SETUP_ACES, ("S-1-5-11", MODIFY))), "S-1-5-11"),
        (
            dep.Acl(dep.ADMINS, True, (*SETUP_ACES, (DEV, 0x40000000))),
            DEV,
        ),  # GENERIC_WRITE
        (dep.Acl(dep.ADMINS, True, (*SETUP_ACES, (DEV, 0x00040000))), DEV),  # WRITE_DAC
        (dep.Acl(dep.ADMINS, True, (("S-1-1-0", 0xFFFFFFFF),)), "S-1-1-0"),  # NULL DACL
        # OWNER RIGHTS (what Python's mkdir(mode=0o700) adds) writes only as the owner.
        (dep.Acl(dep.ADMINS, True, (*SETUP_ACES, (dep.OWNER_RIGHTS, FULL))), None),
        (
            dep.Acl(DEV, True, (*SETUP_ACES, (dep.OWNER_RIGHTS, FULL))),
            "not Administrators",
        ),
    ],
)
def test_acl_problem(acl: dep.Acl, problem: str | None) -> None:
    """GOS_APP must be protected, owned by Administrators, and writable only by SYSTEM,
    Administrators and the deployer: gos-dev can protect a folder it made, not give it away."""
    found = dep.acl_problem(acl, ME)
    assert (
        found is None if problem is None else (found is not None and problem in found)
    )


def test_refuses_a_current_folder_it_did_not_make(repo: Path, app: Path) -> None:
    (app / "current").mkdir()
    (app / "current" / "mine.txt").write_text("x\n", "utf-8")
    before = snapshot(app)
    with pytest.raises(dep.DeployError, match="has no readable release.json"):
        dep.deploy(repo, "HEAD", app)
    assert snapshot(app) == before


def test_failed_build_leaves_gos_app_unchanged(
    repo: Path, app: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dep.deploy(repo, "HEAD", app)
    commit(repo, {"scripts/backup_world.ps1": "# v2\n"})
    before = snapshot(app)

    def broken(release: Path) -> None:
        raise dep.DeployError("uv sync failed (see above); nothing was swapped")

    monkeypatch.setattr(dep, "_build_env", broken)
    with pytest.raises(dep.DeployError, match="uv sync failed"):
        dep.deploy(repo, "HEAD", app)
    assert snapshot(app) == before


def failing_rename(n: int) -> Callable[[Path, Path], None]:
    """os.rename that fails, every time, for the pair it is given on its nth call."""
    real = os.rename
    calls: list[tuple[str, str]] = []
    bad: list[tuple[str, str]] = []

    def rename(src: Path, dst: Path) -> None:
        pair = (str(src), str(dst))
        calls.append(pair)
        if len(calls) == n:
            bad.append(pair)
        if pair in bad:
            raise PermissionError(13, "in use", str(src))
        real(src, dst)

    return rename


@pytest.mark.parametrize("n", [2, 3, 4, 5])
def test_a_failed_swap_is_undone(
    repo: Path, app: Path, monkeypatch: pytest.MonkeyPatch, n: int
) -> None:
    """Renames: build to staged, current aside, staged in, scripts aside, staged scripts in."""
    dep.deploy(repo, "HEAD", app)
    commit(repo, {"scripts/backup_world.ps1": "# v2\n"})
    before = snapshot(app)
    monkeypatch.setattr(dep.os, "rename", failing_rename(n))
    with pytest.raises(dep.DeployError, match="GOS_APP is unchanged"):
        dep.deploy(repo, "HEAD", app)
    assert snapshot(app) == before


def test_first_deploy_swap_failure_is_undone(
    repo: Path, app: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot(app)
    monkeypatch.setattr(dep.os, "rename", failing_rename(4))  # staged scripts in
    with pytest.raises(dep.DeployError, match="GOS_APP is unchanged"):
        dep.deploy(repo, "HEAD", app)
    assert snapshot(app) == before


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
def test_an_interrupted_deploy_is_undone(
    repo: Path, app: Path, monkeypatch: pytest.MonkeyPatch, n: int
) -> None:
    """Ctrl+C on any rename (see test_a_failed_swap_is_undone) leaves GOS_APP as it was."""
    dep.deploy(repo, "HEAD", app)
    commit(repo, {"scripts/backup_world.ps1": "# v2\n"})
    before = snapshot(app)
    real = os.rename
    calls: list[str] = []

    def rename(src: Path, dst: Path) -> None:
        calls.append(str(src))
        if len(calls) == n:
            raise KeyboardInterrupt
        real(src, dst)

    monkeypatch.setattr(dep.os, "rename", rename)
    with pytest.raises(KeyboardInterrupt):
        dep.deploy(repo, "HEAD", app)
    assert snapshot(app) == before


@pytest.mark.parametrize("lost", ["current", "scripts"])
def test_the_next_deploy_restores_what_a_crash_mid_swap_lost(
    repo: Path, app: Path, monkeypatch: pytest.MonkeyPatch, lost: str
) -> None:
    """A crash no handler sees (power cut) between renames leaves only .gos-old-<name>-*."""
    first = dep.deploy(repo, "HEAD", app)
    before = snapshot(app)
    os.rename(app / lost, app / f".gos-old-{lost}-20261105-120000-abcd")
    (
        app / f".gos-old-{lost}-20261104-120000-abcd"
    ).mkdir()  # older, couldn't be deleted

    def broken(release: Path) -> None:
        raise dep.DeployError("uv sync failed (see above); nothing was swapped")

    monkeypatch.setattr(dep, "_build_env", broken)
    with pytest.raises(dep.DeployError, match="uv sync failed"):
        dep.deploy(repo, "HEAD", app)
    assert (
        snapshot(app) == before
    )  # the newest old folder is back, the older one cleared

    monkeypatch.setattr(dep, "_build_env", fake_env)
    commit(repo, {"scripts/backup_world.ps1": "# v2\n"})
    second = dep.deploy(repo, "HEAD", app)
    assert second.replaced == first.commit
    assert sorted(p.name for p in app.iterdir()) == ["current", "scripts"]


def test_the_venv_is_checked_after_a_rename(
    repo: Path, app: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A venv that still points at the folder it was built in fails before the swap."""
    built: list[Path] = []
    checked: list[Path] = []

    def build_env(release: Path) -> None:
        fake_env(release)
        (release / ".venv" / "home").write_text(str(release), "utf-8")
        built.append(release)

    def check_env(release: Path) -> None:
        checked.append(release)
        if not Path((release / ".venv" / "home").read_text("utf-8")).is_dir():
            raise dep.DeployError("the staged gos command doesn't run")

    monkeypatch.setattr(dep, "_build_env", build_env)
    monkeypatch.setattr(dep, "_check_env", check_env)
    before = snapshot(app)
    with pytest.raises(dep.DeployError, match="doesn't run"):
        dep.deploy(repo, "HEAD", app)
    assert built[0].parent == checked[0].parent == app and built[0] != checked[0]
    assert snapshot(app) == before


def test_an_old_release_in_use_is_reported_then_cleared(
    repo: Path, app: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dep.deploy(repo, "HEAD", app)
    real_remove = dep._remove
    monkeypatch.setattr(dep, "_remove", lambda path: False)
    release = dep.deploy(repo, "HEAD", app)
    assert len(release.leftovers) == 2
    assert all(
        p.parent == app and p.name.startswith(".gos-old-") for p in release.leftovers
    )
    assert all(p.is_dir() for p in release.leftovers)

    monkeypatch.setattr(dep, "_remove", real_remove)
    (app / ".gos-staging-interrupted").mkdir()
    dep.deploy(repo, "HEAD", app)
    assert sorted(p.name for p in app.iterdir()) == ["current", "scripts"]


def test_relocate_editable_makes_the_path_relative(tmp_path: Path) -> None:
    release = tmp_path / "release"
    site = release / ".venv" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    (release / "core").mkdir()
    (site / "_virtualenv.pth").write_text("import _virtualenv\n", "utf-8")
    (site / "gregtec_os.pth").write_text(f"{release / 'core'}\n", "utf-8")
    RELOCATE(release)
    line = (site / "gregtec_os.pth").read_text("utf-8").strip()
    assert not os.path.isabs(line)
    assert (site / line).resolve() == (release / "core").resolve()
    assert (site / "_virtualenv.pth").read_text("utf-8") == "import _virtualenv\n"
    with pytest.raises(dep.DeployError, match="found 0"):
        RELOCATE(release)  # nothing absolute is left to fix


def test_config_reads_gos_app(tmp_path: Path) -> None:
    assert config.Config().gos_app == Path("C:/GregtecOS-app")
    toml = tmp_path / "gos.toml"
    toml.write_text(f'[paths]\ngos_app = "{(tmp_path / "x").as_posix()}"\n', "utf-8")
    assert config.load(toml).gos_app == tmp_path / "x"


def test_cli_deploys_to_the_configured_folder(
    repo: Path,
    app: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(dep, "REPO_ROOT", repo)
    monkeypatch.setenv("GOS_HOME", str(tmp_path / "home"))
    toml = tmp_path / "gos.toml"
    toml.write_text(
        "[paths]\n"
        f'gos_app = "{app.as_posix()}"\n'
        f'prism_instances = "{(tmp_path / "Prism" / "instances").as_posix()}"\n',
        "utf-8",
    )
    assert cli.main(["deploy", "--config", str(toml)]) == 0
    out = capsys.readouterr().out
    sha = git(repo, "rev-parse", "HEAD")
    assert f"Deployed {sha[:12]} (HEAD)" in out
    assert str(dep.venv_exe(app / "current", "gos")) in out

    (repo / "scripts" / "old_tool.py").write_text("# edited\n", "utf-8")
    assert cli.main(["deploy", "--config", str(toml)]) == 1
    assert "Not deployed:" in capsys.readouterr().err


@pytest.fixture
def real_uv() -> str:
    uv = os.environ.get("UV") or shutil.which("uv")
    if uv is None or shutil.which("git") is None:
        pytest.skip("needs uv and git")
    return uv


def test_real_uv_release_runs_after_the_swap(
    real_uv: str, tmp_path: Path, app: Path
) -> None:
    """A real clone of this repo's HEAD: the venv works from GOS_APP\\current, not staging."""
    src = tmp_path / "src"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(REPO), str(src)], check=True
    )
    git(src, "update-ref", "refs/remotes/origin/main", "HEAD")
    release = dep.deploy(src, "HEAD", app)

    current = app / "current"
    probe = "import pathlib, gregtec; print(pathlib.Path(gregtec.__file__).resolve())"
    out = subprocess.run(
        [str(dep.venv_exe(current, "python")), "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert (current / "core").resolve() in Path(out.stdout.strip()).parents
    gos = subprocess.run(
        [str(dep.venv_exe(current, "gos")), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert gos.returncode == 0 and "doctor" in gos.stdout
    # UV_LINK_MODE=copy: files are GOS_APP's own, not hardlinks into uv's cache.
    site = next((current / ".venv").glob("**/site-packages"))
    assert (site / "anthropic" / "__init__.py").stat().st_nlink == 1
    assert release.leftovers == ()
    assert sorted(p.name for p in app.iterdir()) == ["current", "scripts"]
