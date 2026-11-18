"""WO-11.2: backups verified by a restore (D332). The backups are made by WO-2.5's own job
(:class:`gregtec.vault.curator.Curator`, ``backup``) in a temporary GOS_HOME; ``gos backup
restore`` restores them into a temporary folder and checks them; every corruption fails loudly."""

from __future__ import annotations

import datetime as dt
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from gregtec import cli
from gregtec.ops import backups
from gregtec.store import Store
from gregtec.vault import curator, deploy

TEMPLATE = Path(__file__).resolve().parents[3] / "vault-template"
TODAY = dt.date(2026, 11, 11)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A GOS_HOME with a core DB (a few rows), a deployed vault, and today's backups."""
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    cfg = home / "config" / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    store = Store(home / "data" / "gos.sqlite")
    try:
        with store.transaction() as conn:
            for i in range(25):
                conn.execute(
                    "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?)",
                    (f"k{i}", "v", i),
                )
            conn.execute(
                "INSERT INTO action(created, kind, args, state) VALUES (1, 'say', '{}', 'done')"
            )
    finally:
        store.close()
    vault = home / "vault"
    deploy.deploy(vault, TEMPLATE)
    (vault / "plans").mkdir(exist_ok=True)
    (vault / "plans" / "pl-0001.md").write_text(
        "---\nid: pl-0001\n---\nst: x\n", "utf-8"
    )
    (vault / "plans" / ".pl-0002.md.1234abcd.gos-tmp").write_text("half", "utf-8")
    (vault / ".git" / "index.lock").write_text("", "utf-8")  # a killed git run's lock
    run_job(home)
    return home


def run_job(home: Path, day: dt.date = TODAY) -> list[curator.JobResult]:
    cur = curator.Curator(
        vault=home / "vault",
        db=home / "data" / "gos.sqlite",
        backups=home / "data" / "backups",
    )
    return cur.run(day, ["backup"])


