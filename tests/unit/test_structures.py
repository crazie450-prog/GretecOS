"""WO-1.8: the gtnh-extractor dataset reader and the structure table (D123).

The fixture in tests/fixtures/gtnh-extractor/ is self-authored in the extractor's schema-v3 shape;
its blocks are made up, not game data.
"""

from __future__ import annotations

import io
import json
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
EBF, FREEZER, TOWER = (
    "gregtech_gt_blockmachines_1000.json",
    "gregtech_gt_blockmachines_1001.json",
    "gregtech_gt_blockmachines_1126.json",
)


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
    assert [c.meta for c in data.controllers] == [1000, 1001, 1126]
    ebf, freezer, tower = data.controllers
    assert (ebf.registry_name, ebf.name, ebf.family) == (
        "gregtech:gt.blockmachines",
        "Electric Blast Furnace",
        "gregtech",
    )
    assert [v.key for v in ebf.variants] == ["t1"]
    assert ebf.variants[0].size == (3, 4, 3)
    assert len(ebf.variants[0].blocks) == 34
    assert [s.channel_value for s in ebf.substitutions["coil"]] == [1, 2, 3]
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
        "gregtech:gt.blockmachines#14003"
    ]


def test_takes_the_out_folder_or_its_multiblocks() -> None:
    assert multiblocks_dir(FIXTURE) == FIXTURE / "multiblocks"
    assert multiblocks_dir(FIXTURE / "multiblocks") == FIXTURE / "multiblocks"
    assert len(read_dataset(FIXTURE / "multiblocks", gt_version=PIN).controllers) == 3


def test_variant_key_names_channels_in_order(dataset: Path) -> None:
    def channels(doc: dict[str, Any]) -> None:
        doc["variants"][0]["channels"] = {"height": 3, "coil": 2}

    edit(dataset, EBF, channels)
    ebf = read_dataset(dataset, gt_version=PIN).controllers[0]
    assert ebf.variants[0].key == "t1;coil=2;height=3"


def test_an_untranslated_name_is_dropped(dataset: Path) -> None:
    def key(doc: dict[str, Any]) -> None:
        doc["controller"]["display_name"] = "gt.blockmachines.industrialsifter.name"

    edit(dataset, FREEZER, key)
    assert read_dataset(dataset, gt_version=PIN).controllers[1].name is None


def _set(path: tuple[Any, ...], value: object) -> Callable[[dict[str, Any]], None]:
    def change(doc: dict[str, Any]) -> None:
        target: Any = doc
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = value

    return change


BAD: list[tuple[str, str, Callable[[dict[str, Any]], None], str]] = [
    ("old schema", EBF, _set(("schema",), 2), "dataset schema 2"),
    (
        "other GT",
        "_meta.json",
        _set(("mod_versions", "GT5-Unofficial"), "5.09.54.133"),
        "pin",
    ),
    ("short count", "_meta.json", _set(("controller_count",), 4), "incomplete"),
    ("bbox", EBF, _set(("variants", 0, "bbox"), [3, 3, 3]), "extent"),
    (
        "facing",
        TOWER,
        _set(("controller", "facing_convention"), "front = south"),
        "NORTH",
    ),
    ("no blocks", FREEZER, _set(("variants", 0, "blocks"), []), "non-empty list"),
    ("bool meta", EBF, _set(("controller", "meta"), True), "not an integer"),
    ("same key", TOWER, _set(("variants", 1, "trigger_stack_size"), 1), "one key"),
    ("no kinds", EBF, _set(("variants", 0, "hatch_slots", 0, "kinds"), []), "kinds"),
    ("stack 0", EBF, _set(("variants", 0, "trigger_stack_size"), 0), "below 1"),
]


