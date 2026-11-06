"""WO-1.8: the gtnh-extractor dataset reader and the structure table (D123).

The fixture in tests/fixtures/gtnh-extractor/ is self-authored in the extractor's schema-v3 shape:
three made-up controllers whose blocks are ``fixture:*``, not game data and not copied from any
dump or from upstream's own fixtures.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gregtec import cli
from gregtec.db import structures as db
from gregtec.ingest.structures import FormatError, multiblocks_dir, read_dataset

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "gtnh-extractor"
PIN = "5.09.54.265"
FURNACE, FREEZER, TOWER = (
    "gregtech_gt_blockmachines_9001.json",
    "gregtech_gt_blockmachines_9002.json",
    "gregtech_gt_blockmachines_9003.json",
)
CELLS = 25 + 26 + 27 + 36  # furnace, freezer, tower t1 and t2


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    out = tmp_path / "2.9.0-20261106T000000Z"
    shutil.copytree(FIXTURE, out)
    return out


def edit(dataset: Path, name: str, change: Callable[[dict[str, Any]], None]) -> None:
    path = dataset / "multiblocks" / name
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


def memory() -> sqlite3.Connection:
    return sqlite3.connect(":memory:", isolation_level=None)


# --- reader -------------------------------------------------------------------------------


def test_reads_the_fixture() -> None:
    data = read_dataset(FIXTURE, gt_version=PIN)
    assert [c.meta for c in data.controllers] == [9001, 9002, 9003]
    furnace, freezer, tower = data.controllers
    assert (furnace.registry_name, furnace.name, furnace.family) == (
        "gregtech:gt.blockmachines",
        "Fixture Furnace",
        "gregtech",
    )
    assert [v.key for v in furnace.variants] == ["t1"]
    assert furnace.variants[0].size == (3, 3, 3)
    assert len(furnace.variants[0].blocks) == 25
    assert [s.channel_value for s in furnace.substitutions["coil"]] == [1, 2, 3]
    assert freezer.family == "gtPlusPlus"
    assert [(v.key, v.size) for v in tower.variants] == [
        ("t1", (3, 3, 3)),
        ("t2", (3, 4, 3)),
    ]
    layers = {s.output_layer for s in tower.variants[1].hatch_slots}
    assert layers == {None, 0, 1, 2}
    assert tower.failures == (
        "fixture: variant family truncated at the trigger-stack ceiling",
    )
    assert data.meta.gt_version == PIN
    assert [f.registry_name for f in data.meta.failures] == [
        "gregtech:gt.blockmachines#9099"
    ]
    assert data.rejected == ()


def test_a_hatch_only_cell_is_air_and_still_reads() -> None:
    # GT's newAny elements (the EBF's muffler, ...) have no casing to fall back on, and the
    # extractor builds with no hatches, so their cell is air with a hatch slot on it.
    furnace = read_dataset(FIXTURE, gt_version=PIN).controllers[0]
    variant = furnace.variants[0]
    muffler = [s for s in variant.hatch_slots if s.kinds == ("Muffler",)]
    assert [s.d for s in muffler] == [(0, 2, 1)]
    assert (0, 2, 1) not in {b.d for b in variant.blocks}


def test_size_spans_a_hatch_slot_outside_the_bbox() -> None:
    raw = json.loads((FIXTURE / "multiblocks" / FREEZER).read_text(encoding="utf-8"))
    assert raw["variants"][0]["bbox"] == [3, 3, 3]  # the extractor's: blocks only
    freezer = read_dataset(FIXTURE, gt_version=PIN).controllers[1]
    assert freezer.variants[0].size == (3, 4, 3)  # plus the slot on top, at dy = 3


def test_takes_the_out_folder_or_its_multiblocks() -> None:
    assert multiblocks_dir(FIXTURE) == FIXTURE / "multiblocks"
    assert multiblocks_dir(FIXTURE / "multiblocks") == FIXTURE / "multiblocks"
    assert len(read_dataset(FIXTURE / "multiblocks", gt_version=PIN).controllers) == 3


def test_variant_key_names_channels_in_order(dataset: Path) -> None:
    def channels(doc: dict[str, Any]) -> None:
        doc["variants"][0]["channels"] = {"height": 3, "coil": 2}

    edit(dataset, FURNACE, channels)
    furnace = read_dataset(dataset, gt_version=PIN).controllers[0]
    assert furnace.variants[0].key == "t1;coil=2;height=3"


def test_an_untranslated_name_is_dropped(dataset: Path) -> None:
    def key(doc: dict[str, Any]) -> None:
        doc["controller"]["display_name"] = "gt.blockmachines.fixturefreezer.name"

    edit(dataset, FREEZER, key)
    assert read_dataset(dataset, gt_version=PIN).controllers[1].name is None


def _set(path: tuple[Any, ...], value: object) -> Callable[[dict[str, Any]], None]:
    def change(doc: dict[str, Any]) -> None:
        target: Any = doc
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = value

    return change


def _twice(doc: dict[str, Any]) -> None:
    blocks = doc["variants"][0]["blocks"]
    blocks.append(dict(blocks[0]))


# A bad _meta.json, another GT or a short dump fails the whole read.
FATAL: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("meta schema", _set(("schema",), 2), "dataset schema 2"),
    ("other GT", _set(("mod_versions", "GT5-Unofficial"), "5.09.54.133"), "pin"),
    ("short count", _set(("controller_count",), 4), "incomplete"),
    ("bad count", _set(("controller_count",), "3"), "not an integer"),
]


@pytest.mark.parametrize(
    ("name", "change", "message"), FATAL, ids=[f[0] for f in FATAL]
)
def test_refuses_a_bad_dump(
    dataset: Path, name: str, change: Callable[[dict[str, Any]], None], message: str
) -> None:
    edit(dataset, "_meta.json", change)
    with pytest.raises(FormatError, match=message):
        read_dataset(dataset, gt_version=PIN)


# One bad controller file is set aside and the rest are read.
SET_ASIDE: list[tuple[str, str, Callable[[dict[str, Any]], None], str]] = [
    ("old schema", FURNACE, _set(("schema",), 2), "dataset schema 2"),
    ("bbox", FURNACE, _set(("variants", 0, "bbox"), [3, 4, 3]), "extent"),
    (
        "facing",
        TOWER,
        _set(("controller", "facing_convention"), "front = south"),
        "NORTH",
    ),
    ("no blocks", FREEZER, _set(("variants", 0, "blocks"), []), "non-empty list"),
    ("bool meta", FURNACE, _set(("controller", "meta"), True), "not an integer"),
    ("same key", TOWER, _set(("variants", 1, "trigger_stack_size"), 1), "one key"),
    (
        "no kinds",
        FURNACE,
        _set(("variants", 0, "hatch_slots", 0, "kinds"), []),
        "kinds",
    ),
    ("stack 0", FURNACE, _set(("variants", 0, "trigger_stack_size"), 0), "below 1"),
    ("cell twice", FREEZER, _twice, "listed twice"),
]


@pytest.mark.parametrize(
    ("name", "target", "change", "message"), SET_ASIDE, ids=[s[0] for s in SET_ASIDE]
)
def test_sets_aside_a_bad_controller(
    dataset: Path,
    name: str,
    target: str,
    change: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    edit(dataset, target, change)
    data = read_dataset(dataset, gt_version=PIN)
    assert [r.file for r in data.rejected] == [target]
    assert re.search(message, data.rejected[0].reason)
    assert len(data.controllers) == 2


def test_sets_aside_broken_json(dataset: Path) -> None:
    (dataset / "multiblocks" / FURNACE).write_text("{", encoding="utf-8")
    data = read_dataset(dataset, gt_version=PIN)
    assert [r.file for r in data.rejected] == [FURNACE]
    assert [c.meta for c in data.controllers] == [9002, 9003]


def test_sets_aside_both_files_of_one_controller(dataset: Path) -> None:
    folder = dataset / "multiblocks"
    shutil.copy(folder / FURNACE, folder / "copy.json")
    edit(dataset, "_meta.json", _set(("controller_count",), 4))
    data = read_dataset(dataset, gt_version=PIN)
    assert [r.file for r in data.rejected] == ["copy.json", FURNACE]
    assert "in 2 files" in data.rejected[0].reason
    assert [c.meta for c in data.controllers] == [9002, 9003]


def test_refuses_a_dump_in_which_nothing_reads(dataset: Path) -> None:
    for name in (FURNACE, FREEZER, TOWER):
        edit(dataset, name, _set(("schema",), 4))
    with pytest.raises(FormatError, match="no controller file reads"):
        read_dataset(dataset, gt_version=PIN)


def test_refuses_a_folder_without_meta(tmp_path: Path) -> None:
    with pytest.raises(FormatError, match="not a gtnh-extractor dataset"):
        read_dataset(tmp_path, gt_version=PIN)


def test_no_pin_reads_any_gt(dataset: Path) -> None:
    edit(dataset, "_meta.json", _set(("mod_versions", "GT5-Unofficial"), "MC1710"))
    assert read_dataset(dataset, gt_version=None).meta.gt_version == "MC1710"


def test_refuses_an_empty_dump(dataset: Path) -> None:
    for name in (FURNACE, FREEZER, TOWER):
        (dataset / "multiblocks" / name).unlink()
    edit(dataset, "_meta.json", _set(("controller_count",), 0))
    with pytest.raises(FormatError, match="no controller file reads$"):
        read_dataset(dataset, gt_version=PIN)


# --- structure table ----------------------------------------------------------------------


def rows(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT meta, variant, size_x, size_y, size_z, source, run_id FROM structure"
        " ORDER BY meta, variant"
    ).fetchall()


def test_load_writes_one_row_per_variant(dataset: Path) -> None:
    conn = memory()
    report = db.load(conn, dataset)
    assert rows(conn) == [
        (9001, "t1", 3, 3, 3, "gtnh-extractor", 1),
        (9002, "t1", 3, 4, 3, "gtnh-extractor", 1),
        (9003, "t1", 3, 3, 3, "gtnh-extractor", 1),
        (9003, "t2", 3, 4, 3, "gtnh-extractor", 1),
    ]
    assert (report.controllers, report.variants, report.cells) == (3, 4, CELLS)
    assert report.families == {"gregtech": 2, "gtPlusPlus": 1}
    assert report.caveats == 1 and report.linked == 0 and report.rejected == ()
    lines = "\n".join(report.lines())
    assert "controllers 3 (with dimensions and cells), variants 4" in lines
    assert "gregtech:gt.blockmachines#9099: fixture: a client-only class" in lines
    assert "set aside by the reader, not loaded: 0" in lines


def test_json_columns_keep_the_extractor_shapes(dataset: Path) -> None:
    conn = memory()
    db.load(conn, dataset)
    source = json.loads((dataset / "multiblocks" / TOWER).read_text(encoding="utf-8"))
    blocks, hatches, subs, channels, failures, name, cls, stack = conn.execute(
        "SELECT blocks_json, hatches_json, subs_json, channels_json, failures_json, name_en,"
        " class_name, trigger_stack_size FROM structure WHERE meta = 9003 AND variant = 't2'"
    ).fetchone()
    assert json.loads(blocks) == source["variants"][1]["blocks"]
    assert json.loads(hatches) == source["variants"][1]["hatch_slots"]
    assert json.loads(subs) == {} and json.loads(channels) == {}
    assert json.loads(failures) == source["failures"]
    assert (name, stack) == ("Fixture Tower", 2)
    assert cls.endswith("MTEFixtureTower")
    blocks, hatches, coil = conn.execute(
        "SELECT blocks_json, hatches_json, subs_json FROM structure WHERE meta = 9001"
    ).fetchone()
    assert json.loads(coil)["coil"][2] == {
        "channel_value": 3,
        "block": "fixture:coil",
        "meta": 2,
    }
    # The muffler cell is a hatch slot with no block.
    assert {"d": [0, 2, 1], "kinds": ["Muffler"]} in json.loads(hatches)
    assert [0, 2, 1] not in [b["d"] for b in json.loads(blocks)]


def test_load_sets_a_bad_controller_aside(dataset: Path) -> None:
    conn = memory()
    edit(dataset, TOWER, _set(("schema",), 4))
    report = db.load(conn, dataset)
    assert [r[:2] for r in rows(conn)] == [(9001, "t1"), (9002, "t1")]
    assert report.controllers == 2
    assert [name for name, _ in report.rejected] == [TOWER]
    lines = "\n".join(report.lines())
    assert "set aside by the reader, not loaded: 1" in lines
    assert f"{TOWER}: dataset schema 4" in lines
    run = conn.execute(
        "SELECT controllers, rejected_json FROM structure_run"
    ).fetchone()
    assert run[0] == 2
    assert [r["file"] for r in json.loads(run[1])] == [TOWER]


def test_records_the_run(dataset: Path) -> None:
    conn = memory()
    db.load(conn, dataset)
    run = conn.execute(
        "SELECT source, pack_version, gt_version, extractor_sha, controllers, failures_json,"
        " mod_versions_json, rejected_json FROM structure_run"
    ).fetchone()
    assert run[:5] == (
        "gtnh-extractor",
        "2.9.0",
        PIN,
        "03b6eb45977fa57822a6e2e660b146b3d95fd9d9+gos-2.9.0",
        3,
    )
    assert json.loads(run[5])[0]["registry_name"] == "gregtech:gt.blockmachines#9099"
    assert json.loads(run[6])["StructureLib"] == "1.4.47"
    assert json.loads(run[7]) == []


def test_reload_replaces_extractor_rows_and_keeps_lab_rows(dataset: Path) -> None:
    conn = memory()
    db.load(conn, dataset)
    conn.execute(
        "INSERT INTO structure(registry_name, meta, variant, size_x, size_y, size_z,"
        " blocks_json, source) VALUES ('gtnl:addon', 7, 't1', 1, 1, 1, '[]', 'lab')"
    )
    edit(dataset, TOWER, lambda doc: doc["variants"].pop())
    report = db.load(conn, dataset)
    assert report.run_id == 2
    assert [r[:2] for r in rows(conn)] == [
        (7, "t1"),
        (9001, "t1"),
        (9002, "t1"),
        (9003, "t1"),
    ]
    assert {r[6] for r in rows(conn) if r[5] == "gtnh-extractor"} == {2}


def test_a_failed_load_changes_nothing(
    dataset: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = memory()
    db.load(conn, dataset)

    def boom(conn: sqlite3.Connection) -> int:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "link_machines", boom)
    with pytest.raises(sqlite3.OperationalError):
        db.load(conn, dataset)
    assert len(rows(conn)) == 4
    assert conn.execute("SELECT count(*) FROM structure_run").fetchone()[0] == 1


def test_a_bad_dump_changes_nothing(dataset: Path) -> None:
    conn = memory()
    db.load(conn, dataset)
    edit(dataset, "_meta.json", _set(("schema",), 4))
    with pytest.raises(FormatError):
        db.load(conn, dataset)
    assert len(rows(conn)) == 4


APPENDIX_D = (
    "CREATE TABLE structure(machine_id INT, variant TEXT, size_x INT, size_y INT, size_z INT,"
    " blocks_json TEXT, schematic_path TEXT, source TEXT)"
)


def test_adds_its_columns_to_an_appendix_d_table(dataset: Path) -> None:
    conn = memory()
    conn.execute(APPENDIX_D)  # what WO-1.3's schema v1 creates
    db.load(conn, dataset)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(structure)")]
    assert columns == [name for name, _ in db.STRUCTURE_COLUMNS]
    assert len(rows(conn)) == 4


def test_adds_new_columns_to_an_older_structure_run(dataset: Path) -> None:
    conn = memory()
    conn.execute(
        "CREATE TABLE structure_run(id INTEGER PRIMARY KEY, loaded_at TEXT NOT NULL,"
        " source TEXT NOT NULL, dataset TEXT, pack_version TEXT, gt_version TEXT,"
        " mod_versions_json TEXT, generated_at TEXT, extractor_sha TEXT, controllers INT,"
        " failures_json TEXT)"
    )
    db.load(conn, dataset)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(structure_run)")]
    assert columns == [name for name, _ in db.RUN_COLUMNS]


def test_schema_text_matches_ensure_schema() -> None:
    absorbed, ensured = memory(), memory()
    absorbed.executescript(db.SCHEMA)
    db.ensure_schema(ensured)

    def shape(conn: sqlite3.Connection) -> list[Any]:
        return [
            (t, [tuple(r) for r in conn.execute(f"PRAGMA table_info({t})")])
            for t in ("structure", "structure_run")
        ] + sorted(
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        )

    assert shape(absorbed) == shape(ensured)


def machine_tables(conn: sqlite3.Connection) -> None:
    """The two WO-1.3 tables link_machines reads, as Appendix D has them."""
    conn.execute(
        "CREATE TABLE item(id INTEGER PRIMARY KEY, registry_name TEXT NOT NULL, meta INT NOT NULL,"
        " nbt_hash TEXT NOT NULL DEFAULT '', UNIQUE(registry_name, meta, nbt_hash))"
    )
    conn.execute(
        "CREATE TABLE machine(id INTEGER PRIMARY KEY, mte_id INT UNIQUE, item_id INT, name_en TEXT)"
    )
    conn.execute("INSERT INTO item VALUES (40, 'gregtech:gt.blockmachines', 9003, '')")
    conn.execute("INSERT INTO item VALUES (41, 'fixture:not_a_machine', 9001, '')")
    conn.execute("INSERT INTO machine VALUES (9, 9003, 40, 'Fixture Tower')")
    conn.execute("INSERT INTO machine VALUES (10, 9001, 41, 'not the furnace')")


def linked(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT meta, variant, machine_id FROM structure ORDER BY meta, variant"
    ).fetchall()


def test_links_machine_rows_by_item(dataset: Path) -> None:
    conn = memory()
    machine_tables(conn)
    report = db.load(conn, dataset)
    assert linked(conn) == [
        (9001, "t1", None),
        (9002, "t1", None),
        (9003, "t1", 9),
        (9003, "t2", 9),
    ]
    assert report.linked == 1


def test_links_when_the_machine_rows_come_later(dataset: Path) -> None:
    # Structures loaded before `gos db build`: WO-1.3's build then calls link_machines (D123).
    conn = memory()
    db.load(conn, dataset)
    machine_tables(conn)
    assert db.link_machines(conn) == 1
    assert [r[2] for r in linked(conn)] == [None, None, 9, 9]


def test_link_leaves_other_sources_alone(dataset: Path) -> None:
    conn = memory()
    db.load(conn, dataset)
    conn.execute(
        "INSERT INTO structure(registry_name, meta, variant, machine_id, source)"
        " VALUES ('gtnl:addon', 7, 't1', 77, 'lab')"
    )
    machine_tables(conn)
    db.link_machines(conn)
    assert conn.execute(
        "SELECT machine_id FROM structure WHERE source = 'lab'"
    ).fetchone() == (77,)


# --- gos structures load ------------------------------------------------------------------


def test_cli_loads_the_newest_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    datasets = tmp_path / "data" / "structures"
    older = datasets / "2.9.0-20261105T000000Z"
    shutil.copytree(FIXTURE, older)
    newest = datasets / "2.9.0-20261106T000000Z"
    shutil.copytree(FIXTURE, newest)
    (datasets / "2.9.0-20261107T000000Z").mkdir()  # an extract that never finished
    assert db.datasets(tmp_path) == [newest, older]
    assert cli.main(["structures", "load", "--gos-home", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"from {newest / 'multiblocks'}" in out
    conn = sqlite3.connect(tmp_path / "data" / "gos.sqlite")
    assert conn.execute("SELECT count(*) FROM structure").fetchone()[0] == 4
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_cli_passes_over_a_dump_that_does_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    datasets = tmp_path / "data" / "structures"
    good = datasets / "2.9.0-20261105T000000Z"
    shutil.copytree(FIXTURE, good)
    failed = datasets / "2.9.0-20261106T000000Z"
    shutil.copytree(FIXTURE, failed)
    edit(failed, "_meta.json", _set(("controller_count",), 5))  # a short dump
    assert cli.main(["structures", "load", "--gos-home", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert f"Passed over {failed}" in captured.err and "incomplete" in captured.err
    assert f"from {good / 'multiblocks'}" in captured.out


def test_cli_when_no_dataset_reads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    datasets = tmp_path / "data" / "structures"
    for name in ("2.9.0-20261105T000000Z", "2.9.0-20261106T000000Z"):
        shutil.copytree(FIXTURE, datasets / name)
        edit(datasets / name, "_meta.json", _set(("schema",), 2))
    assert cli.main(["structures", "load", "--gos-home", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "Passed over" in err and "Not loaded" in err


def test_cli_without_a_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["structures", "load", "--gos-home", str(tmp_path)]) == 2
    assert "gos structures extract" in capsys.readouterr().err


def test_cli_refuses_a_bad_dataset(
    dataset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    edit(dataset, "_meta.json", _set(("mod_versions", "GT5-Unofficial"), "5.09.54.133"))
    home = tmp_path / "home"
    assert cli.main(["structures", "load", str(dataset), "--gos-home", str(home)]) == 1
    err = capsys.readouterr().err
    assert "Not loaded" in err and "Passed over" not in err


def test_main_writes_to_the_stream(dataset: Path, tmp_path: Path) -> None:
    out = io.StringIO()
    assert db.main(tmp_path, dataset, out) == 0
    assert "by package: gregtech 2, gtPlusPlus 1" in out.getvalue()