def gos(home: Path, *args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = cli.main([*args, "--config", str(home / "config" / "gos.toml")])
    return code, capsys.readouterr().out


def folder(home: Path) -> Path:
    return home / "data" / "backups"


def test_the_daily_job_writes_both_backups_with_manifests(home: Path) -> None:
    names = sorted(p.name for p in folder(home).iterdir())
    assert names == [
        "gos-20261111.sqlite",
        "gos-20261111.sqlite.manifest.json",
        "vault-20261111.zip",
        "vault-20261111.zip.manifest.json",
    ]
    db = json.loads((folder(home) / names[1]).read_text("utf-8"))
    assert db["tables"]["kv"] == 25 and db["tables"]["action"] == 1
    vault = json.loads((folder(home) / names[3]).read_text("utf-8"))
    assert "plans/pl-0001.md" in vault["files"]
    assert "_gos/schema.md" in vault["files"]
    # never a killed write's temp file, never a git lock (a restored lock is a stuck lock)
    assert not [n for n in vault["files"] if n.endswith((".gos-tmp", ".lock"))]
    assert any(n.startswith(".git/") for n in vault["files"])  # history comes back


def test_restore_verify_passes_on_real_backups(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = gos(home, "backup", "restore", "--verify", capsys=capsys)
    assert code == 0, out
    assert "FAIL" not in out
    for check in ("db integrity_check", "db row counts", "vault file hashes"):
        assert f"ok   {check}" in out
    assert "Restore verified into a temporary folder, now removed." in out


def test_restore_to_a_folder_keeps_a_usable_copy(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "restored"
    code, out = gos(home, "backup", "restore", "--to", str(dest), capsys=capsys)
    assert code == 0, out
    conn = sqlite3.connect(dest / "data" / "gos.sqlite")
    try:
        assert conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 25
    finally:
        conn.close()
    assert (
        (dest / "vault" / "plans" / "pl-0001.md").read_text("utf-8").endswith("st: x\n")
    )
    assert not (dest / "vault" / ".git" / "index.lock").exists()


@pytest.mark.parametrize("where", ["data", "vault", "data/backups", "."])
def test_restore_never_goes_over_live_data(
    home: Path, where: str, capsys: pytest.CaptureFixture[str]
) -> None:
    target = home / "data" if where == "." else home / where
    if where == ".":
        target = home  # GOS_HOME itself holds data and vault
    code, out = gos(home, "backup", "restore", "--to", str(target), capsys=capsys)
    assert code == 2 and "Refused" in out


def test_restore_needs_verify_or_to(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = gos(home, "backup", "restore", capsys=capsys)
    assert code == 2 and "--verify" in out


def test_restore_refuses_a_folder_that_is_not_empty(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "busy"
    dest.mkdir()
    (dest / "x").write_text("x", "utf-8")
    code, out = gos(home, "backup", "restore", "--to", str(dest), capsys=capsys)
    assert code == 2 and "not an empty folder" in out


# --- corrupted backups fail loudly ------------------------------------------------------------


def flip(path: Path, offset: int) -> None:
    data = bytearray(path.read_bytes())
    data[offset] ^= 0xFF
    path.write_bytes(bytes(data))


def failed(home: Path, capsys: pytest.CaptureFixture[str]) -> str:
    code, out = gos(home, "backup", "restore", "--verify", capsys=capsys)
    assert code == 1, out
    assert "RESTORE CHECK FAILED" in out
    return out


def test_a_flipped_byte_in_the_db_backup_fails(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = folder(home) / "gos-20261111.sqlite"
    flip(db, db.stat().st_size - 100)
    out = failed(home, capsys)
    assert "FAIL db file hash" in out


def test_a_truncated_db_backup_fails_its_integrity_check(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = folder(home) / "gos-20261111.sqlite"
    with open(db, "r+b") as f:
        f.truncate(4096)
    out = failed(home, capsys)
    assert "FAIL db file hash" in out
    assert "FAIL db integrity_check" in out


def test_row_counts_that_disagree_with_the_manifest_fail(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = folder(home) / "gos-20261111.sqlite"
    m = backups.manifest_path(db)
    doc = json.loads(m.read_text("utf-8"))
    doc["tables"]["kv"] = 26
    doc["sha256"] = backups.sha256_file(db)
    m.write_text(json.dumps(doc), "utf-8")
    out = failed(home, capsys)
    assert "FAIL db row counts: kv: 25 rows, manifest 26" in out


def test_a_backup_without_its_manifest_fails(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backups.manifest_path(folder(home) / "vault-20261111.zip").unlink()
    out = failed(home, capsys)
    assert "FAIL vault manifest: vault-20261111.zip has no manifest" in out


def test_an_unreadable_manifest_fails(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backups.manifest_path(folder(home) / "gos-20261111.sqlite").write_text("{", "utf-8")
    out = failed(home, capsys)
    assert "FAIL db manifest" in out


def test_a_damaged_vault_zip_fails(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flip(folder(home) / "vault-20261111.zip", 200)
    out = failed(home, capsys)
    assert "FAIL vault zip hash" in out


def rezip(zip_path: Path, change: dict[str, bytes | None]) -> None:
    """Rewrite the zip with entries changed (None: removed) and its manifest's zip hash fixed,
    so only the per-file check can catch it."""
    with zipfile.ZipFile(zip_path) as zf:
        entries = {i.filename: zf.read(i) for i in zf.infolist()}
    for name, data in change.items():
        if data is None:
            entries.pop(name)
        else:
            entries[name] = data
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    zip_path.write_bytes(buf.getvalue())
    m = backups.manifest_path(zip_path)
    doc = json.loads(m.read_text("utf-8"))
    doc["sha256"] = backups.sha256_file(zip_path)
    m.write_text(json.dumps(doc), "utf-8")


def test_a_changed_or_missing_vault_file_fails(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rezip(
        folder(home) / "vault-20261111.zip",
        {"plans/pl-0001.md": b"tampered", "_gos/schema.md": None, "extra.md": b"x"},
    )
    out = failed(home, capsys)
    assert "FAIL vault file hashes: 3 wrong" in out
    assert "_gos/schema.md missing" in out and "plans/pl-0001.md differs" in out


def test_a_zip_entry_that_escapes_the_folder_fails(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rezip(folder(home) / "vault-20261111.zip", {"../escape.md": b"x"})
    out = failed(home, capsys)
    assert "unsafe or odd entry '../escape.md'" in out
    assert not (tmp_path / "escape.md").exists()


def test_no_backups_at_all_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "empty"
    (home / "config").mkdir(parents=True)
    (home / "config" / "gos.toml").write_text(
        f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8"
    )
    out = failed(home, capsys)
    assert "FAIL db backup: no DB backup" in out
    assert "FAIL vault backup: no vault backup" in out


# --- days, listing, retention, a killed backup ------------------------------------------------


def test_date_picks_that_days_backups_and_list_shows_them(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_job(home, TODAY + dt.timedelta(days=1))
    flip(folder(home) / "gos-20261112.sqlite", 300)  # the newest is bad...
    failed(home, capsys)
    code, out = gos(  # ...an older day still restores
        home, "backup", "restore", "--verify", "--date", "20261111", capsys=capsys
    )
    assert code == 0, out
    code, out = gos(home, "backup", "list", capsys=capsys)
    assert code == 0
    assert out.splitlines()[0].split() == [
        "db",
        "20261112",
        "gos-20261112.sqlite",
        "manifest",
    ]


def test_retention_prunes_backups_with_their_manifests(home: Path) -> None:
    for d in range(1, 10):
        run_job(home, TODAY + dt.timedelta(days=d))
    names = sorted(p.name for p in folder(home).iterdir())
    assert len([n for n in names if n.endswith(".sqlite")]) == curator.KEEP_BACKUPS
    assert len([n for n in names if n.endswith(".zip")]) == curator.KEEP_BACKUPS
    assert len([n for n in names if n.endswith(".manifest.json")]) == 2 * 7


def test_a_backup_killed_after_its_manifest_is_redone(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Power lost between the manifest and the rename: an orphan manifest and a .tmp. The next
    run removes both and writes the day's backup afresh."""
    day = TODAY + dt.timedelta(days=1)
    target = folder(home) / "gos-20261112.sqlite"
    (folder(home) / "gos-20261112.sqlite.tmp").write_bytes(b"half")
    backups.write_manifest(target, {"format": 1, "sha256": "x", "tables": {}})
    zip_tmp = folder(home) / "vault-20261112.zip.tmp"
    zip_tmp.write_bytes(b"half")
    [result] = run_job(home, day)
    assert result.status == "ok", result
    assert not zip_tmp.exists()
    code, out = gos(home, "backup", "restore", "--verify", capsys=capsys)
    assert code == 0, out


def test_a_pre_wo_backup_gets_its_manifest(home: Path) -> None:
    db = folder(home) / "gos-20261111.sqlite"
    backups.manifest_path(db).unlink()
    [result] = run_job(home)
    assert result.detail.startswith("gos-20261111.sqlite already exists")
    assert backups.read_manifest(db)["tables"]["kv"] == 25


def test_a_vault_snapshot_past_its_deadline_leaves_nothing(home: Path) -> None:
    out = folder(home) / "later"
    with pytest.raises(backups.CutShort):
        backups.snapshot_vault(home / "vault", out, TODAY, deadline=0.0)
    assert list(out.iterdir()) == []