@pytest.mark.parametrize(
    ("name", "target", "change", "message"), BAD, ids=[b[0] for b in BAD]
)
def test_refuses_a_bad_dump(
    dataset: Path,
    name: str,
    target: str,
    change: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    edit(dataset, target, change)
    with pytest.raises(FormatError, match=message):
        read_dataset(dataset, gt_version=PIN)


def test_refuses_a_cell_listed_twice(dataset: Path) -> None:
    def twice(doc: dict[str, Any]) -> None:
        blocks = doc["variants"][0]["blocks"]
        blocks.append(dict(blocks[0]))

    edit(dataset, EBF, twice)
    with pytest.raises(FormatError, match="listed twice"):
        read_dataset(dataset, gt_version=PIN)


def test_refuses_a_hatch_slot_off_the_blocks(dataset: Path) -> None:
    def off(doc: dict[str, Any]) -> None:
        doc["variants"][0]["hatch_slots"][0]["d"] = [0, 1, 1]  # the EBF's hollow centre

    edit(dataset, EBF, off)
    with pytest.raises(FormatError, match="not a block cell"):
        read_dataset(dataset, gt_version=PIN)


def test_refuses_one_controller_in_two_files(dataset: Path) -> None:
    folder = dataset / "multiblocks"
    shutil.copy(folder / EBF, folder / "copy.json")
    edit(dataset, "_meta.json", _set(("controller_count",), 4))
    with pytest.raises(FormatError, match="also in"):
        read_dataset(dataset, gt_version=PIN)


def test_refuses_a_folder_without_meta(tmp_path: Path) -> None:
    with pytest.raises(FormatError, match="not a gtnh-extractor dataset"):
        read_dataset(tmp_path, gt_version=PIN)


def test_refuses_broken_json(dataset: Path) -> None:
    (dataset / "multiblocks" / EBF).write_text("{", encoding="utf-8")
    with pytest.raises(FormatError, match=EBF):
        read_dataset(dataset, gt_version=PIN)


def test_no_pin_reads_any_gt(dataset: Path) -> None:
    edit(dataset, "_meta.json", _set(("mod_versions", "GT5-Unofficial"), "MC1710"))
    assert read_dataset(dataset, gt_version=None).meta.gt_version == "MC1710"


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
        (1000, "t1", 3, 4, 3, "gtnh-extractor", 1),
        (1001, "t1", 3, 3, 3, "gtnh-extractor", 1),
        (1126, "t1", 3, 3, 3, "gtnh-extractor", 1),
        (1126, "t2", 3, 4, 3, "gtnh-extractor", 1),
    ]
    assert (report.controllers, report.variants, report.cells) == (
        3,
        4,
        34 + 26 + 26 + 34,
    )
    assert report.families == {"gregtech": 2, "gtPlusPlus": 1}
    assert report.caveats == 1 and report.linked == 0
    lines = "\n".join(report.lines())
    assert "controllers 3 (with dimensions and cells), variants 4" in lines
    assert "gregtech:gt.blockmachines#14003: fixture: a client-only class" in lines


def test_json_columns_keep_the_extractor_shapes(dataset: Path) -> None:
    conn = memory()
    db.load(conn, dataset)
    source = json.loads((dataset / "multiblocks" / TOWER).read_text(encoding="utf-8"))
    blocks, hatches, subs, channels, failures, name, cls, stack = conn.execute(
        "SELECT blocks_json, hatches_json, subs_json, channels_json, failures_json, name_en,"
        " class_name, trigger_stack_size FROM structure WHERE meta = 1126 AND variant = 't2'"
    ).fetchone()
    assert json.loads(blocks) == source["variants"][1]["blocks"]
    assert json.loads(hatches) == source["variants"][1]["hatch_slots"]
    assert json.loads(subs) == {} and json.loads(channels) == {}
    assert json.loads(failures) == source["failures"]
    assert (name, stack) == ("Distillation Tower", 2)
    assert cls.endswith("MTEDistillationTower")
    coil = conn.execute("SELECT subs_json FROM structure WHERE meta = 1000").fetchone()[
        0
    ]
    assert json.loads(coil)["coil"][2] == {
        "channel_value": 3,
        "block": "gregtech:gt.blockcasings5",
        "meta": 2,
    }


