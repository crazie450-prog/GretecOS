"""Ore mixes (WO-1.6, D126) from real export runs of GregtecLink's harnesses.

The runs are the dev runtime's (GT 5.09.54.265 and its dependencies, not the pack; mode
"harness"), whose worldgen registry is GT's full ore-layer list. Checks: every layer is keyed
by material and ore block (an item the run's items.jsonl names), the vanilla dimensions are
named, `gos ores` loads the mixes and finds veins through them, and (client run) every page of
NEI's GT ore-vein handler is one of the registry's layers, ores for ores (V08: the registry has
everything NEI shows, plus the mix name VisualProspecting keys veins by, the weight, density and
size). As in test_db_harness.py this runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job,
right after both harnesses). Nothing is written outside tmp_path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gregtec.db import access
from gregtec.db import ores as ore_db
from gregtec.ingest import collect, ore_mixes, vp

REPO = Path(__file__).resolve().parents[2]
NEI_VEINS = "gtneioreplugin.plugin.gregtech5.PluginGT5VeinStat"


def harness_home(side: str, home: Path) -> Path:
    game = REPO / "mod" / "run" / side
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    collect.collect(game, home, 1, allow_harness=True)
    return home


def items_of(run: Path) -> set[tuple[str, int]]:
    out = set()
    with open(run / "items.jsonl", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            out.add((o["item"], o["meta"]))
    return out


@pytest.mark.parametrize("side", ["server", "client"])
def test_the_registrys_mixes_load_and_find_veins(side: str, tmp_path: Path) -> None:
    home = harness_home(side, tmp_path / "home")
    run_id, mixes = ore_db.read_mixes(home, allow_harness=True)
    run = home / collect.DATA_EXPORTS / run_id
    manifest = json.loads((run / collect.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["parts"]["ore_mixes"] == "ok"
    assert len(mixes.mixes) == manifest["counts"]["ore_mixes"] >= 100
    assert mixes.problems == []
    items = items_of(run)
    for m in mixes.mixes:
        assert (
            m.id.startswith("ore.mix.") and m.dims and m.weight > 0 and m.ymin < m.ymax
        )
        assert [o.layer for o in m.ores] == list(ore_mixes.LAYERS)
        for o in m.ores:
            assert o.material and o.name
            assert (o.registry_name, o.meta) in items, (m.id, o)
    ids = mixes.ids_by_name()
    assert ids["Overworld"] == [0] and ids["Nether"] == [-1] and ids["The End"] == [1]

    # Two veins of real mixes (the positions are made up), in the Overworld and the Nether.
    ow = next(m for m in mixes.mixes if "Overworld" in m.dims)
    nether = next(m for m in mixes.mixes if "Nether" in m.dims)
    veins = [
        vp.Merged(vp.Vein(0, 1, 1, ow.id, False, "generated"), True, "server"),
        vp.Merged(vp.Vein(-1, 4, -2, nether.id, False, "generated"), False, "server"),
    ]
    located = vp.Located(tmp_path, tmp_path, "w_1", None, None, None)
    found_veins = ore_db.Veins(located, None, None, veins, 0)
    path = access.db_path(home)
    conn = access.connect(path)
    try:
        summary = ore_db.write(conn, run_id, mixes, found_veins)
        assert summary.unknown == {}
        primary = ow.ores[0]
        hits = ore_db.find(conn, str(primary.name), dim=0, near=(0, 0))
        assert [(h.mix_id, h.distance) for h in hits] == [(ow.id, 34)]
        assert "primary" in hits[0].layers
        assert (hits[0].ymin, hits[0].ymax) == (ow.ymin, ow.ymax)
        assert (
            ore_db.find(conn, str(nether.ores[0].material), dim=-1)[0].mix_id
            == nether.id
        )
    finally:
        conn.close()


def test_every_nei_vein_page_is_a_registry_layer(tmp_path: Path) -> None:
    home = harness_home("client", tmp_path / "home")
    run_id, mixes = ore_db.read_mixes(home, allow_harness=True)
    run = home / collect.DATA_EXPORTS / run_id
    manifest = json.loads((run / collect.MANIFEST).read_text(encoding="utf-8"))
    pages = []
    for rel in manifest["files"]:
        if not rel.startswith("nei/"):
            continue
        with open(run / rel, encoding="utf-8") as f:
            pages += [
                p for line in f if (p := json.loads(line))["handler"] == NEI_VEINS
            ]
    assert len(pages) == len(mixes.mixes)
    unmatched = []
    for p in pages:
        slots = [
            {(s["strId"], s["Damage"])}
            | {(a["strId"], a["Damage"]) for a in s.get("anyOf", [])}
            for s in p["results"]
        ]
        # A page names no mix, so it is matched by its ores (the registry's plain-stone ore
        # block, or the material in the only stones of this page's dimensions, such as
        # "Horus Neutronium Ore") and its dimension count.
        hits = [
            m
            for m in mixes.mixes
            if len(m.dims) == len(p["ingredients"])
            and len(slots) == 4
            and all(
                (o.registry_name, o.meta) in slot
                or str(o.name) in p["results"][i]["name"]
                for i, (o, slot) in enumerate(zip(m.ores, slots, strict=True))
            )
        ]
        if not hits:
            unmatched.append([s["name"] for s in p["results"]])
    assert unmatched == []
