from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from gregtec import cli
from gregtec.vault import deploy, io, schema

TEMPLATE = Path(__file__).resolve().parents[2] / "vault-template"
TIERS = [
    "ULV",
    "LV",
    "MV",
    "HV",
    "EV",
    "IV",
    "LuV",
    "ZPM",
    "UV",
    "UHV",
    "UEV",
    "UIV",
    "UMV",
    "UXV",
    "MAX",
]
NOTE = "---\nid: ln-steel-01\nt: line\nupd: 2026-11-01\n---\n"


def git(vault: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=vault, capture_output=True, text=True, check=True
    )
    return out.stdout


def commits(vault: Path) -> int:
    return int(git(vault, "rev-list", "--count", "HEAD"))


def test_every_template_note_follows_the_schema() -> None:
    types = schema.load_types(TEMPLATE / "_gos" / "schema.md")
    # Skills and subagent files are the SDK's, not notes (vault.io.NOT_NOTES).
    notes = sorted(
        n
        for n in TEMPLATE.rglob("*.md")
        if not n.relative_to(TEMPLATE).as_posix().startswith(io.NOT_NOTES)
    )
    assert notes
    assert {str(n.relative_to(TEMPLATE)): schema.check(n, types) for n in notes} == {
        str(n.relative_to(TEMPLATE)): [] for n in notes
    }


def test_glossary_seed() -> None:
    lines = (TEMPLATE / "glossary.tsv").read_text(encoding="utf-8").splitlines()
    rows = [line.split("\t") for line in lines]
    assert rows[0] == ["term", "aliases", "kind", "ref", "say"]
    assert all(len(r) == 5 for r in rows), [r for r in rows if len(r) != 5]
    terms = [r[0].lower() for r in rows[1:]]
    assert len(terms) == len(set(terms))
    # removed: gone from 2.9.0 (PA), kept so the assistant can say so
    kinds = {"tier", "machine", "material", "item", "mod", "removed", "line"}
    assert {r[2] for r in rows[1:]} == kinds
    tiers = [r for r in rows[1:] if r[2] == "tier"]
    assert [r[0] for r in tiers] == TIERS
    assert [r[3] for r in tiers] == [f"tier:{i}" for i in range(len(TIERS))]
    assert all(r[4] for r in tiers)  # every tier has a pronunciation


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("id: x\n", "no frontmatter"),
        ("---\nid: x\nt: line\n", "not closed"),
        ("---\nt: line\nid: x\nupd: 2026-11-01\n---\n", "exactly id, t, upd"),
        (NOTE + "This line is prose.\n", "not key: value"),
        (NOTE + "```tsv\na\tb\n", "fence is not closed"),
        (NOTE + "```tsv\na\tb\nc\n```\n", "same number of columns"),
        (NOTE + "k: v\n" * 150, "cap is 150"),
    ],
)
def test_parse_rejects(text: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        schema.parse(text)


def test_check_keys_ids_and_types(tmp_path: Path) -> None:
    types = schema.load_types(TEMPLATE / "_gos" / "schema.md")
    good = tmp_path / "good.md"
    good.write_text(NOTE + "st: running\ntgt_rate_s: 2\nnote: 4 EBF\n")
    assert schema.check(good, types) == []
    bad = tmp_path / "bad.md"
    bad.write_text(NOTE.replace("ln-steel-01", "steel") + "colour: red\n")
    assert schema.check(bad, types) == [
        "id steel doesn't match ln-* for t: line",
        "key colour isn't allowed for t: line",
    ]
    odd = tmp_path / "odd.md"
    odd.write_text(NOTE.replace("t: line", "t: recipe"))
    assert schema.check(odd, types) == ["unknown type t: recipe"]


def test_first_deploy_creates_a_git_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    assert deploy.deploy(vault, TEMPLATE) == "created"
    assert commits(vault) == 1
    template_files = {
        p.relative_to(TEMPLATE): p.read_bytes()
        for p in TEMPLATE.rglob("*")
        if p.is_file()
    }
    vault_files = {
        p.relative_to(vault): p.read_bytes()
        for p in vault.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    assert vault_files == template_files
    # A clean tree: the first commit holds every file.
    assert git(vault, "status", "--porcelain") == ""
    assert deploy.deploy(vault, TEMPLATE) == "unchanged"


def test_first_deploy_needs_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))  # no git in here
    vault = tmp_path / "vault"
    with pytest.raises(RuntimeError, match="git isn't on PATH"):
        deploy.deploy(vault, TEMPLATE)
    assert not vault.exists()


