"""WO-8.1 (D286) on a DB built from a real GregtecLink client harness run: the "what's this?"
answer for the client harness's 10 scripted items (GT, GT addons, non-GT mods, vanilla, an item
with NBT), from the target messages the client module sends for them. The game-side half (the
hover, the look, the key, the link, the reconnect) is the client harness's ``-PharnessClientLink``
mode (mod/README.md), which checks the same answers from the game. As the other harness tests:
only with GOS_REQUIRE_HARNESS_RUN=1 after the client harness; nothing is written outside tmp_path.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.db import access
from gregtec.gamelink import whatsthis
from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]
# (registry, meta, nbt, the DB's English name): mod/.../harness/ClientLinkHarness.java's items
ITEMS: list[tuple[str, int, dict[str, Any] | None, str]] = [
    ("gregtech:gt.blockmachines", 1000, None, "Electric Blast Furnace"),
    ("gregtech:gt.blockcasings2", 0, None, "Solid Steel Machine Casing"),
    ("GoodGenerator:MAR_Casing", 0, None, "Field Restriction Casing"),
    ("appliedenergistics2:tile.BlockSkyStone", 1, None, "Sky Stone Block"),
    ("gregtech:gt.metaitem.01", 11305, None, "Steel Ingot"),
    ("bartworks:gt.bwMetaGeneratedingot", 3, None, "Zirconium Ingot"),
    ("miscutils:itemHotIngotArcanite", 0, None, "Hot Arcanite Ingot"),
    ("appliedenergistics2:item.ItemMultiMaterial", 24, None, "Engineering Processor"),
    ("minecraft:iron_ingot", 0, None, "Iron Ingot"),
    ("IC2:itemBatRE", 1, {"charge": "10000.0D"}, "RE-Battery"),
]


@pytest.fixture(scope="module")
def reg(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Registry:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("whats-this-harness")
    built_home(game, home)
    return build_registry(access.db_path(home), Glossary.find(None))


@pytest.mark.parametrize(("registry", "meta", "nbt", "name"), ITEMS)
def test_each_scripted_item_is_named_and_cited(
    reg: Registry, registry: str, meta: int, nbt: dict[str, Any] | None, name: str
) -> None:
    item: dict[str, Any] = {"item": registry, "meta": meta}
    if nbt is not None:
        item["nbt"] = nbt
    got = whatsthis.answer(reg, {"kind": "item", "item": item})
    assert got.found and got.name == name, got.text
    assert got.key is not None and got.key.startswith(f"{registry}@{meta}")
    assert ("#" in got.key) == (nbt is not None)  # the NBT variant itself, by its hash
    assert got.text.startswith(f"{name}, from ")
    for ref in got.refs:  # every citation is in the answer and is a DB id
        assert ref in got.text and ref.split(":", 1)[0] in ("r", "mte")


def test_the_mods_cover_gt_its_addons_and_others() -> None:
    mods = {r.split(":", 1)[0] for r, *_ in ITEMS}
    assert {"gregtech", "bartworks", "miscutils", "GoodGenerator"} <= mods
    assert {"appliedenergistics2", "IC2", "minecraft"} <= mods
