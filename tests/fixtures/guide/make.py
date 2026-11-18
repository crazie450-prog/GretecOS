"""Writes ``furnace.json``: the build guide's data (gregtec.build.guide_data) for WO-1.8's
self-authored fixture furnace (tests/fixtures/gtnh-extractor, mte 9001, coil tier 1, front north)
with the made-up catalog of tests/unit/test_build_guide_data.py. The dashboard's vitest tests draw
it; tests/unit/test_build_guide_data.py checks it still equals what the core sends. No game data.

``uv run python tests/fixtures/guide/make.py`` after a change to the guide's JSON.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "furnace.json"


def furnace() -> dict[str, object]:
    from gregtec.build import footprints as fp
    from gregtec.build import guide_data
    from gregtec.db import access
    from gregtec.db import structures as structure_db

    spec = importlib.util.spec_from_file_location(
        "_wo108_guide_tests", HERE.parents[1] / "unit" / "test_build_guide_data.py"
    )
    assert spec is not None and spec.loader is not None
    tests = importlib.util.module_from_spec(spec)
    sys.modules["_wo108_guide_tests"] = tests
    spec.loader.exec_module(tests)
    with tempfile.TemporaryDirectory() as tmp:
        conn = access.connect(Path(tmp) / "gos.sqlite")
        try:
            structure_db.load(conn, HERE.parent / "gtnh-extractor")
            tests.add_catalog(conn, Path(tmp) / "run")
            lib = fp.library(conn)
            f = lib.get("gregtech:gt.blockmachines", 9001)
            variants = [
                lib.get(f.registry_name, f.meta, v.variant) for v in lib.for_mte(9001)
            ]
            return guide_data.guide(f, guide_data.looks(conn, f.materials()), variants)
        finally:
            conn.close()


if __name__ == "__main__":
    OUT.write_text(json.dumps(furnace(), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
