r"""``gos deploy``: install a merged commit of this clone into GOS_APP (WO-0.8, D75, plan 4.6).

Clayton runs it from his own profile clone, as himself. GOS_APP (``C:\GregtecOS-app``) is made by
``scripts/setup_gos_dev.ps1``: owner Administrators, full control for SYSTEM, Administrators and
Clayton, gos-dev read only, no inheritance from ``C:\``. Deploy never creates it and never writes
outside it::

    GOS_APP\
      current\     git archive of the commit, its .venv and release.json (what, when, from where)
      scripts\     a copy of current\scripts: fixed paths for the world backup task and Prism hooks

How a deploy runs:

1. Refuse unless GOS_APP is the configured folder, exists, is reached through no link, and
   doesn't overlap the repo, Prism or GOS_HOME. On Windows also refuse unless its ACL is setup's:
   protected from inheritance, owned by Administrators (gos-dev can't give a folder it made to
   Administrators), and no write access for anyone but SYSTEM, Administrators and the deployer.
2. Refuse unless the commit is in ``origin/main`` and the working tree has no uncommitted changes.
3. Build the release in ``GOS_APP\.gos-staging-build-*``: ``git archive``, then
   ``uv venv --relocatable`` and ``uv sync --locked --no-dev`` with ``UV_LINK_MODE=copy``. Copies,
   not hardlinks into uv's cache, so every file is new in GOS_APP and inherits its ACL. The
   editable install's ``.pth`` is made relative. Then rename the build folder to
   ``.gos-staging-*`` and check the venv there, so a path left pointing at the build folder fails
   the check instead of breaking the deployed release.
4. Swap by renames inside GOS_APP (``current`` and ``scripts`` aside, the staged ones in). A folder
   renamed within the same parent keeps the ACEs it inherited there; nothing is moved in from
   outside, so no source ACL comes along. If a rename fails (Windows refuses while a process runs
   from the folder) or the swap is interrupted (Ctrl+C), every rename is undone and GOS_APP is as
   it was.
5. Delete the old folders. One still in use is left and reported; the next deploy removes it. If a
   crash mid-swap left ``current`` or ``scripts`` missing, the next deploy first renames the newest
   matching ``.gos-old-*`` folder back.
"""

from __future__ import annotations

import ctypes
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
SYSTEM, ADMINS = "S-1-5-18", "S-1-5-32-544"
# OWNER RIGHTS: an ACE for it grants its rights to whoever owns the object, nobody else.
# Python >= 3.12.4 adds one when it makes a folder with mode=0o700 (pytest's temp folders).
OWNER_RIGHTS = "S-1-3-4"
# setup_gos_dev.ps1's $WriteMask: write/append data, write EA and attributes, delete, delete
# child, WRITE_DAC, WRITE_OWNER, GENERIC_WRITE, GENERIC_ALL.
WRITE_MASK = 0x000D0156 | 0x50000000


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


@dataclass(frozen=True)
class Acl:
    """What deploy checks of GOS_APP's security descriptor on Windows."""

    owner: str  # SID
    protected: bool  # SE_DACL_PROTECTED: inherits nothing from its parent
    # (SID, access mask) of each allow ACE, inherit-only ones too: children get those.
    allows: tuple[tuple[str, int], ...]


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
    _restore(app)
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
    build, staged, staged_scripts = (
        app / f"{STAGING}build-{stamp}",
        app / f"{STAGING}{stamp}",
        app / f"{STAGING}scripts-{stamp}",
    )
    deployed_at = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        os.mkdir(build)  # inside GOS_APP, so it inherits GOS_APP's ACL
        _extract(repo, commit, build)
        for rel in REQUIRED:
            if not (build / rel).is_file():
                raise DeployError(f"{commit[:12]} has no {rel}; refusing to deploy it")
        _build_env(build)
        relocate_editable(build)
        _rename(build, staged)  # a path still pointing at build now fails the check
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
        for path in (build, staged, staged_scripts):
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
    if sys.platform == "win32":
        problem = acl_problem(read_acl(app), current_sid())
        if problem:
            raise DeployError(
                f"{app} {problem}, so it isn't the folder setup_gos_dev.ps1 makes (owner "
                "Administrators; only SYSTEM, Administrators and you may change it; gos-dev "
                "read only). Run the setup first"
            )


