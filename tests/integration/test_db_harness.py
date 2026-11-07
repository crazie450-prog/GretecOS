"""``gos db build`` (WO-1.3) on real export runs from GregtecLink's harnesses.

The runs are the dev runtime's (GT 5.09.54.265 and its dependencies, not the pack; mode
"harness"), so the checks are the WO's acceptance in shape: every key is one recipe (the
collision test, with the repo's allow-list), the same input gives byte-identical keys and
hashes, two independent exports (the server and the client harness) give the same GT keys, two
client exports give the same keys and hashes for every recipe, NEI-only ones too, no key or NBT
holds a numeric item ID, and the counts report covers every mod. As in
test_collect_harness.py this runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, right
after both harnesses): run them (mod/README.md), then
``GOS_REQUIRE_HARNESS_RUN=1 uv run pytest tests/integration``. The two-client check also needs
an earlier client run: copy ``mod/run/client/gregteclink/exports`` to
``mod/run/client-b/gregteclink/`` before running the client harness again (the harness wipes
its own), and set ``GOS_CLIENT_RUN_B=mod/run/client-b``, as CI does. Nothing is written outside
tmp_path.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from gregtec.db import access, build, report
from gregtec.ingest import collect, gtdump, merge

REPO = Path(__file__).resolve().parents[2]


def harness_home(side: str, home: Path, game: Path | None = None) -> Path:
    """A GOS_HOME holding the harness's newest run, collected as ``gos export collect`` does."""
    game = game or REPO / "mod" / "run" / side
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    collect.collect(game, home, 1, allow_harness=True)
    return home


def rows(db: Path, sql: str, *args: object) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


GT_KEYS = "SELECT ref, signature, content_hash FROM recipe WHERE source IN ('gt', 'gt+nei') ORDER BY ref"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> build.Result:
    home = harness_home("client", tmp_path_factory.mktemp("client"))
    return build.build(home, allow_harness=True)


@pytest.fixture(scope="module")
def client_b(tmp_path_factory: pytest.TempPathFactory) -> build.Result:
    """An earlier client harness run of the same mods (GOS_CLIENT_RUN_B, a game folder)."""
    where = os.environ.get("GOS_CLIENT_RUN_B")
    if not where:
        pytest.skip("set GOS_CLIENT_RUN_B to a game folder with an earlier client run")
    game = Path(where) if Path(where).is_absolute() else REPO / where
    home = harness_home("client", tmp_path_factory.mktemp("client_b"), game)
    return build.build(home, allow_harness=True)


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> build.Result:
    home = harness_home("server", tmp_path_factory.mktemp("server"))
    return build.build(home, allow_harness=True)


@pytest.mark.parametrize("side", ["server", "client"])
def test_a_harness_run_builds_with_unique_keys(
    side: str, request: pytest.FixtureRequest
) -> None:
    result: build.Result = request.getfixturevalue(side)
    db, m = result.db, result.loaded.run.manifest
    counts = m["counts"]
    # The collision test passed (else build() raised): one recipe per key, keys well formed.
    total, distinct = rows(db, "SELECT count(*), count(DISTINCT ref) FROM recipe")[0]
    assert total == distinct > 90_000
    assert rows(
        db, "SELECT count(*) FROM recipe WHERE ref NOT GLOB 'r:*/[0-9a-f]*'"
    ) == [(0,)]
    assert rows(db, "SELECT count(*) FROM recipe_map") == [(counts["recipe_maps"],)]
    assert (
        result.loaded.gt_recipes + len(result.loaded.recipe_errors) == counts["recipes"]
    )
    assert rows(db, "SELECT count(*) FROM machine") == [(counts["machines"],)]
    # Every map with recipes in the dump has recipes in the DB.
    empty = rows(
        db,
        "SELECT m.unloc_name FROM recipe_map m WHERE NOT EXISTS"
        " (SELECT 1 FROM recipe r WHERE r.recipe_map_id = m.id)",
    )
    dumped = {mp.name for mp in result.loaded.maps if mp.count}
    assert not dumped & {e[0] for e in empty}
    if side == "client":
        stats = result.loaded.stats
        assert stats is not None and m["parts"]["nei"] == "ok"
        gt_rows = stats.nei_rows - stats.nei_only
        assert stats.exact + stats.matched > 0.95 * gt_rows
        sources = dict(rows(db, "SELECT source, count(*) FROM recipe GROUP BY 1"))
        assert (
            sources["gt+nei"] > 80_000
            and sources["crafting"] > 10_000
            and sources["furnace"] > 0
        )
        # The handlers that aren't recipes (facades, fuels, ore pages) are left out.
        assert set(stats.not_recipes) <= set(merge.NOT_RECIPES)
        assert sum(stats.not_recipes.values()) > 0
        in_db = {
            h
            for (h,) in rows(
                db,
                "SELECT DISTINCT h.handler_name FROM recipe r"
                " JOIN nei_handler h ON h.id = r.nei_handler_id",
            )
        }
        assert not in_db & set(merge.NOT_RECIPES)
        assert stats.ore_named > 0
    else:
        assert dict(
            rows(db, "SELECT source, count(*) FROM recipe GROUP BY 1")
        ).keys() == {"gt"}


