"""effort.structure_blocks under a connection's value limit (the tools' 1 MB guard).

Defect: it read every variant's ``blocks_json`` for a plan's multiblocks, so a plan with Forge of
the Gods (t3: 1,239,746 bytes of cells in a real dump) failed under the tools' limit with
"string or blob too big", although the smallest variant (t1, 469,878 bytes) is the one it counts.
It now reads only the variants that fit. Here the limit is a few kB and the variants are made up.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.db import access
from gregtec.planner import effort

LIMIT = 4096
CASING = "gregtech:gt.blockcasings"


def _cells(n: int, meta: int = 1) -> str:
    return json.dumps(
        [{"x": i, "y": 0, "z": 0, "block": CASING, "meta": meta} for i in range(n)]
    )


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """mte:900 with three variants: t1 (3 casings), t2 (300: over LIMIT), t3 (10); mte:901
    with only a variant over LIMIT; mte:902 with none."""
    c = access.connect(tmp_path / "gos.sqlite")
    c.execute(
        "INSERT INTO machine(id, mte_id, name_en, is_multiblock) VALUES"
        " (1, 900, 'Forge', 1), (2, 901, 'Huge', 1), (3, 902, 'Bare', 1)"
    )
    c.executemany(
        "INSERT INTO structure(machine_id, variant, blocks_json, source)"
        " VALUES (?, ?, ?, 'gtnh-extractor')",
        [
            (1, "t1", _cells(3)),
            (1, "t2", _cells(300, 2)),
            (1, "t3", _cells(10, 3)),
            (2, "t1", _cells(300)),
        ],
    )
    assert len(_cells(300)) > LIMIT > len(_cells(10))
    try:
        yield c
    finally:
        c.close()


def test_the_smallest_variant_is_counted(conn: sqlite3.Connection) -> None:
    assert effort.structure_blocks(conn, 900) == {f"{CASING}@1": 3}
    assert effort.structure_blocks(conn, 901) == {f"{CASING}@1": 300}
    assert effort.structure_blocks(conn, 902) is None


def test_a_variant_over_the_value_limit_is_not_read(conn: sqlite3.Connection) -> None:
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, LIMIT)
    with pytest.raises(sqlite3.DataError):  # what reading every variant ran into
        conn.execute("SELECT blocks_json FROM structure").fetchall()
    assert effort.structure_blocks(conn, 900) == {f"{CASING}@1": 3}
    assert effort.structure_blocks(conn, 902) is None


def test_with_no_variant_under_the_limit_the_limit_says_so(
    conn: sqlite3.Connection,
) -> None:
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, LIMIT)
    with pytest.raises(sqlite3.DataError, match="too big"):
        effort.structure_blocks(conn, 901)


def test_components_count_a_multi_under_the_limit(conn: sqlite3.Connection) -> None:
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, LIMIT)
    plan = {"nodes": [{"count": 2, "machine": {"mte": 900, "kind": "multiblock"}}]}
    found, notes, skipped = effort.components(conn, plan)
    structure = [c for c in found if c.part == "structure"]
    assert [(c.key, c.qty, c.of) for c in structure] == [(f"{CASING}@1", 6, 900)]
    assert skipped == 1 and notes == [
        "mte:900 has no item in the DB: its making is not counted"
    ]
