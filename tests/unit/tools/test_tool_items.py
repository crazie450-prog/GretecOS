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