def test_two_exports_of_one_pack_give_the_same_gt_keys(
    client: build.Result, server: build.Result
) -> None:
    # Two JVM runs (the server harness and the client harness) of the same mods: GT registers
    # recipes in another order each time, and some maps list outputs in another order.
    assert rows(client.db, GT_KEYS) == rows(server.db, GT_KEYS)


ALL_KEYS = "SELECT ref, signature, content_hash FROM recipe ORDER BY ref"


def test_two_client_exports_give_the_same_keys_for_every_recipe(
    client: build.Result, client_b: build.Result
) -> None:
    # NEI shows another accepted stack in an oredict slot on each run, and some handlers show a
    # random example: neither may change a key or a content hash (M1 of the WO-1.3 review).
    assert client.loaded.run.run_id != client_b.loaded.run.run_id
    a, b = rows(client.db, ALL_KEYS), rows(client_b.db, ALL_KEYS)
    assert len(a) > 140_000
    gone = {r[0] for r in a} - {r[0] for r in b}
    new = {r[0] for r in b} - {r[0] for r in a}
    changed = {x[0] for x, y in zip(a, b) if x != y} - gone - new
    assert (sorted(gone)[:5], sorted(new)[:5], sorted(changed)[:5]) == ([], [], [])
    assert a == b


def test_the_same_run_again_is_byte_identical(
    client: build.Result, tmp_path: Path
) -> None:
    home = client.loaded.run.path.parents[2]
    again = build.build(home, allow_harness=True, db=tmp_path / "again.sqlite")
    q = "SELECT ref, signature, content_hash, source FROM recipe ORDER BY ref"
    assert rows(again.db, q) == rows(client.db, q)
    q = "SELECT registry_name, meta, nbt_hash, nbt_json FROM item ORDER BY 1, 2, 3"
    assert rows(again.db, q) == rows(client.db, q)


