"""WO-2.5: vault I/O and the plan 6.5 path guard (gregtec.vault.io)."""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path

import pytest

from gregtec.vault import io, schema

TEMPLATE = Path(__file__).resolve().parents[2] / "vault-template"
TODAY = dt.date(2026, 11, 6)
LINE = "st: running\ntgt: i:steel_ingot\ntgt_rate_s: 2\nplan: pl-0007\n"


@pytest.fixture
def vault(tmp_path: Path) -> io.VaultIO:
    root = tmp_path / "vault"
    shutil.copytree(TEMPLATE, root)
    return io.VaultIO(root, today=lambda: TODAY)


def text(v: io.VaultIO, rel: str) -> str:
    return (v.root / rel).read_text(encoding="utf-8")


def leftovers(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.name.endswith(".gos-tmp")]


# --- paths and the guard -------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "plans/pl-0007.md",
        "me/goals.md",
        "_gos/schema.md",
        "log/wk/2026-W44.md",
        "INDEX.md",
    ],
)
def test_clean_accepts_note_paths(rel: str) -> None:
    assert io.clean(rel) == rel


@pytest.mark.parametrize(
    ("rel", "why"),
    [
        ("", "give the note's path"),
        ("plans\\pl-1.md", "use / between folders"),
        ("../outside.md", "doesn't start with a dot"),
        ("plans/../me/goals.md", "doesn't start with a dot"),
        (".git/config.md", "doesn't start with a dot"),
        (".obsidian/app.md", "doesn't start with a dot"),
        ("/etc/x.md", "letters, digits"),
        ("C:/x.md", "letters, digits"),
        ("plans/a b.md", "letters, digits"),
        ("plans/pl-1.md:stream", "letters, digits"),
        ("plans/x.", "letters, digits"),
        ("plans/con.md", "Windows device name"),
        ("plans/NUL.md", "Windows device name"),
        ("glossary.tsv", "notes are .md files"),
        ("plans/", "letters, digits"),
        ("a/b/c/d/e/f/g.md", "too long"),
        ("plans/" + "x" * 200 + ".md", "too long"),
    ],
)
def test_clean_refuses(rel: str, why: str) -> None:
    with pytest.raises(io.VaultError, match=why):
        io.clean(rel)


def test_a_path_to_write_is_lower_case() -> None:
    """Windows ignores case: ME/goals.md would be me/goals.md."""
    with pytest.raises(io.VaultError, match="lower-case only"):
        io.clean("ME/goals.md", write=True)
    assert io.clean("ME/goals.md") == "ME/goals.md"  # reading is fine


W, A, P = io.WRITE, io.APPEND, io.PROPOSE


@pytest.mark.parametrize(
    ("rel", "op", "why"),
    [
        ("me/goals.md", W, "Clayton's"),
        ("me/new.md", A, "Clayton's"),
        ("_gos/schema.md", W, "the repo's"),
        ("_gos/skills/x/skill.md", W, "the repo's"),
        ("INDEX.md", W, "generated"),
        ("base/tier.md", A, "generated"),
        ("base/machines.md", W, "generated"),
        ("base/storage.md", W, "generated"),
        ("notes.md", W, "isn't in a folder agents write"),
        ("other/x.md", A, "isn't in a folder agents write"),
        ("log/wk/2026-W44.md", A, "curator's"),
        ("log/mo/2026-10.md", W, "curator's"),
        ("log/2026-11-05.md", A, "only today's log"),
        ("log/2026-11-06.md", W, "append-only; use note_append"),
        ("log/notes.md", A, "daily log"),
        ("inbox/ib-20261106-x.md", W, "come from inbox_propose"),
        ("inbox/ib-schema-check.md", W, "come from inbox_propose"),
        ("plans/pl-1.md", P, "proposals go in inbox/"),
        ("ME/goals.md", W, "Clayton's"),
        ("Plans/pl-1.md", W, "lower-case only"),
    ],
)
def test_write_refusals(rel: str, op: str, why: str) -> None:
    refusal = io.write_refusal(rel, op, today=TODAY)
    assert refusal is not None and why in refusal


