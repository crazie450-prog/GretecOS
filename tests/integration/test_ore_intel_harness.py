"""Ore intel (WO-6.6) over the server harness's real ore mixes (GT 5.09.54.265's worldgen
registry, D126): nearest-vein answers on veins placed by hand.

For a material that is the primary layer of one Overworld mix and only a lower layer of another,
a vein of the lower-layer mix is placed nearer than the primary one; ``ore_find`` must still list
the primary vein first (D142 (c)), with the distances worked out here from the chunks, the
waypoint built from the primary vein's own heights, and the card valid. Mix contents come from
the export; the vein positions are made up. As test_ores_harness.py: runs only with
GOS_REQUIRE_HARNESS_RUN=1, and nothing is written outside tmp_path.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

from gregtec import cards
from gregtec.db import access
from gregtec.db import ores as ore_db
from gregtec.ingest import vp
from gregtec.ores import intel

LOWER = ("secondary", "between", "sporadic")


def harness_home(side: str, home: Path) -> Path:
    path = Path(__file__).with_name("test_ores_harness.py")
    spec = importlib.util.spec_from_file_location("_wo66_ores_harness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.harness_home(side, home)  # type: ignore[no-any-return]


def test_the_primary_vein_ranks_before_a_nearer_lower_layer_one(tmp_path: Path) -> None:
    home = harness_home("server", tmp_path / "home")
    run_id, mixes = ore_db.read_mixes(home, allow_harness=True)
    overworld = [m for m in mixes.mixes if "Overworld" in m.dims]
    pair = None
    for m in overworld:
        material = m.ores[0].material
        other = next(
            (
                o
                for o in overworld
                if o.id != m.id
                and material not in {x.material for x in o.ores if x.layer == "primary"}
                and any(x.material == material and x.layer in LOWER for x in o.ores)
            ),
            None,
        )
        if other is not None:
            pair = (m, other, material)
            break
    assert pair is not None, (
        "no material that is primary in one mix and lower in another"
    )
    primary, lower, material = pair
    # Primary vein at chunk (10, 0): centre (168, 8), 168 blocks east of (0, 8).
    # Lower-layer vein at chunk (2, 0): centre (40, 8), 40 blocks east.
    veins = [
        vp.Merged(vp.Vein(0, 10, 0, primary.id, False, "generated"), False, "server"),
        vp.Merged(vp.Vein(0, 2, 0, lower.id, False, "generated"), False, "server"),
    ]
    located = vp.Located(tmp_path, tmp_path, "w_1", None, None, None)
    conn = access.connect(access.db_path(home))
    try:
        ore_db.write(conn, run_id, mixes, ore_db.Veins(located, None, None, veins, 0))
        found = intel.find(conn, material, 0, (0, 8))
        assert [(v.ref, v.found.distance) for v in found] == [
            ("vein:0/10/0", 168),
            ("vein:0/2/0", 40),
        ]
        assert found[0].layer == "primary" and found[1].layer in LOWER
        assert cards.validate(intel.card(material, found, [])) == []
        row = intel.vein_at(conn, 0, 10, 0)
        assert row is not None
        args = intel.waypoint_for(row)
        assert (args["x"], args["z"], args["dim"]) == (168, 8, 0)
        lo, hi = primary.heights.get("Overworld", (primary.ymin, primary.ymax))
        assert args["y"] == math.floor((lo + hi) / 2)
    finally:
        conn.close()
