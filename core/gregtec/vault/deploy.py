"""Deploy ``vault-template/`` into a live vault (WO-0.5, plan section 6.5).

First run: copy the whole template, ``git init`` and commit. Later runs: mirror only ``_gos/``,
which is repo-owned (skills and agents are authored in the repo, never edited in the live
vault), and add any template folder that is missing. Notes, ``me/`` and everything else in the
live vault are never touched again.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TEMPLATE = REPO_ROOT / "vault-template"
OWNED = "_gos"
# The vault is a data repo: commit as GOS, never sign or use the developer's hooks.
GIT = [
    "git",
    "-c",
    "user.name=Gregtec OS",
    "-c",
    "user.email=gos@localhost",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.hooksPath=",
]


def _git(vault: Path, *args: str) -> str:
    out = subprocess.run(
        [*GIT, *args], cwd=vault, check=True, capture_output=True, text=True
    )
    return out.stdout


def _is_under(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _copy_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".gos-tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def deploy(vault: Path, template: Path = DEFAULT_TEMPLATE) -> str:
    """Create or update ``vault`` from ``template``. Returns created, updated or unchanged."""
    vault, template = vault.resolve(), template.resolve()
    if not (template / OWNED / "schema.md").is_file():
        raise ValueError(f"{template} is not a vault template")
    repo = template.parent
    if _is_under(vault, repo) or _is_under(repo, vault):
        raise ValueError(f"refusing to deploy into or around the repo: {vault}")

    if not vault.exists():
        shutil.copytree(template, vault)
        _git(vault, "init", "-q")
        _git(vault, "add", "-A")
        _git(vault, "commit", "-q", "-m", "Deploy vault template")
        return "created"
    if not (vault / ".git").is_dir():
        raise ValueError(
            f"{vault} exists but isn't a git-backed vault; refusing to touch it"
        )

    changed = False
    for src in sorted(p for p in (template / OWNED).rglob("*") if p.is_file()):
        dst = vault / src.relative_to(template)
        if not dst.is_file() or not filecmp.cmp(src, dst, shallow=False):
            _copy_atomic(src, dst)
            changed = True
    for dst in sorted(p for p in (vault / OWNED).rglob("*") if p.is_file()):
        if not (template / dst.relative_to(vault)).is_file():
            dst.unlink()
            changed = True
    created: list[str] = []
    for folder in sorted(p for p in template.rglob("*") if p.is_dir()):
        target = vault / folder.relative_to(template)
        if not target.exists():
            target.mkdir(parents=True)
            keep = folder / ".gitkeep"
            if keep.is_file():
                shutil.copyfile(keep, target / ".gitkeep")
            created.append(target.relative_to(vault).as_posix())

    if not changed and not created:
        return "unchanged"
    # Stage and commit only what deploy owns; Clayton's and the agents' edits stay as they are.
    paths = [OWNED, *created]
    _git(vault, "add", "-A", "--", *paths)
    staged = subprocess.run(
        [*GIT, "diff", "--cached", "--quiet", "--", *paths], check=False, cwd=vault
    )
    if staged.returncode:
        _git(
            vault, "commit", "-q", "-m", f"Deploy {OWNED}/ from the repo", "--", *paths
        )
    return "updated"