def test_records_the_run(dataset: Path) -> None:
    conn = memory()
    db.load(conn, dataset)
    run = conn.execute(
        "SELECT source, pack_version, gt_version, extractor_sha, controllers, failures_json,"
        " mod_versions_json FROM structure_run"
    ).fetchone()
    assert run[:5] == (
        "gtnh-extractor",
        "2.9.0",
        PIN,
        "03b6eb45977fa57822a6e2e660b146b3d95fd9d9+gos-2.9.0",
        3,
    )
    assert json.loads(run[5])[0]["registry_name"] == "gregtech:gt.blockmachines#14003"
    assert json.loads(run[6])["StructureLib"] == "1.4.47"


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
        (1000, "t1"),
        (1001, "t1"),
        (1126, "t1"),
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
    edit(dataset, EBF, _set(("schema",), 4))
    with pytest.raises(FormatError):
        db.load(conn, dataset)
    assert len(rows(conn)) == 4


APPENDIX_D = (
    "CREATE TABLE structure(machine_id INT, variant TEXT, size_x INT, size_y INT, size_z INT,"
    " blocks_json TEXT, schematic_path TEXT, source TEXT)"
)


def test_adds_its_columns_to_an_appendix_d_table(dataset: Path) -> None:
    conn = memory()
    conn.execute(APPENDIX_D)  # what WO-1.3's schema.sql creates
    db.load(conn, dataset)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(structure)")]
    assert columns == [name for name, _ in db.STRUCTURE_COLUMNS]
    assert len(rows(conn)) == 4


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


def test_links_machine_rows_by_item(dataset: Path) -> None:
    conn = memory()
    # The two WO-1.3 tables link_machines reads, as Appendix D has them.
    conn.execute(
        "CREATE TABLE item(id INTEGER PRIMARY KEY, registry_name TEXT NOT NULL, meta INT NOT NULL,"
        " nbt_hash TEXT NOT NULL DEFAULT '', UNIQUE(registry_name, meta, nbt_hash))"
    )
    conn.execute(
        "CREATE TABLE machine(id INTEGER PRIMARY KEY, mte_id INT UNIQUE, item_id INT, name_en TEXT)"
    )
    conn.execute("INSERT INTO item VALUES (40, 'gregtech:gt.blockmachines', 1126, '')")
    conn.execute("INSERT INTO item VALUES (41, 'gregtech:gt.blockcasings', 1000, '')")
    conn.execute("INSERT INTO machine VALUES (9, 1126, 40, 'Distillation Tower')")
    conn.execute("INSERT INTO machine VALUES (10, 1000, 41, 'not the EBF')")
    report = db.load(conn, dataset)
    linked = conn.execute(
        "SELECT meta, variant, machine_id FROM structure ORDER BY meta, variant"
    ).fetchall()
    assert linked == [
        (1000, "t1", None),
        (1001, "t1", None),
        (1126, "t1", 9),
        (1126, "t2", 9),
    ]
    assert report.linked == 1


# --- gos structures load ------------------------------------------------------------------


def test_cli_loads_the_newest_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    datasets = tmp_path / "data" / "structures"
    shutil.copytree(FIXTURE, datasets / "2.9.0-20261105T000000Z")
    newest = datasets / "2.9.0-20261106T000000Z"
    shutil.copytree(FIXTURE, newest)
    (datasets / "2.9.0-20261107T000000Z").mkdir()  # an extract that never finished
    assert db.newest_dataset(tmp_path) == newest
    assert cli.main(["structures", "load", "--gos-home", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"from {newest / 'multiblocks'}" in out
    conn = sqlite3.connect(tmp_path / "data" / "gos.sqlite")
    assert conn.execute("SELECT count(*) FROM structure").fetchone()[0] == 4
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


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
    assert "Not loaded" in capsys.readouterr().err


def test_main_writes_to_the_stream(dataset: Path, tmp_path: Path) -> None:
    out = io.StringIO()
    assert db.main(tmp_path, dataset, out) == 0
    assert "by package: gregtech 2, gtPlusPlus 1" in out.getvalue()