@pytest.mark.parametrize(
    ("rel", "op"),
    [
        ("base/power.md", W),
        ("base/lines/ln-steel-01.md", W),
        ("plans/pl-0007.md", W),
        ("plans/pl-0007.md", A),
        ("quests/qs-steam.md", W),
        ("themes/th-brass.md", W),
        ("personas/pe-ship.md", A),
        ("inbox/ib-20261106-x.md", A),
        ("inbox/ib-20261106-x.md", P),
        ("log/2026-11-06.md", A),
        ("log/2026-11-06-2.md", A),
    ],
)
def test_agents_may_write(rel: str, op: str) -> None:
    assert io.write_refusal(rel, op, today=TODAY) is None


def test_the_guard_without_today_uses_the_local_date() -> None:
    """The hook-style call: an old daily log is refused even when no ``today`` is given."""
    today = dt.date.today()
    assert io.write_refusal(f"log/{today.isoformat()}.md", A) is None
    why = io.write_refusal("log/2020-01-01.md", A)
    assert why is not None and "only today's log" in why
    with pytest.raises(ValueError, match="op must be one of"):
        io.write_refusal("plans/pl-1.md", "edit")


def test_the_vaults_refusal_adds_the_link_check(vault: io.VaultIO) -> None:
    assert vault.refusal("plans/pl-1.md", W) is None
    assert "Clayton's" in (vault.refusal("me/goals.md", W) or "")
    assert "only today's log" in (vault.refusal("log/2026-11-05.md", A) or "")
    assert "inbox_propose" in (vault.refusal("inbox/ib-schema-check.md", W) or "")
    if sys.platform != "win32":
        (vault.root / "plans" / "pl-link.md").symlink_to(vault.root / "me" / "goals.md")
        assert "a link leads elsewhere" in (vault.refusal("plans/pl-link.md", W) or "")


def test_the_guard_matches_the_schema_writers() -> None:
    """_gos/schema.md's writer column and the guard agree on who writes each note type."""
    types = schema.load_types(TEMPLATE / "_gos" / "schema.md")
    for kind in types.values():
        rel = (
            kind.path
            if not kind.path.endswith("/")
            else f"{kind.path}{kind.id.rstrip('*')}x.md"
        )
        op = W
        if kind.t == "log":
            rel, op = f"log/{TODAY.isoformat()}.md", A
        elif kind.t == "inbox":
            op = P
        refused = io.write_refusal(rel, op, today=TODAY) is not None
        assert refused == (kind.writer in ("repo", "clayton", "generated")), kind.t


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)
def test_a_link_cant_lead_a_write_out_of_its_folder(vault: io.VaultIO) -> None:
    (vault.root / "plans" / "pl-link.md").symlink_to(vault.root / "me" / "goals.md")
    shutil.rmtree(vault.root / "themes")
    (vault.root / "themes").symlink_to(vault.root / "me")
    before = text(vault, "me/goals.md")
    for rel in ("plans/pl-link.md", "themes/th-x.md"):
        with pytest.raises(io.VaultError, match="a link leads elsewhere"):
            vault.write(rel, "note: x")
    assert text(vault, "me/goals.md") == before
    assert not (vault.root / "me" / "th-x.md").exists()


# --- read ----------------------------------------------------------------------------------


def test_read(vault: io.VaultIO) -> None:
    path, body = vault.read("me/goals.md")
    assert path == "me/goals.md" and body.startswith("---\nid: me-goals\n")
    with pytest.raises(io.VaultError, match="no note at plans/pl-9.md"):
        vault.read("plans/pl-9.md")
    with pytest.raises(io.VaultError, match="doesn't start with a dot"):
        vault.read(".git/HEAD.md")


def test_read_refuses_a_huge_note(vault: io.VaultIO) -> None:
    (vault.root / "plans" / "pl-big.md").write_bytes(b"x" * (io.MAX_READ_BYTES + 1))
    with pytest.raises(io.VaultError, match="KiB"):
        vault.read("plans/pl-big.md")


