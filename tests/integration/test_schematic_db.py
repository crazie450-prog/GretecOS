"""The shell writer on a real client harness DB and world (WO-10.4, D309).

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after the client harness (CI's mod job; locally
mod/README.md). The client run is collected and built into a DB in tmp_path; the client
harness's own world (``saves/gregteclink-autoexport``) gives the registry, as in
``test_palette_harness.py``. Then:

- the schematic harness's own input shells (``tests/harness/schematic_inputs.py``) pass the
  palette rule on the real catalog, and ``write_shell`` writes exactly the bytes committed in
  ``mod/harness/schematic/in/``, which read back cell for cell;
- a shell with GT's machine block, a chest or a meta above 15 is refused whole.

Nothing is written outside tmp_path; the harness world is only read.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.build import schematic, worldreg
from gregtec.build.palette import Entry
from gregtec.db import access, build
from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]
GAME = REPO / "mod" / "run" / "client"
WORLD = GAME / "saves" / "gregteclink-autoexport"
IN = REPO / "mod" / "harness" / "schematic" / "in"


def _inputs() -> ModuleType:
    script = REPO / "tests" / "harness" / "schematic_inputs.py"
    spec = importlib.util.spec_from_file_location("schematic_inputs", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if not (GAME / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {GAME / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("schematic")
    collect.collect(GAME, home, 1, allow_harness=True)
    built = build.build(home, allow_harness=True)
    c = access.connect(built.db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def test_the_harness_shells_pass_the_palette_rule_and_round_trip(
    conn: sqlite3.Connection,
) -> None:
    registry = worldreg.read(WORLD)
    for name, shell in _inputs().inputs().items():
        written = schematic.write_shell(conn, registry, shell)
        assert written.how == "single" and written.report.ok
        (part,) = written.parts
        assert part.data == (IN / f"{name}.schematic").read_bytes(), name
        back = schematic.read(part.data)
        assert schematic.diff(shell, back.schematic) == []
        assert back.schematic.offset == shell.offset


@pytest.mark.parametrize(
    ("state", "why"),
    [
        (Entry("gregtech:gt.blockmachines", 0), "machine block"),
        (Entry("minecraft:chest", 0), "tile entity"),
        (Entry("gregtech:gt.blockcasings", 16), "#30"),
    ],
)
def test_a_shell_with_a_refused_state_is_refused_whole(
    conn: sqlite3.Connection, state: Entry, why: str
) -> None:
    registry = worldreg.read(WORLD)
    shell = schematic.sample([Entry("gregtech:gt.blockcasings", 2), state])
    with pytest.raises(schematic.ShellRefused) as caught:
        schematic.write_shell(conn, registry, shell)
    assert any(why in line for line in caught.value.lines), caught.value.lines
