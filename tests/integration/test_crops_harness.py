"""Crop data (WO-1.10, D134-D137) from real export runs of GregtecLink's client harness.

The runs are the dev runtime's (GT 5.09.54.265 and its dependencies plus CropsNH 2.0.140, not the
pack; mode "harness"), so the checks are the WO's acceptance in shape: every crop CropsNH's crop
dumper lists loads into the DB (counted), every crop NEI's Crop Breeding tab shows is in the DB
(read from the run's NEI files here, apart from the loader's own check), drops are keyed by
item, the DB gives back what was parsed, and two client exports of one pack give the same crop
data (``GOS_CLIENT_RUN_B``, as test_db_harness.py). The server harness has no crop step. As in
test_db_harness.py this runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, right after the
harnesses). Nothing is written outside tmp_path.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from pathlib import Path

import pytest

from gregtec.db import build, report
from gregtec.db import crops as crop_db
from gregtec.ingest import collect, crops

REPO = Path(__file__).resolve().parents[2]


def harness_home(side: str, home: Path, game: Path | None = None) -> Path:
    game = game or REPO / "mod" / "run" / side
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    collect.collect(game, home, 1, allow_harness=True)
    return home


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> build.Result:
    home = harness_home("client", tmp_path_factory.mktemp("client"))
    return build.build(home, allow_harness=True)


def dumped_ids(run: Path, manifest: dict[str, object]) -> list[str]:
    """The crop ids of CropsNH's crop dump, read here with the csv module alone."""
    with open(
        run / crops.DIR / crops.CROPS, encoding=crops.charset(manifest), newline=""
    ) as f:
        body = list(csv.reader(f))[1:]
    return [row[0] for row in body]


def nei_breeding_crops(run: Path, manifest: dict[str, object]) -> set[str]:
    """Every crop id (a seed's NBT ``crop``) in NEI's Crop Breeding tab, from NEI's files."""
    out: set[str] = set()
    files = manifest["files"]
    assert isinstance(files, dict)
    for rel in files:
        if not rel.startswith("nei/"):
            continue
        with open(run / rel, encoding="utf-8") as f:
            for line in f:
                if crops.NEI_BREEDING not in line:
                    continue
                o = json.loads(line)
                if o["handler"] != crops.NEI_BREEDING:
                    continue
                for s in (*o["ingredients"], *o["results"]):
                    out.add(s["tag"]["crop"])
    return out


def test_every_dumped_crop_and_every_crop_of_neis_breeding_tab_loads(
    client: build.Result,
) -> None:
    run = client.loaded.run
    manifest = dict(run.manifest)
    assert manifest["parts"]["crops"] == "ok"  # type: ignore[index]
    ids = dumped_ids(run.path, manifest)
    assert len(ids) >= 50 and len(set(ids)) == len(ids)
    conn = sqlite3.connect(client.db)
    try:
        loaded = {
            r[0]
            for r in conn.execute(
                "SELECT id FROM crop WHERE run_id = ?", (client.run_id,)
            )
        }
        tab = nei_breeding_crops(run.path, manifest)
        recipes = conn.execute(
            "SELECT count(*), sum(in_nei), sum(machine_only) FROM crop_parent_recipe"
            " WHERE run_id = ?",
            (client.run_id,),
        ).fetchone()
        orphans = conn.execute(
            "SELECT count(*) FROM crop_parent WHERE crop_id NOT IN (SELECT id FROM crop)"
        ).fetchone()[0]
        drops = conn.execute(
            "SELECT count(*), count(item_id) FROM crop_drop WHERE run_id = ?",
            (client.run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert loaded == set(ids)  # every crop the dumper lists is in the DB
    assert tab and tab <= loaded  # every crop of NEI's Crop Breeding tab is in the DB
    data = client.loaded.crops
    assert data is not None and data.nei is not None
    assert data.problems == []
    assert (
        recipes[1] == data.nei.breeding > 0
    )  # each NEI breeding recipe is a dumped one
    assert (
        recipes[0] == recipes[1] + recipes[2]
    )  # the rest are for the Crop Breeder only
    assert orphans == 0
    assert (
        drops[0] > 0 and drops[1] == drops[0]
    )  # every drop keyed by item and in the catalog
    text = "\n".join(report.lines(client, full=False))
    assert f"Crops: {len(ids)} crops" in text
    assert "every crop they show is in the crop dump" in text
    print(
        f"\n{len(ids)} crops dumped and loaded; NEI's Crop Breeding tab: {data.nei.breeding} "
        f"recipes with {len(tab)} crops; {recipes[0]} parent recipes; {drops[0]} drops"
    )


def test_the_db_gives_back_the_parsed_crop_data(client: build.Result) -> None:
    conn = sqlite3.connect(client.db)
    try:
        back = crop_db.read(conn, client.run_id)
    finally:
        conn.close()
    assert client.loaded.crops is not None
    assert back == client.loaded.crops
    assert crops.diff(back, client.loaded.crops).empty()


def test_two_client_exports_give_the_same_crop_data(
    client: build.Result, tmp_path: Path
) -> None:
    other = os.environ.get("GOS_CLIENT_RUN_B")
    if not other:
        pytest.skip(
            "set GOS_CLIENT_RUN_B to an earlier client run's game folder (CI does)"
        )
    home = harness_home("client", tmp_path / "b", REPO / other)
    b = build.build(home, allow_harness=True)
    assert client.loaded.crops is not None and b.loaded.crops is not None
    d = crops.diff(b.loaded.crops, client.loaded.crops)
    assert d.nei_compared and d.empty(), crops.diff_lines(d)


def test_the_server_harness_has_no_crop_step(tmp_path: Path) -> None:
    home = harness_home("server", tmp_path / "server")
    result = build.build(home, allow_harness=True)
    assert result.loaded.crops_status == "skipped_server"
    assert result.loaded.crops is None
    assert any(
        line.startswith("Crops: none (parts.crops is 'skipped_server'")
        for line in report.lines(result, full=False)
    )
