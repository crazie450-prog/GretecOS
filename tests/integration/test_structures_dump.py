"""WO-1.8 acceptance on a real gtnh-extractor dump: >= 250 base controllers with dimensions and cells.

Set GOS_STRUCTURES_DATASET to the folder `gos structures extract` wrote (it holds multiblocks/);
without it the test is skipped, so run it with -rs to see a skip. In PowerShell:

    $env:GOS_STRUCTURES_DATASET = 'C:\\path\\to\\2.9.0-<UTC time>'
    uv run pytest -rs -s tests/integration/test_structures_dump.py

(cmd's `set NAME=value` sets nothing in PowerShell.) The dump is pack data and never goes in the
repo.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gregtec.db import structures as db

DATASET = os.environ.get("GOS_STRUCTURES_DATASET")


@pytest.mark.skipif(not DATASET, reason="set GOS_STRUCTURES_DATASET to a real dump")
def test_real_dump_loads_with_dimensions_and_cells(tmp_path: Path) -> None:
    assert DATASET
    conn = db.connect(tmp_path / "gos.sqlite")
    report = db.load(conn, Path(DATASET))
    controllers = conn.execute(
        "SELECT count(DISTINCT registry_name || '@' || meta) FROM structure"
        " WHERE size_x > 0 AND size_y > 0 AND size_z > 0 AND json_array_length(blocks_json) > 0"
    ).fetchone()[0]
    print("\n".join(report.lines()))
    assert controllers == report.controllers
    assert controllers >= 250
    # Every controller block is keyed by registry name and meta, never a numeric ID.
    bad = conn.execute(
        "SELECT count(*) FROM structure, json_each(structure.blocks_json) AS cell"
        " WHERE json_type(cell.value, '$.block') != 'text'"
        " OR json_type(cell.value, '$.meta') != 'integer'"
    ).fetchone()[0]
    assert bad == 0
    conn.close()