def test_the_full_oredict_names_more_nei_only_slots(
    client: build.Result, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D145: the run's oredict.jsonl names the NEI-only slots whose members items.jsonl
    # doesn't all list; built again from items.jsonl's ore lists alone, fewer get a name. That
    # isn't a run made before D145: this run's items.jsonl lists every oredict member, which
    # older runs' didn't, so they named fewer still (8,644 of 50,155 for 20261106T092709Z).
    m = client.loaded.run.manifest
    assert m["parts"]["oredict"] == "ok" and client.loaded.oredict is not None
    assert len(client.loaded.oredict) == m["counts"]["oredict"]
    steel = rows(
        client.db,
        "SELECT i.registry_name, i.meta FROM ore_member om JOIN ore o ON o.id = om.ore_id"
        " JOIN item i ON i.id = om.item_id WHERE o.name = 'ingotSteel'",
    )
    assert ("gregtech:gt.metaitem.01", 11305) in steel
    home = client.loaded.run.path.parents[2]
    monkeypatch.setattr(gtdump, "oredict_status", lambda manifest: "absent")
    before = build.build(home, allow_harness=True, db=tmp_path / "before.sqlite")
    now, old = client.loaded.stats, before.loaded.stats
    assert now is not None and old is not None
    assert now.ore_named + now.ore_sets == old.ore_named + old.ore_sets
    assert now.ore_named > old.ore_named
    print(
        f"NEI-only oredict slots named: {now.ore_named:,} of "
        f"{now.ore_named + now.ore_sets:,} (this run's items.jsonl ore lists alone: "
        f"{old.ore_named:,})"
    )


def test_no_numeric_item_id_in_any_item_key(client: build.Result) -> None:
    def walk(value: object) -> None:
        if isinstance(value, dict):
            assert not ("__item" in value and "id" in value), value
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    named = 0
    for (text,) in rows(
        client.db, "SELECT nbt_json FROM item WHERE nbt_json IS NOT NULL"
    ):
        walk(json.loads(text))
        named += "__item" in text
    assert named > 0  # data sticks and the like were named, not numbered
    # AE2 facades keep a numeric block ID in their NBT ("x"); their handler is left out.
    assert rows(
        client.db,
        "SELECT count(*) FROM item WHERE registry_name = 'appliedenergistics2:item.ItemFacade'",
    ) == [(0,)]


def test_search_and_report(client: build.Result) -> None:
    conn = access.connect(client.db, readonly=True)
    try:
        found = access.query(conn, "find_names", {"q": '"stainless steel"', "limit": 5})
        assert found and all("Stainless Steel" in (r["name_en"] or "") for r in found)
        making = access.query(
            conn,
            "recipes_making",
            {"q": '"steel ingot"', "run_id": client.run_id, "limit": 5000},
        )
        assert any(r["source"] == "gt+nei" for r in making)
    finally:
        conn.close()
    text = "\n".join(report.lines(client, full=True))
    assert "Collision test: passed" in text
    # TecTech's Godforge maps are empty in GT's dump; NEI has the molten ones' recipes.
    assert "WARNING: GT categories with recipes only in NEI" in text
    assert "gt.recipe.fog_molten (" in text
    assert "Recipe maps with no GT recipe (" in text
    per_mod = {mod for mod, *_ in report.per_mod(client)}
    assert {"gregtech", "bartworks", "miscutils"} <= per_mod
    # The dev runtime has none of Play's addons; the report says so, never "ok".
    assert text.count("not loaded  ") == len(report.ADDONS)


# Base multiblocks GT registers only with mods the dev runtime doesn't have (WO-1.7): Thaumcraft
# (Research Completer, Large Essentia Smeltery), MobsInfo and EnderIO (Extreme Entity Crusher),
# Forestry (the Industrial Apiary). Their curated rows are for the full pack.
NOT_IN_DEV_RUNTIME = {
    "gregtech.common.tileentities.machines.multi.MTEResearchCompleter",
    "goodgenerator.blocks.tileEntity.MTELargeEssentiaSmeltery",
    "kubatech.tileentity.gregtech.multiblock.MTEExtremeEntityCrusher",
    "kubatech.tileentity.gregtech.multiblock.MTEMegaIndustrialApiary",
}


@pytest.mark.parametrize("side", ["server", "client"])
def test_every_base_multiblock_has_parallels_and_oc_rules_or_a_flag(
    side: str, request: pytest.FixtureRequest
) -> None:
    result: build.Result = request.getfixturevalue(side)
    base, found = report.fact_statuses(result)
    assert {"gregtech", "miscutils", "tectech", "bartworks"} <= base
    multis = [s for s in found if s.machine.mod in base]
    assert len(multis) > 290
    assert [s.machine.mte_id for s in multis if s.state == "missing"] == []
    covered = [s for s in multis if s.state == "covered"]
    assert len(covered) > 120
    assert all(
        s.parallel and s.oc and min(s.parallel.confidence, s.oc.confidence) >= 0.8
        for s in covered
    )
    # Processing machines with their own recipe check and no recipe map are flagged, not n/a.
    states = {s.machine.class_name.rsplit(".", 1)[-1]: s.state for s in multis}
    for cls in ("MTEIntegratedOreFactory", "MTEExtremeIndustrialGreenhouse"):
        assert states[cls] == "flagged", cls
    facts = result.loaded.facts
    assert facts is not None
    assert {c.machine for c in facts.unmatched} == NOT_IN_DEV_RUNTIME
    assert facts.bad_maps == []
    # The EBF's rules, read from GT's source, are in the DB and win over its tooltip.
    conn = access.connect(result.db, readonly=True)
    try:
        ebf = {
            r["key"]: (r["value"], r["source"])
            for r in access.query(conn, "machine_facts", {"mte_id": 1000})
        }
    finally:
        conn.close()
    assert ebf["oc"] == ("heat", "curated")
    assert ebf["machine_heat"] == ("coil_heat + 100*(vt - 2)", "curated")
    assert ebf["parallel"] == ("1", "curated")
    # The Dangote Distillus's tower mode agrees with its tooltip's static 12 parallels.
    [adt] = [
        s.machine
        for s in multis
        if s.machine.class_name.endswith(".MTEAdvDistillationTower")
    ]
    adt_facts = {(f.key, f.source): f.value for f in facts.facts[adt.mte_id]}
    assert adt_facts[("parallel", "tooltip")] == "12"
    assert adt_facts[("parallel[gt.recipe.distillationtower]", "curated")] == "12"
    text = "\n".join(report.lines(result, full=False))
    assert "Base multiblocks (GT's jar): " in text and "missing 0" in text
