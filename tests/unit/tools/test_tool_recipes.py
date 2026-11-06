"""``recipe_find``, ``recipe_uses`` and ``machine_info`` (WO-2.2, plan 7.3) on the fixture DB.

Every row that states a game number must carry the DB's own citation for it, and every number
must be the DB's: the checks compare each row with the recipe the DB holds under its ``ref``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from gregtec.agents.tools import build_registry, recipes, tsv
from gregtec.agents.tools.glossary import Glossary
from gregtec.db import access

if TYPE_CHECKING:
    from conftest import Parsed, Runner

Rows = Callable[..., list[tuple[Any, ...]]]
GT = "gregtech:gt.metaitem.01"
STEEL = f"{GT}@11305"


def check_cited(p: Parsed, db_rows: Rows) -> None:
    """Each row's ref is a recipe of the newest run, and its numbers are that recipe's."""
    for row in p.rows:
        found = db_rows(
            "SELECT eut, duration_ticks, amperage, voltage_tier FROM recipe r"
            " JOIN export_run e ON e.id = r.run_id WHERE r.ref = ? AND e.run_key = ?",
            row["ref"],
            "20261106T120000Z",
        )
        assert len(found) == 1, row["ref"]
        eut, ticks, amps, tier = found[0]
        assert row["eut"] == ("" if eut is None else str(eut))
        assert row["dur_t"] == ("" if ticks is None else str(ticks))
        assert row["amps"] == ("" if amps is None else str(amps))
        assert row["tier"] == recipes.tier_name(tier)
        assert row["dur_s"] == recipes.seconds(ticks)


def test_recipe_find_lists_the_ways_to_make_an_item(run: Runner, db_rows: Rows) -> None:
    p = run("recipe_find", item="steel ingot")
    assert p.head["item"] == f"Steel Ingot [{STEEL}]"
    assert p.head["run"] == "20261106T120000Z (GT 5.09.54.265)"
    assert p.span == "1-5 of 5"
    check_cited(p, db_rows)
    assert all(r["ref"].startswith("r:") for r in p.rows)
    # No tier (NEI-only, crafting) first, then by tier; GT's recycling recipes last.
    assert [r["machine"] for r in p.rows] == [
        "Alloy Kiln",
        "Bricked Blast Furnace",  # its map has no NEI name: named by its machine
        "Blast Furnace",
        "Blast Furnace",
        "gt.recipe.arcfurnace",
    ]
    assert [r["flags"] for r in p.rows] == ["fake?", "", "", "", "recycling"]
    assert any("cite each recipe by its r: id" in n for n in p.notes)


def test_a_recipe_card_shows_slots_chances_fluids_and_conditions(run: Runner) -> None:
    p = run("recipe_find", item=STEEL, machine="gt.recipe.blastfurnace")
    by_in = {r["in"]: r for r in p.rows}
    oxygen = by_in[f"1x Iron Dust [{GT}@2032]; 1000L Oxygen [fluid:oxygen]"]
    assert (
        oxygen["out"] == f"1x Steel Ingot [{STEEL}]; 1x Ashes [{GT}@2815] 11.11% chance"
    )
    assert (oxygen["eut"], oxygen["dur_t"], oxygen["dur_s"]) == ("120", "500", "25")
    assert oxygen["cond"] == "coil_heat=1000"
    circuit = by_in[
        f"1x Steel Dust [{GT}@2305]; Programmed Circuit 1 "
        "[gregtech:gt.integrated_circuit@1] circuit 1 (not consumed)"
    ]
    assert circuit["tier"] == "MV"


def test_disabled_and_fake_recipes_only_on_request(run: Runner, db_rows: Rows) -> None:
    hidden = run("recipe_find", item="steel ingot")
    shown = run("recipe_find", item="steel ingot", show_fake=True)
    assert len(shown.rows) == len(hidden.rows) + 2
    assert sorted(f for f in shown.col("flags") if f in ("disabled", "fake")) == [
        "disabled",
        "fake",
    ]
    assert shown.head["filters"] == "show_fake=True"
    check_cited(shown, db_rows)


def test_tier_max_keeps_lower_tiers_and_recipes_with_none(run: Runner) -> None:
    p = run("recipe_find", item="steel ingot", tier_max="LV")
    assert {r["tier"] for r in p.rows} == {"", "ULV", "LV"}
    assert p.head["filters"] == "tier_max=LV"
    ulv = run("recipe_find", item="steel ingot", tier_max="ULV")
    assert "LV" not in ulv.col("tier")


@pytest.mark.parametrize(
    "machine",
    [
        "EBF",
        "Blast Furnace",
        "gt.recipe.blastfurnace",
        "mte:1000",
        "electric blast furnace",
    ],
)
def test_the_machine_filter(run: Runner, machine: str) -> None:
    p = run("recipe_find", item="steel ingot", machine=machine)
    assert set(p.col("machine")) == {"Blast Furnace"}
    assert len(p.rows) == 2


def test_an_unknown_machine_is_an_error(run: Runner) -> None:
    assert "no machine, recipe map or NEI handler matches" in run.fail(
        "recipe_find", item="steel ingot", machine="zzzqqq"
    )


def test_an_ambiguous_item_gives_the_candidates_not_recipes(run: Runner) -> None:
    for tool in ("recipe_find", "recipe_uses", "quest_for_item"):
        p = run(tool, item="ingot")
        assert p.head["ambiguous"] == "1"
        assert "ref" not in p.columns and "quest" not in p.columns
        assert {"Iron Ingot", "Steel Ingot"} <= set(p.col("name"))


def test_a_fluid_and_a_machine_key(run: Runner) -> None:
    assert run("recipe_find", item="fluid:oxygen").rows == []  # nothing makes it here
    p = run("recipe_find", item="mte:1000")  # a machine means its item
    assert p.head["item"] == "Electric Blast Furnace [gregtech:gt.blockmachines@1000]"


def test_recipe_uses_finds_slots_oredict_and_alternatives(
    run: Runner, db_rows: Rows
) -> None:
    p = run("recipe_uses", item="steel ingot")
    check_cited(p, db_rows)
    ins = p.col("in")
    assert f"1x Steel Ingot [{STEEL}] (any ore:ingotSteel)" in ins
    assert any("Shaped Crafting" == r["machine"] for r in p.rows)
    assert p.rows[-1]["flags"] == "recycling"
    # TConstruct's steel isn't shown in any slot, but the oredict slot takes it.
    tcon = run("recipe_uses", item="TConstruct:materials@16")
    assert tcon.col("in") == [f"1x Steel Ingot [{STEEL}] (any ore:ingotSteel)"]


def test_recipe_uses_matches_a_wildcard_meta_slot(run: Runner) -> None:
    p = run("recipe_uses", item="oak wood planks")
    assert "1x Wooden Planks [minecraft:planks@32767]" in p.col("in")
    assert "4x Stick [minecraft:stick@0]" in p.col("out")


def test_recipe_uses_of_a_fluid(run: Runner) -> None:
    p = run("recipe_uses", item="fluid:oxygen")
    assert len(p.rows) == 2
    assert {r["flags"] for r in p.rows} == {"", "recycling"}


def test_recipe_tools_page_with_a_cursor(
    tool_db: Path, glossary: Glossary, make_runner: Callable[..., Runner]
) -> None:
    reg = build_registry(tool_db, glossary, key=b"k" * 32, page=2)
    run = make_runner(reg)
    first = run("recipe_find", item="steel ingot")
    assert first.span == "1-2 of 5" and first.more
    second = run.page(first.more)
    assert second.span == "3-4 of 5" and second.more
    third = run.page(second.more)
    assert third.span == "5-5 of 5" and third.more is None
    whole = make_runner(build_registry(tool_db, glossary))(
        "recipe_find", item="steel ingot"
    )
    assert [*first.rows, *second.rows, *third.rows] == whole.rows
    assert second.head == first.head  # each page repeats the record


def test_exact_unit_changes() -> None:
    assert [recipes.seconds(t) for t in (None, 0, 1, 7, 20, 98, 130, 1000)] == [
        "",
        "0",
        "0.05",
        "0.35",
        "1",
        "4.9",
        "6.5",
        "50",
    ]
    assert [recipes.percent(bp) for bp in (1, 125, 1111, 7500, 10000)] == [
        "0.01%",
        "1.25%",
        "11.11%",
        "75%",
        "100%",
    ]
    assert recipes.tier_name(None) == "" and recipes.tier_name(8) == "UV"
    assert recipes.tier_name(99) == "99"


def test_with_no_export_run_the_recipe_tools_say_so(
    fresh_db: Path, make_runner: Callable[..., Runner]
) -> None:
    conn = sqlite3.connect(fresh_db)
    try:
        conn.execute("DELETE FROM export_run")
        conn.commit()
    finally:
        conn.close()
    run = make_runner(build_registry(fresh_db, key=b"k" * 32))
    assert "no export run yet" in run.fail("recipe_find", item=STEEL)


# --- machine_info --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "machine",
    [
        "EBF",
        "Electric Blast Furnace",
        "mte:1000",
        "gregtech:gt.blockmachines@1000",
    ],
)
def test_machine_info(run: Runner, machine: str, tool_db: Path) -> None:
    p = run("machine_info", machine=machine)
    assert p.head["machine"] == "mte:1000"
    assert p.head["name"] == "Electric Blast Furnace"
    assert p.head["maps"] == "Blast Furnace [gt.recipe.blastfurnace]"
    assert p.head["multiblock"] == "1"
    facts = {r["key"]: r for r in p.rows}
    assert facts["oc"]["value"] == "heat"
    assert facts["oc"]["source"] == "curated"
    assert facts["parallel"]["confidence"] == "0.95"
    assert facts["batch_mode"]["source"] == "code"
    assert set(p.col("cite")) == {"mte:1000"}
    # One row per key, the planner's pick (named query machine_facts), as the DB holds it.
    conn = access.connect(tool_db, readonly=True)
    try:
        best = access.query(conn, "machine_facts", {"mte_id": 1000})
    finally:
        conn.close()
    assert [r["key"] for r in best] == p.col("key")
    for want, got in zip(best, p.rows, strict=True):
        assert (got["value"], got["source"]) == (want["value"], want["source"])
        assert got["confidence"] == tsv.cell(want["confidence"])


def test_a_singleblock_machine_has_its_tier(run: Runner) -> None:
    p = run("machine_info", machine="mte:31")
    assert p.head["tier"] == "LV"
    assert p.head["maps"] == "Macerator [gt.recipe.macerator]"


@pytest.mark.parametrize("text", ["1000", " 31 "])
def test_a_bare_number_is_never_a_machine_id(run: Runner, text: str) -> None:
    """A number read off F3 or NEI is likely a numeric block ID (CLAUDE.md: never key on
    those), so machine_info wants mte:<id>."""
    error = run.fail("machine_info", machine=text)
    assert "bare number" in error and "mte:<id>" in error


def test_an_unknown_machine(run: Runner) -> None:
    assert "no machine mte:99999" in run.fail("machine_info", machine="mte:99999")
    p = run("machine_info", machine="zzzqqq")
    assert p.rows == [] and p.head["ambiguous"] == "0"