def test_no_vault(tmp_path: Path) -> None:
    with pytest.raises(io.VaultError, match="gos vault deploy"):
        io.VaultIO(tmp_path / "nope").read("me/goals.md")


# --- write ---------------------------------------------------------------------------------


def test_write_makes_the_frontmatter_from_the_path(vault: io.VaultIO) -> None:
    assert vault.write("base/lines/ln-steel-01.md", LINE) == "base/lines/ln-steel-01.md"
    assert text(vault, "base/lines/ln-steel-01.md") == (
        "---\nid: ln-steel-01\nt: line\nupd: 2026-11-06\n---\n" + LINE
    )
    vault.write("base/power.md", "eut_net: 3120\n")
    assert text(vault, "base/power.md").startswith("---\nid: base-power\nt: power\n")
    assert leftovers(vault.root) == []


def test_write_replaces_and_drops_a_copied_frontmatter(vault: io.VaultIO) -> None:
    vault.write("plans/pl-0007.md", "st: draft\n")
    old = "---\nid: pl-0007\nt: plan\nupd: 2026-11-01\n---\nst: done\n"
    vault.write("plans/pl-0007.md", old)
    assert text(vault, "plans/pl-0007.md") == (
        "---\nid: pl-0007\nt: plan\nupd: 2026-11-06\n---\nst: done\n"
    )


@pytest.mark.parametrize(
    ("rel", "body", "why"),
    [
        ("plans/pl-1.md", "This plan makes steel.", "is not key: value"),
        ("plans/pl-1.md", "Rate_s: 2", "is not key: value"),
        ("plans/pl-1.md", "```tsv\na\tb\n1\n```", "same number of columns"),
        ("plans/pl-1.md", "```tsv\na\tb", "tsv fence is not closed"),
        ("plans/pl-1.md", "\n".join(f"k{i}: v" for i in range(146)), "the cap is 150"),
        ("base/lines/ln-1.md", "colour: blue", "key colour isn't allowed"),
        ("base/lines/steel.md", "st: running", "doesn't match ln-*"),
        ("plans/x.md", "st: draft", "doesn't match pl-*"),
        ("base/odd.md", "st: x", "no note type at base/odd.md"),
    ],
)
def test_write_checks_the_grammar(
    vault: io.VaultIO, rel: str, body: str, why: str
) -> None:
    with pytest.raises(io.VaultError, match=why):
        vault.write(rel, body)
    assert not (vault.root / rel).exists()


def test_write_refuses_logs_inbox_and_claytons_notes(vault: io.VaultIO) -> None:
    goals = text(vault, "me/goals.md")
    for rel, why in [
        ("me/goals.md", "Clayton's"),
        ("_gos/schema.md", "the repo's"),
        ("INDEX.md", "generated"),
        ("log/2026-11-06.md", "append-only"),
        ("inbox/ib-20261106-x.md", "inbox_propose"),
    ]:
        with pytest.raises(io.VaultError, match=why):
            vault.write(rel, "note: x")
    assert text(vault, "me/goals.md") == goals


def test_write_refuses_carriage_returns_and_nul(vault: io.VaultIO) -> None:
    vault.write("plans/pl-1.md", "st: a\r\nnote: b\r\n")  # CRLF is fine: written as LF
    assert "\r" not in text(vault, "plans/pl-1.md")
    for bad in ("st: a\rnote: b", "st: a\x00"):
        with pytest.raises(io.VaultError, match="carriage return or NUL"):
            vault.write("plans/pl-2.md", bad)


