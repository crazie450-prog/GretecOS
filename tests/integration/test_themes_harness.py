"""The theme engine (WO-10.6, D314) on a real client harness run's block catalog.

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after the client harness (CI's mod job; locally
mod/README.md). The client run is collected and built into a DB in tmp_path, then (plan 8 row
10.6): **both starter themes validate against the harness catalog**, a theme made and refined
on that catalog keeps its versions, and a palette with a real tile-entity block (GT's machine
block, a chest) is refused with nothing written.

Nothing is written outside tmp_path.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.build import themes
from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.vault.io import VaultIO

REPO = Path(__file__).resolve().parents[2]
GAME = REPO / "mod" / "run" / "client"


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if not (GAME / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {GAME / collect.EXPORTS} (mod/README.md)")
    h = tmp_path_factory.mktemp("themes")
    collect.collect(GAME, h, 1, allow_harness=True)
    built = build.build(h, allow_harness=True)
    assert built.loaded.palette_status == "ok", built.loaded.palette_error
    shutil.copytree(REPO / "vault-template", h / "vault")
    return h


@pytest.fixture(scope="module")
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


def test_both_starter_themes_validate_against_the_harness_catalog(
    home: Path, conn: sqlite3.Connection
) -> None:
    found, problems = themes.Themes(VaultIO(home / "vault")).load()
    assert problems == []
    assert [t.id for t in found if t.source == "shipped"] == [
        "th-foundry",
        "th-marble-works",
    ]
    for t in found:
        result = themes.check(conn, t)
        assert result.ok, (t.id, result.refused)
        # every entry is a named catalog state
        assert {s.block for s in t.palette} <= set(result.names), t.id


def test_a_theme_on_the_real_catalog_keeps_versions_and_refuses_tile_entities(
    home: Path, conn: sqlite3.Connection
) -> None:
    store = themes.Themes(VaultIO(home / "vault"))
    base = [
        themes.Swatch("wall", "gregtech:gt.blockcasings@2"),
        themes.Swatch("roof", "gregtech:gt.blockcasings2@0"),
        themes.Swatch("window", "gregtech:gt.blockglass1@0"),
        themes.Swatch("light", "minecraft:glowstone@0"),
    ]
    made = store.save(
        conn, {"name": "MV Works", "prompt": "MV casings and glass", "palette": base}
    )
    assert made.theme.v == 1
    refined = store.save(
        conn,
        {
            "change": "basalt roof",
            "palette": [
                *base[:1],
                themes.Swatch("roof", "gregtech:gt.blockstones@11"),
                *base[2:],
            ],
        },
        made.theme.id,
    )
    assert refined.theme.v == 2 and store.versions(made.theme.id) == [1]
    before = sorted((home / "vault" / "themes").rglob("*"))
    for block, why in [
        ("gregtech:gt.blockmachines@0", "GT's machine block"),
        ("minecraft:chest@0", "has a tile entity"),
    ]:
        with pytest.raises(themes.ThemeError, match=why):
            store.save(
                conn,
                {
                    "change": "a machine",
                    "palette": [*base, themes.Swatch("accent", block)],
                },
                made.theme.id,
            )
    assert sorted((home / "vault" / "themes").rglob("*")) == before
    assert store.get(made.theme.id).v == 2
