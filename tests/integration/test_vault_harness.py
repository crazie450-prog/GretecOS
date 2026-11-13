"""WO-2.5's curator, note tools and format measurement over a DB built from a real GregtecLink
harness run (the dev runtime's GT 5.09.54.265, not the pack).

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after ``./gradlew runServer -Pharness``
(``GOS_SERVER_RUN`` names another server run folder), as test_db_harness.py does: the vault is
deployed from vault-template/ into tmp_path, the DB is the real build, and the whole curator run
(index, schema check, note_fts mirror, VACUUM INTO of the real file, git) has to pass, with the
note tools and vault_search answering over the result. The measurement counts with a fake
counter: no test calls the API. Nothing is written outside tmp_path.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path

import pytest

from gregtec.agents.tools import DEEP, build_registry
from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.vault import curator, deploy, fts, measure
from gregtec.vault.io import VaultIO

REPO = Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 11, 6)


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the server harness")
    game = Path(os.environ.get("GOS_SERVER_RUN") or REPO / "mod" / "run" / "server")
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("vault-harness")
    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    deploy.deploy(home / "vault", REPO / "vault-template")
    return home


def test_the_curator_runs_on_the_real_db(home: Path) -> None:
    db = access.db_path(home)
    root = home / "vault"
    reg = build_registry(
        db,
        vault=VaultIO(
            root, today=lambda: TODAY, on_write=lambda r: fts.mirror_one(db, root, r)
        ),
    )
    out = reg.call(
        "note_write",
        {
            "path": "base/lines/ln-steel-01.md",
            "body": "st: planned\ntgt: i:steel_ingot",
        },
        lane=DEEP,
    )
    assert not out.is_error, out.text
    results = curator.Curator(root, db, home / "data" / "backups").run(TODAY)
    assert {r.job: r.status for r in results} == {
        "compact": "ok",
        "quests": "skipped",  # the harness DB has no quests loaded (WO-6.4)
        "index": "ok",
        "schema": "ok",
        "fts": "ok",
        "backup": "ok",
        "git": "ok",
    }, [r.line() for r in results]
    backup = home / "data" / "backups" / "gos-20261106.sqlite"
    conn = sqlite3.connect(backup)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        live = sqlite3.connect(db)
        try:
            for table in ("recipe", "item", "machine", "note_fts"):
                query = f"SELECT count(*) FROM {table}"
                assert conn.execute(query).fetchone() == live.execute(query).fetchone()
        finally:
            live.close()
    finally:
        conn.close()
    found = reg.call("vault_search", {"q": "steel"})
    assert not found.is_error and "base/lines/ln-steel-01.md" in found.text
    assert "base/lines/ln-steel-01.md" in (root / "INDEX.md").read_text(
        encoding="utf-8"
    )


def test_measure_samples_the_real_db(home: Path) -> None:
    names = [s.name for s in measure.samples(home / "vault", access.db_path(home))]
    assert "db:recipes(60)" in names and "db:items(120)" in names
    items = [
        s
        for s in measure.samples(None, access.db_path(home))
        if s.name.startswith("db:")
    ]
    assert [len(s.note.tables[0]) for s in items] == [61, 121]  # header + rows
    report = measure.measure(lambda m, t: 3 + len(t) // 4, items, ["claude-haiku-5-5"])
    totals = report.totals()
    assert totals["gos", "claude-haiku-5-5"] < totals["json", "claude-haiku-5-5"]
