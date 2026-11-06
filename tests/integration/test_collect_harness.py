"""``gos export collect`` (WO-1.2) on real export runs from GregtecLink's harnesses.

``./gradlew runServer -Pharness`` and ``runClient25 -Pharness`` (mod/README.md) leave a complete
run in ``mod/run/{server,client}/gregteclink/exports/``: mode "harness", from the dev runtime
(GT 5.09.54.265 and its dependencies, not the pack), so loose checks only. The client's run has
NEI's files in ``nei/``. Without a run this skips, unless GOS_REQUIRE_HARNESS_RUN=1 (CI's mod
job, which runs both harnesses first). Nothing here is committed or written outside tmp_path.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]


def harness_game(side: str) -> Path:
    game = REPO / "mod" / "run" / side
    if not (game / collect.EXPORTS).is_dir():
        why = f"no harness run in {game / collect.EXPORTS} (mod/README.md)"
        if os.environ.get("GOS_REQUIRE_HARNESS_RUN") == "1":
            pytest.fail(why)
        pytest.skip(why)
    return game


def tree(root: Path) -> dict[str, tuple[int, int]]:
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
    }


def first_line(path: Path) -> dict[str, object]:
    with open(path, encoding="utf-8") as f:
        line = json.loads(f.readline())
    assert isinstance(line, dict)
    return line


@pytest.mark.parametrize("side", ["server", "client"])
def test_a_real_harness_run_is_collected_whole(side: str, tmp_path: Path) -> None:
    game = harness_game(side)
    exports = game / collect.EXPORTS
    before = tree(exports)

    # Outside tests a harness run is never game data, so there is nothing to collect.
    with pytest.raises(collect.CollectError, match="a harness run"):
        collect.collect(game, tmp_path, 5)
    assert not any((tmp_path / collect.DATA_EXPORTS).iterdir())

    result = collect.collect(game, tmp_path, 5, allow_harness=True)
    m = result.run.manifest
    assert (result.status, m["mode"], m["side"]) == ("collected", "harness", side)
    assert m["format"] == collect.FORMAT and m["complete"] is True
    dest = result.dest
    assert dest == tmp_path / collect.DATA_EXPORTS / m["run_id"]
    files = {rel for rel in tree(dest) if (dest / rel).is_file()}
    assert files == {*m["files"], collect.MANIFEST}
    for rel, e in m["files"].items():
        with open(dest / rel, "rb") as f:
            assert hashlib.file_digest(f, "sha256").hexdigest() == e["sha256"], rel
    assert (dest / collect.MANIFEST).read_bytes() == result.run.raw

    # "JSONL captured" (plan section 8, WO-1.2): GT's recipes and, from the client, NEI's.
    assert m["parts"]["gt_dump"] == "ok"
    assert {"map", "idx", "eut", "duration"} <= first_line(
        dest / "gt_recipes.jsonl"
    ).keys()
    assert m["files"]["gt_recipes.jsonl"]["lines"] == m["counts"]["recipes"] > 10_000
    nei = sorted(r for r in m["files"] if r.startswith("nei/"))
    if side == "client":
        assert m["parts"]["nei"] == "ok", m.get("part_errors")
        assert len(nei) == m["counts"]["nei_files"] >= 150
        lines = sum(m["files"][r]["lines"] for r in nei)
        assert lines == m["counts"]["nei_recipes"] > 50_000
        assert all(first_line(dest / r)["handler"] for r in nei)
    else:
        assert (m["parts"]["nei"], nei) == ("skipped_server", [])

    # Idempotent, and Export is only ever read.
    again = collect.collect(game, tmp_path, 5, allow_harness=True)
    assert (again.status, again.dest) == ("unchanged", dest)
    assert tree(exports) == before
