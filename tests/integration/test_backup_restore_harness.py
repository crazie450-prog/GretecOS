"""WO-11.2: ``gos backup restore --verify`` on backups of a real DB (D332): the server harness's
run collected and built as ``gos db build`` does, the core's own tables added, a deployed vault,
the daily backup job, then the restore check, and a corrupted copy failing loudly.

Runs only with GOS_REQUIRE_HARNESS_RUN=1 after the server harness (mod/README.md), as
test_vault_harness.py does (``GOS_SERVER_RUN``: another game folder). Nothing is written outside
tmp_path.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from gregtec import cli
from gregtec.db import access
from gregtec.ingest import collect
from gregtec.ops import backups
from gregtec.store import Store
from gregtec.vault import curator, deploy

REPO = Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 11, 11)


@pytest.fixture(scope="module")
def home(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the server harness")
    game = Path(os.environ.get("GOS_SERVER_RUN") or REPO / "mod" / "run" / "server")
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("backup-harness")
    built_home(game, home)  # collect + db build, shared by the run (D340)
    (home / "config").mkdir(exist_ok=True)
    (home / "config" / "gos.toml").write_text(
        f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8"
    )
    Store(
        access.db_path(home)
    ).close()  # the core's own tables, as the core's start adds them
    deploy.deploy(home / "vault", REPO / "vault-template")
    [result] = curator.Curator(
        home / "vault", access.db_path(home), home / "data" / "backups"
    ).run(TODAY, ["backup"])
    assert result.status == "ok", result
    return home


def gos(home: Path, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = cli.main(["backup", *args, "--config", str(home / "config" / "gos.toml")])
    return code, capsys.readouterr().out


def test_the_real_dbs_backup_restores_and_checks(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = home / "data" / "backups"
    manifest = backups.read_manifest(folder / "gos-20261111.sqlite")
    assert manifest["tables"]["recipe"] > 90_000  # the harness's recipes, counted
    assert "llm_usage" in manifest["tables"] and "kv" in manifest["tables"]
    code, out = gos(home, capsys, "restore", "--verify")
    assert code == 0, out
    assert "ok   db row counts" in out and "ok   vault file hashes" in out


def test_a_corrupted_copy_of_the_real_backup_fails(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = home / "data" / "backups"
    bad = tmp_path / "backups"
    bad.mkdir()
    for p in src.iterdir():
        (bad / p.name).write_bytes(p.read_bytes())
    db = bad / "gos-20261111.sqlite"
    data = bytearray(db.read_bytes())
    for offset in range(len(data) // 2, len(data) // 2 + 4096 * 8, 4096):
        data[offset + 100] ^= 0x5A  # a few pages in the middle
    db.write_bytes(bytes(data))
    code, out = gos(home, capsys, "restore", "--verify", "--from", str(bad))
    assert code == 1, out
    assert "FAIL db file hash" in out and "RESTORE CHECK FAILED" in out
    m = backups.manifest_path(bad / "vault-20261111.zip")
    doc = json.loads(m.read_text("utf-8"))
    assert doc["files"]  # the vault's notes and git history are in the snapshot
