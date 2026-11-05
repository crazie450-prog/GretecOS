r"""``gos deploy``: install a merged commit of this clone into GOS_APP (WO-0.8, D75, plan 4.6).

Clayton runs it from his own profile clone, as himself. GOS_APP (``C:\GregtecOS-app``) is made by
``scripts/setup_gos_dev.ps1``: owner Administrators, Clayton full control, gos-dev read only, no
inheritance from ``C:\``. Deploy never creates it and never writes outside it::

    GOS_APP\
      current\     git archive of the commit, its .venv and release.json (what, when, from where)
      scripts\     a copy of current\scripts: fixed paths for the world backup task and Prism hooks

How a deploy runs:

1. Refuse unless GOS_APP is the configured folder, exists, is reached through no link, doesn't
   overlap the repo, Prism or GOS_HOME, and (Windows) has the protected DACL the setup gives it.
2. Refuse unless the commit is in ``origin/main`` and the working tree has no uncommitted changes.
3. Build the release in ``GOS_APP\.gos-staging-*``: ``git archive``, then ``uv venv --relocatable``
   and ``uv sync --locked --no-dev`` with ``UV_LINK_MODE=copy``. Copies, not hardlinks into uv's
   cache, so every file is new in GOS_APP and inherits its ACL. The editable install's ``.pth`` is
   made relative, so the release still works once renamed, and is checked before the swap.
4. Swap by renames inside GOS_APP (``current`` and ``scripts`` aside, the staged ones in). A folder
   renamed within the same parent keeps the ACEs it inherited there; nothing is moved in from
   outside, so no source ACL comes along. If a rename fails (Windows refuses while a process runs
   from the folder), every rename is undone and GOS_APP is as it was.
5. Delete the old folders. One still in use is left and reported; the next deploy removes it.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN = "origin/main"  # only merged code is deployed (plan 4.6)
CURRENT = "current"
SCRIPTS = "scripts"
RECORD = "release.json"
STAGING = ".gos-staging-"
OLD = ".gos-old-"
# The scheduled world backup runs GOS_APP\scripts\backup_world.ps1 (setup_gos_dev.ps1 step 7).
REQUIRED = ("scripts/backup_world.ps1",)
RENAME_TRIES = 3  # antivirus can hold a freshly written file for a moment
RENAME_DELAY = 0.5


class DeployError(RuntimeError):
    """Deploy refused or failed; GOS_APP is unchanged unless the message says otherwise."""


@dataclass(frozen=True)
class Release:
    commit: str
    ref: str
    deployed_at: str
    path: Path  # GOS_APP\current
    replaced: str | None  # the commit it replaced, if any
    leftovers: tuple[Path, ...]  # old folders it couldn't delete yet


def venv_bin(release: Path) -> Path:
    return release / ".venv" / ("Scripts" if os.name == "nt" else "bin")


def venv_exe(release: Path, name: str) -> Path:
    return venv_bin(release) / (f"{name}.exe" if os.name == "nt" else name)


def deploy(
    repo: Path, ref: str, app: Path, *, forbidden: Sequence[Path] = ()
) -> Release:
    """Deploy ``ref`` of ``repo`` into ``app`` (GOS_APP). Raises :class:`DeployError`."""
    app = Path(os.path.abspath(app))
    check_app(app, [repo, *forbidden])
    commit = _commit(repo, ref)
    current, scripts = app / CURRENT, app / SCRIPTS
    replaced = None
    if os.path.lexists(current):
        try:
            replaced = json.loads((current / RECORD).read_text("utf-8"))["commit"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DeployError(
                f"{current} has no readable {RECORD}, so deploy didn't make it; move it "
                "out of GOS_APP yourself, then deploy again"
            ) from exc
    _clear(app)

    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    staged, staged_scripts = (
        app / f"{STAGING}{stamp}",
        app / f"{STAGING}scripts-{stamp}",
    )
    deployed_at = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        os.mkdir(staged)  # inside GOS_APP, so it inherits GOS_APP's ACL
        _extract(repo, commit, staged)
        for rel in REQUIRED:
            if not (staged / rel).is_file():
                raise DeployError(f"{commit[:12]} has no {rel}; refusing to deploy it")
        _build_env(staged)
        relocate_editable(staged)
        _check_env(staged)
        shutil.copytree(staged / SCRIPTS, staged_scripts, copy_function=shutil.copyfile)
        record = {
            "commit": commit,
            "ref": ref,
            "deployed_at": deployed_at,
            "source": str(repo),
        }
        (staged / RECORD).write_text(json.dumps(record, indent=2) + "\n", "utf-8")
        old = _swap([(staged, current), (staged_scripts, scripts)], stamp)
    except BaseException:
        for path in (staged, staged_scripts):
            if os.path.lexists(path):
                _remove(path)
        raise
    leftovers = tuple(p for p in old if not _remove(p))
    return Release(commit, ref, deployed_at, current, replaced, leftovers)


def check_app(app: Path, forbidden: Sequence[Path]) -> None:
    """Refuse a GOS_APP that deploy mustn't write: see the module docstring, step 1."""
    if not app.is_dir() or app.is_symlink() or app.is_junction():
        raise DeployError(
            f"{app} isn't a folder. Run scripts/setup_gos_dev.ps1 first: it makes GOS_APP "
            "with gos-dev read only. Deploy never creates it"
        )
    if _key(app) != Path(os.path.normcase(app)):
        raise DeployError(f"{app} is reached through a link or junction; refusing it")
    for other in forbidden:
        a, b = _key(app), _key(other)
        if a == b or a in b.parents or b in a.parents:
            raise DeployError(
                f"{app} overlaps {other}; GOS_APP must be a folder of its own"
            )
    if sys.platform == "win32" and not _dacl_protected(app):
        raise DeployError(
            f"{app} inherits its permissions from its parent, so it isn't the folder "
            "setup_gos_dev.ps1 made (that one has gos-dev read only). Run the setup first"
        )


