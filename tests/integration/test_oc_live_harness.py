"""WO-7.4 on a real export: the live base names OC's machines from the DB and the displays fit
(D279, D214). As the other harness tests, only with GOS_REQUIRE_HARNESS_RUN=1 after the server
harness (mod/README.md); nothing is written outside tmp_path.

``gos export collect`` + ``gos db build`` make a GOS_HOME from the harness run; the core's own
:func:`gregtec.bridge.ops.build` over it resolves the golden telemetry's GT meta names through the
export's ``meta_name`` (the LSC and the EBF to one machine each, with the DB's English names),
fills ``tel_machine.mte_id``, and builds a board whose lines fit their budget.
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from gregtec.bridge import display as disp
from gregtec.bridge import ops as bridge_ops
from gregtec.config import Config
from gregtec.ingest import collect
from gregtec.store import Store

REPO = Path(__file__).resolve().parents[2]
SHOWN = json.loads(
    (REPO / "tests" / "fixtures" / "oc" / "telemetry-display.json").read_text("utf-8")
)


@pytest.fixture(scope="module")
def home(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("oc-live")
    built_home(game, home)
    return home


def test_the_golden_machines_get_the_dbs_names(home: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "core.sqlite")
    try:
        ops = bridge_ops.build(Config(gos_home=home), store)
        assert ops.live is not None and ops.displays is not None
        doc = copy.deepcopy(SHOWN)
        out = ops.bridge.receive(
            json.dumps(doc).encode(), transport="http", source="t", binding="lab"
        )
        assert out.ok
        base = ops.live.binding("lab")
        ebf = next(
            m for m in base["machines"] if m["meta"] == "multimachine.blastfurnace"
        )
        assert ebf["mte_id"] is not None and "Blast Furnace" in ebf["name"]
        lsc = next(p for p in base["power"] if p["kind"] == "storage")
        assert lsc["mte_id"] is not None and "Supercapacitor" in lsc["name"]
        rows = store.rows(
            "SELECT mte_id FROM tel_machine WHERE name = 'multimachine.blastfurnace'"
        )
        assert rows[0][0] == ebf["mte_id"]
        reply, _ = ops.bridge.reply(out, ack=ops.bridge.seqs.highest(out.agent))
        lines = reply["display"]["lines"]
        assert any("Blast Furnace" in ln["t"] and "stalled" in ln["t"] for ln in lines)
        for ln in lines:
            assert len(ln["t"]) <= disp.BOARD_W and re.match(r"^[ -~]*$", ln["t"])
    finally:
        store.close()