def acl_problem(acl: Acl, me: str) -> str | None:
    r"""Why ``acl`` isn't the ACL setup_gos_dev.ps1 gives GOS_APP, or None. ``me``: deployer's SID.

    gos-dev can make a folder where GOS_APP should be (a standard user may create folders in
    ``C:\``) and protect it, but it can't make Administrators the owner.
    """
    if not acl.protected:
        return "inherits its permissions from its parent"
    if acl.owner != ADMINS:
        return f"is owned by {acl.owner}, not Administrators"
    writers = {sid for sid, mask in acl.allows if mask & WRITE_MASK}
    # OWNER RIGHTS writes as the owner, which is Administrators here (checked above). In a
    # file deploy creates inside GOS_APP, the owner is the deployer (``me``).
    writers -= {SYSTEM, ADMINS, me, OWNER_RIGHTS}
    if writers:
        return f"lets {', '.join(sorted(writers))} change it"
    return None


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
    except BaseException as exc:  # Ctrl+C too: never leave GOS_APP half swapped
        try:
            for staged, live in reversed(placed):
                _rename(live, staged)
            for live, old in reversed(aside):
                _rename(old, live)
        except OSError as undo:
            raise DeployError(
                f"swap failed ({exc!r}) and so did undoing it ({undo}). Fix GOS_APP by hand: "
                + "; ".join(f"{old.name} should be {live.name}" for live, old in aside)
            ) from undo
        if not isinstance(exc, OSError):
            raise
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


def _restore(app: Path) -> None:
    """Rename the newest old ``current`` or ``scripts`` back if a crash mid-swap lost it."""
    for live in (app / CURRENT, app / SCRIPTS):
        olds = sorted(app.glob(f"{OLD}{live.name}-*"))  # the stamp sorts by time
        if olds and not os.path.lexists(live):
            _rename(olds[-1], live)


def _clear(app: Path) -> None:
    """Remove staging and old folders an earlier deploy left; skip any still in use."""
    for entry in app.iterdir():
        if entry.name.startswith((STAGING, OLD)) and entry.is_dir():
            _remove(entry)


def read_acl(path: Path) -> Acl:
    """Windows: the folder's owner, whether its DACL is protected, and its allow ACEs."""
    from ctypes import wintypes

    advapi32, kernel32 = _win_dlls()
    owner, dacl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    se_file_object, owner_information, dacl_information = 1, 0x1, 0x4
    se_dacl_protected, access_allowed, access_denied = 0x1000, 0, 1
    error = advapi32.GetNamedSecurityInfoW(
        str(path),
        se_file_object,
        owner_information | dacl_information,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(sd),
    )
    if error:
        raise ctypes.WinError(error)
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            sd, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        protected = bool(control.value & se_dacl_protected)
        if not dacl.value:  # a NULL DACL grants everyone everything
            return Acl(_sid_string(owner), protected, (("S-1-1-0", 0xFFFFFFFF),))
        # ACL header: revision (byte), pad (byte), size (word), ACE count (word), pad (word).
        count = ctypes.cast(
            dacl.value + 4, ctypes.POINTER(wintypes.WORD)
        ).contents.value
        allows: list[tuple[str, int]] = []
        for index in range(count):
            ace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            assert ace.value is not None
            # ACE header: type (byte), flags (byte), size (word); then mask (dword), then SID.
            kind = ctypes.cast(ace.value, ctypes.POINTER(ctypes.c_ubyte)).contents.value
            if kind == access_denied:  # only takes access away
                continue
            if kind != access_allowed:  # setup never writes another kind
                raise DeployError(
                    f"{path} has an ACE of a kind deploy can't check ({kind})"
                )
            mask = ctypes.cast(
                ace.value + 4, ctypes.POINTER(wintypes.DWORD)
            ).contents.value
            allows.append((_sid_string(ctypes.c_void_p(ace.value + 8)), mask))
        return Acl(_sid_string(owner), protected, tuple(allows))
    finally:
        kernel32.LocalFree(sd)


def current_sid() -> str:
    """Windows: the SID of the user running this process."""
    from ctypes import wintypes

    advapi32, kernel32 = _win_dlls()
    token = wintypes.HANDLE()
    token_query, token_user = 0x0008, 1
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, token_user, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token,
            token_user,
            ctypes.cast(buffer, ctypes.c_void_p),
            size,
            ctypes.byref(size),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER starts with SID_AND_ATTRIBUTES, which starts with the SID pointer.
        return _sid_string(
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents
        )
    finally:
        kernel32.CloseHandle(token)


def _sid_string(sid: ctypes.c_void_p) -> str:
    advapi32, kernel32 = _win_dlls()
    text = ctypes.c_void_p()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.wstring_at(text.value)
    finally:
        kernel32.LocalFree(text)


def _win_dlls() -> tuple[Any, Any]:
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr, out = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    get_info = advapi32.GetNamedSecurityInfoW
    get_info.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        out,
        out,
        out,
        out,
        out,
    ]
    get_info.restype = wintypes.DWORD
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = [
        ptr,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_control.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [ptr, wintypes.DWORD, out]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ptr, out]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    handle = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [
        handle,
        wintypes.DWORD,
        ctypes.POINTER(handle),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    get_token = advapi32.GetTokenInformation
    get_token.argtypes = [
        handle,
        ctypes.c_int,
        ptr,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_token.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = handle
    kernel32.CloseHandle.argtypes = [handle]
    kernel32.LocalFree.argtypes = [ptr]
    return advapi32, kernel32
