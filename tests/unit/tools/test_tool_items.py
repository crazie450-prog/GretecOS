"""``item_resolve`` (WO-2.2, plan 4.4, QA:D11): FTS + glossary aliases, ranked, with ambiguity."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import TEMPLATE, Glossary

if TYPE_CHECKING:
    from conftest import Runner

GT = "gregtech:gt.metaitem.01"
STEEL, STAINLESS = f"{GT}@11305", f"{GT}@11306"


def test_a_material_alone_means_its_ingot(run: Runner) -> None:
    p = run("item_resolve", text="steel")
    assert p.head["ambiguous"] == "0"
    assert p.rows[0]["key"] == STEEL
    assert p.rows[0]["name"] == "Steel Ingot"
    assert p.col("kind") == ["item"] * len(p.rows)


def test_gregtechs_item_wins_a_tie_on_its_name(run: Runner) -> None:
    p = run("item_resolve", text="steel ingot")
    assert p.head["ambiguous"] == "0"
    assert p.rows[0]["key"] == STEEL
    assert p.rows[0]["score"] == "1"
    tcon = next(r for r in p.rows if r["mod"] == "TConstruct")
    assert float(tcon["score"]) < 1  # still listed, never a tie


def test_a_longer_material_name_is_its_own_material(run: Runner) -> None:
    p = run("item_resolve", text="Stainless   STEEL")
    assert p.head["ambiguous"] == "0"
    assert p.rows[0]["key"] == STAINLESS


def test_close_matches_are_ambiguous(run: Runner) -> None:
    p = run("item_resolve", text="ingot")
    assert p.head["ambiguous"] == "1"
    assert {p.rows[0]["name"], p.rows[1]["name"]} == {"Iron Ingot", "Steel Ingot"}
    assert any("ambiguous: ask which item" in n for n in p.notes)


def test_a_weak_match_is_ambiguous(run: Runner) -> None:
    p = run("item_resolve", text="weird")
    assert p.head["ambiguous"] == "1"
    assert len(p.rows) == 1


def test_a_glossary_alias_resolves(run: Runner) -> None:
    p = run("item_resolve", text="SS plate")
    assert p.head["ambiguous"] == "0"
    assert p.head["glossary"] == "SS plate -> stainless steel plate (material)"
    assert p.rows[0]["key"] == f"{GT}@17306"
    assert p.rows[0]["via"] == "stainless steel plate"


def test_machines_resolve_by_alias_and_kind(run: Runner) -> None:
    p = run("item_resolve", text="EBF", kind="machine")
    assert p.rows[0]["key"] == "mte:1000"
    assert p.rows[0]["kind"] == "machine"
    assert p.head["ambiguous"] == "0"
    # By default a machine is not an item candidate twice over.
    plain = run("item_resolve", text="electric blast furnace")
    assert "mte:1000" not in plain.col("key")


def test_fluids_and_kinds(run: Runner) -> None:
    p = run("item_resolve", text="oxygen")
    assert p.rows[0]["key"] == "fluid:oxygen"
    assert p.rows[0]["kind"] == "fluid"
    only = run("item_resolve", text="steel", kind="fluid")
    assert only.rows == [] and only.head["matches"] == "0"


@pytest.mark.parametrize(
    ("text", "key"),
    [
        (STEEL, STEEL),
        ("fluid:oxygen", "fluid:oxygen"),
        ("mte:1000", "mte:1000"),
        ("minecraft:planks@32767", "minecraft:planks@32767"),
    ],
)
def test_keys_resolve_to_themselves(run: Runner, text: str, key: str) -> None:
    p = run("item_resolve", text=text)
    assert p.col("key") == [key]
    assert p.head["ambiguous"] == "0"


CLAY_CAST = "TConstruct:Clay Cast@3"
JET_FUEL = "fluid:jet fuel a"


def test_keys_with_spaces_round_trip(run: Runner) -> None:
    """Registry and fluid names can have spaces (174 of the dev runtime's 1349 fluids do): the
    key item_resolve gives back must work in every tool that takes an item."""
    fuel = run("item_resolve", text="jet fuel")
    assert fuel.rows[0]["key"] == JET_FUEL and fuel.rows[0]["name"] == "Jet Fuel A"
    cast = run("item_resolve", text="clay ingot cast")
    assert cast.rows[0]["key"] == CLAY_CAST
    for key in (JET_FUEL, CLAY_CAST, f"  {JET_FUEL} "):
        p = run("item_resolve", text=key)
        assert p.col("key") == [key.strip()] and p.head["ambiguous"] == "0"
    made = run("recipe_find", item=JET_FUEL)
    assert made.head["item"] == f"Jet Fuel A [{JET_FUEL}]"
    assert [r["out"] for r in made.rows] == [f"50L Jet Fuel A [{JET_FUEL}]"]
    assert made.rows[0]["in"].startswith(
        f"Clay Ingot Cast [{CLAY_CAST}] (not consumed)"
    )
    used = run("recipe_uses", item=CLAY_CAST)
    assert used.col("ref") == made.col("ref")
    quests = run("quest_for_item", item=JET_FUEL)
    assert [(r["match"], r["amount"]) for r in quests.rows] == [("fluid", "250")]
    # A key is looked up exactly: its words don't fall through to a search.
    assert run("recipe_find", item="fluid:jet  fuel a").rows == []


def test_an_unknown_key_or_name_gives_no_candidates(run: Runner) -> None:
    for text in ("gregtech:gt.metaitem.01@99999", "fluid:nope", "zzzqqq"):
        p = run("item_resolve", text=text)
        assert p.rows == []
        assert p.head["ambiguous"] == "0"
    assert run("item_resolve", text="zzzqqq").notes[0].startswith("no item matches")


def test_text_with_no_words_is_an_error(run: Runner) -> None:
    assert "name an item" in run.fail("item_resolve", text="!!! ???")


def test_fts_operators_in_text_are_only_words(run: Runner) -> None:
    for text in ('steel" OR "x', "steel NEAR(ingot)", "steel*", "-steel", "kind:item"):
        run("item_resolve", text=text)  # never an FTS syntax error


def test_display_names_lose_formatting_codes_and_stay_one_cell(run: Runner) -> None:
    p = run("item_resolve", text="weird name item")
    assert p.rows[0]["name"] == "Weird\\tName\\nItem"
    assert "§" not in p.text


def test_limit(run: Runner) -> None:
    p = run("item_resolve", text="steel", limit=2)
    assert len(p.rows) == 2
    assert p.head["matches"] == "7"


def test_the_template_glossary_is_the_fallback(tmp_path: Path) -> None:
    assert Glossary.find(tmp_path / "no-vault").path == TEMPLATE
    found = Glossary.find(None)
    assert found.problem is None
    assert any(e.term == "EBF" for e in found.entries)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "glossary.tsv").write_text(
        "term\taliases\tkind\nfoo\tbar\tmaterial\n", encoding="utf-8"
    )
    live = Glossary.find(vault)
    assert live.path == vault / "glossary.tsv"
    assert [e.forms for e in live.entries] == [("foo", "bar")]


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        ("no header here\n", "no term, aliases, kind header"),
        ("", "no term, aliases, kind header"),
        (b"\xff\xfe", "codec"),
    ],
)
def test_a_bad_glossary_is_ignored(
    tmp_path: Path, content: object, problem: str
) -> None:
    path = tmp_path / "glossary.tsv"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        assert isinstance(content, str)
        path.write_text(content, encoding="utf-8")
    g = Glossary.load(path)
    assert g.entries == ()
    assert problem in (g.problem or "")


def test_a_glossary_saved_with_a_bom_reads(tmp_path: Path) -> None:
    """Windows editors (Notepad, Excel's TSV) start a UTF-8 file with a BOM."""
    path = tmp_path / "glossary.tsv"
    path.write_bytes("﻿term\taliases\tkind\nfoo\tbar\tmaterial\n".encode())
    g = Glossary.load(path)
    assert g.problem is None
    assert [e.forms for e in g.entries] == [("foo", "bar")]


@pytest.mark.parametrize("content", [b"no header here\n", b"\xff\xfe", b""])
def test_a_bad_live_glossary_falls_back_to_the_template(
    tmp_path: Path, content: bytes
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "glossary.tsv").write_bytes(content)
    g = Glossary.find(vault)
    assert g.path == TEMPLATE
    assert any(e.term == "EBF" for e in g.entries)  # "EBF" and "SS" still resolve
    assert g.problem is not None
    assert str(vault / "glossary.tsv") in g.problem and "vault-template" in g.problem


def test_glossary_variants() -> None:
    g = Glossary.load(TEMPLATE)
    forms = [v for v, _ in g.variants("SS plate")]
    assert forms[0] == "SS plate"
    assert "stainless steel plate" in forms
    # Tiers are part of item names as they are: never expanded.
    assert [v for v, _ in g.variants("LV motor")] == ["LV motor"]


def test_without_a_glossary_aliases_dont_resolve(
    tool_db: Path, make_runner: Callable[..., Runner]
) -> None:
    reg = build_registry(tool_db, Glossary(), key=b"k" * 32)
    p = make_runner(reg)("item_resolve", text="SS plate")
    assert "glossary" not in p.head
    assert p.rows == [] or p.rows[0]["key"] != f"{GT}@17306"
