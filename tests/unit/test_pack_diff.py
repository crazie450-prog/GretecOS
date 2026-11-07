"""WO-1.9's recipe and oredict diff and the affected-plan flags, on small hand-made DBs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gregtec.db import access
from gregtec.ingest import gtdump
from gregtec.ingest.normalize import ITEM, Ref
from gregtec.packupdate import diff, plans


def make_db(path: Path) -> sqlite3.Connection:
    conn = access.connect(path)
    conn.execute(
        "INSERT INTO recipe_map(id, unloc_name, owner_mod) VALUES (1, 'gt.recipe.x', 'gregtech')"
    )
    conn.execute(
        "INSERT INTO nei_handler(id, handler_name, mod) VALUES (1, 'nei.thing', 'neimod')"
    )
    conn.execute(
        "INSERT INTO item(id, registry_name, meta, name_en) VALUES"
        " (1, 'IC2:ingot', 0, 'Ingot'), (2, 'minecraft:stick', 0, 'Stick')"
    )
    return conn


def add_run(conn: sqlite3.Connection, run_id: int, key: str, created: str) -> None:
    conn.execute(
        "INSERT INTO export_run(id, created_at, gt_version, fingerprint, run_key)"
        " VALUES (?, ?, '5.09.54.265', '', ?)",
        (run_id, created, key),
    )


def add_recipe(
    conn: sqlite3.Connection,
    rid: int,
    run_id: int,
    ref: str,
    content: str,
    *,
    owners: list[str] | None = None,
    map_id: int | None = None,
    handler: int | None = None,
    out_item: int | None = None,
    eut: int = 30,
    duration: int = 100,
) -> None:
    conn.execute(
        "INSERT INTO recipe(id, run_id, source, recipe_map_id, nei_handler_id, eut,"
        " duration_ticks, signature, content_hash, owners, ref)"
        " VALUES (?, ?, 'gt', ?, ?, ?, ?, 'sig', ?, ?, ?)",
        (
            rid,
            run_id,
            map_id,
            handler,
            eut,
            duration,
            content,
            None if owners is None else json.dumps(owners),
            ref,
        ),
    )
    if out_item is not None:
        conn.execute(
            "INSERT INTO recipe_output(recipe_id, slot, item_id, amount) VALUES (?, 0, ?, 1)",
            (rid, out_item),
        )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "gos.sqlite"
    conn = make_db(path)
    add_run(conn, 1, "old", "2026-11-01T00:00:00Z")
    add_run(conn, 2, "new", "2026-11-02T00:00:00Z")
    # old run
    add_recipe(conn, 1, 1, "r:a/1", "h1", owners=["tst"], map_id=1, out_item=1)
    add_recipe(conn, 2, 1, "r:a/2", "h2", map_id=1, out_item=1)
    add_recipe(conn, 3, 1, "r:a/3", "h3", out_item=2)
    add_recipe(conn, 4, 1, "r:a/4", "h4", handler=1)
    # new run: 1 rebalanced (EU/t), 2 changed elsewhere, 3 removed, 4 same, 5 added
    add_recipe(
        conn, 11, 2, "r:a/1", "h1b", owners=["tst"], map_id=1, out_item=1, eut=32
    )
    add_recipe(conn, 12, 2, "r:a/2", "h2b", map_id=1, out_item=1)
    add_recipe(conn, 14, 2, "r:a/4", "h4", handler=1)
    add_recipe(conn, 15, 2, "r:a/5", "h5", handler=1)
    conn.close()
    return path


def test_recipe_diff_by_key_and_content_hash(db: Path) -> None:
    conn = access.connect(db, readonly=True)
    try:
        d = diff.recipe_diff(conn, 1, conn, 2)
    finally:
        conn.close()
    assert (d.old_count, d.new_count) == (4, 4)
    assert [r.ref for r in d.added] == ["r:a/5"]
    assert [r.ref for r in d.removed] == ["r:a/3"]
    assert [c.new.ref for c in d.changed] == ["r:a/1", "r:a/2"]
    # The mod: GT's owner, else the map's owner, else the output's mod, else NEI's handler.
    mods = {r.ref: r.mod for r in [*d.added, *d.removed, *(c.new for c in d.changed)]}
    assert mods == {
        "r:a/1": "tst",
        "r:a/2": "gregtech",
        "r:a/3": "minecraft",
        "r:a/5": "neimod",
    }
    assert d.changed[0].what() == "EU/t 30 -> 32"
    assert "chances" in d.changed[1].what()
    assert d.by_mod()[0][0] in {"gregtech", "minecraft", "neimod", "tst"}
    assert d.removed[0].output == "Stick (minecraft:stick)"


def test_summary_report_without_run_folders(db: Path, tmp_path: Path) -> None:
    old = diff.side(db, "old", tmp_path)
    new = diff.side(db, None, tmp_path)
    assert new.run_key == "new" and new.folder is None
    s = diff.summarise(old, new)
    text = diff.report(s, ["Plans: none to check"])
    assert "1 added, 1 removed, 2 changed" in text
    assert "Crops: not compared" in text and "Ore dictionary: not compared" in text
    assert "Manifest: a collected run folder is gone" in text
    assert s.gt_problem is None
    note = diff.note_lines(s, [])
    assert note[0] == "old_run: old" and "```tsv" in note and len(note) < 150
    with pytest.raises(diff.DiffError, match="no run zzz"):
        diff.side(db, "zzz", tmp_path)


def test_oredict_diff() -> None:
    def ore(name: str, *metas: int) -> gtdump.OreName:
        return gtdump.OreName(
            name, tuple(Ref(ITEM, "gt:ingot", m) for m in metas), None
        )

    d = diff.oredict_diff(
        [ore("ingotA", 1, 2), ore("gone", 3)], [ore("ingotA", 2, 4), ore("new", 5)]
    )
    assert d.names_added == ["new"] and d.names_removed == ["gone"]
    assert ("ingotA", "gt:ingot@4") in d.members_added
    assert ("ingotA", "gt:ingot@1") in d.members_removed
    assert d.by_mod() == [("gt", 2, 2)]
    assert diff.oredict_diff([ore("a", 1)], [ore("a", 1)]).empty()


def write_plan(folder: Path, pid: str, run: str, cites: list[str]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{pid}.json"
    path.write_text(
        json.dumps(
            {
                "format": plans.FORMAT,
                "id": pid,
                "status": "draft",
                "run": run,
                "cites": cites,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_plans_flagged_for_changed_removed_and_unknown_keys(
    db: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "plans"
    write_plan(folder, "pl-a", "old", ["r:a/1", "q:x"])  # rebalanced
    write_plan(folder, "pl-b", "old", ["r:a/3"])  # removed
    write_plan(folder, "pl-c", "old", ["r:a/4", "w:Page@1"])  # unchanged
    write_plan(folder, "pl-d", "old", ["r:a/9"])  # its own run never had it
    write_plan(folder, "pl-e", "gone", ["r:a/4"])  # its run was pruned
    (folder / "bad.json").write_text("{}", encoding="utf-8")
    found, problems = plans.read_folder(folder)
    assert len(found) == 5 and "bad.json" in problems[0]
    conn = access.connect(db, readonly=True)
    try:
        flagged = plans.check(
            found, lambda k: plans.view(conn, k), plans.RunView(conn, 2, "new")
        )
    finally:
        conn.close()
    why = {f.plan.id: [h.why for h in f.hits] for f in flagged}
    assert why == {
        "pl-a": ["changed in new"],
        "pl-b": ["removed in new"],
        "pl-d": ["not in its own run old"],
        "pl-e": ["its run gone isn't in the DB, so it can't be checked"],
    }
    text = "\n".join(plans.lines(flagged, len(found), problems))
    assert "4 affected by the update" in text and "plan:pl-a" in text


def test_plans_from_the_plan_table_and_mark_affected(db: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    write_plan(home / "data" / "plans", "pl-a", "old", ["r:a/1"])
    write_plan(home / "data" / "plans", "pl-z", "new", ["r:a/1"])  # row says old
    conn = access.connect(db)
    conn.execute(
        "INSERT INTO plan(id, json_path, run_id) VALUES"
        " ('pl-a', 'data/plans/pl-a.json', 1), ('pl-z', 'data/plans/pl-z.json', 1),"
        " ('pl-x', NULL, 1), ('pl-y', 'data/plans/missing.json', 1)"
    )
    found, problems = plans.from_db(conn, home)
    assert [p.id for p in found] == ["pl-a", "pl-z"]
    assert found[1].run == "old"  # the row's run wins, with a note
    assert len(problems) == 3
    assert plans.mark_affected(conn, ["pl-a", "pl-nope"], 2) == 1
    assert (
        conn.execute("SELECT affected_run FROM plan WHERE id = 'pl-a'").fetchone()[0]
        == 2
    )
    conn.close()


@pytest.mark.parametrize(
    "doc, match",
    [
        ({"format": "x"}, "isn't a gos-plan/0"),
        ({"format": plans.FORMAT, "id": "Bad Id", "run": "r", "cites": []}, "id must"),
        ({"format": plans.FORMAT, "id": "pl-1", "cites": []}, "run must"),
        (
            {"format": plans.FORMAT, "id": "pl-1", "run": "r", "cites": [1]},
            "cites must",
        ),
    ],
)
def test_plan_format_is_checked(doc: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        plans.parse(json.dumps(doc).encode(), "p.json")


def test_fixture_plans_from_the_db(db: Path) -> None:
    conn = access.connect(db, readonly=True)
    try:
        p = plans.fixture(conn, 2, "new", "pl-ingot=IC2:ingot@0")
        assert p.cites == ("r:a/1", "r:a/2") and p.run == "new"
        assert plans.parse(plans.dumps(p), "x") == p
        for bad in (
            "noequals",
            "pl-x=IC2:ingot",
            "pl-x=nothing:here@0",
            "pl-x=crop:none",
        ):
            with pytest.raises(ValueError):
                plans.fixture(conn, 2, "new", bad)
    finally:
        conn.close()