def relocate_editable(release: Path) -> None:
    """Make the editable install's ``.pth`` line relative to site-packages.

    uv writes the absolute path of ``release/core``; Python resolves a relative line against
    the folder holding the ``.pth``, so the release keeps working after the rename.
    """
    core = _key(release / "core")
    hits = 0
    for pth in sorted((release / ".venv").glob("**/site-packages/*.pth")):
        lines = pth.read_text("utf-8").splitlines()
        found = [
            i
            for i, line in enumerate(lines)
            if os.path.isabs(line.strip()) and _key(Path(line.strip())) == core
        ]
        for i in found:
            lines[i] = os.path.relpath(release / "core", pth.parent)
        if found:
            pth.write_text("\n".join(lines) + "\n", "utf-8")
        hits += len(found)
    if hits != 1:
        raise DeployError(
            f"expected one editable path to {release / 'core'} in the venv's .pth files, "
            f"found {hits}; nothing was swapped"
        )


def _key(path: Path) -> Path:
    return Path(os.path.normcase(os.path.realpath(path)))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    git = shutil.which("git")
    if git is None:
        raise DeployError("git isn't on PATH")
    return subprocess.run(
        [git, "-C", str(repo), *args], check=False, capture_output=True
    )


def _commit(repo: Path, ref: str) -> str:
    """The commit ``ref`` names, if it is merged and the working tree is clean."""
    if not (repo / ".git").exists():
        raise DeployError(
            f"{repo} isn't a git clone; run gos deploy from your own clone"
        )
    if not ref or ref.startswith("-"):
        raise DeployError(f"{ref!r} isn't a ref")
    found = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if found.returncode:
        raise DeployError(f"{ref!r} isn't a commit in {repo}")
    commit = found.stdout.decode().strip()
    status = _git(repo, "status", "--porcelain", "--untracked-files=no")
    if status.returncode:
        raise DeployError(f"git status failed: {status.stderr.decode().strip()}")
    if status.stdout.strip():
        raise DeployError(
            f"{repo} has uncommitted changes. Deploy ships commits only: commit or "
            "discard them first"
        )
    merged = _git(repo, "merge-base", "--is-ancestor", commit, MAIN)
    if merged.returncode == 1:
        raise DeployError(
            f"{commit[:12]} isn't in {MAIN}. Only merged code is deployed: merge the PR, "
            "then git pull (or git fetch) and deploy again"
        )
    if merged.returncode:
        raise DeployError(
            f"can't check {MAIN}: {merged.stderr.decode().strip()}. Run git fetch origin"
        )
    return commit