def test_redeploy_restores_gos_and_leaves_everything_else(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    deploy.deploy(vault, TEMPLATE)
    goals = vault / "me" / "goals.md"
    goals.write_text(goals.read_text() + "goal: reach HV\n")  # Clayton's edit
    plan = vault / "plans" / "pl-0001.md"
    plan.write_text("---\nid: pl-0001\nt: plan\nupd: 2026-11-02\n---\n")
    # A live edit of a repo-owned file, a stray file, and a deleted folder.
    (vault / "_gos" / "schema.md").write_text("tampered")
    (vault / "_gos" / "skills" / "stray.md").write_text("x")
    for keep in (vault / "inbox").iterdir():
        keep.unlink()
    (vault / "inbox").rmdir()

    assert deploy.deploy(vault, TEMPLATE) == "updated"
    schema_note = vault / "_gos" / "schema.md"
    assert schema_note.read_bytes() == (TEMPLATE / "_gos" / "schema.md").read_bytes()
    assert not (vault / "_gos" / "skills" / "stray.md").exists()
    assert (vault / "inbox" / ".gitkeep").is_file()
    assert goals.read_text().endswith("goal: reach HV\n")
    assert plan.is_file()
    assert commits(vault) == 1  # back to the committed state: nothing new to commit
    assert git(vault, "diff", "--cached", "--name-only") == ""  # nothing staged
    status = git(vault, "status", "--porcelain")
    assert " M me/goals.md" in status.splitlines()
    assert "?? plans/pl-0001.md" in status.splitlines()
    assert "_gos" not in status


def test_redeploy_never_commits_a_deleted_folder(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    deploy.deploy(vault, TEMPLATE)
    plan = vault / "plans" / "pl-0001.md"
    plan.write_text("---\nid: pl-0001\nt: plan\nupd: 2026-11-02\n---\n")
    git(vault, "add", "plans/pl-0001.md")
    git(
        vault,
        "-c",
        "user.name=agent",
        "-c",
        "user.email=agent@localhost",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        "agent note",
    )
    # Deleted by hand: a folder with a committed note, and me/.
    shutil.rmtree(vault / "plans")
    shutil.rmtree(vault / "me")

    assert deploy.deploy(vault, TEMPLATE) == "updated"
    assert (vault / "plans" / ".gitkeep").is_file()
    assert commits(vault) == 2  # no deploy commit: the deletions are not deploy's
    assert git(vault, "diff", "--cached", "--name-only") == ""
    status = git(vault, "status", "--porcelain").splitlines()
    assert {" D plans/pl-0001.md", " D me/goals.md", " D me/prefs.md"} <= set(status)


def test_redeploy_finishes_a_failed_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = tmp_path / "repo" / "vault-template"
    shutil.copytree(TEMPLATE, template)
    vault = tmp_path / "home" / "vault"
    deploy.deploy(vault, template)
    skill = template / "_gos" / "skills" / "recipe-lookup" / "SKILL.md"
    skill.parent.mkdir(exist_ok=True)  # the template has the real skill
    skill.write_text("name: recipe-lookup\n")
    real_git = deploy._git

    def git_that_cannot_commit(cwd: Path, *args: str) -> str:
        if args[0] == "commit":
            raise subprocess.CalledProcessError(128, ["git", *args])
        return real_git(cwd, *args)

    with monkeypatch.context() as m:
        m.setattr(deploy, "_git", git_that_cannot_commit)
        with pytest.raises(subprocess.CalledProcessError):
            deploy.deploy(vault, template)
    assert (vault / "_gos" / "skills" / "recipe-lookup" / "SKILL.md").is_file()
    assert commits(vault) == 1  # mirrored and staged, never committed

    assert deploy.deploy(vault, template) == "updated"
    assert commits(vault) == 2
    assert git(vault, "show", "--name-only", "--format=", "HEAD").split() == [
        "_gos/skills/recipe-lookup/SKILL.md"
    ]
    assert git(vault, "status", "--porcelain") == ""
    assert deploy.deploy(vault, template) == "unchanged"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_redeploy_keeps_a_scripts_mode(tmp_path: Path) -> None:
    template = tmp_path / "repo" / "vault-template"
    shutil.copytree(TEMPLATE, template)
    vault = tmp_path / "home" / "vault"
    deploy.deploy(vault, template)
    script = template / "_gos" / "skills" / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)

    assert deploy.deploy(vault, template) == "updated"
    mode = (vault / "_gos" / "skills" / "run.sh").stat().st_mode
    assert stat.S_IMODE(mode) == 0o755


def test_new_template_version_is_committed(tmp_path: Path) -> None:
    template = tmp_path / "repo" / "vault-template"
    shutil.copytree(TEMPLATE, template)
    vault = tmp_path / "home" / "vault"
    deploy.deploy(vault, template)
    skill = template / "_gos" / "skills" / "recipe-lookup" / "SKILL.md"
    skill.parent.mkdir(exist_ok=True)  # the template has the real skill
    skill.write_text("name: recipe-lookup\n")
    goals = vault / "me" / "goals.md"
    goals.write_text(goals.read_text() + "goal: reach HV\n")
    git(vault, "add", "me/goals.md")  # staged by Clayton: still not deploy's to commit
    plan = vault / "plans" / "pl-0001.md"  # an agent's note: deploy must not stage it
    plan.write_text("---\nid: pl-0001\nt: plan\nupd: 2026-11-02\n---\n")

    assert deploy.deploy(vault, template) == "updated"
    assert (vault / "_gos" / "skills" / "recipe-lookup" / "SKILL.md").is_file()
    assert commits(vault) == 2
    assert git(vault, "show", "--name-only", "--format=", "HEAD").split() == [
        "_gos/skills/recipe-lookup/SKILL.md"
    ]
    assert git(vault, "diff", "--cached", "--name-only").split() == ["me/goals.md"]
    assert "?? plans/pl-0001.md" in git(vault, "status", "--porcelain").splitlines()


def test_refuses_the_repo_and_unknown_folders(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="into or around the repo"):
        deploy.deploy(TEMPLATE.parent / "vault", TEMPLATE)
    plain = tmp_path / "notes"
    plain.mkdir()
    with pytest.raises(ValueError, match="isn't a git-backed vault"):
        deploy.deploy(plain, TEMPLATE)


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["vault", "deploy", "--vault", str(tmp_path / "v")]) == 0
    assert capsys.readouterr().out.startswith("created: ")