def test_write_is_atomic(vault: io.VaultIO, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed replace leaves the old note and no temporary file."""
    vault.write("plans/pl-1.md", "st: a\n")

    def fail(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(io.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        vault.write("plans/pl-1.md", "st: b\n")
    assert text(vault, "plans/pl-1.md").endswith("st: a\n")
    assert leftovers(vault.root) == []


def test_atomic_write_retries_a_note_held_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = os.replace

    def flaky(src: str, dst: str) -> None:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "in use")
        real(src, dst)

    monkeypatch.setattr(io.os, "replace", flaky)
    monkeypatch.setattr(io.time, "sleep", lambda s: None)
    io.atomic_write(tmp_path / "n.md", b"x")
    assert (tmp_path / "n.md").read_bytes() == b"x" and len(calls) == 3


def test_on_write_mirrors_and_its_failure_is_only_logged(
    vault: io.VaultIO, caplog: pytest.LogCaptureFixture
) -> None:
    seen: list[str] = []
    vault.on_write = seen.append
    vault.write("plans/pl-1.md", "st: a")
    assert seen == ["plans/pl-1.md"]

    def broken(rel: str) -> None:
        raise RuntimeError("db gone")

    vault.on_write = broken
    with caplog.at_level("ERROR", logger="gregtec.vault.io"):
        vault.write("plans/pl-2.md", "st: a")
    assert (vault.root / "plans" / "pl-2.md").is_file()
    assert any("note_fts" in r.getMessage() for r in caplog.records)


# --- append --------------------------------------------------------------------------------


def test_append_makes_todays_log_then_only_appends(vault: io.VaultIO) -> None:
    rel = "log/2026-11-06.md"
    vault.append(rel, "ev: 14:32 built ebf\n")
    first = (vault.root / rel).read_bytes()
    assert (
        first
        == b"---\nid: log-2026-11-06\nt: log\nupd: 2026-11-06\n---\nev: 14:32 built ebf\n"
    )
    vault.append(rel, "ev: 14:40 q:1234 done\nnote: steam is low")
    second = (vault.root / rel).read_bytes()
    assert second.startswith(first)
    assert second[len(first) :] == b"ev: 14:40 q:1234 done\nnote: steam is low\n"


def test_append_to_a_log_without_a_final_newline(vault: io.VaultIO) -> None:
    rel = "log/2026-11-06.md"
    (vault.root / rel).write_text(
        "---\nid: log-2026-11-06\nt: log\nupd: 2026-11-06\n---\nev: a", encoding="utf-8"
    )
    vault.append(rel, "ev: b")
    assert text(vault, rel).endswith("ev: a\nev: b\n")


def test_append_refusals(vault: io.VaultIO) -> None:
    rel = "log/2026-11-06.md"
    vault.append(rel, "ev: a")
    before = text(vault, rel)
    for path, lines, why in [
        ("log/2026-11-05.md", "ev: a", "only today's log"),
        ("log/wk/2026-W44.md", "ev: a", "curator's"),
        (rel, "Built the EBF today.", "is not key: value"),
        (rel, "---\nid: x", "body lines only"),
        (rel, "", "nothing to append"),
        (rel, "n_items: 5", "curator's counts"),  # D143: n_ is reserved for the counts
        ("plans/pl-9.md", "st: a", "note_write makes it"),
        ("inbox/ib-nope.md", "note: a", "inbox_propose makes it"),
        ("me/goals.md", "goal: x", "Clayton's"),
    ]:
        with pytest.raises(io.VaultError, match=why):
            vault.append(path, lines)
    assert text(vault, rel) == before


def test_a_full_log_says_where_to_go_on(vault: io.VaultIO) -> None:
    rel = "log/2026-11-06.md"
    vault.append(rel, "\n".join(f"ev: {i}" for i in range(140)))
    with pytest.raises(io.VaultError, match=r"append to log/2026-11-06-2\.md"):
        vault.append(rel, "\n".join(f"ev: {i}" for i in range(10)))
    vault.append("log/2026-11-06-2.md", "ev: more")
    assert "id: log-2026-11-06-2" in text(vault, "log/2026-11-06-2.md")


def test_append_to_a_note_rewrites_it_with_todays_date(vault: io.VaultIO) -> None:
    (vault.root / "plans" / "pl-1.md").write_text(
        "---\nid: pl-1\nt: plan\nupd: 2026-11-01\n---\nst: draft\n", encoding="utf-8"
    )
    vault.append("plans/pl-1.md", "note: pinned the EBF route")
    assert text(vault, "plans/pl-1.md") == (
        "---\nid: pl-1\nt: plan\nupd: 2026-11-06\n---\nst: draft\nnote: pinned the EBF route\n"
    )


def test_append_to_a_note_with_no_body_and_no_final_newline(vault: io.VaultIO) -> None:
    (vault.root / "plans" / "pl-1.md").write_text(
        "---\nid: pl-1\nt: plan\nupd: 2026-11-01\n---", encoding="utf-8"
    )
    vault.append("plans/pl-1.md", "note: x")
    assert text(vault, "plans/pl-1.md") == (
        "---\nid: pl-1\nt: plan\nupd: 2026-11-06\n---\nnote: x\n"
    )


def test_append_to_a_broken_note_is_refused(vault: io.VaultIO) -> None:
    (vault.root / "plans" / "pl-1.md").write_text("no frontmatter\n", encoding="utf-8")
    with pytest.raises(io.VaultError, match="note_write a fixed copy"):
        vault.append("plans/pl-1.md", "st: a")


# --- inbox_propose -------------------------------------------------------------------------


def test_propose(vault: io.VaultIO) -> None:
    rel = vault.propose(
        "Raise the scarcity weight", "weights: scarcity 2\nnote: tin is short"
    )
    assert rel == "inbox/ib-20261106-raise-the-scarcity-weight.md"
    assert text(vault, rel) == (
        "---\nid: ib-20261106-raise-the-scarcity-weight\nt: inbox\nupd: 2026-11-06\n---\n"
        "title: Raise the scarcity weight\nweights: scarcity 2\nnote: tin is short\n"
    )
    again = vault.propose("Raise the  scarcity weight", "note: again")
    assert again == "inbox/ib-20261106-raise-the-scarcity-weight-2.md"
    assert "note: tin is short" in text(vault, rel)  # never replaced
    assert vault.propose("!!!", "note: x") == "inbox/ib-20261106-proposal.md"


def test_propose_refusals(vault: io.VaultIO) -> None:
    for title, body, why in [
        ("", "note: x", "one-line title"),
        ("x" * 121, "note: x", "under 120"),
        ("Change goals", "Please change my goals.", "is not key: value"),
    ]:
        with pytest.raises(io.VaultError, match=why):
            vault.propose(title, body)
    assert list((vault.root / "inbox").glob("*.md")) == []


# --- notes() -------------------------------------------------------------------------------


def test_notes_skips_hidden_folders_skills_and_other_files(vault: io.VaultIO) -> None:
    root = vault.root
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "x.md").write_text("x", encoding="utf-8")
    (root / "_gos" / "skills" / "recipe-lookup").mkdir(parents=True, exist_ok=True)
    (root / "_gos" / "skills" / "recipe-lookup" / "SKILL.md").write_text(
        "x", encoding="utf-8"
    )
    (root / "plans" / "pl-1.md").write_text("x", encoding="utf-8")
    (root / "plans" / "a b.md").write_text("x", encoding="utf-8")
    found = list(io.notes(root))
    assert found == sorted(found)
    assert {
        "INDEX.md",
        "_gos/schema.md",
        "me/goals.md",
        "me/prefs.md",
        "plans/pl-1.md",
    } == set(found)


def test_notes_skips_junctions(
    vault: io.VaultIO, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows junction isn't a symlink and os.walk enters it; notes() doesn't."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pl-x.md").write_text("x", encoding="utf-8")
    shutil.copytree(outside, vault.root / "plans" / "far")
    real = Path.is_junction
    monkeypatch.setattr(
        Path, "is_junction", lambda p: p.name == "far" or real(p), raising=True
    )
    assert "plans/far/pl-x.md" not in set(io.notes(vault.root))


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_notes_skips_a_real_junction(vault: io.VaultIO, tmp_path: Path) -> None:
    import subprocess

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pl-x.md").write_text("x", encoding="utf-8")
    link = vault.root / "plans" / "far"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        check=True,
        capture_output=True,
    )
    assert link.is_junction()
    assert "plans/far/pl-x.md" not in set(io.notes(vault.root))
    with pytest.raises(io.VaultError, match="a link leads elsewhere"):
        vault.read("plans/far/pl-x.md")