def _extract(repo: Path, commit: str, dest: Path) -> None:
    archive = _git(repo, "archive", "--format=tar", commit)
    if archive.returncode:
        raise DeployError(f"git archive failed: {archive.stderr.decode().strip()}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(dest, filter="data")


def _build_env(release: Path) -> None:
    uv = os.environ.get("UV") or shutil.which("uv")  # `uv run` sets UV to itself
    if uv is None:
        raise DeployError("uv isn't on PATH")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_NO_EDITABLE")
    }
    env["UV_LINK_MODE"] = "copy"
    for args in (
        ["venv", "--relocatable", "--quiet", ".venv"],
        ["sync", "--locked", "--no-dev", "--quiet"],
    ):
        if subprocess.run([uv, *args], check=False, cwd=release, env=env).returncode:
            raise DeployError(f"uv {args[0]} failed (see above); nothing was swapped")


def _check_env(release: Path) -> None:
    """The staged release imports gregtec from its own core/ and its gos launcher runs."""
    probe = "import pathlib, gregtec; print(pathlib.Path(gregtec.__file__).resolve())"
    out = subprocess.run(
        [str(venv_exe(release, "python")), "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    found = Path(out.stdout.strip()) if out.returncode == 0 else None
    if found is None or _key(release / "core") not in _key(found).parents:
        raise DeployError(
            f"the staged release imports gregtec from {found}, not its own core/: "
            f"{out.stderr.strip()}"
        )
    gos = subprocess.run(
        [str(venv_exe(release, "gos")), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    if gos.returncode:
        raise DeployError(f"the staged gos command doesn't run: {gos.stderr.strip()}")


def _rename(src: Path, dst: Path) -> None:
    for attempt in range(RENAME_TRIES):
        try:
            os.rename(src, dst)
            return
        except PermissionError:
            if attempt == RENAME_TRIES - 1:
                raise
            time.sleep(RENAME_DELAY)


def _swap(pairs: Sequence[tuple[Path, Path]], stamp: str) -> list[Path]:
    """Rename each staged folder onto its live name; all or nothing. Returns the old folders."""
    aside: list[tuple[Path, Path]] = []  # (live, old)
    placed: list[tuple[Path, Path]] = []  # (staged, live)
    try:
        for staged, live in pairs:
            if os.path.lexists(live):
                old = live.with_name(f"{OLD}{live.name}-{stamp}")
                _rename(live, old)
                aside.append((live, old))
            _rename(staged, live)
            placed.append((staged, live))
    except OSError as exc:
        try:
            for staged, live in reversed(placed):
                _rename(live, staged)
            for live, old in reversed(aside):
                _rename(old, live)
        except OSError as undo:
            raise DeployError(
                f"swap failed ({exc}) and so did undoing it ({undo}). Fix GOS_APP by hand: "
                + "; ".join(f"{old.name} should be {live.name}" for live, old in aside)
            ) from undo
        raise DeployError(
            f"couldn't swap the release in ({exc}). Something is probably running from "
            "that folder: close the gos tools you started from GOS_APP (or wait a minute "
            "for the world backup task), then deploy again. GOS_APP is unchanged"
        ) from exc
    return [old for _live, old in aside]


def _remove(path: Path) -> bool:
    """Delete a folder deploy made; False if Windows refuses (a file in it is in use)."""

    def writable(func: Callable[..., Any], name: str, _exc: BaseException) -> None:
        os.chmod(name, stat.S_IWRITE)  # read-only files on Windows
        func(name)

    try:
        shutil.rmtree(path, onexc=writable)
    except OSError:
        return False
    return True


def _clear(app: Path) -> None:
    """Remove staging and old folders an earlier deploy left; skip any still in use."""
    for entry in app.iterdir():
        if entry.name.startswith((STAGING, OLD)) and entry.is_dir():
            _remove(entry)


def _dacl_protected(path: Path) -> bool:
    """Whether the folder's DACL is protected from inheritance (SE_DACL_PROTECTED)."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = advapi32.GetNamedSecurityInfoW
    get_info.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_info.restype = wintypes.DWORD
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_control.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    se_file_object, dacl_security_information, se_dacl_protected = 1, 4, 0x1000
    error = get_info(
        str(path),
        se_file_object,
        dacl_security_information,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(sd),
    )
    if error:
        raise ctypes.WinError(error)
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not get_control(sd, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(control.value & se_dacl_protected)
    finally:
        kernel32.LocalFree(sd)
